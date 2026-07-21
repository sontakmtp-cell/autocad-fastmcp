"""In-memory presence and socket registry for one Gateway worker."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AgentConnection:
    device_id: str
    session_id: str
    websocket: Any
    protocol_version: str
    last_heartbeat: datetime = field(default_factory=utc_now)
    last_sequence: int = 0
    busy: bool = False
    current_job_id: str | None = None
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def send(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.websocket.send_json(message)


class ConnectionRegistry:
    def __init__(self, *, stale_after_seconds: int = 45) -> None:
        if not 1 <= stale_after_seconds <= 3600:
            raise ValueError("stale_after_seconds must be between 1 and 3600")
        self.stale_after_seconds = stale_after_seconds
        self._connections: dict[str, AgentConnection] = {}
        self._lock = asyncio.Lock()

    async def add(self, connection: AgentConnection) -> AgentConnection | None:
        async with self._lock:
            previous = self._connections.get(connection.device_id)
            self._connections[connection.device_id] = connection
        if previous and previous.websocket is not connection.websocket:
            try:
                await previous.websocket.close(code=4001, reason="connection replaced")
            except Exception:
                pass
        return previous

    async def remove(self, device_id: str, session_id: str) -> None:
        async with self._lock:
            connection = self._connections.get(device_id)
            if connection and connection.session_id == session_id:
                self._connections.pop(device_id, None)

    async def get(self, device_id: str) -> AgentConnection | None:
        async with self._lock:
            return self._connections.get(device_id)

    async def all(self) -> list[AgentConnection]:
        async with self._lock:
            return list(self._connections.values())

    async def mark_heartbeat(
        self,
        device_id: str,
        session_id: str,
        *,
        sequence: int,
        busy: bool,
        current_job_id: str | None,
    ) -> bool:
        async with self._lock:
            connection = self._connections.get(device_id)
            if not connection or connection.session_id != session_id:
                return False
            connection.last_heartbeat = utc_now()
            connection.last_sequence = max(connection.last_sequence, sequence)
            connection.busy = busy
            connection.current_job_id = current_job_id
            return True

    async def stale_devices(self) -> list[str]:
        now = utc_now()
        async with self._lock:
            return [
                connection.device_id
                for connection in self._connections.values()
                if (now - connection.last_heartbeat).total_seconds() > self.stale_after_seconds
            ]

    async def is_fresh(self, device_id: str) -> bool:
        connection = await self.get(device_id)
        if connection is None:
            return False
        return (utc_now() - connection.last_heartbeat).total_seconds() <= self.stale_after_seconds

    async def close_all(self) -> None:
        connections = await self.all()
        for connection in connections:
            try:
                await connection.websocket.close(code=1001, reason="gateway shutdown")
            except Exception:
                pass
