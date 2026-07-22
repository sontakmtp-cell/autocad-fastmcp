"""FastMCP public v1 facade and local-only outer ASGI application."""

from __future__ import annotations

import os
import logging
import re
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from ipaddress import ip_address
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError, ValidationError as FastMCPValidationError
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server.auth import RemoteAuthProvider, require_scopes
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware
from fastmcp.tools.tool import ToolResult
from mcp.types import PromptMessage, ResourceLink, TextContent
from pydantic import Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route, WebSocketRoute

from .contracts import (
    CadListDevicesInput,
    CadListDevicesOutput,
    CadListDevicesOutputC1,
    CadGetJobInput,
    CadGetJobOutput,
    CadGetJobOutputC1,
    CadObserveInput,
    CadObserveInputDurable,
    CadObserveOutput,
    CadObserveOutputDurable,
    CadObserveOutputC1,
    CadQueryInput,
    CadQueryOutput,
    Principal,
)
from .services import (
    MAX_ENTITIES_DEFAULT,
    MAX_ENTITIES_UPPER,
    MAX_ENTITY_DETAIL_CALLS_DEFAULT,
    MAX_ENTITY_DETAIL_CALLS_UPPER,
    MAX_IMAGE_BYTES_UPPER,
    MAX_SNAPSHOT_BYTES_DEFAULT,
    MAX_SNAPSHOT_BYTES_UPPER,
    MAX_SNAPSHOT_COUNT_DEFAULT,
    MAX_SNAPSHOT_COUNT_UPPER,
    MAX_SNAPSHOT_STORE_BYTES_DEFAULT,
    MAX_SNAPSHOT_STORE_BYTES_UPPER,
    OBSERVATION_TIMEOUT_SECONDS_DEFAULT,
    OBSERVATION_TIMEOUT_SECONDS_UPPER,
    SNAPSHOT_TTL_SECONDS_DEFAULT,
    SNAPSHOT_TTL_SECONDS_UPPER,
    GatewayError,
    GatewayServices,
    LOCAL_SUBJECT,
)


CorrelationIdFactory = Callable[[], str]
_correlation_id: ContextVar[str | None] = ContextVar("cad_gateway_correlation_id", default=None)
logger = logging.getLogger(__name__)
_SAFE_PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_JOB_STATES = frozenset(
    {
        "queued",
        "dispatched",
        "acknowledged",
        "running",
        "cancel_requested",
        "reconnect_pending",
        "outcome_unknown",
        "succeeded",
        "failed",
        "cancelled",
        "needs_attention",
    }
)


