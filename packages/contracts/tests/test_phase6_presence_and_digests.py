from __future__ import annotations

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    HeartbeatMessage,
    HelloMessage,
    ProgramExecutionBinding,
    message_dict,
    normalize_sha256_digest,
    parse_agent_message,
)


RAW_DIGEST = "a" * 64
PREFIXED_DIGEST = f"sha256:{RAW_DIGEST}"


def phase2_hello(**updates):
    value = {
        "protocol_version": "cad.agent/2",
        "protocol_min_version": "cad.agent/2",
        "protocol_max_version": "cad.agent/2",
        "message_type": "hello",
        "message_id": "message-001",
        "device_id": "device-001",
        "device_proof": "proof-001",
        "capability_hash": RAW_DIGEST,
        "capabilities": ["observe"],
        "write_lock_enabled": True,
        "hard_pause": False,
        "paused": False,
        "active_document_id": "document-001",
        "active_document_revision": "revision-007",
        "active_job_id": "job-001",
        "support_id": "P6-command-001",
        "mismatch_reason": "policy_mismatch",
        "outcome_unknown": False,
    }
    value.update(updates)
    return value


def phase2_heartbeat(**updates):
    value = {
        "protocol_version": "cad.agent/2",
        "message_type": "heartbeat",
        "message_id": "message-002",
        "session_id": "session-001",
        "device_id": "device-001",
        "sequence": 2,
        "last_processed_sequence": 1,
        "busy": True,
        "current_job_id": "job-001",
        "active_job_id": "job-001",
        "write_lock_enabled": True,
        "hard_pause": False,
        "paused": False,
        "active_document_id": "document-001",
        "active_document_revision": "revision-007",
        "support_id": "P6-command-001",
        "mismatch_reason": "registry_mismatch",
        "outcome_unknown": False,
    }
    value.update(updates)
    return value


def binding(**updates):
    value = {
        "program_digest": PREFIXED_DIGEST,
        "execution_digest": f"sha256:{'b' * 64}",
        "document_id": "document-001",
        "document_revision": "revision-007",
        "runtime_id": "managed_dotnet",
        "runtime_role": "primary",
        "host_family": "R25",
        "host_version": "0.2.0",
        "package_id": "autocad.managed_host.r25",
        "package_version": "0.2.0",
        "package_hash": f"sha256:{'c' * 64}",
        "capability_manifest_hash": f"sha256:{'d' * 64}",
        "operation_registry_version": "cad.program/0.2",
        "operation_registry_hash": f"sha256:{'e' * 64}",
        "policy_version": "phase6-lab-v1",
    }
    value.update(updates)
    return value


def test_phase6_presence_round_trips_on_hello_and_heartbeat():
    hello = parse_agent_message(phase2_hello())
    heartbeat = parse_agent_message(phase2_heartbeat())
    assert isinstance(hello, HelloMessage)
    assert isinstance(heartbeat, HeartbeatMessage)
    assert hello.write_lock_enabled is True
    assert hello.active_document_revision == "revision-007"
    assert heartbeat.active_job_id == heartbeat.current_job_id == "job-001"
    assert heartbeat.support_id == "P6-command-001"


def test_cad_agent_1_regression_and_phase6_field_boundary():
    legacy = {
        "protocol_version": "cad.agent/1",
        "message_type": "hello",
        "message_id": "legacy-001",
        "device_id": "device-legacy",
        "fixture_proof": "proof",
        "capability_hash": RAW_DIGEST,
        "capabilities": ["observe"],
    }
    parsed = parse_agent_message(legacy)
    assert isinstance(parsed, HelloMessage)
    assert message_dict(parsed)["protocol_version"] == "cad.agent/1"
    assert not (_phase6_keys() & message_dict(parsed).keys())
    # Default Pydantic serialization includes optional Phase 6 fields as null;
    # those placeholders must not break a legacy round trip.
    assert isinstance(parse_agent_message(parsed.model_dump()), HelloMessage)

    injected = dict(legacy, write_lock_enabled=True)
    with pytest.raises(ValidationError, match="require cad.agent/2"):
        HelloMessage.model_validate(injected)

    legacy_heartbeat = {
        "protocol_version": "cad.agent/1",
        "message_type": "heartbeat",
        "message_id": "legacy-heartbeat-001",
        "session_id": "session-legacy",
        "device_id": "device-legacy",
        "sequence": 2,
        "last_processed_sequence": 1,
        "busy": False,
    }
    parsed_heartbeat = parse_agent_message(legacy_heartbeat)
    assert isinstance(parsed_heartbeat, HeartbeatMessage)
    assert not (_phase6_keys() & message_dict(parsed_heartbeat).keys())
    with pytest.raises(ValidationError, match="require cad.agent/2"):
        HeartbeatMessage.model_validate(dict(legacy_heartbeat, hard_pause=True))


def _phase6_keys() -> set[str]:
    return {
        "write_lock_enabled",
        "hard_pause",
        "active_document_id",
        "active_document_revision",
        "active_job_id",
        "support_id",
        "mismatch_reason",
        "outcome_unknown",
    }


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (
            phase2_hello(active_document_revision=None),
            "active document identity and revision",
        ),
        (
            phase2_hello(paused=True),
            "hard_pause must match",
        ),
        (
            phase2_heartbeat(active_job_id="another-job"),
            "active_job_id must match",
        ),
        (
            phase2_hello(support_id="x" * 129),
            "String should have at most 128 characters",
        ),
        (
            phase2_heartbeat(mismatch_reason="Policy Mismatch"),
            "string_pattern_mismatch",
        ),
    ],
)
def test_phase6_presence_is_bounded_and_consistent(value, message):
    with pytest.raises(ValidationError, match=message):
        parse_agent_message(value)


def test_digest_normalizer_is_explicit_and_binding_wire_is_strict():
    assert normalize_sha256_digest(RAW_DIGEST) == PREFIXED_DIGEST
    assert normalize_sha256_digest(PREFIXED_DIGEST) == PREFIXED_DIGEST
    with pytest.raises(ValueError, match="lowercase"):
        normalize_sha256_digest(RAW_DIGEST, allow_legacy_raw=False)
    with pytest.raises(ValueError, match="lowercase"):
        normalize_sha256_digest(RAW_DIGEST.upper())
    with pytest.raises(ValueError, match="lowercase"):
        normalize_sha256_digest(f"sha512:{RAW_DIGEST}")

    ProgramExecutionBinding.model_validate(binding())
    for field in (
        "program_digest",
        "execution_digest",
        "package_hash",
        "capability_manifest_hash",
        "operation_registry_hash",
    ):
        invalid = binding(**{field: binding()[field].removeprefix("sha256:")})
        with pytest.raises(ValidationError, match="lowercase sha256"):
            ProgramExecutionBinding.model_validate(invalid)
