"""FastMCP adapter and outer ASGI application for the compatibility spike."""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server.auth import RemoteAuthProvider, require_scopes
from fastmcp.server.dependencies import get_access_token
from fastmcp.tools.tool import ToolResult
from mcp.types import ImageContent, ResourceLink, TextContent
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

from .contracts import (
    CadGetJobInput,
    CadGetJobOutput,
    CadListDevicesInput,
    CadListDevicesOutput,
    CadObserveInput,
    CadObserveOutput,
    DeviceId,
    EventCursor,
    JobId,
    ObservationLevel,
    StrictBoolean,
)
from .services import ArtifactPayload, Phase0Services, Principal, is_valid_png


CorrelationIdFactory = Callable[[], str]


class _OuterHostOriginGuard:
    """Return the Phase 0 contract's 403 before FastMCP sees a bad request."""

    def __init__(self, app: Any, allowed_hosts: list[str] | None, allowed_origins: list[str] | None):
        self.app = app
        self.allowed_hosts = tuple(allowed_hosts or ())
        self.allowed_origins = tuple(allowed_origins or ())

    @staticmethod
    def _host_matches(host: str, allowed: str) -> bool:
        if allowed == "*":
            return True
        if allowed.endswith(":*"):
            return host.startswith(allowed[:-2] + ":")
        return host == allowed

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not (
            scope["path"].startswith("/mcp")
            or scope["path"].startswith("/.well-known/")
        ):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        if self.allowed_hosts and not any(self._host_matches(host, item) for item in self.allowed_hosts):
            await PlainTextResponse("Host is not allowed", status_code=403)(scope, receive, send)
            return
        origin = headers.get("origin")
        if origin and self.allowed_origins and origin not in self.allowed_origins:
            await PlainTextResponse("Origin is not allowed", status_code=403)(scope, receive, send)
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
            raise ToolError("unauthorized: access token required")
        return Principal(subject="local-test", scopes=("autocad.read",))
    subject = token.claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise ToolError("invalid_token: subject claim required")
    return Principal(subject=subject, scopes=tuple(token.scopes))


def _error(result: Any) -> ToolError:
    code = getattr(result, "error_code", None) or "backend_error"
    if code == "not_found":
        return ToolError("not_found: requested resource was not found")
    if code == "backend_error":
        return ToolError("backend_error: backend operation failed")
    return ToolError("internal_error: operation failed")


def _preview_unavailable() -> ToolError:
    return ToolError("preview_unavailable: preview image is unavailable")


def _validated_png(result: Any) -> bytes:
    if not getattr(result, "ok", False):
        raise _preview_unavailable()
    artifact = getattr(result, "payload", None)
    if not isinstance(artifact, ArtifactPayload):
        raise _preview_unavailable()
    if artifact.mime_type != "image/png" or not is_valid_png(artifact.data):
        raise _preview_unavailable()
    return artifact.data


async def _run_service(call: Any) -> dict[str, Any]:
    try:
        result = await call
    except ToolError:
        raise
    except Exception:
        raise ToolError("internal_error: unexpected service failure") from None
    if not result.ok:
        raise _error(result)
    if not isinstance(result.payload, dict):
        raise ToolError("internal_error: invalid service result")
    return result.payload


