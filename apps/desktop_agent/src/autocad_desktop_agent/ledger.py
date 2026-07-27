"""Local durable command ledger; terminal evidence is stored before transmission."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
