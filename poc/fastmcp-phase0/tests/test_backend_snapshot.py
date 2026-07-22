"""Proof that Phase 0 observations are derived from the real EzdxfBackend."""

from __future__ import annotations

import base64
import json

import pytest
from cad_core import CadInvocation, CommandResult
from fastmcp import Client

from autocad_mcp.backends.ezdxf_backend import EzdxfBackend
from fastmcp_phase0.app import build_mcp_server
from fastmcp_phase0.contracts import CadObserveInput
from fastmcp_phase0.services import Phase0Services, Principal


PRINCIPAL = Principal(subject="snapshot-test-user", scopes=("autocad.read",))


class FailCreateLineBackend(EzdxfBackend):
    async def create_line(self, *args, **kwargs) -> CommandResult:
        return CommandResult(ok=False, error="private LINE failure")


class FailCreateCircleBackend(EzdxfBackend):
    async def create_circle(self, *args, **kwargs) -> CommandResult:
        return CommandResult(ok=False, error="private CIRCLE failure")


class InvalidScreenshotBackend(EzdxfBackend):
    async def get_screenshot(self) -> CommandResult:
        return CommandResult(
            ok=True,
            payload=base64.b64encode(b"not-a-png").decode("ascii"),
        )


class FailDrawingInfoBackend(EzdxfBackend):
    async def drawing_info(self) -> CommandResult:
        return CommandResult(ok=False, error="private drawing path", error_code="backend_error")


class FailEntityQueryBackend(EzdxfBackend):
    async def entity_list(self, layer=None) -> CommandResult:
        return CommandResult(ok=False, error="private query path", error_code="backend_error")


async def _observe(service: Phase0Services, correlation_id: str = "corr-snapshot"):
    return await service.observe(
        CadObserveInput(device_id="cad-online-01"),
        PRINCIPAL,
        correlation_id,
    )


@pytest.mark.asyncio
async def test_fixture_contains_exactly_one_real_line_and_circle():
    service = Phase0Services()
    await service.initialize()

    observed = await _observe(service)
    assert observed.ok
    snapshot_id = observed.payload["snapshot_id"]
    stored = await service.read_snapshot(snapshot_id, PRINCIPAL, "resource-corr")
    summary = json.loads(stored.payload)

    assert summary["entity_count"] == 2
    assert summary["entity_summary"] == {"CIRCLE": 1, "LINE": 1}


@pytest.mark.asyncio
async def test_line_creation_failure_aborts_fixture_initialization():
    service = Phase0Services(backend=FailCreateLineBackend())
    with pytest.raises(RuntimeError, match="LINE creation"):
        await service.initialize()
    assert service.materialized_snapshot_count == 0


@pytest.mark.asyncio
async def test_circle_creation_failure_aborts_fixture_initialization():
    service = Phase0Services(backend=FailCreateCircleBackend())
    with pytest.raises(RuntimeError, match="CIRCLE creation"):
        await service.initialize()
    assert service.materialized_snapshot_count == 0


@pytest.mark.asyncio
async def test_invalid_screenshot_aborts_fixture_initialization():
    service = Phase0Services(backend=InvalidScreenshotBackend())
    with pytest.raises(RuntimeError, match="preview validation"):
        await service.initialize()
    assert service.materialized_snapshot_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", [FailDrawingInfoBackend(), FailEntityQueryBackend()])
async def test_backend_observation_failure_is_safe_and_does_not_materialize_snapshot(backend):
    service = Phase0Services(backend=backend)
    await service.initialize()
    server = build_mcp_server(service, auth=None, stateless_http=True)

    async with Client(server) as client:
        result = await client.call_tool(
            "cad_observe",
            {"device_id": "cad-online-01"},
            raise_on_error=False,
        )

    assert result.is_error
    assert "backend_error" in result.content[0].text
    assert "private" not in result.content[0].text
    assert service.materialized_snapshot_count == 0


@pytest.mark.asyncio
async def test_entity_summary_changes_when_real_backend_fixture_changes():
    service = Phase0Services()
    await service.initialize()
    extra_line = await service.application_service.execute(
        CadInvocation(
            group="entity",
            operation="create_line",
            arguments={"x1": 0, "y1": 10, "x2": 100, "y2": 10},
        )
    )
    assert extra_line.result.ok

    observed = await _observe(service, "corr-mutated")
    stored = await service.read_snapshot(
        observed.payload["snapshot_id"],
        PRINCIPAL,
        "resource-mutated",
    )
    summary = json.loads(stored.payload)

    assert summary["entity_count"] == 3
    assert summary["entity_summary"] == {"CIRCLE": 1, "LINE": 2}
