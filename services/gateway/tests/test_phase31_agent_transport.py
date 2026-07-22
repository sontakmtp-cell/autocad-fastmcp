from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from autocad_contracts import (
    AckMessage,
    ErrorMessage,
    HeartbeatMessage,
    HelloMessage,
    MAX_WEBSOCKET_MESSAGE_BYTES,
    ReconcileCommandDescriptor,
    ReconcileResultMessage,
    canonical_capability_hash,
    canonical_json,
    message_dict,
    parse_agent_message,
)
from autocad_gateway.infrastructure.agent_transport.authenticator import FixtureDeviceAuthenticator
from autocad_gateway.infrastructure.agent_transport.connection_registry import (
    AgentConnection,
    ConnectionRegistry,
)
from autocad_gateway.infrastructure.agent_transport.websocket_endpoint import (
    serve_agent_websocket,
)


class ScriptedWebSocket:
    def __init__(self, *, token: str | None, hello: HelloMessage | str | None = None) -> None:
        headers = [] if token is None else [(b"authorization", f"Bearer {token}".encode())]
        self.scope = {"headers": headers}
        self.query_params = {"token": "query-token-must-not-work"}
        self.hello = (
            canonical_json(message_dict(hello)) if isinstance(hello, HelloMessage) else hello
        )
        self.accepted = False
        self.sent: list[dict] = []
        self.closed: list[tuple[int, str]] = []
        self.receive_count = 0
        self._ack_text: str | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, value: dict) -> None:
        self.sent.append(value)

    async def close(self, *, code: int, reason: str) -> None:
        self.closed.append((code, reason))

    async def receive_text(self) -> str:
        self.receive_count += 1
        if self.receive_count == 1:
            if self.hello is None:
                raise WebSocketDisconnect(1000)
            return self.hello
        session_id = self.sent[0]["session_id"]
        if self.receive_count == 2:
            ack = AckMessage(
                session_id=session_id,
                device_id="device-a",
                job_id="job-a",
                command_id="command-a",
                sequence=1,
                status="accepted",
                idempotency_key="request-a",
                payload_hash="a" * 64,
            )
            self._ack_text = canonical_json(message_dict(ack))
            return self._ack_text
        if self.receive_count == 3:
            assert self._ack_text is not None
            return self._ack_text
        if self.receive_count == 4:
            return canonical_json(
                message_dict(
                    HeartbeatMessage(
                        session_id=session_id,
                        device_id="device-a",
                        sequence=2,
                        last_processed_sequence=1,
                    )
                )
            )
        raise WebSocketDisconnect(1000)


class ClosingSocket:
    def __init__(self) -> None:
        self.closed = False

    async def close(self, **_: object) -> None:
        self.closed = True

    async def send_json(self, _: dict) -> None:
        return None


def _hello(*, capability_hash: str | None = None) -> HelloMessage:
    capabilities = ["query", "Observe", "query"]
    return HelloMessage(
        device_id="device-a",
        fixture_proof="token-a",
        capabilities=capabilities,
        capability_hash=capability_hash or canonical_capability_hash(capabilities),
    )


@pytest.mark.asyncio
async def test_query_token_is_rejected_before_websocket_accept():
    websocket = ScriptedWebSocket(token=None, hello=_hello())
    await serve_agent_websocket(
        websocket,
        authenticator=FixtureDeviceAuthenticator({"device-a": "token-a"}),
        registry=ConnectionRegistry(),
        on_message=lambda *_: None,
    )
    assert websocket.accepted is False
    assert websocket.closed == [(4401, "fixture bearer token required")]


@pytest.mark.asyncio
async def test_capability_hash_is_recomputed_and_mismatch_fails_closed():
    websocket = ScriptedWebSocket(token="token-a", hello=_hello(capability_hash="0" * 64))

    async def on_message(*_: object) -> None:
        raise AssertionError("mismatched Agent must not reach handler")

    await serve_agent_websocket(
        websocket,
        authenticator=FixtureDeviceAuthenticator({"device-a": "token-a"}),
        registry=ConnectionRegistry(),
        on_message=on_message,
    )
    assert websocket.accepted is True
    assert parse_agent_message(websocket.sent[0]).code == "capability_mismatch"
    assert websocket.closed[-1][0] == 4400


