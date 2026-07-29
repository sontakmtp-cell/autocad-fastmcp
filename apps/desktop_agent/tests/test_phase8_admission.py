from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from autocad_contracts import (
    Phase8ApprovalBinding,
    Phase8CapabilityEvidence,
    ProgramCommandMessage,
    build_execution_binding_v1,
    canonical_phase8_capability_evidence_digest,
    compile_cad_program_v1,
    program_command_payload_hash,
    seal_cad_program_v1,
)
from autocad_contracts.agent_protocol import canonical_json

from autocad_desktop_agent.config import AgentConfig
from autocad_desktop_agent.executor import AgentExecutionError
from autocad_desktop_agent.phase8_admission import (
    Phase8AdmissionPolicy,
    Phase8PlanAdmission,
    _host_timestamp,
)
from autocad_desktop_agent.program_executor import (
    DocumentWriteSerializer,
    ProgramCommandExecutor,
    RollbackCommandExecutor,
)
from autocad_desktop_agent.runtime.managed_dotnet import ManagedDotNetCadReadPort

_CAPABILITIES = (
    "cad.program.v1.compile",
    "cad.program.v1.execute.create",
)


def test_effect_identity_uses_managed_host_round_trip_timestamp() -> None:
    assert (
        _host_timestamp("2030-01-02T03:04:05.123456+00:00")
        == "2030-01-02T03:04:05.1234560+00:00"
    )


def _digest(domain: str, value: dict) -> str:
    encoded = canonical_json({"domain": domain, "payload": value}).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


def _literal(value_type: str, value: str, unit: str | None = None) -> dict:
    typed = {"type": value_type, "value": value}
    if unit is not None:
        typed["unit"] = unit
    return {"op": "literal", "value": typed}


def _point(x: str, y: str) -> dict:
    return {
        "x": _literal("length", x, "mm"),
        "y": _literal("length", y, "mm"),
        "z": _literal("length", "0", "mm"),
    }


def _rollout(*, entity_types: list[str] | None = None) -> dict:
    value = {
        "epoch": 1,
        "source_enabled": True,
        "create_pack_enabled": True,
        "transform_pack_enabled": False,
        "checkpoint_v2_enabled": False,
        "topology_pack_enabled": False,
        "delete_pack_enabled": False,
        "lt_write_enabled": False,
        "operation_pack_allowlist": ["create-equivalent-v1"],
        "cohort_id": "phase8-lab",
        "cohort_allowed": True,
        "runtime_allowlist": ["managed_dotnet:R25"],
        "entity_type_allowlist": entity_types or ["LAYER", "LINE", "CIRCLE"],
    }
    value["digest"] = _digest("cad.rollout-policy/1", value)
    return value


def _pins(rollout_digest: str) -> dict:
    return {
        "runtime_id": "managed_dotnet",
        "runtime_role": "primary",
        "host_family": "R25",
        "host_version": "1.0.0",
        "package_id": "autocad.managed_host.r25",
        "package_version": "1.0.0",
        "package_hash": f"sha256:{'1' * 64}",
        "capability_manifest_hash": f"sha256:{'2' * 64}",
        "operation_registry_version": "cad.program.1.create.core",
        "operation_registry_hash": f"sha256:{'3' * 64}",
        "policy_version": "phase8-lab-v1",
        "rollout_policy_digest": rollout_digest,
    }


def _source() -> dict:
    return {
        "schema_version": "cad.program/1.0",
        "registry_version": "cad.program/1.0-create-core",
        "program_id": "desktop-phase8",
        "program_revision": 1,
        "device_id": "device-1",
        "source_snapshot_id": "snapshot-1",
        "document_id": "doc-1",
        "expected_document_revision": "42",
        "variables": [],
        "operations": [
            {
                "kind": "create_line",
                "operation_id": "line-main",
                "layer": "PHASE8",
                "start": _point("0", "0"),
                "end": _point("100", "0"),
            },
        ],
        "budgets": {
            "max_source_operations": 8,
            "max_expanded_operations": 16,
            "max_entities": 16,
            "max_vertices": 64,
            "max_expression_nodes": 128,
            "max_coordinate_abs_mm": "10000",
            "max_text_bytes": 1024,
        },
        "required_capabilities": list(_CAPABILITIES),
        "validation_profiles": ["geometry.basic.1"],
        "artifact_refs": [],
        "component_refs": [],
    }