@dataclass(frozen=True)
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/mcp"
    stateless_http: bool = False
    allowed_hosts: tuple[str, ...] = ("127.0.0.1:*", "localhost:*", "[::1]:*")
    allowed_origins: tuple[str, ...] = ()
    max_image_bytes: int = 5 * 1024 * 1024
    max_entities: int = MAX_ENTITIES_DEFAULT
    max_entity_detail_calls: int = MAX_ENTITY_DETAIL_CALLS_DEFAULT
    observation_timeout_seconds: float = OBSERVATION_TIMEOUT_SECONDS_DEFAULT
    max_snapshot_bytes: int = MAX_SNAPSHOT_BYTES_DEFAULT
    snapshot_ttl_seconds: float = SNAPSHOT_TTL_SECONDS_DEFAULT
    max_snapshot_count: int = MAX_SNAPSHOT_COUNT_DEFAULT
    max_snapshot_store_bytes: int = MAX_SNAPSHOT_STORE_BYTES_DEFAULT
    profile: Literal["local", "phase3_poc", "phase4_c1"] = "local"
    db_path: str | None = None
    fixture_tokens: tuple[tuple[str, str], ...] = ()
    fixture_owner_subject: str = "phase3-fixture-user"
    stale_after_seconds: int = 45
    request_wait_timeout_seconds: float = 30.0
    job_deadline_seconds: float = 300.0
    # Backward-compatible constructor alias used by the original Phase 3 tests.
    command_timeout_seconds: float | None = None
    oauth_issuer: str | None = None
    oauth_audience: str | None = None
    oauth_jwks_uri: str | None = None
    public_origin: str | None = None
    required_package_id: str | None = None
    required_package_version: str | None = None
    required_package_sha256: str | None = None
    write_disabled: bool = True
    device_display_name: str = "Máy AutoCAD Lab"

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        allowed_hosts = tuple(
            item.strip()
            for item in os.environ.get(
                "AUTOCAD_MCP_PUBLIC_V1_ALLOWED_HOSTS", "127.0.0.1:*;localhost:*;[::1]:*"
            ).split(";")
            if item.strip()
        )
        allowed_origins = tuple(
            item.strip()
            for item in os.environ.get("AUTOCAD_MCP_PUBLIC_V1_ALLOWED_ORIGINS", "").split(";")
            if item.strip()
        )
        fixture_tokens = tuple(
            (parts[0].strip(), parts[1].strip())
            for item in os.environ.get("AUTOCAD_MCP_PHASE3_FIXTURE_TOKENS", "").split(";")
            if "=" in item
            for parts in [item.split("=", 1)]
            if parts[0].strip() and parts[1].strip()
        )
        profile = os.environ.get("AUTOCAD_MCP_GATEWAY_PROFILE", "local").strip() or "local"
        if profile == "phase4_c1":
            device_id = os.environ.get("AUTOCAD_MCP_PHASE4_DEVICE_ID", "").strip()
            device_credential = os.environ.get(
                "AUTOCAD_MCP_PHASE4_DEVICE_CREDENTIAL", ""
            ).strip()
            fixture_tokens = (
                ((device_id, device_credential),)
                if device_id and device_credential
                else ()
            )
        config = cls(
            host=os.environ.get("AUTOCAD_MCP_PUBLIC_V1_HOST", "127.0.0.1").strip(),
            port=int(os.environ.get("AUTOCAD_MCP_PUBLIC_V1_PORT", "8765")),
            path=os.environ.get("AUTOCAD_MCP_PUBLIC_V1_PATH", "/mcp").strip(),
            stateless_http=os.environ.get("AUTOCAD_MCP_PUBLIC_V1_STATELESS_HTTP", "0")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            max_image_bytes=int(
                os.environ.get("AUTOCAD_MCP_MAX_IMAGE_BYTES", str(5 * 1024 * 1024))
            ),
            max_entities=int(
                os.environ.get("AUTOCAD_MCP_MAX_OBSERVATION_ENTITIES", str(MAX_ENTITIES_DEFAULT))
            ),
            max_entity_detail_calls=int(
                os.environ.get(
                    "AUTOCAD_MCP_MAX_ENTITY_DETAIL_CALLS",
                    str(MAX_ENTITY_DETAIL_CALLS_DEFAULT),
                )
            ),
            observation_timeout_seconds=float(
                os.environ.get(
                    "AUTOCAD_MCP_OBSERVATION_TIMEOUT_SECONDS",
                    str(OBSERVATION_TIMEOUT_SECONDS_DEFAULT),
                )
            ),
            max_snapshot_bytes=int(
                os.environ.get("AUTOCAD_MCP_MAX_SNAPSHOT_BYTES", str(MAX_SNAPSHOT_BYTES_DEFAULT))
            ),
            snapshot_ttl_seconds=float(
                os.environ.get("AUTOCAD_MCP_SNAPSHOT_TTL_SECONDS", str(SNAPSHOT_TTL_SECONDS_DEFAULT))
            ),
            max_snapshot_count=int(
                os.environ.get("AUTOCAD_MCP_MAX_SNAPSHOT_COUNT", str(MAX_SNAPSHOT_COUNT_DEFAULT))
            ),
            max_snapshot_store_bytes=int(
                os.environ.get(
                    "AUTOCAD_MCP_MAX_SNAPSHOT_STORE_BYTES",
                    str(MAX_SNAPSHOT_STORE_BYTES_DEFAULT),
                )
            ),
            profile=profile,
            db_path=(
                os.environ.get("AUTOCAD_MCP_PHASE4_DB_PATH", "").strip()
                if profile == "phase4_c1"
                else os.environ.get("AUTOCAD_MCP_PHASE3_DB_PATH", "").strip()
            ) or None,
            fixture_tokens=fixture_tokens,
            fixture_owner_subject=os.environ.get(
                (
                    "AUTOCAD_MCP_PHASE4_OWNER_SUBJECT"
                    if profile == "phase4_c1"
                    else "AUTOCAD_MCP_PHASE3_OWNER"
                ),
                "phase3-fixture-user",
            ).strip(),
            stale_after_seconds=int(os.environ.get("AUTOCAD_MCP_PHASE3_STALE_SECONDS", "45")),
            request_wait_timeout_seconds=float(
                os.environ.get(
                    "AUTOCAD_MCP_PHASE3_REQUEST_WAIT_TIMEOUT_SECONDS",
                    os.environ.get("AUTOCAD_MCP_PHASE3_TIMEOUT_SECONDS", "30"),
                )
            ),
            job_deadline_seconds=float(
                os.environ.get("AUTOCAD_MCP_PHASE3_JOB_DEADLINE_SECONDS", "300")
            ),
            oauth_issuer=os.environ.get("AUTOCAD_MCP_PHASE4_OAUTH_ISSUER", "").strip() or None,
            oauth_audience=os.environ.get("AUTOCAD_MCP_PHASE4_OAUTH_AUDIENCE", "").strip() or None,
            oauth_jwks_uri=os.environ.get("AUTOCAD_MCP_PHASE4_OAUTH_JWKS_URI", "").strip() or None,
            public_origin=os.environ.get("AUTOCAD_MCP_PHASE4_PUBLIC_ORIGIN", "").strip() or None,
            required_package_id=os.environ.get(
                "AUTOCAD_MCP_PHASE4_PACKAGE_ID", "autocad.lisp.drawing_info"
            ).strip() or None,
            required_package_version=os.environ.get(
                "AUTOCAD_MCP_PHASE4_PACKAGE_VERSION", "3.3-c1"
            ).strip() or None,
            required_package_sha256=os.environ.get(
                "AUTOCAD_MCP_PHASE4_PACKAGE_SHA256", ""
            ).strip() or None,
            write_disabled=os.environ.get("AUTOCAD_MCP_PHASE4_WRITE_DISABLED", "1")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
            device_display_name=os.environ.get(
                "AUTOCAD_MCP_PHASE4_DEVICE_DISPLAY_NAME", "Máy AutoCAD Lab"
            ).strip(),
        )
        return config.validate()

    def validate(self) -> "GatewayConfig":
        try:
            is_loopback = ip_address(self.host).is_loopback
        except ValueError:
            is_loopback = self.host.lower() == "localhost"
        if not is_loopback:
            raise ValueError("Phase 2 no-auth Gateway must bind to loopback")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if not self.path.startswith("/") or any(char.isspace() for char in self.path):
            raise ValueError("path must start with '/' and contain no whitespace")
        _validate_limit("max_image_bytes", self.max_image_bytes, MAX_IMAGE_BYTES_UPPER)
        _validate_limit("max_entities", self.max_entities, MAX_ENTITIES_UPPER)
        _validate_limit(
            "max_entity_detail_calls",
            self.max_entity_detail_calls,
            MAX_ENTITY_DETAIL_CALLS_UPPER,
        )
        _validate_limit(
            "observation_timeout_seconds",
            self.observation_timeout_seconds,
            OBSERVATION_TIMEOUT_SECONDS_UPPER,
        )
        _validate_limit("max_snapshot_bytes", self.max_snapshot_bytes, MAX_SNAPSHOT_BYTES_UPPER)
        _validate_limit(
            "snapshot_ttl_seconds", self.snapshot_ttl_seconds, SNAPSHOT_TTL_SECONDS_UPPER
        )
        _validate_limit(
            "max_snapshot_count", self.max_snapshot_count, MAX_SNAPSHOT_COUNT_UPPER
        )
        _validate_limit(
            "max_snapshot_store_bytes",
            self.max_snapshot_store_bytes,
            MAX_SNAPSHOT_STORE_BYTES_UPPER,
        )
        if self.max_snapshot_bytes > self.max_snapshot_store_bytes:
            raise ValueError("max_snapshot_bytes must not exceed max_snapshot_store_bytes")
        if self.profile not in {"local", "phase3_poc", "phase4_c1"}:
            raise ValueError("profile must be local, phase3_poc or phase4_c1")
        if not 1 <= self.stale_after_seconds <= 3600:
            raise ValueError("stale_after_seconds must be between 1 and 3600")
        if not 0 < self.effective_request_wait_timeout_seconds <= 600:
            raise ValueError("request_wait_timeout_seconds must be between 0 and 600")
        if not 1 <= self.job_deadline_seconds <= 86_400:
            raise ValueError("job_deadline_seconds must be between 1 and 86400")
        if self.profile == "phase3_poc":
            if not self.db_path:
                raise ValueError("phase3_poc requires an explicit db_path")
            if not self.fixture_tokens:
                raise ValueError("phase3_poc requires fixture device tokens")
            if not self.fixture_owner_subject:
                raise ValueError("phase3_poc requires a fixture owner subject")
        if self.profile == "phase4_c1":
            required = {
                "db_path": self.db_path,
                "lab device credential": self.fixture_tokens,
                "lab owner subject": self.fixture_owner_subject,
                "OAuth issuer": self.oauth_issuer,
                "OAuth audience": self.oauth_audience,
                "OAuth JWKS URI": self.oauth_jwks_uri,
                "public origin": self.public_origin,
                "package ID": self.required_package_id,
                "package version": self.required_package_version,
                "package SHA-256": self.required_package_sha256,
                "device display name": self.device_display_name,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError("phase4_c1 requires " + ", ".join(missing))
            if len(self.fixture_tokens) != 1:
                raise ValueError("phase4_c1 requires exactly one lab device")
            if not self.write_disabled:
                raise ValueError("phase4_c1 requires write_disabled=true")
            if not re.fullmatch(r"[0-9a-f]{64}", self.required_package_sha256 or ""):
                raise ValueError("phase4_c1 package SHA-256 must be 64 lowercase hex characters")
            for name, value in {
                "OAuth issuer": self.oauth_issuer,
                "OAuth JWKS URI": self.oauth_jwks_uri,
                "public origin": self.public_origin,
            }.items():
                parsed = urlsplit(value or "")
                if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
                    raise ValueError(f"phase4_c1 {name} must be a canonical HTTPS URL")
            if urlsplit(self.public_origin or "").path not in {"", "/"}:
                raise ValueError("phase4_c1 public origin must not contain a path")
        return self

    @property
    def required_package(self) -> dict[str, str]:
        if not (
            self.required_package_id
            and self.required_package_version
            and self.required_package_sha256
        ):
            return {}
        return {
            "package_id": self.required_package_id,
            "version": self.required_package_version,
            "sha256": self.required_package_sha256,
        }

    @property
    def effective_request_wait_timeout_seconds(self) -> float:
        if self.command_timeout_seconds is not None:
            return float(self.command_timeout_seconds)
        return float(self.request_wait_timeout_seconds)


def current_correlation_id(factory: CorrelationIdFactory | None = None) -> str:
    value = _correlation_id.get()
    if value:
        return value
    return (factory or (lambda: str(uuid.uuid4())))()


def _validate_limit(name: str, value: float, upper: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= upper:
        raise ValueError(f"{name} must be between 1 and {upper}")


def _parse_authority(
    value: str, *, allow_wildcard_port: bool
) -> tuple[str, int | str | None]:
    authority = value.strip()
    if not authority or any(character.isspace() for character in authority):
        raise ValueError("invalid authority")
    if any(character in authority for character in "/?#@"):
        raise ValueError("invalid authority")
    port_text: str | None = None
    if authority.startswith("["):
        closing = authority.find("]")
        if closing < 0:
            raise ValueError("invalid authority")
        host_text = authority[1:closing]
        remainder = authority[closing + 1 :]
        if remainder:
            if not remainder.startswith(":") or not remainder[1:]:
                raise ValueError("invalid authority")
            port_text = remainder[1:]
        host_name = str(ip_address(host_text)).lower()
    else:
        if authority.count(":") > 1:
            raise ValueError("IPv6 Host must be bracketed")
        if ":" in authority:
            host_text, port_text = authority.rsplit(":", 1)
        else:
            host_text = authority
        if not host_text or host_text.endswith("."):
            raise ValueError("invalid authority")
        try:
            host_name = str(ip_address(host_text)).lower()
        except ValueError:
            host_name = host_text.lower()
            if any(not (character.isalnum() or character in ".-") for character in host_name):
                raise ValueError("invalid authority")
    if port_text is None:
        port: int | str | None = None
    elif port_text == "*" and allow_wildcard_port:
        port = "*"
    elif port_text.isascii() and port_text.isdigit() and 1 <= int(port_text) <= 65535:
        port = int(port_text)
    else:
        raise ValueError("invalid authority")
    return host_name, port


def _origin_matches(origin: str, allowed: str) -> bool:
    try:
        return _canonical_origin(origin) == _canonical_origin(allowed)
    except ValueError:
        return False


def _canonical_origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid origin")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("invalid origin") from error
    default_port = 80 if parsed.scheme.lower() == "http" else 443
    return parsed.scheme.lower(), parsed.hostname.lower(), port or default_port


class CorrelationMiddleware:
    """Create and clean up one correlation ID for each HTTP request."""

    def __init__(self, app: Any, factory: CorrelationIdFactory | None = None) -> None:
        self.app = app
        self.factory = factory or (lambda: str(uuid.uuid4()))

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        token: Token[str | None] = _correlation_id.set(self.factory())
        try:
            await self.app(scope, receive, send)
        finally:
            _correlation_id.reset(token)


class CorrelationErrorMiddleware(Middleware):
    """Give in-memory MCP calls a correlation context and map schema errors safely."""

    def __init__(self, factory: CorrelationIdFactory) -> None:
        self.factory = factory

    async def on_call_tool(self, context: Any, call_next: Any) -> Any:
        return await self._run_with_correlation(context, call_next)

    async def on_read_resource(self, context: Any, call_next: Any) -> Any:
        return await self._run_with_correlation(context, call_next)

    async def _run_with_correlation(self, context: Any, call_next: Any) -> Any:
        token: Token[str | None] | None = None
        if _correlation_id.get() is None:
            token = _correlation_id.set(self.factory())
        correlation_id = current_correlation_id(self.factory)
        try:
            return await call_next(context)
        except FastMCPValidationError:
            raise ToolError(
                f"invalid_request: request is invalid; correlation_id={correlation_id}"
            ) from None
        finally:
            if token is not None:
                _correlation_id.reset(token)


class OuterHostOriginGuard:
    """Reject a bad Host/Origin before FastMCP can create a session."""

    def __init__(
        self,
        app: Any,
        allowed_hosts: list[str],
        allowed_origins: list[str],
        protected_path: str = "/mcp",
    ) -> None:
        self.app = app
        self.allowed_hosts = tuple(allowed_hosts)
        self.allowed_origins = tuple(allowed_origins)
        self.protected_path = protected_path.rstrip("/") or "/"

    @staticmethod
    def _host_matches(host: str, allowed: str) -> bool:
        if allowed == "*":
            return True
        try:
            host_name, host_port = _parse_authority(host, allow_wildcard_port=False)
            allowed_name, allowed_port = _parse_authority(
                allowed, allow_wildcard_port=True
            )
        except ValueError:
            return False
        return host_name == allowed_name and (
            allowed_port == "*" or allowed_port == host_port
        )

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "websocket" and scope["path"] == "/agent/ws":
            headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
            host = headers.get("host", "")
            origin = headers.get("origin")
            host_allowed = any(self._host_matches(host, item) for item in self.allowed_hosts)
            origin_allowed = origin is None or any(
                _origin_matches(origin, item) for item in self.allowed_origins
            )
            if not host_allowed or not origin_allowed:
                await send(
                    {
                        "type": "websocket.close",
                        "code": 4403,
                        "reason": "host or origin is not allowed",
                    }
                )
                return
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http" or not (
            scope["path"] == self.protected_path
            or scope["path"].startswith(self.protected_path + "/")
            or scope["path"].startswith("/.well-known/")
        ):
            await self.app(scope, receive, send)
            return
        headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
        host = headers.get("host", "")
        if not self.allowed_hosts or not any(
            self._host_matches(host, item) for item in self.allowed_hosts
        ):
            await PlainTextResponse("Host is not allowed", status_code=403)(
                scope, receive, send
            )
            return
        origin = headers.get("origin")
        if origin and (
            not self.allowed_origins
            or not any(_origin_matches(origin, allowed) for allowed in self.allowed_origins)
        ):
            await PlainTextResponse("Origin is not allowed", status_code=403)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)


