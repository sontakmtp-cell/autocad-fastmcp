from __future__ import annotations

import asyncio
import shutil
import sqlite3
from pathlib import Path

import pytest

from autocad_gateway.domain.jobs import InvalidJobTransition
from autocad_gateway.infrastructure.sqlite.database import DatabaseError, SqliteDatabase
from autocad_gateway.infrastructure.sqlite.repositories import (
    RepositoryConflict,
    SqliteRepository,
    capability_manifest_hash,
)


MIGRATIONS = (
    Path(__file__).parents[1]
    / "src"
    / "autocad_gateway"
    / "infrastructure"
    / "sqlite"
    / "migrations"
)


def _snapshot(snapshot_id: str = "snapshot-1", *, revision: str = "revision-1"):
    return {
        "snapshot_id": snapshot_id,
        "document_revision": revision,
        "observation_level": "summary",
        "drawing": {"name": "fixture.dwg"},
        "entity_summary": {"LINE": 1},
        "entities": [
            {"entity_id": "E1", "entity_type": "LINE", "layer": "0"}
        ],
    }


@pytest.fixture
async def repository(tmp_path):
    database = SqliteDatabase(tmp_path / "gateway.db")
    await database.open()
    repository = SqliteRepository(database)
    await repository.seed_device(
        owner_subject="owner",
        device_id="device-a",
        display_name="Device A",
        capabilities=["observe"],
        fixture_auth_ref="fixture:device-a",
    )
    yield repository
    await database.close()


async def _job(
    repository: SqliteRepository,
    key: str,
    *,
    kind: str = "observe",
    effect_class: str = "read",
):
    return await repository.create_job(
        owner_subject="owner",
        device_id="device-a",
        kind=kind,
        effect_class=effect_class,
        payload={"observation_level": "summary"},
        idempotency_key=key,
        deadline_at=None,
    )


async def _running_job(repository: SqliteRepository, key: str):
    job = await _job(repository, key)
    job = await repository.claim_job(job["job_id"])
    job = await repository.transition_job(job["job_id"], "acknowledged")
    return await repository.transition_job(job["job_id"], "running")


@pytest.mark.asyncio
async def test_migrations_are_current_and_owner_filters_fail_closed(repository):
    assert repository.database.migration_checksums.keys() == {1, 2, 3}
    assert repository.database.migrations_valid is True
    assert repository.database.verify_migration_state() is True
    assert [item["device_id"] for item in await repository.list_devices("owner")] == [
        "device-a"
    ]
    assert await repository.list_devices("other") == []
    with pytest.raises(RepositoryConflict, match="not_found"):
        await repository.create_job(
            owner_subject="other",
            device_id="device-a",
            kind="observe",
            effect_class="read",
            payload={},
            idempotency_key="cross-owner",
            deadline_at=None,
        )


@pytest.mark.asyncio
async def test_idempotency_fingerprint_binds_kind_effect_and_payload(repository):
    first = await _job(repository, "same-request")
    second = await _job(repository, "same-request")
    assert second["existing"] is True
    assert second["job_id"] == first["job_id"]
    assert second["request_fingerprint"] == first["request_fingerprint"]

    for kind, effect_class, payload in [
        ("observe", "read", {"observation_level": "detail"}),
        ("write_fixture", "read", {"observation_level": "summary"}),
        ("observe", "write", {"observation_level": "summary"}),
    ]:
        with pytest.raises(RepositoryConflict, match="idempotency_conflict"):
            await repository.create_job(
                owner_subject="owner",
                device_id="device-a",
                kind=kind,
                effect_class=effect_class,
                payload=payload,
                idempotency_key="same-request",
                deadline_at=None,
            )

    with pytest.raises(RepositoryConflict, match="payload_invalid"):
        await repository.create_job(
            owner_subject="owner",
            device_id="device-a",
            kind="observe",
            effect_class="read",
            payload={"non_finite": float("nan")},
            idempotency_key="invalid-json",
            deadline_at=None,
        )


@pytest.mark.asyncio
async def test_cas_claim_has_one_winner(repository):
    job = await _job(repository, "cas")
    first, second = await asyncio.gather(
        repository.claim_job(job["job_id"]), repository.claim_job(job["job_id"])
    )
    assert sum(value is not None for value in (first, second)) == 1


