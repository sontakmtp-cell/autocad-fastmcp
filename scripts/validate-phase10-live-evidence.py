"""Validate the retained Phase 10 live fixture and no-effect evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_CAPTURE_SCRIPT = Path(__file__).resolve().parent / "phase10-live-public-evidence.py"
_SPEC = importlib.util.spec_from_file_location(
    "phase10_live_public_evidence", _CAPTURE_SCRIPT
)
assert _SPEC and _SPEC.loader
CAPTURE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CAPTURE)


FIXTURES = ("a", "b", "c")
SECTIONS = ("nodes", "relations", "contours", "features", "issues", "evidence")
GATE_ALIASES = {
    "self_intersection": "invalid_or_self_intersecting_contour",
}
GENERIC_FIXTURE_GATES = {
    "document_revision_unchanged",
    "dwg_file_hash_unchanged",
    "entity_count_unchanged",
    "managed_dotnet_runtime",
    "no_cad_effect_attempted",
    "no_write_requested",
    "runtime_identity_bound",
    "source_capabilities_present",
    "source_runtime_managed",
    "stable_scene_reuse",
}
RESTART_GATES = {
    "actual_gateway_process_restart",
    "authoritative_gateway_service",
    "document_revision_unchanged",
    "dwg_file_hash_unchanged",
    "gateway_runtime_identity",
    "gateway_public_reconnect",
    "no_cad_effect_attempted",
    "no_write_requested",
    "old_gateway_process_exited",
    "process_identity_bound",
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
    "agent_session_reconnected",
    "anchor_jobs_ok",
    "foreign_keys_ok",
    "integrity_ok",
    "migrations_ok",
    "no_write_events_in_window",
    "scenes_ok",
    "write_snapshot_sha256_unchanged",
    "write_snapshot_tables_unchanged",
}
# Evidence is local to one lab clock; allow only a small, explicit skew.
_VALIDATION_CLOCK_SKEW = timedelta(seconds=5)
_POST_CAPTURE_STATUS_PATHS = {
    "docs/architecture/Phase-10.md",
    "docs/architecture/phase10-conformance-matrix.md",
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


def _git_head(root: Path) -> str:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("Current Git HEAD is unavailable") from error
    _require(
        re.fullmatch(r"[0-9a-f]{40}", head) is not None,
        "Current Git HEAD is invalid",
    )
    return head


def _git_is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise ValueError("Current Git ancestry is unavailable") from error
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise ValueError("Current Git ancestry is unavailable")


def _git_changed_paths(root: Path, earlier: str, later: str) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", earlier, later],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("Current Git change list is unavailable") from error
    return [path for path in completed.stdout.splitlines() if path]


def _validate_same_implementation(
    root: Path, implementation_commit: str, current_head: str
) -> None:
    _require(
        _git_is_ancestor(root, implementation_commit, current_head),
        "Phase 10 live evidence implementation_commit is not an ancestor of current Git HEAD",
    )
    changed_paths = _git_changed_paths(root, implementation_commit, current_head)
    disallowed = sorted(
        path
        for path in changed_paths
        if not (
            path.startswith("docs/architecture/evidence/")
            or path in _POST_CAPTURE_STATUS_PATHS
        )
    )
    _require(
        not disallowed,
        "Phase 10 live evidence implementation changed after capture: "
        + ", ".join(disallowed),
    )


def _normalize_timestamp(text: str) -> str:
    match = re.search(r"\.\d+", text)
    if match:
        digits = match.group(0)[1:]
        normalized = (digits + "000000")[:6]
        text = text[: match.start()] + "." + normalized + text[match.end() :]
    return text


def _time(value: object, label: str) -> datetime:
    _require(isinstance(value, str), f"{label}: timestamp is required")
    # Python 3.10 rejects fractional seconds with more than six digits;
    # pad or truncate any RFC 3339 fraction to microseconds before parsing.
    text = _normalize_timestamp(value.replace("Z", "+00:00"))
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{label}: invalid timestamp") from error
    _require(parsed.tzinfo is not None, f"{label}: timestamp must be timezone-aware")
    return parsed


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _expected_migrations(root: Path) -> list[dict[str, object]]:
    migration_root = (
        root
        / "services"
        / "gateway"
        / "src"
        / "autocad_gateway"
        / "infrastructure"
        / "sqlite"
        / "migrations"
    )
    result = []
    for path in sorted(migration_root.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        result.append(
            {
                "version": int(path.stem.split("_", 1)[0]),
                "checksum": hashlib.sha256(sql.encode()).hexdigest(),
            }
        )
    _require(result, "No-effect DB: local migrations are missing")
    return result


def _validate_scene_semantics(
    evidence: dict, expected: dict, *, name: str, label: str
) -> None:
    try:
        raw = CAPTURE._validate_scene_invariants(evidence)
    except ValueError as error:
        raise ValueError(f"{label}: {error}") from error

    feature_types = {item["feature_type"] for item in raw["features"]}
    issue_codes = {item["code"] for item in raw["issues"]}
    _require(
        set(expected.get("features", [])) <= feature_types
        and set(expected.get("issues", [])) <= issue_codes,
        f"{label}: expected semantics are absent from raw sections",
    )
    if name == "a":
        circles_by_radius = {
            item["node_id"]: item.get("geometry", {}).get("radius")
            for item in raw["nodes"]
            if item.get("geometry", {}).get("kind") == "circle"
        }
        pattern_circle_nodes = {
            node_id for node_id, radius in circles_by_radius.items() if radius == 5.0
        }
        non_pattern_nodes = {
            node_id for node_id, radius in circles_by_radius.items() if radius == 3.0
        }
        hole_nodes = {
            item["source_node_ids"][0]
            for item in raw["features"]
            if item["feature_type"] == "hole" and len(item["source_node_ids"]) == 1
        }
        patterns = [
            set(item["source_node_ids"])
            for item in raw["features"]
            if item["feature_type"] == "repeated_hole_pattern"
        ]
        _require(
            len(patterns) == 1
            and len(non_pattern_nodes) == 1
            and len(pattern_circle_nodes) == 4
            and patterns[0] == pattern_circle_nodes
            and patterns[0] < hole_nodes,
            f"{label}: non-pattern circle exclusion is not proven",
        )
    elif name == "b":
        slots = [
            set(item["source_node_ids"])
            for item in raw["features"]
            if item["feature_type"] == "slot"
        ]
        groups = [
            set(item["source_node_ids"])
            for item in raw["features"]
            if item["feature_type"] == "concentric_group"
        ]
        circle_nodes = {
            item["node_id"] for item in raw["nodes"] if item["entity_type"] == "CIRCLE"
        }
        near_slot_nodes = {
            item["node_id"]
            for item in raw["nodes"]
            if item.get("geometry", {}).get("kind") == "polyline"
            and item["geometry"].get("closed") is True
            and item["geometry"].get("vertices")
            == [
                {"x": 70.0, "y": -5.0},
                {"x": 90.0, "y": -5.0},
                {"x": 90.0, "y": 5.0},
                {"x": 70.0, "y": 5.0},
            ]
        }
        exact_concentric_nodes = {
            item["node_id"]
            for item in raw["nodes"]
            if item.get("geometry", {}).get("kind") == "circle"
            and item["geometry"].get("center") == {"x": 50.0, "y": 0.0}
            and item["geometry"].get("radius") in {3.0, 6.0}
        }
        near_concentric_nodes = {
            item["node_id"]
            for item in raw["nodes"]
            if item.get("geometry", {}).get("kind") == "circle"
            and item["geometry"].get("center") == {"x": 50.1, "y": 0.0}
            and item["geometry"].get("radius") == 9.0
        }
        _require(
            len(slots) == len(groups) == 1
            and len(near_slot_nodes) == len(near_concentric_nodes) == 1
            and len(slots[0]) == 4
            and near_slot_nodes.isdisjoint(slots[0])
            and groups[0] == exact_concentric_nodes
            and near_concentric_nodes.isdisjoint(groups[0])
            and groups[0] < circle_nodes,
            f"{label}: tolerance-negative feature exclusions are not proven",
        )
    else:
        nodes_by_id = {item["node_id"]: item for item in raw["nodes"]}
        issue_nodes_by_code = {
            code: {
                node_id
                for item in raw["issues"]
                if item["code"] == code
                for node_id in item["source_node_ids"]
            }
            for code in {item["code"] for item in raw["issues"]}
        }
        zero_length_nodes = {
            node_id
            for node_id, item in nodes_by_id.items()
            if item.get("geometry", {}).get("kind") == "line"
            and item["geometry"].get("start") == item["geometry"].get("end")
            == {"x": 20.0, "y": 0.0}
        }
        duplicate_nodes = {
            node_id
            for node_id, item in nodes_by_id.items()
            if item.get("geometry", {}).get("kind") == "line"
            and {
                (
                    item["geometry"].get("start", {}).get("x"),
                    item["geometry"].get("start", {}).get("y"),
                ),
                (
                    item["geometry"].get("end", {}).get("x"),
                    item["geometry"].get("end", {}).get("y"),
                ),
            }
            == {(0.0, 0.0), (10.0, 0.0)}
        }
        bowtie_nodes = {
            node_id
            for node_id, item in nodes_by_id.items()
            if item.get("geometry", {}).get("kind") == "polyline"
            and item["geometry"].get("vertices")
            == [
                {"x": 55.0, "y": 0.0},
                {"x": 65.0, "y": 10.0},
                {"x": 55.0, "y": 10.0},
                {"x": 65.0, "y": 0.0},
            ]
        }
        open_chain_nodes = {
            node_id
            for node_id, item in nodes_by_id.items()
            if item.get("geometry", {}).get("kind") == "line"
            and {
                (
                    item["geometry"].get("start", {}).get("x"),
                    item["geometry"].get("start", {}).get("y"),
                ),
                (
                    item["geometry"].get("end", {}).get("x"),
                    item["geometry"].get("end", {}).get("y"),
                ),
            }
            in (
                {(30.0, 0.0), (40.0, 0.0)},
                {(40.0, 0.0), (45.0, 5.0)},
            )
        }
        valid_nodes = {
            node_id
            for node_id, item in nodes_by_id.items()
            if (
                item.get("geometry", {}).get("kind") == "circle"
                and item["geometry"].get("center") == {"x": 95.0, "y": 5.0}
                and item["geometry"].get("radius") == 2.0
            )
            or (
                item.get("geometry", {}).get("kind") == "polyline"
                and item["geometry"].get("vertices")
                == [
                    {"x": 85.0, "y": 0.0},
                    {"x": 105.0, "y": 0.0},
                    {"x": 105.0, "y": 10.0},
                    {"x": 85.0, "y": 10.0},
                ]
            )
        }
        issue_nodes = {
            node_id for item in raw["issues"] for node_id in item["source_node_ids"]
        }
        hole_nodes = {
            node_id
            for item in raw["features"]
            if item["feature_type"] == "hole"
            for node_id in item["source_node_ids"]
        }
        closed_polyline_nodes = {
            item["node_id"]
            for item in raw["nodes"]
            if item.get("geometry", {}).get("kind") == "polyline"
            and item["geometry"].get("closed") is True
        }
        _require(
            all(item.get("write_authority") is False for item in raw["issues"])
            and len(zero_length_nodes) == 1
            and issue_nodes_by_code.get("degenerate_geometry") == zero_length_nodes
            and len(duplicate_nodes) == 2
            and issue_nodes_by_code.get("duplicate_geometry") == duplicate_nodes
            and len(bowtie_nodes) == 1
            and issue_nodes_by_code.get("self_intersection") == bowtie_nodes
            and len(open_chain_nodes) == 2
            and issue_nodes_by_code.get("open_contour")
            == open_chain_nodes | duplicate_nodes
            and len(valid_nodes) == 2
            and valid_nodes.isdisjoint(issue_nodes)
            and bool(hole_nodes - issue_nodes)
            and bool(closed_polyline_nodes - issue_nodes),
            f"{label}: valid geometry exclusion/read-only issues are not proven",
        )


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


def _validate_gateway_restart_processes(
    before: object, after: object, implementation_commit: str
) -> tuple[dict, dict]:
    _require(
        isinstance(before, dict) and isinstance(after, dict),
        "Gateway restart: raw process records are required",
    )
    old_service = before.get("gateway_service_record")
    new_service = after.get("gateway_service_record")
    _require(
        isinstance(old_service, dict) and isinstance(new_service, dict),
        "Gateway restart: authoritative service records are required",
    )
    old_properties = old_service.get("properties")
    new_properties = new_service.get("properties")
    old_process = old_service.get("process")
    new_process = new_service.get("process")
    old_release = old_service.get("release")
    new_release = new_service.get("release")
    exit_proof = after.get("old_gateway_process_exit")
    _require(
        all(
            isinstance(value, dict)
            for value in (
                old_properties,
                new_properties,
                old_process,
                new_process,
                old_release,
                new_release,
                exit_proof,
            )
        ),
        "Gateway restart: raw service/process/exit records are incomplete",
    )

    old_pid = old_properties.get("MainPID")
    new_pid = new_properties.get("MainPID")
    old_start = old_process.get("start_identity")
    new_start = new_process.get("start_identity")
    _require(
        isinstance(old_pid, int)
        and isinstance(new_pid, int)
        and old_pid > 0
        and new_pid > 0
        and old_pid != new_pid
        and isinstance(old_start, str)
        and bool(old_start)
        and isinstance(new_start, str)
        and bool(new_start)
        and old_start != new_start
        and old_process.get("source") == new_process.get("source") == "procfs"
        and old_process.get("pid") == old_pid
        and new_process.get("pid") == new_pid
        and before.get("gateway_pid") == old_pid
        and after.get("gateway_pid") == new_pid,
        "Gateway restart: process/start identities do not prove a restart",
    )
    _require(
        exit_proof.get("source") == "procfs"
        and exit_proof.get("pid") == old_pid
        and exit_proof.get("start_identity") == old_start
        and "proc_stat_after" in exit_proof
        and exit_proof.get("proc_stat_after") is None
        and exit_proof.get("probe_exit_code") == 0,
        "Gateway restart: old process exit is not proven from procfs",
    )
    _require(
        old_service.get("source") == new_service.get("source") == "systemctl_show"
        and old_properties.get("Id")
        == new_properties.get("Id")
        == "autocad-mcp-phase4.service"
        and new_properties.get("ActiveState") == "active"
        and new_properties.get("SubState") == "running"
        and isinstance(
            old_properties.get("ExecMainStartTimestampMonotonic"), str
        )
        and bool(old_properties["ExecMainStartTimestampMonotonic"])
        and isinstance(
            new_properties.get("ExecMainStartTimestampMonotonic"), str
        )
        and bool(new_properties["ExecMainStartTimestampMonotonic"])
        and old_properties["ExecMainStartTimestampMonotonic"]
        != new_properties["ExecMainStartTimestampMonotonic"],
        "Gateway restart: authoritative systemd service restart is not proven",
    )

    _require(
        CAPTURE._service_restart_gates(
            before,
            after,
            implementation_commit=implementation_commit,
        )["gateway_runtime_identity"],
        "Gateway restart: runtime/release identity is not proven",
    )
    return before, after


def _validate_restart_raw_captures(
    restart: dict, before: dict, after: dict
) -> None:
    identity_before = restart.get("identity_capture_before")
    identity_after = restart.get("identity_capture_after")
    _require(
        isinstance(identity_before, dict) and isinstance(identity_after, dict),
        "Gateway restart: raw identity captures are required",
    )
    derived_before = CAPTURE._derive_gateway_identity(identity_before)
    derived_after = CAPTURE._derive_gateway_identity(identity_after)
    _require(
        derived_before["gateway_service_record"]
        == before.get("gateway_service_record"),
        "Gateway restart: before service record does not match raw capture",
    )
    _require(
        derived_after["gateway_service_record"]
        == after.get("gateway_service_record"),
        "Gateway restart: after service record does not match raw capture",
    )
    _require(
        before.get("gateway_pid") == derived_before["gateway_pid"]
        and after.get("gateway_pid") == derived_after["gateway_pid"],
        "Gateway restart: gateway PID does not match raw capture",
    )
    exit_proof = after.get("old_gateway_process_exit")
    exit_probe = derived_after.get("exit_probe")
    _require(
        isinstance(exit_proof, dict)
        and isinstance(exit_probe, dict)
        and exit_probe.get("command") == exit_proof.get("command")
        and exit_probe.get("pid") == derived_before["gateway_pid"]
        and exit_proof.get("pid") == derived_before["gateway_pid"],
        "Gateway restart: old process exit probe does not match raw capture",
    )
    _require(
        CAPTURE._parse_timestamp(
            identity_before.get("captured_at"), "identity-before captured_at"
        )
        < CAPTURE._parse_timestamp(
            identity_after.get("captured_at"), "identity-after captured_at"
        ),
        "Gateway restart: raw identity capture ordering is invalid",
    )


def validate(root: Path) -> None:
    validation_time = datetime.now(timezone.utc) + _VALIDATION_CLOCK_SKEW
    current_head = _git_head(root)
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
    fixture_sessions: dict[str, str] = {}
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
        _validate_scene_semantics(evidence, expected, name=name, label=label)
        required_gates = GENERIC_FIXTURE_GATES | {
            GATE_ALIASES.get(item, item)
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
            and public_path.get("standalone_desktop_agent") is True,
            f"{label}: public read-only path is incomplete",
        )
        source = evidence.get("source")
        scene = evidence["scene"]
        fixture_device = public_path.get("device_id")
        _require(
            isinstance(source, dict)
            and source.get("snapshot_id") == scene.get("source_snapshot_id")
            and fixture.get("document_id") == scene.get("document_id")
            and fixture_device == scene.get("device_id")
            and source.get("document_revision_before")
            == source.get("document_revision_after")
            == scene.get("document_revision")
            and source.get("entity_count_before")
            == source.get("entity_count_after")
            == scene.get("counts", {}).get("nodes"),
            f"{label}: source/scene no-effect binding is inconsistent",
        )
        observation_job_ids = []
        for moment in ("observation_before", "observation_after"):
            observation = source.get(moment)
            job = observation.get("job") if isinstance(observation, dict) else None
            request = (
                observation.get("request") if isinstance(observation, dict) else None
            )
            _require(
                isinstance(job, dict)
                and isinstance(request, dict)
                and job.get("job_id") == request.get("job_id")
                and job.get("device_id") == request.get("device_id") == fixture_device
                and job.get("snapshot_id") == request.get("snapshot_id")
                and job.get("state") == "succeeded"
                and job.get("kind") == "observe"
                and request.get("document_revision") == scene.get("document_revision")
                and request.get("entity_count") == scene.get("counts", {}).get("nodes"),
                f"{label}: {moment} is not a bound successful read observation",
            )
            observation_job_ids.append(job["job_id"])
        _require(
            len(set(observation_job_ids)) == 2
            and source["observation_before"]["request"]["snapshot_id"]
            == source["snapshot_id"],
            f"{label}: public observation identities/tools are incomplete",
        )
        captured_at = _time(evidence.get("captured_at"), label)
        finalization = evidence.get("finalization")
        finalized_at = (
            _time(finalization.get("finalized_at"), f"{label}: finalized_at")
            if isinstance(finalization, dict)
            else None
        )
        _require(
            isinstance(finalization, dict)
            and finalization.get("implementation_commit")
            == evidence["implementation_commit"]
            and finalized_at is not None
            and finalized_at >= captured_at,
            f"{label}: finalization provenance is invalid",
        )
        _require(
            finalized_at <= validation_time,
            f"{label}: finalization is dated in the future",
        )
        invocations = evidence.get("public_path", {}).get("tool_invocations")
        try:
            CAPTURE._validate_invocation_graph(
                invocations,
                observation_job_ids=observation_job_ids,
                scene_id=scene.get("scene_id"),
                source_snapshot_id=scene.get("source_snapshot_id"),
                device_id=fixture_device,
                fixture_id=fixture.get("fixture_id"),
            )
        except ValueError as error:
            raise ValueError(f"{label}: {error}") from error
        first_invocation = _time(
            invocations[0]["started_at"], f"{label}: first invocation started"
        )
        _require(
            all(
                _time(item["completed_at"], f"{label}: invocation completed")
                <= captured_at
                for item in invocations
            )
            and all(
                item.get("tool") not in CAPTURE.WRITE_TOOLS
                for item in invocations
            ),
            f"{label}: public tool invocation records are incomplete",
        )
        _require(
            evidence.get("public_path", {}).get("invoked_tools")
            == [item["tool"] for item in invocations],
            f"{label}: invoked_tools does not match tool_invocations",
        )
        derived_write_tools = sorted(
            {
                item["tool"]
                for item in invocations
                if item["tool"] in CAPTURE.WRITE_TOOLS
            }
        )
        _require(
            evidence.get("public_path", {}).get("write_tools_invoked")
            == derived_write_tools,
            f"{label}: write_tools_invoked does not match tool_invocations",
        )
        identity = evidence.get("runtime_identity")
        _require(
            isinstance(identity, dict),
            f"{label}: runtime identity is required",
        )
        for part_name, value, keys in (
            ("gateway_process", identity.get("gateway_process"),
             CAPTURE._GATEWAY_PROCESS_KEYS),
            ("desktop_agent_process", identity.get("desktop_agent_process"),
             CAPTURE._DESKTOP_AGENT_KEYS),
            ("autocad_process", identity.get("autocad_process"),
             CAPTURE._AUTOCAD_PROCESS_KEYS),
            ("agent_session", identity.get("agent_session"),
             CAPTURE._AGENT_SESSION_KEYS),
        ):
            _require(
                isinstance(value, dict) and set(value) == keys,
                f"{label}: runtime identity {part_name} is incomplete",
            )
        gateway = identity["gateway_process"]
        desktop = identity["desktop_agent_process"]
        autocad = identity["autocad_process"]
        session = identity["agent_session"]
        managed_host = session.get("managed_host")
        _require(
            isinstance(gateway["process_id"], int)
            and gateway["process_id"] > 0
            and isinstance(gateway["executable"], str)
            and gateway["executable"].startswith("/")
            and CAPTURE._SHA256_RE.fullmatch(gateway["executable_sha256"])
            and gateway["service"] == CAPTURE.SERVICE_UNIT
            and re.fullmatch(r"[0-9a-f]{40}", gateway["release_commit"])
            and gateway["release_commit"] == evidence["implementation_commit"]
            and isinstance(gateway["working_directory"], str)
            and evidence["implementation_commit"][:7]
            in gateway["working_directory"],
            f"{label}: gateway runtime identity is not cross-bound",
        )
        _require(
            isinstance(desktop["process_id"], int)
            and desktop["process_id"] > 0
            and isinstance(desktop["executable"], str)
            and bool(desktop["executable"])
            and CAPTURE._SHA256_RE.fullmatch(desktop["executable_sha256"])
            and desktop.get("standalone") is True,
            f"{label}: desktop agent runtime identity is invalid",
        )
        _require(
            isinstance(autocad["process_id"], int)
            and autocad["process_id"] > 0
            and isinstance(autocad["executable"], str)
            and bool(autocad["executable"]),
            f"{label}: AutoCAD runtime identity is invalid",
        )
        _require(
            CAPTURE._SHA256_RE.fullmatch(autocad["executable_sha256"])
            and _time(
                autocad["started_at"], f"{label}: autocad started_at"
            )
            <= first_invocation,
            f"{label}: AutoCAD executable hash/start identity is invalid",
        )
        _require(
            isinstance(managed_host, dict)
            and set(managed_host) == CAPTURE._MANAGED_HOST_KEYS
            and managed_host.get("runtime_id") == "managed_dotnet"
            and isinstance(managed_host["process_id"], int)
            and managed_host["process_id"] > 0
            and isinstance(managed_host["executable"], str)
            and bool(managed_host["executable"])
            and CAPTURE._SHA256_RE.fullmatch(managed_host["executable_sha256"])
            and _time(
                managed_host["started_at"], f"{label}: host started_at"
            )
            <= first_invocation,
            f"{label}: managed host runtime identity is invalid",
        )
        runtime_evidence = (
            source.get("observation_before", {}).get("job", {}).get(
                "runtime_evidence", {}
            ).get("runtime", {})
        )
        _require(
            isinstance(runtime_evidence, dict)
            and managed_host.get("package_hash")
            == runtime_evidence.get("package_hash")
            and managed_host.get("package_id")
            == runtime_evidence.get("package_id")
            and managed_host.get("package_version")
            == runtime_evidence.get("package_version")
            and managed_host.get("framework")
            == runtime_evidence.get("framework"),
            f"{label}: managed host is not bound to observation runtime evidence",
        )
        _require(
            session.get("device_id") == fixture_device
            and isinstance(session.get("session_id"), str)
            and bool(session["session_id"])
            and session.get("protocol_version") == "cad.agent/2"
            and session.get("disconnected_at") is None,
            f"{label}: agent session runtime identity is invalid",
        )
        _require(
            _time(
                session["connected_at"], f"{label}: session connected_at"
            )
            <= first_invocation
            and _time(
                desktop["started_at"], f"{label}: desktop started_at"
            )
            <= first_invocation,
            f"{label}: runtime identity timestamps are not before capture",
        )
        fixture_sessions[name] = session["session_id"]
        binding = evidence.get("session_binding")
        _require(
            isinstance(binding, dict)
            and binding.get("session_id") == session["session_id"]
            and binding.get("device_id") == fixture_device
            and binding.get("document_id") == scene.get("document_id")
            and binding.get("document_revision") == scene.get("document_revision")
            and binding.get("scene_id") == scene.get("scene_id")
            and binding.get("observation_job_ids") == observation_job_ids
            and _time(binding.get("captured_at"), f"{label}: binding captured_at")
            <= captured_at,
            f"{label}: session binding is not cross-bound",
        )
        if baseline_commit is None:
            baseline_commit = evidence["baseline_commit"]
            implementation_commit = evidence["implementation_commit"]
        _require(
            evidence["baseline_commit"] == baseline_commit
            and evidence["implementation_commit"] == implementation_commit,
            f"{label}: commit provenance differs from Drawing A",
        )

    _validate_same_implementation(root, implementation_commit, current_head)

    for field, label in (
        (("fixture", "dwg_file_hash_before"), "DWG hashes"),
        (("fixture", "document_id"), "document identities"),
        (("source", "snapshot_id"), "snapshot identities"),
        (("scene", "scene_id"), "scene identities"),
        (("scene", "scene_digest"), "scene digests"),
        (("scene", "source_digest"), "source digests"),
    ):
        values = {
            fixture_evidence[name][field[0]][field[1]] for name in FIXTURES
        }
        _require(len(values) == len(FIXTURES), f"A/B/C: {label} are not distinct")

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
    cleanup_captured_at = _time(
        cleanup.get("captured_at"), "Cleanup workflow: captured_at"
    )
    _require(
        cleanup_captured_at <= validation_time,
        "Cleanup workflow: capture is dated in the future",
    )
    _validate_true_gates(cleanup, CLEANUP_GATES, "Cleanup workflow")
    _require(
        cleanup["baseline_commit"] == baseline_commit
        and cleanup["implementation_commit"] == implementation_commit,
        "Cleanup workflow: commit provenance differs from fixture evidence",
    )
    report = cleanup.get("report")
    raw_c_issues = scene_c["sections"]["issues"]["items"]
    raw_c_issue_codes = sorted({item["code"] for item in raw_c_issues})
    _require(
        isinstance(report, dict)
        and report.get("write_authority") is False
        and report.get("scene_id") == cleanup.get("scene_id") == scene_c["scene_id"]
        and report.get("scene_digest")
        == cleanup.get("scene_digest")
        == scene_c["scene_digest"]
        and report.get("source_digest")
        == cleanup.get("source_digest")
        == scene_c["source_digest"]
        and report.get("source_snapshot_id")
        == cleanup.get("source_snapshot_id")
        == scene_c["source_snapshot_id"]
        and report.get("document_revision")
        == cleanup.get("document_revision")
        == scene_c["document_revision"]
        and report.get("issue_codes") == raw_c_issue_codes
        and report.get("issue_count") == len(raw_c_issues)
        and report.get("validation_ok") is True,
        "Cleanup workflow: report is not bound read-only to Drawing C scene",
    )
    started = cleanup.get("started")
    completed = cleanup.get("completed")
    final = cleanup.get("final")
    final_run = final.get("run") if isinstance(final, dict) else None
    final_steps = final.get("steps") if isinstance(final, dict) else None
    final_report = next(
        (
            step.get("output_ref", {}).get("result")
            for step in final_steps
            if isinstance(step, dict) and step.get("step_id") == "report"
        ),
        None,
    ) if isinstance(final_steps, list) else None
    _require(
        isinstance(started, dict)
        and isinstance(completed, dict)
        and isinstance(final_run, dict)
        and started.get("run_id")
        == completed.get("run_id")
        == final_run.get("run_id")
        and started.get("state") == "waiting_for_user"
        and completed.get("state") == final_run.get("state") == "succeeded"
        and final_run.get("skill_id") == "drawing.cleanup-audit"
        and final_run.get("skill_version") == "1.1.0"
        and final_run.get("device_id") == cleanup.get("device_id")
        and final_run.get("owner_subject")
        and final_run.get("initial_snapshot_id") == cleanup.get("source_snapshot_id")
        and final_run.get("initial_document_id") == cleanup.get("document_id")
        and final_run.get("initial_document_revision")
        == cleanup.get("document_revision")
        and final_report == report
        and {
            step.get("step_id")
            for step in final_steps
            if isinstance(step, dict) and step.get("state") == "succeeded"
        }
        == {
            "build_scene",
            "query_scene",
            "validate_scene",
            "report",
            "review",
            "finish",
        },
        "Cleanup workflow: durable run/steps do not prove completion",
    )
    _require(
        cleanup.get("write_requested") is False
        and cleanup.get("cad_effect_attempted") is False
        and cleanup.get("write_tools_invoked") == []
        and cleanup.get("invoked_tools")
        == ["cad_start_workflow", "cad_get_workflow", "cad_control_workflow"],
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
    before = restart.get("gateway_process_before")
    after = restart.get("gateway_process_after")
    before, after = _validate_gateway_restart_processes(
        before, after, implementation_commit
    )
    _validate_restart_raw_captures(restart, before, after)
    desktop_c = drawing_c.get("runtime_identity", {}).get(
        "desktop_agent_process", {}
    )
    _require(
        isinstance(desktop_c, dict)
        and before.get("desktop_agent_pid") == after.get("desktop_agent_pid")
        and before.get("desktop_agent_executable")
        == after.get("desktop_agent_executable")
        == desktop_c.get("executable")
        and before.get("desktop_agent_sha256")
        == after.get("desktop_agent_sha256")
        == desktop_c.get("executable_sha256"),
        "Gateway restart: standalone Agent identity differs from Drawing C",
    )
    CAPTURE._validate_restart_runtime_artifact(
        drawing_c,
        restart,
        device_id=drawing_c["public_path"]["device_id"],
        implementation_commit=implementation_commit,
    )
    _require(
        before.get("agent_session_id") != after.get("agent_session_id")
        and all(
            before.get(key) == after.get(key)
            for key in (
                "desktop_agent_executable",
                "desktop_agent_pid",
                "desktop_agent_sha256",
                "autocad_pid",
                "device_id",
                "active_document_id",
                "active_document_revision",
                "fixture_id",
                "document_id",
                "document_revision",
                "scene_id",
                "scene_digest",
                "source_digest",
            )
        )
        and before.get("device_id") == drawing_c["public_path"]["device_id"]
        and before.get("active_document_id") == scene_c["document_id"]
        and before.get("active_document_revision") == scene_c["document_revision"]
        and before.get("fixture_id") == "phase10-drawing-c-r25/1"
        and before.get("document_id") == scene_c["document_id"]
        and before.get("document_revision") == scene_c["document_revision"]
        and before.get("scene_id") == scene_c["scene_id"]
        and before.get("scene_digest") == scene_c["scene_digest"]
        and before.get("source_digest") == scene_c["source_digest"]
        and _time(before.get("captured_at"), "Gateway restart before")
        < _time(after.get("captured_at"), "Gateway restart after")
        < _time(restart.get("captured_at"), "Gateway restart"),
        "Gateway restart: process/session continuity is not proven",
    )
    post_sections = restart.get("post_restart_sections")
    _require(
        isinstance(post_sections, dict) and set(post_sections) == set(SECTIONS),
        "Gateway restart: post-restart sections are incomplete",
    )
    for section in SECTIONS:
        post = post_sections[section]
        original = scene_c["sections"][section]
        _require(
            isinstance(post, dict)
            and post.get("scene_id") == scene_c["scene_id"]
            and post.get("scene_digest") == scene_c["scene_digest"]
            and post.get("section") == section
            and post.get("items") == original.get("items")
            and post.get("total") == original.get("total")
            and post.get("next_cursor") is None,
            f"Gateway restart: {section} payload changed",
        )
    summary = restart.get("post_restart_summary_resource")
    _require(
        isinstance(summary, dict)
        and all(
            summary.get(key) == scene_c.get(key)
            for key in (
                "scene_id",
                "scene_digest",
                "source_digest",
                "source_snapshot_id",
                "document_id",
                "document_revision",
                "device_id",
                "counts",
                "complete",
            )
        ),
        "Gateway restart: summary resource changed",
    )
    restart_public_path = restart.get("post_restart_public_path")
    _require(
        isinstance(restart_public_path, dict),
        "Gateway restart: post-restart public invocation evidence is missing",
    )
    restart_invocations = restart_public_path.get("tool_invocations")
    no_effect_db = _load(
        evidence_root / "phase10-live-no-effect-db-20260730.json"
    )
    try:
        restart_write_tools = CAPTURE._validate_restart_invocations(
            restart_invocations, scene_id=scene_c["scene_id"]
        )
    except ValueError as error:
        raise ValueError(f"Gateway restart: {error}") from error
    _require(
        restart_public_path.get("invoked_tools")
        == [item["tool"] for item in restart_invocations]
        and restart_public_path.get("write_tools_invoked")
        == restart_write_tools
        == [],
        "Gateway restart: post-restart invocation summaries are invalid",
    )
    _require(
        restart.get("write_requested") is False
        and restart.get("cad_effect_attempted") is False,
        "Gateway restart: no-effect proof is incomplete",
    )
    finalization = restart.get("finalization")
    _require(
        isinstance(finalization, dict)
        and finalization.get("implementation_commit") == implementation_commit
        and isinstance(finalization.get("finalized_at"), str),
        "Gateway restart: finalization provenance is invalid",
    )
    _require(
        _time(finalization["finalized_at"], "Gateway restart: finalized_at")
        <= validation_time,
        "Gateway restart: finalization is dated in the future",
    )
    _require(
        _time(restart.get("captured_at"), "Gateway restart: captured_at")
        <= validation_time,
        "Gateway restart: capture is dated in the future",
    )
    _require(
        _time(restart.get("captured_at"), "Gateway restart: captured_at")
        <= _time(finalization["finalized_at"], "Gateway restart: finalized_at"),
        "Gateway restart: capture occurred after finalization",
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
    for name, evidence in fixture_evidence.items():
        try:
            CAPTURE._bind_no_effect_db(
                evidence,
                no_effect_db,
                device_id=evidence["public_path"]["device_id"],
                implementation_commit=implementation_commit,
                finalized_at=evidence["finalization"]["finalized_at"],
            )
        except ValueError as error:
            raise ValueError(f"Drawing {name.upper()}: {error}") from error
    _require(
        _time(no_effect_db.get("captured_at"), "No-effect DB: captured_at")
        <= validation_time,
        "No-effect DB: capture is dated in the future",
    )
    try:
        CAPTURE._validate_restart_window(
            restart_invocations,
            no_effect_db,
            gateway_after_at=restart["gateway_process_after"]["captured_at"],
            identity_after_at=restart["identity_capture_after"]["captured_at"],
            restart_captured_at=restart["captured_at"],
            finalized_at=restart["finalization"]["finalized_at"],
        )
    except ValueError as error:
        raise ValueError(f"Gateway restart: {error}") from error
    _require(
        isinstance(no_effect_db.get("failures_retests"), list)
        and isinstance(no_effect_db.get("limitations"), list),
        "No-effect DB: failures/retests and limitations must be explicit",
    )
    scope = no_effect_db.get("scope")
    expected_scene_ids = {fixture_evidence[name]["scene"]["scene_id"] for name in FIXTURES}
    expected_job_bindings = {}
    for name in FIXTURES:
        source = fixture_evidence[name]["source"]
        for moment in ("observation_before", "observation_after"):
            observation = source[moment]
            expected_job_bindings[observation["job"]["job_id"]] = {
                "snapshot_id": observation["request"]["snapshot_id"],
                "document_id": fixture_evidence[name]["scene"]["document_id"],
                "document_revision": fixture_evidence[name]["scene"][
                    "document_revision"
                ],
                "entity_count": fixture_evidence[name]["scene"]["counts"]["nodes"],
            }
    _require(
        isinstance(scope, dict)
        and isinstance(scope.get("owner_subject"), str)
        and bool(scope["owner_subject"])
        and scope.get("device_id") == drawing_c["public_path"].get("device_id")
        and set(scope.get("scene_ids", [])) == expected_scene_ids,
        "No-effect DB: owner/device/scene identity is incomplete",
    )
    db_session_ids = {
        item.get("session_id")
        for item in no_effect_db.get("agent_sessions", [])
        if isinstance(item, dict)
    }
    _require(
        set(fixture_sessions.values()) <= db_session_ids,
        "No-effect DB: fixture runtime sessions are absent from session history",
    )
    window_start = _time(scope.get("window_start"), "No-effect DB window start")
    window_end = _time(scope.get("window_end"), "No-effect DB window end")
    _require(window_start < window_end, "No-effect DB: invalid audit window")
    db_captured_at = _time(
        no_effect_db.get("captured_at"), "No-effect DB captured_at"
    )
    _require(
        window_end <= db_captured_at,
        "No-effect DB: evidence was captured before the audit window closed",
    )
    db_session_records = {
        item.get("session_id"): item
        for item in no_effect_db.get("agent_sessions", [])
        if isinstance(item, dict)
    }
    for name in FIXTURES:
        evidence = fixture_evidence[name]
        captured_at = _time(evidence["captured_at"], f"Drawing {name.upper()} captured_at")
        _require(
            window_start <= captured_at <= window_end,
            f"No-effect DB: Drawing {name.upper()} capture is outside the audit window",
        )
        session_id = fixture_sessions[name]
        session_record = db_session_records[session_id]
        disconnected_at = session_record.get("disconnected_at")
        runtime_session = fixture_evidence[name]["runtime_identity"][
            "agent_session"
        ]
        _require(
            _time(
                session_record.get("connected_at"),
                f"No-effect DB: Drawing {name.upper()} session connected_at",
            )
            == _time(
                runtime_session["connected_at"],
                f"No-effect DB: Drawing {name.upper()} runtime connected_at",
            )
            and _time(
                session_record.get("connected_at"),
                f"No-effect DB: Drawing {name.upper()} session connected_at",
            )
            <= captured_at
            and (
                disconnected_at is None
                or captured_at
                <= _time(
                    disconnected_at,
                    f"No-effect DB: Drawing {name.upper()} session disconnected_at",
                )
            ),
            f"No-effect DB: Drawing {name.upper()} session was not active at capture",
        )
        _require(
            _time(
                session_record.get("connected_at"),
                f"No-effect DB: Drawing {name.upper()} session connected_at",
            )
            <= db_captured_at
            and (
                disconnected_at is None
                or _time(
                    disconnected_at,
                    f"No-effect DB: Drawing {name.upper()} session disconnected_at",
                )
                <= db_captured_at
            ),
            f"No-effect DB: Drawing {name.upper()} session timestamp exceeds "
            "the DB capture time",
        )
    anchor_jobs = no_effect_db.get("anchor_jobs")
    _require(
        isinstance(anchor_jobs, list)
        and len(anchor_jobs) == len(expected_job_bindings)
        and {job.get("job_id") for job in anchor_jobs}
        == set(scope.get("anchor_job_ids", []))
        == set(expected_job_bindings),
        "No-effect DB: anchor job identities are incomplete",
    )
    for job in anchor_jobs:
        binding = expected_job_bindings[job["job_id"]]
        try:
            result = json.loads(job["result_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("No-effect DB: anchor job result is invalid") from error
        snapshot = result.get("snapshot")
        drawing = snapshot.get("drawing") if isinstance(snapshot, dict) else None
        _require(
            job.get("owner_subject") == scope["owner_subject"]
            and job.get("device_id") == scope["device_id"]
            and job.get("effect_class") == "read"
            and job.get("kind") == "observe"
            and job.get("state") == "succeeded"
            and window_start
            <= _time(job.get("created_at"), "No-effect DB anchor created")
            <= _time(job.get("updated_at"), "No-effect DB anchor updated")
            <= window_end
            and _time(job.get("updated_at"), "No-effect DB anchor updated")
            <= db_captured_at
            and isinstance(snapshot, dict)
            and isinstance(drawing, dict)
            and snapshot.get("snapshot_id") == binding["snapshot_id"]
            and snapshot.get("document_revision") == binding["document_revision"]
            and drawing.get("document_id") == binding["document_id"]
            and drawing.get("entity_count") == binding["entity_count"],
            "No-effect DB: anchor job is not cross-bound to fixture evidence",
        )

    db_scenes = no_effect_db.get("scenes")
    _require(
        isinstance(db_scenes, list)
        and {scene.get("scene_id") for scene in db_scenes} == expected_scene_ids,
        "No-effect DB: scene records are incomplete",
    )
    fixtures_by_scene = {
        fixture_evidence[name]["scene"]["scene_id"]: fixture_evidence[name]["scene"]
        for name in FIXTURES
    }
    for scene in db_scenes:
        fixture_scene = fixtures_by_scene[scene["scene_id"]]
        _require(
            scene.get("owner_subject") == scope["owner_subject"]
            and scene.get("device_id") == scope["device_id"]
            and scene.get("document_id") == fixture_scene["document_id"]
            and scene.get("document_revision") == fixture_scene["document_revision"]
            and scene.get("source_snapshot_id") == fixture_scene["source_snapshot_id"]
            and scene.get("source_digest") == fixture_scene["source_digest"]
            and scene.get("scene_digest") == fixture_scene["scene_digest"]
            and scene.get("complete") == 1
            and json.loads(scene.get("counts_json", "null"))
            == fixture_scene["counts"]
            and window_start
            <= _time(scene.get("created_at"), "No-effect DB scene created")
            <= window_end
            and _time(scene.get("created_at"), "No-effect DB scene created")
            <= db_captured_at,
            "No-effect DB: scene record is not cross-bound to fixture evidence",
        )
    db_sections = no_effect_db.get("scene_sections")
    _require(
        isinstance(db_sections, list)
        and len(db_sections) == len(FIXTURES) * len(SECTIONS),
        "No-effect DB: scene section records are incomplete",
    )
    seen_sections = set()
    for section in db_sections:
        key = (section.get("scene_id"), section.get("section"))
        _require(
            key[0] in fixtures_by_scene
            and key[1] in SECTIONS
            and key not in seen_sections,
            "No-effect DB: scene section identity is invalid or duplicated",
        )
        seen_sections.add(key)
        raw_items = fixtures_by_scene[key[0]]["sections"][key[1]]["items"]
        _require(
            json.loads(section.get("payload_json", "null")) == raw_items
            and section.get("item_count") == len(raw_items),
            "No-effect DB: scene section payload differs from fixture evidence",
        )

    checks = no_effect_db.get("database_checks")
    migrations = checks.get("schema_migrations") if isinstance(checks, dict) else None
    migration_identity = (
        [
            {"version": item.get("version"), "checksum": item.get("checksum")}
            for item in migrations
        ]
        if isinstance(migrations, list)
        else None
    )
    _require(
        isinstance(checks, dict)
        and checks.get("integrity_check") == ["ok"]
        and checks.get("foreign_key_check") == []
        and migration_identity == _expected_migrations(root),
        "No-effect DB: integrity/foreign-key/migration proof is invalid",
    )
    write_snapshot = no_effect_db.get("write_snapshot")
    snapshot_digest = (
        write_snapshot.get("sha256") if isinstance(write_snapshot, dict) else None
    )
    snapshot_tables = (
        write_snapshot.get("tables") if isinstance(write_snapshot, dict) else None
    )
    restart_comparison = no_effect_db.get("restart_comparison")
    pre_snapshot = (
        restart_comparison.get("pre_restart_write_snapshot")
        if isinstance(restart_comparison, dict)
        else None
    )
    pre_tables = pre_snapshot.get("tables") if isinstance(pre_snapshot, dict) else None
    pre_digest = pre_snapshot.get("sha256") if isinstance(pre_snapshot, dict) else None
    expected_pre = _canonical_digest(pre_tables) if isinstance(pre_tables, dict) else None
    expected_post = (
        _canonical_digest(snapshot_tables)
        if isinstance(snapshot_tables, dict)
        else None
    )
    _require(
        no_effect_db.get("retrospective_no_write_events") == []
        and isinstance(snapshot_tables, dict)
        and isinstance(snapshot_digest, str)
        and snapshot_digest.startswith("sha256:")
        and len(snapshot_digest) == 71
        and pre_tables == snapshot_tables
        and pre_digest == expected_pre
        and snapshot_digest == expected_post
        and expected_pre == expected_post
        and restart_comparison.get("post_restart_write_snapshot_sha256")
        == snapshot_digest
        and restart_comparison.get("sha256_unchanged") is True
        and restart_comparison.get("tables_byte_identical") is True,
        "No-effect DB: durable no-write snapshot is incomplete",
    )
    sessions = no_effect_db.get("agent_sessions")
    active_session_id = no_effect_db.get("active_agent_session_id")
    active_sessions = [
        session
        for session in sessions
        if isinstance(session, dict)
        and session.get("session_id") == active_session_id
        and session.get("disconnected_at") is None
        and session.get("device_status") == "online"
    ] if isinstance(sessions, list) else []
    old_sessions = [
        session
        for session in sessions
        if isinstance(session, dict)
        and session.get("session_id")
        == restart_comparison.get("pre_restart_active_agent_session_id")
    ] if isinstance(sessions, list) and isinstance(restart_comparison, dict) else []
    _require(
        len(active_sessions) == 1
        and active_sessions[0].get("device_id") == scope["device_id"]
        and active_sessions[0].get("owner_subject") == scope["owner_subject"]
        and len(old_sessions) == 1
        and old_sessions[0].get("disconnected_at") is not None
        and restart_comparison.get("post_restart_active_agent_session_id")
        == active_session_id
        == after.get("agent_session_id")
        and restart_comparison.get("pre_restart_active_agent_session_id")
        == before.get("agent_session_id")
        and active_session_id != before.get("agent_session_id")
        and CAPTURE._restart_runtime_session_db_bound(
            no_effect_db,
            restart["runtime_identity_after"],
            device_id=scope["device_id"],
            first_invocation_at=restart["post_restart_public_path"][
                "tool_invocations"
            ][0]["started_at"],
        )
        and _time(
            restart_comparison.get("pre_restart_captured_at"),
            "No-effect DB pre-restart capture",
        )
        < _time(
            restart_comparison.get("post_restart_captured_at"),
            "No-effect DB post-restart capture",
        )
        <= _time(no_effect_db.get("captured_at"), "No-effect DB capture"),
        "No-effect DB: Agent reconnect/session history is invalid",
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
