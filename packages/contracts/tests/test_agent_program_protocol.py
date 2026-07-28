from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    ProgramCommandMessage,
    ProgramResultMessage,
    agent_program_command_json_schema,
    agent_program_result_json_schema,
    agent_rollback_json_schema,
    canonical_payload_hash,
    canonical_phase8_capability_evidence_digest,
    canonical_preview_digest,
    canonical_program_digest,
    canonical_receipt_id,
    build_execution_binding_v1,
    compile_cad_program_v1,
    message_dict,
    operation_registry_digest,
    parse_agent_message,
    program_command_payload,
    program_command_payload_hash,
    seal_cad_program_v1,
)

from test_cad_program_v02 import complete_program
from test_phase8_contracts import compiler_package_hash, pins, source_payload


ROOT = Path(__file__).parents[1]


def binding() -> dict:
    return {
        "program_digest": canonical_program_digest(complete_program()),
        "execution_digest": "sha256:" + "1" * 64,
        "document_id": "document-001",
        "document_revision": "revision-007",
        "runtime_id": "managed_dotnet",
        "runtime_role": "primary",
        "host_family": "R25",
        "host_version": "0.2.0",
        "package_id": "autocad.managed_host.r25",
        "package_version": "0.2.0",
        "package_hash": "sha256:" + "2" * 64,
        "capability_manifest_hash": "sha256:" + "3" * 64,
        "operation_registry_version": "cad.program/0.2",
        "operation_registry_hash": operation_registry_digest(),
        "policy_version": "phase6-policy/1",
    }


def command(kind: str) -> dict:
    value = {
        "protocol_version": "cad.agent/2",
        "message_type": "command",
        "message_id": "message-001",
        "session_id": "session-001",
        "device_id": "device-001",
        "job_id": "job-001",
        "command_id": "command-001",
        "sequence": 1,
        "issued_at": "2026-07-26T01:00:00+00:00",
        "deadline_at": "2026-07-26T01:01:00+00:00",
        "idempotency_key": "idempotency-001",
        "payload_hash": "0" * 64,
        "kind": kind,
        "effect_class": "read" if kind == "program_validate" else "write",
        "binding": binding(),
    }
    if kind in {"program_preview", "program_commit"}:
        value["program"] = complete_program()
        value["preview_id"] = "preview-001"
    if kind == "program_preview":
        value["expires_at"] = "2026-07-26T01:15:00+00:00"
    if kind == "program_commit":
        value["preview_digest"] = "sha256:" + "5" * 64
        value["receipt_id"] = "AUTOCAD_MCP_PROGRAM_" + "6" * 32
    if kind == "program_validate":
        value["validation"] = {
            "validation_id": "validation-001",
            "receipt_id": "receipt-001",
            "expected_entity_count": 6,
            "expected_entity_types": ["LINE", "CIRCLE"],
            "expected_layers": ["MCP-PHASE6"],
        }
    return value


def phase8_capability_evidence(plan) -> list[dict]:
    values = []
    for index, capability in enumerate(plan.required_capabilities, start=1):
        value = {
            "schema_version": "cad.capability-evidence/1",
            "evidence_id": f"evidence-{index}",
            "evidence_authority": "gateway_server",
            "device_id": plan.device_id,
            "capability_key": capability,
            "operation_pack": "create.core/1",
            "runtime_id": plan.execution_pins.runtime_id,
            "host_family": plan.execution_pins.host_family,
            "entity_type": "ALL",
            "support_state": "lab_commit",
            "package_hash": plan.execution_pins.package_hash,
            "capability_manifest_hash": plan.execution_pins.capability_manifest_hash,
            "operation_registry_hash": plan.execution_pins.operation_registry_hash,
            "package_signature_verified": True,
            "agent_evidence_digest": "sha256:" + "8" * 64,
            "host_evidence_digest": "sha256:" + "9" * 64,
            "cohort": "phase8-lab",
            "evidence_version": "phase8.evidence.1",
            "issued_at": "2026-07-26T00:55:00+00:00",
            "valid_until": "2026-07-26T02:00:00+00:00",
        }
        value["evidence_digest"] = canonical_phase8_capability_evidence_digest(value)
        values.append(value)
    return values


