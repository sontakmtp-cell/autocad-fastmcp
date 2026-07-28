from __future__ import annotations

import asyncio
import sqlite3
from copy import deepcopy

import pytest

from autocad_contracts import (
    execution_evidence_digest,
    execution_intent_digest,
    rollback_checkpoint_digest,
    rollback_plan_digest,
    rollback_receipt_digest,
)
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.phase7_repository import Phase7Repository
from autocad_gateway.infrastructure.sqlite.repositories import RepositoryConflict


NOW = "2026-07-27T01:00:00Z"
DECIDED = "2026-07-27T01:01:00Z"
LATER = "2026-07-27T01:10:00Z"


def digest(seed: str) -> str:
    return f"sha256:{seed.encode().hex():0<64}"[:71]


def runtime_pins() -> dict:
    return {
        "runtime_id": "runtime-1",
        "runtime_role": "managed",
        "host_family": "AutoCAD",
        "host_version": "R25",
        "agent_package_id": "agent-package",
        "agent_package_version": "1.0.0",
        "agent_package_hash": digest("agent"),
        "host_package_id": "host-package",
        "host_package_version": "1.0.0",
        "host_package_hash": digest("host"),
    }


def policy_pins() -> dict:
    return {
        "capability_manifest_hash": digest("capability"),
        "operation_registry_hash": digest("registry"),
        "registry_version": "cad.program/0.2",
        "policy_version": "phase7-test",
    }


def intent_value(suffix: str = "0001", **updates) -> dict:
    value = {
        "schema_version": "cad.execution-intent/1",
        "intent_id": f"intent-{suffix}",
        "intent_version": 1,
        "owner_subject": "owner-a",
        "actor_principal": {"issuer": "https://issuer.test/", "subject": "user-a"},
        "action": "program_commit",
        "state": "awaiting_approval",
        "state_version": 0,
        "device_id": "device-a",
        "device_identity_generation": 1,
        "device_key_thumbprint": digest("device-key"),
        "document_id": "document-1",
        "expected_document_revision": "revision-before",
        "program_id": "program-1",
        "program_revision": 1,
        "program_digest": digest("program"),
        "preview_id": "preview-1",
        "preview_digest": digest("preview"),
        "preview_execution_digest": digest("preview-execution"),
        "preview_expires_at": LATER,
        "deterministic_receipt_id": f"future-receipt-{suffix}",
        "commit_execution_digest": digest(f"commit-{suffix}"),
        "runtime_pins": runtime_pins(),
        "policy_pins": policy_pins(),
        "risk_class": "low",
        "required_assurance": "user_recent_auth",
        "trusted_effect_summary": [
            {"kind": "create_entities", "count": 1, "summary": "Create one line"}
        ],
        "idempotency_key": f"intent-key-{suffix}",
        "request_hash": digest(f"request-{suffix}"),
        "created_at": NOW,
        "expires_at": LATER,
        "consent_id": None,
        "released_job_id": None,
    }
    value.update(updates)
    value["intent_digest"] = execution_intent_digest(value)
    return value


def consent_value(intent: dict, suffix: str = "0001", **updates) -> dict:
    value = {
        "schema_version": "cad.consent/1",
        "consent_id": f"consent-{suffix}",
        "consent_version": 1,
        "owner_subject": intent["owner_subject"],
        "intent_id": intent["intent_id"],
        "intent_version": intent["intent_version"],
        "intent_digest": intent["intent_digest"],
        "required_assurance": intent["required_assurance"],
        "state": "requested",
        "state_version": 0,
        "challenge_nonce_hash": digest(f"nonce-{suffix}"),
        "requested_at": NOW,
        "expires_at": LATER,
    }
    value.update(updates)
    return value


