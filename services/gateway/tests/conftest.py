from __future__ import annotations

import pytest_asyncio

from autocad_gateway.app import build_mcp_server
from autocad_gateway.services import GatewayServices
from autocad_mcp.backends.ezdxf_backend import EzdxfBackend


@pytest_asyncio.fixture
async def services():
    backend = EzdxfBackend()
    service = GatewayServices(backend)
    await service.initialize()
    await backend.create_line(0, 0, 100, 0, "0")
    await backend.create_circle(50, 25, 10, "A")
    return service


@pytest_asyncio.fixture
async def local_server(services):
    return build_mcp_server(services)
