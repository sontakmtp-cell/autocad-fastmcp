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
    "cad.recovery.receipt_query",
    "cad.rollback.checkpoint.lookup",
    "cad.rollback.preview",
    "cad.rollback.commit",
    "cad.rollback.validate",
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


def _domain_hash(domain: str, payload) -> str:
    return _canonical_hash({"domain": domain, "payload": payload})


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


def test_phase8_schemas_match_python_snapshots_and_remain_compile_only():
    source = _load(ROOT / "schemas" / "cad-program-1.0.schema.json")
    source_python = _load(CONTRACTS_ROOT / "schemas" / "cad-program-1.0.schema.json")
    plan = _load(ROOT / "schemas" / "cad-execution-plan-1.schema.json")
    plan_python = _load(CONTRACTS_ROOT / "schemas" / "cad-execution-plan-1.schema.json")
    binding = _load(ROOT / "schemas" / "cad-execution-binding-1.schema.json")
    binding_python = _load(
        CONTRACTS_ROOT / "schemas" / "cad-execution-binding-1.schema.json"
    )

    assert source == source_python
    assert plan == plan_python
    assert binding == binding_python
    assert source["$schema"].endswith("2020-12/schema")
    assert plan["$schema"].endswith("2020-12/schema")
    assert source["additionalProperties"] is False
    assert plan["additionalProperties"] is False
    assert set(source["$defs"]["OpaqueArtifactRef"]["properties"]) == {
        "artifact_id",
        "owner_id",
        "content_type",
        "byte_length",
        "artifact_digest",
    }
    assert set(source["$defs"]["OpaqueComponentRef"]["properties"]) == {
        "component_id",
        "owner_id",
        "component_version",
        "content_type",
        "byte_length",
        "component_digest",
    }
    assert set(plan["$defs"]["MaterializedTargetRef"]["properties"]) == {
        "ref_id",
        "owner_id",
        "device_id",
        "document_id",
        "snapshot_id",
        "document_revision",
        "entity_id",
        "entity_type",
        "fingerprint",
    }
    execution_binding = binding
    assert execution_binding["additionalProperties"] is False
    assert set(execution_binding["required"]) == {
        "runtime_id",
        "runtime_role",
        "host_family",
        "host_version",
        "package_id",
        "package_version",
        "package_hash",
        "capability_manifest_hash",
        "operation_registry_version",
        "operation_registry_hash",
        "policy_version",
        "source_digest",
        "schema_version",
        "action",
        "source_schema_version",
        "source_program_id",
        "source_program_revision",
        "compiler_id",
        "compiler_version",
        "compiler_digest",
        "compiler_package_hash",
        "plan_schema_version",
        "execution_plan_digest",
        "expansion_digest",
        "effect_manifest_digest",
        "target_refs_digest",
        "validation_profiles_digest",
        "checkpoint_strategy_digest",
        "hard_budgets_digest",
        "rollout_policy_digest",
        "device_id",
        "source_snapshot_id",
        "document_id",
        "document_revision",
        "execution_binding_digest",
    }

    encoded = json.dumps({"source": source, "plan": plan}).lower()
    for forbidden in (
        "assembly_path",
        "executable",
        "raw_lisp",
        "network_url",
        "delete",
        "erase_entity",
        "trim",
        "fillet",
        "chamfer",
    ):
        assert forbidden not in encoded


def test_phase8_cross_runtime_golden_vector_recomputes_every_digest():
    vector = _load(
        ROOT / "program" / "golden" / "cad-program-1.0-compiler-vector.json"
    )
    source_without_digest = dict(vector["source"])
    source_without_digest.pop("semantic_digest")
    plan_without_digest = dict(vector["plan"])
    plan_without_digest.pop("execution_plan_digest")

    assert source_without_digest == vector["canonical_source"]
    assert plan_without_digest == vector["canonical_plan"]
    domains = vector["digest_domains"]
    assert len(set(domains.values())) == len(domains)
    assert vector["source_digest"] == (
        f"sha256:{_domain_hash(domains['source'], source_without_digest)}"
    )
    assert vector["compiler_digest"] == (
        f"sha256:{_domain_hash(domains['compiler'], vector['compiler_manifest'])}"
    )
    assert vector["expansion_digest"] == (
        f"sha256:{_domain_hash(domains['expansion'], {'operations': vector['plan']['operations']})}"
    )
    assert vector["effect_manifest_digest"] == (
        f"sha256:{_domain_hash(domains['effect'], vector['plan']['effect_manifest'])}"
    )
    assert vector["target_refs_digest"] == (
        f"sha256:{_domain_hash(domains['target_refs'], {'target_refs': vector['plan']['materialized_target_refs']})}"
    )
    assert vector["validation_profiles_digest"] == (
        f"sha256:{_domain_hash(domains['validation_profiles'], {'validation_profiles': vector['plan']['validation_profiles']})}"
    )
    assert vector["checkpoint_strategy_digest"] == (
        f"sha256:{_domain_hash(domains['checkpoint_strategy'], {'checkpoint_strategy': vector['plan']['checkpoint_strategy']})}"
    )
    assert vector["hard_budgets_digest"] == (
        f"sha256:{_domain_hash(domains['hard_budgets'], vector['hard_budgets'])}"
    )
    assert vector["execution_plan_digest"] == (
        f"sha256:{_domain_hash(domains['plan'], plan_without_digest)}"
    )
    assert vector["plan"]["source_digest"] == vector["source_digest"]
    assert vector["plan"]["compiler"]["compiler_digest"] == vector["compiler_digest"]
    assert vector["plan"]["expansion_digest"] == vector["expansion_digest"]
    assert (
        vector["plan"]["effect_manifest_digest"]
        == vector["effect_manifest_digest"]
    )
    assert vector["plan"]["target_refs_digest"] == vector["target_refs_digest"]
    assert vector["execution_binding_digest"] == (
        f"sha256:{_domain_hash(domains['execution_binding'], vector['canonical_execution_binding'])}"
    )
    assert (
        vector["execution_binding"]["execution_plan_digest"]
        == vector["execution_plan_digest"]
    )
    assert vector["plan"]["execution_plan_digest"] == vector["execution_plan_digest"]


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


def test_phase7_rollback_schema_is_strict_and_has_no_raw_handle_input():
    schema = _load(ROOT / "schemas" / "cad-phase7-rollback.schema.json")
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["$defs"]["checkpoint"]["additionalProperties"] is False
    assert schema["$defs"]["rollback_preview_request"]["additionalProperties"] is False
    assert schema["$defs"]["rollback_commit_request"]["additionalProperties"] is False
    encoded_preview = json.dumps(schema["$defs"]["rollback_preview_request"])
    encoded_commit = json.dumps(schema["$defs"]["rollback_commit_request"])
    assert "entity_handles" not in encoded_preview
    assert "entity_handles" not in encoded_commit