def release_material(intent: dict, suffix: str = "0001", **updates) -> dict:
    value = {
        "job_id": f"release-job-{suffix}",
        "command_id": f"release-command-{suffix}",
        "idempotency_key": f"release-key-{suffix}",
        "payload_hash": digest(f"release-payload-{suffix}"),
        "payload": {
            "execution": {
                "intent_id": intent["intent_id"],
                "intent_digest": intent["intent_digest"],
                "program_digest": intent["program_digest"],
                "execution_digest": intent["commit_execution_digest"],
                "document_id": intent["document_id"],
                "expected_document_revision": intent["expected_document_revision"],
                "preview_id": intent["preview_id"],
                "preview_digest": intent["preview_digest"],
                "receipt_id": intent["deterministic_receipt_id"],
            }
        },
        "deadline_at": LATER,
        "kind": intent["action"],
    }
    value.update(updates)
    return value


def evidence_value(intent: dict, **updates) -> dict:
    value = {
        "schema_version": "cad.execution-evidence/1",
        "event_id": "event-0001",
        "owner_subject": "owner-a",
        "source": "gateway",
        "source_sequence": 1,
        "job_id": "evidence-job",
        "command_id": "command-evidence-job",
        "intent_id": intent["intent_id"],
        "execution_digest": intent["commit_execution_digest"],
        "payload": {
            "milestone": "terminal_persisted",
            "outcome": "inconclusive",
            "summary": "Gateway persisted unknown terminal evidence",
            "details": [],
        },
        "source_timestamp": NOW,
        "gateway_received_at": NOW,
    }
    value.update(updates)
    value["event_digest"] = execution_evidence_digest(value)
    return value


def recovery_value(intent: dict, **updates) -> dict:
    value = {
        "schema_version": "cad.recovery-case/1",
        "case_id": "case-0001",
        "owner_subject": "owner-a",
        "state": "open",
        "resolution_version": 0,
        "execution_binding_digest": intent["commit_execution_digest"],
        "intent_id": intent["intent_id"],
        "job_id": "evidence-job",
        "evidence_event_ids": [],
        "missing_evidence": ["Host receipt query unavailable"],
        "current_state": {
            "device_status": "offline",
            "document_status": "unavailable",
        },
        "safe_actions": ["retry_exact_evidence_query", "reopen_exact_document"],
        "operator_notes": [],
        "created_at": NOW,
        "updated_at": NOW,
    }
    value.update(updates)
    return value


def checkpoint_value(suffix: str = "0001", **updates) -> dict:
    value = {
        "schema_version": "cad.rollback.checkpoint/1",
        "checkpoint_id": f"checkpoint-{suffix}",
        "owner_subject": "owner-a",
        "original_receipt_id": "receipt-original",
        "original_receipt_digest": digest("original-receipt"),
        "program_id": "program-1",
        "program_revision": 1,
        "program_digest": digest("program"),
        "preview_id": "preview-1",
        "preview_digest": digest("preview"),
        "execution_digest": digest("original-execution"),
        "document_id": "document-1",
        "document_revision_before": "revision-before",
        "document_revision_after": "revision-after",
        "created_entities": [
            {
                "handle": "1A",
                "entity_type": "AcDbLine",
                "layer": "CAD-MCP",
                "canonical_fingerprint": digest("entity"),
            }
        ],
        "non_entity_object_created": False,
        "runtime_pins": runtime_pins(),
        "policy_pins": policy_pins(),
        "created_at": NOW,
    }
    value.update(updates)
    value["checkpoint_digest"] = rollback_checkpoint_digest(value)
    return value


def plan_value(checkpoint: dict, suffix: str = "0001", **updates) -> dict:
    value = {
        "schema_version": "cad.rollback.plan/1",
        "plan_id": f"plan-{suffix}",
        "owner_subject": checkpoint["owner_subject"],
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_digest": checkpoint["checkpoint_digest"],
        "original_receipt_id": checkpoint["original_receipt_id"],
        "document_id": checkpoint["document_id"],
        "current_document_revision": checkpoint["document_revision_after"],
        "rollback_execution_digest": digest(f"rollback-execution-{suffix}"),
        "entity_handles": ["1A"],
        "conflicts": [],
        "eligible": True,
        "runtime_pins": runtime_pins(),
        "policy_pins": policy_pins(),
        "created_at": NOW,
        "expires_at": LATER,
    }
    value.update(updates)
    value["plan_digest"] = rollback_plan_digest(value)
    return value


