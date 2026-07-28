"""Owner-scoped Phase 6 CAD Program persistence and atomic materialization."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Callable

from autocad_contracts import (
    RollbackCheckpointRecord,
    canonical_json,
    canonical_preview_digest,
    validate_bounded_json,
)

from ...domain.jobs import is_terminal, validate_transition
from ...program_contract_adapter import (
    MAX_RESULT_BYTES,
    program_command_fields,
    program_wire_payload_hash,
)
from .database import SqliteDatabase, new_id, utc_now
from .repositories import RepositoryConflict


_ACTIVE_STATES = (
    "queued",
    "dispatched",
    "acknowledged",
    "running",
    "cancel_requested",
    "reconnect_pending",
    "outcome_unknown",
    "needs_attention",
)


def _json(value: Any, *, limit: int = 512_000) -> str:
    try:
        encoded = canonical_json(value)
    except (TypeError, ValueError) as error:
        raise RepositoryConflict("payload_invalid") from error
    if len(encoded.encode("utf-8")) > limit:
        raise RepositoryConflict("payload_too_large")
    return encoded


def _request_fingerprint(kind: str, effect_class: str, payload_hash: str) -> str:
    return sha256(
        _json(
            {
                "version": "cad.request/1",
                "kind": kind,
                "effect_class": effect_class,
                "payload_hash": payload_hash,
            },
            limit=16_384,
        ).encode("utf-8")
    ).hexdigest()


class ProgramRepository:
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    async def create_program(
        self,
        *,
        owner_subject: str,
        program_id: str,
        device_id: str,
        document_id: str,
        source_snapshot_id: str,
        expected_document_revision: str,
        semantic: dict[str, Any],
        program_digest: str,
        pins: dict[str, str],
        risk_class: str,
        missing_capabilities: list[str],
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        with self.database.transaction() as conn:
            existing_id = self._idempotency(
                conn,
                owner_subject=owner_subject,
                action="prepare",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if existing_id is not None:
                row = self._program_revision_row(conn, owner_subject, existing_id, 1)
                if row is None:
                    raise RepositoryConflict("idempotency_state_invalid")
                return self._program_revision(row), True
            snapshot = conn.execute(
                "SELECT 1 FROM snapshots WHERE owner_subject = ? AND snapshot_id = ? "
                "AND device_id = ? AND document_revision = ?",
                (
                    owner_subject,
                    source_snapshot_id,
                    device_id,
                    expected_document_revision,
                ),
            ).fetchone()
            if snapshot is None:
                raise RepositoryConflict("stale_snapshot")
            conn.execute(
                "INSERT INTO cad_programs(program_id, owner_subject, device_id, document_id, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (program_id, owner_subject, device_id, document_id, now, now),
            )
            conn.execute(
                """
                INSERT INTO cad_program_revisions(
                    program_id, revision, owner_subject, source_snapshot_id,
                    expected_document_revision, schema_version, registry_version,
                    program_digest, semantic_json, operations_json,
                    preconditions_json, postconditions_json, budgets_json,
                    risk_class, missing_capabilities_json, runtime_id, runtime_role,
                    host_family, host_version, package_id, package_version, package_hash,
                    capability_manifest_hash, operation_registry_hash, policy_version,
                    created_at
                ) VALUES (
                    ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
                """,
                (
                    program_id,
                    owner_subject,
                    source_snapshot_id,
                    expected_document_revision,
                    semantic["schema_version"],
                    semantic["registry_version"],
                    program_digest,
                    _json(semantic),
                    _json(semantic["operations"]),
                    _json(semantic["preconditions"]),
                    _json(semantic["postconditions"]),
                    _json(semantic["budgets"]),
                    risk_class,
                    _json(missing_capabilities, limit=65_536),
                    pins["runtime_id"],
                    pins["runtime_role"],
                    pins["host_family"],
                    pins["host_version"],
                    pins["package_id"],
                    pins["package_version"],
                    pins["package_hash"],
                    pins["capability_manifest_hash"],
                    pins["operation_registry_hash"],
                    pins["policy_version"],
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO program_idempotency(owner_subject, action, idempotency_key, "
                "request_hash, response_kind, response_id, created_at) "
                "VALUES (?, 'prepare', ?, ?, 'program', ?, ?)",
                (owner_subject, idempotency_key, request_hash, program_id, now),
            )
            row = self._program_revision_row(conn, owner_subject, program_id, 1)
        return self._program_revision(row), False

    async def get_program_revision(
        self, owner_subject: str, program_id: str, revision: int
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = self._program_revision_row(conn, owner_subject, program_id, revision)
        return self._program_revision(row) if row is not None else None

    async def create_action_job(
        self,
        *,
        owner_subject: str,
        action: str,
        device_id: str,
        document_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
        deadline_at: str,
        acquire_write_lock: bool,
        idempotency_request_hash: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if action not in {"preview", "commit", "validate"}:
            raise RepositoryConflict("invalid_action")
        kind = f"program_{action}"
        effect_class = "write" if action in {"preview", "commit"} else "read"
        try:
            payload_hash = program_wire_payload_hash(
                kind=kind,
                effect_class=effect_class,
                payload=payload,
            )
        except (TypeError, ValueError, KeyError) as error:
            raise RepositoryConflict("payload_invalid") from error
        request_hash = idempotency_request_hash or payload_hash
        request_fingerprint = _request_fingerprint(kind, effect_class, payload_hash)
        now = utc_now()
        with self.database.transaction() as conn:
            existing_id = self._idempotency(
                conn,
                owner_subject=owner_subject,
                action=action,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if existing_id is not None:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE owner_subject = ? AND job_id = ?",
                    (owner_subject, existing_id),
                ).fetchone()
                if row is None:
                    receipt = conn.execute(
                        "SELECT * FROM cad_execution_receipts "
                        "WHERE owner_subject = ? AND receipt_id = ?",
                        (owner_subject, existing_id),
                    ).fetchone()
                    if receipt is not None:
                        return {"prior_receipt": self._receipt(receipt)}, True
                    raise RepositoryConflict("idempotency_state_invalid")
                return self._job(row), True

            owned_device = conn.execute(
                "SELECT 1 FROM devices WHERE owner_subject = ? AND device_id = ?",
                (owner_subject, device_id),
            ).fetchone()
            if owned_device is None:
                raise RepositoryConflict("not_found")
            if action == "commit":
                preview_id = payload["execution"]["preview_id"]
                receipt = conn.execute(
                    "SELECT * FROM cad_execution_receipts "
                    "WHERE owner_subject = ? AND preview_id = ?",
                    (owner_subject, preview_id),
                ).fetchone()
                if receipt is not None:
                    expected = payload["execution"]
                    if (
                        str(receipt["program_digest"]) != expected["program_digest"]
                        or str(receipt["binding_digest"]) != expected["binding_digest"]
                        or str(receipt["preview_execution_digest"])
                        != expected["preview_execution_digest"]
                    ):
                        raise RepositoryConflict("binding_mismatch")
                    conn.execute(
                        "INSERT INTO program_idempotency(owner_subject, action, "
                        "idempotency_key, request_hash, response_kind, response_id, created_at) "
                        "VALUES (?, 'commit', ?, ?, 'receipt', ?, ?)",
                        (
                            owner_subject,
                            idempotency_key,
                            request_hash,
                            str(receipt["receipt_id"]),
                            now,
                        ),
                    )
                    return {"prior_receipt": self._receipt(receipt)}, True

            job_id = new_id("job")
            command_id = new_id("command")
            if acquire_write_lock:
                stale_lock = conn.execute(
                    "SELECT l.job_id, j.state FROM cad_program_write_locks l "
                    "JOIN jobs j ON j.job_id = l.job_id "
                    "WHERE l.device_id = ? AND l.document_id = ?",
                    (device_id, document_id),
                ).fetchone()
                if stale_lock is not None and str(stale_lock["state"]) not in _ACTIVE_STATES:
                    conn.execute(
                        "DELETE FROM cad_program_write_locks "
                        "WHERE device_id = ? AND document_id = ? AND job_id = ?",
                        (device_id, document_id, str(stale_lock["job_id"])),
                    )
                    stale_lock = None
                if stale_lock is not None:
                    raise RepositoryConflict("document_write_busy")
            conn.execute(
                """
                INSERT INTO jobs(
                    job_id, owner_subject, device_id, kind, effect_class, state,
                    state_version, deadline_at, command_id, idempotency_key,
                    payload_hash, payload_json, request_fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    owner_subject,
                    device_id,
                    kind,
                    effect_class,
                    deadline_at,
                    command_id,
                    idempotency_key,
                    payload_hash,
                    _json(payload),
                    request_fingerprint,
                    now,
                    now,
                ),
            )
            self._append_event(conn, job_id, state="queued")
            if acquire_write_lock:
                conn.execute(
                    "INSERT INTO cad_program_write_locks(device_id, document_id, job_id, "
                    "created_at) VALUES (?, ?, ?, ?)",
                    (device_id, document_id, job_id, now),
                )
            conn.execute(
                "INSERT INTO program_idempotency(owner_subject, action, idempotency_key, "
                "request_hash, response_kind, response_id, created_at) "
                "VALUES (?, ?, ?, ?, 'job', ?, ?)",
                (owner_subject, action, idempotency_key, request_hash, job_id, now),
            )
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job(row), False

    async def finalize_program_job(
        self,
        *,
        job_id: str,
        device_id: str,
        command_id: str,
        payload_hash: str,
        target: str,
        result: dict[str, Any] | None,
        error_code: str | None,
        error_summary: str | None,
        session_id: str | None,
        agent_sequence: int | None,
        terminal_hook: Callable[[Any, Any], None] | None = None,
    ) -> dict[str, Any] | None:
        with self.database.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            if (
                str(row["device_id"]) != device_id
                or str(row["command_id"]) != command_id
                or str(row["payload_hash"]) != payload_hash
            ):
                raise RepositoryConflict("message_identity_mismatch")
            if session_id is not None:
                active = conn.execute(
                    "SELECT 1 FROM agent_sessions WHERE session_id = ? AND device_id = ? "
                    "AND disconnected_at IS NULL",
                    (session_id, device_id),
                ).fetchone()
                if active is None:
                    raise RepositoryConflict("session_mismatch")
            current = str(row["state"])
            if is_terminal(current):
                stored = json.loads(row["result_json"]) if row["result_json"] else None
                if (
                    current == target
                    and stored == result
                    and row["error_code"] == error_code
                    and row["error_summary"] == error_summary
                ):
                    if terminal_hook is not None:
                        terminal_hook(conn, row)
                    duplicate = self._job(row)
                    duplicate["duplicate_terminal"] = True
                    return duplicate
                raise RepositoryConflict("terminal_result_conflict")
            validate_transition(
                current,
                target,
                effect_class=str(row["effect_class"]),
                evidence=True,
            )
            if target == "succeeded":
                if not isinstance(result, dict):
                    raise RepositoryConflict("program_result_invalid")
                validate_bounded_json(result)
                if len(canonical_json(result).encode("utf-8")) > MAX_RESULT_BYTES:
                    raise RepositoryConflict("program_result_invalid")
                self._materialize(conn, row, result)
            if terminal_hook is not None:
                terminal_hook(conn, row)
            now = utc_now()
            last_sequence = int(row["last_agent_sequence"])
            sequence = agent_sequence if agent_sequence is not None else last_sequence
            if sequence < last_sequence:
                raise RepositoryConflict("sequence_rejected")
            encoded_result = _json(result, limit=MAX_RESULT_BYTES) if result is not None else None
            updated = conn.execute(
                "UPDATE jobs SET state = ?, state_version = state_version + 1, "
                "result_json = ?, error_code = ?, error_summary = ?, "
                "last_agent_sequence = ?, updated_at = ? "
                "WHERE job_id = ? AND state = ? AND state_version = ?",
                (
                    target,
                    encoded_result,
                    error_code,
                    error_summary,
                    sequence,
                    now,
                    job_id,
                    current,
                    int(row["state_version"]),
                ),
            )
            if updated.rowcount != 1:
                raise RepositoryConflict("cas_conflict")
            self._append_event(
                conn,
                job_id,
                state=target,
                result=result,
                error_code=error_code,
            )
            conn.execute(
                "DELETE FROM cad_program_write_locks WHERE job_id = ?", (job_id,)
            )
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        value = self._job(row)
        value["duplicate_terminal"] = False
        return value

    async def release_write_lock(self, job_id: str) -> None:
        with self.database.transaction() as conn:
            conn.execute("DELETE FROM cad_program_write_locks WHERE job_id = ?", (job_id,))

    async def get_preview(
        self, owner_subject: str, preview_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM cad_previews WHERE owner_subject = ? AND preview_id = ?",
                (owner_subject, preview_id),
            ).fetchone()
        return self._preview(row) if row is not None else None

    async def get_preview_by_job(
        self, owner_subject: str, job_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM cad_previews WHERE owner_subject = ? AND job_id = ?",
                (owner_subject, job_id),
            ).fetchone()
        return self._preview(row) if row is not None else None

    async def get_receipt(
        self, owner_subject: str, receipt_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM cad_execution_receipts "
                "WHERE owner_subject = ? AND receipt_id = ?",
                (owner_subject, receipt_id),
            ).fetchone()
        return self._receipt(row) if row is not None else None

    async def get_receipt_by_job(
        self, owner_subject: str, job_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM cad_execution_receipts "
                "WHERE owner_subject = ? AND job_id = ?",
                (owner_subject, job_id),
            ).fetchone()
        return self._receipt(row) if row is not None else None

    async def get_receipt_by_preview(
        self, owner_subject: str, preview_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM cad_execution_receipts "
                "WHERE owner_subject = ? AND preview_id = ?",
                (owner_subject, preview_id),
            ).fetchone()
        return self._receipt(row) if row is not None else None

    async def get_validation(
        self, owner_subject: str, validation_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM cad_validations "
                "WHERE owner_subject = ? AND validation_id = ?",
                (owner_subject, validation_id),
            ).fetchone()
        return self._validation(row) if row is not None else None

    async def get_validation_by_job(
        self, owner_subject: str, job_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM cad_validations WHERE owner_subject = ? AND job_id = ?",
                (owner_subject, job_id),
            ).fetchone()
        return self._validation(row) if row is not None else None

    async def invalidate_preview(self, preview_id: str, reason: str) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE cad_previews SET invalidated_reason = COALESCE(invalidated_reason, ?) "
                "WHERE preview_id = ?",
                (reason, preview_id),
            )

    @staticmethod
    def _idempotency(
        conn: Any,
        *,
        owner_subject: str,
        action: str,
        idempotency_key: str,
        request_hash: str,
    ) -> str | None:
        row = conn.execute(
            "SELECT request_hash, response_id FROM program_idempotency "
            "WHERE owner_subject = ? AND action = ? AND idempotency_key = ?",
            (owner_subject, action, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if str(row["request_hash"]) != request_hash:
            raise RepositoryConflict("idempotency_conflict")
        return str(row["response_id"])

    @staticmethod
    def _program_revision_row(
        conn: Any, owner_subject: str, program_id: str, revision: int
    ) -> Any | None:
        return conn.execute(
            "SELECT r.*, p.device_id, p.document_id "
            "FROM cad_program_revisions r "
            "JOIN cad_programs p ON p.program_id = r.program_id "
            "WHERE r.owner_subject = ? AND r.program_id = ? AND r.revision = ?",
            (owner_subject, program_id, revision),
        ).fetchone()

    @staticmethod
    def _materialize(conn: Any, job: Any, result: dict[str, Any]) -> None:
        payload = json.loads(job["payload_json"])
        execution = payload["execution"]
        kind = str(job["kind"])
        now = utc_now()
        if kind == "program_preview":
            value = result.get("preview", result)
            expected_preview_digest = canonical_preview_digest(
                execution["preview_id"],
                program_command_fields(
                    kind="program_preview",
                    effect_class="write",
                    payload=payload,
                )["binding"],
            )
            required = {
                "program_digest",
                "execution_digest",
                "binding_digest",
                "preview_id",
                "preview_digest",
                "expires_at",
                "document_revision_before",
                "document_revision_after",
                "preview_strategy",
                "planned_operation_count",
                "planned_entity_count",
                "planned_layer_count",
                "validation",
            }
            if not isinstance(value, dict) or set(value) != required:
                raise RepositoryConflict("program_result_invalid")
            if (
                value["program_digest"] != execution["program_digest"]
                or value["execution_digest"] != execution["execution_digest"]
                or value["binding_digest"] != execution["binding_digest"]
                or value["preview_id"] != execution["preview_id"]
                or execution["preview_digest"] != expected_preview_digest
                or value["preview_digest"] != expected_preview_digest
                or value["document_revision_before"] != execution["expected_document_revision"]
                or value["document_revision_after"] != execution["expected_document_revision"]
                or value["preview_strategy"] != "database_transaction_abort"
                or value["planned_operation_count"] != len(payload["program"]["operations"])
                or not isinstance(value["validation"], dict)
                or value["validation"].get("transaction_aborted") is not True
                or value["validation"].get("drawing_unchanged") is not True
            ):
                raise RepositoryConflict("binding_mismatch")
            try:
                result_expiry = datetime.fromisoformat(
                    str(value["expires_at"]).replace("Z", "+00:00")
                )
                gateway_expiry = datetime.fromisoformat(
                    str(execution["expires_at"]).replace("Z", "+00:00")
                )
            except ValueError as error:
                raise RepositoryConflict("program_result_invalid") from error
            if (
                result_expiry.tzinfo is None
                or result_expiry <= datetime.now(timezone.utc)
                or result_expiry != gateway_expiry
                or value["expires_at"] != execution["expires_at"]
            ):
                raise RepositoryConflict("program_result_invalid")
            for field in (
                "planned_operation_count",
                "planned_entity_count",
                "planned_layer_count",
            ):
                if (
                    isinstance(value[field], bool)
                    or not isinstance(value[field], int)
                    or value[field] < 0
                ):
                    raise RepositoryConflict("program_result_invalid")
            pins = execution["pins"]
            conn.execute(
                """
                INSERT INTO cad_previews(
                    preview_id, owner_subject, program_id, program_revision, job_id,
                    program_digest, execution_digest, preview_digest, binding_digest, document_id,
                    expected_document_revision, runtime_id, runtime_role, host_family,
                    host_version, package_id, package_version, package_hash,
                    capability_manifest_hash, operation_registry_hash, registry_version,
                    policy_version, planned_operation_count, planned_entity_count,
                    planned_layer_count, validation_json, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    value["preview_id"],
                    str(job["owner_subject"]),
                    execution["program_id"],
                    execution["program_revision"],
                    str(job["job_id"]),
                    execution["program_digest"],
                    execution["execution_digest"],
                    value["preview_digest"],
                    execution["binding_digest"],
                    execution["document_id"],
                    execution["expected_document_revision"],
                    pins["runtime_id"],
                    pins["runtime_role"],
                    pins["host_family"],
                    pins["host_version"],
                    pins["package_id"],
                    pins["package_version"],
                    pins["package_hash"],
                    pins["capability_manifest_hash"],
                    pins["operation_registry_hash"],
                    pins["registry_version"],
                    pins["policy_version"],
                    value["planned_operation_count"],
                    value["planned_entity_count"],
                    value["planned_layer_count"],
                    _json(value["validation"], limit=65_536),
                    value["expires_at"],
                    now,
                ),
            )
            return
        if kind == "program_commit":
            value = result.get("receipt", result)
            required = {
                "receipt_id",
                "receipt_digest",
                "program_digest",
                "execution_digest",
                "preview_execution_digest",
                "binding_digest",
                "document_id",
                "document_revision_before",
                "document_revision_after",
                "effect_summary",
                "durable_receipt",
                "checkpoint",
            }
            if not isinstance(value, dict) or set(value) != required:
                raise RepositoryConflict("program_result_invalid")
            comparisons = {
                "receipt_id": execution["receipt_id"],
                "program_digest": execution["program_digest"],
                "execution_digest": execution["execution_digest"],
                "preview_execution_digest": execution["preview_execution_digest"],
                "binding_digest": execution["binding_digest"],
                "document_id": execution["document_id"],
                "document_revision_before": execution["expected_document_revision"],
            }
            if any(value[key] != expected for key, expected in comparisons.items()):
                raise RepositoryConflict("binding_mismatch")
            if (
                not isinstance(value["document_revision_after"], str)
                or not value["document_revision_after"]
                or not isinstance(value["effect_summary"], dict)
                or not isinstance(value["durable_receipt"], dict)
            ):
                raise RepositoryConflict("program_result_invalid")
            pins = execution["pins"]
            conn.execute(
                """
                INSERT INTO cad_execution_receipts(
                    receipt_id, owner_subject, program_id, program_revision, preview_id,
                    job_id, program_digest, execution_digest, receipt_digest, preview_execution_digest,
                    binding_digest, document_id, document_revision_before,
                    document_revision_after, runtime_id, package_hash,
                    capability_manifest_hash, operation_registry_hash, policy_version,
                    effect_summary_json, durable_receipt_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    value["receipt_id"],
                    str(job["owner_subject"]),
                    execution["program_id"],
                    execution["program_revision"],
                    execution["preview_id"],
                    str(job["job_id"]),
                    value["program_digest"],
                    value["execution_digest"],
                    value["receipt_digest"],
                    value["preview_execution_digest"],
                    value["binding_digest"],
                    value["document_id"],
                    value["document_revision_before"],
                    value["document_revision_after"],
                    pins["runtime_id"],
                    pins["package_hash"],
                    pins["capability_manifest_hash"],
                    pins["operation_registry_hash"],
                    pins["policy_version"],
                    _json(value["effect_summary"], limit=65_536),
                    _json(value["durable_receipt"], limit=MAX_RESULT_BYTES),
                    now,
                ),
            )
            if value["checkpoint"] is not None:
                ProgramRepository._insert_phase7_checkpoint(
                    conn,
                    job=job,
                    execution=execution,
                    checkpoint=value["checkpoint"],
                )
            return
        if kind == "program_validate":
            value = result.get("validation", result)
            required = {
                "validation_id",
                "execution_digest",
                "binding_digest",
                "document_revision",
                "passed",
                "report",
            }
            if not isinstance(value, dict) or set(value) != required:
                raise RepositoryConflict("program_result_invalid")
            if (
                value["validation_id"] != execution["validation_id"]
                or value["execution_digest"] != execution["execution_digest"]
                or value["binding_digest"] != execution["binding_digest"]
                or not isinstance(value["passed"], bool)
                or not isinstance(value["report"], dict)
            ):
                raise RepositoryConflict("binding_mismatch")
            conn.execute(
                """
                INSERT INTO cad_validations(
                    validation_id, owner_subject, program_id, program_revision,
                    receipt_id, job_id, execution_digest, binding_digest,
                    document_revision, passed, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    value["validation_id"],
                    str(job["owner_subject"]),
                    execution["program_id"],
                    execution["program_revision"],
                    execution["receipt_id"],
                    str(job["job_id"]),
                    value["execution_digest"],
                    value["binding_digest"],
                    value["document_revision"],
                    int(value["passed"]),
                    _json(value["report"], limit=MAX_RESULT_BYTES),
                    now,
                ),
            )
            return
        raise RepositoryConflict("program_result_invalid")

    @staticmethod
    def _insert_phase7_checkpoint(
        conn: Any,
        *,
        job: Any,
        execution: dict[str, Any],
        checkpoint: dict[str, Any],
    ) -> None:
        intent = conn.execute(
            "SELECT runtime_pins_json, policy_pins_json "
            "FROM execution_intents WHERE owner_subject = ? "
            "AND released_job_id = ? AND state = 'released'",
            (str(job["owner_subject"]), str(job["job_id"])),
        ).fetchone()
        if intent is None:
            # Explicit Phase 6 lab compatibility has no Phase 7 intent and is
            # therefore not publicly rollback-eligible.
            return
        runtime_pins = json.loads(intent["runtime_pins_json"])
        policy_pins = json.loads(intent["policy_pins_json"])
        host_pins = checkpoint.get("runtime_and_policy_pins")
        expected_host_pins = {
            "program_digest": execution["program_digest"],
            "execution_digest": execution["execution_digest"],
            "document_id": execution["document_id"],
            "document_revision": execution["expected_document_revision"],
            "runtime_id": runtime_pins["runtime_id"],
            "runtime_role": runtime_pins["runtime_role"],
            "host_family": runtime_pins["host_family"],
            "host_version": runtime_pins["host_version"],
            "package_id": runtime_pins["host_package_id"],
            "package_version": runtime_pins["host_package_version"],
            "package_hash": runtime_pins["host_package_hash"],
            "capability_manifest_hash": policy_pins["capability_manifest_hash"],
            "operation_registry_version": policy_pins["registry_version"],
            "operation_registry_hash": policy_pins["operation_registry_hash"],
            "policy_version": policy_pins["policy_version"],
        }
        if host_pins != expected_host_pins:
            raise RepositoryConflict("binding_mismatch")
        record = RollbackCheckpointRecord.model_validate(
            {
                **{
                    key: value
                    for key, value in checkpoint.items()
                    if key != "runtime_and_policy_pins"
                },
                "owner_subject": str(job["owner_subject"]),
                "runtime_pins": runtime_pins,
                "policy_pins": policy_pins,
            }
        )
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

    @staticmethod
    def _append_event(
        conn: Any,
        job_id: str,
        *,
        state: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        sequence = int(
            conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM job_events WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
        )
        conn.execute(
            "INSERT INTO job_events(job_id, sequence, event_type, state, progress_json, "
            "error_code, result_json, created_at) VALUES (?, ?, 'state', ?, NULL, ?, ?, ?)",
            (
                job_id,
                sequence,
                state,
                error_code,
                _json(result, limit=MAX_RESULT_BYTES) if result is not None else None,
                utc_now(),
            ),
        )

    @staticmethod
    def _program_revision(row: Any) -> dict[str, Any]:
        pins = {
            key: str(row[key])
            for key in (
                "runtime_id",
                "runtime_role",
                "host_family",
                "host_version",
                "package_id",
                "package_version",
                "package_hash",
                "capability_manifest_hash",
                "operation_registry_hash",
                "registry_version",
                "policy_version",
            )
        }
        return {
            "program_id": str(row["program_id"]),
            "program_revision": int(row["revision"]),
            "owner_subject": str(row["owner_subject"]),
            "device_id": str(row["device_id"]),
            "document_id": str(row["document_id"]),
            "source_snapshot_id": str(row["source_snapshot_id"]),
            "expected_document_revision": str(row["expected_document_revision"]),
            "schema_version": str(row["schema_version"]),
            "program_digest": str(row["program_digest"]),
            "semantic": json.loads(row["semantic_json"]),
            "risk_class": str(row["risk_class"]),
            "missing_capabilities": json.loads(row["missing_capabilities_json"]),
            "pins": pins,
            "created_at": str(row["created_at"]),
        }

    @staticmethod
    def _preview(row: Any) -> dict[str, Any]:
        value = {key: row[key] for key in row.keys()}
        value["program_revision"] = int(value["program_revision"])
        for field in (
            "planned_operation_count",
            "planned_entity_count",
            "planned_layer_count",
        ):
            value[field] = int(value[field])
        value["validation"] = json.loads(value.pop("validation_json"))
        return copy.deepcopy(value)

    @staticmethod
    def _receipt(row: Any) -> dict[str, Any]:
        value = {key: row[key] for key in row.keys()}
        value["program_revision"] = int(value["program_revision"])
        value["effect_summary"] = json.loads(value.pop("effect_summary_json"))
        value["durable_receipt"] = json.loads(value.pop("durable_receipt_json"))
        return copy.deepcopy(value)

    @staticmethod
    def _validation(row: Any) -> dict[str, Any]:
        value = {key: row[key] for key in row.keys()}
        value["program_revision"] = int(value["program_revision"])
        value["passed"] = bool(value["passed"])
        value["report"] = json.loads(value.pop("report_json"))
        return copy.deepcopy(value)

    @staticmethod
    def _job(row: Any) -> dict[str, Any]:
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
            "payload": json.loads(row["payload_json"]),
            "progress": json.loads(row["progress_json"]) if row["progress_json"] else None,
            "last_agent_sequence": int(row["last_agent_sequence"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error_code": row["error_code"],
            "error_summary": row["error_summary"],
            "cancel_requested_at": row["cancel_requested_at"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
