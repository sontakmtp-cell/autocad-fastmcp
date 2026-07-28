from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    ConsentRecord,
    ExecutionEvidenceEvent,
    ExecutionIntentRecord,
    RecoveryCaseRecord,
    RollbackCheckpointRecord,
    RollbackPlanRecord,
    RollbackReceiptRecord,
    execution_evidence_digest,
    execution_intent_digest,
    parse_phase7_domain_record,
    phase7_domain_json_schema,
    rollback_checkpoint_digest,
    rollback_plan_digest,
    rollback_receipt_digest,
)


SCHEMA_PATH = (
    Path(__file__).parents[1] / "schemas" / "cad-phase7-domain.schema.json"
)
NOW = "2026-07-27T01:00:00Z"
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


def intent_value(**updates) -> dict:
    value = {
        "schema_version": "cad.execution-intent/1",
        "intent_id": "intent-0001",
        "intent_version": 1,
        "owner_subject": "owner-a",
        "actor_principal": {"issuer": "https://issuer.test/", "subject": "user-a"},
        "action": "program_commit",
        "state": "awaiting_approval",
        "state_version": 0,
        "device_id": "device-0001",
        "device_identity_generation": 1,
        "device_key_thumbprint": digest("device-key"),
        "document_id": "document-0001",
        "expected_document_revision": "revision-before",
        "program_id": "program-0001",
        "program_revision": 1,
        "program_digest": digest("program"),
        "preview_id": "preview-0001",
        "preview_digest": digest("preview"),
        "preview_execution_digest": digest("preview-execution"),
        "preview_expires_at": LATER,
        "deterministic_receipt_id": "receipt-0001",
        "commit_execution_digest": digest("commit-execution"),
        "runtime_pins": runtime_pins(),
        "policy_pins": policy_pins(),
        "risk_class": "low",
        "required_assurance": "user_recent_auth",
        "trusted_effect_summary": [
            {"kind": "create_entities", "count": 1, "summary": "Create one line"}
        ],
        "idempotency_key": "commit-key-0001",
        "request_hash": digest("request"),
        "created_at": NOW,
        "expires_at": LATER,
        "consent_id": None,
        "released_job_id": None,
    }
    value.update(updates)
    value["intent_digest"] = execution_intent_digest(value)
    return value


def checkpoint_value(**updates) -> dict:
    value = {
        "schema_version": "cad.rollback.checkpoint/1",
        "checkpoint_id": "checkpoint-0001",
        "owner_subject": "owner-a",
        "original_receipt_id": "receipt-0001",
        "original_receipt_digest": digest("original-receipt"),
        "program_id": "program-0001",
        "program_revision": 1,
        "program_digest": digest("program"),
        "preview_id": "preview-0001",
        "preview_digest": digest("preview"),
        "execution_digest": digest("commit-execution"),
        "document_id": "document-0001",
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


def plan_value(**updates) -> dict:
    checkpoint = checkpoint_value()
    value = {
        "schema_version": "cad.rollback.plan/1",
        "plan_id": "plan-0001",
        "owner_subject": "owner-a",
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_digest": checkpoint["checkpoint_digest"],
        "original_receipt_id": checkpoint["original_receipt_id"],
        "document_id": checkpoint["document_id"],
        "current_document_revision": checkpoint["document_revision_after"],
        "rollback_execution_digest": digest("rollback-execution"),
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


def rollback_receipt_value(**updates) -> dict:
    checkpoint = checkpoint_value()
    plan = plan_value()
    value = {
        "schema_version": "cad.rollback.receipt/1",
        "rollback_receipt_id": "rollback-receipt-0001",
        "owner_subject": "owner-a",
        "original_receipt_id": checkpoint["original_receipt_id"],
        "original_receipt_digest": checkpoint["original_receipt_digest"],
        "program_digest": checkpoint["program_digest"],
        "original_execution_digest": checkpoint["execution_digest"],
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_digest": checkpoint["checkpoint_digest"],
        "rollback_plan_id": plan["plan_id"],
        "rollback_plan_digest": plan["plan_digest"],
        "rollback_job_id": "rollback-job-0001",
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


def test_intent_digest_is_deterministic_and_immutable_binding_is_verified():
    value = intent_value()
    first = ExecutionIntentRecord.model_validate(value)
    second = ExecutionIntentRecord.model_validate(deepcopy(value))
    assert first.intent_digest == second.intent_digest

    transitioned = deepcopy(value)
    transitioned.update(
        state="ready", state_version=1, consent_id="consent-0001"
    )
    assert ExecutionIntentRecord.model_validate(transitioned).intent_digest == first.intent_digest

    tampered = deepcopy(value)
    tampered["document_id"] = "document-tampered"
    with pytest.raises(ValidationError, match="intent_digest"):
        ExecutionIntentRecord.model_validate(tampered)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "approved"),
        ("action", "generic_write"),
        ("risk_class", "critical"),
        ("required_assurance", "model_confirmation"),
    ],
)
def test_intent_rejects_non_contract_enums(field, value):
    candidate = intent_value(**{field: value})
    with pytest.raises(ValidationError):
        ExecutionIntentRecord.model_validate(candidate)