def _tool_annotations(*, idempotent: bool) -> dict[str, bool]:
    return {
        "readOnlyHint": True,
        "idempotentHint": idempotent,
        "openWorldHint": False,
        "destructiveHint": False,
    }


def _principal(
    auth: RemoteAuthProvider | None,
    services: Any | None = None,
    correlation_id: str | None = None,
) -> Principal:
    correlation_id = correlation_id or current_correlation_id()
    token = get_access_token()
    if token is None:
        if auth is not None:
            raise ToolError(
                f"invalid_token: access token required; correlation_id={correlation_id}"
            )
        return Principal(
            subject=getattr(services, "owner_subject", LOCAL_SUBJECT),
            scopes=("autocad.read",),
        )
    subject = token.claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ToolError(f"invalid_token: subject claim required; correlation_id={correlation_id}")
    return Principal(subject=subject, scopes=tuple(token.scopes))


def _safe_error(error: GatewayError, correlation_id: str) -> ToolError:
    messages = {
        "invalid_request": "request is invalid",
        "not_found": "requested resource was not found",
        "backend_error": "CAD backend operation failed",
        "response_too_large": "response exceeds the configured size limit",
        "observation_too_large": "the CAD observation exceeds configured limits",
        "observation_budget_exceeded": "the CAD observation exceeded its execution budget",
        "preview_unavailable": "a valid PNG preview is unavailable",
        "device_offline": "the selected CAD device is offline",
        "capability_missing": "the selected device lacks the requested capability",
        "job_in_progress": "the job is still in progress",
        "deadline_expired": "the job deadline has expired",
        "dispatcher_timeout": "the Agent did not finish the job in time",
        "idempotency_conflict": "the request conflicts with an existing job",
        "payload_mismatch": "the command payload does not match the existing command",
        "agent_rejected": "the Agent rejected the command",
        "active_document_changed": "the active AutoCAD document changed during the read",
        "autocad_busy": "AutoCAD is running another command",
        "autocad_not_running": "AutoCAD is not running",
        "command_routing_failed": "the Agent could not route the read command to AutoCAD",
        "dispatcher_not_loaded": "the required AutoLISP dispatcher is not loaded",
        "package_mismatch": "the Agent package does not match the required version",
        "ipc_result_invalid": "AutoCAD returned invalid bounded read evidence",
        "modal_dialog_active": "AutoCAD has a modal dialog open",
        "no_active_document": "AutoCAD has no active document",
        "paused_by_user": "the local user paused remote tasks",
        "outcome_unknown": "the write-like operation has an unknown outcome",
        "internal_error": "operation failed",
    }
    public_code = error.code if error.code in messages else "internal_error"
    details: list[str] = []
    if error.job_id and _SAFE_PUBLIC_ID.fullmatch(error.job_id):
        details.append(f"job_id={error.job_id}")
    if error.job_state in _SAFE_JOB_STATES:
        details.append(f"job_state={error.job_state}")
    details.append(f"correlation_id={correlation_id}")
    return ToolError(
        f"{public_code}: {messages[public_code]}; " + "; ".join(details)
    )