@pytest.mark.asyncio
async def test_session_replacement_heartbeat_stale_recovery_and_capabilities(repository):
    first_caps = ["query", "observe", "observe"]
    activated = await repository.activate_session(
        device_id="device-a",
        session_id="session-a",
        protocol_version="cad.agent/1",
        capabilities=first_caps,
        capability_hash=capability_manifest_hash(first_caps),
        last_sequence=4,
    )
    assert activated["capabilities"] == ["observe", "query"]
    assert activated["capability_changed"] is True
    assert (await repository.get_active_session("device-a"))["last_sequence"] == 4
    await repository.seed_device(
        owner_subject="owner",
        device_id="device-a",
        display_name="Device A renamed",
        capabilities=["seed.must.not.replace.hello"],
        fixture_auth_ref="fixture:device-a",
    )
    assert (await repository.get_device("owner", "device-a"))["capabilities"] == [
        "observe",
        "query",
    ]

    replacement_caps = ["drawing.info", "observe"]
    replacement = await repository.activate_session(
        device_id="device-a",
        session_id="session-b",
        protocol_version="cad.agent/1",
        capabilities=replacement_caps,
        capability_hash=capability_manifest_hash(replacement_caps),
        last_sequence=7,
    )
    assert replacement["replaced_session_ids"] == ["session-a"]
    assert (await repository.get_active_session("device-a"))["session_id"] == "session-b"

    # A delayed disconnect from A cannot take B or the device offline.
    assert await repository.close_session("session-a", device_id="device-a") is False
    assert (await repository.get_device("owner", "device-a"))["status"] == "online"

    assert await repository.mark_session_stale("session-a", device_id="device-a") is False
    assert await repository.mark_session_stale("session-b", device_id="device-a") is True
    assert (await repository.get_device("owner", "device-a"))["status"] == "offline"
    assert (
        await repository.heartbeat_session(
            "session-b", device_id="device-a", sequence=12
        )
        is True
    )
    assert (await repository.get_device("owner", "device-a"))["status"] == "online"
    assert (
        await repository.heartbeat_session(
            "session-b", device_id="device-a", sequence=8
        )
        is True
    )
    assert (await repository.get_active_session("device-a"))["last_sequence"] == 12
    assert (
        await repository.heartbeat_session(
            "session-a", device_id="device-a", sequence=99
        )
        is False
    )
    assert await repository.close_session("session-b", device_id="device-a") is True
    assert await repository.get_active_session("device-a") is None
    assert (await repository.get_device("owner", "device-a"))["status"] == "offline"


@pytest.mark.asyncio
async def test_capability_hash_mismatch_does_not_replace_active_session(repository):
    caps = ["observe"]
    await repository.activate_session(
        device_id="device-a",
        session_id="session-a",
        protocol_version="cad.agent/1",
        capabilities=caps,
        capability_hash=capability_manifest_hash(caps),
    )
    with pytest.raises(RepositoryConflict, match="capability_hash_mismatch"):
        await repository.activate_session(
            device_id="device-a",
            session_id="session-b",
            protocol_version="cad.agent/1",
            capabilities=["observe", "query"],
            capability_hash="0" * 64,
        )
    assert (await repository.get_active_session("device-a"))["session_id"] == "session-a"


@pytest.mark.asyncio
async def test_progress_is_ordered_idempotent_and_terminal_immutable(repository):
    job = await _running_job(repository, "progress")
    first = await repository.append_progress(
        job["job_id"], phase="inspect", percent=50, message="working", sequence=1
    )
    duplicate = await repository.append_progress(
        job["job_id"], phase="inspect", percent=50, message="working", sequence=1
    )
    assert duplicate["progress"] == first["progress"]
    with pytest.raises(RepositoryConflict, match="sequence_rejected"):
        await repository.append_progress(
            job["job_id"], phase="different", percent=51, message="changed", sequence=1
        )

    snapshot = _snapshot("snapshot-progress")
    await repository.finalize_job_result(
        job_id=job["job_id"],
        device_id="device-a",
        command_id=job["command_id"],
        payload_hash=job["payload_hash"],
        target="succeeded",
        result={"snapshot": snapshot},
        snapshot=snapshot,
        agent_sequence=2,
    )
    before, _ = await repository.list_events("owner", job["job_id"])
    with pytest.raises(RepositoryConflict, match="terminal_immutable"):
        await repository.append_progress(
            job["job_id"], phase="late", percent=100, message="late", sequence=3
        )
    after, _ = await repository.list_events("owner", job["job_id"])
    assert after == before