def phase8_command(kind: str) -> dict:
    plan = compile_cad_program_v1(
        seal_cad_program_v1(source_payload()),
        pins(),
        compiler_package_hash=compiler_package_hash(),
    )
    expires_at = "2026-07-26T01:15:00+00:00"
    receipt_id = "AUTOCAD_MCP_PROGRAM_" + "6" * 32
    action = "preview" if kind == "program_preview" else "commit"
    binding = build_execution_binding_v1(
        plan,
        action=action,
        preview_id="preview-phase8-001",
        preview_expires_at=expires_at,
        receipt_id=receipt_id if action == "commit" else None,
    )
    value = {
        "protocol_version": "cad.agent/2",
        "message_type": "command",
        "message_id": "message-phase8-001",
        "session_id": "session-001",
        "device_id": plan.device_id,
        "job_id": "job-phase8-001",
        "command_id": "command-phase8-001",
        "sequence": 1,
        "issued_at": "2026-07-26T01:00:00+00:00",
        "deadline_at": "2026-07-26T01:01:00+00:00",
        "idempotency_key": "idempotency-phase8-001",
        "payload_hash": "0" * 64,
        "kind": kind,
        "effect_class": "write",
        "binding": binding.model_dump(mode="json"),
        "execution_plan": plan.model_dump(mode="json"),
        "capability_evidence": phase8_capability_evidence(plan),
        "preview_id": "preview-phase8-001",
        "expires_at": expires_at,
    }
    if kind == "program_commit":
        value["preview_digest"] = "sha256:" + "5" * 64
        value["receipt_id"] = receipt_id
        value["approval_binding"] = {
            "schema_version": "cad.phase8-approval-binding/1",
            "action": "program_commit",
            "intent_id": "intent-phase8-001",
            "consent_id": "consent-phase8-001",
            "intent_digest": "sha256:" + "a" * 64,
            "approval_proof_digest": "sha256:" + "b" * 64,
            "device_id": plan.device_id,
            "document_id": plan.document_id,
            "document_revision": plan.expected_document_revision,
            "job_id": value["job_id"],
            "command_id": value["command_id"],
            "idempotency_key": value["idempotency_key"],
            "source_digest": plan.source_digest,
            "execution_plan_digest": plan.execution_plan_digest,
            "execution_binding_digest": binding.execution_binding_digest,
            "expansion_digest": plan.expansion_digest,
            "effect_manifest_digest": plan.effect_manifest_digest,
            "target_refs_digest": plan.target_refs_digest,
            "validation_profiles_digest": plan.validation_profiles_digest,
            "checkpoint_strategy_digest": plan.checkpoint_strategy_digest,
            "hard_budgets_digest": plan.hard_budgets_digest,
            "preview_id": value["preview_id"],
            "preview_digest": value["preview_digest"],
            "preview_expires_at": expires_at,
            "receipt_id": receipt_id,
        }
    return value


@pytest.mark.parametrize("kind", ["program_preview", "program_commit", "program_validate"])
def test_typed_program_commands_round_trip(kind):
    parsed = parse_agent_message(command(kind))
    assert isinstance(parsed, ProgramCommandMessage)
    assert parsed.kind == kind
    assert message_dict(parsed)["binding"]["host_family"] == "R25"


