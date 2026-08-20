"""LLM-facing guidance for the optional CAD viewport preview."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp.server.transforms import ToolTransform
from fastmcp.tools.tool_transform import ToolTransformConfig


PREVIEW_AWARE_OBSERVE_DESCRIPTION = (
    "Create a bounded read-only CAD snapshot with stable revision and resource "
    "references. Set include_preview_image=true whenever the user asks to see, "
    "visually inspect, verify the appearance/layout, or otherwise needs an image "
    "of the current AutoCAD drawing. A successful preview is returned as an "
    "image/png artifact resource. Leave include_preview_image=false when only "
    "structured drawing/entity data is needed."
)


def wrap_build_mcp_server(build_server: Callable[..., Any]) -> Callable[..., Any]:
    """Decorate the existing server factory without changing tool behavior."""

    def build_with_preview_guidance(*args: Any, **kwargs: Any) -> Any:
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
