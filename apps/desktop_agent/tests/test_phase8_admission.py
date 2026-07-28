from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from autocad_contracts.agent_protocol import canonical_json

from autocad_desktop_agent.config import AgentConfig
from autocad_desktop_agent.executor import AgentExecutionError
from autocad_desktop_agent.phase8_admission import (
    Phase8AdmissionPolicy,
    Phase8PlanAdmission,
)
from autocad_desktop_agent.program_executor import (
    DocumentWriteSerializer,
    ProgramCommandExecutor,
    RollbackCommandExecutor,
)
from autocad_desktop_agent.runtime.managed_dotnet import ManagedDotNetCadReadPort


def _digest(domain: str, value: dict) -> str:
    encoded = canonical_json({"domain": domain, "payload": value}).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


def _binding(*, program_digest: str, execution_digest: str) -> dict:
    return {
        "program_digest": program_digest,
        "execution_digest": execution_digest,
        "document_id": "doc-1",
        "document_revision": "42",
        "runtime_id": "managed_dotnet",
        "runtime_role": "primary",
        "host_family": "R25",
        "host_version": "1.0.0",
        "package_id": "autocad.managed_host.r25",
        "package_version": "1.0.0",
        "package_hash": f"sha256:{'1' * 64}",
        "capability_manifest_hash": f"sha256:{'2' * 64}",
        "operation_registry_version": "cad.operation-registry/1",
        "operation_registry_hash": f"sha256:{'3' * 64}",
        "policy_version": "phase8-lab-v1",
    }


