"""Streamable HTTP checks using the MCP SDK client, not only HTTP status."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from asgi_lifespan import LifespanManager
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from fastmcp_phase0.app import create_app


async def _round_trip(app, *, stateless: bool, headers: dict[str, str] | None = None):
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=headers,
        ) as http_client:
            async with streamable_http_client(
                "http://testserver/mcp",
                http_client=http_client,
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    called = await session.call_tool("cad_list_devices", {})
                    return listed, called


@pytest.mark.asyncio
@pytest.mark.parametrize("stateless", [False, True])
async def test_stateful_and_stateless_streamable_http(services, stateless):
    app = create_app(
        services,
        auth=None,
        stateless_http=stateless,
        allowed_hosts=["testserver"],
        allowed_origins=["https://chatgpt.com"],
    )
    listed, called = await _round_trip(app, stateless=stateless)
    assert {tool.name for tool in listed.tools} == {
        "cad_list_devices",
        "cad_observe",
        "cad_get_job",
    }
    assert not called.isError
    assert called.structuredContent["contract_version"] == "cad.mcp/0.1"


@pytest.mark.asyncio
async def test_host_and_origin_guards_run_before_tool_execution(services):
    app = create_app(
        services,
        auth=None,
        stateless_http=True,
        allowed_hosts=["testserver"],
        allowed_origins=["https://chatgpt.com"],
    )
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://evil.test") as client:
            wrong_host = await client.get("/healthz")
            assert wrong_host.status_code == 200
            wrong_host_mcp = await client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            assert wrong_host_mcp.status_code == 403
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Origin": "https://evil.test"},
        ) as client:
            wrong_origin = await client.post(
                "/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Origin": "https://evil.test",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            assert wrong_origin.status_code == 403
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Origin": "https://chatgpt.com", "X-Forwarded-Host": "evil.test"},
        ) as client:
            no_origin_forwarding_trust = await client.get("/healthz")
            assert no_origin_forwarding_trust.status_code == 200

    assert services.calls == []


@pytest.mark.asyncio
async def test_two_concurrent_http_requests_keep_identity_and_correlation_separate(services):
    app = create_app(
        services,
        auth=None,
        stateless_http=True,
        allowed_hosts=["testserver"],
        allowed_origins=["https://chatgpt.com"],
    )

    async def call_once():
        return await _round_trip(app, stateless=True)

    first, second = await asyncio.gather(call_once(), call_once())
    assert first[1].structuredContent["correlation_id"] != second[1].structuredContent["correlation_id"]
    assert len(services.calls) == 2
