from __future__ import annotations

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    CommandMessage,
    HelloMessage,
    PHASE5_PROTOCOL_VERSION,
    canonical_capability_hash,
    canonical_package_manifest_hash,
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


def test_phase5_protocol_is_additive_and_has_no_fixture_proof():
    hello = HelloMessage(
        protocol_version=PHASE5_PROTOCOL_VERSION,
        protocol_min_version=PHASE5_PROTOCOL_VERSION,
        protocol_max_version=PHASE5_PROTOCOL_VERSION,
        device_id="device-a",
        device_proof="signed-proof",
        capability_hash=canonical_capability_hash([]),
    )
    assert hello.fixture_proof is None
    assert (
        negotiate_protocol(
            PHASE5_PROTOCOL_VERSION,
            PHASE5_PROTOCOL_VERSION,
            supported_versions=(PHASE5_PROTOCOL_VERSION,),
        )
        == PHASE5_PROTOCOL_VERSION
    )
    with pytest.raises(ValidationError, match="only device_proof"):
        HelloMessage(
            protocol_version=PHASE5_PROTOCOL_VERSION,
            device_id="device-a",
            fixture_proof="legacy-secret",
            device_proof="signed-proof",
            capability_hash=canonical_capability_hash([]),
        )


def test_phase4_package_manifest_is_bounded_and_hash_bound():
    package = {
        "package_id": "autocad.lisp.drawing_info",
        "version": "3.3-c1",
        "sha256": "a" * 64,
    }
    manifest_hash = canonical_package_manifest_hash([package])
    hello = HelloMessage(
        device_id="device-a",
        fixture_proof="fixture",
        capability_hash=canonical_capability_hash(["observe"]),
        capabilities=["observe"],
        packages=[package],
        package_manifest_hash=manifest_hash,
    )
    assert hello.packages[0].version == "3.3-c1"
    with pytest.raises(ValidationError, match="package manifest hash"):
        HelloMessage(
            device_id="device-a",
            fixture_proof="fixture",
            capability_hash=canonical_capability_hash(["observe"]),
            capabilities=["observe"],
            packages=[package],
            package_manifest_hash="b" * 64,
        )
