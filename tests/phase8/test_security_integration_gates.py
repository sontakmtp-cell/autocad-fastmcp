from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256

import pytest

from autocad_contracts import (
    canonical_json,
    canonical_phase8_capability_evidence_digest,
)
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.phase7_repository import Phase7Repository
from autocad_gateway.infrastructure.sqlite.phase8_repository import Phase8Repository
from autocad_gateway.infrastructure.sqlite.repositories import RepositoryConflict
from autocad_gateway.phase8_gateway import (
    Phase8FeatureFlags,
    canonical_rollout_policy_digest,
    phase8_binding_digest,
)
from test_phase7_domain_storage import (
    DECIDED,
    consent_value,
    intent_value,
    release_material,
    seed_parent_rows,
)

from helpers import CanonicalCompilerAdapter, golden


def _canonical_digest(value: dict) -> str:
    return "sha256:" + sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _flags() -> Phase8FeatureFlags:
    value = Phase8FeatureFlags(
        source_enabled=True,
        compiler_enabled=True,
        create_pack_enabled=True,
        rollout_policy_epoch=1,
    )
    return Phase8FeatureFlags(
        **{
            **value.__dict__,
            "rollout_policy_digest": canonical_rollout_policy_digest(value),
        }
    )


def _source_for_gateway() -> dict:
    value = deepcopy(golden()["source"])
    value.pop("semantic_digest", None)
    value.update(
        {
            "program_id": "program-1",
            "program_revision": 1,
            "device_id": "device-a",
            "source_snapshot_id": "snapshot-1",
            "document_id": "document-1",
            "expected_document_revision": "revision-before",
        }
    )
    value.pop("parent_revision", None)
    return value


async def _store_canonical_plan(database: SqliteDatabase):
    repository = Phase8Repository(database)
    compilation = CanonicalCompilerAdapter().compile(_source_for_gateway())
    await repository.create_revision(
        owner_subject="owner-a",
        program_id="program-1",
        revision=1,
        device_id="device-a",
        document_id="document-1",
        source_snapshot_id="snapshot-1",
        expected_document_revision="revision-before",
        source=compilation.source,
        source_digest=compilation.source_digest,
        semantic_digest=compilation.semantic_digest,
        lineage_kind="root",
    )
    flags = _flags()
    plan, _ = await repository.seal_plan(
        owner_subject="owner-a",
        program_id="program-1",
        revision=1,
        compilation=compilation,
        rollout_policy_digest=canonical_rollout_policy_digest(flags),
        rollout_policy_epoch=flags.rollout_policy_epoch,
        plan_id="phase8-security-plan",
    )
    with database.transaction() as conn:
        conn.execute(
            "UPDATE cad_program_revisions SET program_digest = ? "
            "WHERE program_id = ? AND revision = 1",
            (plan["source_digest"], "program-1"),
        )
    return repository, plan


def _phase8_release_material(intent: dict, plan: dict) -> dict:
    value = release_material(intent)
    value["payload"]["execution"].update(
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
            "phase8_binding_digest": phase8_binding_digest(plan),
        }
    )
    return value


@pytest.mark.asyncio
async def test_phase8_release_rejects_missing_intent_and_consent_bindings(
    tmp_path,
):
    database = SqliteDatabase(tmp_path / "phase8-security-bindings.db")
    await database.open()
    try:
        seed_parent_rows(database)
        repository, plan = await _store_canonical_plan(database)
        phase7 = Phase7Repository(database)

        unbound_raw = intent_value(
            suffix="unbound",
            program_digest=plan["source_digest"],
            trusted_effect_summary=plan["trusted_effect_summary"],
        )
        unbound, _ = await phase7.create_intent(unbound_raw)
        unbound_consent, _ = await phase7.create_consent(
            consent_value(unbound_raw, suffix="unbound")
        )
        approved_unbound = await phase7.transition_consent(
            owner_subject="owner-a",
            consent_id=unbound_consent["consent_id"],
            target="approved",
            expected_version=0,
            transition_at=DECIDED,
            decision_source="portal_recent_auth",
            decision_principal={
                "issuer": "https://issuer.test/",
                "subject": "user-a",
            },
        )
        with pytest.raises(RepositoryConflict, match="job_binding_mismatch"):
            await phase7.release_intent(
                owner_subject="owner-a",
                intent_id=unbound["intent_id"],
                expected_intent_version=0,
                consumed_at=DECIDED,
                consent_id=unbound_consent["consent_id"],
                expected_consent_version=approved_unbound["state_version"],
                **_phase8_release_material(unbound, plan),
            )

    finally:
        await database.close()


