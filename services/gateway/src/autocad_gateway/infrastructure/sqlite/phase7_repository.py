"""Owner-scoped Phase 7 persistence with CAS and exact-replay semantics."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from hashlib import sha256
from typing import Any, Callable, TypeVar

from autocad_contracts import (
    ConsentRecord,
    ExecutionEvidenceEvent,
    ExecutionIntentRecord,
    RecoveryCaseRecord,
    RollbackCheckpointRecord,
    RollbackPlanRecord,
    RollbackReceiptRecord,
    canonical_json,
)

from .database import SqliteDatabase
from .repositories import RepositoryConflict


_INTENT_TRANSITIONS = {
    "awaiting_approval": {
        "ready",
        "denied",
        "expired",
        "invalidated",
        "cancelled",
    },
    "ready": {"denied", "expired", "invalidated", "cancelled"},
    "released": set(),
    "denied": set(),
    "expired": set(),
    "invalidated": set(),
    "cancelled": set(),
}
_CONSENT_TRANSITIONS = {
    "requested": {"approved", "denied", "expired", "invalidated"},
    "approved": {"expired", "invalidated"},
    "denied": set(),
    "expired": set(),
    "invalidated": set(),
    "consumed": set(),
}

RecordT = TypeVar(
    "RecordT",
    ExecutionIntentRecord,
    ConsentRecord,
    ExecutionEvidenceEvent,
    RecoveryCaseRecord,
    RollbackCheckpointRecord,
    RollbackPlanRecord,
    RollbackReceiptRecord,
)


def _json(value: Any) -> str:
    return canonical_json(value)


def _job_payload_json(value: dict[str, Any]) -> str:
    try:
        encoded = canonical_json(value)
    except (TypeError, ValueError) as error:
        raise RepositoryConflict("job_payload_invalid") from error
    if len(encoded.encode("utf-8")) > 1_048_576:
        raise RepositoryConflict("job_payload_too_large")
    return encoded


def _request_fingerprint(kind: str, payload_hash: str) -> str:
    encoded = canonical_json(
        {
            "version": "cad.request/1",
            "kind": kind,
            "effect_class": "write",
            "payload_hash": payload_hash,
        }
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _load(value: str | None, default: Any = None) -> Any:
    return json.loads(value) if value is not None else default


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _dump(record: RecordT) -> dict[str, Any]:
    return record.model_dump(mode="json")


def _same(left: RecordT, right: RecordT) -> bool:
    return _dump(left) == _dump(right)


class Phase7Repository:
    """SQLite-only domain storage. It never dispatches to Agent or Host."""

    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    async def create_intent(
        self, value: ExecutionIntentRecord | dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        record = ExecutionIntentRecord.model_validate(value)
        if record.state not in {"awaiting_approval", "ready"} or record.state_version != 0:
            raise RepositoryConflict("intent_initial_state_invalid")
        if (record.required_assurance == "none") != (record.state == "ready"):
            raise RepositoryConflict("intent_initial_state_invalid")
        with self.database.transaction() as conn:
            self._require_intent_parents(conn, record)
            try:
                conn.execute(
                    """
                    INSERT INTO execution_intents(
                        intent_id, intent_version, owner_subject, actor_issuer,
                        actor_subject, action, state, state_version, device_id,
                        device_identity_generation, device_key_thumbprint, document_id,
                        expected_document_revision, program_id, program_revision,
                        program_digest, preview_id, preview_digest,
                        preview_execution_digest, preview_expires_at,
                        deterministic_receipt_id, commit_execution_digest,
                        runtime_pins_json, policy_pins_json, risk_class,
                        required_assurance, trusted_effect_summary_json,
                        idempotency_key, request_hash, intent_digest, created_at,
                        expires_at, consent_id, released_job_id
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        record.intent_id,
                        record.intent_version,
                        record.owner_subject,
                        record.actor_principal.issuer,
                        record.actor_principal.subject,
                        record.action,
                        record.state,
                        record.state_version,
                        record.device_id,
                        record.device_identity_generation,
                        record.device_key_thumbprint,
                        record.document_id,
                        record.expected_document_revision,
                        record.program_id,
                        record.program_revision,
                        record.program_digest,
                        record.preview_id,
                        record.preview_digest,
                        record.preview_execution_digest,
                        record.preview_expires_at,
                        record.deterministic_receipt_id,
                        record.commit_execution_digest,
                        _json(record.runtime_pins.model_dump(mode="json")),
                        _json(record.policy_pins.model_dump(mode="json")),
                        record.risk_class,
                        record.required_assurance,
                        _json(
                            [
                                item.model_dump(mode="json")
                                for item in record.trusted_effect_summary
                            ]
                        ),
                        record.idempotency_key,
                        record.request_hash,
                        record.intent_digest,
                        record.created_at,
                        record.expires_at,
                        record.consent_id,
                        record.released_job_id,
                    ),
                )
            except sqlite3.IntegrityError as error:
                existing = self._intent_conflict_row(conn, record)
                if existing is not None and self._same_intent_request(existing, record):
                    return _dump(existing), True
                raise RepositoryConflict("intent_conflict") from error
        return _dump(record), False

    async def get_intent(
        self, owner_subject: str, intent_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            record = self._intent(conn, owner_subject, intent_id)
        return _dump(record) if record is not None else None

    async def list_intents(
        self, owner_subject: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        self._limit(limit)
        with self.database.read_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_intents WHERE owner_subject = ? "
                "ORDER BY created_at DESC, intent_id LIMIT ?",
                (owner_subject, limit),
            ).fetchall()
        return [_dump(self._intent_record(row)) for row in rows]

    async def transition_intent(
        self,
        *,
        owner_subject: str,
        intent_id: str,
        target: str,
        expected_version: int,
        consent_id: str | None = None,
        transition_at: str | None = None,
    ) -> dict[str, Any] | None:
        if target == "released":
            raise RepositoryConflict("release_binding_required")
        if transition_at is not None:
            _timestamp(transition_at)
        with self.database.transaction() as conn:
            current = self._intent(conn, owner_subject, intent_id)
            if current is None:
                return None
            if current.state == target:
                if consent_id is not None and current.consent_id != consent_id:
                    raise RepositoryConflict("intent_transition_conflict")
                return _dump(current)
            if target not in _INTENT_TRANSITIONS[current.state]:
                raise RepositoryConflict("invalid_intent_transition")
            if current.state_version != expected_version:
                raise RepositoryConflict("cas_conflict")
            if target == "expired":
                if transition_at is None:
                    raise RepositoryConflict("transition_timestamp_required")
                if _timestamp(transition_at) < _timestamp(current.expires_at):
                    raise RepositoryConflict("intent_not_expired")
            consent = (
                self._consent(conn, owner_subject, consent_id)
                if consent_id is not None
                else None
            )
            if consent_id is not None:
                if consent is None or consent.intent_id != intent_id:
                    raise RepositoryConflict("consent_binding_mismatch")
            if target == "ready":
                if current.required_assurance == "none":
                    if consent_id is not None:
                        raise RepositoryConflict("consent_binding_mismatch")
                elif (
                    consent is None
                    or consent.state != "approved"
                    or consent.intent_version != current.intent_version
                    or consent.intent_digest != current.intent_digest
                    or consent.required_assurance != current.required_assurance
                ):
                    raise RepositoryConflict("consent_not_approved")
            updated = conn.execute(
                "UPDATE execution_intents SET state = ?, state_version = state_version + 1, "
                "consent_id = COALESCE(consent_id, ?) "
                "WHERE owner_subject = ? AND intent_id = ? AND state_version = ?",
                (target, consent_id, owner_subject, intent_id, expected_version),
            )
            if updated.rowcount != 1:
                raise RepositoryConflict("cas_conflict")
            result = self._intent(conn, owner_subject, intent_id)
        return _dump(result)

    async def release_intent(
        self,
        *,
        owner_subject: str,
        intent_id: str,
        job_id: str,
        command_id: str,
        idempotency_key: str,
        payload_hash: str,
        payload: dict[str, Any],
        deadline_at: str,
        kind: str,
        expected_intent_version: int,
        consumed_at: str,
        consent_id: str | None = None,
        expected_consent_version: int | None = None,
    ) -> dict[str, Any] | None:
        """Create the exact job and release its intent in one SQLite transaction.

        A job that predates the release is never adopted. Exact retries are
        recognized only through an already-released intent bound to that job.
        """
        _timestamp(consumed_at)
        _timestamp(deadline_at)
        for name, value in (
            ("job_id", job_id),
            ("command_id", command_id),
            ("idempotency_key", idempotency_key),
            ("payload_hash", payload_hash),
        ):
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 256
                or any(character.isspace() for character in value)
            ):
                raise RepositoryConflict(f"{name}_invalid")
        if not isinstance(payload, dict):
            raise RepositoryConflict("job_payload_invalid")
        payload_json = _job_payload_json(payload)
        request_fingerprint = _request_fingerprint(kind, payload_hash)
        with self.database.transaction() as conn:
            intent = self._intent(conn, owner_subject, intent_id)
            if intent is None:
                return None
            expected_kind = intent.action
            if kind != expected_kind:
                raise RepositoryConflict("job_kind_mismatch")
            self._require_job_binding(intent, payload)
            self._require_phase8_release_binding(
                conn,
                intent=intent,
                consent_id=consent_id,
                payload=payload,
            )
            if intent.state == "released":
                if intent.released_job_id != job_id or intent.consent_id != consent_id:
                    raise RepositoryConflict("intent_release_conflict")
                existing_job = conn.execute(
                    "SELECT * FROM jobs WHERE owner_subject = ? AND job_id = ?",
                    (owner_subject, job_id),
                ).fetchone()
                if existing_job is None:
                    raise RepositoryConflict("idempotency_state_invalid")
                if not self._same_job_material(
                    existing_job,
                    device_id=intent.device_id,
                    kind=kind,
                    command_id=command_id,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    payload_json=payload_json,
                    deadline_at=deadline_at,
                    request_fingerprint=request_fingerprint,
                ):
                    raise RepositoryConflict("job_conflict")
                return {
                    "intent": _dump(intent),
                    "job": self._job_record(existing_job),
                    "job_existing": True,
                }
            if intent.state_version != expected_intent_version:
                raise RepositoryConflict("cas_conflict")
            if (
                _timestamp(consumed_at) >= _timestamp(intent.expires_at)
                or _timestamp(consumed_at) >= _timestamp(intent.preview_expires_at)
            ):
                raise RepositoryConflict("intent_expired")
            if _timestamp(deadline_at) <= _timestamp(consumed_at):
                raise RepositoryConflict("job_deadline_invalid")

            if intent.required_assurance == "none":
                if consent_id is not None or intent.state != "ready":
                    raise RepositoryConflict("consent_binding_mismatch")
                consent = None
            else:
                if consent_id is None or expected_consent_version is None:
                    raise RepositoryConflict("consent_required")
                consent = self._consent(conn, owner_subject, consent_id)
                if (
                    consent is None
                    or consent.intent_id != intent.intent_id
                    or consent.intent_version != intent.intent_version
                    or consent.intent_digest != intent.intent_digest
                    or consent.required_assurance != intent.required_assurance
                ):
                    raise RepositoryConflict("consent_binding_mismatch")
                if consent.state != "approved":
                    raise RepositoryConflict("consent_not_approved")
                if consent.state_version != expected_consent_version:
                    raise RepositoryConflict("cas_conflict")
                if _timestamp(consumed_at) >= _timestamp(consent.expires_at):
                    raise RepositoryConflict("consent_expired")

            existing_job = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ? OR command_id = ? OR "
                "(owner_subject = ? AND device_id = ? AND idempotency_key = ?) "
                "LIMIT 1",
                (
                    job_id,
                    command_id,
                    owner_subject,
                    intent.device_id,
                    idempotency_key,
                ),
            ).fetchone()
            if existing_job is not None:
                raise RepositoryConflict("job_preexists_release")

            stale_lock = conn.execute(
                "SELECT l.job_id, j.state FROM cad_program_write_locks l "
                "JOIN jobs j ON j.job_id = l.job_id "
                "WHERE l.device_id = ? AND l.document_id = ?",
                (intent.device_id, intent.document_id),
            ).fetchone()
            active_states = {
                "queued",
                "dispatched",
                "acknowledged",
                "running",
                "cancel_requested",
                "reconnect_pending",
                "outcome_unknown",
                "needs_attention",
            }
            if stale_lock is not None and str(stale_lock["state"]) not in active_states:
                conn.execute(
                    "DELETE FROM cad_program_write_locks "
                    "WHERE device_id = ? AND document_id = ? AND job_id = ?",
                    (
                        intent.device_id,
                        intent.document_id,
                        str(stale_lock["job_id"]),
                    ),
                )
                stale_lock = None
            if stale_lock is not None and str(stale_lock["job_id"]) != job_id:
                raise RepositoryConflict("document_write_busy")

            try:
                conn.execute(
                    """
                    INSERT INTO jobs(
                        job_id, owner_subject, device_id, kind, effect_class, state,
                        state_version, deadline_at, command_id, idempotency_key,
                        payload_hash, payload_json, request_fingerprint,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'write', 'queued', 0, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        owner_subject,
                        intent.device_id,
                        kind,
                        deadline_at,
                        command_id,
                        idempotency_key,
                        payload_hash,
                        payload_json,
                        request_fingerprint,
                        consumed_at,
                        consumed_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise RepositoryConflict("job_conflict") from error
            conn.execute(
                "INSERT INTO job_events("
                "job_id, sequence, event_type, state, progress_json, error_code, "
                "result_json, created_at"
                ") VALUES (?, 1, 'state', 'queued', NULL, NULL, NULL, ?)",
                (job_id, consumed_at),
            )

            if consent is not None:
                consumed = conn.execute(
                    "UPDATE consents SET state = 'consumed', "
                    "state_version = state_version + 1, consumed_at = ? "
                    "WHERE owner_subject = ? AND consent_id = ? "
                    "AND state = 'approved' AND state_version = ?",
                    (
                        consumed_at,
                        owner_subject,
                        consent_id,
                        expected_consent_version,
                    ),
                )
                if consumed.rowcount != 1:
                    raise RepositoryConflict("cas_conflict")

            try:
                conn.execute(
                    "INSERT INTO cad_program_write_locks("
                    "device_id, document_id, job_id, created_at"
                    ") VALUES (?, ?, ?, ?)",
                    (
                        intent.device_id,
                        intent.document_id,
                        job_id,
                        consumed_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise RepositoryConflict("document_write_busy") from error

            try:
                released = conn.execute(
                    "UPDATE execution_intents SET state = 'released', "
                    "state_version = state_version + 1, consent_id = ?, released_job_id = ? "
                    "WHERE owner_subject = ? AND intent_id = ? AND state_version = ? "
                    "AND state IN ('awaiting_approval', 'ready')",
                    (
                        consent_id,
                        job_id,
                        owner_subject,
                        intent_id,
                        expected_intent_version,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise RepositoryConflict("intent_release_conflict") from error
            if released.rowcount != 1:
                raise RepositoryConflict("cas_conflict")
            result = self._intent(conn, owner_subject, intent_id)
            job = conn.execute(
                "SELECT * FROM jobs WHERE owner_subject = ? AND job_id = ?",
                (owner_subject, job_id),
            ).fetchone()
        return {
            "intent": _dump(result),
            "job": self._job_record(job),
            "job_existing": False,
        }

    async def create_consent(
        self, value: ConsentRecord | dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        record = ConsentRecord.model_validate(value)
        if record.state != "requested" or record.state_version != 0:
            raise RepositoryConflict("consent_initial_state_invalid")
        if record.required_assurance == "none":
            raise RepositoryConflict("consent_not_required")
        with self.database.transaction() as conn:
            intent = self._intent(conn, record.owner_subject, record.intent_id)
            if (
                intent is None
                or intent.intent_version != record.intent_version
                or intent.intent_digest != record.intent_digest
                or intent.required_assurance != record.required_assurance
            ):
                raise RepositoryConflict("intent_binding_mismatch")
            try:
                conn.execute(
                    """
                    INSERT INTO consents(
                        consent_id, consent_version, owner_subject, intent_id,
                        intent_version, intent_digest, required_assurance, state,
                        state_version, challenge_nonce_hash, requested_at, expires_at,
                        decided_at, decision_source, decision_principal_json,
                        decision_device_id, decision_device_identity_generation,
                        consumed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._consent_values(record),
                )
            except sqlite3.IntegrityError as error:
                existing = self._consent_conflict_row(conn, record)
                if existing is not None and self._same_consent_request(existing, record):
                    return _dump(existing), True
                raise RepositoryConflict("consent_conflict") from error
            phase8_binding = conn.execute(
                "SELECT binding_digest FROM phase8_intent_bindings "
                "WHERE owner_subject = ? AND intent_id = ?",
                (record.owner_subject, record.intent_id),
            ).fetchone()
            if phase8_binding is not None:
                conn.execute(
                    "INSERT INTO phase8_consent_bindings("
                    "consent_id, owner_subject, intent_id, binding_digest, created_at"
                    ") VALUES (?, ?, ?, ?, ?)",
                    (
                        record.consent_id,
                        record.owner_subject,
                        record.intent_id,
                        phase8_binding["binding_digest"],
                        record.requested_at,
                    ),
                )
        return _dump(record), False

    async def get_consent(
        self, owner_subject: str, consent_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            record = self._consent(conn, owner_subject, consent_id)
        return _dump(record) if record is not None else None

    async def list_consents(
        self, owner_subject: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        self._limit(limit)
        with self.database.read_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM consents WHERE owner_subject = ? "
                "ORDER BY requested_at DESC, consent_id LIMIT ?",
                (owner_subject, limit),
            ).fetchall()
        return [_dump(self._consent_record(row)) for row in rows]

    async def transition_consent(
        self,
        *,
        owner_subject: str,
        consent_id: str,
        target: str,
        expected_version: int,
        transition_at: str,
        decision_source: str | None = None,
        decision_principal: dict[str, str] | None = None,
        decision_device_id: str | None = None,
        decision_device_identity_generation: int | None = None,
    ) -> dict[str, Any] | None:
        _timestamp(transition_at)
        if target == "consumed":
            raise RepositoryConflict("consent_consume_requires_release")
        with self.database.transaction() as conn:
            current = self._consent(conn, owner_subject, consent_id)
            if current is None:
                return None
            candidate_value = current.model_dump(mode="json")
            candidate_value.update(
                {
                    "state": target,
                    "state_version": current.state_version + 1,
                    "decided_at": transition_at if target in {"approved", "denied"} else None,
                    "decision_source": (
                        decision_source if target in {"approved", "denied"} else None
                    ),
                    "decision_principal": (
                        decision_principal if target in {"approved", "denied"} else None
                    ),
                    "decision_device_id": (
                        decision_device_id if target in {"approved", "denied"} else None
                    ),
                    "decision_device_identity_generation": (
                        decision_device_identity_generation
                        if target in {"approved", "denied"}
                        else None
                    ),
                }
            )
            candidate = ConsentRecord.model_validate(candidate_value)
            if current.state == target:
                replay = candidate.model_copy(update={"state_version": current.state_version})
                replay = ConsentRecord.model_validate(replay.model_dump(mode="json"))
                if _same(current, replay):
                    return _dump(current)
                raise RepositoryConflict("consent_transition_conflict")
            if target not in _CONSENT_TRANSITIONS[current.state]:
                raise RepositoryConflict("invalid_consent_transition")
            if current.state_version != expected_version:
                raise RepositoryConflict("cas_conflict")
            if target == "expired" and _timestamp(transition_at) < _timestamp(
                current.expires_at
            ):
                raise RepositoryConflict("consent_not_expired")
            if target in {"approved", "denied"} and _timestamp(transition_at) >= _timestamp(
                current.expires_at
            ):
                raise RepositoryConflict("consent_expired")
            updated = conn.execute(
                """
                UPDATE consents SET
                    state = ?, state_version = state_version + 1, decided_at = ?,
                    decision_source = ?, decision_principal_json = ?,
                    decision_device_id = ?, decision_device_identity_generation = ?
                WHERE owner_subject = ? AND consent_id = ? AND state_version = ?
                """,
                (
                    candidate.state,
                    candidate.decided_at,
                    candidate.decision_source,
                    (
                        _json(candidate.decision_principal.model_dump(mode="json"))
                        if candidate.decision_principal is not None
                        else None
                    ),
                    candidate.decision_device_id,
                    candidate.decision_device_identity_generation,
                    owner_subject,
                    consent_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                raise RepositoryConflict("cas_conflict")
            result = self._consent(conn, owner_subject, consent_id)
        return _dump(result)

    async def append_evidence(
        self, value: ExecutionEvidenceEvent | dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        record = ExecutionEvidenceEvent.model_validate(value)
        with self.database.transaction() as conn:
            return self.insert_evidence(conn, record)

    def insert_evidence(
        self, conn: Any, value: ExecutionEvidenceEvent | dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Append evidence in an existing terminal-result transaction."""

        record = ExecutionEvidenceEvent.model_validate(value)
        job = conn.execute(
            "SELECT 1 FROM jobs WHERE owner_subject = ? AND job_id = ?",
            (record.owner_subject, record.job_id),
        ).fetchone()
        if job is None:
            raise RepositoryConflict("not_found")
        if record.intent_id is not None and self._intent(
            conn, record.owner_subject, record.intent_id
        ) is None:
            raise RepositoryConflict("not_found")
        existing = self._evidence_conflict_row(conn, record)
        if existing is not None:
            if _same(existing, record):
                return _dump(existing), True
            raise RepositoryConflict("evidence_conflict")
        latest = conn.execute(
            "SELECT MAX(source_sequence) FROM execution_evidence_events "
            "WHERE job_id = ? AND source = ?",
            (record.job_id, record.source),
        ).fetchone()[0]
        if latest is not None and record.source_sequence <= int(latest):
            raise RepositoryConflict("evidence_sequence_rejected")
        try:
            conn.execute(
                """
                INSERT INTO execution_evidence_events(
                    event_id, owner_subject, source, source_sequence, job_id,
                    command_id, intent_id, payload_digest, execution_digest,
                    receipt_digest, payload_json, source_timestamp,
                    gateway_received_at, event_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.event_id,
                    record.owner_subject,
                    record.source,
                    record.source_sequence,
                    record.job_id,
                    record.command_id,
                    record.intent_id,
                    record.payload_digest,
                    record.execution_digest,
                    record.receipt_digest,
                    _json(record.payload.model_dump(mode="json")),
                    record.source_timestamp,
                    record.gateway_received_at,
                    record.event_digest,
                ),
            )
        except sqlite3.IntegrityError as error:
            existing = self._evidence_conflict_row(conn, record)
            if existing is not None and _same(existing, record):
                return _dump(existing), True
            raise RepositoryConflict("evidence_conflict") from error
        return _dump(record), False

    async def get_evidence(
        self, owner_subject: str, event_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM execution_evidence_events "
                "WHERE owner_subject = ? AND event_id = ?",
                (owner_subject, event_id),
            ).fetchone()
        return _dump(self._evidence_record(row)) if row is not None else None

    async def list_evidence(
        self, owner_subject: str, job_id: str, *, limit: int = 256
    ) -> list[dict[str, Any]]:
        self._limit(limit, maximum=256)
        with self.database.read_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_evidence_events "
                "WHERE owner_subject = ? AND job_id = ? "
                "ORDER BY gateway_received_at, source, source_sequence LIMIT ?",
                (owner_subject, job_id, limit),
            ).fetchall()
        return [_dump(self._evidence_record(row)) for row in rows]

    async def create_recovery_case(
        self, value: RecoveryCaseRecord | dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        record = RecoveryCaseRecord.model_validate(value)
        if record.state == "resolved" or record.resolution_version != 0:
            raise RepositoryConflict("recovery_initial_state_invalid")
        with self.database.transaction() as conn:
            self._require_recovery_parents(conn, record)
            return self._insert_recovery(conn, record)

    async def get_recovery_case(
        self, owner_subject: str, case_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            record = self._recovery(conn, owner_subject, case_id)
        return _dump(record) if record is not None else None

    async def list_recovery_cases(
        self, owner_subject: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        self._limit(limit)
        with self.database.read_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM recovery_cases WHERE owner_subject = ? "
                "ORDER BY updated_at DESC, case_id LIMIT ?",
                (owner_subject, limit),
            ).fetchall()
        return [_dump(self._recovery_record(row)) for row in rows]

    async def resolve_recovery_case(
        self,
        *,
        owner_subject: str,
        case_id: str,
        expected_version: int,
        resolution: str,
        resolved_at: str,
        operator_note: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        _timestamp(resolved_at)
        with self.database.transaction() as conn:
            current = self._recovery(conn, owner_subject, case_id)
            if current is None:
                return None
            if current.state == "resolved":
                same_note = operator_note is None or (
                    bool(current.operator_notes)
                    and current.operator_notes[-1].model_dump(mode="json") == operator_note
                )
                if (
                    current.resolution == resolution
                    and current.resolved_at == resolved_at
                    and same_note
                ):
                    return _dump(current)
                raise RepositoryConflict("recovery_resolution_conflict")
            notes = [note.model_dump(mode="json") for note in current.operator_notes]
            if operator_note is not None:
                notes.append(operator_note)
            candidate_value = current.model_dump(mode="json")
            candidate_value.update(
                {
                    "state": "resolved",
                    "resolution_version": current.resolution_version + 1,
                    "resolution": resolution,
                    "resolved_at": resolved_at,
                    "updated_at": resolved_at,
                    "operator_notes": notes,
                }
            )
            candidate = RecoveryCaseRecord.model_validate(candidate_value)
            if current.resolution_version != expected_version:
                raise RepositoryConflict("cas_conflict")
            updated = conn.execute(
                """
                UPDATE recovery_cases SET
                    state = 'resolved', resolution_version = resolution_version + 1,
                    resolution = ?, operator_notes_json = ?, updated_at = ?,
                    resolved_at = ?
                WHERE owner_subject = ? AND case_id = ? AND resolution_version = ?
                    AND state <> 'resolved'
                """,
                (
                    candidate.resolution,
                    _json(
                        [note.model_dump(mode="json") for note in candidate.operator_notes]
                    ),
                    candidate.updated_at,
                    candidate.resolved_at,
                    owner_subject,
                    case_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                raise RepositoryConflict("cas_conflict")
            result = self._recovery(conn, owner_subject, case_id)
        return _dump(result)

    async def create_checkpoint(
        self, value: RollbackCheckpointRecord | dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        record = RollbackCheckpointRecord.model_validate(value)
        with self.database.transaction() as conn:
            receipt = conn.execute(
                "SELECT * FROM cad_execution_receipts "
                "WHERE owner_subject = ? AND receipt_id = ?",
                (record.owner_subject, record.original_receipt_id),
            ).fetchone()
            if (
                receipt is None
                or str(receipt["receipt_digest"]) != record.original_receipt_digest
                or str(receipt["program_id"]) != record.program_id
                or int(receipt["program_revision"]) != record.program_revision
                or str(receipt["program_digest"]) != record.program_digest
                or str(receipt["preview_id"]) != record.preview_id
                or str(receipt["execution_digest"]) != record.execution_digest
                or str(receipt["document_id"]) != record.document_id
                or str(receipt["document_revision_before"])
                != record.document_revision_before
                or str(receipt["document_revision_after"])
                != record.document_revision_after
            ):
                raise RepositoryConflict("receipt_binding_mismatch")
            try:
                conn.execute(
                    """
                    INSERT INTO rollback_checkpoints(
                        checkpoint_id, owner_subject, original_receipt_id,
                        original_receipt_digest, program_id, program_revision,
                        program_digest, preview_id, preview_digest, execution_digest,
                        document_id, document_revision_before, document_revision_after,
                        created_entities_json, non_entity_object_created,
                        runtime_pins_json, policy_pins_json, checkpoint_digest, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.checkpoint_id,
                        record.owner_subject,
                        record.original_receipt_id,
                        record.original_receipt_digest,
                        record.program_id,
                        record.program_revision,
                        record.program_digest,
                        record.preview_id,
                        record.preview_digest,
                        record.execution_digest,
                        record.document_id,
                        record.document_revision_before,
                        record.document_revision_after,
                        _json(
                            [item.model_dump(mode="json") for item in record.created_entities]
                        ),
                        int(record.non_entity_object_created),
                        _json(record.runtime_pins.model_dump(mode="json")),
                        _json(record.policy_pins.model_dump(mode="json")),
                        record.checkpoint_digest,
                        record.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                existing = self._checkpoint_conflict_row(conn, record)
                if existing is not None and _same(existing, record):
                    return _dump(existing), True
                raise RepositoryConflict("checkpoint_conflict") from error
        return _dump(record), False

    async def get_checkpoint(
        self, owner_subject: str, checkpoint_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            record = self._checkpoint(conn, owner_subject, checkpoint_id)
        return _dump(record) if record is not None else None

    async def list_checkpoints(
        self, owner_subject: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        return await self._list_records(
            owner_subject,
            table="rollback_checkpoints",
            order="created_at DESC, checkpoint_id",
            parser=self._checkpoint_record,
            limit=limit,
        )

    async def create_rollback_plan(
        self, value: RollbackPlanRecord | dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        record = RollbackPlanRecord.model_validate(value)
        with self.database.transaction() as conn:
            checkpoint = self._checkpoint(
                conn, record.owner_subject, record.checkpoint_id
            )
            if (
                checkpoint is None
                or checkpoint.checkpoint_digest != record.checkpoint_digest
                or checkpoint.original_receipt_id != record.original_receipt_id
                or checkpoint.document_id != record.document_id
                or {item.handle for item in checkpoint.created_entities}
                != set(record.entity_handles)
            ):
                raise RepositoryConflict("checkpoint_binding_mismatch")
            try:
                conn.execute(
                    """
                    INSERT INTO rollback_plans(
                        plan_id, owner_subject, checkpoint_id, checkpoint_digest,
                        original_receipt_id, document_id, current_document_revision,
                        rollback_execution_digest, entity_handles_json, conflicts_json,
                        eligible, runtime_pins_json, policy_pins_json, plan_digest,
                        created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.plan_id,
                        record.owner_subject,
                        record.checkpoint_id,
                        record.checkpoint_digest,
                        record.original_receipt_id,
                        record.document_id,
                        record.current_document_revision,
                        record.rollback_execution_digest,
                        _json(record.entity_handles),
                        _json([item.model_dump(mode="json") for item in record.conflicts]),
                        int(record.eligible),
                        _json(record.runtime_pins.model_dump(mode="json")),
                        _json(record.policy_pins.model_dump(mode="json")),
                        record.plan_digest,
                        record.created_at,
                        record.expires_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                existing = self._plan_conflict_row(conn, record)
                if existing is not None and _same(existing, record):
                    return _dump(existing), True
                raise RepositoryConflict("rollback_plan_conflict") from error
        return _dump(record), False

    async def get_rollback_plan(
        self, owner_subject: str, plan_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            record = self._plan(conn, owner_subject, plan_id)
        return _dump(record) if record is not None else None

    async def list_rollback_plans(
        self, owner_subject: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        return await self._list_records(
            owner_subject,
            table="rollback_plans",
            order="created_at DESC, plan_id",
            parser=self._plan_record,
            limit=limit,
        )

    async def create_rollback_receipt(
        self, value: RollbackReceiptRecord | dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        record = RollbackReceiptRecord.model_validate(value)
        with self.database.transaction() as conn:
            return self.insert_rollback_receipt(conn, record)

    def insert_rollback_receipt(
        self, conn: Any, value: RollbackReceiptRecord | dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Insert a receipt using the caller's transaction when supplied."""

        record = RollbackReceiptRecord.model_validate(value)
        plan = self._plan(conn, record.owner_subject, record.rollback_plan_id)
        checkpoint = self._checkpoint(conn, record.owner_subject, record.checkpoint_id)
        job = conn.execute(
            "SELECT 1 FROM jobs WHERE owner_subject = ? AND job_id = ? "
            "AND effect_class = 'write'",
            (record.owner_subject, record.rollback_job_id),
        ).fetchone()
        if (
            plan is None
            or checkpoint is None
            or job is None
            or plan.plan_digest != record.rollback_plan_digest
            or plan.rollback_execution_digest != record.rollback_execution_digest
            or checkpoint.checkpoint_digest != record.checkpoint_digest
            or checkpoint.original_receipt_id != record.original_receipt_id
            or checkpoint.original_receipt_digest != record.original_receipt_digest
            or checkpoint.program_digest != record.program_digest
            or checkpoint.execution_digest != record.original_execution_digest
            or checkpoint.document_revision_before
            != record.original_document_revision
            or checkpoint.document_id != record.document_id
            or set(plan.entity_handles)
            != {item.handle for item in record.removed_entities}
        ):
            raise RepositoryConflict("rollback_binding_mismatch")
        try:
            conn.execute(
                """
                INSERT INTO rollback_receipts(
                    rollback_receipt_id, owner_subject, original_receipt_id,
                    original_receipt_digest, program_digest,
                    original_execution_digest, original_document_revision,
                    checkpoint_id, checkpoint_digest,
                    rollback_plan_id, rollback_plan_digest, rollback_job_id,
                    rollback_execution_digest, document_id,
                    document_revision_before, document_revision_after,
                    removed_entities_json, runtime_pins_json, policy_pins_json,
                    receipt_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.rollback_receipt_id,
                    record.owner_subject,
                    record.original_receipt_id,
                    record.original_receipt_digest,
                    record.program_digest,
                    record.original_execution_digest,
                    record.original_document_revision,
                    record.checkpoint_id,
                    record.checkpoint_digest,
                    record.rollback_plan_id,
                    record.rollback_plan_digest,
                    record.rollback_job_id,
                    record.rollback_execution_digest,
                    record.document_id,
                    record.document_revision_before,
                    record.document_revision_after,
                    _json([item.model_dump(mode="json") for item in record.removed_entities]),
                    _json(record.runtime_pins.model_dump(mode="json")),
                    _json(record.policy_pins.model_dump(mode="json")),
                    record.receipt_digest,
                    record.created_at,
                ),
            )
        except sqlite3.IntegrityError as error:
            existing = self._rollback_receipt_conflict_row(conn, record)
            if existing is not None and _same(existing, record):
                return _dump(existing), True
            raise RepositoryConflict("rollback_receipt_conflict") from error
        return _dump(record), False

    async def get_rollback_receipt(
        self, owner_subject: str, rollback_receipt_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            record = self._rollback_receipt(
                conn, owner_subject, rollback_receipt_id
            )
        return _dump(record) if record is not None else None

    async def list_rollback_receipts(
        self, owner_subject: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        return await self._list_records(
            owner_subject,
            table="rollback_receipts",
            order="created_at DESC, rollback_receipt_id",
            parser=self._rollback_receipt_record,
            limit=limit,
        )

    @staticmethod
    def _require_job_binding(
        intent: ExecutionIntentRecord, payload: dict[str, Any]
    ) -> None:
        if intent.action == "rollback_commit":
            binding = payload.get("binding")
            arguments = payload.get("arguments")
            if (
                payload.get("kind") != "rollback_commit"
                or payload.get("effect_class") != "write"
                or payload.get("intent_id") != intent.intent_id
                or payload.get("intent_digest") != intent.intent_digest
                or not isinstance(binding, dict)
                or binding.get("program_digest") != intent.program_digest
                or binding.get("execution_digest") != intent.commit_execution_digest
                or binding.get("document_id") != intent.document_id
                or binding.get("document_revision")
                != intent.expected_document_revision
                or not isinstance(arguments, dict)
                or arguments.get("rollback_receipt_id")
                != intent.deterministic_receipt_id
            ):
                raise RepositoryConflict("job_binding_mismatch")
            return
        execution = payload.get("execution")
        if not isinstance(execution, dict):
            raise RepositoryConflict("job_binding_mismatch")
        expected = {
            "intent_id": intent.intent_id,
            "intent_digest": intent.intent_digest,
            "program_digest": intent.program_digest,
            "execution_digest": intent.commit_execution_digest,
            "document_id": intent.document_id,
            "expected_document_revision": intent.expected_document_revision,
            "preview_id": intent.preview_id,
            "preview_digest": intent.preview_digest,
            "receipt_id": intent.deterministic_receipt_id,
        }
        if any(execution.get(key) != value for key, value in expected.items()):
            raise RepositoryConflict("job_binding_mismatch")

    @staticmethod
    def _require_phase8_release_binding(
        conn: Any,
        *,
        intent: ExecutionIntentRecord,
        consent_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        binding = conn.execute(
            "SELECT * FROM phase8_intent_bindings "
            "WHERE owner_subject = ? AND intent_id = ?",
            (intent.owner_subject, intent.intent_id),
        ).fetchone()
        if binding is None:
            return
        execution = payload.get("execution")
        expected = {
            "source_digest": binding["source_digest"],
            "semantic_digest": binding["semantic_digest"],
            "plan_digest": binding["plan_digest"],
            "expansion_digest": binding["expansion_digest"],
            "effect_digest": binding["effect_digest"],
            "target_set_digest": binding["target_set_digest"],
            "reference_digest": binding["reference_digest"],
            "compiler_hash": binding["compiler_hash"],
            "risk_class": binding["risk_class"],
            "trusted_effect_summary": _load(
                binding["trusted_effect_summary_json"]
            ),
            "rollout_policy_digest": binding["rollout_policy_digest"],
            "rollout_policy_epoch": binding["rollout_policy_epoch"],
            "phase8_binding_digest": binding["binding_digest"],
        }
        if not isinstance(execution, dict) or any(
            execution.get(key) != value for key, value in expected.items()
        ):
            raise RepositoryConflict("job_binding_mismatch")
        if intent.required_assurance == "none":
            if consent_id is not None:
                raise RepositoryConflict("consent_binding_mismatch")
            return
        consent_binding = conn.execute(
            "SELECT * FROM phase8_consent_bindings "
            "WHERE owner_subject = ? AND consent_id = ? AND intent_id = ?",
            (intent.owner_subject, consent_id, intent.intent_id),
        ).fetchone()
        if (
            consent_binding is None
            or consent_binding["binding_digest"] != binding["binding_digest"]
        ):
            raise RepositoryConflict("consent_binding_mismatch")

    @staticmethod
    def _same_job_material(
        row: Any,
        *,
        device_id: str,
        kind: str,
        command_id: str,
        idempotency_key: str,
        payload_hash: str,
        payload_json: str,
        deadline_at: str,
        request_fingerprint: str,
    ) -> bool:
        return (
            str(row["device_id"]) == device_id
            and str(row["kind"]) == kind
            and str(row["effect_class"]) == "write"
            and str(row["command_id"]) == command_id
            and str(row["idempotency_key"]) == idempotency_key
            and str(row["payload_hash"]) == payload_hash
            and str(row["payload_json"]) == payload_json
            and row["deadline_at"] == deadline_at
            and row["request_fingerprint"] == request_fingerprint
        )

    @staticmethod
    def _job_record(row: Any) -> dict[str, Any]:
        return {
            "job_id": str(row["job_id"]),
            "owner_subject": str(row["owner_subject"]),
            "device_id": str(row["device_id"]),
            "kind": str(row["kind"]),
            "effect_class": str(row["effect_class"]),
            "state": str(row["state"]),
            "state_version": int(row["state_version"]),
            "deadline_at": row["deadline_at"],
            "command_id": str(row["command_id"]),
            "idempotency_key": str(row["idempotency_key"]),
            "payload_hash": str(row["payload_hash"]),
            "request_fingerprint": row["request_fingerprint"],
            "payload": _load(row["payload_json"]),
            "progress": _load(row["progress_json"]),
            "last_agent_sequence": int(row["last_agent_sequence"]),
            "result": _load(row["result_json"]),
            "error_code": row["error_code"],
            "error_summary": row["error_summary"],
            "cancel_requested_at": row["cancel_requested_at"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _limit(limit: int, *, maximum: int = 100) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
            raise RepositoryConflict("limit_invalid")

    @staticmethod
    def _same_intent_request(
        existing: ExecutionIntentRecord, requested: ExecutionIntentRecord
    ) -> bool:
        return (
            existing.intent_id == requested.intent_id
            and existing.owner_subject == requested.owner_subject
            and existing.idempotency_key == requested.idempotency_key
            and existing.request_hash == requested.request_hash
            and existing.intent_digest == requested.intent_digest
        )

    @staticmethod
    def _same_consent_request(
        existing: ConsentRecord, requested: ConsentRecord
    ) -> bool:
        mutable = {
            "state",
            "state_version",
            "decided_at",
            "decision_source",
            "decision_principal",
            "decision_device_id",
            "decision_device_identity_generation",
            "consumed_at",
        }
        left = existing.model_dump(mode="json")
        right = requested.model_dump(mode="json")
        for field in mutable:
            left.pop(field, None)
            right.pop(field, None)
        return left == right

    async def _list_records(
        self,
        owner_subject: str,
        *,
        table: str,
        order: str,
        parser: Callable[[Any], RecordT],
        limit: int,
    ) -> list[dict[str, Any]]:
        self._limit(limit)
        with self.database.read_connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE owner_subject = ? ORDER BY {order} LIMIT ?",
                (owner_subject, limit),
            ).fetchall()
        return [_dump(parser(row)) for row in rows]

    @staticmethod
    def _require_intent_parents(conn: Any, record: ExecutionIntentRecord) -> None:
        parents = (
            conn.execute(
                "SELECT 1 FROM devices WHERE owner_subject = ? AND device_id = ?",
                (record.owner_subject, record.device_id),
            ).fetchone(),
            conn.execute(
                "SELECT 1 FROM cad_program_revisions WHERE owner_subject = ? "
                "AND program_id = ? AND revision = ? AND program_digest = ?",
                (
                    record.owner_subject,
                    record.program_id,
                    record.program_revision,
                    record.program_digest,
                ),
            ).fetchone(),
            conn.execute(
                "SELECT 1 FROM cad_previews WHERE owner_subject = ? AND preview_id = ? "
                "AND program_id = ? AND program_revision = ? "
                "AND preview_digest = ? AND execution_digest = ?",
                (
                    record.owner_subject,
                    record.preview_id,
                    record.program_id,
                    record.program_revision,
                    record.preview_digest,
                    record.preview_execution_digest,
                ),
            ).fetchone(),
        )
        if any(parent is None for parent in parents):
            raise RepositoryConflict("not_found")

    @staticmethod
    def _require_recovery_parents(conn: Any, record: RecoveryCaseRecord) -> None:
        checks = [
            conn.execute(
                "SELECT 1 FROM execution_intents WHERE owner_subject = ? AND intent_id = ?",
                (record.owner_subject, record.intent_id),
            ).fetchone(),
            conn.execute(
                "SELECT 1 FROM jobs WHERE owner_subject = ? AND job_id = ?",
                (record.owner_subject, record.job_id),
            ).fetchone(),
        ]
        if record.consent_id is not None:
            checks.append(
                conn.execute(
                    "SELECT 1 FROM consents WHERE owner_subject = ? AND consent_id = ?",
                    (record.owner_subject, record.consent_id),
                ).fetchone()
            )
        if record.receipt_id is not None:
            checks.append(
                conn.execute(
                    "SELECT 1 FROM cad_execution_receipts "
                    "WHERE owner_subject = ? AND receipt_id = ?",
                    (record.owner_subject, record.receipt_id),
                ).fetchone()
            )
        if any(check is None for check in checks):
            raise RepositoryConflict("not_found")

    def _insert_recovery(
        self, conn: Any, record: RecoveryCaseRecord
    ) -> tuple[dict[str, Any], bool]:
        try:
            conn.execute(
                """
                INSERT INTO recovery_cases(
                    case_id, owner_subject, state, resolution_version,
                    execution_binding_digest, intent_id, consent_id, job_id,
                    receipt_id, evidence_event_ids_json, missing_evidence_json,
                    latest_query_result_json, current_state_json, safe_actions_json,
                    resolution, operator_notes_json, created_at, updated_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.case_id,
                    record.owner_subject,
                    record.state,
                    record.resolution_version,
                    record.execution_binding_digest,
                    record.intent_id,
                    record.consent_id,
                    record.job_id,
                    record.receipt_id,
                    _json(record.evidence_event_ids),
                    _json(record.missing_evidence),
                    (
                        _json(record.latest_query_result.model_dump(mode="json"))
                        if record.latest_query_result is not None
                        else None
                    ),
                    _json(record.current_state.model_dump(mode="json")),
                    _json(record.safe_actions),
                    record.resolution,
                    _json([note.model_dump(mode="json") for note in record.operator_notes]),
                    record.created_at,
                    record.updated_at,
                    record.resolved_at,
                ),
            )
        except sqlite3.IntegrityError as error:
            existing = self._recovery(conn, record.owner_subject, record.case_id)
            if existing is not None and _same(existing, record):
                return _dump(existing), True
            raise RepositoryConflict("recovery_case_conflict") from error
        return _dump(record), False

    @staticmethod
    def _consent_values(record: ConsentRecord) -> tuple[Any, ...]:
        return (
            record.consent_id,
            record.consent_version,
            record.owner_subject,
            record.intent_id,
            record.intent_version,
            record.intent_digest,
            record.required_assurance,
            record.state,
            record.state_version,
            record.challenge_nonce_hash,
            record.requested_at,
            record.expires_at,
            record.decided_at,
            record.decision_source,
            (
                _json(record.decision_principal.model_dump(mode="json"))
                if record.decision_principal is not None
                else None
            ),
            record.decision_device_id,
            record.decision_device_identity_generation,
            record.consumed_at,
        )

    @classmethod
    def _intent(cls, conn: Any, owner: str, record_id: str) -> ExecutionIntentRecord | None:
        row = conn.execute(
            "SELECT * FROM execution_intents WHERE owner_subject = ? AND intent_id = ?",
            (owner, record_id),
        ).fetchone()
        return cls._intent_record(row) if row is not None else None

    @staticmethod
    def _intent_record(row: Any) -> ExecutionIntentRecord:
        return ExecutionIntentRecord.model_validate(
            {
                "schema_version": "cad.execution-intent/1",
                "intent_id": row["intent_id"],
                "intent_version": row["intent_version"],
                "owner_subject": row["owner_subject"],
                "actor_principal": {
                    "issuer": row["actor_issuer"],
                    "subject": row["actor_subject"],
                },
                "action": row["action"],
                "state": row["state"],
                "state_version": row["state_version"],
                "device_id": row["device_id"],
                "device_identity_generation": row["device_identity_generation"],
                "device_key_thumbprint": row["device_key_thumbprint"],
                "document_id": row["document_id"],
                "expected_document_revision": row["expected_document_revision"],
                "program_id": row["program_id"],
                "program_revision": row["program_revision"],
                "program_digest": row["program_digest"],
                "preview_id": row["preview_id"],
                "preview_digest": row["preview_digest"],
                "preview_execution_digest": row["preview_execution_digest"],
                "preview_expires_at": row["preview_expires_at"],
                "deterministic_receipt_id": row["deterministic_receipt_id"],
                "commit_execution_digest": row["commit_execution_digest"],
                "runtime_pins": _load(row["runtime_pins_json"]),
                "policy_pins": _load(row["policy_pins_json"]),
                "risk_class": row["risk_class"],
                "required_assurance": row["required_assurance"],
                "trusted_effect_summary": _load(row["trusted_effect_summary_json"]),
                "idempotency_key": row["idempotency_key"],
                "request_hash": row["request_hash"],
                "intent_digest": row["intent_digest"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "consent_id": row["consent_id"],
                "released_job_id": row["released_job_id"],
            }
        )

    @classmethod
    def _intent_conflict_row(
        cls, conn: Any, record: ExecutionIntentRecord
    ) -> ExecutionIntentRecord | None:
        row = conn.execute(
            "SELECT * FROM execution_intents WHERE intent_id = ? "
            "OR (owner_subject = ? AND idempotency_key = ?) "
            "OR (owner_subject = ? AND intent_digest = ?) LIMIT 1",
            (
                record.intent_id,
                record.owner_subject,
                record.idempotency_key,
                record.owner_subject,
                record.intent_digest,
            ),
        ).fetchone()
        return cls._intent_record(row) if row is not None else None

    @classmethod
    def _consent(cls, conn: Any, owner: str, record_id: str) -> ConsentRecord | None:
        row = conn.execute(
            "SELECT * FROM consents WHERE owner_subject = ? AND consent_id = ?",
            (owner, record_id),
        ).fetchone()
        return cls._consent_record(row) if row is not None else None

    @staticmethod
    def _consent_record(row: Any) -> ConsentRecord:
        return ConsentRecord.model_validate(
            {
                "schema_version": "cad.consent/1",
                "consent_id": row["consent_id"],
                "consent_version": row["consent_version"],
                "owner_subject": row["owner_subject"],
                "intent_id": row["intent_id"],
                "intent_version": row["intent_version"],
                "intent_digest": row["intent_digest"],
                "required_assurance": row["required_assurance"],
                "state": row["state"],
                "state_version": row["state_version"],
                "challenge_nonce_hash": row["challenge_nonce_hash"],
                "requested_at": row["requested_at"],
                "expires_at": row["expires_at"],
                "decided_at": row["decided_at"],
                "decision_source": row["decision_source"],
                "decision_principal": _load(row["decision_principal_json"]),
                "decision_device_id": row["decision_device_id"],
                "decision_device_identity_generation": row[
                    "decision_device_identity_generation"
                ],
                "consumed_at": row["consumed_at"],
            }
        )

    @classmethod
    def _consent_conflict_row(
        cls, conn: Any, record: ConsentRecord
    ) -> ConsentRecord | None:
        row = conn.execute(
            "SELECT * FROM consents WHERE consent_id = ? OR "
            "(intent_id = ? AND intent_version = ? AND required_assurance = ?) LIMIT 1",
            (
                record.consent_id,
                record.intent_id,
                record.intent_version,
                record.required_assurance,
            ),
        ).fetchone()
        return cls._consent_record(row) if row is not None else None

    @staticmethod
    def _evidence_record(row: Any) -> ExecutionEvidenceEvent:
        return ExecutionEvidenceEvent.model_validate(
            {
                "schema_version": "cad.execution-evidence/1",
                "event_id": row["event_id"],
                "owner_subject": row["owner_subject"],
                "source": row["source"],
                "source_sequence": row["source_sequence"],
                "job_id": row["job_id"],
                "command_id": row["command_id"],
                "intent_id": row["intent_id"],
                "payload_digest": row["payload_digest"],
                "execution_digest": row["execution_digest"],
                "receipt_digest": row["receipt_digest"],
                "payload": _load(row["payload_json"]),
                "source_timestamp": row["source_timestamp"],
                "gateway_received_at": row["gateway_received_at"],
                "event_digest": row["event_digest"],
            }
        )

    @classmethod
    def _evidence_conflict_row(
        cls, conn: Any, record: ExecutionEvidenceEvent
    ) -> ExecutionEvidenceEvent | None:
        row = conn.execute(
            "SELECT * FROM execution_evidence_events WHERE event_id = ? OR "
            "(job_id = ? AND source = ? AND source_sequence = ?) LIMIT 1",
            (
                record.event_id,
                record.job_id,
                record.source,
                record.source_sequence,
            ),
        ).fetchone()
        return cls._evidence_record(row) if row is not None else None

    @classmethod
    def _recovery(cls, conn: Any, owner: str, record_id: str) -> RecoveryCaseRecord | None:
        row = conn.execute(
            "SELECT * FROM recovery_cases WHERE owner_subject = ? AND case_id = ?",
            (owner, record_id),
        ).fetchone()
        return cls._recovery_record(row) if row is not None else None

    @staticmethod
    def _recovery_record(row: Any) -> RecoveryCaseRecord:
        return RecoveryCaseRecord.model_validate(
            {
                "schema_version": "cad.recovery-case/1",
                "case_id": row["case_id"],
                "owner_subject": row["owner_subject"],
                "state": row["state"],
                "resolution_version": row["resolution_version"],
                "execution_binding_digest": row["execution_binding_digest"],
                "intent_id": row["intent_id"],
                "consent_id": row["consent_id"],
                "job_id": row["job_id"],
                "receipt_id": row["receipt_id"],
                "evidence_event_ids": _load(row["evidence_event_ids_json"], []),
                "missing_evidence": _load(row["missing_evidence_json"], []),
                "latest_query_result": _load(row["latest_query_result_json"]),
                "current_state": _load(row["current_state_json"]),
                "safe_actions": _load(row["safe_actions_json"], []),
                "resolution": row["resolution"],
                "operator_notes": _load(row["operator_notes_json"], []),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "resolved_at": row["resolved_at"],
            }
        )

    @classmethod
    def _checkpoint(
        cls, conn: Any, owner: str, record_id: str
    ) -> RollbackCheckpointRecord | None:
        row = conn.execute(
            "SELECT * FROM rollback_checkpoints "
            "WHERE owner_subject = ? AND checkpoint_id = ?",
            (owner, record_id),
        ).fetchone()
        return cls._checkpoint_record(row) if row is not None else None

    @staticmethod
    def _checkpoint_record(row: Any) -> RollbackCheckpointRecord:
        return RollbackCheckpointRecord.model_validate(
            {
                "schema_version": "cad.rollback.checkpoint/1",
                "checkpoint_id": row["checkpoint_id"],
                "owner_subject": row["owner_subject"],
                "original_receipt_id": row["original_receipt_id"],
                "original_receipt_digest": row["original_receipt_digest"],
                "program_id": row["program_id"],
                "program_revision": row["program_revision"],
                "program_digest": row["program_digest"],
                "preview_id": row["preview_id"],
                "preview_digest": row["preview_digest"],
                "execution_digest": row["execution_digest"],
                "document_id": row["document_id"],
                "document_revision_before": row["document_revision_before"],
                "document_revision_after": row["document_revision_after"],
                "created_entities": _load(row["created_entities_json"]),
                "non_entity_object_created": bool(row["non_entity_object_created"]),
                "runtime_pins": _load(row["runtime_pins_json"]),
                "policy_pins": _load(row["policy_pins_json"]),
                "checkpoint_digest": row["checkpoint_digest"],
                "created_at": row["created_at"],
            }
        )

    @classmethod
    def _checkpoint_conflict_row(
        cls, conn: Any, record: RollbackCheckpointRecord
    ) -> RollbackCheckpointRecord | None:
        row = conn.execute(
            "SELECT * FROM rollback_checkpoints WHERE checkpoint_id = ? "
            "OR original_receipt_id = ? OR checkpoint_digest = ? LIMIT 1",
            (
                record.checkpoint_id,
                record.original_receipt_id,
                record.checkpoint_digest,
            ),
        ).fetchone()
        return cls._checkpoint_record(row) if row is not None else None

    @classmethod
    def _plan(cls, conn: Any, owner: str, record_id: str) -> RollbackPlanRecord | None:
        row = conn.execute(
            "SELECT * FROM rollback_plans WHERE owner_subject = ? AND plan_id = ?",
            (owner, record_id),
        ).fetchone()
        return cls._plan_record(row) if row is not None else None

    @staticmethod
    def _plan_record(row: Any) -> RollbackPlanRecord:
        return RollbackPlanRecord.model_validate(
            {
                "schema_version": "cad.rollback.plan/1",
                "plan_id": row["plan_id"],
                "owner_subject": row["owner_subject"],
                "checkpoint_id": row["checkpoint_id"],
                "checkpoint_digest": row["checkpoint_digest"],
                "original_receipt_id": row["original_receipt_id"],
                "document_id": row["document_id"],
                "current_document_revision": row["current_document_revision"],
                "rollback_execution_digest": row["rollback_execution_digest"],
                "entity_handles": _load(row["entity_handles_json"]),
                "conflicts": _load(row["conflicts_json"], []),
                "eligible": bool(row["eligible"]),
                "runtime_pins": _load(row["runtime_pins_json"]),
                "policy_pins": _load(row["policy_pins_json"]),
                "plan_digest": row["plan_digest"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
            }
        )

    @classmethod
    def _plan_conflict_row(
        cls, conn: Any, record: RollbackPlanRecord
    ) -> RollbackPlanRecord | None:
        row = conn.execute(
            "SELECT * FROM rollback_plans WHERE plan_id = ? OR plan_digest = ? "
            "OR rollback_execution_digest = ? "
            "OR (checkpoint_id = ? AND current_document_revision = ?) LIMIT 1",
            (
                record.plan_id,
                record.plan_digest,
                record.rollback_execution_digest,
                record.checkpoint_id,
                record.current_document_revision,
            ),
        ).fetchone()
        return cls._plan_record(row) if row is not None else None

    @classmethod
    def _rollback_receipt(
        cls, conn: Any, owner: str, record_id: str
    ) -> RollbackReceiptRecord | None:
        row = conn.execute(
            "SELECT * FROM rollback_receipts "
            "WHERE owner_subject = ? AND rollback_receipt_id = ?",
            (owner, record_id),
        ).fetchone()
        return cls._rollback_receipt_record(row) if row is not None else None

    @staticmethod
    def _rollback_receipt_record(row: Any) -> RollbackReceiptRecord:
        return RollbackReceiptRecord.model_validate(
            {
                "schema_version": "cad.rollback.receipt/1",
                "rollback_receipt_id": row["rollback_receipt_id"],
                "owner_subject": row["owner_subject"],
                "original_receipt_id": row["original_receipt_id"],
                "original_receipt_digest": row["original_receipt_digest"],
                "program_digest": row["program_digest"],
                "original_execution_digest": row["original_execution_digest"],
                "original_document_revision": row[
                    "original_document_revision"
                ],
                "checkpoint_id": row["checkpoint_id"],
                "checkpoint_digest": row["checkpoint_digest"],
                "rollback_plan_id": row["rollback_plan_id"],
                "rollback_plan_digest": row["rollback_plan_digest"],
                "rollback_job_id": row["rollback_job_id"],
                "rollback_execution_digest": row["rollback_execution_digest"],
                "document_id": row["document_id"],
                "document_revision_before": row["document_revision_before"],
                "document_revision_after": row["document_revision_after"],
                "removed_entities": _load(row["removed_entities_json"]),
                "runtime_pins": _load(row["runtime_pins_json"]),
                "policy_pins": _load(row["policy_pins_json"]),
                "receipt_digest": row["receipt_digest"],
                "created_at": row["created_at"],
            }
        )

    @classmethod
    def _rollback_receipt_conflict_row(
        cls, conn: Any, record: RollbackReceiptRecord
    ) -> RollbackReceiptRecord | None:
        row = conn.execute(
            "SELECT * FROM rollback_receipts WHERE rollback_receipt_id = ? "
            "OR rollback_plan_id = ? OR rollback_job_id = ? "
            "OR rollback_execution_digest = ? OR receipt_digest = ? LIMIT 1",
            (
                record.rollback_receipt_id,
                record.rollback_plan_id,
                record.rollback_job_id,
                record.rollback_execution_digest,
                record.receipt_digest,
            ),
        ).fetchone()
        return cls._rollback_receipt_record(row) if row is not None else None


__all__ = ["Phase7Repository"]