def _plan(
    *,
    transform: bool = False,
    kind: str | None = None,
) -> tuple[dict, dict, dict]:
    source_digest = f"sha256:{'4' * 64}"
    operation_kind = kind or ("move" if transform else "copy")
    operations = [
        {
            "operation_id": "op-1",
            "kind": operation_kind,
            "arguments": {"target_ref": "entity-ref-1"},
        }
    ]
    effect = {
        "operation_id": "op-1",
        "create_count": 0 if transform else 1,
        "modify_count": 1 if transform else 0,
        "erase_count": 0,
        "capability_key": (
            "cad.op.move.line.v1"
            if transform
            else "cad.op.copy.line.v1"
        ),
        "operation_pack": "transform.basic/1" if transform else "create.basic/1",
    }
    effect_manifest = {
        "schema_version": "cad.effect-manifest/1",
        "operations": [effect],
        "totals": {
            "create_count": effect["create_count"],
            "modify_count": effect["modify_count"],
            "erase_count": 0,
        },
    }
    pins = _binding(program_digest=source_digest, execution_digest="pending")
    pins = {
        key: value
        for key, value in pins.items()
        if key
        not in {
            "program_digest",
            "execution_digest",
            "document_id",
            "document_revision",
        }
    }
    plan = {
        "schema_version": "cad.execution-plan/1",
        "source": {
            "schema_version": "cad.program/1.0",
            "digest_domain": "cad.program.source/1",
            "program_id": "program-1",
            "revision": 1,
            "device_id": "device-1",
            "snapshot_id": "snapshot-1",
            "document_id": "doc-1",
            "document_revision": "42",
            "digest": source_digest,
        },
        "compiler": {
            "id": "gateway-compiler",
            "version": "1.0.0",
            "hash": f"sha256:{'5' * 64}",
        },
        "operations": operations,
        "expansion_digest": _digest(
            "cad.program.expansion/1",
            {"operations": operations},
        ),
        "effect_manifest": effect_manifest,
        "effect_manifest_digest": _digest(
            "cad.effect-manifest/1",
            effect_manifest,
        ),
        "pins": pins,
        "budgets": {
            "estimated": {
                "operations": 1,
                "entities": 1,
                "vertices": 0,
                "text_bytes": 0,
                "payload_bytes": 16_384,
                "result_bytes": 16_384,
                "checkpoint_bytes": 16_384,
            },
            "hard": {
                "operations": 16,
                "entities": 16,
                "vertices": 256,
                "text_bytes": 4096,
                "payload_bytes": 65_536,
                "result_bytes": 65_536,
                "checkpoint_bytes": 65_536,
            },
        },
        "required_capabilities": [
            {
                "key": (
                    "cad.op.move.line.v1"
                    if transform
                    else "cad.op.copy.line.v1"
                ),
                "minimum_state": "lab_commit" if transform else "preview_only",
            }
        ],
        "operation_packs": [
            "transform.basic/1" if transform else "create.basic/1"
        ],
        "materialized_refs": [
            {
                "ref_id": "entity-ref-1",
                "device_id": "device-1",
                "document_id": "doc-1",
                "snapshot_id": "snapshot-1",
                "document_revision": "42",
                "entity_id": "entity-1",
                "entity_type": "LINE",
                "fingerprint": f"sha256:{'9' * 64}",
            }
        ],
        "validation_profiles": [
            {
                "profile_id": "geometry.basic/1",
                "digest": f"sha256:{'a' * 64}",
            }
        ],
        "checkpoint_strategy": (
            {
                "schema_version": "cad.rollback.checkpoint/2",
                "strategy": "restore_typed_preimage",
            }
            if transform
            else {
                "schema_version": "cad.rollback.checkpoint/1",
                "strategy": "erase_created_entities",
            }
        ),
    }
    capability_key = plan["required_capabilities"][0]["key"]
    capability_state = "lab_commit" if transform else "preview_only"
    evidence = _capability_evidence(
        capability_key,
        state=capability_state,
        pins=plan["pins"],
        compiler_hash=plan["compiler"]["hash"],
    )
    plan["pins"]["capability_evidence_digest"] = evidence["digest"]
    rollout = {
        "epoch": 1,
        "source_enabled": True,
        "create_pack_enabled": True,
        "transform_pack_enabled": True,
        "checkpoint_v2_enabled": True,
        "topology_pack_enabled": False,
        "delete_pack_enabled": False,
        "lt_write_enabled": False,
        "operation_pack_allowlist": ["create.basic/1", "transform.basic/1"],
        "capability_evidence_digest": evidence["digest"],
        "cohort_id": "phase8-lab",
        "cohort_allowed": True,
        "runtime_allowlist": ["managed_dotnet:R25"],
        "entity_type_allowlist": ["LINE", "CIRCLE", "LWPOLYLINE"],
    }
    rollout["digest"] = _digest("cad.rollout-policy/1", rollout)
    plan["rollout_policy"] = rollout
    plan["pins"]["rollout_policy_digest"] = rollout["digest"]
    plan["pins"]["capability_state_hash"] = _digest(
        "cad.capability-intersection/1",
        {"capability_states": {capability_key: capability_state}}
    )
    plan["target_set_digest"] = _digest(
        "cad.target-refs/1",
        {"target_refs": plan["materialized_refs"]},
    )
    plan["validation_profile_digest"] = _digest(
        "cad.validation-profiles/1",
        {"validation_profiles": plan["validation_profiles"]},
    )
    plan["checkpoint_strategy_digest"] = _digest(
        "cad.checkpoint-strategy/1",
        plan["checkpoint_strategy"],
    )
    plan["hard_budget_digest"] = _digest(
        "cad.execution-budgets/1",
        {
            "max_operations": plan["budgets"]["hard"]["operations"],
            "max_entities": plan["budgets"]["hard"]["entities"],
            "max_vertices": plan["budgets"]["hard"]["vertices"],
            "max_text_bytes": plan["budgets"]["hard"]["text_bytes"],
        },
    )
    plan["execution_plan_digest"] = _digest("cad.execution-plan/1", plan)
    binding = _binding(
        program_digest=source_digest,
        execution_digest=plan["execution_plan_digest"],
    )
    return plan, binding, evidence


def _capability_evidence(
    key: str,
    *,
    state: str,
    pins: dict,
    compiler_hash: str,
) -> dict:
    value = {
        "schema_version": "cad.capability-evidence/1",
        "evidence_id": "evidence-1",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
        "revoked": False,
        "rollout_policy_epoch": 1,
        "package_hash": pins["package_hash"],
        "host_family": pins["host_family"],
        "operation_registry_hash": pins["operation_registry_hash"],
        "compiler_hash": compiler_hash,
        "policy_version": pins["policy_version"],
        "capabilities": [{"key": key, "state": state}],
    }
    value["digest"] = _digest("cad.capability-evidence/1", value)
    return value


