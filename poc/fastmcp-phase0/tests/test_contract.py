"""Contract and in-memory FastMCP client checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client


SNAPSHOTS = Path(__file__).parents[1] / "snapshots"


def _dump(value):
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


@pytest.mark.asyncio
async def test_tools_list_matches_the_three_tool_snapshot(local_server):
    async with Client(local_server) as client:
        result = await client.list_tools()
    actual = [_dump(tool) for tool in result]
    expected = json.loads((SNAPSHOTS / "tools.json").read_text(encoding="utf-8"))
    assert actual == expected
    assert [tool["name"] for tool in actual] == [
        "cad_list_devices",
        "cad_observe",
        "cad_get_job",
    ]


@pytest.mark.asyncio
async def test_resources_and_all_three_tools_work_in_memory(local_server, services):
    async with Client(local_server) as client:
        resources = await client.list_resource_templates()
        resource_snapshot = [_dump(resource) for resource in resources]
        expected_resources = json.loads(
            (SNAPSHOTS / "resources.json").read_text(encoding="utf-8")
        )
        assert resource_snapshot == expected_resources

        devices = await client.call_tool("cad_list_devices", {}, raise_on_error=False)
        assert not devices.is_error
        assert devices.structured_content["default_device_id"] == "cad-online-01"

        observation = await client.call_tool(
            "cad_observe",
            {
                "device_id": "cad-online-01",
                "observation_level": "summary",
                "include_preview_image": True,
            },
            raise_on_error=False,
        )
        assert not observation.is_error
        assert observation.structured_content["document_revision"] == "revision-001"
        assert {item.type for item in observation.content} >= {"text", "resource_link", "image"}

        summary_uri = observation.structured_content["summary_uri"]
        summary = await client.read_resource(summary_uri)
        assert summary[0].mimeType == "application/json"
        summary_data = json.loads(summary[0].text)
        assert summary_data["snapshot_id"] == "snapshot-cad-online-01"

        artifact_uri = observation.structured_content["artifact_refs"][0]["uri"]
        artifact = await client.read_resource(artifact_uri)
        assert artifact[0].mimeType == "image/png"
        assert artifact[0].blob

        job = await client.call_tool(
            "cad_get_job",
            {"job_id": "job-running-01"},
            raise_on_error=False,
        )
        assert not job.is_error
        assert job.structured_content["state"] == "running"

    assert [call["operation"] for call in services.calls] == [
        "cad_list_devices",
        "cad_observe",
        "cad_get_job",
    ]


@pytest.mark.asyncio
async def test_correlation_ids_are_created_at_the_mcp_boundary(local_server):
    async with Client(local_server) as client:
        first = await client.call_tool("cad_list_devices", {}, raise_on_error=False)
        second = await client.call_tool("cad_list_devices", {}, raise_on_error=False)
    assert first.structured_content["correlation_id"] != second.structured_content["correlation_id"]