@pytest.mark.parametrize("kind", ["program_preview", "program_commit", "program_validate"])
def test_program_command_payload_projection_has_model_wire_and_hash_parity(kind):
    expected_hashes = {
        "program_preview": "3cad8aa251b71658020bda9ee7447bed7958e9093bf771514290eb42995cb30f",
        "program_commit": "8a95cb1e0cb748fef60efbf822fcdac1ce6540320a1e8d27ec1abc588af79f2d",
        "program_validate": "644ac91b9b06052af52cfc1cea81846ad4f00dfe1093a6314463a42656b0eb58",
    }
    parsed = ProgramCommandMessage.model_validate(command(kind))
    wire = message_dict(parsed)
    projection = program_command_payload(parsed)
    assert program_command_payload(wire) == projection
    assert program_command_payload_hash(parsed) == program_command_payload_hash(wire)
    assert program_command_payload_hash(parsed) == canonical_payload_hash(projection)
    assert program_command_payload_hash(parsed) == expected_hashes[kind]
    assert "payload_hash" not in projection
    assert "message_id" not in projection


def test_program_command_requires_exact_semantic_binding():
    wrong_document = command("program_preview")
    wrong_document["binding"]["document_id"] = "another-document"
    with pytest.raises(ValidationError, match="document_id does not match"):
        ProgramCommandMessage.model_validate(wrong_document)

    wrong_digest = command("program_preview")
    wrong_digest["binding"]["program_digest"] = "sha256:" + "f" * 64
    with pytest.raises(ValidationError, match="program_digest does not match"):
        ProgramCommandMessage.model_validate(wrong_digest)

    wrong_effect = command("program_validate")
    wrong_effect["effect_class"] = "write"
    with pytest.raises(ValidationError, match="validate requires read effect"):
        ProgramCommandMessage.model_validate(wrong_effect)


def test_preview_digest_and_receipt_id_match_managed_host_golden_vector():
    assert canonical_preview_digest("preview-001", binding()) == (
        "sha256:85da3cf8f778c421b242cb37ccaf2d326ac46e0dc92720b749dc1272da7e7c91"
    )
    assert canonical_receipt_id("preview-001") == (
        "AUTOCAD_MCP_PROGRAM_e3e78279e01c532929adc6d8515a6b83"
    )


def test_commit_requires_preview_and_preview_rejects_client_preview_fields():
    missing = command("program_commit")
    missing.pop("preview_digest")
    with pytest.raises(
        ValidationError,
        match="commit requires exact preview and receipt binding",
    ):
        ProgramCommandMessage.model_validate(missing)

    injected = command("program_preview")
    injected["preview_digest"] = "sha256:" + "5" * 64
    with pytest.raises(
        ValidationError,
        match="preview requires its exact ID and expiry",
    ):
        ProgramCommandMessage.model_validate(injected)

    injected_null = command("program_preview")
    injected_null["preview_id"] = None
    with pytest.raises(
        ValidationError,
        match="preview requires its exact ID and expiry",
    ):
        ProgramCommandMessage.model_validate(injected_null)

    missing_expiry = command("program_preview")
    missing_expiry.pop("expires_at")
    with pytest.raises(
        ValidationError,
        match="preview requires its exact ID and expiry",
    ):
        ProgramCommandMessage.model_validate(missing_expiry)

    for kind in ("program_commit", "program_validate"):
        injected_expiry = command(kind)
        injected_expiry["expires_at"] = "2026-07-26T01:15:00+00:00"
        with pytest.raises(ValidationError):
            ProgramCommandMessage.model_validate(injected_expiry)


def test_preview_expiry_is_canonical_and_covered_by_payload_hash():
    value = command("program_preview")
    value["expires_at"] = "2026-07-26T08:15:00+07:00"
    parsed = ProgramCommandMessage.model_validate(value)
    assert parsed.expires_at == "2026-07-26T01:15:00+00:00"
    original_hash = program_command_payload_hash(parsed)
    changed = parsed.model_copy(update={"expires_at": "2026-07-26T01:15:01+00:00"})
    assert program_command_payload_hash(changed) != original_hash