def test_consent_states_are_strict_and_released_is_not_a_consent_state():
    base = {
        "schema_version": "cad.consent/1",
        "consent_id": "consent-0001",
        "consent_version": 1,
        "owner_subject": "owner-a",
        "intent_id": "intent-0001",
        "intent_version": 1,
        "intent_digest": digest("intent"),
        "required_assurance": "user_recent_auth",
        "state": "requested",
        "state_version": 0,
        "challenge_nonce_hash": digest("nonce"),
        "requested_at": NOW,
        "expires_at": LATER,
    }
    ConsentRecord.model_validate(base)
    with pytest.raises(ValidationError):
        ConsentRecord.model_validate({**base, "state": "released"})


def test_evidence_is_typed_bounded_and_digest_bound():
    value = {
        "schema_version": "cad.execution-evidence/1",
        "event_id": "event-0001",
        "owner_subject": "owner-a",
        "source": "host",
        "source_sequence": 7,
        "job_id": "job-0001",
        "intent_id": "intent-0001",
        "execution_digest": digest("execution"),
        "payload": {
            "milestone": "effect_and_receipt_committed",
            "outcome": "committed",
            "summary": "Effect and receipt committed atomically",
            "details": [{"key": "entity_count", "value": 1}],
        },
        "source_timestamp": NOW,
        "gateway_received_at": NOW,
    }
    value["event_digest"] = execution_evidence_digest(value)
    ExecutionEvidenceEvent.model_validate(value)

    wrong_source = deepcopy(value)
    wrong_source["source"] = "gateway"
    wrong_source["event_digest"] = execution_evidence_digest(wrong_source)
    with pytest.raises(ValidationError, match="Host milestone"):
        ExecutionEvidenceEvent.model_validate(wrong_source)

    extra = deepcopy(value)
    extra["payload"]["unbounded"] = {}
    with pytest.raises(ValidationError, match="Extra inputs"):
        ExecutionEvidenceEvent.model_validate(extra)