@pytest.mark.asyncio
async def test_progress_before_ack_is_rejected_without_event(repository):
    job = await _job(repository, "progress-order")
    job = await repository.claim_job(job["job_id"])
    with pytest.raises(RepositoryConflict, match="message_order_invalid"):
        await repository.append_progress(
            job["job_id"], phase="early", percent=1, message="early", sequence=1
        )
    events, _ = await repository.list_events("owner", job["job_id"])
    assert [event["state"] for event in events] == ["queued", "dispatched"]


@pytest.mark.asyncio
async def test_atomic_finalize_and_identical_duplicate_are_idempotent(repository):
    job = await _running_job(repository, "finalize")
    snapshot = _snapshot()
    result = {"snapshot": snapshot}
    finalized = await repository.finalize_job_result(
        job_id=job["job_id"],
        device_id="device-a",
        command_id=job["command_id"],
        payload_hash=job["payload_hash"],
        target="succeeded",
        result=result,
        snapshot=snapshot,
        expected_version=job["state_version"],
        agent_sequence=1,
    )
    assert finalized["state"] == "succeeded"
    assert finalized["duplicate_terminal"] is False
    assert (await repository.get_snapshot("owner", "snapshot-1"))["job_id"] == job["job_id"]
    events_before, _ = await repository.list_events("owner", job["job_id"])

    duplicate = await repository.finalize_job_result(
        job_id=job["job_id"],
        device_id="device-a",
        command_id=job["command_id"],
        payload_hash=job["payload_hash"],
        target="succeeded",
        result=result,
        snapshot=snapshot,
        expected_version=job["state_version"],
        agent_sequence=1,
    )
    assert duplicate["duplicate_terminal"] is True
    events_after, _ = await repository.list_events("owner", job["job_id"])
    assert events_after == events_before

    with pytest.raises(RepositoryConflict, match="terminal_result_conflict"):
        changed = _snapshot("snapshot-changed", revision="revision-changed")
        await repository.finalize_job_result(
            job_id=job["job_id"],
            device_id="device-a",
            command_id=job["command_id"],
            payload_hash=job["payload_hash"],
            target="succeeded",
            result={"snapshot": changed},
            snapshot=changed,
            agent_sequence=2,
        )
    assert await repository.get_snapshot("owner", "snapshot-changed") is None


@pytest.mark.asyncio
async def test_invalid_result_order_and_stale_cas_leave_no_snapshot(repository):
    dispatched = await _job(repository, "invalid-order")
    dispatched = await repository.claim_job(dispatched["job_id"])
    snapshot = _snapshot("snapshot-invalid-order")
    with pytest.raises(InvalidJobTransition):
        await repository.finalize_job_result(
            job_id=dispatched["job_id"],
            device_id="device-a",
            command_id=dispatched["command_id"],
            payload_hash=dispatched["payload_hash"],
            target="succeeded",
            result={"snapshot": snapshot},
            snapshot=snapshot,
        )
    assert await repository.get_snapshot("owner", "snapshot-invalid-order") is None

    running = await _running_job(repository, "stale-cas")
    stale_snapshot = _snapshot("snapshot-stale-cas")
    with pytest.raises(RepositoryConflict, match="cas_conflict"):
        await repository.finalize_job_result(
            job_id=running["job_id"],
            device_id="device-a",
            command_id=running["command_id"],
            payload_hash=running["payload_hash"],
            target="succeeded",
            result={"snapshot": stale_snapshot},
            snapshot=stale_snapshot,
            expected_version=running["state_version"] - 1,
        )
    assert await repository.get_snapshot("owner", "snapshot-stale-cas") is None


