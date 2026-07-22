"""JWT, protected-resource metadata, authorization, and identity isolation checks."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager

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
from fastmcp_phase0.services import Phase0Services


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


def make_token(
    private_pem: bytes,
    *,
    subject: str | None = "user-01",
    scope: str = "autocad.read",
    **overrides,
) -> str:
    now = int(time.time())
    claims = {
        "client_id": "chatgpt-test-client",
        "azp": "chatgpt-test-client",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 600,
        "scope": scope,
    }
    if subject is not None:
        claims["sub"] = subject
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


@asynccontextmanager
async def mcp_session(app, token: str):
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
                yield session


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


async def _direct_access_error(call) -> str:
    try:
        result = await call
    except Exception as exc:  # MCP SDK surfaces resource not-found as an exception.
        return str(exc)
    assert getattr(result, "isError", False)
    return " ".join(getattr(item, "text", "") for item in result.content)


@pytest.mark.asyncio
async def test_valid_jwt_exposes_sub_to_tool_and_resource_service(services, key_pair):
    private_pem, public_pem = key_pair
    app = auth_app(services, public_pem)
    token = make_token(private_pem, subject="user-valid")

    async with LifespanManager(app):
        async with mcp_session(app, token) as session:
            listed = await session.list_tools()
            called = await session.call_tool("cad_list_devices", {})
            observed = await session.call_tool(
                "cad_observe",
                {"device_id": "cad-online-01"},
            )
            summary = await session.read_resource(observed.structuredContent["summary_uri"])

        assert {tool.name for tool in listed.tools} == {
            "cad_list_devices",
            "cad_observe",
            "cad_get_job",
        }
        assert not called.isError
        assert json.loads(summary.contents[0].text)["entity_summary"] == {
            "CIRCLE": 1,
            "LINE": 1,
        }
        assert {call["subject"] for call in services.calls} == {"user-valid"}
        assert {call["scopes"] for call in services.calls} == {"autocad.read"}
        assert token not in repr(listed)
        assert token not in repr(called)
        assert token not in repr(services.calls)

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
async def test_valid_token_without_scope_initializes_but_components_are_authorized(services, key_pair):
    private_pem, public_pem = key_pair
    app = auth_app(services, public_pem)
    token = make_token(private_pem, subject="user-no-scope", scope="")

    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await initialize_response(client, token)
            assert response.status_code == 200

        async with mcp_session(app, token) as session:
            tools = await session.list_tools()
            resources = await session.list_resource_templates()
            tool_error = await _direct_access_error(
                session.call_tool("cad_list_devices", {})
            )
            resource_error = await _direct_access_error(
                session.read_resource("cad://snapshots/not-authorized/summary")
            )

    assert tools.tools == []
    assert resources.resourceTemplates == []
    assert "not found" in tool_error.lower() or "unknown" in tool_error.lower()
    assert "not found" in resource_error.lower() or "unknown" in resource_error.lower()
    assert token not in tool_error
    assert token not in resource_error
    assert services.calls == []


@pytest.mark.asyncio
async def test_missing_token_invalid_jwt_and_missing_sub_are_401(services, key_pair, caplog):
    private_pem, public_pem = key_pair
    app = auth_app(services, public_pem)
    other_private, _ = key_pair_for_invalid_signature()
    tokens = [
        None,
        make_token(other_private, subject="bad-signature"),
        make_token(private_pem, subject="bad-issuer", iss="https://other-issuer.example.test"),
        make_token(private_pem, subject="bad-audience", aud="https://other-resource.example.test"),
        make_token(private_pem, subject="expired", exp=int(time.time()) - 10),
        make_token(private_pem, subject=None),
    ]
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            for token in tokens:
                caplog.clear()
                response = await initialize_response(client, token)
                assert response.status_code == 401
                if token:
                    assert token not in response.text
                    assert token not in caplog.text
    assert services.calls == []


class OverlapPhase0Services(Phase0Services):
    """Barrier-backed service proving that two authenticated requests overlap."""

    def __init__(self) -> None:
        super().__init__()
        self.tool_entries: list[dict[str, object]] = []
        self.resource_entries: list[dict[str, object]] = []
        self._tool_lock = asyncio.Lock()
        self._resource_lock = asyncio.Lock()
        self._tool_release = asyncio.Event()
        self._resource_release = asyncio.Event()

    async def _barrier(self, entries, lock, release, principal, correlation_id):
        async with lock:
            should_wait = len(entries) < 2
            if should_wait:
                entries.append(
                    {
                        "subject": principal.subject,
                        "scopes": tuple(principal.scopes),
                        "correlation_id": correlation_id,
                    }
                )
                if len(entries) == 2:
                    release.set()
        if should_wait:
            await release.wait()

    async def list_devices(self, request, principal, correlation_id):
        await self._barrier(
            self.tool_entries,
            self._tool_lock,
            self._tool_release,
            principal,
            correlation_id,
        )
        return await super().list_devices(request, principal, correlation_id)

    async def read_snapshot(self, snapshot_id, principal, correlation_id):
        await self._barrier(
            self.resource_entries,
            self._resource_lock,
            self._resource_release,
            principal,
            correlation_id,
        )
        return await super().read_snapshot(snapshot_id, principal, correlation_id)


@pytest.mark.asyncio
async def test_two_concurrent_jwt_users_keep_tool_and_resource_context_isolated(key_pair):
    private_pem, public_pem = key_pair
    services = OverlapPhase0Services()
    await services.initialize()
    app = auth_app(services, public_pem)
    tokens = {
        "user-A": make_token(private_pem, subject="user-A", scope="autocad.read"),
        "user-B": make_token(private_pem, subject="user-B", scope="autocad.read"),
    }

    async def user_flow(subject: str):
        async with mcp_session(app, tokens[subject]) as session:
            devices = await session.call_tool("cad_list_devices", {})
            observed = await session.call_tool(
                "cad_observe",
                {"device_id": "cad-online-01"},
            )
            summary = await session.read_resource(observed.structuredContent["summary_uri"])
            return {
                "subject": subject,
                "devices_correlation": devices.structuredContent["correlation_id"],
                "snapshot_uri": observed.structuredContent["summary_uri"],
                "summary": json.loads(summary.contents[0].text),
            }

    async with LifespanManager(app):
        first, second = await asyncio.gather(user_flow("user-A"), user_flow("user-B"))

        async with mcp_session(app, tokens["user-A"]) as session:
            cross_user_error = await _direct_access_error(
                session.read_resource(second["snapshot_uri"])
            )

    assert {first["subject"], second["subject"]} == {"user-A", "user-B"}
    assert first["devices_correlation"] != second["devices_correlation"]
    assert first["summary"]["snapshot_id"] != second["summary"]["snapshot_id"]
    assert "not found" in cross_user_error.lower()

    assert {entry["subject"] for entry in services.tool_entries} == {"user-A", "user-B"}
    assert {entry["subject"] for entry in services.resource_entries} == {"user-A", "user-B"}
    assert len({entry["correlation_id"] for entry in services.tool_entries}) == 2
    assert len({entry["correlation_id"] for entry in services.resource_entries}) == 2
    assert {entry["scopes"] for entry in services.tool_entries} == {("autocad.read",)}
    assert {entry["scopes"] for entry in services.resource_entries} == {("autocad.read",)}

    calls_by_subject = {
        subject: [call for call in services.calls if call["subject"] == subject]
        for subject in ("user-A", "user-B")
    }
    assert all(calls_by_subject.values())
    assert all("user-B" not in repr(call) for call in calls_by_subject["user-A"])
    assert all("user-A" not in repr(call) for call in calls_by_subject["user-B"])
    assert all(call["scopes"] == "autocad.read" for call in services.calls)
    assert len({call["correlation_id"] for call in services.calls}) == len(services.calls)


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