def rollback_receipt_value(
    checkpoint: dict, plan: dict, suffix: str = "0001", **updates
) -> dict:
    value = {
        "schema_version": "cad.rollback.receipt/1",
        "rollback_receipt_id": f"rollback-receipt-{suffix}",
        "owner_subject": checkpoint["owner_subject"],
        "original_receipt_id": checkpoint["original_receipt_id"],
        "original_receipt_digest": checkpoint["original_receipt_digest"],
        "program_digest": checkpoint["program_digest"],
        "original_execution_digest": checkpoint["execution_digest"],
        "original_document_revision": checkpoint["document_revision_before"],
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_digest": checkpoint["checkpoint_digest"],
        "rollback_plan_id": plan["plan_id"],
        "rollback_plan_digest": plan["plan_digest"],
        "rollback_job_id": "rollback-job",
        "rollback_execution_digest": plan["rollback_execution_digest"],
        "document_id": checkpoint["document_id"],
        "document_revision_before": checkpoint["document_revision_after"],
        "document_revision_after": "revision-rolled-back",
        "removed_entities": [
            {
                "handle": "1A",
                "entity_type": "AcDbLine",
                "prior_fingerprint": digest("entity"),
            }
        ],
        "runtime_pins": runtime_pins(),
        "policy_pins": policy_pins(),
        "created_at": NOW,
    }
    value.update(updates)
    value["receipt_digest"] = rollback_receipt_digest(value)
    return value