@pytest.mark.asyncio
async def test_snapshot_result_cannot_bypass_atomic_finalizer(repository):
    job = await _running_job(repository, "no-snapshot-bypass")
    snapshot = _snapshot("snapshot-no-bypass")
    with pytest.raises(RepositoryConflict, match="atomic_finalization_required"):
        await repository.transition_job(
            job["job_id"], "succeeded", result={"snapshot": snapshot}
        )
    assert (await repository.get_job("owner", job["job_id"]))["state"] == "running"
    assert await repository.get_snapshot("owner", "snapshot-no-bypass") is None

    with pytest.raises(RepositoryConflict, match="atomic_finalization_required"):
        await repository.transition_job(job["job_id"], "succeeded")
    assert (await repository.get_job("owner", job["job_id"]))["state"] == "running"


@pytest.mark.asyncio
async def test_snapshot_or_terminal_write_failure_rolls_back_everything(repository):
    job = await _running_job(repository, "rollback")
    with repository.database.transaction() as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_terminal_update
            BEFORE UPDATE OF state ON jobs
            WHEN NEW.state = 'succeeded'
            BEGIN
                SELECT RAISE(ABORT, 'terminal update rejected');
            END;
            """
        )
    snapshot = _snapshot("snapshot-rollback")
    with pytest.raises(sqlite3.IntegrityError, match="terminal update rejected"):
        await repository.finalize_job_result(
            job_id=job["job_id"],
            device_id="device-a",
            command_id=job["command_id"],
            payload_hash=job["payload_hash"],
            target="succeeded",
            result={"snapshot": snapshot},
            snapshot=snapshot,
        )
    assert (await repository.get_job("owner", job["job_id"]))["state"] == "running"
    assert await repository.get_snapshot("owner", "snapshot-rollback") is None
    events, _ = await repository.list_events("owner", job["job_id"])
    assert events[-1]["state"] == "running"


@pytest.mark.asyncio
async def test_snapshot_insert_failure_does_not_terminalize_job(repository):
    job = await _running_job(repository, "snapshot-insert-rollback")
    with repository.database.transaction() as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_snapshot_insert
            BEFORE INSERT ON snapshots
            BEGIN
                SELECT RAISE(ABORT, 'snapshot insert rejected');
            END;
            """
        )
    snapshot = _snapshot("snapshot-insert-rollback")
    with pytest.raises(sqlite3.IntegrityError, match="snapshot insert rejected"):
        await repository.finalize_job_result(
            job_id=job["job_id"],
            device_id="device-a",
            command_id=job["command_id"],
            payload_hash=job["payload_hash"],
            target="succeeded",
            result={"snapshot": snapshot},
            snapshot=snapshot,
        )
    value = await repository.get_job("owner", job["job_id"])
    assert value["state"] == "running"
    assert value["result"] is None
    assert await repository.get_snapshot("owner", "snapshot-insert-rollback") is None


@pytest.mark.asyncio
async def test_concurrent_identical_finalize_persists_once(repository):
    job = await _running_job(repository, "concurrent-same")
    snapshot = _snapshot("snapshot-concurrent")

    async def finalize():
        return await repository.finalize_job_result(
            job_id=job["job_id"],
            device_id="device-a",
            command_id=job["command_id"],
            payload_hash=job["payload_hash"],
            target="succeeded",
            result={"snapshot": snapshot},
            snapshot=snapshot,
            expected_version=job["state_version"],
            agent_sequence=1,
        )

    first, second = await asyncio.gather(finalize(), finalize())
    assert {first["duplicate_terminal"], second["duplicate_terminal"]} == {False, True}
    events, _ = await repository.list_events("owner", job["job_id"])
    assert [event["state"] for event in events].count("succeeded") == 1
    with repository.database.read_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM snapshots WHERE job_id = ?", (job["job_id"],)
        ).fetchone()[0]
    assert count == 1