async def _run(call: Callable[[], Any], correlation_id: str) -> Any:
    try:
        return await call()
    except ToolError:
        raise
    except ValidationError:
        raise ToolError(
            f"invalid_request: request is invalid; correlation_id={correlation_id}"
        ) from None
    except GatewayError as error:
        logger.info(
            "Gateway operation rejected",
            extra={"correlation_id": correlation_id, "error_code": error.code},
        )
        raise _safe_error(error, correlation_id) from None
    except Exception:
        logger.exception(
            "Unexpected Gateway operation failure",
            extra={"correlation_id": correlation_id},
        )
        raise ToolError(
            f"internal_error: operation failed; correlation_id={correlation_id}"
        ) from None


def build_mcp_server(
    services: GatewayServices,
    auth: RemoteAuthProvider | None = None,
    *,
    correlation_id_factory: CorrelationIdFactory | None = None,
) -> FastMCP:
    """Build exactly the public v1 read surface."""

    make_correlation_id = correlation_id_factory or (lambda: str(uuid.uuid4()))
    auth_check = require_scopes("autocad.read") if auth is not None else None
    phase3 = bool(getattr(services, "is_phase3", False))
    phase4 = bool(getattr(services, "is_phase4", False))
    mcp = FastMCP(
        name=(
            "AutoCAD Gateway public v1.2"
            if phase4
            else "AutoCAD Gateway public v1.1"
            if phase3
            else "AutoCAD Gateway public v1"
        ),
        version="0.4.0" if phase4 else "0.3.0" if phase3 else "0.2.0",
        auth=auth,
        mask_error_details=True,
    )
    mcp.add_middleware(CorrelationErrorMiddleware(make_correlation_id))

    @mcp.tool(
        name="cad_list_devices",
        title="List CAD devices",
        description="List the bounded local CAD devices available for read-only observation.",
        output_schema=(
            CadListDevicesOutputC1.model_json_schema()
            if phase4
            else CadListDevicesOutput.model_json_schema()
        ),
        annotations=_tool_annotations(idempotent=True),
        auth=auth_check,
    )
    async def cad_list_devices(
        online_only: bool = False,
        capability: str | None = None,
        *,
        ctx: Context,
    ) -> dict[str, Any]:
        del ctx
        correlation_id = current_correlation_id(make_correlation_id)
        result = await _run(
            lambda: services.list_devices(
                CadListDevicesInput(online_only=online_only, capability=capability),
                _principal(auth, services, correlation_id),
                correlation_id,
            ),
            correlation_id,
        )
        return result.model_dump(mode="json")

    async def _call_cad_observe(
        request: CadObserveInput | CadObserveInputDurable,
    ) -> ToolResult:
        correlation_id = current_correlation_id(make_correlation_id)
        result = await _run(
            lambda: services.observe(
                request,
                _principal(auth, services, correlation_id),
                correlation_id,
            ),
            correlation_id,
        )
        content: list[Any] = [
            TextContent(type="text", text="CAD observation ready."),
            ResourceLink(
                type="resource_link",
                name="snapshot-summary",
                title="Snapshot summary",
                uri=result.summary_uri,
                mimeType="application/json",
            ),
            ResourceLink(
                type="resource_link",
                name="snapshot-entities",
                title="Snapshot entities",
                uri=result.entities_uri,
                mimeType="application/json",
            ),
        ]
        content.extend(
            ResourceLink(
                type="resource_link",
                name="snapshot-artifact",
                title="Snapshot preview image",
                uri=artifact.uri,
                mimeType=artifact.mime_type,
            )
            for artifact in result.artifact_refs
        )
        return ToolResult(
            content=content,
            structured_content=result.model_dump(mode="json"),
        )

    if phase3:

        @mcp.tool(
            name="cad_observe",
            title="Observe a CAD device",
            description="Create a bounded read-only CAD snapshot with stable revision and resource references.",
            output_schema=(
                CadObserveOutputC1.model_json_schema()
                if phase4
                else CadObserveOutputDurable.model_json_schema()
            ),
            annotations=_tool_annotations(idempotent=False),
            auth=auth_check,
        )
        async def cad_observe_durable(
            device_id: str,
            observation_level: Literal["summary", "detail"] = "summary",
            include_preview_image: bool = False,
            idempotency_key: Annotated[
                str | None,
                Field(min_length=1, max_length=128),
            ] = None,
            *,
            ctx: Context,
        ) -> ToolResult:
            del ctx
            return await _call_cad_observe(
                CadObserveInputDurable(
                    device_id=device_id,
                    observation_level=observation_level,
                    include_preview_image=include_preview_image,
                    idempotency_key=idempotency_key,
                )
            )

    else:

        @mcp.tool(
            name="cad_observe",
            title="Observe a CAD device",
            description="Create a bounded read-only CAD snapshot with stable revision and resource references.",
            output_schema=CadObserveOutput.model_json_schema(),
            annotations=_tool_annotations(idempotent=False),
            auth=auth_check,
        )
        async def cad_observe_local(
            device_id: str,
            observation_level: Literal["summary", "detail"] = "summary",
            include_preview_image: bool = False,
            *,
            ctx: Context,
        ) -> ToolResult:
            del ctx
            return await _call_cad_observe(
                CadObserveInput(
                    device_id=device_id,
                    observation_level=observation_level,
                    include_preview_image=include_preview_image,
                )
            )

    @mcp.tool(
        name="cad_query",
        title="Query a CAD snapshot",
        description="Query a known CAD snapshot by entity type or layer with stable bounded pagination.",
        output_schema=CadQueryOutput.model_json_schema(),
        annotations=_tool_annotations(idempotent=True),
        auth=auth_check,
    )
    async def cad_query(
        snapshot_id: str,
        types: list[str] | None = None,
        layers: list[str] | None = None,
        cursor: str | None = None,
        limit: int = 50,
        *,
        ctx: Context,
    ) -> dict[str, Any]:
        del ctx
        correlation_id = current_correlation_id(make_correlation_id)
        result = await _run(
            lambda: services.query(
                CadQueryInput(
                    snapshot_id=snapshot_id,
                    types=types or [],
                    layers=layers or [],
                    cursor=cursor,
                    limit=limit,
                ),
                _principal(auth, services, correlation_id),
                correlation_id,
            ),
            correlation_id,
        )
        return result.model_dump(mode="json")

    if phase3:

        @mcp.tool(
            name="cad_get_job",
            title="Get a CAD job",
            description="Read the bounded state, progress and ordered events for an observation job.",
            output_schema=(
                CadGetJobOutputC1.model_json_schema()
                if phase4
                else CadGetJobOutput.model_json_schema()
            ),
            annotations=_tool_annotations(idempotent=True),
            auth=auth_check,
        )
        async def cad_get_job(
            job_id: str,
            event_cursor: str | None = None,
            event_limit: int = 50,
            *,
            ctx: Context,
        ) -> dict[str, Any]:
            del ctx
            correlation_id = current_correlation_id(make_correlation_id)
            result = await _run(
                lambda: services.get_job(
                    CadGetJobInput(
                        job_id=job_id,
                        event_cursor=event_cursor,
                        event_limit=event_limit,
                    ),
                    _principal(auth, services, correlation_id),
                    correlation_id,
                ),
                correlation_id,
            )
            return result.model_dump(mode="json")

    @mcp.resource(
        "cad://devices/{device_id}/capabilities",
        name="CAD device capabilities",
        description="Read bounded capabilities for a known CAD device.",
        mime_type="application/json",
        auth=auth_check,
    )
    async def device_capabilities(device_id: str) -> ResourceResult:
        correlation_id = current_correlation_id(make_correlation_id)
        value = await _run(
            lambda: services.read_device_capabilities(
                device_id, _principal(auth, services, correlation_id)
            ),
            correlation_id,
        )
        return ResourceResult([ResourceContent(content=value, mime_type="application/json")])

    @mcp.resource(
        "cad://snapshots/{snapshot_id}/summary",
        name="CAD snapshot summary",
        description="Read the bounded JSON summary for a known CAD snapshot.",
        mime_type="application/json",
        auth=auth_check,
    )
    async def snapshot_summary(snapshot_id: str) -> ResourceResult:
        correlation_id = current_correlation_id(make_correlation_id)
        value = await _run(
            lambda: services.read_snapshot_summary(
                snapshot_id, _principal(auth, services, correlation_id)
            ),
            correlation_id,
        )
        return ResourceResult([ResourceContent(content=value, mime_type="application/json")])

    @mcp.resource(
        "cad://snapshots/{snapshot_id}/entities{?cursor,limit,types,layers}",
        name="CAD snapshot entities",
        description="Read a bounded, filtered page of entities from a known CAD snapshot.",
        mime_type="application/json",
        auth=auth_check,
    )
    async def snapshot_entities(
        snapshot_id: str,
        cursor: str | None = None,
        limit: int = 50,
        types: str | None = None,
        layers: str | None = None,
    ) -> ResourceResult:
        correlation_id = current_correlation_id(make_correlation_id)
        value = await _run(
            lambda: services.read_snapshot_entities(
                snapshot_id,
                _principal(auth, services, correlation_id),
                types=_split_query_values(types),
                layers=_split_query_values(layers),
                cursor=cursor,
                limit=limit,
                correlation_id=correlation_id,
            ),
            correlation_id,
        )
        return ResourceResult([ResourceContent(content=value, mime_type="application/json")])

    @mcp.resource(
        "cad://artifacts/{artifact_id}",
        name="CAD artifact",
        description="Read a bounded PNG preview artifact referenced by a CAD snapshot.",
        mime_type="image/png",
        auth=auth_check,
    )
    async def artifact(artifact_id: str) -> ResourceResult:
        correlation_id = current_correlation_id(make_correlation_id)
        value = await _run(
            lambda: services.read_artifact(
                artifact_id, _principal(auth, services, correlation_id)
            ),
            correlation_id,
        )
        return ResourceResult([ResourceContent(content=value, mime_type="image/png")])

    if phase3:

        @mcp.resource(
            "cad://jobs/{job_id}",
            name="CAD job",
            description="Read the bounded durable state and ordered events for a CAD job.",
            mime_type="application/json",
            auth=auth_check,
        )
        async def job_resource(job_id: str) -> ResourceResult:
            correlation_id = current_correlation_id(make_correlation_id)
            value = await _run(
                lambda: services.read_job_resource(
                    job_id, _principal(auth, services, correlation_id)
                ),
                correlation_id,
            )
            return ResourceResult([ResourceContent(content=value, mime_type="application/json")])

    @mcp.prompt(
        name="plan_cad_change",
        title="Plan a CAD change",
        description="Guide a read-only inspection and planning conversation before any drawing change.",
    )
    async def plan_cad_change() -> list[PromptMessage]:
        return [
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        "Observe the selected CAD device, query the relevant snapshot entities, "
                        "and describe a proposed change. Stop before modifying the drawing."
                    ),
                ),
            )
        ]

    @mcp.prompt(
        name="repair_after_validation",
        title="Repair after validation",
        description="Guide read-only validation follow-up without changing the drawing.",
    )
    async def repair_after_validation() -> list[PromptMessage]:
        return [
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        "Observe again, query the affected entity type or layer, compare the "
                        "snapshot revision, and report what remains to be repaired. Do not edit."
                    ),
                ),
            )
        ]

    return mcp


