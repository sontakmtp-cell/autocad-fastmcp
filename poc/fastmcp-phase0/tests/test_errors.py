"""Safe error mapping and strict validation checks."""

from __future__ import annotations

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_not_found_is_error_without_internal_details(local_server):
    async with Client(local_server) as client:
        result = await client.call_tool(
            "cad_observe",
            {"device_id": "missing-device"},
            raise_on_error=False,
        )
    assert result.is_error
    message = result.content[0].text
    assert "not_found" in message
    assert "device does not exist" not in message


@pytest.mark.asyncio
async def test_backend_and_unexpected_errors_are_masked(local_server, services):
    services.force_backend_error = True
    async with Client(local_server) as client:
        backend_error = await client.call_tool(
            "cad_list_devices",
            {},
            raise_on_error=False,
        )
    assert backend_error.is_error
    assert "backend_error" in backend_error.content[0].text
    assert "internal path" not in backend_error.content[0].text

    services.force_backend_error = False
    services.raise_unexpected = True
    async with Client(local_server) as client:
        unexpected_error = await client.call_tool(
            "cad_list_devices",
            {},
            raise_on_error=False,
        )
    assert unexpected_error.is_error
    assert "internal_error" in unexpected_error.content[0].text
    assert "implementation path" not in unexpected_error.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("cad_observe", {"device_id": ""}),
        ("cad_observe", {"device_id": "x" * 129}),
        ("cad_observe", {"device_id": "cad-online-01", "undeclared": True}),
        (
            "cad_observe",
            {"device_id": "cad-online-01", "include_preview_image": "false"},
        ),
        (
            "cad_observe",
            {"device_id": "cad-online-01", "include_preview_image": 1},
        ),
        (
            "cad_observe",
            {"device_id": "cad-online-01", "observation_level": "everything"},
        ),
        ("cad_list_devices", {"online_only": "false"}),
        ("cad_list_devices", {"online_only": 1}),
        ("cad_get_job", {"job_id": ""}),
        ("cad_get_job", {"job_id": "j" * 129}),
        ("cad_get_job", {"job_id": "job-running-01", "event_cursor": "c" * 129}),
        ("cad_get_job", {"job_id": "job-running-01", "undeclared": 1}),
    ],
)
async def test_invalid_input_does_not_call_service(local_server, services, tool_name, arguments):
    async with Client(local_server) as client:
        result = await client.call_tool(
            tool_name,
            arguments,
            raise_on_error=False,
        )
    assert result.is_error
    assert services.calls == []
