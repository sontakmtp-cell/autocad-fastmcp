"""Small SQLite lifecycle wrapper with fail-closed migration checks."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Iterator


class DatabaseError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Migration:
    version: int
    path: Path
    sql: str
    checksum: str


def _migration_version(path: Path, *, explicit_file: bool) -> int:
    prefix = path.stem.split("_", 1)[0]
    if prefix.isdigit():
        version = int(prefix)
        if version > 0:
            return version
    if explicit_file:
        # Compatibility for tests and callers that supplied the old single-file
        # migration override. The production migration directory is always named.
        return 1
    raise DatabaseError(f"invalid migration filename: {path.name}")


def _sql_statements(sql: str) -> Iterator[str]:
    """Yield complete SQLite statements without breaking quoted semicolons."""

    buffer: list[str] = []
    for character in sql:
        buffer.append(character)
        if character != ";":
            continue
        candidate = "".join(buffer)
        if sqlite3.complete_statement(candidate):
            if candidate.strip():
                yield candidate
            buffer.clear()
    remainder = "".join(buffer).strip()
    if remainder:
        # A trailing comment is harmless. Any other incomplete statement is a
        # malformed migration and must fail before history is recorded.
        uncommented = "\n".join(
            line for line in remainder.splitlines() if not line.lstrip().startswith("--")
        ).strip()
        if uncommented:
            raise DatabaseError("migration contains an incomplete SQL statement")


class SqliteDatabase:
    """One Gateway worker's SQLite connection; transactions are deliberately short."""

    def __init__(self, path: str | Path, *, migration_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.migration_path = (
            Path(migration_path)
            if migration_path
            else Path(__file__).parent / "migrations"
        )
        self.connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self.migration_checksum: str | None = None
        self.migration_checksums: dict[int, str] = {}
        self.migrations_valid = False

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
        try:
            with self._lock:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA busy_timeout = 5000")
                self.connection = connection
            await self.migrate()
        except BaseException:
            with self._lock:
                try:
                    connection.close()
                finally:
                    self.connection = None
                    self.migration_checksum = None
                    self.migration_checksums = {}
                    self.migrations_valid = False
            raise

    async def migrate(self) -> None:
        self.migrations_valid = False
        migrations = self._discover_migrations()
        with self.transaction() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )

        with self.read_connection() as conn:
            rows = conn.execute(
                "SELECT version, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
        applied = {int(row[0]): str(row[1]) for row in rows}
        available = {migration.version: migration for migration in migrations}

        missing = sorted(set(applied) - set(available))
        if missing:
            raise DatabaseError(
                "applied migration file is missing for version "
                + ", ".join(str(version) for version in missing)
            )
        for version, checksum in applied.items():
            if available[version].checksum != checksum:
                raise DatabaseError(f"migration checksum mismatch for version {version}")

        for migration in migrations:
            if migration.version in applied:
                continue
            self._apply_migration(migration)
            applied[migration.version] = migration.checksum

        expected = {migration.version: migration.checksum for migration in migrations}
        if applied != expected:
            raise DatabaseError("migration history does not match available migrations")
        self.migration_checksums = dict(sorted(expected.items()))
        self.migration_checksum = migrations[-1].checksum
        self.migrations_valid = True

    def verify_migration_state(self) -> bool:
        if self.connection is None or not self.migrations_valid:
            return False
        try:
            migrations = self._discover_migrations()
            expected = {migration.version: migration.checksum for migration in migrations}
            with self.read_connection() as conn:
                rows = conn.execute(
                    "SELECT version, checksum FROM schema_migrations ORDER BY version"
                ).fetchall()
            actual = {int(row[0]): str(row[1]) for row in rows}
        except (DatabaseError, sqlite3.Error, OSError):
            return False
        return actual == expected == self.migration_checksums

    def _discover_migrations(self) -> list[Migration]:
        explicit_file = self.migration_path.is_file()
        if explicit_file:
            paths = [self.migration_path]
        elif self.migration_path.is_dir():
            paths = sorted(self.migration_path.glob("*.sql"))
        else:
            raise DatabaseError(f"migration path not found: {self.migration_path}")
        if not paths:
            raise DatabaseError(f"no migration files found: {self.migration_path}")

        migrations: list[Migration] = []
        versions: set[int] = set()
        for path in paths:
            version = _migration_version(path, explicit_file=explicit_file)
            if version in versions:
                raise DatabaseError(f"duplicate migration version: {version}")
            try:
                sql = path.read_text(encoding="utf-8")
            except OSError as error:
                raise DatabaseError(f"cannot read migration file: {path}") from error
            if not sql.strip():
                raise DatabaseError(f"migration file is empty: {path}")
            migrations.append(
                Migration(
                    version=version,
                    path=path,
                    sql=sql,
                    checksum=sha256(sql.encode("utf-8")).hexdigest(),
                )
            )
            versions.add(version)
        migrations.sort(key=lambda migration: migration.version)
        return migrations

    def _apply_migration(self, migration: Migration) -> None:
        connection = self._require_connection()
        with self._lock:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in _sql_statements(migration.sql):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, checksum, applied_at) "
                    "VALUES (?, ?, ?)",
                    (migration.version, migration.checksum, utc_now()),
                )
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

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
            self.migrations_valid = False

    def _require_connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise DatabaseError("SQLite database is not open")
        return self.connection


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"
