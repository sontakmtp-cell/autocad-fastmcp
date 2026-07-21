from __future__ import annotations

import asyncio

import httpx
import pytest
from asgi_lifespan import LifespanManager
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from autocad_gateway.app import GatewayConfig, create_app


async def _round_trip(app):
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
            async with streamable_http_client(
                "http://testserver/mcp", http_client=http_client
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
        config=GatewayConfig(
            stateless_http=stateless,
            allowed_hosts=("testserver",),
            allowed_origins=("https://chatgpt.com",),
        ),
    )
    listed, called = await _round_trip(app)
    assert {item.name for item in listed.tools} == {
        "cad_list_devices",
        "cad_observe",
        "cad_query",
    }
    assert not called.isError
    assert called.structuredContent["contract_version"] == "cad.mcp/1.0"


@pytest.mark.asyncio
async def test_host_and_origin_guard_happens_before_tools(services):
    app = create_app(
        services,
        config=GatewayConfig(
            stateless_http=True,
            allowed_hosts=("testserver",),
            allowed_origins=("https://chatgpt.com",),
        ),
    )
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://evil.test") as client:
            response = await client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            assert response.status_code in {403, 421}
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Origin": "https://evil.test"},
        ) as client:
            response = await client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            assert response.status_code == 403


@pytest.mark.asyncio
async def test_concurrent_requests_get_distinct_correlations(services):
    app = create_app(
        services,
        config=GatewayConfig(stateless_http=True, allowed_hosts=("testserver",)),
    )

    async def once():
        _, result = await _round_trip(app)
        return result.structuredContent["correlation_id"]

    first, second = await asyncio.gather(once(), once())
    assert first != second


def test_no_auth_gateway_rejects_non_loopback():
    with pytest.raises(ValueError):
        create_app(
            object(),
            config=GatewayConfig(host="0.0.0.0"),
        )