def test_recovery_contract_has_only_safe_non_reexecution_actions():
    value = {
        "schema_version": "cad.recovery-case/1",
        "case_id": "case-0001",
        "owner_subject": "owner-a",
        "state": "open",
        "resolution_version": 0,
        "execution_binding_digest": digest("binding"),
        "intent_id": "intent-0001",
        "job_id": "job-0001",
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
    RecoveryCaseRecord.model_validate(value)
    with pytest.raises(ValidationError):
        RecoveryCaseRecord.model_validate(
            {**value, "safe_actions": ["retry_original_write"]}
        )


def test_checkpoint_plan_and_receipt_digests_reject_binding_tampering():
    checkpoint = RollbackCheckpointRecord.model_validate(checkpoint_value())
    plan = RollbackPlanRecord.model_validate(plan_value())
    receipt = RollbackReceiptRecord.model_validate(rollback_receipt_value())
    assert checkpoint.checkpoint_digest == rollback_checkpoint_digest(checkpoint)
    assert plan.plan_digest == rollback_plan_digest(plan)
    assert receipt.receipt_digest == rollback_receipt_digest(receipt)

    for model, value, field in (
        (RollbackCheckpointRecord, checkpoint_value(), "document_revision_after"),
        (RollbackPlanRecord, plan_value(), "current_document_revision"),
        (RollbackReceiptRecord, rollback_receipt_value(), "document_revision_after"),
    ):
        tampered = deepcopy(value)
        tampered[field] += "-tampered"
        with pytest.raises(ValidationError, match="digest"):
            model.model_validate(tampered)


def test_checkpoint_digest_matches_managed_host_vector():
    value = {
        "schema_version": "cad.rollback.checkpoint/1",
        "checkpoint_id": "AUTOCAD_MCP_CHECKPOINT_1b7e81ea3553752b13d39c951899c174",
        "owner_subject": "owner-a",
        "original_receipt_id": "AUTOCAD_MCP_PROGRAM_3e355b4ab201ef57a88ac34d7c5c9cd9",
        "original_receipt_digest": (
            "sha256:83b9970a6b41748822e603c4c5d137307f277a3b14be49c4c0fa843a95d92978"
        ),
        "program_id": "program-1",
        "program_revision": 1,
        "program_digest": f"sha256:{'1' * 64}",
        "preview_id": "preview-1",
        "preview_digest": f"sha256:{'3' * 64}",
        "execution_digest": f"sha256:{'2' * 64}",
        "document_id": "document-1",
        "document_revision_before": "41",
        "document_revision_after": "42",
        "created_entities": [{
            "handle": "1A",
            "entity_type": "LINE",
            "layer": "CAD-MCP",
            "canonical_fingerprint": f"sha256:{'4' * 64}",
        }],
        "non_entity_object_created": True,
        "runtime_pins": {
            "runtime_id": "managed_dotnet",
            "runtime_role": "primary",
            "host_family": "R25",
            "host_version": "0.2.0",
            "agent_package_id": "agent-package",
            "agent_package_version": "1.0.0",
            "agent_package_hash": f"sha256:{'9' * 64}",
            "host_package_id": "autocad.managed_host.r25",
            "host_package_version": "0.2.0",
            "host_package_hash": f"sha256:{'7' * 64}",
        },
        "policy_pins": {
            "capability_manifest_hash": f"sha256:{'8' * 64}",
            "registry_version": "cad.program/0.2",
            "operation_registry_hash": (
                "sha256:5dee5cb2d709f06acff2b8678bb084cd9bfa5d1988e9712510c299d61ba30eb8"
            ),
            "policy_version": "phase6-policy/1",
        },
        "checkpoint_digest": (
            "sha256:eff5ba7a65f9eb9d58cd6a05895050caaa4cde6c285bd9fa9662370ef896474b"
        ),
        "created_at": "2026-07-28T00:00:00.0000000+00:00",
    }
    checkpoint = RollbackCheckpointRecord.model_validate(value)
    assert rollback_checkpoint_digest(checkpoint) == value["checkpoint_digest"]


def test_phase7_union_rejects_extra_fields_and_schema_artifact_is_current():
    value = intent_value()
    parsed = parse_phase7_domain_record(value)
    assert isinstance(parsed, ExecutionIntentRecord)
    with pytest.raises(ValidationError, match="Extra inputs"):
        parse_phase7_domain_record({**value, "model_narrative": "trusted"})

    assert json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) == phase7_domain_json_schema()
