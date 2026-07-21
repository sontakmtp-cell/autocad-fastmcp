"""Shared fixtures for the FastMCP facade spike."""

from __future__ import annotations

import pytest

from fastmcp_phase0.app import build_mcp_server
from fastmcp_phase0.services import Phase0Services


@pytest.fixture
async def services() -> Phase0Services:
    service = Phase0Services()
    await service.initialize()
    return service


@pytest.fixture
def local_server(services: Phase0Services):
    return build_mcp_server(services, auth=None, stateless_http=True)