@pytest.mark.parametrize("kind", ["program_preview", "program_commit"])
def test_phase8_command_carries_only_canonical_plan_and_binding(kind):
    parsed = ProgramCommandMessage.model_validate(phase8_command(kind))
    projection = program_command_payload(parsed)

    assert parsed.binding.schema_version == "cad.execution-binding/1"
    assert parsed.execution_plan.schema_version == "cad.execution-plan/1"
    assert "program" not in projection
    assert projection["execution_plan"] == parsed.execution_plan.model_dump(
        mode="json",
        exclude_none=True,
    )
    assert projection["binding"] == parsed.binding.model_dump(
        mode="json",
        exclude_none=True,
    )
    assert projection["capability_evidence"]
    assert program_command_payload_hash(parsed) == canonical_payload_hash(projection)
    if kind == "program_commit":
        assert projection["approval_binding"]["job_id"] == parsed.job_id
    else:
        assert "approval_binding" not in projection


def test_phase8_and_legacy_modes_cannot_be_mixed():
    phase8 = phase8_command("program_preview")
    phase8["program"] = complete_program()
    with pytest.raises(ValidationError, match="cannot carry source program"):
        ProgramCommandMessage.model_validate(phase8)

    legacy = command("program_preview")
    legacy["execution_plan"] = phase8_command("program_preview")["execution_plan"]
    with pytest.raises(ValidationError, match="cannot be mixed"):
        ProgramCommandMessage.model_validate(legacy)


def test_phase8_requires_untampered_plan_and_binding():
    missing_plan = phase8_command("program_preview")
    missing_plan.pop("execution_plan")
    with pytest.raises(ValidationError, match="requires sealed execution plan"):
        ProgramCommandMessage.model_validate(missing_plan)

    tampered_plan = phase8_command("program_preview")
    tampered_plan["execution_plan"]["execution_plan_digest"] = "sha256:" + "f" * 64
    with pytest.raises(ValidationError, match="execution plan digest"):
        ProgramCommandMessage.model_validate(tampered_plan)

    tampered_binding = phase8_command("program_preview")
    tampered_binding["binding"]["effect_manifest_digest"] = "sha256:" + "e" * 64
    with pytest.raises(ValidationError, match="execution binding digest"):
        ProgramCommandMessage.model_validate(tampered_binding)


@pytest.mark.parametrize(
    ("target", "field"),
    [
        ("execution_plan", "source_program"),
        ("binding", "program"),
        ("approval_binding", "signature_algorithm"),
        ("capability_evidence", "server_payload"),
    ],
)
def test_phase8_nested_contracts_reject_extra_fields(target, field):
    value = phase8_command("program_commit")
    if target == "capability_evidence":
        value[target][0][field] = "unexpected"
    else:
        value[target][field] = "unexpected"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProgramCommandMessage.model_validate(value)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("job_id", "job-replayed"),
        ("command_id", "command-replayed"),
        ("idempotency_key", "idempotency-replayed"),
        ("preview_id", "preview-replayed"),
        ("receipt_id", "receipt-replayed"),
    ],
)
def test_phase8_commit_rejects_replayed_approval_identity(field, replacement):
    value = phase8_command("program_commit")
    value["approval_binding"][field] = replacement
    with pytest.raises(ValidationError, match="approval binding does not match"):
        ProgramCommandMessage.model_validate(value)


def test_phase8_command_rejects_action_and_capability_evidence_mismatch():
    wrong_action = phase8_command("program_preview")
    wrong_action["kind"] = "program_commit"
    wrong_action["preview_digest"] = "sha256:" + "5" * 64
    wrong_action["receipt_id"] = "receipt-phase8-001"
    wrong_action["approval_binding"] = phase8_command("program_commit")[
        "approval_binding"
    ]
    with pytest.raises(ValidationError, match="requested action"):
        ProgramCommandMessage.model_validate(wrong_action)

    wrong_device = phase8_command("program_preview")
    wrong_device["capability_evidence"][0]["device_id"] = "device-replayed"
    wrong_device["capability_evidence"][0]["evidence_digest"] = (
        canonical_phase8_capability_evidence_digest(
            wrong_device["capability_evidence"][0]
        )
    )
    with pytest.raises(ValidationError, match="does not match sealed plan"):
        ProgramCommandMessage.model_validate(wrong_device)


