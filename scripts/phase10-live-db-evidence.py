"""Emit read-only durable DB evidence for the Phase 10 live acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    REPO_ROOT
    / "services"
    / "gateway"
    / "src"
    / "autocad_gateway"
    / "infrastructure"
    / "sqlite"
    / "migrations"
)
ANCHOR_JOB_IDS = (
    "job-cc9f6fe5-de83-4a7b-98a4-994d0655306f",
    "job-69fae0e5-8a6e-40da-8bb8-86bb6c1998d0",
    "job-ccc6b6bd-e986-4e3f-80d2-f221b5d296ac",
    "job-5776f7b6-4c7b-4841-b240-87b43b269e99",
    "job-655884d6-2da7-4c6b-bb0f-5468365a7835",
    "job-fb6d6021-c333-46d8-8b89-9b2ea2b3d0a8",
)
SCENE_IDS = (
    "scn_82e037c2efcf42fc8945b07758cb02ab",
    "scn_3085e66db51f4534946c0a0cb453dc16",
    "scn_83d07dede78e4b73b2411c4bd31f77ae",
)
SCENE_SECTIONS = {"nodes", "relations", "contours", "features", "issues", "evidence"}

NO_WRITE_SQL = """
WITH events(bucket, id, event, event_at) AS (
    SELECT 'cad_programs', program_id, 'created', created_at
      FROM cad_programs
     WHERE owner_subject=:owner AND device_id=:device
    UNION ALL
    SELECT 'cad_programs', program_id, 'updated', updated_at
      FROM cad_programs
     WHERE owner_subject=:owner AND device_id=:device
    UNION ALL
    SELECT 'cad_program_revisions', r.program_id || ':' || r.revision, 'created', r.created_at
      FROM cad_program_revisions r JOIN cad_programs p USING(program_id)
     WHERE r.owner_subject=:owner AND p.device_id=:device
    UNION ALL
    SELECT 'cad_previews', v.preview_id, 'created', v.created_at
      FROM cad_previews v JOIN cad_programs p USING(program_id)
     WHERE v.owner_subject=:owner AND p.device_id=:device
    UNION ALL
    SELECT 'cad_validations', v.validation_id, 'created', v.created_at
      FROM cad_validations v JOIN cad_programs p USING(program_id)
     WHERE v.owner_subject=:owner AND p.device_id=:device
    UNION ALL
    SELECT 'execution_intents', intent_id, 'created', created_at
      FROM execution_intents
     WHERE owner_subject=:owner AND device_id=:device
    UNION ALL
    SELECT 'consents', c.consent_id, 'requested', c.requested_at
      FROM consents c JOIN execution_intents i USING(intent_id)
     WHERE c.owner_subject=:owner AND i.device_id=:device
    UNION ALL
    SELECT 'consents', c.consent_id, 'decided', c.decided_at
      FROM consents c JOIN execution_intents i USING(intent_id)
     WHERE c.owner_subject=:owner AND i.device_id=:device AND c.decided_at IS NOT NULL
    UNION ALL
    SELECT 'consents', c.consent_id, 'consumed', c.consumed_at
      FROM consents c JOIN execution_intents i USING(intent_id)
     WHERE c.owner_subject=:owner AND i.device_id=:device AND c.consumed_at IS NOT NULL
    UNION ALL
    SELECT 'write_jobs', job_id, 'created', created_at
      FROM jobs
     WHERE owner_subject=:owner AND device_id=:device AND effect_class='write'
    UNION ALL
    SELECT 'write_jobs', job_id, 'updated', updated_at
      FROM jobs
     WHERE owner_subject=:owner AND device_id=:device AND effect_class='write'
    UNION ALL
    SELECT 'cad_execution_receipts', r.receipt_id, 'created', r.created_at
      FROM cad_execution_receipts r JOIN cad_programs p USING(program_id)
     WHERE r.owner_subject=:owner AND p.device_id=:device
    UNION ALL
    SELECT 'program_idempotency', action || ':' || idempotency_key, 'created', created_at
      FROM program_idempotency
     WHERE owner_subject=:owner
    UNION ALL
    SELECT 'cad_program_write_locks', device_id || ':' || document_id || ':' || job_id,
           'created', created_at
      FROM cad_program_write_locks
     WHERE device_id=:device
    UNION ALL
    SELECT 'workflow_write_actions', a.action_id, 'created', a.created_at
      FROM workflow_actions a JOIN workflow_runs r USING(run_id)
     WHERE r.owner_subject=:owner AND r.device_id=:device AND a.effect_class='write'
    UNION ALL
    SELECT 'workflow_write_actions', a.action_id, 'updated', a.updated_at
      FROM workflow_actions a JOIN workflow_runs r USING(run_id)
     WHERE r.owner_subject=:owner AND r.device_id=:device AND a.effect_class='write'
)
SELECT bucket, id, event, event_at
  FROM events
 WHERE julianday(event_at) >= julianday(:window_start)
   AND julianday(event_at) <= julianday(:window_end)
 ORDER BY bucket, id, event, event_at