@pytest.mark.asyncio
async def test_bound_runtime_message_duplicate_and_durable_heartbeat_callbacks():
    websocket = ScriptedWebSocket(token="token-a", hello=_hello())
    registry = ConnectionRegistry()
    runtime_messages: list[AckMessage] = []
    heartbeats: list[HeartbeatMessage] = []
    connected: list[AgentConnection] = []
    disconnected: list[AgentConnection] = []

    async def on_message(connection: AgentConnection, message: AckMessage) -> None:
        assert await registry.is_current(connection)
        runtime_messages.append(message)

    async def on_connected(connection: AgentConnection) -> None:
        connected.append(connection)

    async def on_disconnected(connection: AgentConnection) -> None:
        disconnected.append(connection)

    async def on_heartbeat(
        connection: AgentConnection,
        message: HeartbeatMessage,
    ) -> None:
        assert connection.last_sequence == 2
        heartbeats.append(message)

    await serve_agent_websocket(
        websocket,
        authenticator=FixtureDeviceAuthenticator({"device-a": "token-a"}),
        registry=registry,
        on_message=on_message,
        on_connected=on_connected,
        on_disconnected=on_disconnected,
        on_heartbeat=on_heartbeat,
        validate_message=lambda *_: _true(),
    )

    assert len(runtime_messages) == 1
    assert len(heartbeats) == 1
    assert connected[0].capabilities == ("observe", "query")
    assert connected[0].capability_hash == canonical_capability_hash(("observe", "query"))
    assert disconnected == connected
    assert await registry.all() == []


async def _true() -> bool:
    return True


@pytest.mark.asyncio
async def test_replaced_connection_cannot_advance_sequence():
    registry = ConnectionRegistry()
    old_socket = ClosingSocket()
    old = AgentConnection("device-a", "session-a", old_socket, "cad.agent/1")
    new = AgentConnection("device-a", "session-b", ClosingSocket(), "cad.agent/1")
    await registry.add(old)
    previous = await registry.add(new)
    assert previous is old
    assert new.replaced_session_id == "session-a"
    assert old_socket.closed is True
    assert await registry.is_current(old) is False
    assert await registry.is_current_and_fresh(old) is False
    assert await registry.is_current_and_fresh(new) is True
    assert (
        await registry.accept_sequence(old, sequence=1, fingerprint="old-message")
        == "not_current"
    )


@pytest.mark.asyncio
async def test_initial_oversized_message_is_closed_with_bounded_error():
    websocket = ScriptedWebSocket(
        token="token-a",
        hello="x" * (MAX_WEBSOCKET_MESSAGE_BYTES + 1),
    )

    async def on_message(*_: object) -> None:
        raise AssertionError("oversized message must not reach handler")

    await serve_agent_websocket(
        websocket,
        authenticator=FixtureDeviceAuthenticator({"device-a": "token-a"}),
        registry=ConnectionRegistry(),
        on_message=on_message,
    )
    assert parse_agent_message(websocket.sent[0]).code == "message_too_large"
    assert websocket.closed[-1][0] == 4409


def test_protocol_requires_timezone_binding_sequence_and_valid_reconcile_evidence():
    with pytest.raises(ValidationError):
        HeartbeatMessage(
            session_id="session-a",
            device_id="device-a",
            sequence=1,
            issued_at="2026-07-22T00:00:00",
        )
    with pytest.raises(ValidationError):
        AckMessage(
            session_id="session-a",
            device_id="device-a",
            job_id="job-a",
            command_id="command-a",
            status="accepted",
            idempotency_key="request-a",
            payload_hash="a" * 64,
        )
    with pytest.raises(ValidationError):
        ReconcileResultMessage(
            session_id="session-a",
            device_id="device-a",
            job_id="job-a",
            command_id="command-a",
            sequence=1,
            status="terminal",
            payload_hash="a" * 64,
        )
    descriptor = ReconcileCommandDescriptor(
        job_id="job-a",
        command_id="command-a",
        payload_hash="a" * 64,
    )
    assert json.loads(descriptor.model_dump_json())["payload_hash"] == "a" * 64
