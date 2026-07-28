from __future__ import annotations

from copy import deepcopy

import pytest

from autocad_contracts import execution_evidence_digest
from autocad_gateway.infrastructure.sqlite.phase7_repository import Phase7Repository
from autocad_gateway.infrastructure.sqlite.repositories import (
    RepositoryConflict,
    SqliteRepository,
)
from autocad_gateway.phase7_recovery import Phase7RecoveryService
from test_phase7_domain_storage import (
    DECIDED,
    NOW,
    approved_pair,
    digest,
    release_material,
    seed_parent_rows,
)
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase


@pytest.fixture
async def recovery(tmp_path):
    database = SqliteDatabase(tmp_path / "phase7-recovery.db")
    await database.open()
    seed_parent_rows(database)
    phase7 = Phase7Repository(database)
    jobs = SqliteRepository(database)
    service = Phase7RecoveryService(jobs, phase7, max_reconcile_attempts=2)
    try:
        yield service, phase7, jobs
    finally:
        await database.close()


async def _released_unknown(recovery, suffix: str = "recovery"):
    service, phase7, jobs = recovery
    intent, consent = await approved_pair(phase7, suffix)
    material = release_material(intent, suffix)
    released = await phase7.release_intent(
        owner_subject="owner-a",
        intent_id=intent["intent_id"],
        expected_intent_version=0,
        consumed_at=DECIDED,
        consent_id=consent["consent_id"],
        expected_consent_version=consent["state_version"],
        **material,
    )
    job = released["job"]
    for state in ("dispatched", "acknowledged", "running", "outcome_unknown"):
        job = await jobs.transition_job(job["job_id"], state) or job
    return intent, job


async def _released_running(recovery, suffix: str):
    _, phase7, jobs = recovery
    intent, consent = await approved_pair(phase7, suffix)
    released = await phase7.release_intent(
        owner_subject="owner-a",
        intent_id=intent["intent_id"],
        expected_intent_version=0,
        consumed_at=DECIDED,
        consent_id=consent["consent_id"],
        expected_consent_version=consent["state_version"],
        **release_material(intent, suffix),
    )
    job = released["job"]
    for state in ("dispatched", "acknowledged", "running"):
        job = await jobs.transition_job(job["job_id"], state) or job
    return intent, job


def _evidence(intent: dict, job: dict, **updates) -> dict:
    value = {
        "schema_version": "cad.execution-evidence/1",
        "event_id": "event-recovery-1",
        "owner_subject": "owner-a",
        "source": "agent",
        "source_sequence": 1,
        "job_id": job["job_id"],
        "command_id": job["command_id"],
        "intent_id": intent["intent_id"],
        "payload_digest": digest("payload"),
        "execution_digest": intent["commit_execution_digest"],
        "receipt_digest": None,
        "payload": {
            "milestone": "host_result_received",
            "outcome": "inconclusive",
            "summary": "Agent received no exact terminal Host receipt",
            "details": [],
        },
        "source_timestamp": NOW,
        "gateway_received_at": NOW,
    }
    value.update(updates)
    value["event_digest"] = execution_evidence_digest(value)
    return value


@pytest.mark.asyncio
async def test_exact_duplicate_is_idempotent_conflict_freezes_job_and_opens_case(recovery):
    service, phase7, jobs = recovery
    intent, job = await _released_unknown(recovery)
    evidence = _evidence(intent, job)

    created, duplicate = await service.append_evidence(evidence)
    assert duplicate is False
    replay, duplicate = await service.append_evidence(deepcopy(evidence))
    assert duplicate is True
    assert replay == created
    unsafe = _evidence(
        intent,
        job,
        event_id="event-unsafe",
        source_sequence=2,
        payload={
            **evidence["payload"],
            "details": [{"key": "access_token", "value": "must-not-be-persisted"}],
        },
    )
    with pytest.raises(RepositoryConflict, match="unsafe_evidence"):
        await service.append_evidence(unsafe)

    conflict = _evidence(
        intent,
        job,
        payload={
            **evidence["payload"],
            "summary": "Conflicting content for the same source sequence",
        },
    )
    with pytest.raises(RepositoryConflict, match="evidence_conflict"):
        await service.append_evidence(conflict)

    frozen = await jobs.get_job("owner-a", job["job_id"])
    assert frozen is not None and frozen["state"] == "needs_attention"
    cases = await phase7.list_recovery_cases("owner-a")
    assert len(cases) == 1
    assert cases[0]["job_id"] == job["job_id"]
    assert cases[0]["latest_query_result"]["outcome"] == "conflict"
    assert "retry_exact_evidence_query" in cases[0]["safe_actions"]
    assert not {
        "retry_write",
        "manual_mark_success",
        "generic_undo",
        "delete_history",
    }.intersection(cases[0]["safe_actions"])
    assert await phase7.list_recovery_cases("owner-b") == []
    assert len(await phase7.list_evidence("owner-a", job["job_id"])) == 1
    with phase7.database.read_connection() as conn:
        lock = conn.execute(
            "SELECT job_id FROM cad_program_write_locks WHERE job_id = ?",
            (job["job_id"],),
        ).fetchone()
    assert lock is not None


