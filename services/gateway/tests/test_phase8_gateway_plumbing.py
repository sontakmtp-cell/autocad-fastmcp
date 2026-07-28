from __future__ import annotations

import asyncio
import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest
import pytest_asyncio

from autocad_gateway.app import GatewayConfig
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.phase7_repository import Phase7Repository
from autocad_gateway.infrastructure.sqlite.phase8_repository import Phase8Repository
from autocad_gateway.infrastructure.sqlite.repositories import RepositoryConflict
from autocad_gateway.phase8_contract_adapter import CompiledProgram
from autocad_gateway.phase8_gateway import (
    Phase8FeatureFlags,
    Phase8GatewayService,
    canonical_rollout_policy_digest,
    phase8_binding_digest,
)
from test_phase7_domain_storage import (
    DECIDED,
    consent_value,
    digest as phase7_digest,
    intent_value,
    release_material,
    seed_parent_rows,
)


def digest(seed: str) -> str:
    return "sha256:" + sha256(seed.encode("utf-8")).hexdigest()


SOURCE = {
    "schema_version": "cad.program/1.0",
    "program_id": "program-1",
    "revision": 1,
    "operations": [{"operation_id": "op-1", "kind": "copy_as_new"}],
}
RUNTIME_PINS = {
    "runtime_id": "managed_dotnet",
    "host_family": "R25",
    "package_hash": digest("package"),
    "capability_manifest_hash": digest("capability-manifest"),
    "operation_registry_hash": digest("operation-registry"),
}
EFFECT_SUMMARY = (
    {
        "kind": "create_entities",
        "count": 1,
        "summary": "Create one bounded drawing entity.",
    },
)


def feature_flags(**updates) -> Phase8FeatureFlags:
    values = {
        "source_enabled": True,
        "compiler_enabled": True,
        "rollout_policy_epoch": 1,
        **updates,
    }
    value = Phase8FeatureFlags(**values)
    return replace(
        value,
        rollout_policy_digest=canonical_rollout_policy_digest(value),
    )


def compilation(
    *,
    source: dict = SOURCE,
    source_digest: str | None = None,
    checkpoint_strategy: str = "cad.rollback.checkpoint/1",
    create_count: int = 1,
    modify_count: int = 0,
    erase_count: int = 0,
) -> CompiledProgram:
    source_digest = source_digest or digest(json.dumps(source, sort_keys=True))
    return CompiledProgram(
        source=deepcopy(source),
        source_digest=source_digest,
        semantic_digest=digest("semantic-" + source_digest),
        plan={
            "schema_version": "cad.execution-plan/1",
            "operations": [{"operation_id": "expanded-op-1", "kind": "copy_as_new"}],
        },
        plan_digest=digest("plan-" + source_digest),
        expansion_digest=digest("expansion-" + source_digest),
        effect_manifest={
            "schema_version": "cad.effect-manifest/1",
            "create_count": create_count,
            "modify_count": modify_count,
            "erase_count": erase_count,
        },
        effect_digest=digest("effect-" + source_digest),
        target_set_digest=digest("targets-" + source_digest),
        reference_digest=digest("refs-" + source_digest),
        risk_class="low",
        trusted_effect_summary=EFFECT_SUMMARY,
        compiler_id="cad-program-compiler",
        compiler_version="1.0.0",
        compiler_hash=digest("compiler"),
        hard_budgets={"max_operations": 8, "max_entities": 8},
        required_capabilities=("cad.op.copy.line.v1",),
        operation_packs=("create-equivalent-v1",),
        validation_profiles=("geometry.basic/1",),
        runtime_pins=RUNTIME_PINS,
        checkpoint_strategy=checkpoint_strategy,
        create_count=create_count,
        modify_count=modify_count,
        erase_count=erase_count,
    )


class FakeCompiler:
    def __init__(self, result: CompiledProgram) -> None:
        self.result = result

    def compile(self, source: dict) -> CompiledProgram:
        assert source == self.result.source
        return self.result


@pytest_asyncio.fixture
async def phase8(tmp_path):
    database = SqliteDatabase(tmp_path / "phase8.db")
    await database.open()
    seed_parent_rows(database)
    repository = Phase8Repository(database)
    try:
        yield database, repository
    finally:
        await database.close()


