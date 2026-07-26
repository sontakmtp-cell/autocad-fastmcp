"""Keep Phase 5 runtime work additive behind the existing public MCP facade."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import Client


SNAPSHOTS = Path(__file__).parents[1] / "snapshots"


def _dump(value):
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


async def test_phase5_keeps_public_tool_names_and_safety_annotations(local_server):
    expected = json.loads((SNAPSHOTS / "tools.json").read_text(encoding="utf-8"))

    async with Client(local_server) as client:
        tools = [_dump(item) for item in await client.list_tools()]

    assert [item["name"] for item in tools] == [item["name"] for item in expected]
    assert [item["name"] for item in tools] == [
        "cad_list_devices",
        "cad_observe",
        "cad_query",
    ]
    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
        assert tool["annotations"]["openWorldHint"] is False


async def test_phase5_keeps_public_resource_and_prompt_snapshots(local_server):
    expected_resources = json.loads(
        (SNAPSHOTS / "resources.json").read_text(encoding="utf-8")
    )
    expected_prompts = json.loads(
        (SNAPSHOTS / "prompts.json").read_text(encoding="utf-8")
    )

    async with Client(local_server) as client:
        resources = [_dump(item) for item in await client.list_resource_templates()]
        prompts = [_dump(item) for item in await client.list_prompts()]

    assert resources == expected_resources
    assert prompts == expected_prompts


async def test_phase5_does_not_publish_runtime_or_arbitrary_code_controls(local_server):
    async with Client(local_server) as client:
        tools = await client.list_tools()
        resources = await client.list_resource_templates()

    exposed = {
        *(item.name.lower() for item in tools),
        *(str(item.uriTemplate).lower() for item in resources),
    }
    forbidden_fragments = {
        "execute_lisp",
        "arbitrary",
        "assembly",
        "runtime_selector",
        "shell",
        "upload",
    }
    assert not any(
        fragment in value for value in exposed for fragment in forbidden_fragments
    )