def insert_row(conn, table: str, values: dict) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO {table}({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )


def seed_parent_rows(database: SqliteDatabase) -> None:
    with database.transaction() as conn:
        for owner, device in (("owner-a", "device-a"), ("owner-b", "device-b")):
            insert_row(
                conn,
                "devices",
                {
                    "device_id": device,
                    "owner_subject": owner,
                    "display_name": device,
                    "status": "online",
                    "capabilities_json": "[]",
                    "fixture_auth_ref": "fixture",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            )
        for job_id, kind, effect in (
            ("snapshot-job", "observe", "read"),
            ("preview-job", "program_preview", "write"),
            ("original-job", "program_commit", "write"),
            ("rollback-job", "rollback_commit", "write"),
            ("evidence-job", "program_commit", "write"),
        ):
            insert_row(
                conn,
                "jobs",
                {
                    "job_id": job_id,
                    "owner_subject": "owner-a",
                    "device_id": "device-a",
                    "kind": kind,
                    "effect_class": effect,
                    "state": "queued",
                    "state_version": 0,
                    "command_id": f"command-{job_id}",
                    "idempotency_key": f"job-key-{job_id}",
                    "payload_hash": digest(f"payload-{job_id}"),
                    "payload_json": "{}",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            )
        insert_row(
            conn,
            "snapshots",
            {
                "snapshot_id": "snapshot-1",
                "owner_subject": "owner-a",
                "device_id": "device-a",
                "job_id": "snapshot-job",
                "revision": 1,
                "document_revision": "revision-before",
                "observation_level": "summary",
                "drawing_json": "{}",
                "entity_summary_json": "{}",
                "entities_json": "[]",
                "created_at": NOW,
            },
        )
        insert_row(
            conn,
            "cad_programs",
            {
                "program_id": "program-1",
                "owner_subject": "owner-a",
                "device_id": "device-a",
                "document_id": "document-1",
                "created_at": NOW,
                "updated_at": NOW,
            },
        )
        insert_row(
            conn,
            "cad_program_revisions",
            {
                "program_id": "program-1",
                "revision": 1,
                "owner_subject": "owner-a",
                "source_snapshot_id": "snapshot-1",
                "expected_document_revision": "revision-before",
                "schema_version": "cad.program/0.2",
                "registry_version": "cad.program/0.2",
                "program_digest": digest("program"),
                "semantic_json": "{}",
                "operations_json": "[]",
                "preconditions_json": "[]",
                "postconditions_json": "[]",
                "budgets_json": "{}",
                "risk_class": "low",
                "missing_capabilities_json": "[]",
                "runtime_id": "runtime-1",
                "runtime_role": "managed",
                "host_family": "AutoCAD",
                "host_version": "R25",
                "package_id": "package-1",
                "package_version": "1.0.0",
                "package_hash": digest("package"),
                "capability_manifest_hash": digest("capability"),
                "operation_registry_hash": digest("registry"),
                "policy_version": "phase7-test",
                "created_at": NOW,
            },
        )
        insert_row(
            conn,
            "cad_previews",
            {
                "preview_id": "preview-1",
                "owner_subject": "owner-a",
                "program_id": "program-1",
                "program_revision": 1,
                "job_id": "preview-job",
                "program_digest": digest("program"),
                "execution_digest": digest("preview-execution"),
                "preview_digest": digest("preview"),
                "binding_digest": digest("binding"),
                "document_id": "document-1",
                "expected_document_revision": "revision-before",
                "runtime_id": "runtime-1",
                "runtime_role": "managed",
                "host_family": "AutoCAD",
                "host_version": "R25",
                "package_id": "package-1",
                "package_version": "1.0.0",
                "package_hash": digest("package"),
                "capability_manifest_hash": digest("capability"),
                "operation_registry_hash": digest("registry"),
                "registry_version": "cad.program/0.2",
                "policy_version": "phase7-test",
                "planned_operation_count": 1,
                "planned_entity_count": 1,
                "planned_layer_count": 0,
                "validation_json": "{}",
                "expires_at": LATER,
                "created_at": NOW,
            },
        )
        insert_row(
            conn,
            "cad_execution_receipts",
            {
                "receipt_id": "receipt-original",
                "owner_subject": "owner-a",
                "program_id": "program-1",
                "program_revision": 1,
                "preview_id": "preview-1",
                "job_id": "original-job",
                "program_digest": digest("program"),
                "execution_digest": digest("original-execution"),
                "receipt_digest": digest("original-receipt"),
                "preview_execution_digest": digest("preview-execution"),
                "binding_digest": digest("binding"),
                "document_id": "document-1",
                "document_revision_before": "revision-before",
                "document_revision_after": "revision-after",
                "runtime_id": "runtime-1",
                "package_hash": digest("package"),
                "capability_manifest_hash": digest("capability"),
                "operation_registry_hash": digest("registry"),
                "policy_version": "phase7-test",
                "effect_summary_json": "{}",
                "durable_receipt_json": "{}",
                "created_at": NOW,
            },
        )


@pytest.fixture
async def repository(tmp_path):
    database = SqliteDatabase(tmp_path / "phase7.db")
    await database.open()
    seed_parent_rows(database)
    value = Phase7Repository(database)
    try:
        yield value
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_migration_has_exact_phase7_tables_and_owner_key(repository):
    assert repository.database.migration_checksums.keys() == {1, 2, 3, 4, 5, 6}
    expected = {
        "execution_intents",
        "consents",
        "execution_evidence_events",
        "recovery_cases",
        "rollback_checkpoints",
        "rollback_plans",
        "rollback_receipts",
    }
    with repository.database.read_connection() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert expected <= tables
        for table in expected:
            columns = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            assert "owner_subject" in columns


@pytest.mark.asyncio
async def test_intent_exact_replay_conflict_immutability_and_owner_isolation(repository):
    value = intent_value()
    created, duplicate = await repository.create_intent(value)
    assert duplicate is False
    assert created == value
    replay, duplicate = await repository.create_intent(deepcopy(value))
    assert duplicate is True
    assert replay == created

    conflicting = intent_value(
        trusted_effect_summary=[
            {"kind": "create_entities", "count": 2, "summary": "Create two lines"}
        ]
    )
    with pytest.raises(RepositoryConflict, match="intent_conflict"):
        await repository.create_intent(conflicting)

    assert await repository.get_intent("owner-b", value["intent_id"]) is None
    assert await repository.list_intents("owner-b") == []
    with pytest.raises(sqlite3.IntegrityError, match="execution_intent_immutable"):
        with repository.database.transaction() as conn:
            conn.execute(
                "UPDATE execution_intents SET intent_digest = ? WHERE intent_id = ?",
                (digest("tampered"), value["intent_id"]),
            )


@pytest.mark.asyncio
async def test_intent_state_machine_rejects_reverse_and_release_without_binding(repository):
    value, consent = await approved_pair(repository, "transition")
    ready = await repository.transition_intent(
        owner_subject="owner-a",
        intent_id=value["intent_id"],
        target="ready",
        expected_version=0,
        consent_id=consent["consent_id"],
    )
    assert ready["state"] == "ready"
    assert ready["state_version"] == 1
    replay, duplicate = await repository.create_intent(value)
    assert duplicate is True
    assert replay == ready
    assert (
        await repository.transition_intent(
            owner_subject="owner-a",
            intent_id=value["intent_id"],
            target="ready",
            expected_version=0,
            consent_id=consent["consent_id"],
        )
        == ready
    )
    with pytest.raises(RepositoryConflict, match="invalid_intent_transition"):
        await repository.transition_intent(
            owner_subject="owner-a",
            intent_id=value["intent_id"],
            target="awaiting_approval",
            expected_version=1,
        )
    with pytest.raises(RepositoryConflict, match="release_binding_required"):
        await repository.transition_intent(
            owner_subject="owner-a",
            intent_id=value["intent_id"],
            target="released",
            expected_version=1,
        )

    expiring = intent_value("expiring")
    await repository.create_intent(expiring)
    with pytest.raises(RepositoryConflict, match="intent_not_expired"):
        await repository.transition_intent(
            owner_subject="owner-a",
            intent_id=expiring["intent_id"],
            target="expired",
            expected_version=0,
            transition_at=DECIDED,
        )
    expired = await repository.transition_intent(
        owner_subject="owner-a",
        intent_id=expiring["intent_id"],
        target="expired",
        expected_version=0,
        transition_at=LATER,
    )
    assert expired["state"] == "expired"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["denied", "expired", "invalidated"])
async def test_consent_terminal_transitions_and_owner_isolation(repository, target):
    intent = intent_value(target)
    consent = consent_value(intent, target)
    await repository.create_intent(intent)
    created, duplicate = await repository.create_consent(consent)
    assert duplicate is False
    replay, duplicate = await repository.create_consent(deepcopy(consent))
    assert duplicate is True
    assert replay == created
    kwargs = {}
    if target == "denied":
        kwargs = {
            "decision_source": "portal_recent_auth",
            "decision_principal": {
                "issuer": "https://issuer.test/",
                "subject": "user-a",
            },
        }
    transitioned = await repository.transition_consent(
        owner_subject="owner-a",
        consent_id=consent["consent_id"],
        target=target,
        expected_version=0,
        transition_at=LATER if target == "expired" else DECIDED,
        **kwargs,
    )
    assert transitioned["state"] == target
    replay, duplicate = await repository.create_consent(consent)
    assert duplicate is True
    assert replay == transitioned
    assert await repository.get_consent("owner-b", consent["consent_id"]) is None
    assert await repository.list_consents("owner-b") == []
    with pytest.raises(RepositoryConflict, match="invalid_consent_transition"):
        await repository.transition_consent(
            owner_subject="owner-a",
            consent_id=consent["consent_id"],
            target="approved",
            expected_version=1,
            transition_at=DECIDED,
            decision_source="portal_recent_auth",
            decision_principal={
                "issuer": "https://issuer.test/",
                "subject": "user-a",
            },
        )


async def approved_pair(repository, suffix: str) -> tuple[dict, dict]:
    intent = intent_value(suffix)
    consent = consent_value(intent, suffix)
    await repository.create_intent(intent)
    await repository.create_consent(consent)
    approved = await repository.transition_consent(
        owner_subject="owner-a",
        consent_id=consent["consent_id"],
        target="approved",
        expected_version=0,
        transition_at=DECIDED,
        decision_source="portal_recent_auth",
        decision_principal={
            "issuer": "https://issuer.test/",
            "subject": "user-a",
        },
    )
    return intent, approved


@pytest.mark.asyncio
async def test_consent_consume_and_intent_release_are_atomic_and_race_safe(repository):
    intent, consent = await approved_pair(repository, "release")
    material = release_material(intent, "exact")
    with repository.database.read_connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM jobs WHERE job_id = ?", (material["job_id"],)
        ).fetchone() is None

    released = await repository.release_intent(
        owner_subject="owner-a",
        intent_id=intent["intent_id"],
        expected_intent_version=0,
        consumed_at=DECIDED,
        consent_id=consent["consent_id"],
        expected_consent_version=consent["state_version"],
        **material,
    )
    assert released["intent"]["state"] == "released"
    assert released["intent"]["released_job_id"] == material["job_id"]
    assert released["job"]["state"] == "queued"
    assert released["job"]["payload"] == material["payload"]
    assert released["job_existing"] is False
    with repository.database.read_connection() as conn:
        assert conn.execute(
            "SELECT job_id FROM cad_program_write_locks "
            "WHERE device_id = 'device-a' AND document_id = 'document-1'"
        ).fetchone()[0] == material["job_id"]
        assert conn.execute(
            "SELECT COUNT(*) FROM job_events WHERE job_id = ?",
            (material["job_id"],),
        ).fetchone()[0] == 1
    assert (await repository.get_consent("owner-a", consent["consent_id"]))[
        "state"
    ] == "consumed"
    replay = await repository.release_intent(
        owner_subject="owner-a",
        intent_id=intent["intent_id"],
        expected_intent_version=0,
        consumed_at=DECIDED,
        consent_id=consent["consent_id"],
        expected_consent_version=consent["state_version"],
        **material,
    )
    assert replay["intent"] == released["intent"]
    assert replay["job"] == released["job"]
    assert replay["job_existing"] is True
    assert (await repository.get_consent("owner-a", consent["consent_id"]))[
        "state_version"
    ] == 2

    conflicting_material = {
        **material,
        "payload_hash": digest("conflicting-payload"),
    }
    with pytest.raises(RepositoryConflict, match="job_conflict"):
        await repository.release_intent(
            owner_subject="owner-a",
            intent_id=intent["intent_id"],
            expected_intent_version=0,
            consumed_at=DECIDED,
            consent_id=consent["consent_id"],
            expected_consent_version=consent["state_version"],
            **conflicting_material,
        )
    with pytest.raises(RepositoryConflict, match="intent_release_conflict"):
        await repository.release_intent(
            owner_subject="owner-a",
            intent_id=intent["intent_id"],
            expected_intent_version=0,
            consumed_at=DECIDED,
            consent_id=consent["consent_id"],
            expected_consent_version=consent["state_version"],
            **{
                **material,
                "job_id": "release-job-conflict",
                "command_id": "release-command-conflict",
            },
        )


@pytest.mark.asyncio
async def test_two_call_release_race_creates_one_job(repository):
    intent, consent = await approved_pair(repository, "race")
    material = release_material(intent, "race")

    async def release():
        return await repository.release_intent(
            owner_subject="owner-a",
            intent_id=intent["intent_id"],
            expected_intent_version=0,
            consumed_at=DECIDED,
            consent_id=consent["consent_id"],
            expected_consent_version=1,
            **material,
        )

    outcomes = await asyncio.gather(
        release(),
        release(),
    )
    assert {value["job_existing"] for value in outcomes} == {False, True}
    assert outcomes[0]["job"]["job_id"] == outcomes[1]["job"]["job_id"]
    with repository.database.read_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE job_id = ?", (material["job_id"],)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM job_events WHERE job_id = ?", (material["job_id"],)
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_preexisting_conflicting_job_or_idempotency_fails_closed(repository):
    intent, consent = await approved_pair(repository, "preexisting")
    material = release_material(intent, "preexisting")
    with repository.database.transaction() as conn:
        insert_row(
            conn,
            "jobs",
            {
                "job_id": "unrelated-preexisting-job",
                "owner_subject": "owner-a",
                "device_id": "device-a",
                "kind": "program_commit",
                "effect_class": "write",
                "state": "queued",
                "state_version": 0,
                "deadline_at": LATER,
                "command_id": "unrelated-preexisting-command",
                "idempotency_key": material["idempotency_key"],
                "payload_hash": digest("unrelated"),
                "payload_json": "{}",
                "created_at": NOW,
                "updated_at": NOW,
            },
        )
    with pytest.raises(RepositoryConflict, match="job_preexists_release"):
        await repository.release_intent(
            owner_subject="owner-a",
            intent_id=intent["intent_id"],
            expected_intent_version=0,
            consumed_at=DECIDED,
            consent_id=consent["consent_id"],
            expected_consent_version=1,
            **material,
        )
    assert await repository.get_intent("owner-a", intent["intent_id"]) == intent
    assert (await repository.get_consent("owner-a", consent["consent_id"]))[
        "state"
    ] == "approved"
    with repository.database.read_connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM jobs WHERE job_id = ?", (material["job_id"],)
        ).fetchone() is None


