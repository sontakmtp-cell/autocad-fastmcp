from types import SimpleNamespace

import httpx
import pytest
from fastmcp import Client

from autocad_gateway.app import GatewayConfig, build_mcp_server, create_app
from autocad_gateway.composition import build_human_auth


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
        tools = {tool.name: tool for tool in await client.list_tools()}
        names = set(tools)
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
        assert tools["cad_control_workflow"].inputSchema["properties"]["action"][
            "enum"
        ] == ["submit_input", "resume", "cancel"]


@pytest.mark.asyncio
async def test_phase9_write_profile_advertises_write_scope_in_real_metadata(
    tmp_path,
):
    digest = "sha256:" + "a" * 64
    config = GatewayConfig(
        profile="phase9_workflow",
        db_path=str(tmp_path / "phase9-auth.sqlite"),
        oauth_issuer="https://issuer.test/",
        oauth_audience="https://cad.test",
        oauth_jwks_uri="https://issuer.test/.well-known/jwks.json",
        public_origin="https://cad.test",
        phase7_c2_enabled=True,
        trusted_approval_enabled=True,
        managed_write_enabled=True,
        program_v0_enabled=True,
        phase6_allowed_device_ids=("device-a",),
        program_v1_source_enabled=True,
        program_v1_compiler_enabled=True,
        phase8_rollout_policy_epoch=1,
        phase8_compiler_package_hash=digest,
        phase8_package_id="autocad.managed_host.r25",
        phase8_package_version="0.8.0",
        phase8_package_hash=digest,
        phase8_capability_manifest_hash=digest,
        phase8_operation_registry_hash=digest,
        phase9_workflow_engine_enabled=True,
        phase9_write_workflows_enabled=True,
    ).validate()
    services = SimpleNamespace(
        is_phase3=False,
        is_phase4=False,
        is_phase6=False,
        is_phase7=False,
        is_phase8=False,
        is_phase9=True,
        owner_subject="owner-a",
        workflow_service=SimpleNamespace(enabled=False, catalog_enabled=False),
    )
    with pytest.raises(ValueError, match="requires OAuth"):
        create_app(services, config=config)
    app = build_mcp_server(
        services, build_human_auth(config)
    ).http_app(path="/mcp", stateless_http=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        metadata = await client.get(
            "/.well-known/oauth-protected-resource/mcp"
        )
    assert metadata.status_code == 200
    assert metadata.json()["scopes_supported"] == [
        "autocad.read",
        "autocad.device.manage",
        "autocad.write",
    ]


def test_phase9_flags_are_default_off():
    config = GatewayConfig()
    assert config.phase9_skill_catalog_enabled is False
    assert config.phase9_workflow_engine_enabled is False
    assert config.phase9_public_workflow_tools_enabled is False
    assert config.phase9_auto_dimension_skill_enabled is False
    assert config.phase9_cleanup_audit_skill_enabled is False
    assert config.phase9_plate_pattern_skill_enabled is False
    assert config.phase9_write_workflows_enabled is False
