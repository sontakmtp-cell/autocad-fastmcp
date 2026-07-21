"""Outbound WebSocket Agent with an in-memory command ledger."""

from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
import uuid
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from autocad_contracts import (
    AckMessage,
    CancelMessage,
    CommandMessage,
    HeartbeatMessage,
    HelloMessage,
    ProgressMessage,
    ReconcileMessage,
    ReconcileResultMessage,
    ResultMessage,
    WelcomeMessage,
    canonical_json,
    parse_agent_message,
)

from .scenarios import validate_scenario


@dataclass
class LedgerEntry:
    command_id: str
    payload_hash: str
    status: str = "not_started"
    result_status: str | None = None
    result: dict[str, Any] | None = None


@dataclass
class SimulatedAgent:
    url: str
    device_id: str
    token: str
    scenario: str = "success"
    ssl_context: ssl.SSLContext | None = None
    heartbeat_interval_seconds: int = 10
    ledger: dict[str, LedgerEntry] = field(default_factory=dict)
    _sequence: int = 0

    def __post_init__(self) -> None:
        validate_scenario(self.scenario)

    async def run(self, *, stop_after_terminal: bool = False) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with websockets.connect(
            self.url,
            additional_headers=headers,
            ssl=self.ssl_context,
            max_size=1_048_576,
        ) as websocket:
            hello = HelloMessage(
                device_id=self.device_id,
                fixture_proof=self.token,
                capability_hash=self._capability_hash(),
                capabilities=["observe", "query"],
            )
            await websocket.send(json.dumps(hello.model_dump(mode="json", exclude_none=True)))
            welcome = parse_agent_message(await websocket.recv())
            if not isinstance(welcome, WelcomeMessage):
                raise RuntimeError("Gateway did not send welcome")
            if self.scenario == "stale_heartbeat":
                await asyncio.Future()
            heartbeat_task = asyncio.create_task(self._heartbeats(websocket))
            try:
                while True:
                    raw = await websocket.recv()
                    message = parse_agent_message(raw)
                    if isinstance(message, CommandMessage):
                        await self._handle_command(websocket, message)
                        if stop_after_terminal and any(
                            entry.status == "terminal" for entry in self.ledger.values()
                        ):
                            return
                    elif isinstance(message, CancelMessage):
                        await self._handle_cancel(websocket, message)
                    elif isinstance(message, ReconcileMessage):
                        await self._handle_reconcile(websocket, message)
            except ConnectionClosed:
                return
            finally:
                heartbeat_task.cancel()

    async def _heartbeats(self, websocket: Any) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            heartbeat = HeartbeatMessage(
                session_id=None,
                device_id=self.device_id,
                sequence=self._sequence,
                last_processed_sequence=self._sequence,
            )
            await websocket.send(json.dumps(heartbeat.model_dump(mode="json", exclude_none=True)))

    async def _handle_command(self, websocket: Any, command: CommandMessage) -> None:
        existing = self.ledger.get(command.command_id)
        if existing:
            ack = AckMessage(
                session_id=command.session_id,
                device_id=self.device_id,
                job_id=command.job_id,
                command_id=command.command_id,
                status="already_terminal" if existing.status == "terminal" else "duplicate",
                idempotency_key=command.idempotency_key,
                payload_hash=existing.payload_hash,
            )
            await websocket.send(json.dumps(ack.model_dump(mode="json", exclude_none=True)))
            return
        entry = LedgerEntry(command.command_id, command.payload_hash)
        self.ledger[command.command_id] = entry
        if self.scenario == "delay_before_ack":
            await asyncio.sleep(0.5)
        if self.scenario == "drop_before_ack":
            await websocket.close(code=1011, reason="failure injection before ack")
            return
        ack_hash = "0" * 64 if self.scenario == "payload_hash_mismatch" else command.payload_hash
        ack = AckMessage(
            session_id=command.session_id,
            device_id=self.device_id,
            job_id=command.job_id,
            command_id=command.command_id,
            status="accepted",
            idempotency_key=command.idempotency_key,
            payload_hash=ack_hash,
        )
        await websocket.send(json.dumps(ack.model_dump(mode="json", exclude_none=True)))
        if self.scenario == "duplicate_ack":
            await websocket.send(json.dumps(ack.model_dump(mode="json", exclude_none=True)))
        if self.scenario in {"drop_after_ack_before_start", "reconnect_not_started"}:
            await websocket.close(code=1011, reason="failure injection after ack")
            return
        if self.scenario == "cancel_before_start":
            return
        entry.status = "started"
        progress_one = await self._progress(command, "inspect", 50, "Reading fixture drawing")
        await websocket.send(json.dumps(progress_one.model_dump(mode="json", exclude_none=True)))
        if self.scenario == "out_of_order_progress":
            progress_old = progress_one.model_copy(update={"sequence": max(0, progress_one.sequence - 1)})
            await websocket.send(json.dumps(progress_old.model_dump(mode="json", exclude_none=True)))
        if self.scenario == "duplicate_progress":
            await websocket.send(json.dumps(progress_one.model_dump(mode="json", exclude_none=True)))
        if self.scenario in {"drop_after_start_before_result", "reconnect_started"}:
            await websocket.close(code=1011, reason="failure injection after start")
            return
        if self.scenario == "delay_result":
            await asyncio.sleep(0.5)
        progress_two = await self._progress(command, "complete", 100, "Fixture observation ready")
        await websocket.send(json.dumps(progress_two.model_dump(mode="json", exclude_none=True)))
        result = ResultMessage(
            session_id=command.session_id,
            device_id=self.device_id,
            job_id=command.job_id,
            command_id=command.command_id,
            sequence=self._next_sequence(),
            status="succeeded",
            payload_hash=command.payload_hash,
            result=self._result_payload(command),
        )
        await websocket.send(json.dumps(result.model_dump(mode="json", exclude_none=True)))
        if self.scenario == "duplicate_result":
            await websocket.send(json.dumps(result.model_dump(mode="json", exclude_none=True)))
        entry.status = "terminal"
        entry.result_status = "succeeded"
        entry.result = result.result

    async def _handle_cancel(self, websocket: Any, message: CancelMessage) -> None:
        entry = self.ledger.get(message.command_id or "")
        if entry and entry.status == "terminal" and self.scenario == "cancel_too_late":
            return
        result = ResultMessage(
            session_id=message.session_id,
            device_id=self.device_id,
            job_id=message.job_id,
            command_id=message.command_id,
            status="cancelled",
            payload_hash=entry.payload_hash if entry else "0" * 64,
        )
        await websocket.send(json.dumps(result.model_dump(mode="json", exclude_none=True)))
        if entry:
            entry.status = "terminal"
            entry.result_status = "cancelled"

    async def _handle_reconcile(self, websocket: Any, message: ReconcileMessage) -> None:
        for command_id in message.command_ids:
            entry = self.ledger.get(command_id)
            status = entry.status if entry else "not_started"
            if self.scenario == "reconnect_terminal" and entry:
                status = "terminal"
            result_status = entry.result_status if entry else None
            reply = ReconcileResultMessage(
                session_id=message.session_id,
                device_id=self.device_id,
                command_id=command_id,
                status=status,
                payload_hash=entry.payload_hash if entry else "0" * 64,
                result_status=result_status,
                result=entry.result if entry else None,
            )
            await websocket.send(json.dumps(reply.model_dump(mode="json", exclude_none=True)))

    async def _progress(self, command: CommandMessage, phase: str, percent: int, message: str) -> ProgressMessage:
        return ProgressMessage(
            session_id=command.session_id,
            device_id=self.device_id,
            job_id=command.job_id,
            command_id=command.command_id,
            sequence=self._next_sequence(),
            phase=phase,
            percent=percent,
            message=message,
        )

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _result_payload(self, command: CommandMessage) -> dict[str, Any]:
        if command.kind == "write_fixture":
            return {"fixture": "write-effect-recorded"}
        entities = [
            {"entity_id": "E1", "entity_type": "LINE", "layer": "0", "geometry": {"start": [0, 0], "end": [100, 0]}},
            {"entity_id": "E2", "entity_type": "CIRCLE", "layer": "A", "geometry": {"center": [50, 25], "radius": 10}},
        ]
        drawing = {"entity_count": len(entities), "layers": ["0", "A"], "name": "phase3-fixture"}
        revision = hashlib.sha256(canonical_json({"drawing": drawing, "entities": entities}).encode()).hexdigest()
        snapshot = {
            "snapshot_id": f"snapshot-{uuid.uuid4()}",
            "document_revision": revision,
            "observation_level": command.payload.get("observation_level", "summary"),
            "drawing": drawing,
            "entity_summary": {"CIRCLE": 1, "LINE": 1},
            "entities": entities,
        }
        return {"snapshot": snapshot}

    @staticmethod
    def _capability_hash() -> str:
        return hashlib.sha256(b"observe,query").hexdigest()
