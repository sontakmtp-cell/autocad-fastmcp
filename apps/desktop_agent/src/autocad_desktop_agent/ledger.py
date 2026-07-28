"""Local durable command ledger; terminal evidence is stored before transmission."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal


LedgerState = Literal[
    "received",
    "accepted",
    "started",
    "succeeded",
    "failed",
    "cancelled",
    "outcome_unknown",
]
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "outcome_unknown"})
_SENSITIVE_EVIDENCE_KEYS = frozenset(
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


def _validate_evidence_detail(value: Any, *, depth: int = 0) -> None:
    if depth > 3:
        raise LedgerConflict("evidence_details_invalid")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value) > 1_024:
            raise LedgerConflict("evidence_details_invalid")
        return
    if isinstance(value, list):
        if len(value) > 64:
            raise LedgerConflict("evidence_details_invalid")
        for item in value:
            _validate_evidence_detail(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 32:
            raise LedgerConflict("evidence_details_invalid")
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not key
                or len(key) > 128
                or key.casefold() in _SENSITIVE_EVIDENCE_KEYS
            ):
                raise LedgerConflict("evidence_details_invalid")
            _validate_evidence_detail(item, depth=depth + 1)
        return
    raise LedgerConflict("evidence_details_invalid")


@dataclass(frozen=True)
class LedgerEntry:
    command_id: str
    job_id: str
    idempotency_key: str
    payload_hash: str
    state: LedgerState
    result: dict[str, Any] | None
    error_code: str | None
    package_id: str
    package_version: str
    package_sha256: str
    session_id: str
    device_id: str
    sequence: int
    cancel_requested: bool
    kind: str
    binding: dict[str, Any] | None


class LedgerConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class LedgerEvidenceEvent:
    event_id: str
    job_id: str
    command_id: str
    source: Literal["agent", "host"]
    source_sequence: int
    milestone: str
    outcome: str
    summary: str
    details: dict[str, Any]
    source_timestamp: str
    event_digest: str


class CommandLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._connection:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    package_id TEXT NOT NULL,
                    package_version TEXT NOT NULL,
                    package_sha256 TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL DEFAULT 'observe',
                    binding_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_evidence (
                    event_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    command_id TEXT NOT NULL,
                    source TEXT NOT NULL CHECK (source IN ('agent', 'host')),
                    source_sequence INTEGER NOT NULL CHECK (source_sequence >= 0),
                    milestone TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    source_timestamp TEXT NOT NULL,
                    event_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (job_id, source, source_sequence)
                );
                CREATE TRIGGER IF NOT EXISTS execution_evidence_no_update
                BEFORE UPDATE ON execution_evidence
                BEGIN
                    SELECT RAISE(ABORT, 'execution_evidence_append_only');
                END;
                CREATE TRIGGER IF NOT EXISTS execution_evidence_no_delete
                BEFORE DELETE ON execution_evidence
                BEGIN
                    SELECT RAISE(ABORT, 'execution_evidence_append_only');
                END;
                """
            )
            columns = {
                str(row["name"])
                for row in self._connection.execute(
                    "PRAGMA table_info(commands)"
                ).fetchall()
            }
            if "kind" not in columns:
                self._connection.execute(
                    "ALTER TABLE commands ADD COLUMN kind TEXT NOT NULL DEFAULT 'observe'"
                )
            if "binding_json" not in columns:
                self._connection.execute(
                    "ALTER TABLE commands ADD COLUMN binding_json TEXT"
                )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def record_received(
        self,
        *,
        command_id: str,
        job_id: str,
        idempotency_key: str,
        payload_hash: str,
        package: dict[str, str],
        session_id: str,
        device_id: str,
        kind: str = "observe",
        binding: dict[str, Any] | None = None,
    ) -> tuple[LedgerEntry, bool]:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM commands WHERE command_id = ?", (command_id,)
            ).fetchone()
            matched_command_id = row is not None
            if row is None:
                row = self._connection.execute(
                    "SELECT * FROM commands WHERE device_id = ? AND idempotency_key = ? "
                    "ORDER BY created_at LIMIT 1",
                    (device_id, idempotency_key),
                ).fetchone()
            if row is not None:
                entry = self._entry(row)
                if (
                    entry.payload_hash != payload_hash
                    or entry.idempotency_key != idempotency_key
                    or entry.device_id != device_id
                    or entry.kind != kind
                    or entry.binding != binding
                ):
                    raise LedgerConflict(
                        "replay_payload_mismatch"
                        if matched_command_id
                        else "idempotency_conflict"
                    )
                if entry.command_id == command_id and entry.job_id != job_id:
                    raise LedgerConflict("replay_payload_mismatch")
                return entry, False
            now = _now()
            self._connection.execute(
                """
                INSERT INTO commands(command_id, job_id, idempotency_key, payload_hash, state,
                    package_id, package_version, package_sha256, session_id, device_id,
                    kind, binding_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'received', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    job_id,
                    idempotency_key,
                    payload_hash,
                    package["package_id"],
                    package["version"],
                    package["sha256"],
                    session_id,
                    device_id,
                    kind,
                    (
                        json.dumps(binding, ensure_ascii=False, sort_keys=True)
                        if binding is not None
                        else None
                    ),
                    now,
                    now,
                ),
            )
            return self.get(command_id), True  # type: ignore[return-value]

    def transition(
        self,
        command_id: str,
        state: LedgerState,
        *,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        sequence: int | None = None,
    ) -> LedgerEntry:
        with self._lock, self._connection:
            current = self.get(command_id)
            if current is None:
                raise LedgerConflict("command_not_found")
            if current.state in TERMINAL:
                if current.state == state and current.result == result and current.error_code == error_code:
                    return current
                raise LedgerConflict("terminal_result_conflict")
            order = {
                "received": 0,
                "accepted": 1,
                "started": 2,
                "succeeded": 3,
                "failed": 3,
                "cancelled": 3,
                "outcome_unknown": 3,
            }
            if order[state] < order[current.state]:
                raise LedgerConflict("invalid_transition")
            self._connection.execute(
                "UPDATE commands SET state = ?, result_json = ?, error_code = ?, "
                "sequence = COALESCE(?, sequence), updated_at = ? WHERE command_id = ?",
                (
                    state,
                    json.dumps(result, ensure_ascii=False, sort_keys=True) if result is not None else None,
                    error_code,
                    sequence,
                    _now(),
                    command_id,
                ),
            )
            return self.get(command_id)  # type: ignore[return-value]

    def request_cancel(self, command_id: str) -> LedgerEntry | None:
        with self._lock, self._connection:
            if self.get(command_id) is None:
                return None
            self._connection.execute(
                "UPDATE commands SET cancel_requested = 1, updated_at = ? WHERE command_id = ?",
                (_now(), command_id),
            )
            return self.get(command_id)

    def get(self, command_id: str) -> LedgerEntry | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM commands WHERE command_id = ?", (command_id,)
            ).fetchone()
        return self._entry(row) if row else None

    def reconcile_status(self, command_id: str, payload_hash: str) -> tuple[str, LedgerEntry | None]:
        entry = self.get(command_id)
        if entry is None:
            return "not_started", None
        if entry.payload_hash != payload_hash:
            raise LedgerConflict("replay_payload_mismatch")
        if entry.state in {"received", "accepted"}:
            return "not_started", entry
        if entry.state == "started":
            return "started", entry
        return "terminal", entry

    def append_evidence(
        self,
        *,
        event_id: str,
        command_id: str,
        source: Literal["agent", "host"],
        source_sequence: int,
        milestone: str,
        outcome: str,
        summary: str,
        details: dict[str, Any] | None = None,
        source_timestamp: str | None = None,
    ) -> tuple[LedgerEvidenceEvent, bool]:
        """Append bounded recovery evidence without changing command lifecycle state."""

        if source not in {"agent", "host"}:
            raise LedgerConflict("evidence_source_invalid")
        if source_sequence < 0:
            raise LedgerConflict("evidence_sequence_invalid")
        if not event_id or len(event_id) > 256:
            raise LedgerConflict("evidence_event_id_invalid")
        if not milestone or len(milestone) > 128:
            raise LedgerConflict("evidence_milestone_invalid")
        if not outcome or len(outcome) > 64:
            raise LedgerConflict("evidence_outcome_invalid")
        if not summary or len(summary) > 512:
            raise LedgerConflict("evidence_summary_invalid")
        entry = self.get(command_id)
        if entry is None:
            raise LedgerConflict("command_not_found")
        safe_details = details or {}
        if not isinstance(safe_details, dict) or len(safe_details) > 32:
            raise LedgerConflict("evidence_details_invalid")
        _validate_evidence_detail(safe_details)
        try:
            details_json = json.dumps(
                safe_details,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise LedgerConflict("evidence_details_invalid") from error
        if len(details_json.encode("utf-8")) > 8_192:
            raise LedgerConflict("evidence_details_invalid")
        timestamp = source_timestamp or _now()
        digest_payload = json.dumps(
            {
                "event_id": event_id,
                "job_id": entry.job_id,
                "command_id": command_id,
                "source": source,
                "source_sequence": source_sequence,
                "milestone": milestone,
                "outcome": outcome,
                "summary": summary,
                "details": safe_details,
                "source_timestamp": timestamp,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        event_digest = f"sha256:{sha256(digest_payload.encode('utf-8')).hexdigest()}"
        candidate = LedgerEvidenceEvent(
            event_id=event_id,
            job_id=entry.job_id,
            command_id=command_id,
            source=source,
            source_sequence=source_sequence,
            milestone=milestone,
            outcome=outcome,
            summary=summary,
            details=safe_details,
            source_timestamp=timestamp,
            event_digest=event_digest,
        )
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM execution_evidence WHERE event_id = ? OR "
                "(job_id = ? AND source = ? AND source_sequence = ?) LIMIT 1",
                (event_id, entry.job_id, source, source_sequence),
            ).fetchone()
            if row is not None:
                existing = self._evidence_entry(row)
                if existing == candidate:
                    return existing, False
                raise LedgerConflict("evidence_conflict")
            latest = self._connection.execute(
                "SELECT MAX(source_sequence) FROM execution_evidence "
                "WHERE job_id = ? AND source = ?",
                (entry.job_id, source),
            ).fetchone()[0]
            if latest is not None and source_sequence <= int(latest):
                raise LedgerConflict("evidence_sequence_rejected")
            self._connection.execute(
                """
                INSERT INTO execution_evidence(
                    event_id, job_id, command_id, source, source_sequence,
                    milestone, outcome, summary, details_json, source_timestamp,
                    event_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.event_id,
                    candidate.job_id,
                    candidate.command_id,
                    candidate.source,
                    candidate.source_sequence,
                    candidate.milestone,
                    candidate.outcome,
                    candidate.summary,
                    details_json,
                    candidate.source_timestamp,
                    candidate.event_digest,
                    _now(),
                ),
            )
        return candidate, True

    def list_evidence(self, command_id: str, *, limit: int = 256) -> list[LedgerEvidenceEvent]:
        if limit < 1 or limit > 256:
            raise LedgerConflict("evidence_limit_invalid")
        entry = self.get(command_id)
        if entry is None:
            return []
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM execution_evidence WHERE command_id = ? "
                "ORDER BY source_timestamp, source, source_sequence LIMIT ?",
                (command_id, limit),
            ).fetchall()
        return [self._evidence_entry(row) for row in rows]

    def reconcile_evidence(
        self, command_id: str, payload_hash: str
    ) -> tuple[str, LedgerEntry | None, tuple[LedgerEvidenceEvent, ...]]:
        """Return source-attributed evidence for the existing read-only reconcile path."""

        status, entry = self.reconcile_status(command_id, payload_hash)
        return status, entry, tuple(self.list_evidence(command_id))

    def set_paused(self, paused: bool) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO settings(key, value) VALUES ('paused', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("1" if paused else "0",),
            )

    def is_paused(self) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM settings WHERE key = 'paused'"
            ).fetchone()
        return bool(row and row[0] == "1")

    def initialize_write_lock(self, enabled: bool) -> bool:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT value FROM settings WHERE key = 'write_lock'"
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO settings(key, value) VALUES ('write_lock', ?)",
                    ("1" if enabled else "0",),
                )
                return enabled
            return row[0] == "1"

    def set_write_lock(self, enabled: bool) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO settings(key, value) VALUES ('write_lock', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("1" if enabled else "0",),
            )

    def is_write_lock_enabled(self) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM settings WHERE key = 'write_lock'"
            ).fetchone()
        return bool(row and row[0] == "1")

    def recover_interrupted_programs(self) -> tuple[int, int]:
        """Never make an interrupted started write eligible for blind retry."""

        with self._lock, self._connection:
            now = _now()
            unknown = self._connection.execute(
                "UPDATE commands SET state = 'outcome_unknown', "
                "error_code = 'outcome_unknown', updated_at = ? "
                "WHERE state = 'started' AND kind = 'program_commit'",
                (now,),
            ).rowcount
            failed = self._connection.execute(
                "UPDATE commands SET state = 'failed', "
                "error_code = 'agent_restarted', updated_at = ? "
                "WHERE state = 'started' AND kind IN "
                "('program_preview', 'program_validate')",
                (now,),
            ).rowcount
        return int(unknown), int(failed)

    def next_sequence(self) -> int:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT value FROM settings WHERE key = 'sequence'"
            ).fetchone()
            value = int(row[0]) + 1 if row else 1
            self._connection.execute(
                "INSERT INTO settings(key, value) VALUES ('sequence', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(value),),
            )
            return value

    def last_sequence(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM settings WHERE key = 'sequence'"
            ).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _entry(row: sqlite3.Row) -> LedgerEntry:
        return LedgerEntry(
            command_id=str(row["command_id"]),
            job_id=str(row["job_id"]),
            idempotency_key=str(row["idempotency_key"]),
            payload_hash=str(row["payload_hash"]),
            state=str(row["state"]),  # type: ignore[arg-type]
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error_code=row["error_code"],
            package_id=str(row["package_id"]),
            package_version=str(row["package_version"]),
            package_sha256=str(row["package_sha256"]),
            session_id=str(row["session_id"]),
            device_id=str(row["device_id"]),
            sequence=int(row["sequence"]),
            cancel_requested=bool(row["cancel_requested"]),
            kind=str(row["kind"]),
            binding=(
                json.loads(row["binding_json"])
                if row["binding_json"]
                else None
            ),
        )

    @staticmethod
    def _evidence_entry(row: sqlite3.Row) -> LedgerEvidenceEvent:
        return LedgerEvidenceEvent(
            event_id=str(row["event_id"]),
            job_id=str(row["job_id"]),
            command_id=str(row["command_id"]),
            source=str(row["source"]),  # type: ignore[arg-type]
            source_sequence=int(row["source_sequence"]),
            milestone=str(row["milestone"]),
            outcome=str(row["outcome"]),
            summary=str(row["summary"]),
            details=json.loads(row["details_json"]),
            source_timestamp=str(row["source_timestamp"]),
            event_digest=str(row["event_digest"]),
        )
