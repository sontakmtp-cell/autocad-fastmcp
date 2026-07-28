from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    ProgramCommandMessage,
    build_execution_binding_v1,
    canonical_phase8_capability_evidence_digest,
    parse_agent_message,
)
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.phase8_repository import Phase8Repository
from autocad_gateway.phase8_gateway import (
    Phase8FeatureFlags,
    canonical_rollout_policy_digest,
    phase8_binding_digest,
)
from autocad_desktop_agent.phase8_admission import (
    Phase8AdmissionPolicy,
    Phase8PlanAdmission,
)

from helpers import CanonicalCompilerAdapter, compile_golden, golden


NOW = datetime(2026, 7, 28, tzinfo=timezone.utc).isoformat()


def _insert(conn, table: str, values: dict) -> None:
    fields = ", ".join(values)
    markers = ", ".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO {table} ({fields}) VALUES ({markers})",
        tuple(values.values()),
    )


def _seed_gateway_parents(database: SqliteDatabase) -> None:
    with database.transaction() as conn:
        _insert(
            conn,
            "devices",
            {
                "device_id": "device-001",
                "owner_subject": "owner-phase8",
                "display_name": "Phase 8 conformance device",
                "status": "online",
                "capabilities_json": "[]",
                "fixture_auth_ref": "fixture",
                "created_at": NOW,
                "updated_at": NOW,
            },
        )
        _insert(
            conn,
            "jobs",
            {
                "job_id": "phase8-snapshot-job",
                "owner_subject": "owner-phase8",
                "device_id": "device-001",
                "kind": "observe",
                "effect_class": "read",
                "state": "succeeded",
                "state_version": 1,
                "command_id": "phase8-snapshot-command",
                "idempotency_key": "phase8-snapshot-key",
                "payload_hash": "a" * 64,
                "payload_json": "{}",
                "created_at": NOW,
                "updated_at": NOW,
            },
        )
        _insert(
            conn,
            "snapshots",
            {
                "snapshot_id": "snapshot-001",
                "owner_subject": "owner-phase8",
                "device_id": "device-001",
                "job_id": "phase8-snapshot-job",
                "revision": 1,
                "document_revision": "revision-007",
                "observation_level": "summary",
                "drawing_json": "{}",
                "entity_summary_json": "{}",
                "entities_json": "[]",
                "created_at": NOW,
            },
        )
        _insert(
            conn,
            "cad_programs",
            {
                "program_id": "phase8-golden",
                "owner_subject": "owner-phase8",
                "device_id": "device-001",
                "document_id": "document-001",
                "created_at": NOW,
                "updated_at": NOW,
            },
        )


def _flags() -> Phase8FeatureFlags:
    base = Phase8FeatureFlags(
        source_enabled=True,
        compiler_enabled=True,
        create_pack_enabled=True,
        rollout_policy_epoch=1,
    )
    return Phase8FeatureFlags(
        **{
            **base.__dict__,
            "rollout_policy_digest": canonical_rollout_policy_digest(base),
        }
    )


@pytest.mark.asyncio
async def test_real_compiler_output_is_stored_immutably_and_bound_by_gateway(
    tmp_path,
):
    database = SqliteDatabase(tmp_path / "phase8-cross-stack.db")
    await database.open()
    try:
        _seed_gateway_parents(database)
        repository = Phase8Repository(database)
        compilation = CanonicalCompilerAdapter().compile(
            deepcopy(golden()["source"])
        )
        fixture = golden()
        revision, _ = await repository.create_revision(
            owner_subject="owner-phase8",
            program_id="phase8-golden",
            revision=1,
            device_id="device-001",
            document_id="document-001",
            source_snapshot_id="snapshot-001",
            expected_document_revision="revision-007",
            source=compilation.source,
            source_digest=compilation.source_digest,
            semantic_digest=compilation.semantic_digest,
            lineage_kind="root",
        )
        flags = _flags()
        prepared, _ = await repository.seal_plan(
            owner_subject="owner-phase8",
            program_id="phase8-golden",
            revision=1,
            compilation=compilation,
            rollout_policy_digest=canonical_rollout_policy_digest(flags),
            rollout_policy_epoch=flags.rollout_policy_epoch,
            plan_id="phase8-golden.r2",
        )
        stored = await repository.get_plan("owner-phase8", "phase8-golden.r2")

        assert revision["source"] == compilation.source
        assert {key: value for key, value in stored.items() if key != "invalidations"} == prepared
        assert stored["invalidations"] == []
        assert stored["plan"] == compilation.plan
        assert stored["plan_digest"] == fixture["execution_plan_digest"]
        assert stored["effect_digest"] == fixture["effect_manifest_digest"]
        assert stored["source_digest"] == fixture["source_digest"]
        binding_digest = phase8_binding_digest(stored)
        assert binding_digest == phase8_binding_digest(deepcopy(stored))
        tampered = deepcopy(stored)
        tampered["risk_class"] = "medium"
        assert phase8_binding_digest(tampered) != binding_digest
        assert (await repository.get_plan("owner-other", "phase8-golden.r2")) is None
    finally:
        await database.close()


