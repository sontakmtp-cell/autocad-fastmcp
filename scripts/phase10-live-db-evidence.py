"""Emit read-only durable DB evidence for the Phase 10 live acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
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
COMMIT_RE = re.compile(r"[0-9a-f]{40}")

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


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _expected_migrations() -> list[dict[str, Any]]:
    result = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        version = int(path.stem.split("_", 1)[0])
        sql = path.read_text(encoding="utf-8")
        result.append(
            {"version": version, "checksum": hashlib.sha256(sql.encode()).hexdigest()}
        )
    return result


def _snapshot_digest(tables: dict[str, Any]) -> str:
    encoded = json.dumps(
        tables, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def _parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include an offset")
    return parsed


def _load_pre_restart_evidence(
    path: Path,
    *,
    owner: str,
    device: str,
    implementation_commit: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("pre-restart evidence is missing")
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("pre-restart evidence is not valid JSON") from error
    if not isinstance(evidence, dict):
        raise ValueError("pre-restart evidence must be an object")
    if evidence.get("schema_version") != "cad.phase10-live-db-evidence/1":
        raise ValueError("pre-restart evidence schema mismatch")
    if evidence.get("status") != "PASS":
        raise ValueError("pre-restart evidence did not pass")
    if evidence.get("implementation_commit") != implementation_commit:
        raise ValueError("pre-restart evidence commit differs from post capture")
    scope = evidence.get("scope")
    if not isinstance(scope, dict) or (
        scope.get("owner_subject") != owner or scope.get("device_id") != device
    ):
        raise ValueError("pre-restart evidence scope differs from post capture")
    _parse_timestamp(evidence.get("captured_at"), "pre-restart evidence captured_at")
    gates = evidence.get("gate_results")
    if not isinstance(gates, dict) or not gates or not all(gates.values()):
        raise ValueError("pre-restart evidence gates did not pass")
    snapshot = evidence.get("write_snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("tables"), dict):
        raise ValueError("pre-restart evidence write snapshot is missing")
    if snapshot.get("sha256") != _snapshot_digest(snapshot["tables"]):
        raise ValueError("pre-restart evidence write snapshot digest is invalid")
    active_session_id = evidence.get("active_agent_session_id")
    sessions = evidence.get("agent_sessions")
    active_sessions = [
        session
        for session in sessions
        if isinstance(session, dict)
        and session.get("session_id") == active_session_id
        and session.get("device_id") == device
        and session.get("owner_subject") == owner
        and session.get("device_status") == "online"
        and session.get("disconnected_at") is None
    ] if isinstance(sessions, list) and isinstance(active_session_id, str) else []
    if len(active_sessions) != 1:
        raise ValueError("pre-restart active agent session is invalid")
    return evidence


def collect_session_evidence(
    database: Path,
    *,
    owner: str,
    device: str,
    implementation_commit: str,
) -> dict[str, Any]:
    if not COMMIT_RE.fullmatch(implementation_commit):
        raise ValueError("implementation_commit must be a 40-character lowercase SHA")
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
        sessions = _rows(
            connection.execute(
                "SELECT s.*,d.status AS device_status,d.owner_subject "
                "FROM agent_sessions s JOIN devices d USING(device_id) "
                "WHERE s.device_id=? AND d.owner_subject=? "
                "ORDER BY s.connected_at DESC,s.session_id DESC",
                (device, owner),
            )
        )
        connection.execute("COMMIT")
    finally:
        connection.close()

    migration_identity = [
        {"version": row["version"], "checksum": row["checksum"]}
        for row in migrations
    ]
    active_sessions = [
        row
        for row in sessions
        if row["disconnected_at"] is None
        and row["device_status"] == "online"
        and row["protocol_version"] == "cad.agent/2"
    ]
    captured_at = datetime.now(timezone.utc).isoformat()
    gates = {
        "integrity_ok": integrity == ["ok"],
        "foreign_keys_ok": not foreign_keys,
        "migrations_ok": migration_identity == _expected_migrations(),
        "active_cad_agent_session_ok": len(active_sessions) == 1,
    }
    return {
        "schema_version": "cad.phase10-live-session-evidence/1",
        "implementation_commit": implementation_commit,
        "captured_at": captured_at,
        "database": str(database),
        "scope": {"owner_subject": owner, "device_id": device},
        "database_checks": {
            "integrity_check": integrity,
            "foreign_key_check": foreign_keys,
            "schema_migrations": migrations,
        },
        "agent_sessions": sessions,
        "active_agent_session_id": (
            active_sessions[0]["session_id"] if len(active_sessions) == 1 else None
        ),
        "gate_results": gates,
        "status": "PASS" if all(gates.values()) else "FAIL",
    }


def collect_evidence(
    database: Path,
    *,
    owner: str,
    device: str,
    window_start: str,
    window_end: str,
    implementation_commit: str,
    anchor_job_ids: tuple[str, ...] = ANCHOR_JOB_IDS,
    scene_ids: tuple[str, ...] = SCENE_IDS,
    pre_restart_evidence: Path | None = None,
) -> dict[str, Any]:
    if not COMMIT_RE.fullmatch(implementation_commit):
        raise ValueError("implementation_commit must be a 40-character lowercase SHA")
    window_start_at = _parse_timestamp(window_start, "window_start")
    window_end_at = _parse_timestamp(window_end, "window_end")
    if window_start_at >= window_end_at:
        raise ValueError("window_start must be before window_end")
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

        job_marks = ",".join("?" for _ in anchor_job_ids)
        anchor_jobs = _rows(
            connection.execute(
                f"SELECT * FROM jobs WHERE job_id IN ({job_marks}) ORDER BY job_id",
                anchor_job_ids,
            )
        )
        scene_marks = ",".join("?" for _ in scene_ids)
        scenes = _rows(
            connection.execute(
                f"SELECT * FROM scene_records WHERE scene_id IN ({scene_marks}) "
                "ORDER BY scene_id",
                scene_ids,
            )
        )
        scene_sections = _rows(
            connection.execute(
                f"SELECT * FROM scene_sections WHERE scene_id IN ({scene_marks}) "
                "ORDER BY scene_id,section",
                scene_ids,
            )
        )

        values = {"owner": owner, "device": device}
        snapshot = {
            name: _rows(
                connection.execute(sql, tuple(values[key] for key in parameter_keys))
            )
            for name, (sql, parameter_keys) in SNAPSHOT_QUERIES.items()
        }
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
        for scene_id in scene_ids
    }
    active_sessions = [
        row
        for row in sessions
        if row["disconnected_at"] is None
        and row["device_status"] == "online"
        and row["owner_subject"] == owner
        and row["protocol_version"] == "cad.agent/2"
    ]
    captured_at = datetime.now(timezone.utc).isoformat()
    captured_at_value = _parse_timestamp(captured_at, "captured_at")
    gates = {
        "audit_window_closed": window_end_at <= captured_at_value,
        "integrity_ok": integrity == ["ok"],
        "foreign_keys_ok": not foreign_keys,
        "migrations_ok": migration_identity == _expected_migrations(),
        "no_write_events_in_window": not no_write_events,
        "anchor_jobs_ok": anchor_ids == set(anchor_job_ids)
        and all(
            row["owner_subject"] == owner
            and row["device_id"] == device
            and row["effect_class"] == "read"
            and row["state"] == "succeeded"
            and window_start_at
            <= _parse_timestamp(row["created_at"], "anchor job created_at")
            <= _parse_timestamp(row["updated_at"], "anchor job updated_at")
            <= window_end_at
            and _parse_timestamp(row["updated_at"], "anchor job updated_at")
            <= captured_at_value
            for row in anchor_jobs
        ),
        "scenes_ok": {row["scene_id"] for row in scenes} == set(scene_ids)
        and all(
            row["owner_subject"] == owner
            and row["device_id"] == device
            and row["complete"] == 1
            and window_start_at
            <= _parse_timestamp(row["created_at"], "scene created_at")
            <= window_end_at
            and _parse_timestamp(row["created_at"], "scene created_at")
            <= captured_at_value
            for row in scenes
        )
        and all(sections == SCENE_SECTIONS for sections in sections_by_scene.values()),
        "active_session_ok": len(active_sessions) == 1,
    }
    evidence = {
        "schema_version": "cad.phase10-live-db-evidence/1",
        "implementation_commit": implementation_commit,
        "captured_at": captured_at,
        "database": str(database),
        "scope": {
            "owner_subject": owner,
            "device_id": device,
            "window_start": window_start,
            "window_end": window_end,
            "anchor_job_ids": list(anchor_job_ids),
            "scene_ids": list(scene_ids),
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
            "sha256": _snapshot_digest(snapshot),
            "tables": snapshot,
        },
        "agent_sessions": sessions,
        "active_agent_session_id": (
            active_sessions[0]["session_id"] if len(active_sessions) == 1 else None
        ),
        "gate_results": gates,
        "status": "PASS" if all(gates.values()) else "FAIL",
    }
    if pre_restart_evidence is not None:
        pre = _load_pre_restart_evidence(
            pre_restart_evidence,
            owner=owner,
            device=device,
            implementation_commit=implementation_commit,
        )
        pre_captured_at = _parse_timestamp(
            pre["captured_at"], "pre-restart evidence captured_at"
        )
        post_captured_at = _parse_timestamp(
            captured_at, "post-restart evidence captured_at"
        )
        if pre_captured_at >= post_captured_at:
            raise ValueError("pre-restart evidence was not captured before post capture")
        pre_snapshot = pre["write_snapshot"]
        comparison = {
            "pre_restart_active_agent_session_id": pre["active_agent_session_id"],
            "post_restart_active_agent_session_id": evidence["active_agent_session_id"],
            "pre_restart_write_snapshot": pre_snapshot,
            "post_restart_write_snapshot_sha256": evidence["write_snapshot"]["sha256"],
            "pre_restart_captured_at": pre["captured_at"],
            "post_restart_captured_at": captured_at,
            "sha256_unchanged": (
                pre_snapshot["sha256"] == evidence["write_snapshot"]["sha256"]
            ),
            "tables_byte_identical": (
                pre_snapshot["tables"] == evidence["write_snapshot"]["tables"]
            ),
        }
        evidence["restart_comparison"] = comparison
        pre_session_id = comparison["pre_restart_active_agent_session_id"]
        post_session_id = comparison["post_restart_active_agent_session_id"]
        sessions_by_id = {row["session_id"]: row for row in sessions}
        pre_session = sessions_by_id.get(pre_session_id)
        post_session = sessions_by_id.get(post_session_id)
        session_reconnected = (
            isinstance(pre_session_id, str)
            and isinstance(post_session_id, str)
            and pre_session_id != post_session_id
            and pre_session is not None
            and post_session is not None
            and all(
                row["device_id"] == device
                and row["owner_subject"] == owner
                and row["protocol_version"] == "cad.agent/2"
                for row in (pre_session, post_session)
            )
            and pre_session["disconnected_at"] is not None
            and post_session["disconnected_at"] is None
            and post_session["device_status"] == "online"
            and _parse_timestamp(
                pre_session["connected_at"], "pre-restart session connected_at"
            )
            <= pre_captured_at
            <= _parse_timestamp(
                pre_session["disconnected_at"],
                "pre-restart session disconnected_at",
            )
            <= _parse_timestamp(
                post_session["connected_at"], "post-restart session connected_at"
            )
            <= post_captured_at
        )
        gates.update(
            {
                "agent_session_reconnected": session_reconnected,
                "write_snapshot_sha256_unchanged": comparison["sha256_unchanged"],
                "write_snapshot_tables_unchanged": comparison[
                    "tables_byte_identical"
                ],
            }
        )
        evidence["status"] = "PASS" if all(gates.values()) else "FAIL"
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--owner-subject", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument(
        "--window-start", default="2026-07-30T06:15:00+00:00"
    )
    parser.add_argument("--window-end", default="2026-07-30T06:43:00+00:00")
    parser.add_argument("--implementation-commit", required=True)
    parser.add_argument("--anchor-job-id", action="append")
    parser.add_argument("--scene-id", action="append")
    parser.add_argument("--pre-restart-evidence", type=Path)
    parser.add_argument("--session-only", action="store_true")
    parser.add_argument("--operator", default="local-operator")
    args = parser.parse_args()
    if args.session_only:
        if args.pre_restart_evidence is not None:
            parser.error("--pre-restart-evidence cannot be used with --session-only")
        evidence = collect_session_evidence(
            args.database,
            owner=args.owner_subject,
            device=args.device_id,
            implementation_commit=args.implementation_commit,
        )
    else:
        evidence = collect_evidence(
            args.database,
            owner=args.owner_subject,
            device=args.device_id,
            window_start=args.window_start,
            window_end=args.window_end,
            implementation_commit=args.implementation_commit,
            anchor_job_ids=tuple(args.anchor_job_id or ANCHOR_JOB_IDS),
            scene_ids=tuple(args.scene_id or SCENE_IDS),
            pre_restart_evidence=args.pre_restart_evidence,
        )
    evidence.update(
        {
            "baseline_commit": _git("merge-base", "HEAD", "origin/main"),
            "capture_command": "python scripts/phase10-live-db-evidence.py "
            "--database <redacted> --owner-subject <redacted> "
            f"--device-id {args.device_id} "
            f"--implementation-commit {args.implementation_commit} "
            + ("--session-only" if args.session_only else "<scoped window/job/scene arguments>"),
            "operator": args.operator,
            "failures_retests": [],
            "limitations": [],
        }
    )
    json.dump(evidence, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
