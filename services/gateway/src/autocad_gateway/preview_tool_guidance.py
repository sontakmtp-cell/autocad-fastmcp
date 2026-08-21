"""LLM-facing guidance and runtime binding for CAD viewport preview."""

from __future__ import annotations

from collections.abc import Callable
from types import MethodType
from typing import Any

from fastmcp.server.transforms import ToolTransform
from fastmcp.tools.tool_transform import ToolTransformConfig

from .application.job_service import DurableJobService
from .durable_services import DurableGatewayServices
from .preview_image_compat import (
    _observe_with_preview,
    _read_preview_artifact,
    _safe_agent_error_with_preview,
    _validate_c1_with_preview,
    install_preview_image_compat,
)


PREVIEW_AWARE_OBSERVE_DESCRIPTION = (
    "Create a bounded read-only CAD snapshot with stable revision and resource "
    "references. Set include_preview_image=true whenever the user asks to see, "
    "visually inspect, verify the appearance/layout, or otherwise needs an image "
    "of the current AutoCAD drawing. A successful preview is returned as an "
    "image/png artifact resource. Leave include_preview_image=false when only "
    "structured drawing/entity data is needed."
)


def _bind_preview_runtime(services: Any) -> None:
    """Bind preview support to the exact durable service used by FastMCP.

    Package-import monkeypatches are intentionally retained for backwards
    compatibility, but production launchers can import modules in different
    orders.  Binding the active service instance here makes the public MCP
    route deterministic and prevents a later class-level compatibility patch
    from restoring the historical ``include_preview_image`` rejection.
    """

    install_preview_image_compat()
    if not isinstance(services, DurableGatewayServices):
        return

    DurableJobService._validate_c1_observation = _validate_c1_with_preview
    DurableJobService._safe_agent_error = staticmethod(_safe_agent_error_with_preview)
    services.observe = MethodType(_observe_with_preview, services)
    services.read_artifact = MethodType(_read_preview_artifact, services)


def wrap_build_mcp_server(build_server: Callable[..., Any]) -> Callable[..., Any]:
    """Decorate the existing server factory and bind preview runtime support."""

    def build_with_preview_guidance(*args: Any, **kwargs: Any) -> Any:
        services = args[0] if args else kwargs.get("services")
        _bind_preview_runtime(services)
        mcp = build_server(*args, **kwargs)
        mcp.add_transform(
            ToolTransform(
                {
                    "cad_observe": ToolTransformConfig(
                        description=PREVIEW_AWARE_OBSERVE_DESCRIPTION,
                    )
                }
            )
        )
        return mcp

    return build_with_preview_guidance