def _approval(plan: dict, binding: dict) -> dict:
    preview_expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=10)
    ).isoformat()
    identity = {
        "action": "program_commit",
        "execution_plan_digest": plan["execution_plan_digest"],
        "effect_manifest_digest": plan["effect_manifest_digest"],
        "target_set_digest": plan["target_set_digest"],
        "document_id": binding["document_id"],
        "document_revision_before": binding["document_revision"],
        "preview_id": "preview-1",
        "preview_expires_at": preview_expires_at,
        "receipt_id": "receipt-1",
        "checkpoint_strategy_digest": plan["checkpoint_strategy_digest"],
        "operation_packs": plan["operation_packs"],
        "runtime_id": binding["runtime_id"],
        "host_family": binding["host_family"],
        "package_hash": binding["package_hash"],
        "operation_registry_hash": binding["operation_registry_hash"],
        "policy_version": binding["policy_version"],
        "rollout_policy_digest": plan["pins"]["rollout_policy_digest"],
    }
    return {
        "intent_digest": f"sha256:{'6' * 64}",
        "binding_domain": "cad.execution-intent/2",
        "consent_id": "consent-1",
        "approval_proof_digest": f"sha256:{'7' * 64}",
        "action": "program_commit",
        "preview_id": "preview-1",
        "preview_expires_at": preview_expires_at,
        "receipt_id": "receipt-1",
        "source_digest": plan["source"]["digest"],
        "compiler_hash": plan["compiler"]["hash"],
        "expansion_digest": plan["expansion_digest"],
        "effect_manifest_digest": plan["effect_manifest_digest"],
        "execution_plan_digest": plan["execution_plan_digest"],
        "capability_state_hash": plan["pins"]["capability_state_hash"],
        "target_set_digest": plan["target_set_digest"],
        "validation_profile_digest": plan["validation_profile_digest"],
        "checkpoint_strategy_digest": plan["checkpoint_strategy_digest"],
        "hard_budget_digest": plan["hard_budget_digest"],
        "effect_identity_digest": _digest(
            "cad.effect-identity/1",
            identity,
        ),
        **plan["pins"],
    }


def _admission() -> Phase8PlanAdmission:
    return Phase8PlanAdmission(
        Phase8AdmissionPolicy(
            source_enabled=True,
            create_pack_enabled=True,
            transform_pack_enabled=True,
            checkpoint_v2_enabled=True,
            operation_pack_allowlist=frozenset(
                {"create.basic/1", "transform.basic/1"}
            ),
            rollout_policy_epoch=1,
        )
    )


def test_preview_accepts_exact_sealed_plan_and_forwards_no_source() -> None:
    plan, binding, evidence = _plan()
    verified = _admission().verify(
        plan,
        binding=binding,
        command_kind="program_preview",
        approval_binding=None,
        capability_states={"cad.op.copy.line.v1": "preview_only"},
        server_capability_evidence=evidence,
    )

    assert verified.host_arguments() == {"execution_plan": plan}
    result = _admission().verify_result(
        verified,
        {
            "execution_plan_digest": plan["execution_plan_digest"],
            "effect_manifest_digest": plan["effect_manifest_digest"],
            "target_set_digest": plan["target_set_digest"],
            "hard_budget_digest": plan["hard_budget_digest"],
            "rollout_policy_digest": plan["pins"]["rollout_policy_digest"],
            "transaction_aborted": True,
            "drawing_unchanged": True,
        },
        command_kind="program_preview",
    )
    assert result["drawing_unchanged"] is True


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("expansion_digest", "expansion_mismatch"),
        ("effect_manifest_digest", "effect_mismatch"),
        ("target_set_digest", "target_mismatch"),
        ("hard_budget_digest", "budget_mismatch"),
        ("execution_plan_digest", "plan_mismatch"),
    ],
)
def test_digest_mismatch_fails_before_dispatch(field: str, code: str) -> None:
    plan, binding, evidence = _plan()
    plan[field] = f"sha256:{'f' * 64}"
    if field == "execution_plan_digest":
        binding["execution_digest"] = plan[field]

    with pytest.raises(AgentExecutionError, match=code):
        _admission().verify(
            plan,
            binding=binding,
            command_kind="program_preview",
            approval_binding=None,
            capability_states={"cad.op.copy.line.v1": "preview_only"},
            server_capability_evidence=evidence,
        )


def test_runtime_package_registry_and_policy_pins_are_exact() -> None:
    plan, binding, evidence = _plan()
    cases = {
        "host_version": "runtime_mismatch",
        "package_hash": "package_mismatch",
        "operation_registry_hash": "registry_mismatch",
        "policy_version": "policy_mismatch",
    }
    for field, code in cases.items():
        changed = dict(binding)
        changed[field] = (
            f"sha256:{'e' * 64}" if field.endswith("_hash") else "changed"
        )
        with pytest.raises(AgentExecutionError, match=code):
            _admission().verify(
                plan,
                binding=changed,
                command_kind="program_preview",
                approval_binding=None,
                capability_states={"cad.op.copy.line.v1": "preview_only"},
                server_capability_evidence=evidence,
            )

    missing = dict(binding)
    missing.pop("policy_version")
    with pytest.raises(AgentExecutionError, match="policy_mismatch"):
        _admission().verify(
            plan,
            binding=missing,
            command_kind="program_preview",
            approval_binding=None,
            capability_states={"cad.op.copy.line.v1": "preview_only"},
            server_capability_evidence=evidence,
        )