def _compiled() -> tuple[object, dict]:
    rollout = _rollout()
    source = seal_cad_program_v1(_source())
    plan = compile_cad_program_v1(
        source,
        _pins(rollout["digest"]),
        compiler_package_hash=f"sha256:{'7' * 64}",
    )
    return plan, rollout


def _transform_compiled() -> tuple[object, dict]:
    rollout = _rollout(entity_types=["LINE"])
    source = _source()
    source["registry_version"] = "cad.program/1.0-phase8-core"
    source["operations"] = [
        {
            "kind": "move_entity",
            "operation_id": "move-line",
            "target_ref_id": "ref-line-1",
            "displacement": _point("25", "0"),
        }
    ]
    source["required_capabilities"] = ["cad.program.v1.compile"]
    sealed = seal_cad_program_v1(source)
    plan = compile_cad_program_v1(
        sealed,
        _pins(rollout["digest"]),
        compiler_package_hash=f"sha256:{'7' * 64}",
        materialized_target_refs=[
            {
                "ref_id": "ref-line-1",
                "owner_id": "owner-1",
                "device_id": "device-1",
                "document_id": "doc-1",
                "snapshot_id": "snapshot-1",
                "document_revision": "42",
                "entity_id": "entity-line-1",
                "entity_type": "LINE",
                "fingerprint": f"sha256:{'d' * 64}",
            }
        ],
        materialized_owner_id="owner-1",
    )
    return plan, rollout


def _evidence(
    plan,
    rollout: dict,
    *,
    state: str,
    host_family: str = "R25",
    entity_types: list[str] | None = None,
    operation_pack: str = "create-equivalent-v1",
) -> list[Phase8CapabilityEvidence]:
    pins = plan.execution_pins
    del rollout
    issued = datetime.now(timezone.utc)
    results = []
    for index, key in enumerate(plan.required_capabilities):
        value = {
            "schema_version": "cad.capability-evidence/1",
            "evidence_authority": "gateway_server",
            "evidence_id": f"evidence-{index}",
            "device_id": plan.device_id,
            "capability_key": key,
            "operation_pack": operation_pack,
            "runtime_id": "managed_dotnet",
            "host_family": host_family,
            "entity_type": (entity_types or ["LINE"])[0],
            "support_state": state,
            "package_hash": pins.package_hash,
            "capability_manifest_hash": pins.capability_manifest_hash,
            "operation_registry_hash": pins.operation_registry_hash,
            "package_signature_verified": True,
            "agent_evidence_digest": f"sha256:{'a' * 64}",
            "host_evidence_digest": f"sha256:{'b' * 64}",
            "cohort": "phase8-lab",
            "evidence_version": "1",
            "issued_at": issued.isoformat(),
            "valid_until": (issued + timedelta(hours=1)).isoformat(),
        }
        value["evidence_digest"] = canonical_phase8_capability_evidence_digest(
            value
        )
        results.append(Phase8CapabilityEvidence.model_validate(value))
    return results


def _legacy_binding(plan) -> dict:
    pins = plan.execution_pins.model_dump(mode="json")
    pins.pop("rollout_policy_digest")
    return {
        "program_digest": plan.source_digest,
        "execution_digest": plan.execution_plan_digest,
        "document_id": plan.document_id,
        "document_revision": plan.expected_document_revision,
        **pins,
    }


def _admission() -> Phase8PlanAdmission:
    return Phase8PlanAdmission(
        Phase8AdmissionPolicy(
            source_enabled=True,
            create_pack_enabled=True,
            transform_pack_enabled=False,
            checkpoint_v2_enabled=False,
            operation_pack_allowlist=frozenset({"create-equivalent-v1"}),
            rollout_policy_epoch=1,
        )
    )


def _transform_admission() -> Phase8PlanAdmission:
    return Phase8PlanAdmission(
        Phase8AdmissionPolicy(
            source_enabled=True,
            create_pack_enabled=False,
            transform_pack_enabled=True,
            checkpoint_v2_enabled=True,
            operation_pack_allowlist=frozenset({"transform-exact-v1"}),
            rollout_policy_epoch=1,
        )
    )


def _preview_artifacts():
    plan, rollout = _compiled()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    binding = build_execution_binding_v1(
        plan,
        action="preview",
        preview_id="preview-1",
        preview_expires_at=expires_at,
    )
    evidence = _evidence(plan, rollout, state="preview_only")
    return plan, binding, evidence, expires_at


