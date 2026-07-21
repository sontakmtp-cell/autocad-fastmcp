"""Job orchestration; all socket waits happen outside repository transactions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from autocad_contracts import (
    AckMessage,
    CancelMessage,
    CommandMessage,
    ProgressMessage,
    ReconcileMessage,
    ReconcileResultMessage,
    ResultMessage,
)

from ..domain.jobs import is_terminal
from ..infrastructure.agent_transport.connection_registry import AgentConnection, ConnectionRegistry
from ..infrastructure.sqlite.repositories import RepositoryConflict, SqliteRepository


class DurableJobError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DurableJobService:
    def __init__(
        self,
        repository: SqliteRepository,
        registry: ConnectionRegistry,
        *,
        command_timeout_seconds: int = 30,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.command_timeout_seconds = max(1, min(command_timeout_seconds, 600))
        self._waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._dispatch_lock = asyncio.Lock()

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
            raise DurableJobError(error.code) from None
        if is_terminal(job["state"]):
            return job
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._waiters[job["job_id"]] = waiter
        try:
            await self.dispatch(job["job_id"], correlation_id=correlation_id)
            try:
                return await asyncio.wait_for(waiter, timeout=self.command_timeout_seconds)
            except asyncio.TimeoutError:
                current = await self.repository.get_job(owner_subject, job["job_id"])
                if current and current["state"] in {"dispatched", "acknowledged", "running"}:
                    try:
                        await self.repository.transition_job(
                            job["job_id"], "failed", error_code="dispatcher_timeout", error_summary="Agent did not finish before timeout"
                        )
                    except Exception:
                        pass
                raise DurableJobError("dispatcher_timeout") from None
        finally:
            self._waiters.pop(job["job_id"], None)

    async def dispatch(self, job_id: str, *, correlation_id: str) -> bool:
        async with self._dispatch_lock:
            # The repository deliberately exposes no unscoped user-facing get_job, but
            # the dispatcher may use the internal command lookup after a job is claimed.
            raw = await self._get_internal_job(job_id)
            if raw is None or is_terminal(raw["state"]):
                return False
            if raw["state"] == "outcome_unknown":
                raise DurableJobError("outcome_unknown")
            if raw["state"] == "needs_attention":
                return False
            if raw["state"] == "queued":
                claimed = await self.repository.claim_job(job_id)
                if claimed is None:
                    raw = await self._get_internal_job(job_id)
                    if raw is None:
                        return False
                else:
                    raw = claimed
            connection = await self.registry.get(raw["device_id"])
            if connection is None or not await self.registry.is_fresh(raw["device_id"]):
                if raw["state"] == "dispatched":
                    try:
                        await self.repository.transition_job(job_id, "reconnect_pending")
                    except Exception:
                        pass
                raise DurableJobError("device_offline")
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
                raise DurableJobError("device_offline") from error
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
                try:
                    updated = await self.repository.transition_job(
                        job["job_id"],
                        "failed",
                        error_code="deadline_expired",
                        error_summary="Job deadline expired before completion",
                    )
                    self._resolve(updated)
                except Exception:
                    pass

    async def handle_message(self, connection: AgentConnection, message: Any) -> None:
        job = await self.repository.get_job_by_command(connection.device_id, message.command_id)
        if job is None or job["device_id"] != connection.device_id:
            return
        if message.session_id and message.session_id != connection.session_id:
            return
        if isinstance(message, AckMessage):
            await self._handle_ack(job, message)
        elif isinstance(message, ProgressMessage):
            await self._handle_progress(job, message)
        elif isinstance(message, ResultMessage):
            await self._handle_result(job, message)
        elif isinstance(message, ReconcileResultMessage):
            await self.handle_reconcile_result(connection, message)

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
            except Exception:
                pass

    async def handle_connected(self, connection: AgentConnection) -> None:
        jobs = await self.repository.jobs_for_device(connection.device_id)
        command_ids = [
            job["command_id"]
            for job in jobs
            if job["state"] in {"reconnect_pending", "outcome_unknown"}
        ]
        if command_ids:
            await connection.send(
                ReconcileMessage(
                    session_id=connection.session_id,
                    device_id=connection.device_id,
                    command_ids=command_ids,
                ).model_dump(mode="json", exclude_none=True)
            )

    async def handle_reconcile_result(
        self, connection: AgentConnection, message: ReconcileResultMessage
    ) -> None:
        job = await self.repository.get_job_by_command(connection.device_id, message.command_id)
        if job is None or message.payload_hash != job["payload_hash"]:
            return
        if message.status == "terminal" and message.result_status:
            result = ResultMessage(
                session_id=connection.session_id,
                device_id=connection.device_id,
                job_id=job["job_id"],
                command_id=job["command_id"],
                payload_hash=message.payload_hash,
                status=message.result_status,
                result=message.result,
            )
            await self._handle_result(job, result)
            return
        if job["state"] == "outcome_unknown":
            if message.status == "started":
                return
            try:
                updated = await self.repository.transition_job(job["job_id"], "needs_attention", evidence=True)
                self._resolve(updated)
            except Exception:
                pass
            return
        if message.status == "not_started" and job["state"] == "reconnect_pending":
            try:
                updated = await self.repository.transition_job(job["job_id"], "queued", evidence=True)
                await self.dispatch(updated["job_id"], correlation_id=updated["job_id"])
            except DurableJobError:
                pass

    async def cancel(self, job_id: str, *, owner_subject: str, reason: str) -> dict[str, Any]:
        job = await self.repository.get_job(owner_subject, job_id)
        if job is None:
            raise DurableJobError("not_found")
        if job["state"] == "queued":
            return await self.repository.transition_job(job_id, "cancelled") or job
        if is_terminal(job["state"]):
            return job
        updated = await self.repository.transition_job(job_id, "cancel_requested")
        if updated is None:
            raise DurableJobError("not_found")
        connection = await self.registry.get(job["device_id"])
        if connection:
            await connection.send(
                CancelMessage(
                    session_id=connection.session_id,
                    device_id=job["device_id"],
                    job_id=job_id,
                    command_id=job["command_id"],
                    reason=reason,
                ).model_dump(mode="json", exclude_none=True)
            )
        return updated

    async def _handle_ack(self, job: dict[str, Any], message: AckMessage) -> None:
        if message.payload_hash != job["payload_hash"]:
            await self._fail_payload(job)
            return
        if message.status == "accepted" and job["state"] == "dispatched":
            updated = await self.repository.transition_job(job["job_id"], "acknowledged")
            if updated:
                self._resolve(updated)

    async def _handle_progress(self, job: dict[str, Any], message: ProgressMessage) -> None:
        if job["state"] == "acknowledged":
            job = await self.repository.transition_job(job["job_id"], "running") or job
        try:
            updated = await self.repository.append_progress(
                job["job_id"], phase=message.phase, percent=message.percent, message=message.message, sequence=message.sequence
            )
        except RepositoryConflict:
            return
        if updated:
            self._resolve(updated)

    async def _handle_result(self, job: dict[str, Any], message: ResultMessage) -> None:
        if message.payload_hash != job["payload_hash"]:
            await self._fail_payload(job)
            return
        if is_terminal(job["state"]):
            return
        target = message.status
        if target == "cancelled" and job["state"] == "cancel_requested":
            target = "cancelled"
        try:
            if target == "succeeded" and message.result and message.result.get("snapshot"):
                await self.repository.save_snapshot(
                    owner_subject=job["owner_subject"],
                    device_id=job["device_id"],
                    job_id=job["job_id"],
                    snapshot=message.result["snapshot"],
                )
            updated = await self.repository.transition_job(
                job["job_id"],
                target,
                result=message.result,
                error_code=message.error_code,
                error_summary=message.error_message,
                evidence=True,
            )
        except Exception:
            return
        if updated:
            self._resolve(updated)

    async def _fail_payload(self, job: dict[str, Any]) -> None:
        try:
            updated = await self.repository.transition_job(
                job["job_id"], "failed", error_code="payload_mismatch", error_summary="Agent payload hash did not match Gateway"
            )
            self._resolve(updated)
        except Exception:
            pass

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

    def _resolve(self, job: dict[str, Any] | None) -> None:
        if not job:
            return
        waiter = self._waiters.get(job["job_id"])
        if waiter and not waiter.done() and is_terminal(job["state"]):
            waiter.set_result(job)
