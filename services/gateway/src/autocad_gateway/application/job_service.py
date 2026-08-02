"""Job orchestration; all socket waits happen outside repository transactions."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING, Any

from autocad_contracts import (
    AckMessage,
    CancelMessage,
    CommandMessage,
    ProgressMessage,
    ProgramCommandMessage,
    ProgramResultMessage,
    RollbackCommandMessage,
    RollbackResultMessage,
    ReconcileCommandDescriptor,
    ReconcileMessage,
    ReconcileResultMessage,
    ResultMessage,
    RuntimeEvidence,
    program_command_payload_hash,
    rollback_command_payload_hash,
)

from ..domain.jobs import InvalidJobTransition, is_terminal
from ..infrastructure.agent_transport.connection_registry import AgentConnection, ConnectionRegistry
from ..infrastructure.sqlite.repositories import RepositoryConflict, SqliteRepository
from ..program_contract_adapter import program_command_fields


logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..phase7_recovery import Phase7RecoveryService


class DurableJobError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        job_id: str | None = None,
        job_state: str | None = None,
    ) -> None:
        self.code = code
        self.job_id = job_id
        self.job_state = job_state
        super().__init__(code)


class DurableJobService:
    def __init__(
        self,
        repository: SqliteRepository,
        registry: ConnectionRegistry,
        *,
        request_wait_timeout_seconds: float = 30,
        command_timeout_seconds: float | None = None,
        required_package: dict[str, str] | None = None,
        program_repository: Any | None = None,
        program_policy_version: str | None = None,
        managed_write_enabled: bool = False,
        allowed_write_device_ids: tuple[str, ...] = (),
        phase7_recovery_service: Phase7RecoveryService | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        if command_timeout_seconds is not None:
            request_wait_timeout_seconds = command_timeout_seconds
        self.request_wait_timeout_seconds = max(
            0.001, min(float(request_wait_timeout_seconds), 600.0)
        )
        self._waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._dispatch_lock = asyncio.Lock()
        self.required_package = dict(required_package or {})
        self.program_repository = program_repository
        self.program_policy_version = program_policy_version
        self.managed_write_enabled = managed_write_enabled
        self.allowed_write_device_ids = frozenset(allowed_write_device_ids)
        self.phase7_recovery_service = phase7_recovery_service

    async def wait_for_existing_job(
        self,
        job: dict[str, Any],
        *,
        owner_subject: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        if is_terminal(job["state"]):
            return job
        waiter = self._waiter_for(job["job_id"])
        try:
            await self.dispatch(job["job_id"], correlation_id=correlation_id)
        except DurableJobError as error:
            current = await self.repository.get_job(owner_subject, job["job_id"])
            raise DurableJobError(
                error.code,
                job_id=job["job_id"],
                job_state=current["state"] if current else job["state"],
            ) from None
        try:
            return await asyncio.wait_for(
                asyncio.shield(waiter),
                timeout=self.request_wait_timeout_seconds,
            )
        except asyncio.TimeoutError:
            current = await self.repository.get_job(owner_subject, job["job_id"])
            return current or job

    async def create_and_observe(
        self,
        *,
        owner_subject: str,
        device_id: str,
        payload: dict[str, Any],
        correlation_id: str,
        idempotency_key: str,
        deadline_at: str | None,
    ) -> dict[str, Any]:
        try:
            job = await self.repository.create_job(
                owner_subject=owner_subject,
                device_id=device_id,
                kind="observe",
                effect_class="read",
                payload=payload,
                idempotency_key=idempotency_key,
                deadline_at=deadline_at,
            )
        except RepositoryConflict as error:
            public_code = "idempotency_conflict" if error.code == "payload_mismatch" else error.code
            raise DurableJobError(public_code) from None
        if is_terminal(job["state"]):
            return job
        waiter = self._waiter_for(job["job_id"])
        try:
            await self.dispatch(job["job_id"], correlation_id=correlation_id)
        except DurableJobError as error:
            current = await self.repository.get_job(owner_subject, job["job_id"])
            raise DurableJobError(
                error.code,
                job_id=job["job_id"],
                job_state=current["state"] if current else job["state"],
            ) from None
        try:
            return await asyncio.wait_for(
                asyncio.shield(waiter),
                timeout=self.request_wait_timeout_seconds,
            )
        except asyncio.TimeoutError:
            current = await self.repository.get_job(owner_subject, job["job_id"])
            return current or job

    async def dispatch(self, job_id: str, *, correlation_id: str) -> bool:
        async with self._dispatch_lock:
            # The repository deliberately exposes no unscoped user-facing get_job, but
            # the dispatcher may use the internal command lookup after a job is claimed.
            raw = await self._get_internal_job(job_id)
            if raw is None or is_terminal(raw["state"]):
                return False
            if raw["state"] == "outcome_unknown":
                raise DurableJobError(
                    "outcome_unknown", job_id=job_id, job_state=raw["state"]
                )
            if raw["state"] == "needs_attention":
                return False
            if raw["state"] != "queued":
                return False
            await self._require_dispatch_capability(raw)
            claimed = await self.repository.claim_job(job_id)
            if claimed is None:
                return False
            raw = claimed
            connection = await self.registry.get(raw["device_id"])
            if connection is None or not await self.registry.is_current_and_fresh(
                connection
            ):
                updated = await self.repository.transition_job(job_id, "reconnect_pending")
                self._resolve(updated)
                raise DurableJobError(
                    "device_offline",
                    job_id=job_id,
                    job_state=updated["state"] if updated else raw["state"],
                )
            await self._require_dispatch_capability(raw, connection=connection)
            command_values = {
                "protocol_version": connection.protocol_version,
                "correlation_id": correlation_id,
                "session_id": connection.session_id,
                "device_id": raw["device_id"],
                "job_id": job_id,
                "command_id": raw["command_id"],
                "deadline_at": raw["deadline_at"],
                "idempotency_key": raw["idempotency_key"],
                "payload_hash": raw["payload_hash"],
                "kind": raw["kind"],
                "effect_class": raw["effect_class"],
                "payload": raw["payload"],
            }
            if raw["kind"] in {
                "program_preview",
                "program_commit",
                "program_validate",
            }:
                program_values = {
                    **{
                        key: value
                        for key, value in command_values.items()
                        if key != "payload"
                    },
                    **program_command_fields(
                        kind=raw["kind"],
                        effect_class=raw["effect_class"],
                        payload=raw["payload"],
                    ),
                }
                command = ProgramCommandMessage(**program_values)
                if program_command_payload_hash(command) != raw["payload_hash"]:
                    updated = await self.repository.transition_job(
                        job_id,
                        "failed",
                        error_code="payload_mismatch",
                        error_summary="Typed Program command hash did not match durable job",
                    )
                    await self._release_program_lock_if_terminal(updated)
                    self._resolve(updated)
                    raise DurableJobError(
                        "payload_mismatch",
                        job_id=job_id,
                        job_state=updated["state"] if updated else raw["state"],
                    )
            elif raw["kind"] in {
                "receipt_lookup",
                "checkpoint_lookup",
                "rollback_preview",
                "rollback_commit",
                "rollback_validate",
            }:
                rollback_values = {
                    key: value
                    for key, value in command_values.items()
                    if key not in {"payload", "kind", "effect_class"}
                }
                rollback_values.update(raw["payload"])
                command = RollbackCommandMessage(**rollback_values)
                if rollback_command_payload_hash(command) != raw["payload_hash"]:
                    updated = await self.repository.transition_job(
                        job_id,
                        "failed",
                        error_code="payload_mismatch",
                        error_summary="Typed rollback command hash did not match durable job",
                    )
                    self._resolve(updated)
                    raise DurableJobError(
                        "payload_mismatch",
                        job_id=job_id,
                        job_state=updated["state"] if updated else raw["state"],
                    )
            else:
                if (
                    raw["kind"] == "observe"
                    and raw["payload"].get("observation_level") == "detail"
                    and "cad.observe.detail-provenance/1"
                    in connection.capabilities
                ):
                    command_values["detail_snapshot_contract"] = (
                        "cad.observe-detail/2"
                    )
                command = CommandMessage(**command_values)
            try:
                await connection.send(command.model_dump(mode="json", exclude_none=True))
            except Exception as error:
                await self.handle_disconnect(raw["device_id"])
                current = await self._get_internal_job(job_id)
                raise DurableJobError(
                    "device_offline",
                    job_id=job_id,
                    job_state=current["state"] if current else raw["state"],
                ) from error
            return True

    async def sweep_deadlines(self) -> None:
        now = datetime.now(timezone.utc)
        for job in await self.repository.all_nonterminal_jobs():
            deadline = job.get("deadline_at")
            if not deadline:
                continue
            try:
                expired = datetime.fromisoformat(str(deadline).replace("Z", "+00:00")) <= now
            except ValueError:
                expired = True
            if expired:
                if (
                    job["kind"] == "program_commit"
                    and job["state"] == "outcome_unknown"
                ):
                    if self.phase7_recovery_service is not None:
                        await self.phase7_recovery_service.ensure_recovery_case(
                            owner_subject=job["owner_subject"],
                            job=job,
                            cause="deadline_outcome_unknown",
                            latest_query_result={
                                "outcome": "inconclusive",
                                "source": "gateway",
                                "summary": "Commit deadline passed without exact receipt proof",
                                "queried_at": now.isoformat(),
                            },
                        )
                    logger.warning(
                        "Expired Program commit remains recoverable pending receipt reconciliation",
                        extra={"job_id": job["job_id"], "state": job["state"]},
                    )
                    continue
                target = "needs_attention" if job["state"] == "outcome_unknown" else "failed"
                try:
                    updated = await self.repository.transition_job(
                        job["job_id"],
                        target,
                        evidence=job["state"] == "reconnect_pending",
                        error_code="deadline_expired",
                        error_summary=(
                            "Job deadline expired with an unknown Agent outcome"
                            if target == "needs_attention"
                            else "Job deadline expired before completion"
                        ),
                    )
                    self._resolve(updated)
                except (InvalidJobTransition, RepositoryConflict):
                    logger.info(
                        "Deadline lost a terminal-state race",
                        extra={"job_id": job["job_id"], "state": job["state"]},
                    )

    async def handle_message(self, connection: AgentConnection, message: Any) -> None:
        job = await self.repository.get_job_by_command(connection.device_id, message.command_id)
        if job is None:
            raise DurableJobError("invalid_message")
        self._validate_message_binding(connection, job, message)
        message_payload_hash = getattr(message, "payload_hash", None)
        if (
            message_payload_hash is not None
            and message_payload_hash != job["payload_hash"]
        ):
            logger.warning(
                "Agent payload hash mismatch rejected before message handling",
                extra={"job_id": job["job_id"], "state": job["state"]},
            )
            await self._fail_payload(job)
            return
        if isinstance(message, AckMessage):
            await self._handle_ack(connection, job, message)
        elif isinstance(message, ProgressMessage):
            await self._handle_progress(job, message)
        elif isinstance(
            message, (ResultMessage, ProgramResultMessage, RollbackResultMessage)
        ):
            await self._handle_result(job, message)
        elif isinstance(message, ReconcileResultMessage):
            await self.handle_reconcile_result(connection, message, job=job)

    async def validate_message(self, connection: AgentConnection, message: Any) -> bool:
        job = await self.repository.get_job_by_command(
            connection.device_id, message.command_id
        )
        if job is None:
            return False
        try:
            self._validate_message_binding(connection, job, message)
        except DurableJobError:
            return False
        return True

    async def handle_disconnect(self, device_id: str) -> None:
        for job in await self.repository.jobs_for_device(device_id):
            if job["state"] not in {"dispatched", "acknowledged", "running", "cancel_requested"}:
                continue
            if job["effect_class"] == "write" and job["state"] in {"acknowledged", "running", "cancel_requested"}:
                target = "outcome_unknown"
            else:
                target = "reconnect_pending"
            try:
                updated = await self.repository.transition_job(job["job_id"], target)
                self._resolve(updated)
                if (
                    target == "outcome_unknown"
                    and updated is not None
                    and self.phase7_recovery_service is not None
                ):
                    await self.phase7_recovery_service.ensure_recovery_case(
                        owner_subject=updated["owner_subject"],
                        job=updated,
                        cause="commit_outcome_unknown",
                    )
            except (InvalidJobTransition, RepositoryConflict):
                logger.info(
                    "Disconnect recovery lost a state race",
                    extra={"job_id": job["job_id"], "state": job["state"]},
                )

    async def handle_connected(self, connection: AgentConnection) -> None:
        jobs = await self.repository.jobs_for_device(connection.device_id)
        normalized_jobs: list[dict[str, Any]] = []
        for job in jobs:
            if job["state"] != "cancel_requested":
                normalized_jobs.append(job)
                continue
            target = (
                "outcome_unknown"
                if job["effect_class"] == "write"
                else "reconnect_pending"
            )
            try:
                normalized_jobs.append(
                    await self.repository.transition_job(job["job_id"], target)
                    or job
                )
            except (InvalidJobTransition, RepositoryConflict):
                current = await self._get_internal_job(job["job_id"])
                if current is not None:
                    normalized_jobs.append(current)
        jobs = normalized_jobs
        recovery_jobs = [
            job
            for job in jobs
            if job["state"] in {"reconnect_pending", "outcome_unknown"}
        ]
        for offset in range(0, len(recovery_jobs), 64):
            await self._send_reconcile(connection, recovery_jobs[offset : offset + 64])
        for job in jobs:
            if job["state"] == "queued":
                try:
                    await self.dispatch(job["job_id"], correlation_id=job["job_id"])
                except DurableJobError as error:
                    if error.code not in {"device_offline", "capability_missing"}:
                        raise

    async def handle_reconcile_result(
        self,
        connection: AgentConnection,
        message: ReconcileResultMessage,
        *,
        job: dict[str, Any] | None = None,
        _attempt: int = 0,
    ) -> None:
        # Refresh even when the transport supplied a previously validated row: a
        # concurrent cancel/result may have changed state or durable cancel intent.
        latest = await self.repository.get_job_by_command(
            connection.device_id, message.command_id
        )
        job = latest or job
        if job is None:
            raise DurableJobError("invalid_message")
        self._validate_message_binding(connection, job, message)
        if message.payload_hash != job["payload_hash"]:
            await self._fail_payload(job)
            return
        if message.status == "terminal" and message.result_status:
            program_kinds = {
                "program_preview",
                "program_commit",
                "program_validate",
            }
            rollback_kinds = {
                "receipt_lookup",
                "checkpoint_lookup",
                "rollback_preview",
                "rollback_commit",
                "rollback_validate",
            }
            binding_mismatch = False
            if job["kind"] in program_kinds:
                binding_mismatch = (
                    message.kind != job["kind"]
                    or message.binding is None
                    or message.binding.model_dump(mode="json")
                    != self._program_result_binding(job)
                )
            elif job["kind"] in rollback_kinds:
                payload = job.get("payload")
                binding_mismatch = (
                    not isinstance(payload, dict)
                    or message.kind != job["kind"]
                    or message.binding is None
                    or message.binding.model_dump(mode="json")
                    != payload.get("binding")
                )
            if binding_mismatch:
                logger.warning(
                    "Typed reconciliation did not prove its durable execution binding",
                    extra={"job_id": job["job_id"], "state": job["state"]},
                )
                if self.phase7_recovery_service is not None:
                    await self.phase7_recovery_service.record_reconcile_outcome(
                        owner_subject=job["owner_subject"],
                        job_id=job["job_id"],
                        outcome="conflict",
                        source="agent",
                        summary="Agent reconciliation binding conflicts with the durable job",
                        attempt=_attempt + 1,
                    )
                return
            if (
                job["kind"] in {"program_commit", "rollback_commit"}
                and job["state"] == "outcome_unknown"
                and message.result_status == "failed"
                and message.error_code == "outcome_unknown"
            ):
                logger.warning(
                    "Agent reconciliation still reports an unknown write outcome",
                    extra={"job_id": job["job_id"], "state": job["state"]},
                )
                if self.phase7_recovery_service is not None:
                    await self.phase7_recovery_service.record_reconcile_outcome(
                        owner_subject=job["owner_subject"],
                        job_id=job["job_id"],
                        outcome="inconclusive",
                        source="agent",
                        summary="Agent ledger cannot prove the exact write outcome",
                        attempt=_attempt + 1,
                    )
                return
            message_type = (
                ProgramResultMessage
                if job["kind"] in program_kinds
                else RollbackResultMessage
                if job["kind"] in rollback_kinds
                else ResultMessage
            )
            result_values = {
                "session_id": connection.session_id,
                "device_id": connection.device_id,
                "job_id": job["job_id"],
                "command_id": job["command_id"],
                "sequence": message.sequence,
                "payload_hash": message.payload_hash,
                "status": message.result_status,
                "result": message.result,
                "error_code": message.error_code,
                "error_message": message.error_message,
            }
            if message_type in {ProgramResultMessage, RollbackResultMessage}:
                result_values.update(
                    kind=message.kind,
                    binding=message.binding,
                )
            result = message_type(**result_values)
            await self._handle_result(job, result)
            return
        if message.status == "terminal":
            raise DurableJobError(
                "invalid_message", job_id=job["job_id"], job_state=job["state"]
            )
        if job["state"] == "outcome_unknown":
            if message.status == "started":
                if job.get("cancel_requested_at"):
                    await self._send_cancel(
                        connection,
                        job,
                        reason="Durable cancellation requested before reconnect",
                    )
                logger.info(
                    "Started outcome remains unknown and will not be retried",
                    extra={
                        "job_id": job["job_id"],
                        "cancel_requested": bool(job.get("cancel_requested_at")),
                    },
                )
                if self.phase7_recovery_service is not None:
                    await self.phase7_recovery_service.record_reconcile_outcome(
                        owner_subject=job["owner_subject"],
                        job_id=job["job_id"],
                        outcome="inconclusive",
                        source="agent",
                        summary="Agent reports started without exact terminal evidence",
                        attempt=_attempt + 1,
                    )
                return
            if message.status == "not_started" and job.get("cancel_requested_at"):
                try:
                    updated = await self.repository.transition_job(
                        job["job_id"],
                        "needs_attention",
                        expected_version=job["state_version"],
                    )
                except RepositoryConflict as error:
                    if error.code == "cas_conflict" and _attempt < 2:
                        return await self.handle_reconcile_result(
                            connection, message, _attempt=_attempt + 1
                        )
                    raise
                self._resolve(updated)
                if updated is not None and self.phase7_recovery_service is not None:
                    await self.phase7_recovery_service.ensure_recovery_case(
                        owner_subject=updated["owner_subject"],
                        job=updated,
                        cause="bounded_inconclusive",
                    )
                logger.info(
                    "Unknown write-like outcome with prior cancel intent was not retried",
                    extra={"job_id": job["job_id"], "state": "needs_attention"},
                )
                return
            try:
                updated = await self.repository.transition_job(
                    job["job_id"],
                    "needs_attention",
                    expected_version=job["state_version"],
                    evidence=True,
                )
                self._resolve(updated)
                if updated is not None and self.phase7_recovery_service is not None:
                    await self.phase7_recovery_service.ensure_recovery_case(
                        owner_subject=updated["owner_subject"],
                        job=updated,
                        cause="bounded_inconclusive",
                    )
            except RepositoryConflict as error:
                if error.code == "cas_conflict" and _attempt < 2:
                    return await self.handle_reconcile_result(
                        connection, message, _attempt=_attempt + 1
                    )
                logger.info(
                    "Unknown-outcome reconciliation lost a state race",
                    extra={"job_id": job["job_id"]},
                )
            except InvalidJobTransition:
                logger.info(
                    "Unknown-outcome reconciliation lost a state race",
                    extra={"job_id": job["job_id"]},
                )
            return

        if message.status == "not_started" and job["state"] == "reconnect_pending":
            if job.get("cancel_requested_at"):
                try:
                    updated = await self.repository.transition_job(
                        job["job_id"],
                        "cancelled",
                        expected_version=job["state_version"],
                        evidence=True,
                    )
                except RepositoryConflict as error:
                    if error.code == "cas_conflict" and _attempt < 2:
                        return await self.handle_reconcile_result(
                            connection, message, _attempt=_attempt + 1
                        )
                    raise
                self._resolve(updated)
                logger.info(
                    "Reconciled not-started job honoured durable cancel intent",
                    extra={"job_id": job["job_id"], "state": "cancelled"},
                )
                return
            try:
                updated = await self.repository.transition_job(
                    job["job_id"],
                    "queued",
                    expected_version=job["state_version"],
                    evidence=True,
                )
                await self.dispatch(updated["job_id"], correlation_id=updated["job_id"])
            except DurableJobError as error:
                if error.code not in {"device_offline", "capability_missing"}:
                    raise
            except RepositoryConflict as error:
                if error.code == "cas_conflict" and _attempt < 2:
                    return await self.handle_reconcile_result(
                        connection, message, _attempt=_attempt + 1
                    )
                raise
            return
        if message.status == "started" and job["state"] == "reconnect_pending":
            target = "outcome_unknown" if job["effect_class"] == "write" else "running"
            try:
                updated = await self.repository.transition_job(
                    job["job_id"],
                    target,
                    expected_version=job["state_version"],
                    evidence=True,
                )
            except RepositoryConflict as error:
                if error.code == "cas_conflict" and _attempt < 2:
                    return await self.handle_reconcile_result(
                        connection, message, _attempt=_attempt + 1
                    )
                raise
            if job.get("cancel_requested_at"):
                if target == "running":
                    updated = await self.repository.transition_job(
                        job["job_id"], "cancel_requested"
                    )
                await self._send_cancel(
                    connection,
                    updated or job,
                    reason="Durable cancellation requested before reconnect",
                )
            self._resolve(updated)
            logger.info(
                "Reconnected command is already started and was not redispatched",
                extra={"job_id": job["job_id"], "state": target},
            )
            if (
                target == "outcome_unknown"
                and updated is not None
                and self.phase7_recovery_service is not None
            ):
                await self.phase7_recovery_service.record_reconcile_outcome(
                    owner_subject=updated["owner_subject"],
                    job_id=updated["job_id"],
                    outcome="inconclusive",
                    source="agent",
                    summary="Agent reports the write started without terminal evidence",
                    attempt=_attempt + 1,
                )
            return
        raise DurableJobError(
            "invalid_message", job_id=job["job_id"], job_state=job["state"]
        )

    async def record_phase7_evidence(
        self, value: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        if self.phase7_recovery_service is None:
            raise DurableJobError("phase7_recovery_disabled")
        return await self.phase7_recovery_service.append_evidence(value)

    async def cancel(self, job_id: str, *, owner_subject: str, reason: str) -> dict[str, Any]:
        job = await self.repository.get_job(owner_subject, job_id)
        if job is None:
            raise DurableJobError("not_found")
        updated: dict[str, Any] | None = None
        for _ in range(3):
            try:
                updated = await self.repository.request_job_cancel(
                    job_id,
                    expected_version=job["state_version"],
                )
                break
            except RepositoryConflict as error:
                if error.code != "cas_conflict":
                    raise DurableJobError(
                        "invalid_state",
                        job_id=job_id,
                        job_state=job["state"],
                    ) from error
                current = await self.repository.get_job(owner_subject, job_id)
                if current is None:
                    raise DurableJobError("not_found") from None
                if is_terminal(current["state"]):
                    return current
                job = current
        if updated is None:
            raise DurableJobError(
                "invalid_state", job_id=job_id, job_state=job["state"]
            )
        if is_terminal(updated["state"]):
            return updated
        connection = await self.registry.get(job["device_id"])
        if connection and await self.registry.is_current_and_fresh(connection):
            await self._send_cancel(connection, updated, reason=reason)
        return updated

    async def _handle_ack(
        self,
        connection: AgentConnection,
        job: dict[str, Any],
        message: AckMessage,
    ) -> None:
        if message.payload_hash != job["payload_hash"]:
            await self._fail_payload(job)
            return
        if message.idempotency_key != job["idempotency_key"]:
            await self._fail_payload(job)
            return
        if message.status == "accepted":
            if job["state"] == "dispatched":
                updated = await self.repository.transition_job(job["job_id"], "acknowledged")
                self._resolve(updated)
                return
            if job["state"] in {
                "acknowledged",
                "running",
                "cancel_requested",
                "succeeded",
                "failed",
                "cancelled",
                "needs_attention",
            }:
                logger.info(
                    "Duplicate accepted ACK left job state unchanged",
                    extra={"job_id": job["job_id"], "state": job["state"]},
                )
                return
            raise DurableJobError(
                "invalid_message", job_id=job["job_id"], job_state=job["state"]
            )
        if message.status == "rejected":
            if is_terminal(job["state"]):
                logger.info(
                    "Rejected ACK arrived after terminal outcome",
                    extra={"job_id": job["job_id"], "state": job["state"]},
                )
                return
            try:
                rejection_code, rejection_summary = self._safe_agent_error(
                    message.reason or "agent_rejected"
                )
                updated = await self.repository.transition_job(
                    job["job_id"],
                    "failed",
                    error_code=rejection_code,
                    error_summary=rejection_summary,
                    evidence=job["state"] == "outcome_unknown",
                )
            except (InvalidJobTransition, RepositoryConflict) as error:
                raise DurableJobError(
                    "invalid_message", job_id=job["job_id"], job_state=job["state"]
                ) from error
            self._resolve(updated)
            return
        if message.status in {"duplicate", "already_terminal"}:
            if is_terminal(job["state"]):
                logger.info(
                    "Ledger ACK confirmed an already terminal Gateway job",
                    extra={"job_id": job["job_id"], "status": message.status},
                )
                return
            if message.status == "duplicate" and job["state"] in {
                "acknowledged",
                "running",
                "cancel_requested",
            }:
                logger.info(
                    "Duplicate ledger ACK left an already-started job unchanged",
                    extra={"job_id": job["job_id"], "state": job["state"]},
                )
                return
            if job["state"] == "dispatched":
                job = await self.repository.transition_job(
                    job["job_id"], "reconnect_pending"
                ) or job
            elif message.status == "already_terminal" and job["state"] in {
                "acknowledged",
                "running",
                "cancel_requested",
            }:
                target = (
                    "outcome_unknown"
                    if job["effect_class"] == "write"
                    else "reconnect_pending"
                )
                job = await self.repository.transition_job(job["job_id"], target) or job
            await self._send_reconcile(connection, [job])
            return
        raise DurableJobError(
            "invalid_message", job_id=job["job_id"], job_state=job["state"]
        )

    async def _handle_progress(self, job: dict[str, Any], message: ProgressMessage) -> None:
        if is_terminal(job["state"]):
            logger.info(
                "Late progress ignored for terminal job",
                extra={"job_id": job["job_id"], "state": job["state"]},
            )
            return
        if job["state"] == "acknowledged":
            job = await self.repository.transition_job(job["job_id"], "running") or job
        elif job["state"] not in {"running", "cancel_requested"}:
            raise DurableJobError(
                "invalid_message", job_id=job["job_id"], job_state=job["state"]
            )
        try:
            updated = await self.repository.append_progress(
                job["job_id"], phase=message.phase, percent=message.percent, message=message.message, sequence=message.sequence
            )
        except RepositoryConflict as error:
            logger.warning(
                "Progress event rejected",
                extra={"job_id": job["job_id"], "error_code": error.code},
            )
            raise DurableJobError(
                error.code, job_id=job["job_id"], job_state=job["state"]
            ) from None
        if updated:
            self._resolve(updated)

    async def _handle_result(
        self,
        job: dict[str, Any],
        message: ResultMessage | ProgramResultMessage | RollbackResultMessage,
    ) -> None:
        if message.payload_hash != job["payload_hash"]:
            await self._fail_payload(job)
            return
        target = message.status
        result = message.result
        if isinstance(message, ProgramResultMessage):
            self._validate_program_result_binding(job, message)
            if target == "outcome_unknown":
                try:
                    updated = await self.repository.transition_job(
                        job["job_id"],
                        "outcome_unknown",
                        error_code="outcome_unknown",
                        error_summary="Agent reported an unknown commit outcome",
                    )
                except (InvalidJobTransition, RepositoryConflict) as error:
                    raise DurableJobError(
                        "outcome_unknown",
                        job_id=job["job_id"],
                        job_state=job["state"],
                    ) from error
                self._resolve(updated)
                if updated is not None and self.phase7_recovery_service is not None:
                    await self.phase7_recovery_service.ensure_recovery_case(
                        owner_subject=updated["owner_subject"],
                        job=updated,
                        cause="commit_outcome_unknown",
                    )
                return
            result = self._normalize_program_result(job, message)
        elif isinstance(message, RollbackResultMessage):
            self._validate_rollback_result_binding(job, message)
            if target == "outcome_unknown":
                try:
                    updated = await self.repository.transition_job(
                        job["job_id"],
                        "outcome_unknown",
                        error_code="outcome_unknown",
                        error_summary="Agent reported an unknown rollback outcome",
                    )
                except (InvalidJobTransition, RepositoryConflict) as error:
                    raise DurableJobError(
                        "outcome_unknown",
                        job_id=job["job_id"],
                        job_state=job["state"],
                    ) from error
                self._resolve(updated)
                if updated is not None and self.phase7_recovery_service is not None:
                    await self.phase7_recovery_service.ensure_recovery_case(
                        owner_subject=updated["owner_subject"],
                        job=updated,
                        cause="commit_outcome_unknown",
                    )
                return
            result = (
                message.result.model_dump(mode="json")
                if hasattr(message.result, "model_dump")
                else message.result
            )
        error_code: str | None = None
        error_summary: str | None = None
        snapshot: dict[str, Any] | None = None
        if target == "succeeded" and job["kind"] == "observe":
            candidate = result.get("snapshot") if isinstance(result, dict) else None
            if not isinstance(candidate, dict):
                target = "failed"
                result = None
                error_code = "backend_error"
                error_summary = "Agent returned an invalid observation result"
            else:
                validation_error = self._validate_c1_observation(
                    result,
                    candidate,
                    expected_package=job.get("payload", {}).get("package"),
                )
                if validation_error is not None:
                    target = "failed"
                    result = None
                    error_code = validation_error
                    error_summary = "Agent returned invalid C1 observation evidence"
                else:
                    snapshot = self._normalize_c1_snapshot(candidate)
                    if snapshot is not candidate:
                        result = {**result, "snapshot": snapshot}
        elif target == "failed":
            result = None
            error_code, error_summary = self._safe_agent_error(message.error_code)
        elif target == "cancelled":
            result = None
            error_code = "cancelled"
            error_summary = "Agent confirmed cancellation"
        if job["kind"] in {
            "program_preview",
            "program_commit",
            "program_validate",
        }:
            if self.program_repository is None:
                raise DurableJobError(
                    "backend_error", job_id=job["job_id"], job_state=job["state"]
                )
            try:
                terminal_hook = None
                if self.phase7_recovery_service is not None:
                    terminal_evidence = (
                        await self.phase7_recovery_service.prepare_terminal_evidence(
                            job=job,
                            target=target,
                            result=result if isinstance(result, dict) else None,
                        )
                    )

                    def terminal_hook(conn, _row):
                        self.phase7_recovery_service.phase7.insert_evidence(
                            conn, terminal_evidence
                        )

                updated = await self.program_repository.finalize_program_job(
                    job_id=job["job_id"],
                    device_id=job["device_id"],
                    command_id=job["command_id"],
                    payload_hash=job["payload_hash"],
                    target=target,
                    result=result,
                    error_code=error_code,
                    error_summary=error_summary,
                    session_id=message.session_id,
                    agent_sequence=message.sequence,
                    terminal_hook=terminal_hook,
                )
            except RepositoryConflict as error:
                if error.code not in {
                    "program_result_invalid",
                    "binding_mismatch",
                    "payload_too_large",
                }:
                    raise
                release_write_lock = True
                latest = await self._get_internal_job(job["job_id"])
                if (
                    job["kind"] == "program_commit"
                    and latest is not None
                    and latest["state"] in {
                        "acknowledged",
                        "running",
                        "cancel_requested",
                        "outcome_unknown",
                    }
                ):
                    updated = latest
                    if latest["state"] != "outcome_unknown":
                        updated = await self.repository.transition_job(
                            job["job_id"],
                            "outcome_unknown",
                            error_code="binding_mismatch",
                            error_summary=(
                                "Commit result did not prove its exact execution binding"
                            ),
                        )
                    else:
                        logger.warning(
                            "Reconciled commit evidence still did not prove its binding",
                            extra={"job_id": job["job_id"]},
                        )
                    release_write_lock = False
                    if (
                        updated is not None
                        and self.phase7_recovery_service is not None
                    ):
                        await self.phase7_recovery_service.record_reconcile_outcome(
                            owner_subject=updated["owner_subject"],
                            job_id=updated["job_id"],
                            outcome="conflict",
                            source="agent",
                            summary=(
                                "Program result conflicts with the exact execution binding"
                            ),
                        )
                else:
                    updated = await self.repository.transition_job(
                        job["job_id"],
                        "failed",
                        error_code=error.code,
                        error_summary="Agent returned invalid bounded CAD Program evidence",
                    )
                if release_write_lock:
                    await self.program_repository.release_write_lock(job["job_id"])
            if updated:
                self._resolve(updated)
            return
        if job["kind"] in {
            "receipt_lookup",
            "checkpoint_lookup",
            "rollback_preview",
            "rollback_commit",
            "rollback_validate",
        }:
            try:
                terminal_hook = None
                terminal_evidence = None
                if self.phase7_recovery_service is not None:
                    terminal_evidence = (
                        await self.phase7_recovery_service.prepare_terminal_evidence(
                            job=job,
                            target=target,
                            result=result if isinstance(result, dict) else None,
                        )
                    )
                receipt = None
                if (
                    target == "succeeded"
                    and job["kind"] == "rollback_commit"
                    and self.phase7_recovery_service is not None
                    and isinstance(result, dict)
                ):
                    receipt = (
                        await self.phase7_recovery_service.prepare_rollback_receipt(
                            owner_subject=job["owner_subject"],
                            job=job,
                            result=result,
                        )
                    )
                if terminal_evidence is not None or job["kind"] == "rollback_commit":

                    def terminal_hook(conn, _row):
                        if terminal_evidence is not None:
                            self.phase7_recovery_service.phase7.insert_evidence(
                                conn, terminal_evidence
                            )
                        if receipt is not None:
                            self.phase7_recovery_service.phase7.insert_rollback_receipt(
                                conn, receipt
                            )
                        if job["kind"] == "rollback_commit":
                            conn.execute(
                                "DELETE FROM cad_program_write_locks WHERE job_id = ?",
                                (job["job_id"],),
                            )
                updated = await self.repository.finalize_job_result(
                    job_id=job["job_id"],
                    device_id=job["device_id"],
                    command_id=job["command_id"],
                    payload_hash=job["payload_hash"],
                    target=target,
                    result=result,
                    error_code=error_code,
                    error_summary=error_summary,
                    session_id=message.session_id,
                    agent_sequence=message.sequence,
                    evidence=True,
                    terminal_hook=terminal_hook,
                )
            except RepositoryConflict as error:
                raise DurableJobError(
                    error.code, job_id=job["job_id"], job_state=job["state"]
                ) from None
            if updated:
                self._resolve(updated)
            return
        try:
            updated = await self.repository.finalize_job_result(
                job_id=job["job_id"],
                device_id=job["device_id"],
                command_id=job["command_id"],
                payload_hash=job["payload_hash"],
                target=target,
                result=result,
                error_code=error_code,
                error_summary=error_summary,
                snapshot=snapshot,
                session_id=message.session_id,
                agent_sequence=message.sequence,
                evidence=True,
            )
        except RepositoryConflict as error:
            if error.code == "terminal_result_conflict":
                logger.warning(
                    "Conflicting duplicate terminal result rejected",
                    extra={"job_id": job["job_id"]},
                )
                raise DurableJobError(
                    error.code, job_id=job["job_id"], job_state=job["state"]
                ) from None
            raise
        except InvalidJobTransition as error:
            raise DurableJobError(
                "invalid_message", job_id=job["job_id"], job_state=job["state"]
            ) from error
        if updated:
            self._resolve(updated)

    @staticmethod
    def _program_result_binding(job: dict[str, Any]) -> dict[str, Any]:
        payload = job["payload"]
        phase8_binding = payload.get("binding")
        if (
            isinstance(phase8_binding, dict)
            and phase8_binding.get("schema_version")
            == "cad.execution-binding/1"
        ):
            return phase8_binding
        execution = job["payload"]["execution"]
        pins = execution["pins"]
        return {
            "program_digest": execution["program_digest"],
            "execution_digest": execution["execution_digest"],
            "document_id": execution["document_id"],
            "document_revision": execution["expected_document_revision"],
            "runtime_id": pins["runtime_id"],
            "runtime_role": pins["runtime_role"],
            "host_family": pins["host_family"],
            "host_version": pins["host_version"],
            "package_id": pins["package_id"],
            "package_version": pins["package_version"],
            "package_hash": pins["package_hash"],
            "capability_manifest_hash": pins["capability_manifest_hash"],
            "operation_registry_version": pins["registry_version"],
            "operation_registry_hash": pins["operation_registry_hash"],
            "policy_version": pins["policy_version"],
        }

    @staticmethod
    def _validate_program_result_binding(
        job: dict[str, Any], message: ProgramResultMessage
    ) -> None:
        if message.kind != job["kind"]:
            raise DurableJobError(
                "binding_mismatch",
                job_id=job["job_id"],
                job_state=job["state"],
            )
        expected = DurableJobService._program_result_binding(job)
        if message.binding.model_dump(mode="json") != expected:
            raise DurableJobError(
                "binding_mismatch",
                job_id=job["job_id"],
                job_state=job["state"],
            )

    @staticmethod
    def _validate_rollback_result_binding(
        job: dict[str, Any], message: RollbackResultMessage
    ) -> None:
        payload = job.get("payload")
        if (
            not isinstance(payload, dict)
            or message.kind != job["kind"]
            or message.binding.model_dump(mode="json") != payload.get("binding")
        ):
            raise DurableJobError(
                "binding_mismatch",
                job_id=job["job_id"],
                job_state=job["state"],
            )

    @staticmethod
    def _normalize_program_result(
        job: dict[str, Any], message: ProgramResultMessage
    ) -> dict[str, Any] | None:
        if message.result is None:
            return None
        value = (
            message.result
            if isinstance(message.result, dict)
            else message.result.model_dump(mode="json")
        )
        binding = job["payload"].get("binding")
        if (
            isinstance(binding, dict)
            and binding.get("schema_version")
            == "cad.execution-binding/1"
        ):
            plan = job["payload"]["execution_plan"]
            expected = {
                "execution_plan_digest": binding["execution_plan_digest"],
                "effect_manifest_digest": binding["effect_manifest_digest"],
                "target_refs_digest": binding["target_refs_digest"],
                "hard_budgets_digest": binding["hard_budgets_digest"],
                "rollout_policy_digest": plan["execution_pins"][
                    "rollout_policy_digest"
                ],
            }
            if any(value.get(field) != exact for field, exact in expected.items()):
                raise DurableJobError(
                    "binding_mismatch",
                    job_id=job["job_id"],
                    job_state=job["state"],
                )
            if message.kind == "program_preview":
                valid = (
                    value.get("transaction_aborted") is True
                    and value.get("drawing_unchanged") is True
                    and isinstance(value.get("preview_digest"), str)
                )
            else:
                valid = (
                    value.get("receipt_id") == binding.get("receipt_id")
                    and value.get("milestone") == "effect_and_receipt_committed"
                )
            if not valid:
                raise DurableJobError(
                    "binding_mismatch",
                    job_id=job["job_id"],
                    job_state=job["state"],
                )
            return value
        execution = job["payload"]["execution"]
        if message.kind == "program_preview":
            return {
                "program_digest": execution["program_digest"],
                "execution_digest": execution["execution_digest"],
                "binding_digest": execution["binding_digest"],
                "preview_id": value["preview_id"],
                "preview_digest": value["preview_digest"],
                "expires_at": value["expires_at"],
                "document_revision_before": execution["expected_document_revision"],
                "document_revision_after": execution["expected_document_revision"],
                "preview_strategy": "database_transaction_abort",
                "planned_operation_count": value["planned_operation_count"],
                "planned_entity_count": value["planned_entity_count"],
                "planned_layer_count": value["planned_layer_count"],
                "validation": {
                    "transaction_aborted": value["transaction_aborted"],
                    "drawing_unchanged": value["drawing_unchanged"],
                },
            }
        if message.kind == "program_commit":
            return {
                "receipt_id": value["receipt_id"],
                "receipt_digest": value["receipt_digest"],
                "program_digest": execution["program_digest"],
                "execution_digest": execution["execution_digest"],
                "preview_execution_digest": execution["preview_execution_digest"],
                "binding_digest": execution["binding_digest"],
                "document_id": execution["document_id"],
                "document_revision_before": value["document_revision_before"],
                "document_revision_after": value["document_revision_after"],
                "effect_summary": {
                    "created_entities": value["created_entity_count"],
                    "duplicate": value["duplicate"],
                },
                "durable_receipt": value,
                "checkpoint": value.get("checkpoint"),
            }
        return {
            "validation_id": value["validation_id"],
            "execution_digest": execution["execution_digest"],
            "binding_digest": execution["binding_digest"],
            "document_revision": value["document_revision"],
            "passed": value["valid"],
            "report": {
                "checks": value["checks"],
                "failures": value["failures"],
            },
        }

    def _validate_c1_observation(
        self,
        result: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        expected_package: dict[str, str] | None = None,
    ) -> str | None:
        package = dict(expected_package or self.required_package)
        if not package:
            return None
        evidence = result.get("execution_evidence")
        revision = snapshot.get("revision_evidence")
        drawing = snapshot.get("drawing")
        summary = snapshot.get("entity_summary")
        if not all(isinstance(value, dict) for value in (evidence, revision, drawing, summary)):
            return "backend_error"
        if set(result) != {"snapshot", "execution_evidence"}:
            return "backend_error"
        if set(snapshot) != {
            "snapshot_id",
            "document_revision",
            "observation_level",
            "drawing",
            "entity_summary",
            "entities",
            "revision_evidence",
        }:
            return "backend_error"
        if evidence.get("package") != package:
            return "package_mismatch"
        base_evidence_keys = {"agent_version", "runtime_state", "package"}
        runtime_evidence_keys = {"runtime", "degraded", "degradation_reason"}
        evidence_keys = set(evidence)
        if evidence_keys == base_evidence_keys:
            runtime = None
        elif evidence_keys == base_evidence_keys | runtime_evidence_keys:
            try:
                runtime = RuntimeEvidence.model_validate(evidence.get("runtime"))
            except (TypeError, ValueError):
                return "backend_error"
            if not isinstance(evidence.get("degraded"), bool):
                return "backend_error"
            degradation_reason = evidence.get("degradation_reason")
            if (
                degradation_reason is not None
                and (
                    not isinstance(degradation_reason, str)
                    or not 1 <= len(degradation_reason) <= 128
                )
            ):
                return "backend_error"
        else:
            return "backend_error"
        agent_version = evidence.get("agent_version")
        if not isinstance(agent_version, str) or not 1 <= len(agent_version) <= 64:
            return "backend_error"
        managed_dotnet = runtime is not None and runtime.id == "managed_dotnet"
        observation_level = snapshot.get("observation_level")
        if observation_level == "detail":
            return self._validate_c1_detail_observation(
                snapshot,
                revision=revision,
                drawing=drawing,
                summary=summary,
                managed_dotnet=managed_dotnet,
            )
        if observation_level != "summary":
            return "backend_error"
        if (
            set(revision)
            != {"revision_schema", "revision_strength", "commit_safe"}
            or revision
            != {
                "revision_schema": "cad.revision/1",
                "revision_strength": "summary_only",
                "commit_safe": False,
            }
            or snapshot.get("entities") != []
        ):
            return "backend_error"
        document_revision = snapshot.get("document_revision")
        if not isinstance(document_revision, str) or re.fullmatch(r"[0-9a-f]{64}", document_revision) is None:
            return "backend_error"
        document_name = drawing.get("document_name")
        compatibility_drawing_keys = {
            "document_name",
            "entity_count",
            "layers",
            "layer_count",
            "truncated",
            "dispatcher_version",
            "package_id",
            "package_version",
        }
        managed_drawing_keys = {
            "document_name",
            "entity_count",
            "layers",
            "layer_count",
            "truncated",
        }
        if set(drawing) != (
            managed_drawing_keys if managed_dotnet else compatibility_drawing_keys
        ):
            return "backend_error"
        if (
            not isinstance(document_name, str)
            or not document_name
            or len(document_name) > 255
            or PureWindowsPath(document_name).name != document_name
            or "/" in document_name
        ):
            return "backend_error"
        layers = drawing.get("layers")
        entity_count = drawing.get("entity_count")
        layer_count = drawing.get("layer_count")
        if (
            not isinstance(layers, list)
            or len(layers) > 256
            or any(not isinstance(item, str) or len(item) > 255 for item in layers)
            or isinstance(entity_count, bool)
            or not isinstance(entity_count, int)
            or entity_count < 0
            or isinstance(layer_count, bool)
            or not isinstance(layer_count, int)
            or layer_count < len(layers)
            or not isinstance(drawing.get("truncated"), bool)
            or summary != {"entity_count": entity_count, "detail_available": False}
        ):
            return "backend_error"
        if not managed_dotnet and (
            drawing.get("dispatcher_version") != package["version"]
            or drawing.get("package_id") != package["package_id"]
            or drawing.get("package_version") != package["version"]
        ):
            return "backend_error"
        return None

    @classmethod
    def _validate_c1_detail_observation(
        cls,
        snapshot: dict[str, Any],
        *,
        revision: dict[str, Any],
        drawing: dict[str, Any],
        summary: dict[str, Any],
        managed_dotnet: bool,
    ) -> str | None:
        entities = snapshot.get("entities")
        truncated = summary.get("truncated")
        if (
            not managed_dotnet
            or set(revision)
            != {"revision_schema", "revision_strength", "commit_safe"}
            or revision.get("revision_schema") != "cad.revision/1"
            or revision.get("revision_strength")
            != "database_object_fingerprint"
            or not isinstance(revision.get("commit_safe"), bool)
            or not isinstance(truncated, bool)
            or revision["commit_safe"] is truncated
            or not isinstance(entities, list)
            or len(entities) > 512
            or summary
            != {
                "entity_count": len(entities),
                "detail_available": True,
                "truncated": truncated,
            }
            or any(not cls._valid_c1_detail_entity(entity) for entity in entities)
            or len(
                {
                    "geometry_status" in entity
                    for entity in entities
                    if isinstance(entity, dict)
                }
            )
            > 1
        ):
            return "backend_error"
        document_revision = snapshot.get("document_revision")
        if (
            not isinstance(document_revision, str)
            or re.fullmatch(r"[1-9][0-9]{0,18}", document_revision) is None
        ):
            return "backend_error"
        if set(drawing) != {
            "document_id",
            "document_name",
            "database_fingerprint",
            "entity_count",
            "layers",
            "layer_count",
            "truncated",
        }:
            return "backend_error"
        document_id = drawing.get("document_id")
        database_fingerprint = drawing.get("database_fingerprint")
        document_name = drawing.get("document_name")
        layers = drawing.get("layers")
        entity_count = drawing.get("entity_count")
        layer_count = drawing.get("layer_count")
        if (
            not isinstance(document_id, str)
            or re.fullmatch(r"doc-[A-Za-z0-9._-]{1,124}", document_id) is None
            or not isinstance(database_fingerprint, str)
            or not 1 <= len(database_fingerprint) <= 128
            or not isinstance(document_name, str)
            or not document_name
            or len(document_name) > 255
            or PureWindowsPath(document_name).name != document_name
            or "/" in document_name
            or not isinstance(layers, list)
            or len(layers) > 256
            or any(not isinstance(item, str) or len(item) > 255 for item in layers)
            or isinstance(entity_count, bool)
            or not isinstance(entity_count, int)
            or entity_count < len(entities)
            or isinstance(layer_count, bool)
            or not isinstance(layer_count, int)
            or layer_count < len(layers)
            or not isinstance(drawing.get("truncated"), bool)
        ):
            return "backend_error"
        return None

    @classmethod
    def _valid_c1_detail_entity(cls, entity: Any) -> bool:
        base_keys = {
            "entity_id",
            "entity_type",
            "layer",
            "space",
            "bounds",
            "geometry",
            "geometry_truncated",
            "fingerprint",
        }
        provenance_keys = {
            "geometry_status",
            "geometry_reason",
            "source_runtime",
            "source_capabilities",
        }
        if (
            not isinstance(entity, dict)
            or set(entity) not in (base_keys, base_keys | provenance_keys)
        ):
            return False
        entity_type = entity.get("entity_type")
        geometry = entity.get("geometry")
        if (
            not isinstance(entity.get("entity_id"), str)
            or re.fullmatch(r"[0-9A-Fa-f]{1,32}", entity["entity_id"]) is None
            or entity_type not in {"LINE", "CIRCLE", "LWPOLYLINE", "ARC"}
            or not isinstance(entity.get("layer"), str)
            or len(entity["layer"]) > 255
            or entity.get("space") != "model"
            or not isinstance(entity.get("geometry_truncated"), bool)
            or not isinstance(entity.get("fingerprint"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", entity["fingerprint"]) is None
            or not cls._valid_c1_bounds(entity.get("bounds"))
        ):
            return False
        if provenance_keys <= set(entity):
            status = entity.get("geometry_status")
            reason = entity.get("geometry_reason")
            source_capabilities = entity.get("source_capabilities")
            expected_capability = {
                "LINE": "entity.geometry.line/1",
                "CIRCLE": "entity.geometry.circle/1",
                "LWPOLYLINE": "entity.geometry.polyline/1",
                "ARC": "entity.geometry.arc/1",
            }[entity_type]
            expected_source_capabilities = (
                [expected_capability]
                if status in {"exact", "truncated"}
                else []
            )
            if (
                entity.get("source_runtime") != "managed_dotnet"
                or status
                not in {
                    "exact",
                    "truncated",
                    "unsupported",
                    "unavailable",
                    "invalid",
                }
                or (
                    reason is not None
                    and (
                        not isinstance(reason, str)
                        or not 1 <= len(reason) <= 128
                    )
                )
                or not isinstance(source_capabilities, list)
                or source_capabilities != expected_source_capabilities
                or entity["geometry_truncated"] is not (status == "truncated")
                or (
                    status == "exact"
                    and (geometry is None or reason is not None)
                )
                or (
                    status != "exact"
                    and (geometry is not None or reason is None)
                )
            ):
                return False
        if geometry is None:
            return True
        if not isinstance(geometry, dict):
            return False
        if entity_type == "LINE":
            keys = set(geometry)
            return (
                keys == {"start", "end"}
                or (
                    keys
                    == {"start", "end", "start_elevation", "end_elevation"}
                    and cls._valid_c1_number(geometry.get("start_elevation"))
                    and cls._valid_c1_number(geometry.get("end_elevation"))
                )
            ) and all(
                cls._valid_c1_point(geometry.get(key), 2) for key in ("start", "end")
            )
        if entity_type in {"CIRCLE", "ARC"}:
            radius = geometry.get("radius")
            keys = {"center", "radius"}
            if entity_type == "ARC":
                keys |= {"start_angle_radians", "end_angle_radians"}
            canonical_keys = keys | {"elevation", "normal"}
            return (
                set(geometry)
                in (
                    (keys, canonical_keys)
                    if entity_type == "CIRCLE"
                    else (canonical_keys,)
                )
                and cls._valid_c1_point(geometry.get("center"), 2)
                and cls._valid_c1_number(radius)
                and radius > 0
                and (
                    set(geometry) == keys
                    or (
                        cls._valid_c1_number(geometry.get("elevation"))
                        and cls._valid_c1_point(geometry.get("normal"), 3)
                    )
                )
                and (
                    entity_type == "CIRCLE"
                    or (
                        cls._valid_c1_number(geometry.get("start_angle_radians"))
                        and cls._valid_c1_number(geometry.get("end_angle_radians"))
                    )
                )
            )
        points = geometry.get("points")
        keys = set(geometry)
        bulges = geometry.get("bulges")
        return (
            (
                keys == {"points", "closed"}
                or (
                    keys == {"points", "bulges", "closed", "elevation", "normal"}
                    and isinstance(bulges, list)
                    and isinstance(points, list)
                    and len(bulges) == len(points)
                    and all(cls._valid_c1_number(value) for value in bulges)
                    and cls._valid_c1_number(geometry.get("elevation"))
                    and cls._valid_c1_point(geometry.get("normal"), 3)
                )
            )
            and isinstance(points, list)
            and len(points) <= 4096
            and all(cls._valid_c1_point(point, 2) for point in points)
            and isinstance(geometry.get("closed"), bool)
        )

    @staticmethod
    def _normalize_c1_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        if snapshot.get("observation_level") != "detail":
            return snapshot
        entities = snapshot["entities"]
        if not entities or all("geometry_status" in entity for entity in entities):
            return snapshot
        normalized = []
        for entity in entities:
            if "geometry_status" in entity:
                normalized.append(entity)
                continue
            geometry = entity.get("geometry")
            status = (
                "truncated"
                if entity["geometry_truncated"]
                else "bounded_projection"
                if geometry is not None
                else "unavailable"
            )
            normalized.append(
                {
                    **entity,
                    "geometry_status": status,
                    "geometry_reason": "legacy_agent_provenance_unavailable",
                    "source_runtime": "managed_dotnet_legacy",
                    "source_capabilities": [],
                }
            )
        return {**snapshot, "entities": normalized}

    @classmethod
    def _valid_c1_bounds(cls, bounds: Any) -> bool:
        return bounds is None or (
            isinstance(bounds, dict)
            and set(bounds) == {"min", "max"}
            and cls._valid_c1_point(bounds.get("min"), 3)
            and cls._valid_c1_point(bounds.get("max"), 3)
        )

    @classmethod
    def _valid_c1_point(cls, value: Any, size: int) -> bool:
        return (
            isinstance(value, list)
            and len(value) == size
            and all(cls._valid_c1_number(item) for item in value)
        )

    @staticmethod
    def _valid_c1_number(value: Any) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
        )

    async def _fail_payload(self, job: dict[str, Any]) -> None:
        target = "needs_attention" if job["state"] == "outcome_unknown" else "failed"
        try:
            updated = await self.repository.transition_job(
                job["job_id"],
                target,
                evidence=job["state"] == "reconnect_pending",
                error_code="payload_mismatch",
                error_summary="Agent payload hash did not match Gateway",
            )
            await self._release_program_lock_if_terminal(updated)
            self._resolve(updated)
        except (InvalidJobTransition, RepositoryConflict):
            logger.info(
                "Payload mismatch arrived after a concurrent terminal outcome",
                extra={"job_id": job["job_id"], "state": job["state"]},
            )

    async def _get_internal_job(self, job_id: str) -> dict[str, Any] | None:
        # The repository's owner-scoped method is intentionally the only public lookup.
        # This internal scan is bounded to one worker's durable jobs and never crosses
        # the MCP boundary.
        for owner in await self._known_owners():
            job = await self.repository.get_job(owner, job_id)
            if job:
                return job
        return None

    async def _known_owners(self) -> list[str]:
        with self.repository.database.read_connection() as conn:
            rows = conn.execute("SELECT DISTINCT owner_subject FROM jobs").fetchall()
        return [str(row[0]) for row in rows]

    async def _require_dispatch_capability(
        self,
        job: dict[str, Any],
        *,
        connection: AgentConnection | None = None,
    ) -> None:
        required = "observe" if job["kind"] in {"observe", "write_fixture"} else job["kind"]
        connection = connection or await self.registry.get(job["device_id"])
        if connection is not None and hasattr(connection, "capabilities"):
            capabilities = set(connection.capabilities)
        else:
            device = await self.repository.get_device(
                job["owner_subject"], job["device_id"]
            )
            capabilities = set(device["capabilities"]) if device else set()
        failure_code: str | None = None
        failure_summary = "Agent does not advertise the required capability"
        if required not in capabilities:
            failure_code = "capability_missing"
        elif connection is not None and connection.paused:
            failure_code = "paused_by_user"
            failure_summary = "Agent is paused by the local user"
        elif job["kind"] in {"program_preview", "program_commit", "program_validate"}:
            failure_code, failure_summary = self._program_dispatch_failure(
                job, connection
            )
        elif job["kind"] in {
            "receipt_lookup",
            "checkpoint_lookup",
            "rollback_preview",
            "rollback_commit",
            "rollback_validate",
        }:
            failure_code, failure_summary = self._rollback_dispatch_failure(
                job, connection
            )
        else:
            required_package = dict(
                job.get("payload", {}).get("package") or self.required_package
            )
            if not required_package:
                return
            packages = (
                list(connection.packages)
                if connection is not None
                else list((device or {}).get("packages", []))
            )
            if required_package not in packages:
                failure_code = "package_mismatch"
                failure_summary = "Agent package does not match the required manifest"
        if failure_code is None:
            return
        try:
            updated = await self.repository.transition_job(
                job["job_id"],
                "failed",
                error_code=failure_code,
                error_summary=failure_summary,
            )
        except (InvalidJobTransition, RepositoryConflict) as error:
            raise DurableJobError(
                failure_code,
                job_id=job["job_id"],
                job_state=job["state"],
            ) from error
        self._resolve(updated)
        await self._release_program_lock_if_terminal(updated)
        raise DurableJobError(
            failure_code,
            job_id=job["job_id"],
            job_state=updated["state"] if updated else job["state"],
        )

    async def _release_program_lock_if_terminal(
        self, job: dict[str, Any] | None
    ) -> None:
        if (
            job is not None
            and self.program_repository is not None
            and job.get("kind") in {"program_preview", "program_commit"}
            and is_terminal(job["state"])
        ):
            await self.program_repository.release_write_lock(job["job_id"])

    def _program_dispatch_failure(
        self,
        job: dict[str, Any],
        connection: AgentConnection | None,
    ) -> tuple[str | None, str]:
        if connection is None:
            return "device_offline", "Agent is not connected"
        payload = job.get("payload", {})
        binding = payload.get("binding") if isinstance(payload, dict) else None
        phase8 = (
            isinstance(binding, dict)
            and binding.get("schema_version")
            == "cad.execution-binding/1"
        )
        if phase8:
            plan = payload.get("execution_plan")
            pins = (
                plan.get("execution_pins")
                if isinstance(plan, dict)
                else None
            )
        else:
            execution = payload.get("execution")
            pins = (
                execution.get("pins")
                if isinstance(execution, dict)
                else None
            )
        if not isinstance(pins, dict):
            return "binding_mismatch", "Program execution binding is missing"
        if (
            self.program_policy_version is None
            or pins.get("policy_version") != self.program_policy_version
        ):
            return "policy_mismatch", "Gateway policy changed after preparation"
        if (
            job["kind"] in {"program_preview", "program_commit"}
            and (
                not self.managed_write_enabled
                or connection.device_id not in self.allowed_write_device_ids
            )
        ):
            return "feature_disabled", "Managed write is not enabled for this device"
        if connection.hard_pause or connection.paused:
            return "paused_by_user", "Agent hard pause is active"
        if job["kind"] in {"program_preview", "program_commit"} and not connection.write_lock_enabled:
            return "write_lock_disabled", "Agent write lock is disabled"
        capability_hash = str(connection.capability_manifest_hash or "")
        registry_hash = str(connection.operation_registry_hash or "")
        capability_hash = (
            capability_hash
            if capability_hash.startswith("sha256:")
            else "sha256:" + capability_hash
        )
        registry_hash = (
            registry_hash
            if registry_hash.startswith("sha256:")
            else "sha256:" + registry_hash
        )
        if (
            capability_hash != pins.get("capability_manifest_hash")
            or registry_hash != pins.get("operation_registry_hash")
            or connection.registry_version
            != pins.get(
                "operation_registry_version"
                if phase8
                else "registry_version"
            )
        ):
            return "binding_mismatch", "Capability or registry evidence changed"
        packages = list(connection.packages)
        package = {
            "package_id": pins.get("package_id"),
            "version": pins.get("package_version"),
            "sha256": str(pins.get("package_hash", "")).removeprefix("sha256:"),
        }
        if package not in packages:
            return "package_mismatch", "Managed Host package evidence changed"
        manifest = connection.capability_manifest or {}
        products = manifest.get("cad_products", [])
        matching = [
            item
            for item in products
            if isinstance(item, dict)
            and isinstance(item.get("runtime"), dict)
            and item["runtime"].get("id") == pins.get("runtime_id")
            and item["runtime"].get("role") == pins.get("runtime_role")
            and item["runtime"].get("host_family") == pins.get("host_family")
            and item["runtime"].get("host_version") == pins.get("host_version")
            and item.get("edition") == "full"
        ]
        if len(matching) != 1:
            return "runtime_mismatch", "Managed R25 runtime evidence changed"
        return None, ""

    def _rollback_dispatch_failure(
        self,
        job: dict[str, Any],
        connection: AgentConnection | None,
    ) -> tuple[str | None, str]:
        if connection is None:
            return "device_offline", "Agent is not connected"
        payload = job.get("payload")
        binding = payload.get("binding") if isinstance(payload, dict) else None
        if not isinstance(binding, dict):
            return "binding_mismatch", "Rollback execution binding is missing"
        if (
            self.program_policy_version is None
            or binding.get("policy_version") != self.program_policy_version
        ):
            return "policy_mismatch", "Gateway policy changed after rollback preview"
        if (
            binding.get("runtime_id") != "managed_dotnet"
            or binding.get("runtime_role") != "primary"
            or binding.get("host_family") != "R25"
        ):
            return "runtime_mismatch", "Rollback requires exact Managed .NET R25"
        if job["kind"] == "rollback_commit" and (
            not self.managed_write_enabled
            or connection.device_id not in self.allowed_write_device_ids
        ):
            return "feature_disabled", "Managed rollback is not enabled for this device"
        if connection.hard_pause or connection.paused:
            return "paused_by_user", "Agent hard pause is active"
        if job["kind"] == "rollback_commit" and not connection.write_lock_enabled:
            return "write_lock_disabled", "Agent write lock is disabled"
        if (
            connection.active_document_id != binding.get("document_id")
            or connection.active_document_revision != binding.get("document_revision")
        ):
            return "stale_revision", "Active document changed after rollback preview"
        capability_hash = str(connection.capability_manifest_hash or "")
        registry_hash = str(connection.operation_registry_hash or "")
        capability_hash = (
            capability_hash
            if capability_hash.startswith("sha256:")
            else "sha256:" + capability_hash
        )
        registry_hash = (
            registry_hash
            if registry_hash.startswith("sha256:")
            else "sha256:" + registry_hash
        )
        if (
            capability_hash != binding.get("capability_manifest_hash")
            or registry_hash != binding.get("operation_registry_hash")
            or connection.registry_version
            != binding.get("operation_registry_version")
        ):
            return "binding_mismatch", "Rollback capability or registry evidence changed"
        package = {
            "package_id": binding.get("package_id"),
            "version": binding.get("package_version"),
            "sha256": str(binding.get("package_hash", "")).removeprefix("sha256:"),
        }
        if package not in list(connection.packages):
            return "package_mismatch", "Rollback Managed Host package evidence changed"
        products = (connection.capability_manifest or {}).get("cad_products", [])
        matching = [
            item
            for item in products
            if isinstance(item, dict)
            and isinstance(item.get("runtime"), dict)
            and item["runtime"].get("id") == binding.get("runtime_id")
            and item["runtime"].get("role") == binding.get("runtime_role")
            and item["runtime"].get("host_family") == binding.get("host_family")
            and item["runtime"].get("host_version") == binding.get("host_version")
            and item.get("edition") == "full"
        ]
        if len(matching) != 1:
            return "runtime_mismatch", "Rollback Managed R25 runtime evidence changed"
        return None, ""

    @staticmethod
    def _validate_message_binding(
        connection: AgentConnection,
        job: dict[str, Any],
        message: Any,
    ) -> None:
        if message.session_id != connection.session_id:
            raise DurableJobError(
                "invalid_message", job_id=job["job_id"], job_state=job["state"]
            )
        if message.device_id != connection.device_id or job["device_id"] != connection.device_id:
            raise DurableJobError(
                "invalid_message", job_id=job["job_id"], job_state=job["state"]
            )
        if message.command_id != job["command_id"]:
            raise DurableJobError(
                "invalid_message", job_id=job["job_id"], job_state=job["state"]
            )
        message_job_id = getattr(message, "job_id", None)
        if message_job_id is not None and message_job_id != job["job_id"]:
            raise DurableJobError(
                "invalid_message", job_id=job["job_id"], job_state=job["state"]
            )

    @staticmethod
    def _safe_agent_error(error_code: str | None) -> tuple[str, str]:
        messages = {
            "active_document_changed": "The active AutoCAD document changed during the read",
            "autocad_busy": "AutoCAD is running another command",
            "autocad_not_running": "AutoCAD is not running",
            "command_routing_failed": "Agent could not route the read command to AutoCAD",
            "deadline_expired": "Agent reported that the job deadline expired",
            "capability_missing": "Agent does not support the requested capability",
            "dispatcher_not_loaded": "The required AutoLISP dispatcher is not loaded",
            "dispatcher_timeout": "The AutoLISP dispatcher did not respond in time",
            "ipc_result_invalid": "AutoCAD returned invalid bounded read evidence",
            "modal_dialog_active": "AutoCAD has a modal dialog open",
            "no_active_document": "AutoCAD has no active document",
            "payload_mismatch": "Agent rejected a mismatched command payload",
            "package_mismatch": "Agent package does not match Gateway policy",
            "paused_by_user": "The local user paused remote tasks",
            "agent_rejected": "Agent rejected the command",
        }
        if error_code in messages:
            return error_code, messages[error_code]
        return "backend_error", "Agent reported a bounded CAD operation failure"

    async def _send_reconcile(
        self, connection: AgentConnection, jobs: list[dict[str, Any]]
    ) -> None:
        await connection.send(
            ReconcileMessage(
                protocol_version=connection.protocol_version,
                session_id=connection.session_id,
                device_id=connection.device_id,
                commands=[
                    ReconcileCommandDescriptor(
                        job_id=job["job_id"],
                        command_id=job["command_id"],
                        payload_hash=job["payload_hash"],
                    )
                    for job in jobs
                ],
            ).model_dump(mode="json", exclude_none=True)
        )

    @staticmethod
    async def _send_cancel(
        connection: AgentConnection,
        job: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        await connection.send(
            CancelMessage(
                protocol_version=connection.protocol_version,
                session_id=connection.session_id,
                device_id=job["device_id"],
                job_id=job["job_id"],
                command_id=job["command_id"],
                reason=reason,
            ).model_dump(mode="json", exclude_none=True)
        )

    def _waiter_for(self, job_id: str) -> asyncio.Future[dict[str, Any]]:
        waiter = self._waiters.get(job_id)
        if waiter is None or waiter.done():
            waiter = asyncio.get_running_loop().create_future()
            self._waiters[job_id] = waiter
        return waiter

    def _resolve(self, job: dict[str, Any] | None) -> None:
        if not job:
            return
        waiter = self._waiters.get(job["job_id"])
        if waiter and not waiter.done() and is_terminal(job["state"]):
            waiter.set_result(job)
            self._waiters.pop(job["job_id"], None)

    def cancel_waiters(self) -> None:
        for waiter in self._waiters.values():
            if not waiter.done():
                waiter.cancel()
        self._waiters.clear()
