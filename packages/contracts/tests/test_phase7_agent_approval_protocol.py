from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocad_contracts.agent_protocol import (
    ApprovalDecisionMessage,
    ApprovalRequestMessage,
    agent_approval_json_schema,
    approval_decision_proof_payload,
    approval_request_digest,
    parse_agent_message,
)


ROOT = Path(__file__).parents[1]
DIGEST = "sha256:" + "a" * 64
THUMBPRINT = "sha256:" + "b" * 64
NONCE = "n" * 43


def request_payload(**updates: object) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    value: dict[str, object] = {
        "protocol_version": "cad.agent/2",
        "message_type": "approval_request",
        "message_id": "message-approval-1",
        "correlation_id": "correlation-1",
        "session_id": "session-current",
        "device_id": "device-001",
        "sequence": 7,
        "issued_at": now.isoformat(),
        "deadline_at": (now + timedelta(minutes=5)).isoformat(),
        "approval_request_id": "approval-request-1",
        "intent_id": "intent-1",
        "consent_id": "consent-1",
        "intent_digest": DIGEST,
        "challenge_nonce": NONCE,
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "required_assurance": "device_local_confirmation",
        "device_identity_generation": 1,
        "device_key_thumbprint": THUMBPRINT,
        "trusted_summary": {
            "operation": "program_commit",
            "operation_summary": "Tạo 3 đường tròn theo preview đã khóa.",
            "document_name": "drawing33.dwg",
            "document_id": "document-1",
            "operation_count": 1,
            "entity_count": 3,
            "runtime_label": "Managed .NET R25",
            "runtime_id": "managed_dotnet",
            "package_id": "autocad.managed_host.r25",
            "package_version": "0.2.0",
            "registry_version": "cad.program/0.2",
            "risk_class": "medium",
            "preview_created_at": now.isoformat(),
            "warnings": ["Chỉ tạo mới; không sửa đối tượng cũ."],
            "support_id": "support-1",
        },
    }
    value.update(updates)
    value["approval_request_digest"] = approval_request_digest(value)
    return value


def decision_payload(request: ApprovalRequestMessage, **updates: object) -> dict[str, object]:
    decided_at = datetime.now(timezone.utc).isoformat()
    value: dict[str, object] = {
        "protocol_version": "cad.agent/2",
        "message_type": "approval_decision",
        "message_id": "message-decision-1",
        "session_id": request.session_id,
        "device_id": request.device_id,
        "approval_request_id": request.approval_request_id,
        "approval_request_digest": request.approval_request_digest,
        "intent_id": request.intent_id,
        "consent_id": request.consent_id,
        "intent_digest": request.intent_digest,
        "challenge_nonce": request.challenge_nonce,
        "decision": "approve",
        "decided_at": decided_at,
        "device_identity_generation": request.device_identity_generation,
        "device_key_thumbprint": request.device_key_thumbprint,
        "device_session_proof": "s" * 86,
    }
    value.update(updates)
    return value


def test_request_and_decision_are_distinct_strict_control_messages() -> None:
    request = parse_agent_message(request_payload())
    assert isinstance(request, ApprovalRequestMessage)
    decision = parse_agent_message(decision_payload(request))
    assert isinstance(decision, ApprovalDecisionMessage)
    assert not hasattr(request, "action")
    assert not hasattr(decision, "payload")

    for payload in (
        request_payload(extra="forbidden"),
        decision_payload(request, extra="forbidden"),
        {
            "protocol_version": "cad.agent/2",
            "message_type": "approval_action",
            "action": "approve",
        },
    ):
        with pytest.raises(ValidationError):
            parse_agent_message(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("protocol_version", "cad.agent/1"),
        ("challenge_nonce", "short"),
        ("intent_digest", "sha256:" + "A" * 64),
        ("device_identity_generation", 0),
        ("device_key_thumbprint", "b" * 64),
        ("required_assurance", "none"),
    ],
)
def test_request_rejects_wrong_protocol_nonce_digest_generation_key_and_assurance(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        parse_agent_message(request_payload(**{field: value}))


def test_request_rejects_expiry_tampering_and_full_document_path() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        parse_agent_message(
            request_payload(
                issued_at=(now - timedelta(minutes=10)).isoformat(),
                deadline_at=(now - timedelta(minutes=5)).isoformat(),
                expires_at=(now - timedelta(minutes=5)).isoformat(),
            )
        )

    tampered = request_payload()
    tampered["session_id"] = "session-replaced"
    with pytest.raises(ValidationError):
        parse_agent_message(tampered)

    full_path = request_payload()
    full_path["trusted_summary"]["document_name"] = r"C:\private\drawing33.dwg"
    full_path["approval_request_digest"] = approval_request_digest(full_path)
    with pytest.raises(ValidationError):
        parse_agent_message(full_path)


def test_approval_fields_and_message_size_are_bounded() -> None:
    oversized = request_payload()
    oversized["trusted_summary"]["operation_summary"] = "x" * 513
    oversized["approval_request_digest"] = approval_request_digest(oversized)
    with pytest.raises(ValidationError):
        parse_agent_message(oversized)

    warnings = request_payload()
    warnings["trusted_summary"]["warnings"] = ["w"] * 17
    warnings["approval_request_digest"] = approval_request_digest(warnings)
    with pytest.raises(ValidationError):
        parse_agent_message(warnings)


def test_decision_proof_payload_binds_every_security_field() -> None:
    request = parse_agent_message(request_payload())
    assert isinstance(request, ApprovalRequestMessage)
    decision = decision_payload(request)
    proof = approval_decision_proof_payload(
        approval_request_id=str(decision["approval_request_id"]),
        approval_request_digest=str(decision["approval_request_digest"]),
        session_id=str(decision["session_id"]),
        device_id=str(decision["device_id"]),
        device_identity_generation=int(decision["device_identity_generation"]),
        device_key_thumbprint=str(decision["device_key_thumbprint"]),
        consent_id=str(decision["consent_id"]),
        intent_id=str(decision["intent_id"]),
        intent_digest=str(decision["intent_digest"]),
        challenge_nonce=str(decision["challenge_nonce"]),
        decision="approve",
        decided_at=str(decision["decided_at"]),
    )
    assert proof.startswith("cad.agent.approval-decision/1\n")
    for binding in (
        request.approval_request_id,
        request.approval_request_digest,
        request.session_id,
        request.device_id,
        request.consent_id,
        request.intent_id,
        request.intent_digest,
        request.challenge_nonce,
        request.device_key_thumbprint,
        str(request.device_identity_generation),
    ):
        assert binding in proof


def test_approval_schema_snapshot_is_current() -> None:
    expected = json.loads(
        (ROOT / "schemas" / "cad-agent-2-approval.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert expected == agent_approval_json_schema()
