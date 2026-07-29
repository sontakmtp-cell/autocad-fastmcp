from types import SimpleNamespace

import pytest
from fastmcp import Client

from autocad_gateway.app import GatewayConfig, build_mcp_server


class _WorkflowFacade:
    enabled = True
    catalog_enabled = True

    async def list_skills(self, **kwargs):
        return {"skills": [], "next_cursor": None}


@pytest.mark.asyncio
async def test_phase9_registers_four_workflow_tools_not_skill_tools():
    services = SimpleNamespace(
        is_phase3=False,
        is_phase4=False,
        is_phase6=False,
        is_phase7=False,
        is_phase8=False,
        is_phase9=True,
        owner_subject="owner-a",
        workflow_service=_WorkflowFacade(),
    )
    async with Client(build_mcp_server(services)) as client:
        names = {tool.name for tool in await client.list_tools()}
        workflow_names = {name for name in names if "workflow" in name or "skills" in name}
        assert workflow_names == {
            "cad_list_skills",
            "cad_start_workflow",
            "cad_get_workflow",
            "cad_control_workflow",
        }
        assert not any(
            skill in name
            for name in names
            for skill in ("auto_dimension", "cleanup_audit", "plate_pattern")
        )


def test_phase9_flags_are_default_off():
    config = GatewayConfig()
    assert config.phase9_skill_catalog_enabled is False
    assert config.phase9_workflow_engine_enabled is False
    assert config.phase9_public_workflow_tools_enabled is False
    assert config.phase9_auto_dimension_skill_enabled is False
    assert config.phase9_cleanup_audit_skill_enabled is False
    assert config.phase9_plate_pattern_skill_enabled is False
    assert config.phase9_write_workflows_enabled is False
