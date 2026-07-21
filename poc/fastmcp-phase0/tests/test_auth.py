"""JWT, protected-resource metadata, and authorization boundary checks."""

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

from fastmcp_phase0.app import create_app
from fastmcp_phase0.auth import build_remote_auth


ISSUER = "https://issuer.example.test"
AUDIENCE = "https://cad.example.test/mcp"
RESOURCE_URL = AUDIENCE
ALLOWED_HOSTS = ["testserver"]
ALLOWED_ORIGINS = ["https://chatgpt.com"]


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


def make_token(private_pem: bytes, *, subject: str = "user-01", scope: str = "autocad.read", **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": subject,
        "client_id": "chatgpt-test-client",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 600,
        "scope": scope,
    }
    claims.update(overrides)
    return jwt.encode(claims, private_pem, algorithm="RS256")


def auth_app(services, public_pem: bytes):
    auth = build_remote_auth(
        public_key=public_pem,
        issuer=ISSUER,
        audience=AUDIENCE,
        resource_url=RESOURCE_URL,
    )
    return create_app(
        services,
        auth=auth,
        stateless_http=True,
        allowed_hosts=ALLOWED_HOSTS,
        allowed_origins=ALLOWED_ORIGINS,
    )


async def authorized_round_trip(app, token: str):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": "https://chatgpt.com",
        },
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


async def initialize_response(client: httpx.AsyncClient, token: str | None):
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "phase0-test", "version": "0.1"},
            },
        },
    )


@pytest.mark.asyncio
async def test_valid_jwt_exposes_sub_to_the_service_and_metadata(services, key_pair):
    private_pem, public_pem = key_pair
    app = auth_app(services, public_pem)
    token = make_token(private_pem, subject="user-valid")

    async with LifespanManager(app):
        listed, called = await authorized_round_trip(app, token)
        assert {tool.name for tool in listed.tools} == {
            "cad_list_devices",
            "cad_observe",
            "cad_get_job",
        }
        assert not called.isError
        assert services.calls[-1]["subject"] == "user-valid"
        assert services.calls[-1]["scopes"] == "autocad.read"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Origin": "https://chatgpt.com"},
        ) as client:
            metadata = await client.get("/.well-known/oauth-protected-resource/mcp")
            assert metadata.status_code == 200
            body = metadata.json()
            assert body["resource"] == RESOURCE_URL
            assert ISSUER in {server.rstrip("/") for server in body["authorization_servers"]}
            assert "autocad.read" in body["scopes_supported"]


@pytest.mark.asyncio
async def test_missing_scope_is_rejected_before_the_service(services, key_pair):
    private_pem, public_pem = key_pair
    app = auth_app(services, public_pem)
    token = make_token(private_pem, subject="user-no-scope", scope="")
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await initialize_response(client, token)
    assert response.status_code in {401, 403}
    assert services.calls == []


@pytest.mark.asyncio
async def test_missing_token_and_invalid_jwt_variants_are_401(services, key_pair):
    private_pem, public_pem = key_pair
    app = auth_app(services, public_pem)
    other_private, _ = key_pair_for_invalid_signature()
    tokens = [
        None,
        make_token(other_private, subject="bad-signature"),
        make_token(private_pem, subject="bad-issuer", iss="https://other-issuer.example.test"),
        make_token(private_pem, subject="bad-audience", aud="https://other-resource.example.test"),
        make_token(private_pem, subject="expired", exp=int(time.time()) - 10),
    ]
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            for token in tokens:
                response = await initialize_response(client, token)
                assert response.status_code == 401
                if token:
                    assert token not in response.text
    assert services.calls == []


def key_pair_for_invalid_signature():
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
