from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from asgi_lifespan import LifespanManager
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from autocad_gateway.app import GatewayConfig, OuterHostOriginGuard, create_app


INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "gateway-test", "version": "1"},
    },
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@asynccontextmanager
async def _live_server(app):
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    server_task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)


async def _round_trip(base_url):
    async with httpx.AsyncClient(
        base_url=base_url,
        headers={"Host": "testserver"},
        trust_env=False,
    ) as http_client:
        async with streamable_http_client(
            f"{base_url}/mcp", http_client=http_client
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
    async with _live_server(app) as base_url:
        listed, called = await _round_trip(base_url)
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
                json=INITIALIZE_REQUEST,
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
                json=INITIALIZE_REQUEST,
            )
            assert response.status_code == 403


@pytest.mark.asyncio
async def test_origin_is_fail_closed_when_allowlist_is_empty_and_exact_when_configured(services):
    denied = create_app(
        services,
        config=GatewayConfig(stateless_http=True, allowed_hosts=("testserver",)),
    )
    async with LifespanManager(denied):
        transport = httpx.ASGITransport(app=denied)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Origin": "https://hostile.test"},
        ) as client:
            response = await client.post("/mcp", json={})
            assert response.status_code == 403

    allowed = create_app(
        services,
        config=GatewayConfig(
            stateless_http=True,
            allowed_hosts=("testserver",),
            allowed_origins=("https://chatgpt.com",),
        ),
    )
    async with LifespanManager(allowed):
        transport = httpx.ASGITransport(app=allowed)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Origin": "https://chatgpt.com"},
        ) as client:
            response = await client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json=INITIALIZE_REQUEST,
            )
            assert response.status_code != 403
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Origin": "https://chatgpt.com.evil.test"},
        ) as client:
            response = await client.post("/mcp", json={})
            assert response.status_code == 403


@pytest.mark.parametrize(
    ("host", "allowed", "matches"),
    [
        ("127.0.0.1:8765", "127.0.0.1:*", True),
        ("localhost:8765", "localhost:*", True),
        ("[::1]:8765", "[::1]:*", True),
        ("localhost.evil.test:8765", "localhost:*", False),
        ("127.0.0.1.evil.test:8765", "127.0.0.1:*", False),
        ("127.0.0.1:80.evil", "127.0.0.1:*", False),
        ("[::1].evil:8765", "[::1]:*", False),
    ],
)
def test_host_matching_parses_exact_authorities(host, allowed, matches):
    assert OuterHostOriginGuard._host_matches(host, allowed) is matches


@pytest.mark.asyncio
async def test_concurrent_requests_get_distinct_correlations(services):
    app = create_app(
        services,
        config=GatewayConfig(stateless_http=True, allowed_hosts=("testserver",)),
    )

    async with _live_server(app) as base_url:

        async def once():
            _, result = await _round_trip(base_url)
            return result.structuredContent["correlation_id"]

        first, second = await asyncio.gather(once(), once())
    assert first != second


def test_no_auth_gateway_rejects_non_loopback():
    with pytest.raises(ValueError):
        create_app(
            object(),
            config=GatewayConfig(host="0.0.0.0"),
        )


@pytest.mark.asyncio
async def test_agent_websocket_host_and_origin_fail_closed():
    called = False
    sent = []

    async def inner(scope, receive, send):
        nonlocal called
        called = True

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    guard = OuterHostOriginGuard(
        inner,
        ["cad.example"],
        ["https://agent.example"],
    )
    await guard(
        {
            "type": "websocket",
            "path": "/agent/ws",
            "headers": [(b"host", b"evil.example")],
        },
        receive,
        send,
    )
    assert called is False
    assert sent == [{
        "type": "websocket.close",
        "code": 4403,
        "reason": "host or origin is not allowed",
    }]

    await guard(
        {
            "type": "websocket",
            "path": "/agent/ws",
            "headers": [
                (b"host", b"cad.example"),
                (b"origin", b"https://agent.example"),
            ],
        },
        receive,
        send,
    )
    assert called is True