@pytest.mark.asyncio
async def test_concurrent_conflicting_finalize_rejects_loser_without_orphan(repository):
    job = await _running_job(repository, "concurrent-conflict")

    async def finalize(snapshot):
        return await repository.finalize_job_result(
            job_id=job["job_id"],
            device_id="device-a",
            command_id=job["command_id"],
            payload_hash=job["payload_hash"],
            target="succeeded",
            result={"snapshot": snapshot},
            snapshot=snapshot,
            expected_version=job["state_version"],
            agent_sequence=1,
        )

    outcomes = await asyncio.gather(
        finalize(_snapshot("snapshot-race-a", revision="revision-a")),
        finalize(_snapshot("snapshot-race-b", revision="revision-b")),
        return_exceptions=True,
    )
    assert sum(isinstance(value, dict) for value in outcomes) == 1
    conflicts = [value for value in outcomes if isinstance(value, RepositoryConflict)]
    assert len(conflicts) == 1
    assert conflicts[0].code == "terminal_result_conflict"
    with repository.database.read_connection() as conn:
        rows = conn.execute(
            "SELECT snapshot_id FROM snapshots WHERE job_id = ?", (job["job_id"],)
        ).fetchall()
    assert len(rows) == 1
    stored = await repository.get_job("owner", job["job_id"])
    assert stored["result"]["snapshot"]["snapshot_id"] == rows[0][0]


