"""ASGI WebSocket handshake and message validation."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable

from autocad_contracts import (
    AckMessage,
    ErrorMessage,
    HeartbeatMessage,
    HelloMessage,
    ProgressMessage,
    ReconcileResultMessage,
    ResultMessage,
    WelcomeMessage,
    negotiate_protocol,
    parse_agent_message,
)

from .authenticator import FixtureAuthError, FixtureDeviceAuthenticator
from .connection_registry import AgentConnection, ConnectionRegistry


MessageHandler = Callable[[AgentConnection, Any], Awaitable[None]]
ConnectionHandler = Callable[[AgentConnection], Awaitable[None]]
DisconnectHandler = Callable[[AgentConnection], Awaitable[None]]


def _bearer_token(websocket: Any) -> str | None:
    headers = {key.decode().lower(): value.decode() for key, value in websocket.scope.get("headers", [])}
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return websocket.query_params.get("token")


async def _send_error(websocket: Any, code: str, message: str) -> None:
    await websocket.send_json(
        ErrorMessage(code=code, message=message).model_dump(mode="json", exclude_none=True)
    )


async def serve_agent_websocket(
    websocket: Any,
    *,
    authenticator: FixtureDeviceAuthenticator,
    registry: ConnectionRegistry,
    on_message: MessageHandler,
    on_connected: ConnectionHandler | None = None,
    on_disconnected: DisconnectHandler | None = None,
    heartbeat_interval_seconds: int = 10,
) -> None:
    token = _bearer_token(websocket)
    if not token:
        await websocket.close(code=4401, reason="fixture token required")
        return
    try:
        authenticated_device = authenticator.authenticate(token)
    except FixtureAuthError:
        await websocket.close(code=4401, reason="fixture authentication failed")
        return

    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        hello = parse_agent_message(raw)
        if not isinstance(hello, HelloMessage):
            await _send_error(websocket, "invalid_message", "hello is required first")
            await websocket.close(code=4400, reason="hello required")
            return
        if hello.device_id != authenticated_device:
            await _send_error(websocket, "auth_failed", "device does not match fixture token")
            await websocket.close(code=4403, reason="device mismatch")
            return
        if hello.fixture_proof != token:
            await _send_error(websocket, "auth_failed", "fixture proof does not match token")
            await websocket.close(code=4403, reason="fixture proof mismatch")
            return
        selected = negotiate_protocol(hello.protocol_min_version, hello.protocol_max_version)
        if selected is None:
            await _send_error(websocket, "incompatible", "cad.agent/1 is not supported")
            await websocket.close(code=4406, reason="incompatible protocol")
            return
        session_id = f"session-{uuid.uuid4()}"
        connection = AgentConnection(
            device_id=authenticated_device,
            session_id=session_id,
            websocket=websocket,
            protocol_version=selected,
            last_sequence=hello.last_processed_sequence,
        )
        await registry.add(connection)
        await websocket.send_json(
            WelcomeMessage(
                session_id=session_id,
                selected_version=selected,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
            ).model_dump(mode="json", exclude_none=True)
        )
        if on_connected:
            await on_connected(connection)
        while True:
            raw = await websocket.receive_json()
            message = parse_agent_message(raw)
            if isinstance(message, HeartbeatMessage):
                await registry.mark_heartbeat(
                    connection.device_id,
                    connection.session_id,
                    sequence=message.last_processed_sequence,
                    busy=message.busy,
                    current_job_id=message.current_job_id,
                )
            elif isinstance(message, (AckMessage, ProgressMessage, ResultMessage, ReconcileResultMessage)):
                await on_message(connection, message)
            else:
                await _send_error(websocket, "invalid_message", "message is not valid from Agent")
    except (asyncio.TimeoutError, ValueError, TypeError) as error:
        try:
            await _send_error(websocket, "invalid_message", str(error)[:256])
        except Exception:
            pass
    except Exception:
        # Starlette/uvicorn reports normal disconnects as exceptions. Durable state is
        # cleaned in the finally block and is never deleted here.
        pass
    finally:
        if "connection" in locals():
            if on_disconnected:
                await on_disconnected(connection)
            await registry.remove(connection.device_id, connection.session_id)
