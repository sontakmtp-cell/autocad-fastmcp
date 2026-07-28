"""Fail-closed Phase 7 execution evidence and RecoveryCase orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from autocad_contracts import (
    ExecutionEvidenceEvent,
    RecoveryCaseRecord,
    RollbackReceiptRecord,
    execution_evidence_digest,
)

from .domain.jobs import InvalidJobTransition
from .infrastructure.sqlite.phase7_repository import Phase7Repository
from .infrastructure.sqlite.repositories import RepositoryConflict, SqliteRepository


RecoveryCause = Literal[
    "bounded_inconclusive",
    "document_unavailable",
    "evidence_conflict",
    "deadline_outcome_unknown",
    "commit_outcome_unknown",
]

_SAFE_ACTIONS = (
    "retry_exact_evidence_query",
    "reopen_exact_document",
    "collect_redacted_diagnostics",
    "materialize_from_exact_host_receipt",
    "mark_unresolved",
    "needs_support",
)
_SENSITIVE_DETAIL_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "cookie",
        "password",
        "pipe_secret",
        "private_key",
        "refresh_token",
        "secret",
        "token",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _case_id(job_id: str) -> str:
    return f"recovery-{sha256(job_id.encode('utf-8')).hexdigest()[:32]}"


class Phase7RecoveryService:
    """Adds recovery evidence around the existing durable job state machine."""

    def __init__(
        self,
        jobs: SqliteRepository,
        phase7: Phase7Repository,
        *,
        max_reconcile_attempts: int = 1,
        cases_enabled: bool = True,
    ) -> None:
        self.jobs = jobs
        self.phase7 = phase7
        self.max_reconcile_attempts = max(1, min(int(max_reconcile_attempts), 10))
        self.cases_enabled = cases_enabled

    async def append_evidence(
        self, value: ExecutionEvidenceEvent | dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        record = ExecutionEvidenceEvent.model_validate(value)
        if any(
            item.key.casefold() in _SENSITIVE_DETAIL_KEYS
            for item in record.payload.details
        ):
            raise RepositoryConflict("unsafe_evidence")
        try:
            return await self.phase7.append_evidence(record)
        except RepositoryConflict as error:
            if error.code not in {"evidence_conflict", "evidence_sequence_rejected"}:
                raise
            job = await self.jobs.get_job(record.owner_subject, record.job_id)
            if job is not None:
                await self._freeze_for_conflict(job)
                await self.ensure_recovery_case(
                    owner_subject=record.owner_subject,
                    job=job,
                    cause="evidence_conflict",
                    evidence_event_ids=[
                        item["event_id"]
                        for item in await self.phase7.list_evidence(
                            record.owner_subject, record.job_id
                        )
                    ],
                    latest_query_result={
                        "outcome": "conflict",
                        "source": record.source,
                        "summary": "Evidence sequence or digest conflicts with durable history",
                        "queried_at": _now(),
                    },
                )
            raise

    async def record_reconcile_outcome(
        self,
        *,
        owner_subject: str,
        job_id: str,
        outcome: Literal[
            "not_found",
            "committed",
            "rolled_back",
            "conflict",
            "aborted",
            "inconclusive",
            "unavailable",
            "malformed_ledger",
        ],
        source: Literal["gateway", "agent", "host"],
        summary: str,
        attempt: int = 1,
        current_state: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.cases_enabled:
            return None
        job = await self.jobs.get_job(owner_subject, job_id)
        if job is None:
            return None
        cause: RecoveryCause | None = None
        if outcome in {"conflict", "malformed_ledger"}:
            cause = "evidence_conflict"
            await self._freeze_for_conflict(job)
        elif outcome == "unavailable":
            cause = "document_unavailable"
        elif outcome == "inconclusive" and attempt >= self.max_reconcile_attempts:
            cause = "bounded_inconclusive"
        if cause is None:
            return None
        return await self.ensure_recovery_case(
            owner_subject=owner_subject,
            job=job,
            cause=cause,
            latest_query_result={
                "outcome": outcome,
                "source": source,
                "summary": summary[:512],
                "queried_at": _now(),
            },
            current_state=current_state,
        )

    async def prepare_rollback_receipt(
        self,
        *,
        owner_subject: str,
        job: dict[str, Any],
        result: dict[str, Any],
    ) -> RollbackReceiptRecord:
        """Validate exact Host receipt evidence before the atomic terminal write."""

        receipt = result.get("receipt")
        if not isinstance(receipt, dict):
            raise RepositoryConflict("rollback_result_invalid")
        plan = await self.phase7.get_rollback_plan(
            owner_subject, str(receipt.get("rollback_plan_id", ""))
        )
        checkpoint = await self.phase7.get_checkpoint(
            owner_subject, str(receipt.get("checkpoint_id", ""))
        )
        if plan is None or checkpoint is None:
            raise RepositoryConflict("rollback_binding_mismatch")
        pins = receipt.get("runtime_and_policy_pins")
        if not isinstance(pins, dict):
            raise RepositoryConflict("rollback_binding_mismatch")
        expected_host_pins = {
            "program_digest": checkpoint["program_digest"],
            "execution_digest": checkpoint["execution_digest"],
            "document_id": checkpoint["document_id"],
            "document_revision": checkpoint["document_revision_before"],
            "runtime_id": plan["runtime_pins"]["runtime_id"],
            "runtime_role": plan["runtime_pins"]["runtime_role"],
            "host_family": plan["runtime_pins"]["host_family"],
            "host_version": plan["runtime_pins"]["host_version"],
            "package_id": plan["runtime_pins"]["host_package_id"],
            "package_version": plan["runtime_pins"]["host_package_version"],
            "package_hash": plan["runtime_pins"]["host_package_hash"],
            "capability_manifest_hash": plan["policy_pins"][
                "capability_manifest_hash"
            ],
            "operation_registry_version": plan["policy_pins"]["registry_version"],
            "operation_registry_hash": plan["policy_pins"][
                "operation_registry_hash"
            ],
            "policy_version": plan["policy_pins"]["policy_version"],
        }
        if pins != expected_host_pins:
            raise RepositoryConflict("rollback_binding_mismatch")
        record = RollbackReceiptRecord.model_validate(
            {
                "schema_version": "cad.rollback.receipt/1",
                "rollback_receipt_id": receipt["rollback_receipt_id"],
                "owner_subject": owner_subject,
                "original_receipt_id": receipt["original_receipt_id"],
                "original_receipt_digest": receipt["original_receipt_digest"],
                "program_digest": checkpoint["program_digest"],
                "original_execution_digest": checkpoint["execution_digest"],
                "original_document_revision": checkpoint[
                    "document_revision_before"
                ],
                "checkpoint_id": receipt["checkpoint_id"],
                "checkpoint_digest": receipt["checkpoint_digest"],
                "rollback_plan_id": receipt["rollback_plan_id"],
                "rollback_plan_digest": receipt["rollback_plan_digest"],
                "rollback_job_id": job["job_id"],
                "rollback_execution_digest": receipt[
                    "rollback_execution_digest"
                ],
                "document_id": receipt["document_id"],
                "document_revision_before": receipt[
                    "document_revision_before"
                ],
                "document_revision_after": receipt[
                    "document_revision_after"
                ],
                "removed_entities": receipt["removed_entities"],
                "runtime_pins": plan["runtime_pins"],
                "policy_pins": plan["policy_pins"],
                "receipt_digest": receipt["receipt_digest"],
                "created_at": receipt["created_at"],
            }
        )
        return record

    async def prepare_terminal_evidence(
        self,
        *,
        job: dict[str, Any],
        target: str,
        result: dict[str, Any] | None,
    ) -> ExecutionEvidenceEvent:
        """Create the deterministic Gateway terminal event before atomic persistence."""

        intent = await self._released_intent(job["owner_subject"], job["job_id"])
        event_id = f"evidence-terminal-{sha256(job['job_id'].encode()).hexdigest()[:32]}"
        existing = await self.phase7.get_evidence(job["owner_subject"], event_id)
        if existing is not None:
            return ExecutionEvidenceEvent.model_validate(existing)
        received_at = _now()
        outcome = (
            "rolled_back"
            if target == "succeeded" and job["kind"] == "rollback_commit"
            else "committed"
            if target == "succeeded" and job["kind"] == "program_commit"
            else "observed"
            if target == "succeeded"
            else "inconclusive"
            if target == "outcome_unknown"
            else "aborted"
        )
        result = result if isinstance(result, dict) else {}
        execution_digest = (
            result.get("rollback_execution_digest")
            or result.get("execution_digest")
            or job.get("payload", {}).get("binding", {}).get("execution_digest")
        )
        receipt_digest = result.get("receipt_digest")
        value: dict[str, Any] = {
            "schema_version": "cad.execution-evidence/1",
            "event_id": event_id,
            "owner_subject": job["owner_subject"],
            "source": "gateway",
            "source_sequence": 1,
            "job_id": job["job_id"],
            "command_id": job["command_id"],
            "intent_id": intent["intent_id"] if intent is not None else None,
            "payload_digest": (
                job["payload_hash"]
                if str(job["payload_hash"]).startswith("sha256:")
                else f"sha256:{job['payload_hash']}"
            ),
            "execution_digest": execution_digest,
            "receipt_digest": receipt_digest,
            "payload": {
                "milestone": "terminal_persisted",
                "outcome": outcome,
                "summary": "Gateway atomically persisted the terminal result and evidence",
                "details": [
                    {"key": "job_kind", "value": job["kind"]},
                    {"key": "terminal_state", "value": target},
                ],
            },
            "source_timestamp": received_at,
            "gateway_received_at": received_at,
            "event_digest": "sha256:" + "0" * 64,
        }
        value["event_digest"] = execution_evidence_digest(value)
        return ExecutionEvidenceEvent.model_validate(value)

    async def ensure_recovery_case(
        self,
        *,
        owner_subject: str,
        job: dict[str, Any],
        cause: RecoveryCause,
        evidence_event_ids: list[str] | None = None,
        latest_query_result: dict[str, Any] | None = None,
        current_state: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Create one owner-scoped case; never re-execute or release a write lock."""

        if not self.cases_enabled:
            return None
        if job.get("owner_subject") != owner_subject:
            return None
        intent = await self._released_intent(owner_subject, job["job_id"])
        if intent is None:
            return None
        existing = await self.phase7.get_recovery_case(
            owner_subject, _case_id(job["job_id"])
        )
        if existing is not None:
            return existing
        evidence_ids = evidence_event_ids
        if evidence_ids is None:
            evidence_ids = [
                item["event_id"]
                for item in await self.phase7.list_evidence(owner_subject, job["job_id"])
            ]
        timestamp = _now()
        state = {
            "device_status": "unknown",
            "document_status": (
                "unavailable" if cause == "document_unavailable" else "unknown"
            ),
        }
        if current_state is not None:
            state.update(current_state)
        missing = {
            "bounded_inconclusive": ["Exact Host receipt or abort proof is missing"],
            "document_unavailable": ["Exact DWG is unavailable for receipt lookup"],
            "evidence_conflict": ["Evidence sequence or digest is conflicting"],
            "deadline_outcome_unknown": [
                "Job deadline passed without exact commit outcome proof"
            ],
            "commit_outcome_unknown": ["Exact commit receipt or abort proof is missing"],
        }[cause]
        value = RecoveryCaseRecord.model_validate(
            {
                "schema_version": "cad.recovery-case/1",
                "case_id": _case_id(job["job_id"]),
                "owner_subject": owner_subject,
                "state": "open",
                "resolution_version": 0,
                "execution_binding_digest": intent["commit_execution_digest"],
                "intent_id": intent["intent_id"],
                "consent_id": intent.get("consent_id"),
                "job_id": job["job_id"],
                "receipt_id": None,
                "evidence_event_ids": evidence_ids[-256:],
                "missing_evidence": missing,
                "latest_query_result": latest_query_result,
                "current_state": state,
                "safe_actions": list(_SAFE_ACTIONS),
                "operator_notes": [],
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        try:
            created, _ = await self.phase7.create_recovery_case(value)
            return created
        except RepositoryConflict as error:
            if error.code != "recovery_case_conflict":
                raise
            return await self.phase7.get_recovery_case(
                owner_subject, _case_id(job["job_id"])
            )

    async def _freeze_for_conflict(self, job: dict[str, Any]) -> None:
        current = job
        try:
            if current["state"] in {
                "dispatched",
                "acknowledged",
                "running",
                "cancel_requested",
            }:
                current = (
                    await self.jobs.transition_job(
                        current["job_id"],
                        "outcome_unknown",
                        expected_version=current["state_version"],
                    )
                    or current
                )
            if current["state"] in {"reconnect_pending", "outcome_unknown"}:
                await self.jobs.transition_job(
                    current["job_id"],
                    "needs_attention",
                    expected_version=current["state_version"],
                    evidence=True,
                    error_code="evidence_conflict",
                    error_summary="Conflicting durable execution evidence requires support",
                )
        except (InvalidJobTransition, RepositoryConflict):
            latest = await self.jobs.get_job(job["owner_subject"], job["job_id"])
            if latest is None or latest["state"] != "needs_attention":
                raise

    async def _released_intent(
        self, owner_subject: str, job_id: str
    ) -> dict[str, Any] | None:
        with self.phase7.database.read_connection() as conn:
            row = conn.execute(
                "SELECT intent_id FROM execution_intents "
                "WHERE owner_subject = ? AND released_job_id = ? AND state = 'released'",
                (owner_subject, job_id),
            ).fetchone()
        if row is None:
            return None
        return await self.phase7.get_intent(owner_subject, str(row["intent_id"]))


__all__ = ["Phase7RecoveryService", "RecoveryCause"]
