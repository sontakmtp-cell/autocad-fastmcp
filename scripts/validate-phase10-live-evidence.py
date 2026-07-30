"""Validate the retained Phase 10 live fixture and no-effect evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


FIXTURES = ("a", "b", "c")
GENERIC_FIXTURE_GATES = {
    "document_revision_unchanged",
    "dwg_file_hash_unchanged",
    "entity_count_unchanged",
    "managed_dotnet_runtime",
    "no_cad_effect_attempted",
    "no_write_requested",
    "source_capabilities_present",
    "source_runtime_managed",
    "stable_scene_reuse",
}
RESTART_GATES = {
    "actual_gateway_process_restart",
    "document_revision_unchanged",
    "dwg_file_hash_unchanged",
    "gateway_public_reconnect",
    "no_cad_effect_attempted",
    "no_write_requested",
    "public_query_succeeded",
    "same_scene_retrieved",
    "standalone_desktop_agent",
}
CLEANUP_GATES = {
    "cleanup_workflow_version",
    "document_revision_unchanged",
    "dwg_file_hash_unchanged",
    "no_cad_effect_attempted",
    "no_write_requested",
    "read_only_report",
    "required_issues_reported",
    "same_scene_reused",
    "same_snapshot_and_revision",
    "workflow_completed",
}
NO_EFFECT_DB_GATES = {
    "active_session_ok",
    "anchor_jobs_ok",
    "foreign_keys_ok",
    "integrity_ok",
    "migrations_ok",
    "no_write_events_in_window",
    "scenes_ok",
}


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_common(
    evidence: dict, *, schema: str, fixture_id: str, label: str
) -> None:
    _require(evidence.get("schema_version") == schema, f"{label}: schema mismatch")
    _require(evidence.get("status") == "PASS", f"{label}: status must be PASS")
    _require(evidence.get("fixture_id", fixture_id) == fixture_id, f"{label}: fixture mismatch")
    for name in ("baseline_commit", "implementation_commit"):
        value = evidence.get(name)
        _require(
            isinstance(value, str)
            and len(value) == 40
            and all(char in "0123456789abcdef" for char in value),
            f"{label}: {name} must be a full Git SHA",
        )
    _require(
        isinstance(evidence.get("failures_retests"), list),
        f"{label}: failures_retests must be explicit",
    )
    _require(
        isinstance(evidence.get("limitations"), list),
        f"{label}: limitations must be explicit",
    )


def _validate_true_gates(evidence: dict, required: set[str], label: str) -> None:
    gates = evidence.get("gate_results")
    _require(isinstance(gates, dict), f"{label}: gate_results must be an object")
    missing = required - gates.keys()
    _require(not missing, f"{label}: missing gates {sorted(missing)}")
    failed = sorted(name for name in required if gates.get(name) is not True)
    _require(not failed, f"{label}: failed gates {failed}")


def validate(root: Path) -> None:
    fixture_root = root / "fixtures" / "phase10" / "live"
    evidence_root = root / "docs" / "architecture" / "evidence"
    manifest = _load(fixture_root / "manifest.json")
    _require(
        manifest.get("schema_version") == "autocad-mcp.phase10-live-fixtures/1",
        "fixture manifest schema mismatch",
    )
    rows = manifest.get("fixtures")
    _require(isinstance(rows, list), "fixture manifest rows are required")
    by_name = {
        row.get("dwg", "").removeprefix("phase10-drawing-").removesuffix(".dwg"): row
        for row in rows
        if isinstance(row, dict)
    }
    _require(set(by_name) == set(FIXTURES), "fixtures must be exactly A, B and C")

    fixture_evidence: dict[str, dict] = {}
    baseline_commit = implementation_commit = None
    for name in FIXTURES:
        row = by_name[name]
        fixture_id = f"phase10-drawing-{name}-r25/1"
        _require(row.get("fixture_id") == fixture_id, f"Drawing {name}: fixture ID mismatch")
        _require(
            row.get("independent_source") is True,
            f"Drawing {name}: source is not independent",
        )
        for key in ("dwg", "dxf"):
            path = fixture_root / str(row.get(key, ""))
            _require(path.is_file(), f"Drawing {name}: missing {key.upper()} fixture")
            _require(
                _digest(path) == row.get(f"{key}_sha256"),
                f"Drawing {name}: {key.upper()} hash mismatch",
            )

        label = f"Drawing {name.upper()}"
        evidence = _load(
            evidence_root / f"phase10-live-r25-drawing-{name}-20260730.json"
        )
        fixture_evidence[name] = evidence
        _validate_common(
            evidence,
            schema="cad.phase10-live-public-fixture/1",
            fixture_id=fixture_id,
            label=label,
        )
        fixture = evidence.get("fixture")
        _require(isinstance(fixture, dict), f"{label}: fixture evidence is required")
        _require(fixture.get("fixture_id") == fixture_id, f"{label}: fixture ID mismatch")
        expected_hash = row["dwg_sha256"]
        _require(
            fixture.get("dwg_file_hash_before")
            == fixture.get("dwg_file_hash_after")
            == expected_hash,
            f"{label}: retained DWG hash does not match manifest",
        )
        expected = row.get("expected")
        _require(isinstance(expected, dict), f"{label}: expected outcomes are required")
        required_gates = GENERIC_FIXTURE_GATES | {
            item
            for values in expected.values()
            for item in values
        }
        _validate_true_gates(evidence, required_gates, label)
        no_effect = evidence.get("no_effect")
        _require(
            isinstance(no_effect, dict)
            and no_effect.get("document_revision_unchanged") is True
            and no_effect.get("dwg_file_hash_unchanged") is True
            and no_effect.get("entity_count_unchanged") is True
            and no_effect.get("write_requested") is False
            and no_effect.get("cad_effect_attempted") is False,
            f"{label}: no-effect proof is incomplete",
        )
        public_path = evidence.get("public_path")
        _require(
            isinstance(public_path, dict)
            and public_path.get("standalone_desktop_agent") is True
            and public_path.get("write_tools_invoked") == [],
            f"{label}: public read-only path is incomplete",
        )
        if baseline_commit is None:
            baseline_commit = evidence["baseline_commit"]
            implementation_commit = evidence["implementation_commit"]
        _require(
            evidence["baseline_commit"] == baseline_commit
            and evidence["implementation_commit"] == implementation_commit,
            f"{label}: commit provenance differs from Drawing A",
        )

    drawing_c = fixture_evidence["c"]
    scene_c = drawing_c["scene"]
    cleanup = _load(
        evidence_root / "phase10-live-cleanup-workflow-20260730.json"
    )
    _validate_common(
        cleanup,
        schema="cad.phase10-live-cleanup-workflow/1",
        fixture_id="phase10-drawing-c-r25/1",
        label="Cleanup workflow",
    )
    _validate_true_gates(cleanup, CLEANUP_GATES, "Cleanup workflow")
    _require(
        cleanup["baseline_commit"] == baseline_commit
        and cleanup["implementation_commit"] == implementation_commit,
        "Cleanup workflow: commit provenance differs from fixture evidence",
    )
    report = cleanup.get("report")
    _require(
        isinstance(report, dict)
        and report.get("write_authority") is False
        and report.get("scene_id") == cleanup.get("scene_id") == scene_c["scene_id"]
        and report.get("scene_digest")
        == cleanup.get("scene_digest")
        == scene_c["scene_digest"]
        and report.get("source_digest")
        == cleanup.get("source_digest")
        == scene_c["source_digest"],
        "Cleanup workflow: report is not bound read-only to Drawing C scene",
    )
    _require(
        cleanup.get("write_requested") is False
        and cleanup.get("cad_effect_attempted") is False
        and cleanup.get("write_tools_invoked") == [],
        "Cleanup workflow: no-effect proof is incomplete",
    )

    restart = _load(
        evidence_root / "phase10-live-gateway-restart-20260730.json"
    )
    _validate_common(
        restart,
        schema="cad.phase10-live-gateway-restart/1",
        fixture_id="phase10-drawing-c-r25/1",
        label="Gateway restart",
    )
    _validate_true_gates(restart, RESTART_GATES, "Gateway restart")
    _require(
        restart["baseline_commit"] == baseline_commit
        and restart["implementation_commit"] == implementation_commit,
        "Gateway restart: commit provenance differs from fixture evidence",
    )
    _require(
        restart.get("scene_id") == scene_c["scene_id"]
        and restart.get("scene_digest") == scene_c["scene_digest"]
        and restart.get("source_digest") == scene_c["source_digest"],
        "Gateway restart: Drawing C scene identity mismatch",
    )
    _require(
        restart.get("write_requested") is False
        and restart.get("cad_effect_attempted") is False,
        "Gateway restart: no-effect proof is incomplete",
    )

    no_effect_db = _load(
        evidence_root / "phase10-live-no-effect-db-20260730.json"
    )
    _require(
        no_effect_db.get("schema_version") == "cad.phase10-live-db-evidence/1",
        "No-effect DB: schema mismatch",
    )
    _require(no_effect_db.get("status") == "PASS", "No-effect DB: status must be PASS")
    _validate_true_gates(no_effect_db, NO_EFFECT_DB_GATES, "No-effect DB")
    _require(
        no_effect_db.get("baseline_commit") == baseline_commit
        and no_effect_db.get("implementation_commit") == implementation_commit,
        "No-effect DB: commit provenance differs from fixture evidence",
    )
    _require(
        isinstance(no_effect_db.get("failures_retests"), list)
        and isinstance(no_effect_db.get("limitations"), list),
        "No-effect DB: failures/retests and limitations must be explicit",
    )
    scope = no_effect_db.get("scope")
    expected_scene_ids = {fixture_evidence[name]["scene"]["scene_id"] for name in FIXTURES}
    _require(
        isinstance(scope, dict)
        and isinstance(scope.get("owner_subject"), str)
        and bool(scope["owner_subject"])
        and scope.get("device_id") == drawing_c["public_path"].get("device_id")
        and set(scope.get("scene_ids", [])) == expected_scene_ids,
        "No-effect DB: owner/device/scene identity is incomplete",
    )
    write_snapshot = no_effect_db.get("write_snapshot")
    snapshot_digest = (
        write_snapshot.get("sha256") if isinstance(write_snapshot, dict) else None
    )
    _require(
        no_effect_db.get("retrospective_no_write_events") == []
        and isinstance(no_effect_db.get("active_agent_session_id"), str)
        and bool(no_effect_db["active_agent_session_id"])
        and isinstance(snapshot_digest, str)
        and snapshot_digest.startswith("sha256:")
        and len(snapshot_digest) == 71,
        "No-effect DB: durable no-write snapshot is incomplete",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    try:
        validate(args.root)
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as error:
        print(f"Phase 10 live evidence validation failed: {error}", file=sys.stderr)
        return 1
    print("Phase 10 live evidence validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