@pytest.mark.asyncio
async def test_release_failure_rolls_back_consent_consumption(repository):
    intent, consent = await approved_pair(repository, "rollback")
    material = release_material(intent, "rollback")
    with repository.database.transaction() as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_phase7_release
            BEFORE UPDATE OF released_job_id ON execution_intents
            WHEN NEW.released_job_id IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT, 'release rejected');
            END;
            """
        )
    with pytest.raises(RepositoryConflict, match="intent_release_conflict"):
        await repository.release_intent(
            owner_subject="owner-a",
            intent_id=intent["intent_id"],
            expected_intent_version=0,
            consumed_at=DECIDED,
            consent_id=consent["consent_id"],
            expected_consent_version=1,
            **material,
        )
    assert (await repository.get_consent("owner-a", consent["consent_id"]))[
        "state"
    ] == "approved"
    assert (await repository.get_intent("owner-a", intent["intent_id"]))[
        "state"
    ] == "awaiting_approval"
    with repository.database.read_connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM jobs WHERE job_id = ?", (material["job_id"],)
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM job_events WHERE job_id = ?", (material["job_id"],)
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM cad_program_write_locks WHERE job_id = ?",
            (material["job_id"],),
        ).fetchone() is None


@pytest.mark.asyncio
async def test_evidence_is_append_only_exact_idempotent_and_sequence_unique(repository):
    intent = intent_value("evidence")
    await repository.create_intent(intent)
    event = evidence_value(intent)
    created, duplicate = await repository.append_evidence(event)
    assert duplicate is False
    replay, duplicate = await repository.append_evidence(deepcopy(event))
    assert duplicate is True
    assert replay == created

    conflict = evidence_value(
        intent,
        event_id="event-other",
        payload={
            "milestone": "terminal_persisted",
            "outcome": "conflict",
            "summary": "Conflicting event at same source sequence",
            "details": [],
        },
    )
    with pytest.raises(RepositoryConflict, match="evidence_conflict"):
        await repository.append_evidence(conflict)
    stale_sequence = evidence_value(
        intent,
        event_id="event-stale",
        source_sequence=0,
    )
    with pytest.raises(RepositoryConflict, match="evidence_sequence_rejected"):
        await repository.append_evidence(stale_sequence)
    assert await repository.get_evidence("owner-b", event["event_id"]) is None
    assert await repository.list_evidence("owner-b", event["job_id"]) == []
    with pytest.raises(sqlite3.IntegrityError, match="append_only"):
        with repository.database.transaction() as conn:
            conn.execute(
                "DELETE FROM execution_evidence_events WHERE event_id = ?",
                (event["event_id"],),
            )


@pytest.mark.asyncio
async def test_recovery_resolution_uses_cas_and_exact_replay(repository):
    intent = intent_value("recovery")
    await repository.create_intent(intent)
    case = recovery_value(intent)
    await repository.create_recovery_case(case)
    note = {
        "note_id": "note-0001",
        "actor": {"issuer": "https://issuer.test/", "subject": "operator-a"},
        "text": "Exact Host receipt materialized.",
        "created_at": DECIDED,
    }
    resolved = await repository.resolve_recovery_case(
        owner_subject="owner-a",
        case_id=case["case_id"],
        expected_version=0,
        resolution="exact_receipt_materialized",
        resolved_at=DECIDED,
        operator_note=note,
    )
    assert resolved["state"] == "resolved"
    assert (
        await repository.resolve_recovery_case(
            owner_subject="owner-a",
            case_id=case["case_id"],
            expected_version=0,
            resolution="exact_receipt_materialized",
            resolved_at=DECIDED,
            operator_note=note,
        )
        == resolved
    )
    with pytest.raises(RepositoryConflict, match="recovery_resolution_conflict"):
        await repository.resolve_recovery_case(
            owner_subject="owner-a",
            case_id=case["case_id"],
            expected_version=0,
            resolution="unresolved",
            resolved_at=DECIDED,
        )
    assert await repository.get_recovery_case("owner-b", case["case_id"]) is None
    assert await repository.list_recovery_cases("owner-b") == []


@pytest.mark.asyncio
async def test_checkpoint_plan_and_rollback_receipt_uniqueness(repository):
    checkpoint = checkpoint_value()
    created_checkpoint, duplicate = await repository.create_checkpoint(checkpoint)
    assert duplicate is False
    assert (await repository.create_checkpoint(deepcopy(checkpoint)))[1] is True

    conflicting_checkpoint = checkpoint_value(
        "other", created_entities=[
            {
                "handle": "1B",
                "entity_type": "AcDbLine",
                "layer": "CAD-MCP",
                "canonical_fingerprint": digest("entity-other"),
            }
        ]
    )
    with pytest.raises(RepositoryConflict, match="checkpoint_conflict"):
        await repository.create_checkpoint(conflicting_checkpoint)

    plan = plan_value(checkpoint)
    created_plan, duplicate = await repository.create_rollback_plan(plan)
    assert duplicate is False
    assert (await repository.create_rollback_plan(deepcopy(plan)))[1] is True
    conflicting_plan = plan_value(
        checkpoint,
        "other",
        rollback_execution_digest=digest("rollback-other"),
    )
    with pytest.raises(RepositoryConflict, match="rollback_plan_conflict"):
        await repository.create_rollback_plan(conflicting_plan)

    receipt = rollback_receipt_value(checkpoint, plan)
    created_receipt, duplicate = await repository.create_rollback_receipt(receipt)
    assert duplicate is False
    assert (await repository.create_rollback_receipt(deepcopy(receipt)))[1] is True
    conflicting_receipt = rollback_receipt_value(
        checkpoint,
        plan,
        "other",
        document_revision_after="revision-other",
    )
    with pytest.raises(RepositoryConflict, match="rollback_receipt_conflict"):
        await repository.create_rollback_receipt(conflicting_receipt)

    assert await repository.get_checkpoint("owner-b", checkpoint["checkpoint_id"]) is None
    assert await repository.list_checkpoints("owner-b") == []
    assert await repository.get_rollback_plan("owner-b", plan["plan_id"]) is None
    assert await repository.list_rollback_plans("owner-b") == []
    assert (
        await repository.get_rollback_receipt(
            "owner-b", receipt["rollback_receipt_id"]
        )
        is None
    )
    assert await repository.list_rollback_receipts("owner-b") == []
    assert created_checkpoint["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert created_plan["plan_id"] == plan["plan_id"]
    assert created_receipt["rollback_receipt_id"] == receipt["rollback_receipt_id"]
