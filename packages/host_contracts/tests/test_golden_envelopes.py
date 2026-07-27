from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
CONTRACTS_ROOT = ROOT.parent / "contracts"
REQUIRED_PROGRAM_OPERATIONS = {
    "ensure_layer",
    "create_line",
    "create_circle",
    "create_polyline",
    "create_rectangle",
    "create_text",
    "create_dimension_linear",
}
ALLOWED_OPERATIONS = {
    "host.health",
    "drawing.observe.summary",
    "entity.snapshot.page",
    "document.events.summary",
    "cad.program.preview",
    "cad.program.commit",
    "cad.program.validate",
}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_schemas_are_json_schema_2020_12_and_forbid_extra_envelope_fields():
    envelope = _load(ROOT / "schemas" / "cad-host-envelope.schema.json")
    payloads = _load(ROOT / "schemas" / "cad-host-payloads.schema.json")
    message = _load(ROOT / "schemas" / "cad-host-message.schema.json")
    assert envelope["$schema"].endswith("2020-12/schema")
    assert payloads["$schema"].endswith("2020-12/schema")
    assert message["$schema"].endswith("2020-12/schema")
    assert envelope["additionalProperties"] is False
    assert set(payloads["$defs"]) == {
        "handshake",
        "handshake_result",
        "command",
        "result",
        "error",
    }
    command_arguments = payloads["$defs"]["command"]["properties"]["arguments"]["properties"]
    assert command_arguments["program"]["$ref"] == "cad-program-0.2.schema.json"
    assert command_arguments["preview_id"]["maxLength"] == 128
    assert command_arguments["expires_at"]["format"] == "date-time"
    assert command_arguments["receipt_id"]["maxLength"] == 128
    assert set(command_arguments["validation"]["required"]) == {
        "validation_id",
        "receipt_id",
    }
    assert command_arguments["execution_binding"]["additionalProperties"] is False
    assert set(command_arguments["execution_binding"]["required"]) >= {
        "program_digest",
        "execution_digest",
        "runtime_id",
        "package_hash",
        "capability_manifest_hash",
        "operation_registry_version",
        "operation_registry_hash",
        "policy_version",
    }


def test_cad_program_02_schema_matches_python_snapshot_and_is_runtime_neutral():
    host_schema = _load(ROOT / "schemas" / "cad-program-0.2.schema.json")
    python_schema = _load(CONTRACTS_ROOT / "schemas" / "cad-program-0.2.schema.json")
    assert host_schema == python_schema
    assert host_schema["$schema"].endswith("2020-12/schema")
    assert host_schema["additionalProperties"] is False

    serialized = json.dumps(host_schema)
    for forbidden in (
        "runtime_id",
        "runtime_role",
        "host_family",
        "host_version",
        "package_hash",
        "capability_manifest_hash",
        "operation_registry_hash",
        "policy_version",
        "execution_digest",
    ):
        assert f'"{forbidden}"' not in serialized

    operation_kinds = {
        definition["properties"]["kind"]["const"]
        for definition in host_schema["$defs"].values()
        if isinstance(definition, dict)
        and isinstance(definition.get("properties"), dict)
        and isinstance(definition["properties"].get("kind"), dict)
        and "const" in definition["properties"]["kind"]
        and definition["properties"]["kind"]["const"].startswith(("ensure_", "create_"))
    }
    assert operation_kinds == REQUIRED_PROGRAM_OPERATIONS


def test_cad_program_01_remains_available_for_lab_regression():
    legacy = _load(ROOT / "schemas" / "cad-program-0.1.schema.json")
    assert legacy["$id"].endswith("/cad.program/0.1/schema.json")
    assert legacy["properties"]["registry_version"]["const"] == "cad.program/0.1"
    legacy_program = _load(ROOT / "program" / "golden" / "create-only-program.json")
    assert legacy_program["registry_version"] == "cad.program/0.1"


def test_cross_language_digest_vector_is_canonical_and_complete():
    vector = _load(ROOT / "program" / "golden" / "cad-program-0.2-digest-vector.json")
    canonical = json.dumps(
        vector["program"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert vector["canonical_json"] == canonical
    assert vector["program_digest"] == f"sha256:{_canonical_hash(vector['program'])}"
    assert vector["registry_digest"] == f"sha256:{_canonical_hash(vector['registry'])}"
    assert {
        operation["kind"] for operation in vector["program"]["operations"]
    } == REQUIRED_PROGRAM_OPERATIONS
    assert {
        operation["kind"] for operation in vector["registry"]["operations"]
    } == REQUIRED_PROGRAM_OPERATIONS


def test_golden_envelopes_have_matching_payload_hashes_and_bounded_identity():
    for path in sorted((ROOT / "golden").glob("*.json")):
        envelope = _load(path)
        assert envelope["protocol_version"] == "cad.host/1"
        assert envelope["payload_hash"] == _canonical_hash(envelope["payload"])
        assert 1 <= len(envelope["session_id"]) <= 128
        assert 1 <= len(envelope["command_id"]) <= 128
        assert 0 <= envelope["sequence"] <= 1_000_000_000


def test_command_golden_messages_only_use_explicit_read_registry():
    commands = [
        _load(path)
        for path in (ROOT / "golden").glob("*.json")
        if _load(path)["message_type"] == "command"
    ]
    assert commands
    assert {item["payload"]["operation_id"] for item in commands} <= ALLOWED_OPERATIONS
    serialized = json.dumps(commands).lower()
    for forbidden in ("script", "assembly_path", "executable", "raw_lisp", "network_url"):
        assert forbidden not in serialized
