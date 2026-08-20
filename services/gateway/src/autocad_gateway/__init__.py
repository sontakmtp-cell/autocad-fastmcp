"""FastMCP public v1 local Gateway."""

from . import app as _app
from .app import GatewayConfig, create_app
from .contracts import Principal
from .durable_services import DurableGatewayServices
from .services import GatewayServices
from .observe_detail_compat import install_observe_detail_compat
from .preview_image_compat import install_preview_image_compat
from .preview_tool_guidance import wrap_build_mcp_server

install_observe_detail_compat()
install_preview_image_compat()
build_mcp_server = wrap_build_mcp_server(_app.build_mcp_server)
_app.build_mcp_server = build_mcp_server

__all__ = [
    "GatewayConfig",
    "GatewayServices",
    "DurableGatewayServices",
    "Principal",
    "build_mcp_server",
    "create_app",
]
