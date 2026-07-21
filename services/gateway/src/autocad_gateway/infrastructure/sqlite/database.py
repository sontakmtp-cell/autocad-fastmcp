"""Small SQLite lifecycle wrapper with fail-closed migration checks."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Iterator


class DatabaseError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteDatabase:
    """One Gateway worker's SQLite connection; transactions are deliberately short."""

    def __init__(self, path: str | Path, *, migration_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.migration_path = Path(migration_path) if migration_path else Path(__file__).parent / "migrations" / "0001_phase3.sql"
        self.connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self.migration_checksum: str | None = None

    @property
    def is_open(self) -> bool:
        return self.connection is not None

    async def open(self) -> None:
        if self.connection is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None, timeout=5
        )
        connection.row_factory = sqlite3.Row
        with self._lock:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 5000")
            self.connection = connection
        await self.migrate()

    async def migrate(self) -> None:
        connection = self._require_connection()
        if not self.migration_path.is_file():
            raise DatabaseError(f"migration file not found: {self.migration_path}")
        sql = self.migration_path.read_text(encoding="utf-8")
        checksum = sha256(sql.encode("utf-8")).hexdigest()
        with self.transaction() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            rows = conn.execute(
                "SELECT version, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            existing = {int(row[0]): str(row[1]) for row in rows}
            if 1 in existing and existing[1] != checksum:
                raise DatabaseError("migration checksum mismatch for version 1")
            if 1 not in existing:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations(version, checksum, applied_at) VALUES (1, ?, ?)",
                    (checksum, utc_now()),
                )
        self.migration_checksum = checksum
        del connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._require_connection()
        with self._lock:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    @contextmanager
    def read_connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._require_connection()
        with self._lock:
            yield connection

    async def backup_to(self, target: str | Path) -> None:
        destination = Path(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self._require_connection()
        with self._lock:
            backup = sqlite3.connect(str(destination), check_same_thread=False)
            try:
                source.backup(backup)
            finally:
                backup.close()

    async def close(self) -> None:
        if self.connection is None:
            return
        with self._lock:
            self.connection.close()
            self.connection = None

    def _require_connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise DatabaseError("SQLite database is not open")
        return self.connection


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"
