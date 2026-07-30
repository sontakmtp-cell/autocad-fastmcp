from types import SimpleNamespace
import hashlib
import json
from pathlib import Path

import pytest
from fastmcp import Client

from autocad_gateway.app import GatewayConfig, build_mcp_server


class SceneFacade:
    async def build(self, owner, request, correlation_id):
        raise AssertionError("schema discovery must not call scene build")

    async def query(self, owner, request, correlation_id):
        raise AssertionError("schema discovery must not call scene query")


@pytest.mark.asyncio
async def test_phase10_adds_exactly_two_read_only_scene_tools():
    services = SimpleNamespace(
        is_phase3=False,
        is_phase4=False,
        is_phase6=False,
        is_phase7=False,
        is_phase8=False,
        is_phase9=False,
        owner_subject="owner-a",
        workflow_service=None,
        scene_service=SceneFacade(),
        phase10_public_scene_tools_enabled=True,
        phase10_scene_resources_enabled=True,
    )
    async with Client(build_mcp_server(services)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        scene_tools = {name for name in tools if "scene" in name}
        assert scene_tools == {"cad_build_scene", "cad_query_scene"}
        for name in scene_tools:
            annotations = tools[name].annotations
            assert annotations.readOnlyHint is True
            assert annotations.destructiveHint is False
            assert annotations.idempotentHint is True
        assert not any(
            token in name
            for name in tools
            for token in ("hole", "slot", "contour", "relation", "feature")
        )

        templates = {
            template.uriTemplate for template in await client.list_resource_templates()
        }
        assert {
            "cad://scenes/{scene_id}/summary",
            "cad://scenes/{scene_id}/nodes",
            "cad://scenes/{scene_id}/relations",
            "cad://scenes/{scene_id}/contours",
            "cad://scenes/{scene_id}/features",
            "cad://scenes/{scene_id}/issues",
            "cad://scenes/{scene_id}/evidence",
        }.issubset(templates)


@pytest.mark.asyncio
async def test_phase10_tool_and_resource_snapshots_are_stable():
    services = SimpleNamespace(
        is_phase3=False,
        is_phase4=False,
        is_phase6=False,
        is_phase7=False,
        is_phase8=False,
        is_phase9=False,
        owner_subject="owner-a",
        workflow_service=None,
        scene_service=SceneFacade(),
        phase10_public_scene_tools_enabled=True,
        phase10_scene_resources_enabled=True,
    )
    async with Client(build_mcp_server(services)) as client:
        tools = []
        for tool in await client.list_tools():
            if "scene" not in tool.name:
                continue
            schema_digest = lambda value: hashlib.sha256(
                json.dumps(
                    value, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            tools.append(
                {
                    "name": tool.name,
                    "annotations": tool.annotations.model_dump(exclude_none=True),
                    "input_schema_sha256": schema_digest(tool.inputSchema),
                    "output_schema_sha256": schema_digest(tool.outputSchema),
                }
            )
        resources = [
            value.model_dump(by_alias=True, exclude_none=True)
            for value in await client.list_resource_templates()
            if "scenes/" in value.uriTemplate
        ]
    snapshots = Path(__file__).parents[2] / "snapshots"
    assert tools == json.loads((snapshots / "phase10_tools.json").read_text())
    assert resources == json.loads(
        (snapshots / "phase10_resources.json").read_text()
    )


def test_phase10_flags_default_off_and_dependencies_fail_closed(tmp_path):
    config = GatewayConfig()
    assert config.phase10_scene_engine_enabled is False
    assert config.phase10_public_scene_tools_enabled is False
    assert config.phase10_scene_resources_enabled is False
    assert config.phase10_mechanical_features_enabled is False
    assert config.phase10_annotation_links_enabled is False
    assert config.phase10_workflow_scene_steps_enabled is False
    assert config.phase10_portal_scene_views_enabled is False

    with pytest.raises(ValueError, match="require the scene engine"):
        GatewayConfig(phase10_public_scene_tools_enabled=True).validate()
    with pytest.raises(ValueError, match="explicit Phase 9 lab profile"):
        GatewayConfig(phase10_scene_engine_enabled=True).validate()
    with pytest.raises(ValueError, match="32-byte cursor secret"):
        digest = "sha256:" + "a" * 64
        GatewayConfig(
            profile="phase9_workflow",
            db_path=str(tmp_path / "phase10-config.sqlite"),
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
            phase10_scene_engine_enabled=True,
            phase10_public_scene_tools_enabled=True,
            phase10_cursor_signing_secret="short",
        ).validate()
