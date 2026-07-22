"""In-memory presence and socket registry for one Gateway worker."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from autocad_contracts import canonical_capability_hash


logger = logging.getLogger(__name__)
SequenceDecision = Literal["accepted", "duplicate", "rejected", "not_current"]
_RECENT_SEQUENCE_LIMIT = 256


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AgentConnection:
    device_id: str
    session_id: str
    websocket: Any
    protocol_version: str
    capabilities: tuple[str, ...] = ("observe", "query")
    capability_hash: str = field(
        default_factory=lambda: canonical_capability_hash(("observe", "query"))
    )
    last_heartbeat: datetime = field(default_factory=utc_now)
    last_sequence: int = 0
    busy: bool = False
    current_job_id: str | None = None
    agent_version: str | None = None
    runtime_state: str | None = None
    document_name: str | None = None
    paused: bool = False
    current_command_id: str | None = None
    packages: tuple[dict[str, str], ...] = ()
    package_manifest_hash: str | None = None
    replaced_session_id: str | None = None
    send_timeout_seconds: float = 5.0
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _sequence_fingerprints: OrderedDict[int, str] = field(
        default_factory=OrderedDict,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not 0.1 <= self.send_timeout_seconds <= 30:
            raise ValueError("send_timeout_seconds must be between 0.1 and 30")

    async def send(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            await asyncio.wait_for(
                self.websocket.send_json(message),
                timeout=self.send_timeout_seconds,
            )

    def record_sequence(self, sequence: int, fingerprint: str) -> SequenceDecision:
        existing = self._sequence_fingerprints.get(sequence)
        if existing is not None:
            return "duplicate" if existing == fingerprint else "rejected"
        if sequence <= self.last_sequence:
            return "rejected"
        self.last_sequence = sequence
        self._sequence_fingerprints[sequence] = fingerprint
        while len(self._sequence_fingerprints) > _RECENT_SEQUENCE_LIMIT:
            self._sequence_fingerprints.popitem(last=False)
        return "accepted"


class ConnectionRegistry:
    def __init__(
        self,
        *,
        stale_after_seconds: int = 45,
        close_timeout_seconds: float = 2.0,
    ) -> None:
        if not 1 <= stale_after_seconds <= 3600:
            raise ValueError("stale_after_seconds must be between 1 and 3600")
        if not 0.1 <= close_timeout_seconds <= 30:
            raise ValueError("close_timeout_seconds must be between 0.1 and 30")
        self.stale_after_seconds = stale_after_seconds
        self.close_timeout_seconds = close_timeout_seconds
        self._connections: dict[str, AgentConnection] = {}
        self._lock = asyncio.Lock()

    async def add(self, connection: AgentConnection) -> AgentConnection | None:
        async with self._lock:
            previous = self._connections.get(connection.device_id)
            self._connections[connection.device_id] = connection
            if previous is not None and previous.session_id != connection.session_id:
                connection.replaced_session_id = previous.session_id
        if previous and previous.websocket is not connection.websocket:
            try:
                await asyncio.wait_for(
                    previous.websocket.close(code=4001, reason="connection replaced"),
                    timeout=self.close_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Timed out closing replaced Agent connection",
                    extra={"device_id": previous.device_id, "session_id": previous.session_id},
                )
            except Exception:
                logger.exception(
                    "Failed to close replaced Agent connection",
                    extra={"device_id": previous.device_id, "session_id": previous.session_id},
                )
        return previous

    async def remove(self, device_id: str, session_id: str) -> bool:
        async with self._lock:
            connection = self._connections.get(device_id)
            if connection and connection.session_id == session_id:
                self._connections.pop(device_id, None)
                return True
            return False

    async def get(self, device_id: str) -> AgentConnection | None:
        async with self._lock:
            return self._connections.get(device_id)

    async def is_current(self, connection: AgentConnection) -> bool:
        async with self._lock:
            current = self._connections.get(connection.device_id)
            return current is connection and current.session_id == connection.session_id

    async def is_current_and_fresh(self, connection: AgentConnection) -> bool:
        now = utc_now()
        async with self._lock:
            current = self._connections.get(connection.device_id)
            return bool(
                current is connection
                and current.session_id == connection.session_id
                and (now - current.last_heartbeat).total_seconds()
                <= self.stale_after_seconds
            )

    async def all(self) -> list[AgentConnection]:
        async with self._lock:
            return list(self._connections.values())

    async def accept_sequence(
        self,
        connection: AgentConnection,
        *,
        sequence: int,
        fingerprint: str,
    ) -> SequenceDecision:
        async with self._lock:
            current = self._connections.get(connection.device_id)
            if current is not connection or current.session_id != connection.session_id:
                return "not_current"
            return connection.record_sequence(sequence, fingerprint)

    async def mark_heartbeat(
        self,
        device_id: str,
        session_id: str,
        *,
        sequence: int,
        busy: bool,
        current_job_id: str | None,
        runtime_state: str | None = None,
        document_name: str | None = None,
        paused: bool | None = None,
        current_command_id: str | None = None,
    ) -> bool:
        async with self._lock:
            connection = self._connections.get(device_id)
            if not connection or connection.session_id != session_id:
                return False
            connection.last_heartbeat = utc_now()
            connection.last_sequence = max(connection.last_sequence, sequence)
            connection.busy = busy
            connection.current_job_id = current_job_id
            connection.runtime_state = runtime_state
            connection.document_name = document_name
            if paused is not None:
                connection.paused = paused
            connection.current_command_id = current_command_id
            return True

    async def stale_connections(self) -> list[AgentConnection]:
        now = utc_now()
        async with self._lock:
            return [
                connection
                for connection in self._connections.values()
                if (now - connection.last_heartbeat).total_seconds() > self.stale_after_seconds
            ]

    async def stale_devices(self) -> list[str]:
        return [connection.device_id for connection in await self.stale_connections()]

    async def is_fresh(self, device_id: str) -> bool:
        connection = await self.get(device_id)
        if connection is None:
            return False
        return (utc_now() - connection.last_heartbeat).total_seconds() <= self.stale_after_seconds

    async def close_all(self) -> None:
        connections = await self.all()

        async def close_one(connection: AgentConnection) -> None:
            try:
                await asyncio.wait_for(
                    connection.websocket.close(code=1001, reason="gateway shutdown"),
                    timeout=self.close_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Timed out closing Agent connection during shutdown",
                    extra={"device_id": connection.device_id, "session_id": connection.session_id},
                )
            except Exception:
                logger.exception(
                    "Failed to close Agent connection during shutdown",
                    extra={"device_id": connection.device_id, "session_id": connection.session_id},
                )

        await asyncio.gather(*(close_one(connection) for connection in connections))
