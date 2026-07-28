from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent


def _load(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def test_source_compiler_vectors_cover_required_malicious_categories():
    catalog = _load("source-compiler-vectors.json")
    vectors = catalog["vectors"]
    ids = [item["id"] for item in vectors]
    categories = {item["category"] for item in vectors}

    assert catalog["adapter_status"] == "real_canonical_compiler"
    assert len(ids) == len(set(ids))
    assert {
        "arbitrary_code",
        "arbitrary_command",
        "arbitrary_path",
        "network_access",
        "environment_access",
        "nondeterminism",
        "recursion",
        "unbounded_iteration",
        "expansion_budget",
        "numeric_error",
        "non_finite_number",
        "untrusted_target_binding",
        "invalid_reference",
        "deterministic_expansion",
    } <= categories
    assert all(
        item["expected"]["outcome"] == "reject"
        for item in vectors
        if item["id"] != "valid-typed-repeat-deterministic"
    )
    by_id = {item["id"]: item for item in vectors}
    assert "path" in json.dumps(by_id["reject-file-path"]["input"]).lower()
    assert "url" in json.dumps(by_id["reject-network"]["input"]).lower()
    assert "command" in json.dumps(by_id["reject-command"]["input"]).lower()


def test_cross_runtime_claims_are_granular_and_fail_closed():
    catalog = _load("cross-runtime-categories.json")
    categories = {item["id"]: item for item in catalog["categories"]}
    rules = catalog["rules"]

    assert len(catalog["claim_key_fields"]) == 7
    assert rules["lt_write_default"] == "off"
    assert rules["write_fallback"] == "forbidden"
    assert rules["ezdxf_live_dwg_authority"] is False
    assert "modify" in rules["checkpoint_v1_forbidden_effects"]
    assert "erase" in rules["checkpoint_v1_forbidden_effects"]

    assert categories["lt-write-negative"]["write_enabled"] is False
    assert categories["lt-write-negative"]["expected_result"] == "capability_missing"
    ezdxf = categories["ezdxf-headless"]
    assert ezdxf["tier"] == "headless_only"
    assert ezdxf["live_dwg_authority"] is False
    assert "live_dwg_commit" in ezdxf["must_not_prove"]


def test_fault_matrix_covers_drop_duplicate_recovery_and_invalidation():
    matrix = _load("fault-recovery-matrix.json")
    cases = {item["id"]: item for item in matrix["cases"]}

    assert matrix["status"] == "mixed_automated_contract_and_live_pending"
    assert matrix["live_evidence_status"] == "missing"
    assert all((ROOT / path).is_file() for path in matrix["automated_evidence"])
    assert {
        "drop-before-release",
        "drop-after-dispatch-before-ack",
        "drop-after-started",
        "effect-response-lost",
        "duplicate-exact-command",
        "conflicting-duplicate",
        "checkpoint-missing-after-effect",
        "rollback-response-lost",
        "restart-with-unknown",
        "hard-pause-before-effect",
        "capability-changed-after-preview",
    } <= cases.keys()
    assert all(item["may_reexecute"] is False for item in cases.values())
    assert cases["drop-after-started"]["expected_state"] == "outcome_unknown"
    assert (
        cases["effect-response-lost"]["expected_state"]
        == "reconcile_exact_receipt"
    )


def test_rollout_capability_matrix_keeps_effects_and_extensions_closed():
    matrix = _load("rollout-capability-matrix.json")
    slices = {item["slice"]: item for item in matrix["slices"]}

    assert matrix["security_review_commit"].startswith("7da49a1")
    assert len(matrix["default_off_flags"]) == 9
    assert slices["8.0"]["decision"] == "go"
    assert slices["8.1"]["decision"] == "go_compile_only"
    assert all(
        slices[slice_id]["decision"] == "go_signed_r25_lab"
        for slice_id in ("8.2", "8.4", "8.5")
    )
    assert slices["8.3"]["decision"] == "go_automated"
    assert slices["8.6"]["decision"] == "no_go_disabled"
    r25 = matrix["capability_admission"][0]
    assert r25["phase8_write_state"] == "signed_lab"
    assert r25["effective_write"] is True
    assert all(
        item["effective_write"] is False
        for item in matrix["capability_admission"][1:]
    )
    assert all(item["enabled"] is False for item in matrix["extension_packs"])


def test_regression_matrix_references_real_repo_paths():
    matrix = _load("regression-matrix.json")
    suites = matrix["suites"]
    ids = [item["id"] for item in suites]

    assert len(ids) == len(set(ids))
    assert {
        "root-phase0-5",
        "contracts-phase6-7",
        "gateway-phase0-7",
        "desktop-agent-phase4-7",
        "managed-host-core-phase6-7",
        "web-portal-unit-component-phase7",
        "web-portal-e2e-phase7",
        "web-portal-build-phase7",
        "phase8-cross-stack-conformance",
        "phase8-host-json-checkpoint-v2",
    } <= set(ids)
    for suite in suites:
        assert (ROOT / suite["workdir"]).is_dir()
        executable = suite["executable"]
        assert executable in {"uv", "dotnet", "npm"}
        assert suite["evidence_kind"] == "automated"


def test_checkpoint_v1_schema_is_not_misrepresented_as_restore_v2():
    schema_path = (
        ROOT
        / "packages"
        / "host_contracts"
        / "schemas"
        / "cad-phase7-rollback.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    checkpoint = schema["$defs"]["checkpoint"]
    encoded = json.dumps(checkpoint, sort_keys=True).lower()

    assert checkpoint["properties"]["schema_version"]["const"] == (
        "cad.rollback.checkpoint/1"
    )
    assert "created_entities" in checkpoint["properties"]
    assert "pre_image" not in encoded
    assert "restore_descriptor" not in encoded
