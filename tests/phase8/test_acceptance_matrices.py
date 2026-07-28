from __future__ import annotations

import json
from pathlib import Path


HERE = Path(__file__).parent
ROOT = HERE.parents[1]


def _load(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def test_cross_stack_matrix_has_executable_evidence_and_explicit_blockers():
    matrix = _load("cross-stack-acceptance.json")
    stages = {item["id"]: item for item in matrix["stages"]}

    assert matrix["schema_version"] == "phase8.cross-stack-acceptance/1"
    assert {
        "canonical-compiler",
        "gateway-sealed-storage",
        "gateway-binding",
        "cad-agent-2-wire",
        "desktop-admission",
        "host-json-contract",
        "intent-consent-release",
        "materialized-evidence-digests",
        "host-dispatch-registration",
        "profile-snapshots",
        "r25-live",
    } == stages.keys()
    assert stages["canonical-compiler"]["status"] == "failing_fixture_mismatch"
    assert stages["gateway-sealed-storage"]["status"] == "blocked_by_canonical_fixture"
    assert stages["gateway-binding"]["status"] == "blocked_by_canonical_fixture"
    assert stages["cad-agent-2-wire"]["status"] == "failing_fixture_mismatch"
    assert stages["desktop-admission"]["status"] == "automated_green"
    assert stages["host-json-contract"]["status"] == "automated_green"
    assert stages["intent-consent-release"]["status"] == "failing_unbound_release"
    assert stages["materialized-evidence-digests"]["status"] == "automated_green"
    assert stages["host-dispatch-registration"]["status"] == "failing_unregistered"
    assert stages["profile-snapshots"]["status"] == "automated_green"
    assert stages["r25-live"]["status"] == "external_live_blocker"
    for stage in stages.values():
        for path in stage["evidence"]:
            assert (ROOT / path).exists(), path


def test_exact_transform_checkpoint_v2_matrix_is_fail_closed_and_complete():
    matrix = _load("transform-checkpoint-v2-matrix.json")
    cases = {item["id"]: item for item in matrix["cases"]}

    assert matrix["schema_version"] == "phase8.transform-checkpoint-v2/1"
    assert matrix["checkpoint_v1_allowed_for_modify"] is False
    assert {
        "move-line-commit-restore",
        "rotate-circle-commit-restore",
        "scale-polyline-commit-restore",
        "drop-before-transform-commit",
        "drop-after-effect-response-lost",
        "checkpoint-payload-tamper",
        "dependency-closure-mismatch",
        "duplicate-exact-transform",
        "duplicate-conflicting-transform",
        "unsupported-custom-entity",
        "lt-transform-negative",
    } <= cases.keys()
    assert all(item["delete_allowed"] is False for item in cases.values())
    assert all(
        item["checkpoint_schema"] == "cad.rollback.checkpoint/2"
        for item in cases.values()
        if item["effect"] == "modify"
    )
    assert cases["lt-transform-negative"]["expected"] == "capability_missing"
    assert cases["unsupported-custom-entity"]["expected"] == "capability_missing"


def test_phase0_7_regression_matrix_keeps_every_prior_surface():
    matrix = _load("regression-matrix.json")
    suites = {item["id"]: item for item in matrix["suites"]}

    expected = {
        "root-phase0-5": "0-5",
        "contracts-phase6-7": "6-7",
        "host-contracts-phase6-7": "6-7",
        "gateway-phase0-7": "0-7",
        "desktop-agent-phase4-7": "4-7",
        "managed-host-core-phase6-7": "6-7",
        "web-portal-unit-component-phase7": "7",
        "web-portal-e2e-phase7": "7",
        "web-portal-build-phase7": "7",
    }
    assert {key: suites[key]["phase_scope"] for key in expected} == expected
    assert "phase8-cross-stack-conformance" in suites
    assert "phase8-host-json-checkpoint-v2" in suites
