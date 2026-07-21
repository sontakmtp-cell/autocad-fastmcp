"""FastMCP 3.4.4 Gateway facade compatibility spike."""

from .app import build_mcp_server, create_app
from .contracts import (
    CadGetJobInput,
    CadListDevicesInput,
    CadObserveInput,
)
from .services import Phase0Services, Principal

__all__ = [
    "CadGetJobInput",
    "CadListDevicesInput",
    "CadObserveInput",
    "Phase0Services",
    "Principal",
    "build_mcp_server",
    "create_app",
]