def test_hard_budget_and_granular_capability_state_fail_closed() -> None:
    plan, binding, evidence = _plan()
    plan["budgets"]["hard"]["operations"] = 0
    plan["hard_budget_digest"] = _digest(
        "cad.execution-budgets/1",
        {
            "max_operations": plan["budgets"]["hard"]["operations"],
            "max_entities": plan["budgets"]["hard"]["entities"],
            "max_vertices": plan["budgets"]["hard"]["vertices"],
            "max_text_bytes": plan["budgets"]["hard"]["text_bytes"],
        },
    )
    plan["execution_plan_digest"] = _digest(
        "cad.execution-plan/1",
        {key: value for key, value in plan.items() if key != "execution_plan_digest"}
    )
    binding["execution_digest"] = plan["execution_plan_digest"]
    with pytest.raises(AgentExecutionError, match="budget_exceeded"):
        _admission().verify(
            plan,
            binding=binding,
            command_kind="program_preview",
            approval_binding=None,
            capability_states={"cad.op.copy.line.v1": "preview_only"},
            server_capability_evidence=evidence,
        )

    plan, binding, evidence = _plan()
    with pytest.raises(AgentExecutionError, match="capability_missing"):
        _admission().verify(
            plan,
            binding=binding,
            command_kind="program_preview",
            approval_binding=None,
            capability_states={"cad.op.copy.line.v1": "contract_only"},
            server_capability_evidence=evidence,
        )


def test_self_report_cannot_exceed_server_evidence_or_stale_epoch() -> None:
    plan, binding, evidence = _plan()
    with pytest.raises(AgentExecutionError, match="capability_missing"):
        _admission().verify(
            plan,
            binding=binding,
            command_kind="program_commit",
            approval_binding=None,
            capability_states={"cad.op.copy.line.v1": "certified"},
            server_capability_evidence=evidence,
            preview_id="preview-1",
            receipt_id="receipt-1",
        )

    stale_policy = Phase8PlanAdmission(
        Phase8AdmissionPolicy(
            source_enabled=True,
            create_pack_enabled=True,
            transform_pack_enabled=True,
            checkpoint_v2_enabled=True,
            operation_pack_allowlist=frozenset(
                {"create.basic/1", "transform.basic/1"}
            ),
            rollout_policy_epoch=2,
        )
    )
    with pytest.raises(AgentExecutionError, match="capability_missing"):
        stale_policy.verify(
            plan,
            binding=binding,
            command_kind="program_preview",
            approval_binding=None,
            capability_states={"cad.op.copy.line.v1": "preview_only"},
            server_capability_evidence=evidence,
        )


@pytest.mark.parametrize("field", ["path", "command", "script", "raw_handle"])
def test_arbitrary_authority_fields_are_rejected(field: str) -> None:
    plan, binding, evidence = _plan()
    plan["operations"][0]["arguments"][field] = "attacker-controlled"
    with pytest.raises(AgentExecutionError, match="capability_missing"):
        _admission().verify(
            plan,
            binding=binding,
            command_kind="program_preview",
            approval_binding=None,
            capability_states={"cad.op.copy.line.v1": "preview_only"},
            server_capability_evidence=evidence,
        )


