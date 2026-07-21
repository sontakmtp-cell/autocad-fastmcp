from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
from fastmcp import Client


SNAPSHOTS = Path(__file__).parents[1] / "snapshots"


def _dump(value):
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def _schema_digest(value):
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _tool_snapshot(tool):
    value = _dump(tool)
    return {
        "name": value["name"],
        "title": value["title"],
        "description": value["description"],
        "annotations": value["annotations"],
        "input_schema_sha256": _schema_digest(value["inputSchema"]),
        "output_schema_sha256": _schema_digest(value["outputSchema"]),
    }


@pytest.mark.asyncio
async def test_public_contract_has_exactly_three_tools(local_server):
    async with Client(local_server) as client:
        actual = [_tool_snapshot(tool) for tool in await client.list_tools()]
    expected = json.loads((SNAPSHOTS / "tools.json").read_text(encoding="utf-8"))
    assert actual == expected
    assert [item["name"] for item in actual] == [
        "cad_list_devices",
        "cad_observe",
        "cad_query",
    ]


@pytest.mark.asyncio
async def test_public_contract_has_four_resources_and_two_prompts(local_server):
    async with Client(local_server) as client:
        resources = [_dump(item) for item in await client.list_resource_templates()]
        prompts = [_dump(item) for item in await client.list_prompts()]
    assert resources == json.loads((SNAPSHOTS / "resources.json").read_text(encoding="utf-8"))
    assert prompts == json.loads((SNAPSHOTS / "prompts.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_observe_query_and_resource_read_flow(local_server):
    async with Client(local_server) as client:
        devices = await client.call_tool("cad_list_devices", {})
        assert devices.structured_content["default_device_id"] == "local-default"
        observation = await client.call_tool(
            "cad_observe",
            {
                "device_id": "local-default",
                "observation_level": "detail",
                "include_preview_image": True,
            },
        )
        assert not observation.is_error
        snapshot_id = observation.structured_content["snapshot_id"]
        query = await client.call_tool(
            "cad_query", {"snapshot_id": snapshot_id, "types": ["LINE"]}
        )
        assert query.structured_content["total"] == 1
        summary = await client.read_resource(observation.structured_content["summary_uri"])
        assert json.loads(summary[0].text)["snapshot_id"] == snapshot_id
        entities = await client.read_resource(observation.structured_content["entities_uri"])
        assert json.loads(entities[0].text)["total"] == 2
        artifact_uri = observation.structured_content["artifact_refs"][0]["uri"]
        artifact = await client.read_resource(artifact_uri)
        assert artifact[0].blob


@pytest.mark.asyncio
async def test_correlation_id_is_unique_per_in_memory_tool_call(local_server):
    async with Client(local_server) as client:
        first = await client.call_tool("cad_list_devices", {})
        second = await client.call_tool("cad_list_devices", {})
    assert first.structured_content["correlation_id"] != second.structured_content["correlation_id"]


@pytest.mark.asyncio
async def test_public_surface_does_not_expose_legacy_or_write_names(local_server):
    async with Client(local_server) as client:
        tools = await client.list_tools()
        resources = await client.list_resource_templates()
    tool_names = {item.name for item in tools}
    resource_uris = {item.uriTemplate for item in resources}
    assert not tool_names & {"drawing", "entity", "execute_lisp", "cad_get_job", "cad_prepare_program"}
    assert all("write" not in name and "lisp" not in name for name in tool_names)
    assert "cad://artifacts/{artifact_id}" in resource_uris
