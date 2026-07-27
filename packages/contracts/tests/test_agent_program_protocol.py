from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    ProgramCommandMessage,
    ProgramResultMessage,
    agent_program_command_json_schema,
    agent_program_result_json_schema,
    canonical_payload_hash,
    canonical_preview_digest,
    canonical_program_digest,
    canonical_receipt_id,
    message_dict,
    operation_registry_digest,
    parse_agent_message,
    program_command_payload,
    program_command_payload_hash,
)

from test_cad_program_v02 import complete_program


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


@pytest.mark.parametrize("kind", ["program_preview", "program_commit", "program_validate"])
def test_typed_program_commands_round_trip(kind):
    parsed = parse_agent_message(command(kind))
    assert isinstance(parsed, ProgramCommandMessage)
    assert parsed.kind == kind
    assert message_dict(parsed)["binding"]["host_family"] == "R25"


@pytest.mark.parametrize("kind", ["program_preview", "program_commit", "program_validate"])
def test_program_command_payload_projection_has_model_wire_and_hash_parity(kind):
    expected_hashes = {
        "program_preview": "4e63e8ba5c7d7aa051caf0a2067677b93131bcab6572d91801538862f4feb93f",
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
    with pytest.raises(ValidationError, match="preview requires its exact ID"):
        ProgramCommandMessage.model_validate(injected)

    injected_null = command("program_preview")
    injected_null["preview_id"] = None
    with pytest.raises(ValidationError, match="preview requires its exact ID"):
        ProgramCommandMessage.model_validate(injected_null)


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
    ],
)
def test_agent_program_schema_snapshots_are_current(filename, generated):
    snapshot = json.loads((ROOT / "schemas" / filename).read_text(encoding="utf-8"))
    assert snapshot == generated()
    assert snapshot["additionalProperties"] is False
    assert snapshot["$schema"].endswith("2020-12/schema")
    assert snapshot["allOf"]