@pytest.mark.asyncio
async def test_cancel_intent_survives_recovery_and_cancelled_reconcile(repository):
    read_job = await _job(repository, "cancel-recovery")
    read_job = await repository.claim_job(read_job["job_id"])
    read_job = await repository.transition_job(read_job["job_id"], "cancel_requested")
    assert read_job["cancel_requested_at"] is not None
    recovered = await repository.transition_job(read_job["job_id"], "reconnect_pending")
    assert recovered["cancel_requested_at"] == read_job["cancel_requested_at"]
    cancelled = await repository.finalize_job_result(
        job_id=read_job["job_id"],
        device_id="device-a",
        command_id=read_job["command_id"],
        payload_hash=read_job["payload_hash"],
        target="cancelled",
        error_code="cancelled",
        error_summary="Agent confirmed cancellation",
        evidence=True,
    )
    assert cancelled["state"] == "cancelled"

    write_job = await _job(
        repository,
        "unknown-cancelled",
        kind="write_fixture",
        effect_class="write",
    )
    write_job = await repository.claim_job(write_job["job_id"])
    write_job = await repository.transition_job(write_job["job_id"], "acknowledged")
    unknown = await repository.transition_job(write_job["job_id"], "outcome_unknown")
    cancelled_unknown = await repository.finalize_job_result(
        job_id=unknown["job_id"],
        device_id="device-a",
        command_id=unknown["command_id"],
        payload_hash=unknown["payload_hash"],
        target="cancelled",
        error_code="cancelled",
        error_summary="Reconcile proved cancellation",
        evidence=True,
    )
    assert cancelled_unknown["state"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_state", ["reconnect_pending", "outcome_unknown"])
async def test_cancel_request_preserves_recovery_state_and_is_idempotent(
    repository, recovery_state
):
    job = await _job(
        repository,
        f"cancel-intent-{recovery_state}",
        kind="write_fixture" if recovery_state == "outcome_unknown" else "observe",
        effect_class="write" if recovery_state == "outcome_unknown" else "read",
    )
    job = await repository.claim_job(job["job_id"])
    if recovery_state == "outcome_unknown":
        job = await repository.transition_job(job["job_id"], "acknowledged")
    job = await repository.transition_job(job["job_id"], recovery_state)
    previous_version = job["state_version"]

    requested = await repository.request_job_cancel(
        job["job_id"], expected_version=previous_version
    )
    assert requested["state"] == recovery_state
    assert requested["state_version"] == previous_version + 1
    assert requested["cancel_requested_at"] is not None

    replay = await repository.request_job_cancel(
        job["job_id"], expected_version=previous_version
    )
    assert replay == requested
    events, _ = await repository.list_events("owner", job["job_id"])
    cancel_events = [
        event for event in events if event["result"] == {"cancel_requested": True}
    ]
    assert len(cancel_events) == 1
    assert cancel_events[0]["event_type"] == "state"
    assert cancel_events[0]["state"] == recovery_state


@pytest.mark.asyncio
async def test_cancel_request_atomically_handles_queued_active_and_terminal_jobs(repository):
    queued = await _job(repository, "cancel-queued")
    cancelled = await repository.request_job_cancel(
        queued["job_id"], expected_version=queued["state_version"]
    )
    assert cancelled["state"] == "cancelled"
    assert cancelled["cancel_requested_at"] is not None

    running = await _running_job(repository, "cancel-running")
    requested = await repository.request_job_cancel(
        running["job_id"], expected_version=running["state_version"]
    )
    assert requested["state"] == "cancel_requested"
    assert requested["cancel_requested_at"] is not None

    terminal = await _job(repository, "cancel-terminal-winner")
    terminal = await repository.transition_job(
        terminal["job_id"],
        "failed",
        error_code="deadline_expired",
        error_summary="The durable deadline expired",
    )
    events_before, _ = await repository.list_events("owner", terminal["job_id"])
    unchanged = await repository.request_job_cancel(
        terminal["job_id"], expected_version=terminal["state_version"]
    )
    events_after, _ = await repository.list_events("owner", terminal["job_id"])
    assert unchanged == terminal
    assert unchanged["cancel_requested_at"] is None
    assert events_after == events_before


@pytest.mark.asyncio
async def test_cancel_request_cas_conflict_has_no_intent_or_event(repository):
    job = await _running_job(repository, "cancel-cas")
    events_before, _ = await repository.list_events("owner", job["job_id"])
    with pytest.raises(RepositoryConflict, match="cas_conflict"):
        await repository.request_job_cancel(
            job["job_id"], expected_version=job["state_version"] - 1
        )

    unchanged = await repository.get_job("owner", job["job_id"])
    events_after, _ = await repository.list_events("owner", job["job_id"])
    assert unchanged["state"] == "running"
    assert unchanged["cancel_requested_at"] is None
    assert events_after == events_before


@pytest.mark.asyncio
async def test_replaced_session_cannot_finalize_job(repository):
    caps = ["observe"]
    await repository.activate_session(
        device_id="device-a",
        session_id="session-a",
        protocol_version="cad.agent/1",
        capabilities=caps,
        capability_hash=capability_manifest_hash(caps),
    )
    await repository.activate_session(
        device_id="device-a",
        session_id="session-b",
        protocol_version="cad.agent/1",
        capabilities=caps,
        capability_hash=capability_manifest_hash(caps),
    )
    job = await _running_job(repository, "session-finalize")
    snapshot = _snapshot("snapshot-session")
    with pytest.raises(RepositoryConflict, match="session_mismatch"):
        await repository.finalize_job_result(
            job_id=job["job_id"],
            device_id="device-a",
            command_id=job["command_id"],
            payload_hash=job["payload_hash"],
            target="succeeded",
            result={"snapshot": snapshot},
            snapshot=snapshot,
            session_id="session-a",
        )
    await repository.finalize_job_result(
        job_id=job["job_id"],
        device_id="device-a",
        command_id=job["command_id"],
        payload_hash=job["payload_hash"],
        target="succeeded",
        result={"snapshot": snapshot},
        snapshot=snapshot,
        session_id="session-b",
    )


@pytest.mark.asyncio
async def test_backup_restore_keeps_job_events_snapshot_session_and_capabilities(repository, tmp_path):
    caps = ["drawing.info", "observe"]
    await repository.activate_session(
        device_id="device-a",
        session_id="session-a",
        protocol_version="cad.agent/1",
        capabilities=caps,
        capability_hash=capability_manifest_hash(caps),
        last_sequence=8,
    )
    job = await _running_job(repository, "backup")
    snapshot = _snapshot("snapshot-backup")
    await repository.finalize_job_result(
        job_id=job["job_id"],
        device_id="device-a",
        command_id=job["command_id"],
        payload_hash=job["payload_hash"],
        target="succeeded",
        result={"snapshot": snapshot},
        snapshot=snapshot,
    )
    backup_path = tmp_path / "backup.db"
    await repository.database.backup_to(backup_path)
    restored_db = SqliteDatabase(backup_path)
    await restored_db.open()
    restored = SqliteRepository(restored_db)
    try:
        assert (await restored.get_job("owner", job["job_id"]))["state"] == "succeeded"
        assert await restored.get_snapshot("owner", "snapshot-backup") is not None
        events, _ = await restored.list_events("owner", job["job_id"])
        assert events[-1]["state"] == "succeeded"
        device = await restored.get_device("owner", "device-a")
        assert device["capabilities"] == sorted(caps)
        session = await restored.get_active_session("device-a")
        assert session["last_sequence"] == 8
    finally:
        await restored_db.close()


def _copy_migrations(target: Path, *versions: int) -> None:
    target.mkdir()
    for version in versions:
        source = next(MIGRATIONS.glob(f"{version:04d}_*.sql"))
        shutil.copyfile(source, target / source.name)


@pytest.mark.asyncio
async def test_multi_file_migrations_apply_only_pending_and_rerun_is_stable(tmp_path):
    migration_dir = tmp_path / "migrations"
    _copy_migrations(migration_dir, 1)
    path = tmp_path / "gateway.db"
    first = SqliteDatabase(path, migration_path=migration_dir)
    await first.open()
    with first.read_connection() as conn:
        assert [row[0] for row in conn.execute("SELECT version FROM schema_migrations")] == [1]
        conn.execute(
            "INSERT INTO devices(device_id, owner_subject, display_name, status, capabilities_json, fixture_auth_ref, created_at, updated_at) "
            "VALUES ('kept', 'owner', 'Kept', 'offline', '[]', 'fixture', 'now', 'now')"
        )
        conn.execute(
            "INSERT INTO agent_sessions(session_id, device_id, protocol_version, connected_at, last_heartbeat_at) "
            "VALUES ('session-a', 'kept', 'cad.agent/1', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO agent_sessions(session_id, device_id, protocol_version, connected_at, last_heartbeat_at) "
            "VALUES ('session-b', 'kept', 'cad.agent/1', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
    await first.close()

    source = next(MIGRATIONS.glob("0002_*.sql"))
    shutil.copyfile(source, migration_dir / source.name)
    second = SqliteDatabase(path, migration_path=migration_dir)
    await second.open()
    assert second.migration_checksums.keys() == {1, 2}
    with second.read_connection() as conn:
        assert conn.execute("SELECT display_name FROM devices WHERE device_id='kept'").fetchone()[0] == "Kept"
        active = conn.execute(
            "SELECT session_id FROM agent_sessions WHERE device_id='kept' "
            "AND disconnected_at IS NULL"
        ).fetchall()
        assert [row[0] for row in active] == ["session-b"]
    before = dict(second.migration_checksums)
    await second.migrate()
    assert second.migration_checksums == before
    await second.close()


@pytest.mark.asyncio
async def test_changed_or_missing_applied_migration_fails_closed(tmp_path):
    migration_dir = tmp_path / "migrations"
    _copy_migrations(migration_dir, 1, 2)
    path = tmp_path / "gateway.db"
    database = SqliteDatabase(path, migration_path=migration_dir)
    await database.open()
    await database.close()

    first_file = next(migration_dir.glob("0001_*.sql"))
    first_file.write_text(first_file.read_text(encoding="utf-8") + "\n-- changed\n", encoding="utf-8")
    changed = SqliteDatabase(path, migration_path=migration_dir)
    with pytest.raises(DatabaseError, match="checksum mismatch"):
        await changed.open()
    assert changed.is_open is False
    assert changed.migrations_valid is False

    first_file.unlink()
    missing = SqliteDatabase(path, migration_path=migration_dir)
    with pytest.raises(DatabaseError, match="missing"):
        await missing.open()
    assert missing.is_open is False


@pytest.mark.asyncio
async def test_failed_migration_rolls_back_its_schema_and_history(tmp_path):
    migration_dir = tmp_path / "migrations"
    _copy_migrations(migration_dir, 1)
    (migration_dir / "0002_broken.sql").write_text(
        "CREATE TABLE partial_table(id INTEGER);\nTHIS IS NOT SQL;\n",
        encoding="utf-8",
    )
    path = tmp_path / "gateway.db"
    database = SqliteDatabase(path, migration_path=migration_dir)
    with pytest.raises(sqlite3.OperationalError):
        await database.open()
    assert database.is_open is False
    connection = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "partial_table" not in tables
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,)]
    finally:
        connection.close()