def test_cad_agent_2_phase8_command_acceptance_gate():
    fixture, source, plan, _ = compile_golden()
    expires_at = "2026-07-28T01:00:00+00:00"
    binding_v1 = build_execution_binding_v1(
        plan,
        action="preview",
        preview_id="preview-phase8",
        preview_expires_at=expires_at,
    )
    fields = ProgramCommandMessage.model_fields
    assert {
        "execution_plan",
        "approval_binding",
        "capability_evidence",
    } <= fields.keys(), "cad.agent/2 lacks mandatory canonical Phase 8 fields"

    payload = {
        "protocol_version": "cad.agent/2",
        "message_type": "command",
        "message_id": "message-phase8",
        "session_id": "session-phase8",
        "device_id": source.device_id,
        "job_id": "job-phase8",
        "command_id": "command-phase8",
        "sequence": 1,
        "issued_at": NOW,
        "idempotency_key": binding_v1.execution_binding_digest,
        "payload_hash": "0" * 64,
        "kind": "program_preview",
        "effect_class": "write",
        "binding": binding_v1.model_dump(mode="json"),
        "execution_plan": plan.model_dump(mode="json"),
        "capability_evidence": _capability_evidence(plan),
        "preview_id": "preview-phase8",
        "expires_at": expires_at,
    }
    parsed = parse_agent_message(payload)
    assert parsed.execution_plan.execution_plan_digest == fixture[
        "execution_plan_digest"
    ]


def _capability_evidence(plan) -> list[dict]:
    evidence = []
    for index, capability in enumerate(plan.required_capabilities, start=1):
        value = {
            "schema_version": "cad.capability-evidence/1",
            "evidence_id": f"phase8-golden-evidence-{index}",
            "evidence_authority": "gateway_server",
            "device_id": plan.device_id,
            "capability_key": capability,
            "operation_pack": "create.core/1",
            "runtime_id": plan.execution_pins.runtime_id,
            "host_family": plan.execution_pins.host_family,
            "entity_type": "LINE",
            "support_state": "preview_only",
            "package_hash": plan.execution_pins.package_hash,
            "capability_manifest_hash": plan.execution_pins.capability_manifest_hash,
            "operation_registry_hash": plan.execution_pins.operation_registry_hash,
            "package_signature_verified": True,
            "agent_evidence_digest": "sha256:" + "8" * 64,
            "host_evidence_digest": "sha256:" + "9" * 64,
            "cohort": "phase8-conformance",
            "evidence_version": "phase8.evidence.1",
            "issued_at": "2026-01-01T00:00:00+00:00",
            "valid_until": "2099-01-01T00:00:00+00:00",
        }
        value["evidence_digest"] = canonical_phase8_capability_evidence_digest(value)
        evidence.append(value)
    return evidence


def test_desktop_admission_consumes_canonical_wire_artifacts():
    _, source, plan, _ = compile_golden()
    expires_at = "2099-01-01T00:00:00+00:00"
    binding = build_execution_binding_v1(
        plan,
        action="preview",
        preview_id="preview-phase8",
        preview_expires_at=expires_at,
    )
    evidence = _capability_evidence(plan)
    policy = Phase8AdmissionPolicy(
        source_enabled=True,
        create_pack_enabled=True,
        transform_pack_enabled=False,
        checkpoint_v2_enabled=False,
        operation_pack_allowlist=frozenset({"create.core/1"}),
        rollout_policy_epoch=1,
    )

    admitted = Phase8PlanAdmission(policy).verify(
        plan.model_dump(mode="json"),
        binding=binding.model_dump(mode="json"),
        command_kind="program_preview",
        approval_binding=None,
        capability_states={
            capability: "preview_only" for capability in plan.required_capabilities
        },
        server_capability_evidence=evidence,
        device_id=source.device_id,
        issued_at=NOW,
        preview_id="preview-phase8",
        preview_expires_at=expires_at,
    )

    assert admitted.execution_plan_digest == plan.execution_plan_digest
    assert admitted.host_arguments() == {
        "execution_plan": plan.model_dump(mode="json", exclude_none=True)
    }


def test_legacy_agent_command_cannot_smuggle_phase8_plan_as_extra_data():
    fixture = golden()
    legacy = {
        "protocol_version": "cad.agent/2",
        "message_type": "command",
        "message_id": "message-legacy",
        "session_id": "session-phase8",
        "device_id": "device-001",
        "job_id": "job-phase8",
        "command_id": "command-phase8",
        "sequence": 1,
        "issued_at": NOW,
        "idempotency_key": "idempotency-phase8",
        "payload_hash": "0" * 64,
        "kind": "program_preview",
        "effect_class": "write",
        "binding": {
            "program_digest": fixture["source_digest"],
            "execution_digest": fixture["execution_plan_digest"],
            "document_id": "document-001",
            "document_revision": "revision-007",
            **fixture["plan"]["execution_pins"],
        },
        "program": fixture["source"],
        "execution_plan": fixture["plan"],
        "preview_id": "preview-phase8",
        "expires_at": "2026-07-28T01:00:00+00:00",
    }
    with pytest.raises(ValidationError):
        ProgramCommandMessage.model_validate(legacy)
