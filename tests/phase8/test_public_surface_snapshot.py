from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
for source in (
    ROOT / "services" / "gateway" / "src",
    ROOT / "packages" / "contracts" / "src",
    ROOT / "packages" / "cad_core" / "src",
    ROOT / "src",
):
    sys.path.insert(0, str(source))

from autocad_gateway.app import build_mcp_server  # noqa: E402
from fastmcp import Client  # noqa: E402


SNAPSHOT = Path(__file__).with_name("public-surface-phase7.json")
PHASE8_DELTA = Path(__file__).with_name("public-surface-phase8-delta.json")


class _Phase7Admission:
    async def read_intent(self, *_):
        return None

    async def read_consent(self, *_):
        return None

    async def read_evidence(self, *_):
        return None

    async def read_recovery(self, *_):
        return None

    async def read_checkpoint(self, *_):
        return None

    async def read_rollback(self, *_):
        return None

    async def read_rollback_receipt(self, *_):
        return None


class _Phase7RegistrationOnlyServices:
    profile = "phase7_c2"
    is_phase3 = True
    is_phase4 = True
    is_phase6 = True
    is_phase7 = True
    is_phase8 = False
    local_subject = "phase8-conformance"
    program_service = object()
    phase7_admission = _Phase7Admission()


class _Phase8RegistrationOnlyServices(_Phase7RegistrationOnlyServices):
    profile = "phase8_program"
    is_phase8 = True


def _dump(value):
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def _digest(value) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _tool_snapshot(tool) -> dict:
    value = _dump(tool)
    return {
        "name": value["name"],
        "title": value["title"],
        "annotations": value["annotations"],
        "input_schema_sha256": _digest(value["inputSchema"]),
        "output_schema_sha256": _digest(value["outputSchema"]),
    }


def _schema_property_names(value) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            names.update(name.lower() for name in properties)
        for child in value.values():
            names.update(_schema_property_names(child))
    elif isinstance(value, list):
        for child in value:
            names.update(_schema_property_names(child))
    return names


@pytest.mark.asyncio
async def test_phase7_public_tool_and_resource_snapshot_is_unchanged():
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    server = build_mcp_server(_Phase7RegistrationOnlyServices())

    async with Client(server) as client:
        tools = await client.list_tools()
        resources = await client.list_resource_templates()

    actual_tools = [_tool_snapshot(tool) for tool in tools]
    actual_resources = [
        [item.name, str(item.uriTemplate), item.mimeType] for item in resources
    ]
    assert actual_tools == expected["tools"]
    assert actual_resources == expected["resources"]


@pytest.mark.asyncio
async def test_phase8_profile_has_only_the_documented_prepare_schema_delta():
    baseline = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    delta = json.loads(PHASE8_DELTA.read_text(encoding="utf-8"))
    server = build_mcp_server(_Phase8RegistrationOnlyServices())

    async with Client(server) as client:
        tools = await client.list_tools()
        resources = await client.list_resource_templates()

    actual_tools = {_tool_snapshot(tool)["name"]: _tool_snapshot(tool) for tool in tools}
    baseline_tools = {item["name"]: item for item in baseline["tools"]}
    assert actual_tools.keys() == baseline_tools.keys()
    assert len(actual_tools) == delta["tool_count"]
    assert delta["new_public_tool_names"] == []
    for name in actual_tools.keys() - delta["changed_tools"].keys():
        assert actual_tools[name] == baseline_tools[name]
    for name, change in delta["changed_tools"].items():
        actual = actual_tools[name]
        expected = baseline_tools[name]
        assert actual["title"] == expected["title"]
        assert actual["annotations"] == expected["annotations"]
        assert actual["input_schema_sha256"] == change["input_schema_sha256"]
        assert actual["output_schema_sha256"] == change["output_schema_sha256"]
    actual_resources = [
        [item.name, str(item.uriTemplate), item.mimeType] for item in resources
    ]
    assert len(actual_resources) == delta["resource_template_count"]
    assert actual_resources == baseline["resources"]


@pytest.mark.asyncio
async def test_s8_010_phase7_phase8_surface_and_sensitive_authority_denylist():
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    server = build_mcp_server(_Phase8RegistrationOnlyServices())

    async with Client(server) as client:
        tools = await client.list_tools()
        resources = await client.list_resource_templates()

    names = {tool.name for tool in tools}
    forbidden = {
        "cad_move",
        "cad_rotate",
        "cad_scale",
        "cad_delete",
        "cad_trim",
        "cad_fillet",
        "cad_chamfer",
        "cad_extend",
        "cad_join",
        "cad_explode",
        "cad_execute",
        "cad_approve",
    }
    assert names.isdisjoint(forbidden)
    assert not any("approv" in name.lower() for name in names)

    sensitive_fields = {
        "owner",
        "owner_subject",
        "trusted_owner",
        "risk",
        "risk_class",
        "runtime",
        "runtime_id",
        "runtime_role",
        "handle",
        "handles",
        "entity_handle",
        "entity_handles",
        "objectid",
        "object_id",
        "restore_payload",
        "restore_descriptor",
        "capability_elevation",
        "approval_proof",
        "command",
        "command_name",
        "path",
        "file_path",
        "url",
    }
    exposed_fields = set().union(
        *(_schema_property_names(tool.inputSchema) for tool in tools)
    )
    assert exposed_fields.isdisjoint(sensitive_fields)

    freeze = expected["phase8_contract_freeze"]
    assert expected["security_control"] == "S8-010"
    assert freeze["baseline"] == "phase7_c2"
    assert freeze["tool_names_must_remain_exact"] is True
    assert freeze["resource_templates_must_remain_exact"] is True
    assert names == {item["name"] for item in expected["tools"]}
    assert {str(item.uriTemplate) for item in resources} == {
        item[1] for item in expected["resources"]
    }
