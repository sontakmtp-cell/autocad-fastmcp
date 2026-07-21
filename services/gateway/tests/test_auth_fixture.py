from __future__ import annotations

import time

import httpx
import jwt
import pytest
from asgi_lifespan import LifespanManager
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from autocad_gateway.app import GatewayConfig, create_app
from autocad_gateway.auth import build_fixture_auth


ISSUER = "https://issuer.example.test"
AUDIENCE = "https://cad.example.test/mcp"


@pytest.fixture(scope="module")
def key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def make_token(private_pem: bytes, *, scope: str = "autocad.read") -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "fixture-user",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + 600,
            "scope": scope,
        },
        private_pem,
        algorithm="RS256",
    )


@pytest.mark.asyncio
async def test_jwt_fixture_accepts_autocad_read_and_propagates_subject(services, key_pair):
    private_pem, public_pem = key_pair
    services.owner_subject = "fixture-user"
    auth = build_fixture_auth(
        public_key=public_pem,
        issuer=ISSUER,
        audience=AUDIENCE,
        resource_url=AUDIENCE,
    )
    app = create_app(
        services,
        auth=auth,
        config=GatewayConfig(
            stateless_http=True,
            allowed_hosts=("testserver",),
            allowed_origins=("https://chatgpt.com",),
        ),
    )
    token = make_token(private_pem)
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {token}"},
        ) as http_client:
            async with streamable_http_client(
                "http://testserver/mcp", http_client=http_client
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    called = await session.call_tool("cad_list_devices", {})
                    assert not called.isError
                    assert called.structuredContent["default_device_id"] == "local-default"


@pytest.mark.asyncio
async def test_jwt_fixture_missing_read_scope_is_rejected_before_tools(services, key_pair):
    private_pem, public_pem = key_pair
    auth = build_fixture_auth(
        public_key=public_pem,
        issuer=ISSUER,
        audience=AUDIENCE,
        resource_url=AUDIENCE,
    )
    app = create_app(
        services,
        auth=auth,
        config=GatewayConfig(stateless_http=True, allowed_hosts=("testserver",)),
    )
    token = make_token(private_pem, scope="")
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "gateway-test", "version": "1"},
                    },
                },
            )
            assert response.status_code in {401, 403}
