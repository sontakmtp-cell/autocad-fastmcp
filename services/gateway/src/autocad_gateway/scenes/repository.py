"""Immutable SQLite scene repository."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from ..infrastructure.sqlite.database import SqliteDatabase, new_id, utc_now


class SceneRepositoryConflict(RuntimeError):
    """A duplicate identity was reused with different immutable content."""


def _json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


class SceneRepository:
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    async def create(
        self,
        *,
        owner_subject: str,
        root: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
        request_hash: str,
        idempotency_key: str,
        tolerance_digest: str,
        build_options_digest: str,
        section_digests: dict[str, str],
        correlation_id: str,
        expires_at: str,
    ) -> tuple[dict[str, Any], bool]:
        identity = (
            owner_subject,
            str(root["source_digest"]),
            str(root["projection_version"]),
            str(root["engine_version"]),
            str(root["profile_id"]),
            tolerance_digest,
            build_options_digest,
        )
        with self.database.transaction() as conn:
            replay = conn.execute(
                """
                SELECT r.*,
                       b.request_hash AS binding_request_hash,
                       b.source_digest AS binding_source_digest,
                       b.projection_version AS binding_projection_version,
                       b.engine_version AS binding_engine_version,
                       b.profile_id AS binding_profile_id,
                       b.tolerance_digest AS binding_tolerance_digest,
                       b.build_options_digest AS binding_build_options_digest,
                       b.scene_digest AS binding_scene_digest
                FROM scene_request_bindings b
                JOIN scene_records r ON r.scene_id = b.scene_id
                WHERE b.owner_subject = ? AND b.idempotency_key = ?
                """,
                (owner_subject, idempotency_key),
            ).fetchone()
            if replay is not None:
                if (
                    str(replay["binding_request_hash"]) != request_hash
                    or (
                        str(replay["binding_source_digest"]),
                        str(replay["binding_projection_version"]),
                        str(replay["binding_engine_version"]),
                        str(replay["binding_profile_id"]),
                        str(replay["binding_tolerance_digest"]),
                        str(replay["binding_build_options_digest"]),
                    )
                    != identity[1:]
                    or str(replay["binding_scene_digest"]) != str(root["scene_digest"])
                ):
                    raise SceneRepositoryConflict("idempotency_conflict")
                return self._record(replay), True

            duplicate = conn.execute(
                """
                SELECT * FROM scene_records
                WHERE owner_subject = ? AND source_digest = ?
                  AND projection_version = ? AND engine_version = ?
                  AND profile_id = ? AND tolerance_digest = ?
                  AND build_options_digest = ?
                """,
                identity,
            ).fetchone()
            if duplicate is not None:
                if str(duplicate["scene_digest"]) != str(root["scene_digest"]):
                    raise SceneRepositoryConflict("scene_conflict")
                conn.execute(
                    """
                    INSERT INTO scene_request_bindings(
                        owner_subject, idempotency_key, request_hash, source_digest,
                        projection_version, engine_version, profile_id,
                        tolerance_digest, build_options_digest, scene_digest,
                        scene_id, correlation_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        owner_subject,
                        idempotency_key,
                        request_hash,
                        *identity[1:],
                        str(root["scene_digest"]),
                        str(duplicate["scene_id"]),
                        correlation_id,
                        utc_now(),
                    ),
                )
                return self._record(duplicate), True

            scene_id = str(root.get("scene_id") or new_id("scn").replace("scn-", "scn_"))
            persisted_root = dict(root)
            persisted_root["scene_id"] = scene_id
            try:
                conn.execute(
                    """
                    INSERT INTO scene_records(
                        scene_id, owner_subject, device_id, source_snapshot_id,
                        document_id, document_revision, space, projection_version,
                        engine_version, profile_id, tolerance_digest,
                        build_options_digest, source_digest, scene_digest,
                        request_hash, idempotency_key, complete, root_json,
                        counts_json, warnings_json, capabilities_json,
                        correlation_id, created_at, expires_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        scene_id,
                        owner_subject,
                        str(root["device_id"]),
                        str(root["source_snapshot_id"]),
                        str(root["document_id"]),
                        str(root["document_revision"]),
                        str(root["space"]),
                        str(root["projection_version"]),
                        str(root["engine_version"]),
                        str(root["profile_id"]),
                        tolerance_digest,
                        build_options_digest,
                        str(root["source_digest"]),
                        str(root["scene_digest"]),
                        request_hash,
                        idempotency_key,
                        int(bool(root["complete"])),
                        _json(persisted_root),
                        _json(root["counts"]),
                        _json(root.get("warnings", [])),
                        _json(root.get("capabilities", [])),
                        correlation_id,
                        utc_now(),
                        expires_at,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO scene_request_bindings(
                        owner_subject, idempotency_key, request_hash, source_digest,
                        projection_version, engine_version, profile_id,
                        tolerance_digest, build_options_digest, scene_digest,
                        scene_id, correlation_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        owner_subject,
                        idempotency_key,
                        request_hash,
                        *identity[1:],
                        str(root["scene_digest"]),
                        scene_id,
                        correlation_id,
                        utc_now(),
                    ),
                )
                for section, items in sorted(sections.items()):
                    section_digest = str(section_digests[section])
                    conn.execute(
                        """
                        INSERT INTO scene_sections(
                            scene_id, section, payload_json, item_count, section_digest
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (scene_id, section, _json(items), len(items), section_digest),
                    )
            except sqlite3.IntegrityError as error:
                raise SceneRepositoryConflict("scene_conflict") from error
            row = conn.execute(
                "SELECT * FROM scene_records WHERE scene_id = ?", (scene_id,)
            ).fetchone()
        assert row is not None
        return self._record(row), False

    async def get(
        self, owner_subject: str, scene_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM scene_records
                WHERE owner_subject = ? AND scene_id = ?
                """,
                (owner_subject, scene_id),
            ).fetchone()
        return self._record(row) if row is not None else None

    async def get_section(
        self, owner_subject: str, scene_id: str, section: str
    ) -> list[dict[str, Any]] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                """
                SELECT s.payload_json
                FROM scene_sections s
                JOIN scene_records r ON r.scene_id = s.scene_id
                WHERE r.owner_subject = ? AND r.scene_id = ? AND s.section = ?
                """,
                (owner_subject, scene_id, section),
            ).fetchone()
        if row is None:
            return None
        value = json.loads(str(row["payload_json"]))
        if not isinstance(value, list):
            raise SceneRepositoryConflict("scene_invalid")
        return value

    async def list(
        self, owner_subject: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self.database.read_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM scene_records
                WHERE owner_subject = ?
                ORDER BY created_at DESC, scene_id
                LIMIT ?
                """,
                (owner_subject, min(max(limit, 1), 100)),
            ).fetchall()
        return [self._record(row) for row in rows]

    async def delete_expired(self, *, now: str | None = None) -> int:
        cutoff = now or datetime.now(timezone.utc).isoformat()
        with self.database.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM scene_records WHERE expires_at <= ?", (cutoff,)
            )
        return int(cursor.rowcount)

    @staticmethod
    def _record(row: Any) -> dict[str, Any]:
        return {
            "scene_id": str(row["scene_id"]),
            "owner_subject": str(row["owner_subject"]),
            "device_id": str(row["device_id"]),
            "source_snapshot_id": str(row["source_snapshot_id"]),
            "document_id": str(row["document_id"]),
            "document_revision": str(row["document_revision"]),
            "space": str(row["space"]),
            "projection_version": str(row["projection_version"]),
            "engine_version": str(row["engine_version"]),
            "profile_id": str(row["profile_id"]),
            "tolerance_digest": str(row["tolerance_digest"]),
            "source_digest": str(row["source_digest"]),
            "scene_digest": str(row["scene_digest"]),
            "complete": bool(row["complete"]),
            "root": json.loads(str(row["root_json"])),
            "counts": json.loads(str(row["counts_json"])),
            "warnings": json.loads(str(row["warnings_json"])),
            "capabilities": json.loads(str(row["capabilities_json"])),
            "created_at": str(row["created_at"]),
            "expires_at": str(row["expires_at"]),
        }