def _commit_artifacts():
    plan, rollout = _compiled()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    binding = build_execution_binding_v1(
        plan,
        action="commit",
        preview_id="preview-1",
        preview_expires_at=expires_at,
        receipt_id="receipt-1",
    )
    evidence = _evidence(plan, rollout, state="lab_commit")
    state_hash = _digest(
        "cad.capability-intersection/1",
        {
            "capability_states": {key: "lab_commit" for key in _CAPABILITIES}
        },
    )
    approval = {
        "schema_version": "cad.phase8-approval-binding/1",
        "action": "program_commit",
        "intent_id": "intent-1",
        "intent_digest": f"sha256:{'8' * 64}",
        "approval_proof_digest": f"sha256:{'9' * 64}",
        "consent_id": "consent-1",
        "device_id": plan.device_id,
        "document_id": plan.document_id,
        "document_revision": plan.expected_document_revision,
        "job_id": "job-1",
        "command_id": "command-1",
        "idempotency_key": "idem-1",
        "preview_id": binding.preview_id,
        "preview_digest": f"sha256:{'c' * 64}",
        "preview_expires_at": binding.preview_expires_at,
        "receipt_id": binding.receipt_id,
        "execution_binding_digest": binding.execution_binding_digest,
        "source_digest": plan.source_digest,
        "expansion_digest": plan.expansion_digest,
        "effect_manifest_digest": plan.effect_manifest_digest,
        "execution_plan_digest": plan.execution_plan_digest,
        "target_refs_digest": plan.target_refs_digest,
        "validation_profiles_digest": plan.validation_profiles_digest,
        "checkpoint_strategy_digest": plan.checkpoint_strategy_digest,
        "hard_budgets_digest": plan.hard_budgets_digest,
    }
    del state_hash
    return plan, binding, evidence, Phase8ApprovalBinding.model_validate(approval)


def test_real_compiler_preview_is_verified_and_only_sealed_plan_is_dispatched() -> None:
    plan, binding, evidence, expires_at = _preview_artifacts()
    verified = _admission().verify(
        plan,
        binding=binding,
        legacy_binding=_legacy_binding(plan),
        command_kind="program_preview",
        approval_binding=None,
        capability_states={key: "preview_only" for key in _CAPABILITIES},
        server_capability_evidence=evidence,
        device_id="device-1",
        preview_id="preview-1",
        preview_expires_at=expires_at,
    )

    arguments = ProgramCommandExecutor._host_arguments(
        SimpleNamespace(),
        verified,
    )
    assert arguments == {
        "execution_plan": plan.model_dump(mode="json", exclude_none=True),
        "capability_evidence": [
            item.model_dump(mode="json", exclude_none=True)
            for item in evidence
        ],
    }
    assert "program" not in arguments
    assert "execution_binding" not in arguments

    result = _admission().verify_result(
        verified,
        {
            "execution_plan_digest": plan.execution_plan_digest,
            "effect_manifest_digest": plan.effect_manifest_digest,
            "target_refs_digest": plan.target_refs_digest,
            "hard_budgets_digest": plan.hard_budgets_digest,
            "rollout_policy_digest": plan.execution_pins.rollout_policy_digest,
            "transaction_aborted": True,
            "drawing_unchanged": True,
        },
        command_kind="program_preview",
    )
    assert result["drawing_unchanged"] is True


def test_non_entity_compile_evidence_accepts_the_all_scope() -> None:
    plan, binding, evidence, expires_at = _preview_artifacts()
    values = [item.model_dump(mode="json") for item in evidence]
    compile_value = next(
        item
        for item in values
        if item["capability_key"] == "cad.program.v1.compile"
    )
    compile_value["entity_type"] = "ALL"
    compile_value["evidence_digest"] = canonical_phase8_capability_evidence_digest(
        compile_value
    )

    verified = _admission().verify(
        plan,
        binding=binding,
        command_kind="program_preview",
        approval_binding=None,
        capability_states={key: "preview_only" for key in _CAPABILITIES},
        server_capability_evidence=values,
        device_id="device-1",
        preview_id="preview-1",
        preview_expires_at=expires_at,
    )

    assert any(
        item["capability_key"] == "cad.program.v1.compile"
        and item["entity_type"] == "ALL"
        for item in verified.capability_evidence
    )


