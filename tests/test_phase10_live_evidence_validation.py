from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "validate-phase10-live-evidence.py"
SPEC = importlib.util.spec_from_file_location("phase10_live_validator", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _repo(tmp_path: Path) -> Path:
    fixtures = tmp_path / "fixtures" / "phase10" / "live"
    evidence = tmp_path / "docs" / "architecture" / "evidence"
    rows = []
    artifacts = {}
    expected = {
        "a": {
            "features": ["hole", "repeated_hole_pattern"],
            "negative": ["non_pattern_circle_excluded"],
        },
        "b": {
            "features": ["slot", "concentric_group"],
            "negative": [
                "near_slot_excluded",
                "near_concentric_outside_tolerance",
            ],
        },
        "c": {
            "issues": [
                "degenerate_geometry",
                "duplicate_geometry",
                "open_contour",
                "self_intersection",
            ],
            "negative": ["valid_geometry_not_flagged_for_cleanup"],
        },
    }
    commit = "a" * 40
    for name in VALIDATOR.FIXTURES:
        dwg = fixtures / f"phase10-drawing-{name}.dwg"
        dxf = fixtures / f"phase10-drawing-{name}.dxf"
        dwg.parent.mkdir(parents=True, exist_ok=True)
        dwg.write_bytes(f"dwg-{name}".encode())
        dxf.write_bytes(f"dxf-{name}".encode())
        fixture_id = f"phase10-drawing-{name}-r25/1"
        rows.append(
            {
                "dwg": dwg.name,
                "dwg_sha256": _sha(dwg),
                "dxf": dxf.name,
                "dxf_sha256": _sha(dxf),
                "expected": expected[name],
                "fixture_id": fixture_id,
                "independent_source": True,
            }
        )
        gates = {
            VALIDATOR.GATE_ALIASES.get(key, key): True
            for key in VALIDATOR.GENERIC_FIXTURE_GATES
            | {item for values in expected[name].values() for item in values}
        }
        artifacts[name] = {
            "schema_version": "cad.phase10-live-public-fixture/1",
            "status": "PASS",
            "baseline_commit": commit,
            "implementation_commit": commit,
            "failures_retests": [],
            "limitations": [],
            "fixture": {
                "fixture_id": fixture_id,
                "dwg_file_hash_before": _sha(dwg),
                "dwg_file_hash_after": _sha(dwg),
            },
            "gate_results": gates,
            "no_effect": {
                "document_revision_unchanged": True,
                "dwg_file_hash_unchanged": True,
                "entity_count_unchanged": True,
                "write_requested": False,
                "cad_effect_attempted": False,
            },
            "public_path": {
                "device_id": "device-live",
                "standalone_desktop_agent": True,
                "write_tools_invoked": [],
            },
            "scene": {
                "scene_id": "scene-c" if name == "c" else f"scene-{name}",
                "scene_digest": "digest-c" if name == "c" else f"digest-{name}",
                "source_digest": "source-c" if name == "c" else f"source-{name}",
            },
        }
        _write_json(
            evidence / f"phase10-live-r25-drawing-{name}-20260730.json",
            artifacts[name],
        )

    _write_json(
        fixtures / "manifest.json",
        {
            "schema_version": "autocad-mcp.phase10-live-fixtures/1",
            "fixtures": rows,
        },
    )
    common = {
        "status": "PASS",
        "baseline_commit": commit,
        "implementation_commit": commit,
        "failures_retests": [],
        "limitations": [],
        "fixture_id": "phase10-drawing-c-r25/1",
        "scene_id": "scene-c",
        "scene_digest": "digest-c",
        "source_digest": "source-c",
        "write_requested": False,
        "cad_effect_attempted": False,
    }
    _write_json(
        evidence / "phase10-live-cleanup-workflow-20260730.json",
        {
            **common,
            "schema_version": "cad.phase10-live-cleanup-workflow/1",
            "gate_results": {key: True for key in VALIDATOR.CLEANUP_GATES},
            "write_tools_invoked": [],
            "report": {
                "write_authority": False,
                "scene_id": "scene-c",
                "scene_digest": "digest-c",
                "source_digest": "source-c",
            },
        },
    )
    _write_json(
        evidence / "phase10-live-gateway-restart-20260730.json",
        {
            **common,
            "schema_version": "cad.phase10-live-gateway-restart/1",
            "gate_results": {key: True for key in VALIDATOR.RESTART_GATES},
        },
    )
    _write_json(
        evidence / "phase10-live-no-effect-db-20260730.json",
        {
            "schema_version": "cad.phase10-live-db-evidence/1",
            "status": "PASS",
            "baseline_commit": commit,
            "implementation_commit": commit,
            "failures_retests": [],
            "limitations": [],
            "gate_results": {key: True for key in VALIDATOR.NO_EFFECT_DB_GATES},
            "scope": {
                "owner_subject": "owner-live",
                "device_id": "device-live",
                "scene_ids": ["scene-a", "scene-b", "scene-c"],
            },
            "retrospective_no_write_events": [],
            "active_agent_session_id": "session-live",
            "write_snapshot": {"sha256": "sha256:" + "b" * 64},
        },
    )
    return tmp_path


def test_validator_accepts_complete_bound_evidence(tmp_path: Path) -> None:
    VALIDATOR.validate(_repo(tmp_path))
    assert (
        VALIDATOR.GATE_ALIASES["self_intersection"]
        == "invalid_or_self_intersecting_contour"
    )


def test_validator_rejects_fixture_hash_or_cleanup_scene_mismatch(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    (root / "fixtures" / "phase10" / "live" / "phase10-drawing-a.dwg").write_bytes(
        b"tampered"
    )
    with pytest.raises(ValueError, match="DWG hash mismatch"):
        VALIDATOR.validate(root)

    root = _repo(tmp_path)
    cleanup = (
        root
        / "docs"
        / "architecture"
        / "evidence"
        / "phase10-live-cleanup-workflow-20260730.json"
    )
    value = json.loads(cleanup.read_text(encoding="utf-8"))
    value["report"]["scene_id"] = "wrong-scene"
    _write_json(cleanup, value)
    with pytest.raises(ValueError, match="not bound read-only"):
        VALIDATOR.validate(root)
