from __future__ import annotations

import pytest
import pytest_asyncio

from autocad_gateway.app import build_mcp_server
from autocad_gateway.services import GatewayServices
from autocad_mcp.backends.ezdxf_backend import EzdxfBackend


PHASE3_TEST_FILES = frozenset(
    {
        "test_agent_protocol.py",
        "test_dispatch_reconcile.py",
        "test_gateway_restart.py",
        "test_job_state_machine.py",
        "test_phase3_mcp_flow.py",
        "test_sqlite_repositories.py",
        "test_stale_and_deadline.py",
    }
)


def pytest_collection_modifyitems(items):
    phase3 = pytest.mark.phase3
    for item in items:
        if item.path.name in PHASE3_TEST_FILES:
            item.add_marker(phase3)


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
