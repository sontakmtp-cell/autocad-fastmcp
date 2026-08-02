from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "phase10-live-db-evidence.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("phase10_live_db_evidence", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collects_deterministic_read_only_phase10_db_evidence(tmp_path):
    module = _load_script()
    database = tmp_path / "phase10.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE schema_migrations(
            version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL
        );
        CREATE TABLE devices(
            device_id TEXT PRIMARY KEY, owner_subject TEXT, status TEXT
        );
        CREATE TABLE agent_sessions(
            session_id TEXT PRIMARY KEY, device_id TEXT, protocol_version TEXT, connected_at TEXT,
            last_heartbeat_at TEXT, disconnected_at TEXT
        );
        CREATE TABLE jobs(
            job_id TEXT PRIMARY KEY, owner_subject TEXT, device_id TEXT, effect_class TEXT,
            state TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE cad_programs(
            program_id TEXT PRIMARY KEY, owner_subject TEXT, device_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE cad_program_revisions(
            program_id TEXT, revision INTEGER, owner_subject TEXT, created_at TEXT
        );
        CREATE TABLE cad_previews(
            preview_id TEXT PRIMARY KEY, owner_subject TEXT, program_id TEXT, created_at TEXT
        );
        CREATE TABLE cad_validations(
            validation_id TEXT PRIMARY KEY, owner_subject TEXT, program_id TEXT, created_at TEXT
        );
        CREATE TABLE execution_intents(
            intent_id TEXT PRIMARY KEY, owner_subject TEXT, device_id TEXT, created_at TEXT
        );
        CREATE TABLE consents(
            consent_id TEXT PRIMARY KEY, owner_subject TEXT, intent_id TEXT,
            requested_at TEXT, decided_at TEXT, consumed_at TEXT
        );
        CREATE TABLE cad_execution_receipts(
            receipt_id TEXT PRIMARY KEY, owner_subject TEXT, program_id TEXT, created_at TEXT
        );
        CREATE TABLE program_idempotency(
            owner_subject TEXT, action TEXT, idempotency_key TEXT, created_at TEXT
        );
        CREATE TABLE cad_program_write_locks(
            device_id TEXT, document_id TEXT, job_id TEXT, created_at TEXT
        );
        CREATE TABLE workflow_runs(
            run_id TEXT PRIMARY KEY, owner_subject TEXT, device_id TEXT
        );
        CREATE TABLE workflow_actions(
            action_id TEXT PRIMARY KEY, run_id TEXT, effect_class TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE scene_records(
            scene_id TEXT PRIMARY KEY, owner_subject TEXT, device_id TEXT, complete INTEGER
        );
        CREATE TABLE scene_sections(
            scene_id TEXT, section TEXT
        );
        """
    )
    migrations = []
    for path in sorted(module.MIGRATIONS.glob("*.sql")):
        version = int(path.stem.split("_", 1)[0])
        checksum = hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
        migrations.append((version, checksum, "2026-07-30T00:00:00+00:00"))
    connection.executemany(
        "INSERT INTO schema_migrations VALUES(?,?,?)", migrations
    )
    connection.execute(
        "INSERT INTO devices VALUES('device-live','owner-live','online')"
    )
    connection.execute(
        "INSERT INTO agent_sessions VALUES("
        "'session-live','device-live','cad.agent/2','2026-07-30T06:00:00+00:00',"
        "'2026-07-30T06:42:30+00:00',NULL)"
    )
    connection.executemany(
        "INSERT INTO jobs VALUES(?,?,?,?,?,?,?)",
        [
            (
                job_id,
                "owner-live",
                "device-live",
                "read",
                "succeeded",
                "2026-07-30T06:20:00+00:00",
                "2026-07-30T06:20:01+00:00",
            )
            for job_id in module.ANCHOR_JOB_IDS
        ],
    )
    connection.executemany(
        "INSERT INTO scene_records VALUES(?,?,?,1)",
        [
            (scene_id, "owner-live", "device-live")
            for scene_id in module.SCENE_IDS
        ],
    )
    connection.executemany(
        "INSERT INTO scene_sections VALUES(?,?)",
        [
            (scene_id, section)
            for scene_id in module.SCENE_IDS
            for section in module.SCENE_SECTIONS
        ],
    )
    connection.commit()
    connection.close()

    first = module.collect_evidence(
        database,
        owner="owner-live",
        device="device-live",
        window_start="2026-07-30T06:15:00+00:00",
        window_end="2026-07-30T06:43:00+00:00",
        implementation_commit="a" * 40,
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--database",
            str(database),
            "--owner-subject",
            "owner-live",
            "--device-id",
            "device-live",
            "--implementation-commit",
            "a" * 40,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    cli_evidence = json.loads(completed.stdout)

    assert first["status"] == "PASS"
    assert first["implementation_commit"] == "a" * 40
    assert first["retrospective_no_write_events"] == []
    assert first["active_agent_session_id"] == "session-live"
    assert (
        first["write_snapshot"]["sha256"]
        == cli_evidence["write_snapshot"]["sha256"]
    )

    session_only = module.collect_session_evidence(
        database,
        owner="owner-live",
        device="device-live",
        implementation_commit="a" * 40,
    )
    assert session_only["schema_version"] == "cad.phase10-live-session-evidence/1"
    assert session_only["status"] == "PASS"
    assert session_only["active_agent_session_id"] == "session-live"

    pre_path = tmp_path / "pre-restart.json"
    pre_path.write_text(json.dumps(first), encoding="utf-8")
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE agent_sessions SET disconnected_at=? WHERE session_id='session-live'",
        ("2026-07-30T06:43:30+00:00",),
    )
    connection.execute(
        "INSERT INTO agent_sessions VALUES(?,?,?,?,?,?)",
        (
            "session-after",
            "device-live",
            "cad.agent/2",
            "2026-07-30T06:43:31+00:00",
            "2026-07-30T06:44:00+00:00",
            None,
        ),
    )
    connection.commit()
    connection.close()

    post = module.collect_evidence(
        database,
        owner="owner-live",
        device="device-live",
        window_start="2026-07-30T06:15:00+00:00",
        window_end="2026-07-30T06:43:00+00:00",
        implementation_commit="a" * 40,
        pre_restart_evidence=pre_path,
    )
    comparison = post["restart_comparison"]
    assert post["status"] == "PASS"
    assert comparison["pre_restart_active_agent_session_id"] == "session-live"
    assert comparison["post_restart_active_agent_session_id"] == "session-after"
    assert comparison["pre_restart_write_snapshot"] == first["write_snapshot"]
    assert comparison["post_restart_write_snapshot_sha256"] == post["write_snapshot"]["sha256"]
    assert comparison["sha256_unchanged"] is True
    assert comparison["tables_byte_identical"] is True

    with pytest.raises(ValueError, match="pre-restart evidence is missing"):
        module.collect_evidence(
            database,
            owner="owner-live",
            device="device-live",
            window_start="2026-07-30T06:15:00+00:00",
            window_end="2026-07-30T06:43:00+00:00",
            implementation_commit="a" * 40,
            pre_restart_evidence=tmp_path / "missing.json",
        )

    tampered = json.loads(pre_path.read_text(encoding="utf-8"))
    tampered["write_snapshot"]["sha256"] = "sha256:" + "0" * 64
    tampered_path = tmp_path / "tampered-pre-restart.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="write snapshot digest is invalid"):
        module.collect_evidence(
            database,
            owner="owner-live",
            device="device-live",
            window_start="2026-07-30T06:15:00+00:00",
            window_end="2026-07-30T06:43:00+00:00",
            implementation_commit="a" * 40,
            pre_restart_evidence=tampered_path,
        )

    wrong_scope = json.loads(pre_path.read_text(encoding="utf-8"))
    wrong_scope["scope"]["device_id"] = "other-device"
    wrong_scope_path = tmp_path / "wrong-scope-pre-restart.json"
    wrong_scope_path.write_text(json.dumps(wrong_scope), encoding="utf-8")
    with pytest.raises(ValueError, match="scope differs from post capture"):
        module.collect_evidence(
            database,
            owner="owner-live",
            device="device-live",
            window_start="2026-07-30T06:15:00+00:00",
            window_end="2026-07-30T06:43:00+00:00",
            implementation_commit="a" * 40,
            pre_restart_evidence=wrong_scope_path,
        )

    wrong_commit = json.loads(pre_path.read_text(encoding="utf-8"))
    wrong_commit["implementation_commit"] = "b" * 40
    wrong_commit_path = tmp_path / "wrong-commit-pre-restart.json"
    wrong_commit_path.write_text(json.dumps(wrong_commit), encoding="utf-8")
    with pytest.raises(ValueError, match="commit differs from post capture"):
        module.collect_evidence(
            database,
            owner="owner-live",
            device="device-live",
            window_start="2026-07-30T06:15:00+00:00",
            window_end="2026-07-30T06:43:00+00:00",
            implementation_commit="a" * 40,
            pre_restart_evidence=wrong_commit_path,
        )

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE agent_sessions SET protocol_version='cad.agent/1' "
        "WHERE session_id='session-after'"
    )
    connection.commit()
    connection.close()
    failed_session_only = module.collect_session_evidence(
        database,
        owner="owner-live",
        device="device-live",
        implementation_commit="a" * 40,
    )
    assert failed_session_only["status"] == "FAIL"
    assert failed_session_only["active_agent_session_id"] is None