async def test_program_executor_accepts_typed_phase8_command_and_dispatches_no_source() -> None:
    plan, binding, evidence, expires_at = _preview_artifacts()
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    command = ProgramCommandMessage(
        session_id="session-1",
        device_id="device-1",
        job_id="job-1",
        command_id="command-1",
        idempotency_key="preview-idem-1",
        payload_hash="0" * 64,
        kind="program_preview",
        effect_class="write",
        binding=binding,
        execution_plan=plan,
        capability_evidence=evidence,
        preview_id="preview-1",
        expires_at=expires_at,
        deadline_at=deadline,
    )
    command = command.model_copy(
        update={"payload_hash": program_command_payload_hash(command)}
    )
    host_result = {
        "execution_plan_digest": plan.execution_plan_digest,
        "effect_manifest_digest": plan.effect_manifest_digest,
        "target_refs_digest": plan.target_refs_digest,
        "hard_budgets_digest": plan.hard_budgets_digest,
        "rollout_policy_digest": plan.execution_pins.rollout_policy_digest,
        "transaction_aborted": True,
        "drawing_unchanged": True,
    }

    class Adapter:
        arguments = None

        async def health(self):
            return SimpleNamespace(
                ok=True,
                payload={
                    "active_document_id": "doc-1",
                    "active_document_revision": "42",
                },
                error_code=None,
            )

        async def program_command(self, kind, *, arguments, deadline_at):
            assert kind == "program_preview"
            assert deadline_at == deadline
            self.arguments = arguments
            return SimpleNamespace(ok=True, payload=host_result, error_code=None)

    adapter = Adapter()

    class Broker:
        async def select_write_runtime(self, binding, **kwargs):
            assert isinstance(binding, type(command.binding))
            assert kwargs["required_capability"] == "cad.program.preview"
            return SimpleNamespace(
                adapter=adapter,
                capability_states={
                    key: "preview_only" for key in _CAPABILITIES
                },
            )

    result = await ProgramCommandExecutor(
        Broker(),
        phase8_admission=_admission(),
    ).execute(command, write_lock_enabled=True)
    assert result["drawing_unchanged"] is True
    assert adapter.arguments == {
        "execution_plan": plan.model_dump(mode="json", exclude_none=True),
        "capability_evidence": [
            item.model_dump(mode="json", exclude_none=True)
            for item in evidence
        ],
    }
    assert "program" not in adapter.arguments
    assert "execution_binding" not in adapter.arguments


def test_exact_move_commit_requires_transform_gate_checkpoint_v2_and_exact_tuple() -> None:
    plan, rollout = _transform_compiled()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    binding = build_execution_binding_v1(
        plan,
        action="commit",
        preview_id="preview-move-1",
        preview_expires_at=expires_at,
        receipt_id="receipt-move-1",
    )
    evidence = _evidence(
        plan,
        rollout,
        state="lab_commit",
        operation_pack="transform-exact-v1",
    )
    states = {key: "lab_commit" for key in plan.required_capabilities}
    approval = Phase8ApprovalBinding(
        schema_version="cad.phase8-approval-binding/1",
        action="program_commit",
        intent_id="intent-move-1",
        consent_id="consent-move-1",
        intent_digest=f"sha256:{'8' * 64}",
        approval_proof_digest=f"sha256:{'9' * 64}",
        device_id="device-1",
        document_id="doc-1",
        document_revision="42",
        job_id="job-move-1",
        command_id="command-move-1",
        idempotency_key="idem-move-1",
        source_digest=plan.source_digest,
        execution_plan_digest=plan.execution_plan_digest,
        execution_binding_digest=binding.execution_binding_digest,
        expansion_digest=plan.expansion_digest,
        effect_manifest_digest=plan.effect_manifest_digest,
        target_refs_digest=plan.target_refs_digest,
        validation_profiles_digest=plan.validation_profiles_digest,
        checkpoint_strategy_digest=plan.checkpoint_strategy_digest,
        hard_budgets_digest=plan.hard_budgets_digest,
        preview_id="preview-move-1",
        preview_digest=f"sha256:{'c' * 64}",
        preview_expires_at=expires_at,
        receipt_id="receipt-move-1",
    )
    common = {
        "plan": plan,
        "binding": binding,
        "command_kind": "program_commit",
        "approval_binding": approval,
        "capability_states": states,
        "server_capability_evidence": evidence,
        "device_id": "device-1",
        "job_id": "job-move-1",
        "command_id": "command-move-1",
        "preview_id": "preview-move-1",
        "preview_digest": f"sha256:{'c' * 64}",
        "preview_expires_at": expires_at,
        "receipt_id": "receipt-move-1",
        "idempotency_key": "idem-move-1",
    }

    verified = _transform_admission().verify(**common)
    assert verified.modifies_entities is True
    assert plan.checkpoint_strategy == "cad.rollback.checkpoint/2"
    assert "cad.op.move.line.v1" in verified.required_capabilities
    assert verified.operation_packs == ("transform-exact-v1",)

    with pytest.raises(AgentExecutionError, match="checkpoint_mismatch"):
        _admission().verify(**common)

    wrong_tuple = _evidence(
        plan,
        rollout,
        state="lab_commit",
        entity_types=["CIRCLE"],
        operation_pack="transform-exact-v1",
    )
    with pytest.raises(AgentExecutionError, match="capability_missing"):
        _transform_admission().verify(
            **{**common, "server_capability_evidence": wrong_tuple}
        )

    result = _transform_admission().verify_result(
        verified,
        {
            "execution_plan_digest": plan.execution_plan_digest,
            "effect_manifest_digest": plan.effect_manifest_digest,
            "target_refs_digest": plan.target_refs_digest,
            "hard_budgets_digest": plan.hard_budgets_digest,
            "rollout_policy_digest": plan.execution_pins.rollout_policy_digest,
            "effect_identity_digest": verified.effect_identity_digest,
            "milestone": "effect_and_receipt_committed",
            "checkpoint": {
                "schema_version": "cad.rollback.checkpoint/2",
                "digest": f"sha256:{'e' * 64}",
            },
        },
        command_kind="program_commit",
    )
    assert result["checkpoint"]["schema_version"] == "cad.rollback.checkpoint/2"