@pytest.mark.asyncio
async def test_terminal_result_and_gateway_evidence_persist_atomically(recovery):
    service, phase7, jobs = recovery
    intent, job = await _released_running(recovery, "terminal")
    result = {
        "execution_digest": intent["commit_execution_digest"],
        "receipt_digest": digest("terminal-receipt"),
    }
    event = await service.prepare_terminal_evidence(
        job=job,
        target="succeeded",
        result=result,
    )

    finalized = await jobs.finalize_job_result(
        job_id=job["job_id"],
        device_id=job["device_id"],
        command_id=job["command_id"],
        payload_hash=job["payload_hash"],
        target="succeeded",
        result=result,
        evidence=True,
        terminal_hook=lambda conn, _row: phase7.insert_evidence(conn, event),
    )

    assert finalized is not None and finalized["state"] == "succeeded"
    evidence = await phase7.list_evidence("owner-a", job["job_id"])
    assert len(evidence) == 1
    assert evidence[0]["payload"]["milestone"] == "terminal_persisted"
    assert evidence[0]["intent_id"] == intent["intent_id"]


@pytest.mark.asyncio
async def test_bounded_inconclusive_opens_one_case_without_reexecution_or_unlock(recovery):
    service, phase7, jobs = recovery
    _, job = await _released_unknown(recovery, "bounded")

    assert (
        await service.record_reconcile_outcome(
            owner_subject="owner-a",
            job_id=job["job_id"],
            outcome="inconclusive",
            source="host",
            summary="First exact receipt query was inconclusive",
            attempt=1,
        )
        is None
    )
    case = await service.record_reconcile_outcome(
        owner_subject="owner-a",
        job_id=job["job_id"],
        outcome="inconclusive",
        source="host",
        summary="Bounded exact receipt queries remain inconclusive",
        attempt=2,
    )
    assert case is not None
    replay = await service.record_reconcile_outcome(
        owner_subject="owner-a",
        job_id=job["job_id"],
        outcome="inconclusive",
        source="host",
        summary="A later query cannot create another case",
        attempt=3,
    )
    assert replay["case_id"] == case["case_id"]
    assert len(await phase7.list_recovery_cases("owner-a")) == 1
    current = await jobs.get_job("owner-a", job["job_id"])
    assert current is not None and current["state"] == "outcome_unknown"
    with phase7.database.read_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE job_id = ?", (job["job_id"],)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM cad_program_write_locks WHERE job_id = ?",
            (job["job_id"],),
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_document_unavailable_and_deadline_unknown_are_fail_closed(recovery):
    service, phase7, jobs = recovery
    _, job = await _released_unknown(recovery, "unavailable")
    case = await service.record_reconcile_outcome(
        owner_subject="owner-a",
        job_id=job["job_id"],
        outcome="unavailable",
        source="host",
        summary="Exact DWG is closed",
        current_state={
            "device_status": "online",
            "document_status": "unavailable",
        },
    )
    assert case is not None
    assert case["current_state"]["document_status"] == "unavailable"
    assert "reopen_exact_document" in case["safe_actions"]

    same = await service.ensure_recovery_case(
        owner_subject="owner-a",
        job=job,
        cause="deadline_outcome_unknown",
    )
    assert same is not None and same["case_id"] == case["case_id"]
    current = await jobs.get_job("owner-a", job["job_id"])
    assert current is not None and current["state"] == "outcome_unknown"
