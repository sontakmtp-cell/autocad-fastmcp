"""Outbound Agent session with isolated read and CAD Program routing."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from autocad_contracts import (
    AckMessage,
    CancelMessage,
    CommandMessage,
    ErrorMessage,
    HeartbeatMessage,
    HelloMessage,
    ProgramCommandMessage,
    ProgramResultMessage,
    ReconcileMessage,
    ReconcileResultMessage,
    ResultMessage,
    WelcomeMessage,
    canonical_capabilities,
    canonical_capability_hash,
    canonical_capability_manifest_hash,
    canonical_package_manifest_hash,
    canonical_payload_hash,
    message_dict,
    parse_agent_message,
)

from . import __version__
from .config import AgentConfig
from .credentials import CredentialProvider
from .diagnostics import export_diagnostics
from .executor import AgentExecutionError, DrawingInfoExecutor
from .ledger import CommandLedger, LedgerConflict, TERMINAL
from .manifest import PackageMismatch, verify_package
from .state import AgentIntent, AgentViewState, RuntimeState, runtime_user_label
from .program_executor import ProgramCommandExecutor


class AgentCore:
    def __init__(
        self,
        config: AgentConfig,
        credentials: CredentialProvider,
        ledger: CommandLedger,
        executor: DrawingInfoExecutor,
        runtime_broker: Any | None = None,
        program_executor: ProgramCommandExecutor | None = None,
    ) -> None:
        self.config = config.validate()
        self.credentials = credentials
        self.ledger = ledger
        self.executor = executor
        self.runtime_broker = runtime_broker
        self.program_executor = program_executor
        if runtime_broker is not None and hasattr(
            self.executor,
            "set_runtime_broker",
        ):
            self.executor.set_runtime_broker(runtime_broker)
        self.package = config.package
        self._package_valid = self._refresh_package()
        self.paused = ledger.is_paused()
        self.write_lock_enabled = ledger.initialize_write_lock(
            config.write_lock_enabled
        )
        interrupted_unknown, _ = ledger.recover_interrupted_programs()
        self._stop = asyncio.Event()
        self._retry = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._observers: list[Callable[[AgentViewState], None]] = []
        self._session_id: str | None = None
        self._protocol_version = str(
            getattr(credentials, "protocol_version", "cad.agent/1")
        )
        self._current_command_id: str | None = None
        self._last_ids: dict[str, Any] = {}
        self._state = AgentViewState(
            device_name=config.device_name,
            runtime_state=(
                RuntimeState.INCOMPATIBLE
                if not self._package_valid
                else RuntimeState.PAUSED if self.paused else RuntimeState.OFFLINE
            ),
            paused=self.paused,
            hard_pause=self.paused,
            write_lock_enabled=self.write_lock_enabled,
            outcome_unknown=interrupted_unknown > 0,
            support_id=(
                "P6-RECOVERY-UNKNOWN"
                if interrupted_unknown > 0
                else None
            ),
            agent_version=__version__,
            package_version=config.package_version,
            managed_host_enabled=config.managed_host_enabled,
            full_compat_fallback_enabled=config.allow_full_compat_fallback,
        )

    @property
    def view_state(self) -> AgentViewState:
        return self._state

    def subscribe(self, callback: Callable[[AgentViewState], None]) -> None:
        self._observers.append(callback)
        callback(self._state)

    def handle_intent(self, intent: AgentIntent, diagnostics_target: Path | None = None) -> None:
        if intent in {AgentIntent.RETRY, AgentIntent.RETRY_RUNTIME_PROBE}:
            self._set_event(self._retry)
        elif intent in {AgentIntent.PAUSE, AgentIntent.RESUME}:
            self.set_paused(intent == AgentIntent.PAUSE)
        elif intent in {
            AgentIntent.ENABLE_WRITE_LOCK,
            AgentIntent.DISABLE_WRITE_LOCK,
        }:
            self.set_write_lock(intent == AgentIntent.ENABLE_WRITE_LOCK)
        elif intent == AgentIntent.EXPORT_DIAGNOSTICS:
            if diagnostics_target is None:
                raise ValueError("diagnostics target is required")
            export_diagnostics(
                diagnostics_target,
                device_id=self.config.device_id,
                values={
                    "agent_version": __version__,
                    "package_manifest_hash": canonical_package_manifest_hash([self.package]),
                    "product": self._state.product,
                    "edition": self._state.edition,
                    "release_year": self._state.release_year,
                    "series": self._state.series,
                    "vertical": self._state.vertical,
                    "runtime_id": self._state.runtime_id,
                    "runtime_role": self._state.runtime_role,
                    "degradation_reason": self._state.degradation_reason,
                    "host_family": self._state.host_family,
                    "host_version": self._state.host_version,
                    "host_package_version": self._state.host_package_version,
                    "host_package_hash": self._state.host_package_hash,
                    "host_handshake_state": self._state.host_handshake_state,
                    "capability_manifest_hash": self._state.capability_manifest_hash,
                    "registry_version": self._state.registry_version,
                    "active_document_id": self._state.active_document_id,
                    "active_document_revision": (
                        self._state.active_document_revision
                    ),
                    "write_lock_enabled": self._state.write_lock_enabled,
                    "hard_pause": self._state.hard_pause,
                    "active_job_id": self._state.active_job_id,
                    "mismatch_reason": self._state.mismatch_reason,
                    "outcome_unknown": self._state.outcome_unknown,
                    "support_id": self._state.support_id,
                    **self._last_ids,
                },
            )
        elif intent == AgentIntent.EXIT:
            self._set_event(self._stop)
            self._set_event(self._retry)

    def _set_event(self, event: asyncio.Event) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(event.set)
                return
            except RuntimeError:
                pass
        event.set()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.ledger.set_paused(paused)
        self._publish(
            runtime_state=RuntimeState.PAUSED if paused else RuntimeState.CONNECTING,
            paused=paused,
            hard_pause=paused,
        )
        if not paused:
            self._set_event(self._retry)

    def set_write_lock(self, enabled: bool) -> None:
        self.write_lock_enabled = enabled
        self.ledger.set_write_lock(enabled)
        self._publish(write_lock_enabled=enabled)

    async def run_forever(self) -> None:
        import websockets

        self._loop = asyncio.get_running_loop()
        backoff = 1
        while not self._stop.is_set():
            self._retry.clear()
            connection_stage = "package"
            if not self._refresh_package():
                self._publish(
                    runtime_state=RuntimeState.INCOMPATIBLE,
                    server_connected=False,
                    support_code="C1-PKG-001",
                )
                await self._wait_for_retry(backoff)
                backoff = min(backoff * 2, self.config.reconnect_max_seconds)
                continue
            self._publish(
                runtime_state=RuntimeState.CONNECTING,
                server_connected=False,
                support_code=None,
            )
            try:
                connection_stage = "credential"
                credential = self.credentials.load()
                if inspect.isawaitable(credential):
                    credential = await credential
                connection_stage = "connect"
                async with websockets.connect(
                    self.config.gateway_ws_url,
                    additional_headers={"Authorization": f"Bearer {credential}"},
                    max_size=1_048_576,
                    max_queue=16,
                    open_timeout=10,
                    close_timeout=5,
                ) as websocket:
                    backoff = 1
                    connection_stage = "session"
                    await self._run_session(websocket, credential)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                diagnostic_stage = str(
                    self._last_ids.get("connection_stage", connection_stage)
                )
                self._last_ids.update(
                    {
                        "connection_stage": diagnostic_stage,
                        "safe_error_code": "connection_failed",
                        "safe_error_type": type(exc).__name__,
                    }
                )
                close_code = None
                if isinstance(exc, websockets.exceptions.ConnectionClosed):
                    frame = exc.rcvd or exc.sent
                    close_code = frame.code if frame is not None else None
                if close_code == 4403:
                    self._last_ids["safe_error_code"] = "credential_revoked"
                support_codes = {
                    "credential": "C1-AUTH-001",
                    "connect": "C1-NET-001",
                    "hello": "C1-PROTO-001",
                    "presence": "C1-CAD-001",
                }
                if self._state.runtime_state != RuntimeState.INCOMPATIBLE:
                    self._publish(
                        runtime_state=RuntimeState.OFFLINE,
                        server_connected=False,
                        support_code=support_codes.get(
                            diagnostic_stage, "C1-NET-002"
                        ),
                    )
            if self._stop.is_set():
                break
            await self._wait_for_retry(backoff)
            backoff = min(backoff * 2, self.config.reconnect_max_seconds)
        self._loop = None
        self.ledger.close()

    async def _wait_for_retry(self, timeout: int) -> None:
        try:
            await asyncio.wait_for(self._retry.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def _refresh_package(self) -> bool:
        try:
            self.package = verify_package(self.config.package_path, self.config.package)
        except PackageMismatch:
            self._package_valid = False
            return False
        self._package_valid = True
        return True

    async def _run_session(self, websocket: Any, credential: str) -> None:
        self._last_ids["connection_stage"] = "hello"
        message_id = str(uuid.uuid4())
        protocol_version = str(
            getattr(self.credentials, "protocol_version", "cad.agent/1")
        )
        if protocol_version == "cad.agent/2":
            proof = self.credentials.hello_proof(message_id, credential)
            fixture_proof = None
        else:
            proof = hmac.new(
                credential.encode("utf-8"),
                f"{self.config.device_id}:{message_id}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            fixture_proof = "phase4-c1"
        capabilities = ["observe"]
        capability_manifest = None
        packages = [self.package]
        if self.runtime_broker is not None and self.config.program_v0_enabled:
            try:
                selection = await self.runtime_broker.describe_managed_runtime()
            except Exception:
                pass
            else:
                capability_manifest = selection.manifest
                product = (
                    capability_manifest.cad_products[0]
                    if capability_manifest.cad_products
                    else None
                )
                if product is not None:
                    mapping = {
                        "cad.program.preview": "program_preview",
                        "cad.program.commit": "program_commit",
                        "cad.program.validate": "program_validate",
                    }
                    capabilities.extend(
                        mapping[item]
                        for item in product.capabilities
                        if item in mapping
                    )
                    runtime = product.runtime
                    if (
                        runtime.package_id
                        and runtime.package_version
                        and runtime.package_hash
                    ):
                        packages.append(
                            {
                                "package_id": runtime.package_id,
                                "version": runtime.package_version,
                                "sha256": runtime.package_hash.removeprefix(
                                    "sha256:"
                                ),
                            }
                        )
        capabilities = list(canonical_capabilities(capabilities))
        proof_fields = (
            {"device_proof": proof}
            if protocol_version == "cad.agent/2"
            else {"fixture_proof": fixture_proof, "device_proof": proof}
        )
        hello = HelloMessage(
            protocol_version=protocol_version,
            protocol_min_version=protocol_version,
            protocol_max_version=protocol_version,
            message_id=message_id,
            device_id=self.config.device_id,
            capability_hash=canonical_capability_hash(capabilities),
            capabilities=capabilities,
            last_processed_sequence=self.ledger.last_sequence(),
            agent_version=__version__,
            runtime_state=self._state.runtime_state.value,
            document_name=self._state.document_name,
            paused=self.paused,
            current_command_id=self._current_command_id,
            packages=packages,
            package_manifest_hash=canonical_package_manifest_hash(packages),
            capability_manifest=capability_manifest,
            capability_manifest_hash=(
                canonical_capability_manifest_hash(capability_manifest)
                if capability_manifest is not None
                else None
            ),
            **self._phase6_presence_fields(protocol_version),
            **proof_fields,
        )
        await self._send(websocket, hello)
        welcome = parse_agent_message(await websocket.recv())
        if not isinstance(welcome, WelcomeMessage):
            raise RuntimeError("Gateway did not send welcome")
        self._session_id = welcome.session_id
        self._last_ids["connection_stage"] = "presence"
        await self._refresh_presence(server_connected=True)
        self._last_ids["connection_stage"] = "online"
        self._last_ids.pop("safe_error_code", None)
        self._last_ids.pop("safe_error_type", None)
        queue: asyncio.Queue[CommandMessage | ProgramCommandMessage] = asyncio.Queue(
            maxsize=self.config.queue_size
        )
        receiver = asyncio.create_task(self._receive_loop(websocket, queue))
        worker = asyncio.create_task(self._worker(websocket, queue))
        heartbeat = asyncio.create_task(
            self._heartbeat_loop(websocket, welcome.heartbeat_interval_seconds)
        )
        stopper = asyncio.create_task(self._stop.wait())
        done, pending = await asyncio.wait(
            {receiver, worker, heartbeat, stopper}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            error = task.exception()
            if error:
                raise error

    async def _receive_loop(
        self,
        websocket: Any,
        queue: asyncio.Queue[CommandMessage | ProgramCommandMessage],
    ) -> None:
        async for raw in websocket:
            message = parse_agent_message(raw)
            if isinstance(message, (CommandMessage, ProgramCommandMessage)):
                if message.session_id != self._session_id or message.device_id != self.config.device_id:
                    raise RuntimeError("command binding mismatch")
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    await self._reject(websocket, message, "agent_busy")
            elif isinstance(message, ReconcileMessage):
                await self._handle_reconcile(websocket, message)
            elif isinstance(message, CancelMessage):
                await self._handle_cancel(websocket, message)
            elif isinstance(message, ErrorMessage):
                if message.code in {"package_mismatch", "capability_mismatch", "incompatible"}:
                    self._publish(
                        runtime_state=RuntimeState.INCOMPATIBLE,
                        server_connected=False,
                        support_code="C1-PKG-003",
                    )
                raise RuntimeError("Gateway rejected Agent compatibility")

    async def _worker(
        self,
        websocket: Any,
        queue: asyncio.Queue[CommandMessage | ProgramCommandMessage],
    ) -> None:
        while True:
            command = await queue.get()
            try:
                if isinstance(command, ProgramCommandMessage):
                    await self._handle_program_command(websocket, command)
                else:
                    await self._handle_command(websocket, command)
            finally:
                queue.task_done()

    async def _handle_command(self, websocket: Any, command: CommandMessage) -> None:
        if canonical_payload_hash(command.payload) != command.payload_hash:
            await self._reject(websocket, command, "payload_mismatch")
            return
        package = command.payload.get("package")
        if not isinstance(package, dict):
            await self._reject(websocket, command, "package_mismatch")
            return
        try:
            entry, created = self.ledger.record_received(
                command_id=command.command_id,
                job_id=command.job_id,
                idempotency_key=command.idempotency_key,
                payload_hash=command.payload_hash,
                package=package,
                session_id=command.session_id,
                device_id=command.device_id,
            )
        except LedgerConflict:
            await self._reject(websocket, command, "payload_mismatch")
            return
        if not created:
            if entry.state in TERMINAL:
                await self._ack(websocket, command, "already_terminal")
                await self._send_terminal(websocket, command, entry)
                return
            if entry.state == "started":
                await self._ack(websocket, command, "duplicate")
                return
        if self.paused:
            await self._reject(websocket, command, "paused_by_user")
            self.ledger.transition(command.command_id, "failed", error_code="paused_by_user")
            return
        try:
            self.executor.validate_command(command)
        except AgentExecutionError as error:
            await self._reject(websocket, command, error.code)
            self.ledger.transition(command.command_id, "failed", error_code=error.code)
            return
        self.ledger.transition(command.command_id, "accepted")
        await self._ack(websocket, command, "accepted")
        self.ledger.transition(command.command_id, "started")
        self._current_command_id = command.command_id
        self._last_ids.update(
            command_id=command.command_id,
            job_id=command.job_id,
            correlation_id=command.correlation_id,
        )
        self._publish(
            runtime_state=RuntimeState.BUSY_REMOTE,
            current_task="Đọc thông tin bản vẽ",
            active_job_id=command.job_id,
        )
        terminal_state = RuntimeState.READY
        try:
            result = await self.executor.execute(command)
        except AgentExecutionError as error:
            entry = self.ledger.transition(command.command_id, "failed", error_code=error.code)
            self._last_ids["safe_error_code"] = error.code
            terminal_state = {
                "autocad_not_running": RuntimeState.AUTOCAD_CLOSED,
                "no_active_document": RuntimeState.NO_DOCUMENT,
                "autocad_busy": RuntimeState.BUSY_USER,
                "modal_dialog_active": RuntimeState.MODAL,
                "package_mismatch": RuntimeState.INCOMPATIBLE,
                "dispatcher_not_loaded": RuntimeState.INCOMPATIBLE,
                "dispatcher_timeout": RuntimeState.INCOMPATIBLE,
            }.get(error.code, RuntimeState.INCOMPATIBLE)
        except Exception:
            entry = self.ledger.transition(command.command_id, "failed", error_code="backend_error")
            self._last_ids["safe_error_code"] = "backend_error"
            terminal_state = RuntimeState.INCOMPATIBLE
        else:
            entry = self.ledger.transition(command.command_id, "succeeded", result=result)
            document = result["snapshot"]["drawing"]["document_name"]
            self._publish(document_name=document, autocad_state="Đã kết nối")
        finally:
            self._current_command_id = None
            self._publish(
                runtime_state=RuntimeState.PAUSED if self.paused else terminal_state,
                current_task=None,
                active_job_id=None,
            )
        await self._send_terminal(websocket, command, entry)

    async def _handle_program_command(
        self,
        websocket: Any,
        command: ProgramCommandMessage,
    ) -> None:
        if self.program_executor is None:
            self._publish_program_rejection(command, "capability_missing")
            await self._reject(websocket, command, "capability_missing")
            return
        binding = command.binding.model_dump(mode="json")
        package = {
            "package_id": command.binding.package_id,
            "version": command.binding.package_version,
            "sha256": command.binding.package_hash.removeprefix("sha256:"),
        }
        try:
            entry, created = self.ledger.record_received(
                command_id=command.command_id,
                job_id=command.job_id,
                idempotency_key=command.idempotency_key,
                payload_hash=command.payload_hash,
                package=package,
                session_id=command.session_id,
                device_id=command.device_id,
                kind=command.kind,
                binding=binding,
            )
        except LedgerConflict as error:
            code = (
                "idempotency_conflict"
                if str(error) == "idempotency_conflict"
                else "payload_mismatch"
            )
            self._publish_program_rejection(command, code)
            await self._reject(websocket, command, code)
            return
        if not created:
            if entry.state in TERMINAL:
                await self._ack(websocket, command, "already_terminal")
                await self._send_program_terminal(websocket, command, entry)
                return
            if entry.state == "started":
                await self._ack(websocket, command, "duplicate")
                return
        if self.paused:
            entry = self.ledger.transition(
                command.command_id,
                "failed",
                error_code="paused_by_user",
            )
            self._publish_program_rejection(command, "paused_by_user")
            await self._reject(websocket, command, "paused_by_user")
            await self._send_program_terminal(websocket, command, entry)
            return
        if command.effect_class == "write" and not self.write_lock_enabled:
            entry = self.ledger.transition(
                command.command_id,
                "failed",
                error_code="write_lock_disabled",
            )
            self._publish_program_rejection(command, "write_lock_disabled")
            await self._reject(websocket, command, "write_lock_disabled")
            await self._send_program_terminal(websocket, command, entry)
            return
        try:
            self.program_executor.validate_command(command)
        except AgentExecutionError as error:
            entry = self.ledger.transition(
                command.command_id,
                "failed",
                error_code=error.code,
            )
            self._publish_program_rejection(command, error.code)
            await self._reject(websocket, command, error.code)
            await self._send_program_terminal(websocket, command, entry)
            return

        if entry.state == "received":
            entry = self.ledger.transition(command.command_id, "accepted")
        await self._ack(websocket, command, "accepted")
        self.ledger.transition(command.command_id, "started")
        self._current_command_id = command.command_id
        support_id = f"P6-{command.command_id[-12:]}"
        self._last_ids.update(
            command_id=command.command_id,
            job_id=command.job_id,
            correlation_id=command.correlation_id,
            execution_digest=command.binding.execution_digest,
            program_digest=command.binding.program_digest,
        )
        self._publish(
            runtime_state=RuntimeState.BUSY_REMOTE,
            current_task={
                "program_preview": "Đang xem trước CAD Program",
                "program_commit": "Đang tạo đối tượng trong bản vẽ",
                "program_validate": "Đang kiểm tra kết quả CAD Program",
            }[command.kind],
            active_job_id=command.job_id,
            mismatch_reason=None,
            outcome_unknown=False,
            support_id=support_id,
        )
        terminal_state = RuntimeState.READY
        try:
            result = await self.program_executor.execute(
                command,
                write_lock_enabled=self.write_lock_enabled,
            )
        except AgentExecutionError as error:
            uncertain = (
                command.kind == "program_commit"
                and error.code
                in {
                    "managed_host_unavailable",
                    "session_rejected",
                    "deadline_expired",
                    "outcome_unknown",
                }
            )
            if uncertain:
                entry = self.ledger.transition(
                    command.command_id,
                    "outcome_unknown",
                    error_code="outcome_unknown",
                )
                terminal_state = RuntimeState.OUTCOME_UNKNOWN
                self._publish(
                    mismatch_reason=error.code,
                    outcome_unknown=True,
                )
            else:
                entry = self.ledger.transition(
                    command.command_id,
                    "failed",
                    error_code=error.code,
                )
                terminal_state = self._program_error_state(error.code)
                self._publish(mismatch_reason=error.code)
            self._last_ids["safe_error_code"] = error.code
        except Exception:
            if command.kind == "program_commit":
                entry = self.ledger.transition(
                    command.command_id,
                    "outcome_unknown",
                    error_code="outcome_unknown",
                )
                terminal_state = RuntimeState.OUTCOME_UNKNOWN
                self._publish(
                    mismatch_reason="backend_error",
                    outcome_unknown=True,
                )
            else:
                entry = self.ledger.transition(
                    command.command_id,
                    "failed",
                    error_code="backend_error",
                )
                terminal_state = RuntimeState.INCOMPATIBLE
        else:
            entry = self.ledger.transition(
                command.command_id,
                "succeeded",
                result=result,
            )
            revision = (
                result.get("document_revision_after")
                if command.kind == "program_commit"
                else result.get("document_revision")
                if command.kind == "program_validate"
                else command.binding.document_revision
            )
            self._publish(
                active_document_id=command.binding.document_id,
                active_document_revision=(
                    str(revision) if revision is not None else None
                ),
            )
        finally:
            self._current_command_id = None
            self._publish(
                runtime_state=(
                    RuntimeState.PAUSED if self.paused else terminal_state
                ),
                current_task=None,
                active_job_id=None,
            )
        await self._send_program_terminal(websocket, command, entry)

    async def _handle_reconcile(self, websocket: Any, message: ReconcileMessage) -> None:
        if message.session_id != self._session_id or message.device_id != self.config.device_id:
            raise RuntimeError("reconcile binding mismatch")
        for descriptor in message.commands:
            try:
                status, entry = self.ledger.reconcile_status(
                    descriptor.command_id, descriptor.payload_hash
                )
            except LedgerConflict:
                status, entry = "started", None
            kwargs: dict[str, Any] = {}
            if status == "terminal" and entry is not None:
                kwargs["result_status"] = (
                    "failed"
                    if entry.state == "outcome_unknown"
                    else entry.state
                )
                if entry.kind in {
                    "program_preview",
                    "program_commit",
                    "program_validate",
                }:
                    kwargs["kind"] = entry.kind
                    kwargs["binding"] = entry.binding
                if entry.state == "succeeded":
                    kwargs["result"] = entry.result
                elif entry.state in {"failed", "outcome_unknown"}:
                    kwargs["error_code"] = entry.error_code or "backend_error"
                    kwargs["error_message"] = "Agent command failed"
            await self._send(
                websocket,
                ReconcileResultMessage(
                    protocol_version=self._protocol_version,
                    session_id=self._session_id,
                    device_id=self.config.device_id,
                    job_id=descriptor.job_id,
                    command_id=descriptor.command_id,
                    sequence=self.ledger.next_sequence(),
                    status=status,
                    payload_hash=descriptor.payload_hash,
                    **kwargs,
                ),
            )

    async def _handle_cancel(self, websocket: Any, message: CancelMessage) -> None:
        if message.session_id != self._session_id or message.device_id != self.config.device_id:
            raise RuntimeError("cancel binding mismatch")
        entry = self.ledger.get(message.command_id)
        if entry is None:
            return
        if entry.job_id != message.job_id or entry.device_id != message.device_id:
            raise RuntimeError("cancel ledger binding mismatch")
        entry = self.ledger.request_cancel(message.command_id)
        if entry is None or entry.state in TERMINAL:
            return
        if entry.state in {"received", "accepted"}:
            entry = self.ledger.transition(message.command_id, "cancelled")
            synthetic = CommandMessage(
                session_id=message.session_id,
                device_id=message.device_id,
                job_id=message.job_id,
                command_id=message.command_id,
                idempotency_key=entry.idempotency_key,
                payload_hash=entry.payload_hash,
                payload={},
            )
            await self._send_terminal(websocket, synthetic, entry)

    async def _heartbeat_loop(self, websocket: Any, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            if self._current_command_id is None:
                await self._refresh_presence(server_connected=True)
            await self._send(
                websocket,
                HeartbeatMessage(
                    protocol_version=self._protocol_version,
                    session_id=self._session_id,
                    device_id=self.config.device_id,
                    sequence=self.ledger.next_sequence(),
                    busy=self._current_command_id is not None,
                    last_processed_sequence=max(0, self.ledger.last_sequence() - 1),
                    current_job_id=(
                        self._state.active_job_id
                        if self._current_command_id
                        else None
                    ),
                    runtime_state=self._state.runtime_state.value,
                    document_name=self._state.document_name,
                    paused=self.paused,
                    current_command_id=self._current_command_id,
                    **self._phase6_presence_fields(self._protocol_version),
                ),
            )

    async def _refresh_presence(self, *, server_connected: bool) -> None:
        presence = await self.executor.probe()
        self._publish(
            runtime_state=(
                RuntimeState.PAUSED
                if self.paused
                else RuntimeState(presence.runtime_state)
            ),
            server_connected=server_connected,
            autocad_state=presence.autocad_state,
            document_name=presence.document_name,
            active_document_id=getattr(
                presence,
                "active_document_id",
                None,
            ),
            active_document_revision=getattr(
                presence,
                "active_document_revision",
                None,
            ),
            support_code=(
                {
                    "incompatible": "C1-PKG-002",
                    "plugin_required": "P5-HOST-001",
                    "host_not_loaded": "P5-HOST-002",
                    "runtime_version_mismatch": "P5-HOST-003",
                    "degraded_compatibility": "P5-RUNTIME-001",
                }.get(presence.runtime_state)
            ),
            product=getattr(presence, "product", None),
            edition=getattr(presence, "edition", None),
            release_year=getattr(presence, "release_year", None),
            series=getattr(presence, "series", None),
            runtime_id=getattr(presence, "runtime_id", None),
            runtime_role=getattr(presence, "runtime_role", None),
            host_family=getattr(presence, "host_family", None),
            host_version=getattr(presence, "host_version", None),
            host_package_version=getattr(presence, "host_package_version", None),
            host_package_hash=getattr(presence, "host_package_hash", None),
            host_handshake_state=getattr(presence, "host_handshake_state", None),
            degradation_reason=getattr(presence, "degradation_reason", None),
            capability_manifest_hash=getattr(
                presence, "capability_manifest_hash", None
            ),
            registry_version=getattr(presence, "registry_version", None),
        )

    async def _ack(self, websocket: Any, command: CommandMessage, status: str, reason: str | None = None) -> None:
        await self._send(
            websocket,
            AckMessage(
                protocol_version=self._protocol_version,
                session_id=self._session_id,
                device_id=self.config.device_id,
                job_id=command.job_id,
                command_id=command.command_id,
                sequence=self.ledger.next_sequence(),
                status=status,
                idempotency_key=command.idempotency_key,
                payload_hash=command.payload_hash,
                reason=reason,
            ),
        )

    async def _reject(self, websocket: Any, command: CommandMessage, code: str) -> None:
        await self._ack(websocket, command, "rejected", reason=code)

    async def _send_terminal(self, websocket: Any, command: CommandMessage, entry: Any) -> None:
        sequence = self.ledger.next_sequence()
        if entry.state == "succeeded":
            message = ResultMessage(
                protocol_version=self._protocol_version,
                session_id=self._session_id,
                device_id=self.config.device_id,
                job_id=command.job_id,
                command_id=command.command_id,
                sequence=sequence,
                status="succeeded",
                payload_hash=entry.payload_hash,
                result=entry.result,
            )
        elif entry.state == "cancelled":
            message = ResultMessage(
                protocol_version=self._protocol_version,
                session_id=self._session_id,
                device_id=self.config.device_id,
                job_id=command.job_id,
                command_id=command.command_id,
                sequence=sequence,
                status="cancelled",
                payload_hash=entry.payload_hash,
            )
        else:
            message = ResultMessage(
                protocol_version=self._protocol_version,
                session_id=self._session_id,
                device_id=self.config.device_id,
                job_id=command.job_id,
                command_id=command.command_id,
                sequence=sequence,
                status="failed",
                payload_hash=entry.payload_hash,
                error_code=entry.error_code or "backend_error",
                error_message="Agent command failed",
            )
        await self._send(websocket, message)

    async def _send_program_terminal(
        self,
        websocket: Any,
        command: ProgramCommandMessage,
        entry: Any,
    ) -> None:
        status = entry.state
        kwargs: dict[str, Any] = {}
        if status == "succeeded":
            kwargs["result"] = entry.result
        elif status == "failed":
            kwargs["error_code"] = entry.error_code or "backend_error"
            kwargs["error_message"] = "Agent CAD Program command failed"
        elif status == "outcome_unknown":
            kwargs["error_code"] = "outcome_unknown"
            kwargs["error_message"] = (
                "Commit may have started; inspect the drawing before any retry"
            )
        await self._send(
            websocket,
            ProgramResultMessage(
                protocol_version="cad.agent/2",
                session_id=self._session_id,
                device_id=self.config.device_id,
                job_id=command.job_id,
                command_id=command.command_id,
                sequence=self.ledger.next_sequence(),
                kind=command.kind,
                status=status,
                payload_hash=entry.payload_hash,
                binding=command.binding,
                **kwargs,
            ),
        )

    @staticmethod
    def _program_error_state(code: str) -> RuntimeState:
        return {
            "autocad_busy": RuntimeState.BUSY_USER,
            "modal_dialog_active": RuntimeState.MODAL,
            "no_active_document": RuntimeState.NO_DOCUMENT,
            "active_document_changed": RuntimeState.RUNTIME_CHANGED,
            "stale_snapshot": RuntimeState.RUNTIME_CHANGED,
            "runtime_mismatch": RuntimeState.RUNTIME_CHANGED,
            "runtime_changed": RuntimeState.RUNTIME_CHANGED,
            "package_mismatch": RuntimeState.VERSION_MISMATCH,
            "capability_mismatch": RuntimeState.CAPABILITY_MISSING,
            "capability_missing": RuntimeState.CAPABILITY_MISSING,
            "registry_mismatch": RuntimeState.CAPABILITY_MISSING,
            "policy_mismatch": RuntimeState.RUNTIME_CHANGED,
            "write_lock_disabled": RuntimeState.READY,
            "paused_by_user": RuntimeState.PAUSED,
        }.get(code, RuntimeState.INCOMPATIBLE)

    def _publish_program_rejection(
        self,
        command: ProgramCommandMessage,
        code: str,
    ) -> None:
        self._last_ids.update(
            command_id=command.command_id,
            job_id=command.job_id,
            safe_error_code=code,
        )
        self._publish(
            runtime_state=(
                RuntimeState.PAUSED
                if self.paused
                else self._program_error_state(code)
            ),
            mismatch_reason=code,
            support_id=f"P6-{command.command_id[-12:]}",
        )

    @staticmethod
    async def _send(websocket: Any, message: Any) -> None:
        await websocket.send(json.dumps(message_dict(message), ensure_ascii=False))

    def _publish(self, **changes: Any) -> None:
        values = dict(self._state.__dict__)
        values.update(changes)
        candidate = AgentViewState(**values)
        values["runtime_label"] = runtime_user_label(candidate)
        self._state = AgentViewState(**values)
        for callback in tuple(self._observers):
            with suppress(Exception):
                callback(self._state)

    def _phase6_presence_fields(
        self,
        protocol_version: str,
    ) -> dict[str, Any]:
        if protocol_version != "cad.agent/2":
            return {}
        fields: dict[str, Any] = {
            "write_lock_enabled": self.write_lock_enabled,
            "hard_pause": self.paused,
            "outcome_unknown": self._state.outcome_unknown,
        }
        if (
            self._state.active_document_id is not None
            and self._state.active_document_revision is not None
        ):
            fields.update(
                active_document_id=self._state.active_document_id,
                active_document_revision=self._state.active_document_revision,
            )
        if self._state.active_job_id is not None:
            fields["active_job_id"] = self._state.active_job_id
        if self._state.support_id is not None:
            fields["support_id"] = self._state.support_id
        if self._state.mismatch_reason is not None:
            fields["mismatch_reason"] = self._state.mismatch_reason
        return fields
