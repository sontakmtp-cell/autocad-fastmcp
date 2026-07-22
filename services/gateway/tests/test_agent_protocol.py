from __future__ import annotations

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    CommandMessage,
    HelloMessage,
    canonical_capability_hash,
    canonical_payload_hash,
    negotiate_protocol,
    parse_agent_message,
)


def test_protocol_is_strict_and_canonical_hash_is_stable():
    payload = {"b": 2, "a": 1}
    assert canonical_payload_hash(payload) == canonical_payload_hash({"a": 1, "b": 2})
    with pytest.raises(ValidationError):
        HelloMessage(device_id="a", fixture_proof="t", capability_hash="x", extra=True)
    with pytest.raises(ValidationError):
        CommandMessage(
            device_id="a",
            job_id="j",
            command_id="c",
            idempotency_key="i",
            payload_hash="0" * 64,
            payload={"x": 1},
            unexpected=True,
        )


def test_version_negotiation_and_discriminated_parse():
    assert negotiate_protocol("cad.agent/1", "cad.agent/1") == "cad.agent/1"
    assert negotiate_protocol("cad.agent/2", "cad.agent/2") is None
    value = parse_agent_message(
        HelloMessage(
            device_id="device-a",
            fixture_proof="fixture",
            capability_hash=canonical_capability_hash([]),
        ).model_dump()
    )
    assert isinstance(value, HelloMessage)
