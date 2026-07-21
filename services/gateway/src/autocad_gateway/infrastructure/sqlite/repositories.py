"""Owner-scoped repositories for durable Gateway state."""

from __future__ import annotations

import copy
import json
from typing import Any

from autocad_contracts import canonical_payload_hash

from ...domain.jobs import EffectClass, JobState, validate_transition
from .database import SqliteDatabase, new_id, utc_now


class RepositoryConflict(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _json(value: Any, *, limit: int = 512_000) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > limit:
        raise RepositoryConflict("payload_too_large")
    return encoded


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
        with self.database.transaction() as conn:
            conn.execute(
                """
                INSERT INTO devices(device_id, owner_subject, display_name, status, capabilities_json, fixture_auth_ref, created_at, updated_at)
                VALUES (?, ?, ?, 'offline', ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET owner_subject=excluded.owner_subject,
                    display_name=excluded.display_name, capabilities_json=excluded.capabilities_json,
                    fixture_auth_ref=excluded.fixture_auth_ref, updated_at=excluded.updated_at
                """,
                (device_id, owner_subject, display_name, _json(capabilities, limit=16_384), fixture_auth_ref, now, now),
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

    async def create_session(
        self, *, device_id: str, session_id: str, protocol_version: str
    ) -> None:
        now = utc_now()
        with self.database.transaction() as conn:
            conn.execute(
                "INSERT INTO agent_sessions(session_id, device_id, protocol_version, connected_at, last_heartbeat_at, last_sequence) VALUES (?, ?, ?, ?, ?, 0)",
                (session_id, device_id, protocol_version, now, now),
            )
            conn.execute(
                "UPDATE devices SET status = 'online', updated_at = ? WHERE device_id = ?",
                (now, device_id),
            )

    async def heartbeat_session(self, session_id: str, *, sequence: int = 0) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE agent_sessions SET last_heartbeat_at = ?, last_sequence = MAX(last_sequence, ?) WHERE session_id = ?",
                (utc_now(), sequence, session_id),
            )

    async def close_session(self, session_id: str, *, device_id: str | None = None) -> None:
        now = utc_now()
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT device_id FROM agent_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            actual_device = device_id or (str(row[0]) if row else None)
            conn.execute(
                "UPDATE agent_sessions SET disconnected_at = ? WHERE session_id = ? AND disconnected_at IS NULL",
                (now, session_id),
            )
            if actual_device:
                active = conn.execute(
                    "SELECT 1 FROM agent_sessions WHERE device_id = ? AND disconnected_at IS NULL AND session_id <> ? LIMIT 1",
                    (actual_device, session_id),
                ).fetchone()
                if active is None:
                    conn.execute(
                        "UPDATE devices SET status = 'offline', updated_at = ? WHERE device_id = ?",
                        (now, actual_device),
                    )

    async def mark_sessions_disconnected(self) -> None:
        now = utc_now()
        with self.database.transaction() as conn:
            conn.execute("UPDATE agent_sessions SET disconnected_at = ? WHERE disconnected_at IS NULL", (now,))
            conn.execute("UPDATE devices SET status = 'offline', updated_at = ?", (now,))

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
        payload_hash = canonical_payload_hash(payload)
        now = utc_now()
        with self.database.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM jobs WHERE owner_subject = ? AND device_id = ? AND idempotency_key = ?",
                (owner_subject, device_id, idempotency_key),
            ).fetchone()
            if existing:
                if str(existing["payload_hash"]) != payload_hash:
                    raise RepositoryConflict("payload_mismatch")
                return {**self._job(existing), "existing": True}
            job_id = new_id("job")
            command_id = new_id("command")
            conn.execute(
                """
                INSERT INTO jobs(job_id, owner_subject, device_id, kind, effect_class, state, state_version,
                    deadline_at, command_id, idempotency_key, payload_hash, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, owner_subject, device_id, kind, effect_class, deadline_at, command_id, idempotency_key, payload_hash, _json(payload), now, now),
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
        with self.database.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            current = str(row["state"])
            validate_transition(current, target, effect_class=str(row["effect_class"]), evidence=evidence)
            version = int(row["state_version"])
            if expected_version is not None and version != expected_version:
                raise RepositoryConflict("cas_conflict")
            now = utc_now()
            updated = conn.execute(
                "UPDATE jobs SET state = ?, state_version = state_version + 1, result_json = COALESCE(?, result_json), error_code = COALESCE(?, error_code), error_summary = COALESCE(?, error_summary), updated_at = ? WHERE job_id = ? AND state_version = ? AND state = ?",
                (target, _json(result) if result is not None else None, error_code, error_summary, now, job_id, version, current),
            )
            if updated.rowcount != 1:
                raise RepositoryConflict("cas_conflict")
            self._append_event(conn, job_id, "state", state=target, result=result, error_code=error_code)
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job(row)

    async def append_progress(
        self, job_id: str, *, phase: str, percent: int, message: str, sequence: int
    ) -> dict[str, Any] | None:
        progress = {"phase": phase, "percent": percent, "message": message, "sequence": sequence}
        with self.database.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            previous = json.loads(row["progress_json"]) if row["progress_json"] else None
            if previous and sequence < int(previous.get("sequence", 0)):
                raise RepositoryConflict("sequence_rejected")
            if previous and sequence == int(previous.get("sequence", 0)):
                if previous != progress:
                    raise RepositoryConflict("sequence_rejected")
                return self._job(row)
            conn.execute(
                "UPDATE jobs SET progress_json = ?, updated_at = ? WHERE job_id = ?",
                (_json(progress, limit=16_384), utc_now(), job_id),
            )
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

    async def save_snapshot(
        self,
        *,
        owner_subject: str,
        device_id: str,
        job_id: str,
        snapshot: dict[str, Any],
    ) -> None:
        drawing = snapshot.get("drawing", {})
        entities = snapshot.get("entities", [])
        entity_summary = snapshot.get("entity_summary", {})
        with self.database.transaction() as conn:
            revision = conn.execute(
                "SELECT COALESCE(MAX(revision), 0) + 1 FROM snapshots WHERE job_id = ?", (job_id,)
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO snapshots(snapshot_id, owner_subject, device_id, job_id, revision, document_revision,
                    observation_level, drawing_json, entity_summary_json, entities_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(snapshot["snapshot_id"]), owner_subject, device_id, job_id, revision,
                    str(snapshot["document_revision"]), str(snapshot.get("observation_level", "summary")),
                    _json(drawing), _json(entity_summary), _json(entities), utc_now(),
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
            "fixture_auth_ref": str(row["fixture_auth_ref"]),
            "updated_at": str(row["updated_at"]),
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
            "payload": json.loads(row["payload_json"]),
            "progress": json.loads(row["progress_json"]) if row["progress_json"] else None,
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error_code": row["error_code"],
            "error_summary": row["error_summary"],
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
            (job_id, sequence, event_type, state, _json(progress, limit=16_384) if progress else None, error_code, _json(result) if result else None, utc_now()),
        )