@pytest.mark.parametrize(
    ("target", "code"),
    [
        ("plan", "plan_mismatch"),
        ("binding", "binding_mismatch"),
    ],
)
def test_real_compiler_artifact_tamper_fails_before_dispatch(
    target: str,
    code: str,
) -> None:
    plan, binding, evidence, expires_at = _preview_artifacts()
    plan_value = plan.model_dump(mode="json", exclude_none=True)
    binding_value = binding.model_dump(mode="json", exclude_none=True)
    if target == "plan":
        plan_value["operations"][0]["end"]["x_mm"] = "101"
    else:
        binding_value["package_hash"] = f"sha256:{'f' * 64}"

    with pytest.raises(AgentExecutionError, match=code):
        _admission().verify(
            plan_value,
            binding=binding_value,
            command_kind="program_preview",
            approval_binding=None,
            capability_states={key: "preview_only" for key in _CAPABILITIES},
            server_capability_evidence=evidence,
            device_id="device-1",
            preview_id="preview-1",
            preview_expires_at=expires_at,
        )


def test_unsupported_runtime_entity_tuple_and_optimistic_self_report_fail_closed() -> None:
    plan, binding, _, expires_at = _preview_artifacts()
    bad_rollout = _rollout()
    bad_plan = compile_cad_program_v1(
        seal_cad_program_v1(_source()),
        _pins(bad_rollout["digest"]),
        compiler_package_hash=f"sha256:{'7' * 64}",
    )
    bad_binding = build_execution_binding_v1(
        bad_plan,
        action="preview",
        preview_id="preview-1",
        preview_expires_at=expires_at,
    )
    bad_evidence = _evidence(
        bad_plan,
        bad_rollout,
        state="certified",
        host_family="LT",
        entity_types=["LAYER", "CIRCLE"],
    )
    with pytest.raises(AgentExecutionError, match="capability_missing"):
        _admission().verify(
            bad_plan,
            binding=bad_binding,
            command_kind="program_preview",
            approval_binding=None,
            capability_states={key: "certified" for key in _CAPABILITIES},
            server_capability_evidence=bad_evidence,
            device_id="device-1",
            preview_id="preview-1",
            preview_expires_at=expires_at,
        )

    _, rollout = _compiled()
    evidence = _evidence(plan, rollout, state="contract_only")
    with pytest.raises(AgentExecutionError, match="capability_missing"):
        _admission().verify(
            plan,
            binding=binding,
            command_kind="program_preview",
            approval_binding=None,
            capability_states={key: "certified" for key in _CAPABILITIES},
            server_capability_evidence=evidence,
            device_id="device-1",
            preview_id="preview-1",
            preview_expires_at=expires_at,
        )


