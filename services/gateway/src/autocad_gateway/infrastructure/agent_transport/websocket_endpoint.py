"""ASGI WebSocket handshake, binding and bounded message validation."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import uuid
from typing import Any, Awaitable, Callable

from autocad_contracts import (
    AckMessage,
    ErrorMessage,
    HeartbeatMessage,
    HelloMessage,
    MAX_WEBSOCKET_MESSAGE_BYTES,
    ProgressMessage,
    PROTOCOL_VERSION,
    ReconcileResultMessage,
    ResultMessage,
    WelcomeMessage,
    canonical_capabilities,
    canonical_capability_hash,
    canonical_json,
    message_dict,
    negotiate_protocol,
    parse_agent_message,
)
from starlette.websockets import WebSocketDisconnect

from .authenticator import FixtureAuthError, FixtureDeviceAuthenticator
from .connection_registry import AgentConnection, ConnectionRegistry


logger = logging.getLogger(__name__)
MessageHandler = Callable[[AgentConnection, Any], Awaitable[None]]
ConnectionHandler = Callable[[AgentConnection], Awaitable[None]]
DisconnectHandler = Callable[[AgentConnection], Awaitable[None]]
HeartbeatHandler = Callable[[AgentConnection, HeartbeatMessage], Awaitable[None]]
MessageValidator = Callable[[AgentConnection, Any], Awaitable[bool]]

_RUNTIME_AGENT_MESSAGES = (
    AckMessage,
    ProgressMessage,
    ResultMessage,
    ReconcileResultMessage,
)


def _bearer_token(websocket: Any) -> str | None:
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in websocket.scope.get("headers", [])
    }
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    return None


async def _send_error(websocket: Any, code: str, message: str) -> None:
    await websocket.send_json(message_dict(ErrorMessage(code=code, message=message)))


async def _safe_close(websocket: Any, *, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:
        logger.debug("WebSocket was already closed", exc_info=True)


async def _receive_text(websocket: Any) -> str:
    raw = await websocket.receive_text()
    if len(raw.encode("utf-8")) > MAX_WEBSOCKET_MESSAGE_BYTES:
        raise OverflowError("Agent message exceeds the protocol byte limit")
    return raw


def _fingerprint(message: Any) -> str:
    return hashlib.sha256(canonical_json(message_dict(message)).encode("utf-8")).hexdigest()


def _matches_connection(connection: AgentConnection, message: Any) -> bool:
    return (
        message.protocol_version == connection.protocol_version
        and message.session_id == connection.session_id
        and message.device_id == connection.device_id
    )


async def serve_agent_websocket(
    websocket: Any,
    *,
    authenticator: FixtureDeviceAuthenticator,
    registry: ConnectionRegistry,
    on_message: MessageHandler,
    on_connected: ConnectionHandler | None = None,
    on_disconnected: DisconnectHandler | None = None,
    on_heartbeat: HeartbeatHandler | None = None,
    validate_message: MessageValidator | None = None,
    heartbeat_interval_seconds: int = 10,
) -> None:
    """Serve one authenticated Agent session.

    The optional callbacks deliberately expose no repository type. Durable composition
    may persist heartbeat/session state and validate job-command binding without moving
    SQLite concerns into the transport package.
    """

    if not 1 <= heartbeat_interval_seconds <= 300:
        raise ValueError("heartbeat_interval_seconds must be between 1 and 300")
    token = _bearer_token(websocket)
    if not token:
        await _safe_close(websocket, code=4401, reason="fixture bearer token required")
        return
    try:
        authenticated_device = authenticator.authenticate(token)
    except FixtureAuthError:
        await _safe_close(websocket, code=4401, reason="fixture authentication failed")
        return

    await websocket.accept()
    connection: AgentConnection | None = None
    try:
        raw = await asyncio.wait_for(_receive_text(websocket), timeout=10)
        hello = parse_agent_message(raw)
        if not isinstance(hello, HelloMessage):
            await _send_error(websocket, "invalid_message", "hello is required first")
            await _safe_close(websocket, code=4400, reason="hello required")
            return
        if hello.protocol_version != PROTOCOL_VERSION:
            await _send_error(websocket, "incompatible", "Agent envelope version is unsupported")
            await _safe_close(websocket, code=4406, reason="incompatible protocol")
            return
        if hello.device_id != authenticated_device:
            await _send_error(websocket, "auth_failed", "device does not match fixture token")
            await _safe_close(websocket, code=4403, reason="device mismatch")
            return
        if not hmac.compare_digest(hello.fixture_proof, token):
            await _send_error(websocket, "auth_failed", "fixture proof does not match token")
            await _safe_close(websocket, code=4403, reason="fixture proof mismatch")
            return
        selected = negotiate_protocol(hello.protocol_min_version, hello.protocol_max_version)
        if selected is None:
            await _send_error(websocket, "incompatible", "cad.agent/1 is not supported")
            await _safe_close(websocket, code=4406, reason="incompatible protocol")
            return
        capabilities = canonical_capabilities(hello.capabilities)
        capability_hash = canonical_capability_hash(capabilities)
        if not hmac.compare_digest(hello.capability_hash, capability_hash):
            await _send_error(
                websocket,
                "capability_mismatch",
                "capability manifest hash does not match its canonical content",
            )
            await _safe_close(websocket, code=4400, reason="capability hash mismatch")
            return

        session_id = f"session-{uuid.uuid4()}"
        connection = AgentConnection(
            device_id=authenticated_device,
            session_id=session_id,
            websocket=websocket,
            protocol_version=selected,
            capabilities=capabilities,
            capability_hash=capability_hash,
            last_sequence=hello.last_processed_sequence,
        )
        await registry.add(connection)
        await websocket.send_json(
            message_dict(
                WelcomeMessage(
                    session_id=session_id,
                    selected_version=selected,
                    heartbeat_interval_seconds=heartbeat_interval_seconds,
                )
            )
        )
        if on_connected:
            await on_connected(connection)

        while True:
            raw = await _receive_text(websocket)
            message = parse_agent_message(raw)
            if not isinstance(message, (HeartbeatMessage, *_RUNTIME_AGENT_MESSAGES)):
                await _send_error(websocket, "invalid_message", "message is not valid from Agent")
                continue
            if not await registry.is_current(connection):
                await _send_error(websocket, "binding_mismatch", "Agent session was replaced")
                await _safe_close(websocket, code=4001, reason="connection replaced")
                return
            if not _matches_connection(connection, message):
                await _send_error(
                    websocket,
                    "binding_mismatch",
                    "message envelope does not match the authenticated session",
                )
                await _safe_close(websocket, code=4403, reason="message binding mismatch")
                return
            if isinstance(message, _RUNTIME_AGENT_MESSAGES) and validate_message is not None:
                if not await validate_message(connection, message):
                    await _send_error(
                        websocket,
                        "binding_mismatch",
                        "message does not match its durable command",
                    )
                    await _safe_close(websocket, code=4403, reason="command binding mismatch")
                    return

            sequence_decision = await registry.accept_sequence(
                connection,
                sequence=message.sequence,
                fingerprint=_fingerprint(message),
            )
            if sequence_decision == "not_current":
                await _safe_close(websocket, code=4001, reason="connection replaced")
                return
            if sequence_decision == "rejected":
                await _send_error(
                    websocket,
                    "sequence_rejected",
                    "Agent sequence is stale or conflicts with an earlier message",
                )
                continue
            if sequence_decision == "duplicate":
                # Exact wire replay is already represented by the first delivery.  It
                # must not append a second durable event or refresh stale presence.
                continue

            if isinstance(message, HeartbeatMessage):
                marked = await registry.mark_heartbeat(
                    connection.device_id,
                    connection.session_id,
                    sequence=message.sequence,
                    busy=message.busy,
                    current_job_id=message.current_job_id,
                )
                if not marked:
                    await _safe_close(websocket, code=4001, reason="connection replaced")
                    return
                if on_heartbeat:
                    await on_heartbeat(connection, message)
            else:
                await on_message(connection, message)
    except OverflowError:
        try:
            await _send_error(websocket, "message_too_large", "Agent message exceeds limit")
        finally:
            await _safe_close(websocket, code=4409, reason="message too large")
    except asyncio.TimeoutError:
        try:
            await _send_error(websocket, "invalid_message", "hello timed out")
        finally:
            await _safe_close(websocket, code=4408, reason="hello timeout")
    except (ValueError, TypeError):
        try:
            await _send_error(websocket, "invalid_message", "Agent message is invalid")
        finally:
            await _safe_close(websocket, code=4400, reason="invalid Agent message")
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception(
            "Unexpected Agent WebSocket failure",
            extra={
                "device_id": connection.device_id if connection else authenticated_device,
                "session_id": connection.session_id if connection else None,
            },
        )
    finally:
        if connection is not None:
            try:
                if on_disconnected:
                    await on_disconnected(connection)
            except Exception:
                logger.exception(
                    "Agent disconnect callback failed",
                    extra={
                        "device_id": connection.device_id,
                        "session_id": connection.session_id,
                    },
                )
            finally:
                await registry.remove(connection.device_id, connection.session_id)