async def create_root_and_plan(
    repository: Phase8Repository,
    *,
    result: CompiledProgram | None = None,
    plan_id: str = "phase8-plan-1",
    flags: Phase8FeatureFlags | None = None,
):
    result = result or compilation()
    flags = flags or feature_flags()
    await repository.create_revision(
        owner_subject="owner-a",
        program_id="program-1",
        revision=1,
        device_id="device-a",
        document_id="document-1",
        source_snapshot_id="snapshot-1",
        expected_document_revision="revision-before",
        source=result.source,
        source_digest=result.source_digest,
        semantic_digest=result.semantic_digest,
        lineage_kind="root",
    )
    plan, _ = await repository.seal_plan(
        owner_subject="owner-a",
        program_id="program-1",
        revision=1,
        compilation=result,
        rollout_policy_digest=canonical_rollout_policy_digest(flags),
        rollout_policy_epoch=flags.rollout_policy_epoch,
        plan_id=plan_id,
    )
    return result, plan


def test_phase8_flags_are_additive_default_off_and_extension_gates_fail_closed():
    config = GatewayConfig().validate()
    assert config.program_v1_source_enabled is False
    assert config.program_v1_compiler_enabled is False
    assert config.program_v1_create_pack_enabled is False
    assert config.program_v1_transform_pack_enabled is False
    assert config.program_v1_topology_pack_enabled is False
    assert config.program_v1_delete_pack_enabled is False
    assert config.checkpoint_v2_enabled is False
    assert config.lt_portable_write_enabled is False
    with pytest.raises(ValueError, match="rollout policy epoch"):
        GatewayConfig(
            program_v1_source_enabled=True,
            program_v1_compiler_enabled=True,
        ).validate()
    with pytest.raises(ValueError, match="destructive extension gate"):
        GatewayConfig(
            program_v1_source_enabled=True,
            program_v1_compiler_enabled=True,
            checkpoint_v2_enabled=True,
            program_v1_topology_pack_enabled=True,
        ).validate()
    with pytest.raises(ValueError, match="LT write certification gate"):
        GatewayConfig(lt_portable_write_enabled=True).validate()


@pytest.mark.asyncio
async def test_gateway_calls_injected_compiler_without_reinterpreting_source(phase8):
    _, repository = phase8
    result = compilation()
    disabled = Phase8GatewayService(repository, Phase8FeatureFlags())
    with pytest.raises(RepositoryConflict, match="feature_disabled"):
        await disabled.prepare_root(
            owner_subject="owner-a",
            program_id="program-1",
            device_id="device-a",
            document_id="document-1",
            source_snapshot_id="snapshot-1",
            expected_document_revision="revision-before",
            source=SOURCE,
        )
    service = Phase8GatewayService(
        repository,
        feature_flags(),
        compiler=FakeCompiler(result),
    )
    prepared = await service.prepare_root(
        owner_subject="owner-a",
        program_id="program-1",
        device_id="device-a",
        document_id="document-1",
        source_snapshot_id="snapshot-1",
        expected_document_revision="revision-before",
        source=SOURCE,
        plan_id="phase8-plan-1",
    )
    assert prepared["revision"]["source"] == SOURCE
    assert prepared["plan"]["plan"] == result.plan


