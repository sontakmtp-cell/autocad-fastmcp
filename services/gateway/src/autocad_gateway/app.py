"""FastMCP public v1 facade and local-only outer ASGI application."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, Literal

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server.auth import RemoteAuthProvider, require_scopes
from fastmcp.server.dependencies import get_access_token
from fastmcp.tools.tool import ToolResult
from mcp.types import PromptMessage, ResourceLink, TextContent
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

from .contracts import (
    CadListDevicesInput,
    CadListDevicesOutput,
    CadObserveInput,
    CadObserveOutput,
    CadQueryInput,
    CadQueryOutput,
    Principal,
)
from .services import GatewayError, GatewayServices, LOCAL_SUBJECT


CorrelationIdFactory = Callable[[], str]
_correlation_id: ContextVar[str | None] = ContextVar("cad_gateway_correlation_id", default=None)


@dataclass(frozen=True)
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/mcp"
    stateless_http: bool = False
    allowed_hosts: tuple[str, ...] = ("127.0.0.1:*", "localhost:*", "[::1]:*")
    allowed_origins: tuple[str, ...] = ()
    max_image_bytes: int = 5 * 1024 * 1024

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
        if self.max_image_bytes <= 0:
            raise ValueError("max_image_bytes must be greater than zero")
        return self


def current_correlation_id(factory: CorrelationIdFactory | None = None) -> str:
    value = _correlation_id.get()
    if value:
        return value
    return (factory or (lambda: str(uuid.uuid4())))()


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


class OuterHostOriginGuard:
    """Reject a bad Host/Origin before FastMCP can create a session."""

    def __init__(
        self,
        app: Any,
        allowed_hosts: list[str],
        allowed_origins: list[str],
    ) -> None:
        self.app = app
        self.allowed_hosts = tuple(allowed_hosts)
        self.allowed_origins = tuple(allowed_origins)

    @staticmethod
    def _host_matches(host: str, allowed: str) -> bool:
        if allowed == "*":
            return True
        if allowed.endswith(":*"):
            return host.startswith(allowed[:-2] + ":")
        return host == allowed or host.split(":", 1)[0] == allowed

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not (
            scope["path"].startswith("/mcp")
            or scope["path"].startswith("/.well-known/")
        ):
            await self.app(scope, receive, send)
            return
        headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
        host = headers.get("host", "")
        if self.allowed_hosts and not any(
            self._host_matches(host, item) for item in self.allowed_hosts
        ):
            await PlainTextResponse("Host is not allowed", status_code=403)(
                scope, receive, send
            )
            return
        origin = headers.get("origin")
        if origin and self.allowed_origins and origin not in self.allowed_origins:
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


def _principal(auth: RemoteAuthProvider | None) -> Principal:
    token = get_access_token()
    if token is None:
        if auth is not None:
            raise ToolError("invalid_token: access token required")
        return Principal(subject=LOCAL_SUBJECT, scopes=("autocad.read",))
    subject = token.claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ToolError("invalid_token: subject claim required")
    return Principal(subject=subject, scopes=tuple(token.scopes))


def _safe_error(error: GatewayError) -> ToolError:
    messages = {
        "invalid_request": "request is invalid",
        "not_found": "requested resource was not found",
        "backend_error": "CAD backend operation failed",
        "response_too_large": "response exceeds the configured size limit",
        "internal_error": "operation failed",
    }
    return ToolError(f"{error.code}: {messages.get(error.code, messages['internal_error'])}")


async def _run(call: Any) -> Any:
    try:
        return await call
    except ToolError:
        raise
    except ValidationError:
        raise ToolError("invalid_request: request is invalid") from None
    except GatewayError as error:
        raise _safe_error(error) from None
    except Exception:
        raise ToolError("internal_error: operation failed") from None


def build_mcp_server(
    services: GatewayServices,
    auth: RemoteAuthProvider | None = None,
    *,
    correlation_id_factory: CorrelationIdFactory | None = None,
) -> FastMCP:
    """Build exactly the public v1 read surface."""

    make_correlation_id = correlation_id_factory or (lambda: str(uuid.uuid4()))
    auth_check = require_scopes("autocad.read") if auth is not None else None
    mcp = FastMCP(
        name="AutoCAD Gateway public v1",
        version="0.2.0",
        auth=auth,
        mask_error_details=True,
    )

    @mcp.tool(
        name="cad_list_devices",
        title="List CAD devices",
        description="List the bounded local CAD devices available for read-only observation.",
        output_schema=CadListDevicesOutput.model_json_schema(),
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
        request = CadListDevicesInput(online_only=online_only, capability=capability)
        result = await _run(
            services.list_devices(
                request, _principal(auth), current_correlation_id(make_correlation_id)
            )
        )
        return result.model_dump(mode="json")

    @mcp.tool(
        name="cad_observe",
        title="Observe a CAD device",
        description="Create a bounded read-only CAD snapshot with stable revision and resource references.",
        output_schema=CadObserveOutput.model_json_schema(),
        annotations=_tool_annotations(idempotent=False),
        auth=auth_check,
    )
    async def cad_observe(
        device_id: str,
        observation_level: Literal["summary", "detail"] = "summary",
        include_preview_image: bool = False,
        *,
        ctx: Context,
    ) -> ToolResult:
        del ctx
        request = CadObserveInput(
            device_id=device_id,
            observation_level=observation_level,
            include_preview_image=include_preview_image,
        )
        principal = _principal(auth)
        result = await _run(
            services.observe(request, principal, current_correlation_id(make_correlation_id))
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
        request = CadQueryInput(
            snapshot_id=snapshot_id,
            types=types or [],
            layers=layers or [],
            cursor=cursor,
            limit=limit,
        )
        result = await _run(
            services.query(
                request, _principal(auth), current_correlation_id(make_correlation_id)
            )
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
        value = await _run(services.read_device_capabilities(device_id, _principal(auth)))
        return ResourceResult([ResourceContent(content=value, mime_type="application/json")])

    @mcp.resource(
        "cad://snapshots/{snapshot_id}/summary",
        name="CAD snapshot summary",
        description="Read the bounded JSON summary for a known CAD snapshot.",
        mime_type="application/json",
        auth=auth_check,
    )
    async def snapshot_summary(snapshot_id: str) -> ResourceResult:
        value = await _run(services.read_snapshot_summary(snapshot_id, _principal(auth)))
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
        type_values = _split_query_values(types)
        layer_values = _split_query_values(layers)
        value = await _run(
            services.read_snapshot_entities(
                snapshot_id,
                _principal(auth),
                types=type_values,
                layers=layer_values,
                cursor=cursor,
                limit=limit,
                correlation_id=current_correlation_id(make_correlation_id),
            )
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
        value = await _run(services.read_artifact(artifact_id, _principal(auth)))
        return ResourceResult([ResourceContent(content=value, mime_type="image/png")])

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
    if not value:
        return []
    return [item for item in value.split(",") if item]


def create_app(
    services: GatewayServices,
    auth: RemoteAuthProvider | None = None,
    *,
    config: GatewayConfig | None = None,
    stateless_http: bool | None = None,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    correlation_id_factory: CorrelationIdFactory | None = None,
) -> Starlette:
    config = (config or GatewayConfig.from_env()).validate()
    if stateless_http is not None:
        config = GatewayConfig(
            host=config.host,
            port=config.port,
            path=config.path,
            stateless_http=stateless_http,
            allowed_hosts=config.allowed_hosts,
            allowed_origins=config.allowed_origins,
            max_image_bytes=config.max_image_bytes,
        )
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

    @asynccontextmanager
    async def lifespan(app: Starlette):
        await services.initialize()
        async with mcp_app.lifespan(app):
            yield

    outer_app: Any = Starlette(
        routes=[Route("/healthz", healthz, methods=["GET"]), Mount("/", app=mcp_app)],
        lifespan=lifespan,
    )
    outer_app = OuterHostOriginGuard(
        outer_app, configured_hosts, configured_origins
    )
    return CorrelationMiddleware(outer_app, correlation_id_factory)