def test_phase8_payload_hash_covers_approval_and_capability_evidence():
    original = phase8_command("program_commit")
    parsed = ProgramCommandMessage.model_validate(original)
    original_hash = program_command_payload_hash(parsed)

    changed_approval = deepcopy(original)
    changed_approval["approval_binding"]["approval_proof_digest"] = (
        "sha256:" + "c" * 64
    )
    assert (
        program_command_payload_hash(
            ProgramCommandMessage.model_validate(changed_approval)
        )
        != original_hash
    )

    changed_evidence = deepcopy(original)
    changed_evidence["capability_evidence"][0]["agent_evidence_digest"] = (
        "sha256:" + "d" * 64
    )
    changed_evidence["capability_evidence"][0]["evidence_digest"] = (
        canonical_phase8_capability_evidence_digest(
            changed_evidence["capability_evidence"][0]
        )
    )
    assert (
        program_command_payload_hash(
            ProgramCommandMessage.model_validate(changed_evidence)
        )
        != original_hash
    )


@pytest.mark.parametrize(
    ("kind", "result"),
    [
        (
            "program_preview",
            {
                "preview_id": "preview-001",
                "preview_digest": "sha256:" + "5" * 64,
                "expires_at": "2026-07-26T01:15:00+00:00",
                "planned_operation_count": 7,
                "planned_entity_count": 6,
                "planned_layer_count": 1,
                "transaction_aborted": True,
                "drawing_unchanged": True,
            },
        ),
        (
            "program_commit",
            {
                "receipt_id": "receipt-001",
                "receipt_digest": "sha256:" + "6" * 64,
                "document_revision_before": "revision-007",
                "document_revision_after": "revision-008",
                "created_entity_count": 6,
                "duplicate": False,
            },
        ),
        (
            "program_validate",
            {
                "validation_id": "validation-001",
                "valid": True,
                "document_revision": "revision-008",
                "checks": ["entity_count", "layers"],
                "failures": [],
            },
        ),
    ],
)
def test_typed_program_results_round_trip(kind, result):
    value = {
        "protocol_version": "cad.agent/2",
        "message_type": "result",
        "message_id": "message-result-001",
        "session_id": "session-001",
        "device_id": "device-001",
        "job_id": "job-001",
        "command_id": "command-001",
        "sequence": 2,
        "issued_at": "2026-07-26T01:00:30+00:00",
        "kind": kind,
        "status": "succeeded",
        "payload_hash": canonical_payload_hash(result),
        "binding": binding(),
        "result": result,
    }
    parsed = parse_agent_message(value)
    assert isinstance(parsed, ProgramResultMessage)
    assert parsed.kind == kind


def test_only_commit_can_report_outcome_unknown():
    value = {
        "protocol_version": "cad.agent/2",
        "message_type": "result",
        "message_id": "message-result-001",
        "session_id": "session-001",
        "device_id": "device-001",
        "job_id": "job-001",
        "command_id": "command-001",
        "sequence": 2,
        "issued_at": "2026-07-26T01:00:30+00:00",
        "kind": "program_preview",
        "status": "outcome_unknown",
        "payload_hash": "0" * 64,
        "binding": binding(),
    }
    with pytest.raises(ValidationError, match="only commit"):
        ProgramResultMessage.model_validate(value)


@pytest.mark.parametrize(
    ("filename", "generated"),
    [
        ("cad-agent-2-program-command.schema.json", agent_program_command_json_schema),
        ("cad-agent-2-program-result.schema.json", agent_program_result_json_schema),
        ("cad-agent-2-rollback.schema.json", agent_rollback_json_schema),
    ],
)
def test_agent_program_schema_snapshots_are_current(filename, generated):
    snapshot = json.loads((ROOT / "schemas" / filename).read_text(encoding="utf-8"))
    assert snapshot == generated()
    assert snapshot["$schema"].endswith("2020-12/schema")
    if filename == "cad-agent-2-rollback.schema.json":
        assert snapshot["oneOf"]
    else:
        assert snapshot["additionalProperties"] is False
        assert snapshot["allOf"]