@pytest.mark.asyncio
async def test_revisions_plans_refs_and_conflicts_are_immutable_and_cas_guarded(phase8):
    database, repository = phase8
    result, plan = await create_root_and_plan(repository)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.transaction() as conn:
            conn.execute(
                "UPDATE phase8_execution_plans SET risk_class = 'high' "
                "WHERE plan_id = ?",
                (plan["plan_id"],),
            )
    materialized, duplicate = await repository.create_materialized_ref(
        owner_subject="owner-a",
        plan_id=plan["plan_id"],
        materialized_ref_id="materialized-ref-1",
        snapshot_id="snapshot-1",
        device_id="device-a",
        document_id="document-1",
        document_revision="revision-before",
        ref_kind="query_result",
        query_digest=digest("query"),
        result_digest=digest("result"),
        fingerprint_digest=digest("fingerprints"),
        target_set_digest=plan["target_set_digest"],
        reference_digest=plan["reference_digest"],
        materialized={"entity_refs": [{"entity_id": "entity-1", "type": "LINE"}]},
    )
    assert duplicate is False
    assert materialized["result_digest"] == digest("result")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.transaction() as conn:
            conn.execute(
                "UPDATE phase8_materialized_refs SET result_digest = ? "
                "WHERE materialized_ref_id = ?",
                (digest("changed"), "materialized-ref-1"),
            )

    patched_source = {**SOURCE, "revision": 2}
    patched = compilation(source=patched_source)
    await repository.create_revision(
        owner_subject="owner-a",
        program_id="program-1",
        revision=2,
        device_id="device-a",
        document_id="document-1",
        source_snapshot_id="snapshot-1",
        expected_document_revision="revision-before",
        source=patched.source,
        source_digest=patched.source_digest,
        semantic_digest=patched.semantic_digest,
        lineage_kind="patch",
        parent_revision=1,
        lineage_request_digest=digest("patch-request"),
    )
    report, _ = await repository.create_conflict_report(
        owner_subject="owner-a",
        program_id="program-1",
        source_revision=1,
        candidate_revision=2,
        request_kind="patch",
        old_snapshot_id="snapshot-1",
        new_snapshot_id=None,
        request_digest=digest("patch-request"),
        conflicts_digest=digest("conflicts"),
        conflicts=[{"code": "target_fingerprint_changed", "ref_id": "entity-1"}],
        conflict_report_id="conflict-1",
    )
    assert report["state"] == "open"
    with pytest.raises(RepositoryConflict, match="rebase_conflict_open"):
        await repository.seal_plan(
            owner_subject="owner-a",
            program_id="program-1",
            revision=2,
            compilation=patched,
            rollout_policy_digest=canonical_rollout_policy_digest(feature_flags()),
            rollout_policy_epoch=1,
            plan_id="phase8-plan-2",
        )

    resolved_source = {**patched_source, "revision": 3}
    resolved = compilation(source=resolved_source)
    await repository.create_revision(
        owner_subject="owner-a",
        program_id="program-1",
        revision=3,
        device_id="device-a",
        document_id="document-1",
        source_snapshot_id="snapshot-1",
        expected_document_revision="revision-before",
        source=resolved.source,
        source_digest=resolved.source_digest,
        semantic_digest=resolved.semantic_digest,
        lineage_kind="conflict_resolution",
        parent_revision=2,
        lineage_request_digest=digest("resolution-request"),
    )

    outcomes = await asyncio.gather(
        repository.transition_conflict(
            owner_subject="owner-a",
            conflict_report_id="conflict-1",
            target="resolved",
            expected_sequence=1,
            resolution_revision=3,
            event_digest=digest("resolved"),
        ),
        repository.transition_conflict(
            owner_subject="owner-a",
            conflict_report_id="conflict-1",
            target="abandoned",
            expected_sequence=1,
            event_digest=digest("abandoned"),
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(value, RepositoryConflict) for value in outcomes) == 1
    with pytest.raises(sqlite3.IntegrityError, match="append_only"):
        with database.transaction() as conn:
            conn.execute(
                "DELETE FROM phase8_conflict_events WHERE conflict_report_id = ?",
                ("conflict-1",),
            )


@pytest.mark.asyncio
async def test_checkpoint_v1_cannot_seal_modify_or_erase_plan(phase8):
    _, repository = phase8
    unsafe = compilation(
        checkpoint_strategy="cad.rollback.checkpoint/1",
        create_count=0,
        modify_count=1,
    )
    await repository.create_revision(
        owner_subject="owner-a",
        program_id="program-1",
        revision=1,
        device_id="device-a",
        document_id="document-1",
        source_snapshot_id="snapshot-1",
        expected_document_revision="revision-before",
        source=unsafe.source,
        source_digest=unsafe.source_digest,
        semantic_digest=unsafe.semantic_digest,
        lineage_kind="root",
    )
    with pytest.raises(RepositoryConflict, match="checkpoint_v1_effect_mismatch"):
        await repository.seal_plan(
            owner_subject="owner-a",
            program_id="program-1",
            revision=1,
            compilation=unsafe,
            rollout_policy_digest=canonical_rollout_policy_digest(feature_flags()),
            rollout_policy_epoch=1,
            plan_id="unsafe-plan",
        )


@pytest.mark.asyncio
async def test_capability_admission_intersects_report_with_trusted_server_evidence(phase8):
    _, repository = phase8
    flags = feature_flags(
        create_pack_enabled=True,
        operation_pack_allowlist=("create-equivalent-v1",),
    )
    _, plan = await create_root_and_plan(repository, flags=flags)
    service = Phase8GatewayService(repository, flags)
    with pytest.raises(RepositoryConflict, match="capability_missing"):
        await service.admit(
            owner_subject="owner-a",
            device_id="device-a",
            plan_id=plan["plan_id"],
            action="commit",
            cohort="lab",
            reported_capabilities=("cad.op.copy.line.v1",),
            current_runtime_pins=RUNTIME_PINS,
        )

    evidence = {
            "evidence_id": "evidence-copy-line",
            "evidence_authority": "gateway_server",
            "owner_subject": "owner-a",
            "device_id": "device-a",
            "capability_key": "cad.op.copy.line.v1",
            "operation_pack": "create-equivalent-v1",
            "runtime_id": "managed_dotnet",
            "host_family": "R25",
            "entity_type": "LINE",
            "support_state": "lab_commit",
            "package_hash": RUNTIME_PINS["package_hash"],
            "capability_manifest_hash": RUNTIME_PINS["capability_manifest_hash"],
            "operation_registry_hash": RUNTIME_PINS["operation_registry_hash"],
            "package_signature_verified": True,
            "agent_evidence_digest": digest("agent-evidence"),
            "host_evidence_digest": digest("host-evidence"),
            "cohort": "lab",
            "evidence_version": "1",
            "valid_until": (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
            "evidence_digest": digest("trusted-capability-evidence"),
        }
    with pytest.raises(RepositoryConflict, match="capability_evidence_untrusted"):
        await repository.record_capability_evidence(
            {**evidence, "evidence_authority": "agent_self_report"}
        )
    await repository.record_capability_evidence(evidence)
    admitted = await service.admit(
        owner_subject="owner-a",
        device_id="device-a",
        plan_id=plan["plan_id"],
        action="commit",
        cohort="lab",
        reported_capabilities=("cad.op.copy.line.v1",),
        current_runtime_pins=RUNTIME_PINS,
    )
    assert admitted["capability_evidence_ids"] == ["evidence-copy-line"]
    changed_policy = Phase8GatewayService(
        repository,
        feature_flags(
            create_pack_enabled=True,
            operation_pack_allowlist=("create-equivalent-v1",),
            rollout_policy_epoch=2,
        ),
    )
    with pytest.raises(RepositoryConflict, match="policy_mismatch"):
        await changed_policy.admit(
            owner_subject="owner-a",
            device_id="device-a",
            plan_id=plan["plan_id"],
            action="commit",
            cohort="lab",
            reported_capabilities=("cad.op.copy.line.v1",),
            current_runtime_pins=RUNTIME_PINS,
        )


@pytest.mark.asyncio
async def test_phase7_release_requires_exact_phase8_intent_and_consent_binding(phase8):
    _, repository = phase8
    result = compilation(source_digest=phase7_digest("program"))
    result = CompiledProgram(
        **{
            **result.__dict__,
            "semantic_digest": digest("phase7-compatible-semantic"),
            "trusted_effect_summary": (
                {
                    "kind": "create_entities",
                    "count": 1,
                    "summary": "Create one line",
                },
            ),
            "runtime_pins": RUNTIME_PINS,
        }
    )
    _, plan = await create_root_and_plan(repository, result=result)
    phase7 = Phase7Repository(repository.database)
    intent_raw = intent_value()
    intent, _ = await phase7.create_intent(intent_raw)
    binding = phase8_binding_digest(plan)
    await repository.bind_intent(
        owner_subject="owner-a",
        intent_id=intent["intent_id"],
        plan_id=plan["plan_id"],
        binding_digest=binding,
    )
    consent_raw = consent_value(intent_raw)
    consent, _ = await phase7.create_consent(consent_raw)
    await repository.bind_consent(
        owner_subject="owner-a",
        consent_id=consent["consent_id"],
        intent_id=intent["intent_id"],
    )
    approved = await phase7.transition_consent(
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
    material = release_material(intent)
    material["payload"]["execution"].update(
        {
            "source_digest": plan["source_digest"],
            "semantic_digest": plan["semantic_digest"],
            "plan_digest": plan["plan_digest"],
            "expansion_digest": plan["expansion_digest"],
            "effect_digest": plan["effect_digest"],
            "target_set_digest": plan["target_set_digest"],
            "reference_digest": plan["reference_digest"],
            "compiler_hash": plan["compiler_hash"],
            "risk_class": plan["risk_class"],
            "trusted_effect_summary": plan["trusted_effect_summary"],
            "rollout_policy_digest": plan["rollout_policy_digest"],
            "rollout_policy_epoch": plan["rollout_policy_epoch"],
            "phase8_binding_digest": binding,
        }
    )
    tampered = deepcopy(material)
    tampered["payload"]["execution"]["target_set_digest"] = digest("wrong-targets")
    with pytest.raises(RepositoryConflict, match="job_binding_mismatch"):
        await phase7.release_intent(
            owner_subject="owner-a",
            intent_id=intent["intent_id"],
            expected_intent_version=0,
            consumed_at=DECIDED,
            consent_id=consent["consent_id"],
            expected_consent_version=approved["state_version"],
            **tampered,
        )

    outcomes = await asyncio.gather(
        phase7.release_intent(
            owner_subject="owner-a",
            intent_id=intent["intent_id"],
            expected_intent_version=0,
            consumed_at=DECIDED,
            consent_id=consent["consent_id"],
            expected_consent_version=approved["state_version"],
            **material,
        ),
        phase7.release_intent(
            owner_subject="owner-a",
            intent_id=intent["intent_id"],
            expected_intent_version=0,
            consumed_at=DECIDED,
            consent_id=consent["consent_id"],
            expected_consent_version=approved["state_version"],
            **material,
        ),
    )
    assert {value["job_existing"] for value in outcomes} == {False, True}
