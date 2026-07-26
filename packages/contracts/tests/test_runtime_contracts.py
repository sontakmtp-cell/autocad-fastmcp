from __future__ import annotations

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    CapabilityManifest,
    HelloMessage,
    canonical_capability_hash,
    canonical_capability_manifest,
    canonical_capability_manifest_hash,
    message_dict,
    parse_agent_message,
)


def _manifest() -> dict:
    return {
        "schema_version": "cad.capability/1",
        "registry_version": "cad.program/0",
        "cad_products": [
            {
                "product": "AutoCAD Mechanical",
                "edition": "full",
                "release_year": 2025,
                "series": "R25.0",
                "runtime": {
                    "id": "managed_dotnet",
                    "role": "primary",
                    "host_family": "R25",
                    "host_version": "0.1.0",
                    "future_optional": {"supported": True},
                },
                "capabilities": ["query.entities", "observe.summary", "observe.summary"],
            }
        ],
        "fallback_runtimes": [
            {
                "id": "autolisp_file_ipc",
                "role": "compatibility",
                "package_version": "3.3-c1",
            }
        ],
    }


def test_manifest_is_canonical_and_hash_is_stable_for_ordering():
    first = _manifest()
    second = _manifest()
    second["cad_products"][0]["capabilities"] = ["observe.summary", "query.entities"]
    assert canonical_capability_manifest(first) == canonical_capability_manifest(second)
    assert canonical_capability_manifest_hash(first) == canonical_capability_manifest_hash(second)
    assert len(canonical_capability_manifest_hash(first)) == 64


def test_unknown_optional_runtime_fields_are_retained():
    parsed = CapabilityManifest.model_validate(_manifest())
    dumped = canonical_capability_manifest(parsed)
    assert dumped["cad_products"][0]["runtime"]["future_optional"] == {"supported": True}


def test_legacy_hello_still_parses_without_runtime_manifest():
    legacy = {
        "protocol_version": "cad.agent/1",
        "message_type": "hello",
        "device_id": "device-a",
        "fixture_proof": "proof",
        "capability_hash": canonical_capability_hash(["observe"]),
        "capabilities": ["observe"],
    }
    parsed = parse_agent_message(legacy)
    assert isinstance(parsed, HelloMessage)
    assert parsed.capability_manifest is None


def test_additive_hello_round_trips_and_checks_manifest_hash():
    manifest = CapabilityManifest.model_validate(_manifest())
    digest = canonical_capability_manifest_hash(manifest)
    hello = HelloMessage(
        device_id="device-a",
        fixture_proof="proof",
        capability_hash=canonical_capability_hash(["observe"]),
        capabilities=["observe"],
        capability_manifest=manifest,
        capability_manifest_hash=digest,
    )
    parsed = parse_agent_message(message_dict(hello))
    assert isinstance(parsed, HelloMessage)
    assert parsed.capability_manifest_hash == digest


def test_manifest_without_hash_fails_closed():
    with pytest.raises(ValidationError, match="requires its canonical hash"):
        HelloMessage(
            device_id="device-a",
            fixture_proof="proof",
            capability_hash=canonical_capability_hash(["observe"]),
            capabilities=["observe"],
            capability_manifest=CapabilityManifest.model_validate(_manifest()),
        )