@pytest.mark.asyncio
async def test_bound_release_succeeds_and_cross_owner_guessed_ids_are_hidden(
    tmp_path,
):
    database = SqliteDatabase(tmp_path / "phase8-security-owner-scope.db")
    await database.open()
    try:
        seed_parent_rows(database)
        repository, plan = await _store_canonical_plan(database)
        phase7 = Phase7Repository(database)
        bound_raw = intent_value(
            suffix="bound",
            program_digest=plan["source_digest"],
            trusted_effect_summary=plan["trusted_effect_summary"],
        )
        bound, _ = await phase7.create_intent(bound_raw)
        exact_binding = phase8_binding_digest(plan)
        await repository.bind_intent(
            owner_subject="owner-a",
            intent_id=bound["intent_id"],
            plan_id=plan["plan_id"],
            binding_digest=exact_binding,
        )
        consent, _ = await phase7.create_consent(
            consent_value(bound_raw, suffix="bound")
        )
        await repository.bind_consent(
            owner_subject="owner-a",
            consent_id=consent["consent_id"],
            intent_id=bound["intent_id"],
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
        released = await phase7.release_intent(
            owner_subject="owner-a",
            intent_id=bound["intent_id"],
            expected_intent_version=0,
            consumed_at=DECIDED,
            consent_id=consent["consent_id"],
            expected_consent_version=approved["state_version"],
            **_phase8_release_material(bound, plan),
        )
        assert released["job"]["state"] == "queued"

        assert await repository.get_plan("owner-b", plan["plan_id"]) is None
        with pytest.raises(RepositoryConflict, match="not_found"):
            await repository.bind_intent(
                owner_subject="owner-b",
                intent_id=bound["intent_id"],
                plan_id=plan["plan_id"],
                binding_digest=exact_binding,
            )
        with pytest.raises(RepositoryConflict, match="not_found"):
            await repository.bind_consent(
                owner_subject="owner-b",
                consent_id=consent["consent_id"],
                intent_id=bound["intent_id"],
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_materialized_and_capability_evidence_digests_are_recomputed(
    tmp_path,
):
    database = SqliteDatabase(tmp_path / "phase8-security-digests.db")
    await database.open()
    try:
        seed_parent_rows(database)
        repository, plan = await _store_canonical_plan(database)
        materialized = {
            "schema_version": "cad.materialized-ref/1",
            "target_refs": [
                {
                    "ref_id": "ref-security-1",
                    "owner_id": "owner-a",
                    "device_id": "device-a",
                    "document_id": "document-1",
                    "snapshot_id": "snapshot-1",
                    "document_revision": "revision-before",
                    "entity_id": "entity-security-1",
                    "entity_type": "LINE",
                    "fingerprint": "sha256:" + "e" * 64,
                }
            ],
        }
        wrong_digest = "sha256:" + "f" * 64
        with pytest.raises(
            RepositoryConflict,
            match="materialized_ref_digest_mismatch",
        ):
            await repository.create_materialized_ref(
                owner_subject="owner-a",
                plan_id=plan["plan_id"],
                materialized_ref_id="materialized-security-wrong",
                snapshot_id="snapshot-1",
                device_id="device-a",
                document_id="document-1",
                document_revision="revision-before",
                ref_kind="query_result",
                query_digest="sha256:" + "a" * 64,
                result_digest=wrong_digest,
                fingerprint_digest="sha256:" + "b" * 64,
                target_set_digest=plan["target_set_digest"],
                reference_digest=plan["reference_digest"],
                materialized=materialized,
            )

        pins = plan["runtime_pins"]
        issued = datetime.now(timezone.utc)
        evidence = {
            "schema_version": "cad.capability-evidence/1",
            "evidence_id": "evidence-security-1",
            "evidence_authority": "gateway_server",
            "owner_subject": "owner-a",
            "device_id": "device-a",
            "capability_key": plan["required_capabilities"][0],
            "operation_pack": plan["operation_packs"][0],
            "runtime_id": pins["runtime_id"],
            "host_family": pins["host_family"],
            "entity_type": "LINE",
            "support_state": "preview_only",
            "package_hash": pins["package_hash"],
            "capability_manifest_hash": pins["capability_manifest_hash"],
            "operation_registry_hash": pins["operation_registry_hash"],
            "package_signature_verified": True,
            "agent_evidence_digest": "sha256:" + "c" * 64,
            "host_evidence_digest": "sha256:" + "d" * 64,
            "cohort": "phase8-security",
            "evidence_version": "1",
            "issued_at": issued.isoformat(),
            "valid_until": "2099-01-01T00:00:00+00:00",
        }
        wire = {key: value for key, value in evidence.items() if key != "owner_subject"}
        evidence["evidence_digest"] = canonical_phase8_capability_evidence_digest(wire)
        recorded, _ = await repository.record_capability_evidence(evidence)
        assert recorded["evidence_digest"] == evidence["evidence_digest"]

        tampered = deepcopy(evidence)
        tampered["evidence_id"] = "evidence-security-tampered"
        tampered["support_state"] = "certified"
        with pytest.raises(RepositoryConflict, match="capability_evidence_invalid"):
            await repository.record_capability_evidence(tampered)

        assert _canonical_digest(materialized) != wrong_digest
    finally:
        await database.close()