def build_mcp_server(
    services: Phase0Services,
    auth: RemoteAuthProvider | None,
    stateless_http: bool,
    *,
    correlation_id_factory: CorrelationIdFactory | None = None,
) -> FastMCP:
    """Build only the three-tool facade; transport is configured separately."""

    del stateless_http
    make_correlation_id = correlation_id_factory or (lambda: str(uuid.uuid4()))
    auth_check = require_scopes("autocad.read") if auth is not None else None
    mcp = FastMCP(
        name="AutoCAD Gateway Phase 0",
        version="0.1.0",
        auth=auth,
        mask_error_details=True,
        strict_input_validation=True,
    )

    @mcp.tool(
        name="cad_list_devices",
        title="List CAD devices",
        description="Use this when you need the bounded list of available CAD devices.",
        output_schema=CadListDevicesOutput.model_json_schema(),
        annotations=_tool_annotations(idempotent=True),
        auth=auth_check,
    )
    async def cad_list_devices(
        online_only: StrictBoolean = False,
        capability: str | None = None,
        *,
        ctx: Context,
    ) -> dict[str, Any]:
        del ctx
        principal = _principal(auth)
        request = CadListDevicesInput(online_only=online_only, capability=capability)
        correlation_id = make_correlation_id()
        return await _run_service(services.list_devices(request, principal, correlation_id))

    @mcp.tool(
        name="cad_observe",
        title="Observe a CAD device",
        description="Use this when you need a bounded read-only CAD snapshot and its artifact references.",
        output_schema=CadObserveOutput.model_json_schema(),
        annotations=_tool_annotations(idempotent=False),
        auth=auth_check,
    )
    async def cad_observe(
        device_id: DeviceId,
        observation_level: ObservationLevel = "summary",
        include_preview_image: StrictBoolean = False,
        *,
        ctx: Context,
    ) -> ToolResult:
        del ctx
        principal = _principal(auth)
        request = CadObserveInput(
            device_id=device_id,
            observation_level=observation_level,
            include_preview_image=include_preview_image,
        )
        correlation_id = make_correlation_id()
        output = await _run_service(services.observe(request, principal, correlation_id))
        result = CadObserveOutput.model_validate(output)
        content: list[Any] = [
            TextContent(type="text", text="CAD observation ready."),
            ResourceLink(
                type="resource_link",
                name="snapshot-summary",
                title="Snapshot summary",
                uri=result.summary_uri,
                mimeType="application/json",
            ),
        ]
        if include_preview_image:
            preview_ref = next(
                (item for item in result.artifact_refs if item.mime_type == "image/png"),
                None,
            )
            if preview_ref is None:
                raise _preview_unavailable()
            preview = await services.read_artifact(
                preview_ref.artifact_id,
                principal,
                correlation_id,
            )
            preview_bytes = _validated_png(preview)
            content.append(
                ImageContent(
                    type="image",
                    data=base64.b64encode(preview_bytes).decode("ascii"),
                    mimeType="image/png",
                )
            )
        return ToolResult(
            content=content,
            structured_content=result.model_dump(mode="json"),
        )

    @mcp.tool(
        name="cad_get_job",
        title="Get CAD job status",
        description="Use this when you need the bounded status of a known CAD job.",
        output_schema=CadGetJobOutput.model_json_schema(),
        annotations=_tool_annotations(idempotent=True),
        auth=auth_check,
    )
    async def cad_get_job(
        job_id: JobId,
        event_cursor: EventCursor | None = None,
        *,
        ctx: Context,
    ) -> dict[str, Any]:
        del ctx
        principal = _principal(auth)
        request = CadGetJobInput(job_id=job_id, event_cursor=event_cursor)
        correlation_id = make_correlation_id()
        return await _run_service(services.get_job(request, principal, correlation_id))

    @mcp.resource(
        "cad://snapshots/{snapshot_id}/summary",
        name="CAD snapshot summary",
        description="Read the bounded JSON summary for a known CAD snapshot.",
        mime_type="application/json",
        auth=auth_check,
    )
    async def snapshot_summary(snapshot_id: str) -> ResourceResult:
        result = await services.read_snapshot(
            snapshot_id,
            _principal(auth),
            make_correlation_id(),
        )
        if not result.ok:
            raise _error(result)
        if not isinstance(result.payload, str):
            raise ToolError("internal_error: invalid snapshot result")
        return ResourceResult(
            [ResourceContent(content=result.payload, mime_type="application/json")]
        )

    @mcp.resource(
        "cad://artifacts/{artifact_id}",
        name="CAD artifact",
        description="Read a bounded PNG artifact referenced by a CAD snapshot.",
        mime_type="image/png",
        auth=auth_check,
    )
    async def artifact(artifact_id: str) -> ResourceResult:
        result = await services.read_artifact(
            artifact_id,
            _principal(auth),
            make_correlation_id(),
        )
        if not result.ok:
            raise _error(result)
        payload = _validated_png(result)
        return ResourceResult(
            [ResourceContent(content=payload, mime_type="image/png")]
        )

    return mcp


def create_app(
    services: Phase0Services,
    auth: RemoteAuthProvider | None,
    *,
    stateless_http: bool = True,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    correlation_id_factory: CorrelationIdFactory | None = None,
) -> Starlette:
    """Create an outer ASGI app with /healthz and the FastMCP /mcp endpoint."""

    mcp = build_mcp_server(
        services,
        auth,
        stateless_http,
        correlation_id_factory=correlation_id_factory,
    )
    mcp_app = mcp.http_app(
        path="/mcp",
        stateless_http=stateless_http,
        host_origin_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )

    async def healthz(request: Request) -> PlainTextResponse:
        del request
        return PlainTextResponse("ok")

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp_app.lifespan(app):
            yield

    outer_app = Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )
    if allowed_hosts is not None or allowed_origins is not None:
        return _OuterHostOriginGuard(outer_app, allowed_hosts, allowed_origins)
    return outer_app
