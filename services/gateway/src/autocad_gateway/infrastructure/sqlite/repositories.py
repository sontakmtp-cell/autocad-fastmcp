"""Owner-scoped repositories for durable Gateway state."""

from __future__ import annotations

import copy
import json
from hashlib import sha256
from typing import Any

from autocad_contracts import (
    canonical_package_manifest_hash,
    canonical_packages,
    canonical_capabilities as protocol_canonical_capabilities,
    canonical_capability_hash as protocol_capability_hash,
    canonical_payload_hash,
)

from ...domain.jobs import EffectClass, JobState, is_terminal, validate_transition
from .database import SqliteDatabase, new_id, utc_now


class RepositoryConflict(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _json(value: Any, *, limit: int = 512_000) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise RepositoryConflict("payload_invalid") from error
    if len(encoded.encode("utf-8")) > limit:
        raise RepositoryConflict("payload_too_large")
    return encoded


def canonical_capabilities(capabilities: list[str]) -> list[str]:
    try:
        return list(protocol_canonical_capabilities(capabilities))
    except ValueError as error:
        raise RepositoryConflict("capabilities_invalid") from error


def capability_manifest_hash(capabilities: list[str]) -> str:
    canonical = canonical_capabilities(capabilities)
    return protocol_capability_hash(canonical)


def _request_fingerprint(kind: str, effect_class: EffectClass, payload_hash: str) -> str:
    value = {
        "effect_class": effect_class,
        "kind": kind,
        "payload_hash": payload_hash,
        "version": "cad.request/1",
    }
    return sha256(_json(value, limit=16_384).encode("utf-8")).hexdigest()


class SqliteRepository:
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    async def seed_device(
        self,
        *,
        owner_subject: str,
        device_id: str,
        display_name: str,
        capabilities: list[str],
        fixture_auth_ref: str,
    ) -> None:
        now = utc_now()
        normalized_capabilities = canonical_capabilities(capabilities)
        capability_hash = capability_manifest_hash(normalized_capabilities)
        with self.database.transaction() as conn:
            conn.execute(
                """
                INSERT INTO devices(device_id, owner_subject, display_name, status, capabilities_json,
                    fixture_auth_ref, created_at, updated_at, capability_hash)
                VALUES (?, ?, ?, 'offline', ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET owner_subject=excluded.owner_subject,
                    display_name=excluded.display_name, fixture_auth_ref=excluded.fixture_auth_ref,
                    updated_at=excluded.updated_at
                """,
                (
                    device_id,
                    owner_subject,
                    display_name,
                    _json(normalized_capabilities, limit=16_384),
                    fixture_auth_ref,
                    now,
                    now,
                    capability_hash,
                ),
            )

    async def list_devices(
        self, owner_subject: str, *, online_only: bool = False, capability: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM devices WHERE owner_subject = ?"
        parameters: list[Any] = [owner_subject]
        if online_only:
            query += " AND status = 'online'"
        with self.database.read_connection() as conn:
            rows = conn.execute(query + " ORDER BY device_id", parameters).fetchall()
        values = [self._device(row) for row in rows]
        if capability:
            values = [value for value in values if capability in value["capabilities"]]
        return values

    async def get_device(self, owner_subject: str, device_id: str) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE owner_subject = ? AND device_id = ?",
                (owner_subject, device_id),
            ).fetchone()
        return self._device(row) if row else None

    async def set_device_status(self, device_id: str, status: str) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE devices SET status = ?, updated_at = ? WHERE device_id = ?",
                (status, utc_now(), device_id),
            )

    async def activate_session(
        self,
        *,
        device_id: str,
        session_id: str,
        protocol_version: str,
        capabilities: list[str],
        capability_hash: str,
        last_sequence: int = 0,
        agent_version: str | None = None,
        packages: list[dict[str, Any]] | None = None,
        package_manifest_hash: str | None = None,
        runtime_state: str | None = None,
        document_name: str | None = None,
        paused: bool | None = None,
    ) -> dict[str, Any]:
        normalized_capabilities = canonical_capabilities(capabilities)
        computed_hash = capability_manifest_hash(normalized_capabilities)
        if capability_hash != computed_hash:
            raise RepositoryConflict("capability_hash_mismatch")
        if last_sequence < 0:
            raise RepositoryConflict("sequence_rejected")
        normalized_packages = [
            item.model_dump(mode="json") for item in canonical_packages(packages or [])
        ]
        computed_package_hash = canonical_package_manifest_hash(normalized_packages)
        if package_manifest_hash is not None and package_manifest_hash != computed_package_hash:
            raise RepositoryConflict("package_hash_mismatch")
        now = utc_now()
        with self.database.transaction() as conn:
            device = conn.execute(
                "SELECT capabilities_json, capability_hash FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if device is None:
                raise RepositoryConflict("not_found")
            existing_session = conn.execute(
                "SELECT device_id, protocol_version, disconnected_at FROM agent_sessions "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if existing_session is not None and (
                str(existing_session["device_id"]) != device_id
                or str(existing_session["protocol_version"]) != protocol_version
            ):
                raise RepositoryConflict("session_mismatch")
            replaced_rows = conn.execute(
                "SELECT session_id FROM agent_sessions WHERE device_id = ? "
                "AND disconnected_at IS NULL AND session_id <> ? ORDER BY connected_at, session_id",
                (device_id, session_id),
            ).fetchall()
            replaced_session_ids = [str(row[0]) for row in replaced_rows]
            conn.execute(
                "UPDATE agent_sessions SET disconnected_at = ? WHERE device_id = ? "
                "AND disconnected_at IS NULL AND session_id <> ?",
                (now, device_id, session_id),
            )
            encoded_capabilities = _json(normalized_capabilities, limit=16_384)
            if existing_session is None:
                conn.execute(
                    """
                    INSERT INTO agent_sessions(session_id, device_id, protocol_version, connected_at,
                        last_heartbeat_at, last_sequence, capabilities_json, capability_hash,
                        agent_version, package_manifest_json, package_manifest_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        device_id,
                        protocol_version,
                        now,
                        now,
                        last_sequence,
                        encoded_capabilities,
                        computed_hash,
                        agent_version,
                        _json(normalized_packages, limit=65_536),
                        computed_package_hash,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE agent_sessions
                    SET disconnected_at = NULL, last_heartbeat_at = ?,
                        last_sequence = MAX(last_sequence, ?), capabilities_json = ?,
                        capability_hash = ?, agent_version = ?, package_manifest_json = ?,
                        package_manifest_hash = ?
                    WHERE session_id = ?
                    """,
                    (
                        now,
                        last_sequence,
                        encoded_capabilities,
                        computed_hash,
                        agent_version,
                        _json(normalized_packages, limit=65_536),
                        computed_package_hash,
                        session_id,
                    ),
                )
            capability_changed = (
                str(device["capabilities_json"]) != encoded_capabilities
                or device["capability_hash"] != computed_hash
            )
            conn.execute(
                "UPDATE devices SET status = 'online', capabilities_json = ?, capability_hash = ?, "
                "agent_version = ?, runtime_state = ?, document_name = ?, paused = ?, "
                "package_manifest_json = ?, package_manifest_hash = ?, runtime_updated_at = ?, "
                "updated_at = ? WHERE device_id = ?",
                (
                    encoded_capabilities,
                    computed_hash,
                    agent_version,
                    runtime_state,
                    document_name,
                    int(bool(paused)),
                    _json(normalized_packages, limit=65_536),
                    computed_package_hash,
                    now,
                    now,
                    device_id,
                ),
            )
            active_sequence = int(
                conn.execute(
                    "SELECT last_sequence FROM agent_sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
            )
        return {
            "session_id": session_id,
            "device_id": device_id,
            "capabilities": normalized_capabilities,
            "capability_hash": computed_hash,
            "capability_changed": capability_changed,
            "replaced_session_ids": replaced_session_ids,
            "last_sequence": active_sequence,
            "packages": normalized_packages,
            "package_manifest_hash": computed_package_hash,
        }

    async def create_session(
        self, *, device_id: str, session_id: str, protocol_version: str
    ) -> None:
        """Compatibility wrapper; new handshakes should call ``activate_session``."""

        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT capabilities_json FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
        if row is None:
            raise RepositoryConflict("not_found")
        capabilities = json.loads(row["capabilities_json"])
        await self.activate_session(
            device_id=device_id,
            session_id=session_id,
            protocol_version=protocol_version,
            capabilities=capabilities,
            capability_hash=capability_manifest_hash(capabilities),
        )

    async def heartbeat_session(
        self,
        session_id: str,
        *,
        device_id: str,
        sequence: int = 0,
        runtime_state: str | None = None,
        document_name: str | None = None,
        paused: bool | None = None,
    ) -> bool:
        if sequence < 0:
            raise RepositoryConflict("sequence_rejected")
        now = utc_now()
        with self.database.transaction() as conn:
            updated = conn.execute(
                "UPDATE agent_sessions SET last_heartbeat_at = ?, "
                "last_sequence = MAX(last_sequence, ?) WHERE session_id = ? AND device_id = ? "
                "AND disconnected_at IS NULL",
                (now, sequence, session_id, device_id),
            )
            if updated.rowcount != 1:
                return False
            conn.execute(
                "UPDATE devices SET status = 'online', runtime_state = ?, document_name = ?, "
                "paused = COALESCE(?, paused), runtime_updated_at = ?, updated_at = ? "
                "WHERE device_id = ?",
                (
                    runtime_state,
                    document_name,
                    int(paused) if paused is not None else None,
                    now,
                    now,
                    device_id,
                ),
            )
        return True

    async def mark_session_stale(self, session_id: str, *, device_id: str) -> bool:
        now = utc_now()
        with self.database.transaction() as conn:
            active = conn.execute(
                "SELECT 1 FROM agent_sessions WHERE session_id = ? AND device_id = ? "
                "AND disconnected_at IS NULL",
                (session_id, device_id),
            ).fetchone()
            if active is None:
                return False
            conn.execute(
                "UPDATE devices SET status = 'offline', updated_at = ? WHERE device_id = ?",
                (now, device_id),
            )
        return True

    async def get_active_session(self, device_id: str) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM agent_sessions WHERE device_id = ? AND disconnected_at IS NULL",
                (device_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "session_id": str(row["session_id"]),
            "device_id": str(row["device_id"]),
            "protocol_version": str(row["protocol_version"]),
            "connected_at": str(row["connected_at"]),
            "last_heartbeat_at": str(row["last_heartbeat_at"]),
            "last_sequence": int(row["last_sequence"]),
            "capabilities": json.loads(row["capabilities_json"]),
            "capability_hash": row["capability_hash"],
            "agent_version": row["agent_version"],
            "packages": json.loads(row["package_manifest_json"]),
            "package_manifest_hash": row["package_manifest_hash"],
        }

    async def close_session(self, session_id: str, *, device_id: str | None = None) -> bool:
        now = utc_now()
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT device_id, disconnected_at FROM agent_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return False
            actual_device = str(row["device_id"])
            if device_id is not None and device_id != actual_device:
                raise RepositoryConflict("session_mismatch")
            updated = conn.execute(
                "UPDATE agent_sessions SET disconnected_at = ? WHERE session_id = ? AND disconnected_at IS NULL",
                (now, session_id),
            )
            if updated.rowcount == 1:
                active = conn.execute(
                    "SELECT 1 FROM agent_sessions WHERE device_id = ? AND disconnected_at IS NULL AND session_id <> ? LIMIT 1",
                    (actual_device, session_id),
                ).fetchone()
                if active is None:
                    conn.execute(
                        "UPDATE devices SET status = 'offline', updated_at = ? WHERE device_id = ?",
                        (now, actual_device),
                    )
        return updated.rowcount == 1

    async def mark_sessions_disconnected(self) -> None:
        now = utc_now()
        with self.database.transaction() as conn:
            conn.execute("UPDATE agent_sessions SET disconnected_at = ? WHERE disconnected_at IS NULL", (now,))
            conn.execute(
                "UPDATE devices SET status = 'offline', updated_at = ? WHERE status = 'online'",
                (now,),
            )

    async def create_job(
        self,
        *,
        owner_subject: str,
        device_id: str,
        kind: str,
        effect_class: EffectClass,
        payload: dict[str, Any],
        idempotency_key: str,
        deadline_at: str | None,
    ) -> dict[str, Any]:
        try:
            payload_hash = canonical_payload_hash(payload)
        except (TypeError, ValueError) as error:
            raise RepositoryConflict("payload_invalid") from error
        request_fingerprint = _request_fingerprint(kind, effect_class, payload_hash)
        now = utc_now()
        with self.database.transaction() as conn:
            owned_device = conn.execute(
                "SELECT 1 FROM devices WHERE device_id = ? AND owner_subject = ?",
                (device_id, owner_subject),
            ).fetchone()
            if owned_device is None:
                raise RepositoryConflict("not_found")
            existing = conn.execute(
                "SELECT * FROM jobs WHERE owner_subject = ? AND device_id = ? AND idempotency_key = ?",
                (owner_subject, device_id, idempotency_key),
            ).fetchone()
            if existing:
                existing_fingerprint = existing["request_fingerprint"]
                if (
                    str(existing["kind"]) != kind
                    or str(existing["effect_class"]) != effect_class
                    or str(existing["payload_hash"]) != payload_hash
                    or (
                        existing_fingerprint is not None
                        and str(existing_fingerprint) != request_fingerprint
                    )
                ):
                    raise RepositoryConflict("idempotency_conflict")
                if existing_fingerprint is None:
                    conn.execute(
                        "UPDATE jobs SET request_fingerprint = ? WHERE job_id = ? "
                        "AND request_fingerprint IS NULL",
                        (request_fingerprint, str(existing["job_id"])),
                    )
                    existing = conn.execute(
                        "SELECT * FROM jobs WHERE job_id = ?", (str(existing["job_id"]),)
                    ).fetchone()
                return {**self._job(existing), "existing": True}
            job_id = new_id("job")
            command_id = new_id("command")
            conn.execute(
                """
                INSERT INTO jobs(job_id, owner_subject, device_id, kind, effect_class, state, state_version,
                    deadline_at, command_id, idempotency_key, payload_hash, payload_json,
                    request_fingerprint, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?, ?, ?, ?)
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
            self._append_event(conn, job_id, "state", state="queued")
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return {**self._job(row), "existing": False}

    async def get_job(self, owner_subject: str, job_id: str) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE owner_subject = ? AND job_id = ?",
                (owner_subject, job_id),
            ).fetchone()
        return self._job(row) if row else None

    async def get_job_by_command(self, device_id: str, command_id: str) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE device_id = ? AND command_id = ?",
                (device_id, command_id),
            ).fetchone()
        return self._job(row) if row else None

    async def claim_job(self, job_id: str) -> dict[str, Any] | None:
        with self.database.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row or row["state"] != "queued":
                return None
            version = int(row["state_version"])
            now = utc_now()
            updated = conn.execute(
                "UPDATE jobs SET state = 'dispatched', state_version = state_version + 1, updated_at = ? WHERE job_id = ? AND state = 'queued' AND state_version = ?",
                (now, job_id, version),
            )
            if updated.rowcount != 1:
                return None
            self._append_event(conn, job_id, "state", state="dispatched")
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job(row)

    async def transition_job(
        self,
        job_id: str,
        target: JobState,
        *,
        expected_version: int | None = None,
        evidence: bool = False,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> dict[str, Any] | None:
        if isinstance(result, dict) and isinstance(result.get("snapshot"), dict):
            raise RepositoryConflict("atomic_finalization_required")
        with self.database.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            if target == "succeeded" and str(row["kind"]) == "observe":
                raise RepositoryConflict("atomic_finalization_required")
            current = str(row["state"])
            validate_transition(current, target, effect_class=str(row["effect_class"]), evidence=evidence)
            version = int(row["state_version"])
            if expected_version is not None and version != expected_version:
                raise RepositoryConflict("cas_conflict")
            now = utc_now()
            updated = conn.execute(
                "UPDATE jobs SET state = ?, state_version = state_version + 1, "
                "result_json = COALESCE(?, result_json), error_code = COALESCE(?, error_code), "
                "error_summary = COALESCE(?, error_summary), "
                "cancel_requested_at = CASE WHEN ? = 'cancel_requested' "
                "THEN COALESCE(cancel_requested_at, ?) ELSE cancel_requested_at END, "
                "updated_at = ? WHERE job_id = ? AND state_version = ? AND state = ?",
                (
                    target,
                    _json(result) if result is not None else None,
                    error_code,
                    error_summary,
                    target,
                    now,
                    now,
                    job_id,
                    version,
                    current,
                ),
            )
            if updated.rowcount != 1:
                raise RepositoryConflict("cas_conflict")
            self._append_event(conn, job_id, "state", state=target, result=result, error_code=error_code)
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job(row)

    async def request_job_cancel(
        self,
        job_id: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any] | None:
        """Atomically record durable cancellation intent without losing recovery state.

        Active work moves to ``cancel_requested`` and queued work is cancelled
        immediately. Recovery states remain unchanged because their outcome must be
        established by reconciliation evidence. Replays after the intent was stored
        are idempotent, including when the job has subsequently become terminal.
        """

        with self.database.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None

            current = str(row["state"])
            if is_terminal(current) or row["cancel_requested_at"] is not None:
                return self._job(row)

            version = int(row["state_version"])
            if expected_version is not None and version != expected_version:
                raise RepositoryConflict("cas_conflict")

            if current == "queued":
                target: JobState = "cancelled"
                validate_transition(
                    current,
                    target,
                    effect_class=str(row["effect_class"]),
                )
            elif current in {"dispatched", "acknowledged", "running"}:
                target = "cancel_requested"
                validate_transition(
                    current,
                    target,
                    effect_class=str(row["effect_class"]),
                )
            elif current in {"cancel_requested", "reconnect_pending", "outcome_unknown"}:
                target = current  # type: ignore[assignment]
            else:
                raise RepositoryConflict("invalid_state")

            now = utc_now()
            updated = conn.execute(
                "UPDATE jobs SET state = ?, state_version = state_version + 1, "
                "cancel_requested_at = ?, updated_at = ? "
                "WHERE job_id = ? AND state = ? AND state_version = ? "
                "AND cancel_requested_at IS NULL",
                (target, now, now, job_id, current, version),
            )
            if updated.rowcount != 1:
                latest = conn.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                if latest is not None and latest["cancel_requested_at"] is not None:
                    return self._job(latest)
                raise RepositoryConflict("cas_conflict")

            self._append_event(
                conn,
                job_id,
                "state",
                state=target,
                result={"cancel_requested": True},
            )
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job(row)

    async def append_progress(
        self,
        job_id: str,
        *,
        phase: str,
        percent: int,
        message: str,
        sequence: int,
        expected_version: int | None = None,
    ) -> dict[str, Any] | None:
        progress = {"phase": phase, "percent": percent, "message": message, "sequence": sequence}
        with self.database.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            current = str(row["state"])
            if is_terminal(current):
                raise RepositoryConflict("terminal_immutable")
            if current not in {"acknowledged", "running", "cancel_requested"}:
                raise RepositoryConflict("message_order_invalid")
            version = int(row["state_version"])
            if expected_version is not None and version != expected_version:
                raise RepositoryConflict("cas_conflict")
            previous = json.loads(row["progress_json"]) if row["progress_json"] else None
            last_sequence = int(row["last_agent_sequence"])
            if sequence < last_sequence:
                raise RepositoryConflict("sequence_rejected")
            if sequence == last_sequence and previous is not None:
                if previous != progress:
                    raise RepositoryConflict("sequence_rejected")
                return self._job(row)
            updated = conn.execute(
                "UPDATE jobs SET progress_json = ?, last_agent_sequence = ?, updated_at = ? "
                "WHERE job_id = ? AND state = ? AND state_version = ?",
                (
                    _json(progress, limit=16_384),
                    sequence,
                    utc_now(),
                    job_id,
                    current,
                    version,
                ),
            )
            if updated.rowcount != 1:
                raise RepositoryConflict("cas_conflict")
            self._append_event(conn, job_id, "progress", state=str(row["state"]), progress=progress)
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job(row)

    async def list_events(
        self, owner_subject: str, job_id: str, *, cursor: int = 0, limit: int = 50
    ) -> tuple[list[dict[str, Any]], str | None]:
        job = await self.get_job(owner_subject, job_id)
        if job is None:
            return [], None
        with self.database.read_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM job_events WHERE job_id = ? AND sequence > ? ORDER BY sequence LIMIT ?",
                (job_id, cursor, limit + 1),
            ).fetchall()
        values = [self._event(row) for row in rows[:limit]]
        next_cursor = str(values[-1]["sequence"]) if len(rows) > limit and values else None
        return values, next_cursor

    async def finalize_job_result(
        self,
        *,
        job_id: str,
        device_id: str,
        command_id: str,
        payload_hash: str,
        target: JobState,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
        snapshot: dict[str, Any] | None = None,
        session_id: str | None = None,
        expected_version: int | None = None,
        agent_sequence: int | None = None,
        evidence: bool = False,
    ) -> dict[str, Any] | None:
        """Atomically persist an Agent terminal result and its optional snapshot."""

        if not is_terminal(target):
            raise RepositoryConflict("terminal_state_required")
        if agent_sequence is not None and agent_sequence < 0:
            raise RepositoryConflict("sequence_rejected")
        result_snapshot = result.get("snapshot") if isinstance(result, dict) else None
        if snapshot is None and isinstance(result_snapshot, dict):
            snapshot = result_snapshot
        if snapshot is not None and snapshot != result_snapshot:
            raise RepositoryConflict("snapshot_result_invalid")
        if snapshot is not None and target != "succeeded":
            raise RepositoryConflict("snapshot_result_invalid")

        with self.database.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            if str(row["device_id"]) != device_id or str(row["command_id"]) != command_id:
                raise RepositoryConflict("message_identity_mismatch")
            if str(row["payload_hash"]) != payload_hash:
                raise RepositoryConflict("payload_mismatch")
            if session_id is not None:
                active_session = conn.execute(
                    "SELECT 1 FROM agent_sessions WHERE session_id = ? AND device_id = ? "
                    "AND disconnected_at IS NULL",
                    (session_id, device_id),
                ).fetchone()
                if active_session is None:
                    raise RepositoryConflict("session_mismatch")

            current = str(row["state"])
            if is_terminal(current):
                stored_result = (
                    json.loads(row["result_json"]) if row["result_json"] is not None else None
                )
                if (
                    current == target
                    and stored_result == result
                    and row["error_code"] == error_code
                    and row["error_summary"] == error_summary
                ):
                    duplicate = self._job(row)
                    duplicate["duplicate_terminal"] = True
                    return duplicate
                raise RepositoryConflict("terminal_result_conflict")

            version = int(row["state_version"])
            if expected_version is not None and version != expected_version:
                raise RepositoryConflict("cas_conflict")
            validate_transition(
                current,
                target,
                effect_class=str(row["effect_class"]),
                evidence=evidence,
            )
            last_sequence = int(row["last_agent_sequence"])
            if agent_sequence is not None and (
                agent_sequence < last_sequence
                or (
                    agent_sequence == last_sequence
                    and (last_sequence != 0 or row["progress_json"] is not None)
                )
            ):
                raise RepositoryConflict("sequence_rejected")
            if str(row["kind"]) == "observe" and target == "succeeded" and snapshot is None:
                raise RepositoryConflict("snapshot_required")

            if snapshot is not None:
                self._insert_snapshot(conn, row, snapshot)

            now = utc_now()
            result_json = _json(result) if result is not None else None
            updated = conn.execute(
                """
                UPDATE jobs
                SET state = ?, state_version = state_version + 1, result_json = ?,
                    error_code = ?, error_summary = ?, last_agent_sequence = ?, updated_at = ?
                WHERE job_id = ? AND state = ? AND state_version = ?
                """,
                (
                    target,
                    result_json,
                    error_code,
                    error_summary,
                    agent_sequence if agent_sequence is not None else last_sequence,
                    now,
                    job_id,
                    current,
                    version,
                ),
            )
            if updated.rowcount != 1:
                raise RepositoryConflict("cas_conflict")
            self._append_event(
                conn,
                job_id,
                "state",
                state=target,
                result=result,
                error_code=error_code,
            )
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        finalized = self._job(row)
        finalized["duplicate_terminal"] = False
        return finalized

    @staticmethod
    def _insert_snapshot(conn: Any, job: Any, snapshot: dict[str, Any]) -> None:
        snapshot_id = snapshot.get("snapshot_id")
        document_revision = snapshot.get("document_revision")
        observation_level = snapshot.get("observation_level", "summary")
        drawing = snapshot.get("drawing", {})
        entities = snapshot.get("entities", [])
        entity_summary = snapshot.get("entity_summary", {})
        revision_evidence = snapshot.get("revision_evidence") or {}
        expected_observation_level = json.loads(job["payload_json"]).get(
            "observation_level", "summary"
        )
        if (
            not isinstance(snapshot_id, str)
            or not snapshot_id
            or len(snapshot_id.encode("utf-8")) > 128
            or not isinstance(document_revision, str)
            or not document_revision
            or len(document_revision.encode("utf-8")) > 256
            or not isinstance(observation_level, str)
            or observation_level not in {"summary", "detail"}
            or observation_level != expected_observation_level
            or not isinstance(drawing, dict)
            or not isinstance(entity_summary, dict)
            or not isinstance(entities, list)
            or not isinstance(revision_evidence, dict)
        ):
            raise RepositoryConflict("snapshot_invalid")
        existing_for_job = conn.execute(
            "SELECT snapshot_id FROM snapshots WHERE job_id = ? LIMIT 1",
            (str(job["job_id"]),),
        ).fetchone()
        if existing_for_job is not None:
            raise RepositoryConflict("snapshot_conflict")
        existing_id = conn.execute(
            "SELECT job_id FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
        if existing_id is not None:
            raise RepositoryConflict("snapshot_conflict")
        conn.execute(
            """
            INSERT INTO snapshots(snapshot_id, owner_subject, device_id, job_id, revision,
                document_revision, observation_level, drawing_json, entity_summary_json,
                entities_json, created_at, revision_strength, commit_safe)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                str(job["owner_subject"]),
                str(job["device_id"]),
                str(job["job_id"]),
                document_revision,
                observation_level,
                _json(drawing),
                _json(entity_summary),
                _json(entities),
                utc_now(),
                revision_evidence.get("revision_strength"),
                int(bool(revision_evidence.get("commit_safe", False))),
            ),
        )

    async def get_snapshot(self, owner_subject: str, snapshot_id: str) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE owner_subject = ? AND snapshot_id = ?",
                (owner_subject, snapshot_id),
            ).fetchone()
        if not row:
            return None
        return {
            "snapshot_id": str(row["snapshot_id"]),
            "owner_subject": str(row["owner_subject"]),
            "device_id": str(row["device_id"]),
            "job_id": str(row["job_id"]),
            "revision": int(row["revision"]),
            "document_revision": str(row["document_revision"]),
            "observation_level": str(row["observation_level"]),
            "drawing": json.loads(row["drawing_json"]),
            "entity_summary": json.loads(row["entity_summary_json"]),
            "entities": json.loads(row["entities_json"]),
            "revision_evidence": (
                {
                    "revision_schema": "cad.revision/1",
                    "revision_strength": str(row["revision_strength"]),
                    "commit_safe": bool(row["commit_safe"]),
                }
                if row["revision_strength"]
                else None
            ),
        }

    async def jobs_for_device(self, device_id: str) -> list[dict[str, Any]]:
        with self.database.read_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE device_id = ? AND state NOT IN ('succeeded', 'failed', 'cancelled', 'needs_attention') ORDER BY created_at",
                (device_id,),
            ).fetchall()
        return [self._job(row) for row in rows]

    async def all_nonterminal_jobs(self) -> list[dict[str, Any]]:
        with self.database.read_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE state NOT IN ('succeeded', 'failed', 'cancelled', 'needs_attention') ORDER BY created_at"
            ).fetchall()
        return [self._job(row) for row in rows]

    @staticmethod
    def _device(row: Any) -> dict[str, Any]:
        return {
            "device_id": str(row["device_id"]),
            "owner_subject": str(row["owner_subject"]),
            "display_name": str(row["display_name"]),
            "status": str(row["status"]),
            "capabilities": json.loads(row["capabilities_json"]),
            "capability_hash": row["capability_hash"],
            "fixture_auth_ref": str(row["fixture_auth_ref"]),
            "updated_at": str(row["updated_at"]),
            "agent_version": row["agent_version"],
            "runtime_state": row["runtime_state"],
            "document_name": row["document_name"],
            "paused": bool(row["paused"]),
            "packages": json.loads(row["package_manifest_json"]),
            "package_manifest_hash": row["package_manifest_hash"],
            "runtime_updated_at": row["runtime_updated_at"],
        }

    @staticmethod
    def _job(row: Any) -> dict[str, Any]:
        value = {
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
        return copy.deepcopy(value)

    @staticmethod
    def _event(
        row: Any,
    ) -> dict[str, Any]:
        return {
            "sequence": int(row["sequence"]),
            "event_type": str(row["event_type"]),
            "state": row["state"],
            "progress": json.loads(row["progress_json"]) if row["progress_json"] else None,
            "error_code": row["error_code"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "created_at": str(row["created_at"]),
        }

    @staticmethod
    def _append_event(
        conn: Any,
        job_id: str,
        event_type: str,
        *,
        state: str | None = None,
        progress: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        sequence = int(
            conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM job_events WHERE job_id = ?", (job_id,)
            ).fetchone()[0]
        )
        conn.execute(
            "INSERT INTO job_events(job_id, sequence, event_type, state, progress_json, error_code, result_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                sequence,
                event_type,
                state,
                _json(progress, limit=16_384) if progress is not None else None,
                error_code,
                _json(result) if result is not None else None,
                utc_now(),
            ),
        )