def test_transform_commit_requires_phase7_binding_and_checkpoint_v2() -> None:
    plan, binding, evidence = _plan(transform=True)
    states = {"cad.op.move.line.v1": "lab_commit"}
    with pytest.raises(AgentExecutionError, match="approval_required"):
        _admission().verify(
            plan,
            binding=binding,
            command_kind="program_commit",
            approval_binding=None,
            capability_states=states,
            server_capability_evidence=evidence,
            preview_id="preview-1",
            receipt_id="receipt-1",
        )

    verified = _admission().verify(
        plan,
        binding=binding,
        command_kind="program_commit",
        approval_binding=(approval := _approval(plan, binding)),
        capability_states=states,
        server_capability_evidence=evidence,
        preview_id="preview-1",
        receipt_id="receipt-1",
        idempotency_key=approval["effect_identity_digest"],
    )
    result = _admission().verify_result(
        verified,
        {
            "execution_plan_digest": plan["execution_plan_digest"],
            "effect_manifest_digest": plan["effect_manifest_digest"],
            "target_set_digest": plan["target_set_digest"],
            "hard_budget_digest": plan["hard_budget_digest"],
            "rollout_policy_digest": plan["pins"]["rollout_policy_digest"],
            "effect_identity_digest": approval["effect_identity_digest"],
            "milestone": "effect_and_receipt_committed",
            "checkpoint": {
                "schema_version": "cad.rollback.checkpoint/2",
                "digest": f"sha256:{'8' * 64}",
            },
        },
        command_kind="program_commit",
    )
    assert result["checkpoint"]["schema_version"] == "cad.rollback.checkpoint/2"

    with pytest.raises(AgentExecutionError, match="effect_identity_mismatch"):
        _admission().verify(
            plan,
            binding=binding,
            command_kind="program_commit",
            approval_binding=approval,
            capability_states=states,
            server_capability_evidence=evidence,
            preview_id="preview-1",
            receipt_id="receipt-1",
            idempotency_key="caller-chosen-key",
        )

    plan_v1, binding_v1, evidence_v1 = _plan(transform=True)
    plan_v1["checkpoint_strategy"] = {
        "schema_version": "cad.rollback.checkpoint/1",
        "strategy": "erase_created_entities",
    }
    plan_v1["checkpoint_strategy_digest"] = _digest(
        "cad.checkpoint-strategy/1",
        plan_v1["checkpoint_strategy"],
    )
    plan_v1["execution_plan_digest"] = _digest(
        "cad.execution-plan/1",
        {
            key: value
            for key, value in plan_v1.items()
            if key != "execution_plan_digest"
        },
    )
    binding_v1["execution_digest"] = plan_v1["execution_plan_digest"]
    approval_v1 = _approval(plan_v1, binding_v1)
    with pytest.raises(AgentExecutionError, match="checkpoint_mismatch"):
        _admission().verify(
            plan_v1,
            binding=binding_v1,
            command_kind="program_commit",
            approval_binding=approval_v1,
            capability_states=states,
            server_capability_evidence=evidence_v1,
            preview_id="preview-1",
            receipt_id="receipt-1",
            idempotency_key=approval_v1["effect_identity_digest"],
        )


@pytest.mark.parametrize("kind", ["delete", "trim", "fillet", "chamfer"])
def test_destructive_and_topology_operations_remain_disabled(kind: str) -> None:
    plan, binding, evidence = _plan(kind=kind)
    with pytest.raises(AgentExecutionError, match="capability_missing"):
        _admission().verify(
            plan,
            binding=binding,
            command_kind="program_preview",
            approval_binding=None,
            capability_states={"cad.op.copy.line.v1": "certified"},
            server_capability_evidence=evidence,
        )


def test_phase8_config_is_default_off_and_transform_requires_checkpoint() -> None:
    base = {
        "gateway_ws_url": "wss://gateway.example/agent/ws",
        "device_id": "device-1",
        "device_name": "Lab",
        "ledger_path": Path("agent.db"),
        "package_path": Path("package"),
        "package_sha256": "a" * 64,
    }
    assert AgentConfig(**base).validate().program_v1_source_enabled is False
    with pytest.raises(ValueError, match="checkpoint v2"):
        AgentConfig(
            **base,
            program_v1_source_enabled=True,
            program_v1_transform_pack_enabled=True,
            operation_pack_allowlist=frozenset({"transform.basic/1"}),
        ).validate()


async def test_program_and_rollback_share_one_document_write_lane() -> None:
    serializer = DocumentWriteSerializer()
    broker = SimpleNamespace()
    program = ProgramCommandExecutor(broker, write_serializer=serializer)
    rollback = RollbackCommandExecutor(broker, write_serializer=serializer)
    assert program.write_serializer is rollback.write_serializer

    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> None:
        async with program.write_serializer.acquire("doc-1"):
            entered.set()
            await release.wait()

    task = asyncio.create_task(hold())
    await entered.wait()
    with pytest.raises(AgentExecutionError, match="agent_busy"):
        async with rollback.write_serializer.acquire("doc-1"):
            pass
    async with rollback.write_serializer.acquire("doc-2"):
        pass
    release.set()
    await task


def test_managed_manifest_phase8_capability_filter_is_positive_allowlist() -> None:
    allowed = ManagedDotNetCadReadPort._phase8_capability_allowed
    assert allowed("cad.op.copy.line.v1")
    assert allowed("cad.op.move.circle.v1")
    assert allowed("cad.rollback.checkpoint.v2.lwpolyline")
    assert not allowed("cad.op.load_assembly.line.v1")
    assert not allowed("cad.op.delete.line.v1")
    assert not allowed("cad.op.trim.line.v1")