"""

SNAPSHOT_QUERIES = {
    "cad_programs": (
        "SELECT * FROM cad_programs WHERE owner_subject=? AND device_id=? "
        "ORDER BY program_id",
        ("owner", "device"),
    ),
    "cad_program_revisions": (
        "SELECT r.* FROM cad_program_revisions r JOIN cad_programs p USING(program_id) "
        "WHERE r.owner_subject=? AND p.device_id=? ORDER BY r.program_id,r.revision",
        ("owner", "device"),
    ),
    "cad_previews": (
        "SELECT v.* FROM cad_previews v JOIN cad_programs p USING(program_id) "
        "WHERE v.owner_subject=? AND p.device_id=? ORDER BY v.preview_id",
        ("owner", "device"),
    ),
    "cad_validations": (
        "SELECT v.* FROM cad_validations v JOIN cad_programs p USING(program_id) "
        "WHERE v.owner_subject=? AND p.device_id=? ORDER BY v.validation_id",
        ("owner", "device"),
    ),
    "execution_intents": (
        "SELECT * FROM execution_intents WHERE owner_subject=? AND device_id=? "
        "ORDER BY intent_id",
        ("owner", "device"),
    ),
    "consents": (
        "SELECT c.* FROM consents c JOIN execution_intents i USING(intent_id) "
        "WHERE c.owner_subject=? AND i.device_id=? ORDER BY c.consent_id",
        ("owner", "device"),
    ),
    "write_jobs": (
        "SELECT * FROM jobs WHERE owner_subject=? AND device_id=? "
        "AND effect_class='write' ORDER BY job_id",
        ("owner", "device"),
    ),
    "cad_execution_receipts": (
        "SELECT r.* FROM cad_execution_receipts r JOIN cad_programs p USING(program_id) "
        "WHERE r.owner_subject=? AND p.device_id=? ORDER BY r.receipt_id",
        ("owner", "device"),
    ),
    "program_idempotency": (
        "SELECT * FROM program_idempotency WHERE owner_subject=? "
        "ORDER BY action,idempotency_key",
        ("owner",),
    ),
    "cad_program_write_locks": (
        "SELECT * FROM cad_program_write_locks WHERE device_id=? "
        "ORDER BY document_id,job_id",
        ("device",),
    ),
    "workflow_write_actions": (
        "SELECT a.* FROM workflow_actions a JOIN workflow_runs r USING(run_id) "
        "WHERE r.owner_subject=? AND r.device_id=? AND a.effect_class='write' "
        "ORDER BY a.action_id",
        ("owner", "device"),
    ),
}


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def _expected_migrations() -> list[dict[str, Any]]:
    result = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        version = int(path.stem.split("_", 1)[0])
        sql = path.read_text(encoding="utf-8")
        result.append(
            {"version": version, "checksum": hashlib.sha256(sql.encode()).hexdigest()}
        )
    return result


def collect_evidence(
    database: Path,
    *,
    owner: str,
    device: str,
    window_start: str,
    window_end: str,
) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"{database.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = _rows(connection.execute("PRAGMA foreign_key_check"))
        migrations = _rows(
            connection.execute(
                "SELECT version,checksum,applied_at FROM schema_migrations ORDER BY version"
            )
        )
        migration_identity = [
            {"version": row["version"], "checksum": row["checksum"]}
            for row in migrations
        ]
        parameters = {
            "owner": owner,
            "device": device,
            "window_start": window_start,
            "window_end": window_end,
        }
        no_write_events = _rows(connection.execute(NO_WRITE_SQL, parameters))

        job_marks = ",".join("?" for _ in ANCHOR_JOB_IDS)
        anchor_jobs = _rows(
            connection.execute(
                f"SELECT * FROM jobs WHERE job_id IN ({job_marks}) ORDER BY job_id",
                ANCHOR_JOB_IDS,
            )
        )
        scene_marks = ",".join("?" for _ in SCENE_IDS)
        scenes = _rows(
            connection.execute(
                f"SELECT * FROM scene_records WHERE scene_id IN ({scene_marks}) "
                "ORDER BY scene_id",
                SCENE_IDS,
            )
        )
        scene_sections = _rows(
            connection.execute(
                f"SELECT * FROM scene_sections WHERE scene_id IN ({scene_marks}) "
                "ORDER BY scene_id,section",
                SCENE_IDS,
            )
        )

        values = {"owner": owner, "device": device}
        snapshot = {
            name: _rows(
                connection.execute(sql, tuple(values[key] for key in parameter_keys))
            )
            for name, (sql, parameter_keys) in SNAPSHOT_QUERIES.items()
        }
        snapshot_json = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        sessions = _rows(
            connection.execute(
                "SELECT s.*,d.status AS device_status,d.owner_subject "
                "FROM agent_sessions s JOIN devices d USING(device_id) "
                "WHERE s.device_id=? ORDER BY s.connected_at DESC,s.session_id DESC",
                (device,),
            )
        )
        connection.execute("COMMIT")
    finally:
        connection.close()

    anchor_ids = {row["job_id"] for row in anchor_jobs}
    sections_by_scene = {
        scene_id: {
            row["section"] for row in scene_sections if row["scene_id"] == scene_id
        }
        for scene_id in SCENE_IDS
    }
    active_sessions = [
        row
        for row in sessions
        if row["disconnected_at"] is None
        and row["device_status"] == "online"
        and row["owner_subject"] == owner
    ]
    gates = {
        "integrity_ok": integrity == ["ok"],
        "foreign_keys_ok": not foreign_keys,
        "migrations_ok": migration_identity == _expected_migrations(),
        "no_write_events_in_window": not no_write_events,
        "anchor_jobs_ok": anchor_ids == set(ANCHOR_JOB_IDS)
        and all(
            row["owner_subject"] == owner
            and row["device_id"] == device
            and row["effect_class"] == "read"
            and row["state"] == "succeeded"
            for row in anchor_jobs
        ),
        "scenes_ok": {row["scene_id"] for row in scenes} == set(SCENE_IDS)
        and all(
            row["owner_subject"] == owner
            and row["device_id"] == device
            and row["complete"] == 1
            for row in scenes
        )
        and all(sections == SCENE_SECTIONS for sections in sections_by_scene.values()),
        "active_session_ok": len(active_sessions) == 1,
    }
    return {
        "schema_version": "cad.phase10-live-db-evidence/1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "database": str(database),
        "scope": {
            "owner_subject": owner,
            "device_id": device,
            "window_start": window_start,
            "window_end": window_end,
            "anchor_job_ids": list(ANCHOR_JOB_IDS),
            "scene_ids": list(SCENE_IDS),
        },
        "database_checks": {
            "integrity_check": integrity,
            "foreign_key_check": foreign_keys,
            "schema_migrations": migrations,
        },
        "retrospective_no_write_events": no_write_events,
        "anchor_jobs": anchor_jobs,
        "scenes": scenes,
        "scene_sections": scene_sections,
        "write_snapshot": {
            "sha256": "sha256:" + hashlib.sha256(snapshot_json.encode()).hexdigest(),
            "tables": snapshot,
        },
        "agent_sessions": sessions,
        "active_agent_session_id": (
            active_sessions[0]["session_id"] if len(active_sessions) == 1 else None
        ),
        "gate_results": gates,
        "status": "PASS" if all(gates.values()) else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--owner-subject", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument(
        "--window-start", default="2026-07-30T06:15:00+00:00"
    )
    parser.add_argument("--window-end", default="2026-07-30T06:43:00+00:00")
    args = parser.parse_args()
    evidence = collect_evidence(
        args.database,
        owner=args.owner_subject,
        device=args.device_id,
        window_start=args.window_start,
        window_end=args.window_end,
    )
    json.dump(evidence, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