def _split_query_values(value: str | None) -> list[str]:
    if value is None or value == "":
        return []
    return value.split(",")


def create_app(
    services: Any,
    auth: RemoteAuthProvider | None = None,
    *,
    config: GatewayConfig | None = None,
    stateless_http: bool | None = None,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    correlation_id_factory: CorrelationIdFactory | None = None,
) -> Starlette:
    config = (config or GatewayConfig.from_env()).validate()
    if config.profile == "phase4_c1" and auth is None:
        raise ValueError("phase4_c1 requires OAuth authentication")
    if stateless_http is not None:
        config = replace(config, stateless_http=stateless_http)
    configured_hosts = allowed_hosts if allowed_hosts is not None else list(config.allowed_hosts)
    configured_origins = (
        allowed_origins if allowed_origins is not None else list(config.allowed_origins)
    )
    if auth is None:
        try:
            ip_address(config.host).is_loopback
        except ValueError:
            if config.host.lower() != "localhost":
                raise ValueError("no-auth public v1 must bind to loopback")
    mcp = build_mcp_server(
        services, auth, correlation_id_factory=correlation_id_factory
    )
    mcp_app = mcp.http_app(
        path=config.path,
        stateless_http=config.stateless_http,
        host_origin_protection=True,
        allowed_hosts=configured_hosts,
        allowed_origins=configured_origins,
    )

    async def healthz(request: Request) -> PlainTextResponse:
        del request
        return PlainTextResponse("ok")

    async def readyz(request: Request) -> PlainTextResponse:
        del request
        database = getattr(services, "database", None)
        if database is not None and not database.is_open:
            return PlainTextResponse("not ready", status_code=503)
        readiness = getattr(services, "is_ready", None)
        if callable(readiness) and not readiness():
            return PlainTextResponse("not ready", status_code=503)
        return PlainTextResponse("ready")

    async def agent_ws(websocket: Any) -> None:
        transport = getattr(services, "job_service", None)
        authenticator = getattr(services, "agent_authenticator", None)
        registry = getattr(services, "registry", None)
        if transport is None or authenticator is None or registry is None:
            await websocket.close(code=4404, reason="Agent transport is disabled")
            return
        from .infrastructure.agent_transport.websocket_endpoint import serve_agent_websocket

        await serve_agent_websocket(
            websocket,
            authenticator=authenticator,
            registry=registry,
            on_message=transport.handle_message,
            validate_message=getattr(transport, "validate_message", None),
            on_connected=getattr(services, "on_agent_connected", transport.handle_connected),
            on_heartbeat=getattr(services, "on_agent_heartbeat", None),
            on_disconnected=getattr(
                services,
                "on_agent_disconnected",
                lambda connection: transport.handle_disconnect(connection.device_id),
            ),
        )

    @asynccontextmanager
    async def lifespan(app: Starlette):
        await services.initialize()
        try:
            async with mcp_app.lifespan(app):
                yield
        finally:
            shutdown = getattr(services, "shutdown", None)
            if shutdown is not None:
                await shutdown()

    outer_app: Any = Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/readyz", readyz, methods=["GET"]),
            WebSocketRoute("/agent/ws", agent_ws),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )
    outer_app = OuterHostOriginGuard(
        outer_app, configured_hosts, configured_origins, protected_path=config.path
    )
    return CorrelationMiddleware(outer_app, correlation_id_factory)
