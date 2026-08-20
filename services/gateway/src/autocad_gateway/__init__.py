"""FastMCP public v1 local Gateway."""

from .app import GatewayConfig, build_mcp_server, create_app
from .contracts import Principal
from .durable_services import DurableGatewayServices
from .services import GatewayServices
from .observe_detail_compat import install_observe_detail_compat
from .preview_image_compat import install_preview_image_compat

install_observe_detail_compat()
install_preview_image_compat()

__all__ = [
    "GatewayConfig",
    "GatewayServices",
    "DurableGatewayServices",
    "Principal",
    "build_mcp_server",
    "create_app",
]
