"""Job orchestration; all socket waits happen outside repository transactions."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import PureWindowsPath
from typing import Any

from autocad_contracts import (
    AckMessage,
    CancelMessage,
    CommandMessage,
    ProgressMessage,
    ReconcileCommandDescriptor,
    ReconcileMessage,
    ReconcileResultMessage,
    ResultMessage,
    RuntimeEvidence,
)

from ..domain.jobs import InvalidJobTransition, is_terminal
from ..infrastructure.agent_transport.connection_registry import AgentConnection, ConnectionRegistry
from ..infrastructure.sqlite.repositories import RepositoryConflict, SqliteRepository


logger = logging.getLogger(__name__)


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
            command = CommandMessage(
                correlation_id=correlation_id,
                session_id=connection.session_id,
                device_id=raw["device_id"],
                job_id=job_id,
                command_id=raw["command_id"],
                deadline_at=raw["deadline_at"],
                idempotency_key=raw["idempotency_key"],
                payload_hash=raw["payload_hash"],
                kind=raw["kind"],
                effect_class=raw["effect_class"],
                payload=raw["payload"],
            )
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
        elif isinstance(message, ResultMessage):
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
            result = ResultMessage(
                session_id=connection.session_id,
                device_id=connection.device_id,
                job_id=job["job_id"],
                command_id=job["command_id"],
                sequence=message.sequence,
                payload_hash=message.payload_hash,
                status=message.result_status,
                result=message.result,
                error_code=message.error_code,
                error_message=message.error_message,
            )
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
            return
        raise DurableJobError(
            "invalid_message", job_id=job["job_id"], job_state=job["state"]
        )

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

    async def _handle_result(self, job: dict[str, Any], message: ResultMessage) -> None:
        if message.payload_hash != job["payload_hash"]:
            await self._fail_payload(job)
            return
        target = message.status
        result = message.result
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
                validation_error = self._validate_c1_observation(result, candidate)
                if validation_error is not None:
                    target = "failed"
                    result = None
                    error_code = validation_error
                    error_summary = "Agent returned invalid C1 observation evidence"
                else:
                    snapshot = candidate
        elif target == "failed":
            result = None
            error_code, error_summary = self._safe_agent_error(message.error_code)
        elif target == "cancelled":
            result = None
            error_code = "cancelled"
            error_summary = "Agent confirmed cancellation"
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

    def _validate_c1_observation(
        self, result: dict[str, Any], snapshot: dict[str, Any]
    ) -> str | None:
        if not self.required_package:
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
        if evidence.get("package") != self.required_package:
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
        if set(revision) != {"revision_schema", "revision_strength", "commit_safe"} or revision != {
            "revision_schema": "cad.revision/1",
            "revision_strength": "summary_only",
            "commit_safe": False,
        }:
            return "backend_error"
        if snapshot.get("observation_level") != "summary" or snapshot.get("entities") != []:
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
        managed_dotnet = runtime is not None and runtime.id == "managed_dotnet"
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
            drawing.get("dispatcher_version") != self.required_package["version"]
            or drawing.get("package_id") != self.required_package["package_id"]
            or drawing.get("package_version") != self.required_package["version"]
        ):
            return "backend_error"
        return None

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
        elif self.required_package:
            packages = (
                list(connection.packages)
                if connection is not None
                else list((device or {}).get("packages", []))
            )
            if self.required_package not in packages:
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
        raise DurableJobError(
            failure_code,
            job_id=job["job_id"],
            job_state=updated["state"] if updated else job["state"],
        )

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