def test_commit_requires_exact_phase7_binding_effect_identity_and_replay_key() -> None:
    plan, binding, evidence, approval = _commit_artifacts()
    common = {
        "plan": plan,
        "binding": binding,
        "legacy_binding": _legacy_binding(plan),
        "command_kind": "program_commit",
        "capability_states": {key: "lab_commit" for key in _CAPABILITIES},
        "server_capability_evidence": evidence,
        "device_id": "device-1",
        "job_id": "job-1",
        "command_id": "command-1",
        "preview_id": "preview-1",
        "preview_digest": f"sha256:{'c' * 64}",
        "preview_expires_at": binding.preview_expires_at,
        "receipt_id": "receipt-1",
        "idempotency_key": "idem-1",
    }
    with pytest.raises(AgentExecutionError, match="approval_required"):
        _admission().verify(approval_binding=None, **common)

    verified = _admission().verify(
        approval_binding=approval,
        **common,
    )
    assert verified.effect_identity_digest is not None
    assert verified.host_arguments() == {
        "execution_plan": plan.model_dump(mode="json", exclude_none=True),
        "approval_binding": approval.model_dump(mode="json"),
        "capability_evidence": [
            item.model_dump(mode="json", exclude_none=True)
            for item in evidence
        ],
    }

    changed_key = dict(common)
    changed_key["idempotency_key"] = "idem-replayed"
    with pytest.raises(AgentExecutionError, match="approval_binding_mismatch"):
        _admission().verify(
            approval_binding=approval,
            **changed_key,
        )

    replayed = approval.model_dump(mode="json")
    replayed["receipt_id"] = "receipt-replayed"
    with pytest.raises(AgentExecutionError, match="approval_binding_mismatch"):
        _admission().verify(
            approval_binding=replayed,
            **common,
        )

    result = _admission().verify_result(
        verified,
        {
            "execution_plan_digest": plan.execution_plan_digest,
            "effect_manifest_digest": plan.effect_manifest_digest,
            "target_refs_digest": plan.target_refs_digest,
            "hard_budgets_digest": plan.hard_budgets_digest,
            "rollout_policy_digest": plan.execution_pins.rollout_policy_digest,
            "effect_identity_digest": verified.effect_identity_digest,
            "milestone": "effect_and_receipt_committed",
        },
        command_kind="program_commit",
    )
    assert result["milestone"] == "effect_and_receipt_committed"


def test_runtime_pin_or_stale_rollout_evidence_cannot_be_substituted() -> None:
    plan, binding, evidence, expires_at = _preview_artifacts()
    legacy = _legacy_binding(plan)
    legacy["package_hash"] = f"sha256:{'e' * 64}"
    with pytest.raises(AgentExecutionError, match="package_mismatch"):
        _admission().verify(
            plan,
            binding=binding,
            legacy_binding=legacy,
            command_kind="program_preview",
            approval_binding=None,
            capability_states={key: "preview_only" for key in _CAPABILITIES},
            server_capability_evidence=evidence,
            device_id="device-1",
            preview_id="preview-1",
            preview_expires_at=expires_at,
        )

    stale = [item.model_dump(mode="json") for item in evidence]
    issued = datetime.now(timezone.utc) - timedelta(hours=2)
    for item in stale:
        item["issued_at"] = issued.isoformat()
        item["valid_until"] = (issued + timedelta(hours=1)).isoformat()
        item["evidence_digest"] = canonical_phase8_capability_evidence_digest(
            item
        )
    with pytest.raises(AgentExecutionError, match="capability_missing"):
        _admission().verify(
            plan,
            binding=binding,
            command_kind="program_preview",
            approval_binding=None,
            capability_states={key: "preview_only" for key in _CAPABILITIES},
            server_capability_evidence=stale,
            device_id="device-1",
            preview_id="preview-1",
            preview_expires_at=expires_at,
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
    assert allowed("cad.program.v1.compile")
    assert allowed("cad.op.copy.line.v1")
    assert allowed("cad.op.move.circle.v1")
    assert allowed("cad.rollback.checkpoint.v2.lwpolyline")
    assert not allowed("cad.op.load_assembly.line.v1")
    assert not allowed("cad.op.delete.line.v1")
    assert not allowed("cad.op.trim.line.v1")
