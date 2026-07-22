"""Outbound reconnecting WebSocket Agent with an in-memory command ledger."""

from __future__ import annotations

import asyncio
import copy
import json
import ssl
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from autocad_contracts import (
    AckMessage,
    CancelMessage,
    CommandMessage,
    ErrorMessage,
    HeartbeatMessage,
    HelloMessage,
    MAX_WEBSOCKET_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    ProgressMessage,
    ReconcileCommandDescriptor,
    ReconcileMessage,
    ReconcileResultMessage,
    ResultMessage,
    WelcomeMessage,
    canonical_capability_hash,
    canonical_json,
    canonical_payload_hash,
    document_revision,
    message_dict,
    parse_agent_message,
)

from .scenarios import validate_scenario


_CAPABILITIES = ("observe", "query")


@dataclass
class LedgerEntry:
    command_id: str
    job_id: str
    idempotency_key: str
    payload_hash: str
    status: str = "not_started"
    result_status: str | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    allow_redispatch: bool = False
    execution_count: int = 0
    cancel_requested: bool = False


@dataclass
class SimulatedAgent:
    url: str
    device_id: str
    token: str
    scenario: str = "success"
    ssl_context: ssl.SSLContext | None = None
    heartbeat_interval_seconds: int = 10
    fixture_variant: int = 0
    reconnect_initial_seconds: float = 0.05
    reconnect_max_seconds: float = 1.0
    max_reconnects: int = 8
    ledger: dict[str, LedgerEntry] = field(default_factory=dict)
    _sequence: int = 0
    _session_count: int = 0
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _injected: set[tuple[str, str]] = field(default_factory=set, repr=False)
    _stop_after_terminal: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        validate_scenario(self.scenario)
        if not 0 <= self.fixture_variant <= 1_000_000:
            raise ValueError("fixture_variant must be between 0 and 1000000")
        if not 0.01 <= self.reconnect_initial_seconds <= 10:
            raise ValueError("reconnect_initial_seconds must be between 0.01 and 10")
        if not self.reconnect_initial_seconds <= self.reconnect_max_seconds <= 30:
            raise ValueError("reconnect_max_seconds is outside the bounded retry range")
        if not 0 <= self.max_reconnects <= 100:
            raise ValueError("max_reconnects must be between 0 and 100")

    @property
    def session_count(self) -> int:
        return self._session_count

    @property
    def execution_count(self) -> int:
        return sum(entry.execution_count for entry in self.ledger.values())

    def set_fixture_variant(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
            raise ValueError("fixture variant must be an integer between 0 and 1000000")
        self.fixture_variant = value

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self, *, stop_after_terminal: bool = False) -> None:
        """Run until stopped, a terminal test result, or the reconnect budget is exhausted."""

        self._stop_after_terminal = stop_after_terminal
        reconnects = 0
        delay = self.reconnect_initial_seconds
        while not self._stop_event.is_set():
            try:
                await self._run_session()
            except asyncio.CancelledError:
                self.stop()
                raise
            except (ConnectionClosed, OSError, asyncio.TimeoutError):
                if self._stop_event.is_set():
                    return
            if self._stop_event.is_set():
                return
            reconnects += 1
            if reconnects > self.max_reconnects:
                raise RuntimeError("simulated Agent reconnect budget exhausted")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, self.reconnect_max_seconds)

    async def _run_session(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with websockets.connect(
            self.url,
            additional_headers=headers,
            ssl=self.ssl_context,
            max_size=MAX_WEBSOCKET_MESSAGE_BYTES,
            max_queue=16,
            open_timeout=5,
            close_timeout=2,
        ) as websocket:
            self._session_count += 1
            hello = HelloMessage(
                device_id=self.device_id,
                fixture_proof=self.token,
                capability_hash=canonical_capability_hash(_CAPABILITIES),
                capabilities=list(_CAPABILITIES),
                last_processed_sequence=self._sequence,
            )
            await self._send(websocket, hello)
            welcome = parse_agent_message(await self._recv(websocket))
            if (
                not isinstance(welcome, WelcomeMessage)
                or welcome.protocol_version != PROTOCOL_VERSION
                or welcome.selected_version != PROTOCOL_VERSION
            ):
                raise RuntimeError("Gateway did not send welcome")
            session_id = welcome.session_id
            self.heartbeat_interval_seconds = welcome.heartbeat_interval_seconds

            session_tasks: set[asyncio.Task[Any]] = set()
            if self.scenario != "stale_heartbeat":
                session_tasks.add(
                    asyncio.create_task(self._heartbeats(websocket, session_id))
                )
            session_tasks.add(asyncio.create_task(self._close_when_stopped(websocket)))
            try:
                while not self._stop_event.is_set():
                    message = parse_agent_message(await self._recv(websocket))
                    if isinstance(message, CommandMessage):
                        self._validate_gateway_binding(message, session_id)
                        task = await self._accept_command(websocket, message)
                        if task is not None:
                            session_tasks.add(task)
                    elif isinstance(message, CancelMessage):
                        self._validate_gateway_binding(message, session_id)
                        await self._handle_cancel(websocket, message)
                    elif isinstance(message, ReconcileMessage):
                        self._validate_gateway_binding(message, session_id)
                        await self._handle_reconcile(websocket, message)
                    elif isinstance(message, ErrorMessage):
                        # Failure scenarios intentionally provoke bounded protocol errors.
                        continue
                    else:
                        raise RuntimeError("Gateway sent an invalid message to Agent")
            finally:
                for task in session_tasks:
                    task.cancel()
                if session_tasks:
                    await asyncio.gather(*session_tasks, return_exceptions=True)

    async def _recv(self, websocket: Any) -> str | bytes:
        raw = await websocket.recv()
        size = len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8"))
        if size > MAX_WEBSOCKET_MESSAGE_BYTES:
            raise RuntimeError("Gateway message exceeds Agent byte limit")
        return raw

    async def _send(self, websocket: Any, message: Any) -> None:
        encoded = canonical_json(message_dict(message))
        if len(encoded.encode("utf-8")) > MAX_WEBSOCKET_MESSAGE_BYTES:
            raise RuntimeError("Agent message exceeds byte limit")
        async with self._send_lock:
            await websocket.send(encoded)

    async def _close_when_stopped(self, websocket: Any) -> None:
        await self._stop_event.wait()
        await websocket.close(code=1000, reason="simulator stopped")

    async def _heartbeats(self, websocket: Any, session_id: str) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            last_processed = self._sequence
            heartbeat = HeartbeatMessage(
                session_id=session_id,
                device_id=self.device_id,
                sequence=self._next_sequence(),
                last_processed_sequence=last_processed,
                busy=any(entry.status == "started" for entry in self.ledger.values()),
                current_job_id=next(
                    (entry.job_id for entry in self.ledger.values() if entry.status == "started"),
                    None,
                ),
            )
            await self._send(websocket, heartbeat)

    def _validate_gateway_binding(self, message: Any, session_id: str) -> None:
        if (
            message.protocol_version != PROTOCOL_VERSION
            or message.session_id != session_id
            or message.device_id != self.device_id
        ):
            raise RuntimeError("Gateway message does not match Agent session")

    async def _accept_command(
        self,
        websocket: Any,
        command: CommandMessage,
    ) -> asyncio.Task[None] | None:
        actual_payload_hash = canonical_payload_hash(command.payload)
        if actual_payload_hash != command.payload_hash:
            await self._send_ack(
                websocket,
                command,
                status="rejected",
                reason="command payload hash mismatch",
            )
            return None
        existing = self.ledger.get(command.command_id)
        if existing is not None:
            if (
                existing.job_id != command.job_id
                or existing.idempotency_key != command.idempotency_key
                or existing.payload_hash != command.payload_hash
            ):
                await self._send_ack(
                    websocket,
                    command,
                    status="rejected",
                    reason="command identity conflicts with ledger",
                )
                return None
            if existing.status == "terminal":
                await self._send_ack(websocket, command, status="already_terminal")
                await self._send_terminal_from_ledger(websocket, command, existing)
                return None
            if existing.status == "started" or not existing.allow_redispatch:
                await self._send_ack(websocket, command, status="duplicate")
                return None
            existing.allow_redispatch = False
            return asyncio.create_task(self._execute_command(websocket, command, existing))

        if command.deadline_at is not None and datetime.fromisoformat(
            command.deadline_at
        ) <= datetime.now(timezone.utc):
            await self._send_ack(
                websocket,
                command,
                status="rejected",
                reason="deadline_expired",
            )
            return None

        entry = LedgerEntry(
            command_id=command.command_id,
            job_id=command.job_id,
            idempotency_key=command.idempotency_key,
            payload_hash=command.payload_hash,
        )
        self.ledger[command.command_id] = entry
        return asyncio.create_task(self._execute_command(websocket, command, entry))

    async def _execute_command(
        self,
        websocket: Any,
        command: CommandMessage,
        entry: LedgerEntry,
    ) -> None:
        if self.scenario == "delay_before_ack" and self._inject_once(command.command_id):
            await asyncio.sleep(0.5)
        if self.scenario == "drop_before_ack" and self._inject_once(command.command_id):
            await websocket.close(code=1011, reason="failure injection before ack")
            return

        wrong_hash = "0" * 64 if self.scenario == "payload_hash_mismatch" else None
        ack = await self._send_ack(
            websocket,
            command,
            status="accepted",
            payload_hash=wrong_hash,
        )
        if self.scenario == "duplicate_ack":
            await self._send(websocket, ack)
        if self.scenario in {"drop_after_ack_before_start", "reconnect_not_started"} and self._inject_once(command.command_id):
            await websocket.close(code=1011, reason="failure injection after ack")
            return
        if self.scenario == "cancel_before_start":
            return

        entry.status = "started"
        entry.execution_count += 1
        progress_one = self._progress(command, "inspect", 50, "Reading fixture drawing")
        await self._send(websocket, progress_one)
        if self.scenario == "out_of_order_progress":
            progress_old = progress_one.model_copy(
                update={"sequence": max(1, progress_one.sequence - 1)}
            )
            await self._send(websocket, progress_old)
        if self.scenario == "duplicate_progress":
            await self._send(websocket, progress_one)
        if self.scenario in {"drop_after_start_before_result", "reconnect_started"} and self._inject_once(command.command_id):
            await websocket.close(code=1011, reason="failure injection after start")
            return
        if self.scenario == "cancel_while_running":
            for _ in range(100):
                if entry.cancel_requested or entry.status == "terminal":
                    return
                await asyncio.sleep(0.01)
        if self.scenario == "delay_result":
            await asyncio.sleep(0.5)
        if entry.cancel_requested or entry.status == "terminal":
            return

        progress_two = self._progress(command, "complete", 100, "Fixture observation ready")
        await self._send(websocket, progress_two)
        result_payload = self._result_payload(command)
        entry.status = "terminal"
        entry.result_status = "succeeded"
        entry.result = result_payload
        result = ResultMessage(
            session_id=command.session_id,
            device_id=self.device_id,
            job_id=command.job_id,
            command_id=command.command_id,
            sequence=self._next_sequence(),
            status="succeeded",
            payload_hash=command.payload_hash,
            result=result_payload,
        )
        if self.scenario == "reconnect_terminal" and self._inject_once(command.command_id):
            await websocket.close(code=1011, reason="failure injection before terminal send")
            return
        await self._send(websocket, result)
        if self.scenario == "duplicate_result":
            await self._send(websocket, result)
        if self._stop_after_terminal:
            self.stop()

    async def _send_ack(
        self,
        websocket: Any,
        command: CommandMessage,
        *,
        status: str,
        reason: str | None = None,
        payload_hash: str | None = None,
    ) -> AckMessage:
        ack = AckMessage(
            session_id=command.session_id,
            device_id=self.device_id,
            job_id=command.job_id,
            command_id=command.command_id,
            sequence=self._next_sequence(),
            status=status,
            idempotency_key=command.idempotency_key,
            payload_hash=payload_hash or command.payload_hash,
            reason=reason,
        )
        await self._send(websocket, ack)
        return ack

    async def _handle_cancel(self, websocket: Any, message: CancelMessage) -> None:
        entry = self.ledger.get(message.command_id)
        if entry is None or entry.job_id != message.job_id:
            return
        if entry.status == "terminal":
            await self._send_terminal_from_ledger(websocket, message, entry)
            return
        entry.cancel_requested = True
        entry.status = "terminal"
        entry.result_status = "cancelled"
        entry.result = None
        await self._send_terminal_from_ledger(websocket, message, entry)
        if self._stop_after_terminal:
            self.stop()

    async def _send_terminal_from_ledger(
        self,
        websocket: Any,
        message: CommandMessage | CancelMessage,
        entry: LedgerEntry,
    ) -> None:
        if entry.result_status is None:
            return
        await self._send(
            websocket,
            ResultMessage(
                session_id=message.session_id,
                device_id=self.device_id,
                job_id=entry.job_id,
                command_id=entry.command_id,
                sequence=self._next_sequence(),
                status=entry.result_status,
                payload_hash=entry.payload_hash,
                result=entry.result,
                error_code=entry.error_code,
                error_message=entry.error_message,
            ),
        )

    async def _handle_reconcile(self, websocket: Any, message: ReconcileMessage) -> None:
        for descriptor in message.commands:
            await self._send_reconcile_result(websocket, message, descriptor)

    async def _send_reconcile_result(
        self,
        websocket: Any,
        message: ReconcileMessage,
        descriptor: ReconcileCommandDescriptor,
    ) -> None:
        entry = self.ledger.get(descriptor.command_id)
        if entry is None:
            status = "not_started"
            result_status = None
            result = None
            error_code = None
            error_message = None
        else:
            if entry.job_id != descriptor.job_id or entry.payload_hash != descriptor.payload_hash:
                raise RuntimeError("Gateway reconciliation descriptor conflicts with Agent ledger")
            status = entry.status
            result_status = entry.result_status
            result = entry.result
            error_code = entry.error_code
            error_message = entry.error_message
            if status == "not_started":
                entry.allow_redispatch = True
        reply = ReconcileResultMessage(
            session_id=message.session_id,
            device_id=self.device_id,
            job_id=descriptor.job_id,
            command_id=descriptor.command_id,
            sequence=self._next_sequence(),
            status=status,
            payload_hash=descriptor.payload_hash,
            result_status=result_status,
            result=result,
            error_code=error_code,
            error_message=error_message,
        )
        await self._send(websocket, reply)
        if status == "terminal" and self._stop_after_terminal:
            self.stop()

    def _progress(
        self,
        command: CommandMessage,
        phase: str,
        percent: int,
        message: str,
    ) -> ProgressMessage:
        return ProgressMessage(
            session_id=command.session_id,
            device_id=self.device_id,
            job_id=command.job_id,
            command_id=command.command_id,
            sequence=self._next_sequence(),
            payload_hash=command.payload_hash,
            phase=phase,
            percent=percent,
            message=message,
        )

    def _next_sequence(self) -> int:
        if self._sequence >= 1_000_000_000:
            raise RuntimeError("Agent sequence exhausted")
        self._sequence += 1
        return self._sequence

    def _inject_once(self, command_id: str) -> bool:
        key = (self.scenario, command_id)
        if key in self._injected:
            return False
        self._injected.add(key)
        return True

    def _result_payload(self, command: CommandMessage) -> dict[str, Any]:
        if command.kind == "write_fixture":
            return {"fixture": "write-effect-recorded", "variant": self.fixture_variant}

        variant = self.fixture_variant
        revision_entities = [
            {
                "entity_id": "E1",
                "entity_type": "LINE",
                "layer": "0",
                "geometry": {
                    "start": [variant, 0],
                    "end": [100 + variant, 0],
                },
            },
            {
                "entity_id": "E2",
                "entity_type": "CIRCLE",
                "layer": "A",
                "geometry": {
                    "center": [50, 25 + variant],
                    "radius": 10 + variant,
                },
            },
        ]
        drawing = {
            "entity_count": len(revision_entities),
            "layers": ["0", "A"],
            "name": "phase3-fixture",
        }
        revision_drawing = {key: copy.deepcopy(value) for key, value in drawing.items() if key != "entity_count"}
        revision = document_revision(
            document_identity={
                "device_id": self.device_id,
                "document_name": drawing["name"],
            },
            drawing=revision_drawing,
            entities=revision_entities,
        )
        public_entities = copy.deepcopy(revision_entities)
        if command.payload.get("observation_level", "summary") == "summary":
            for entity in public_entities:
                entity["geometry"] = {}
        snapshot = {
            "snapshot_id": f"snapshot-{uuid.uuid4()}",
            "document_revision": revision,
            "observation_level": command.payload.get("observation_level", "summary"),
            "drawing": drawing,
            "entity_summary": {"CIRCLE": 1, "LINE": 1},
            "entities": public_entities,
        }
        return {"snapshot": snapshot}
