from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "validate-phase10-live-evidence.py"
SPEC = importlib.util.spec_from_file_location("phase10_live_validator", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)
EVIDENCE_FILES = (
    "phase10-live-r25-drawing-a-20260730.json",
    "phase10-live-r25-drawing-b-20260730.json",
    "phase10-live-r25-drawing-c-20260730.json",
    "phase10-live-cleanup-workflow-20260730.json",
    "phase10-live-gateway-restart-20260730.json",
    "phase10-live-no-effect-db-20260730.json",
)


def _repo(path: Path) -> Path:
    shutil.copytree(
        ROOT / "fixtures" / "phase10" / "live",
        path / "fixtures" / "phase10" / "live",
    )
    evidence = path / "docs" / "architecture" / "evidence"
    evidence.mkdir(parents=True)
    for name in EVIDENCE_FILES:
        shutil.copy2(ROOT / "docs" / "architecture" / "evidence" / name, evidence)
    shutil.copytree(
        ROOT
        / "services"
        / "gateway"
        / "src"
        / "autocad_gateway"
        / "infrastructure"
        / "sqlite"
        / "migrations",
        path
        / "services"
        / "gateway"
        / "src"
        / "autocad_gateway"
        / "infrastructure"
        / "sqlite"
        / "migrations",
    )
    _upgrade_restart_artifact(path)
    return path


def _evidence(root: Path, name: str) -> tuple[Path, dict]:
    path = root / "docs" / "architecture" / "evidence" / name
    return path, json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _upgrade_restart_artifact(root: Path) -> None:
    path, value = _evidence(
        root, "phase10-live-gateway-restart-20260730.json"
    )
    before = value["gateway_process_before"]
    after = value["gateway_process_after"]
    old_service = before.get("gateway_service_record", {})
    executable = old_service.get("process", {}).get(
        "executable", before.get("gateway_executable", "/usr/bin/python3.12")
    )
    working_directory = old_service.get("release", {}).get(
        "working_directory", before.get("gateway_working_directory")
    )
    commit = value["implementation_commit"]
    properties = {
        "Id": "autocad-mcp-phase4.service",
        "ActiveState": "active",
        "SubState": "running",
        "ExecStart": f"{{ path={executable} ; argv[]={executable} app.py }}",
        "WorkingDirectory": working_directory,
    }
    release = {
        "source": "git_rev_parse",
        "working_directory": working_directory,
        "commit": commit,
    }
    process = {
        "source": "procfs",
        "executable": executable,
        "executable_sha256": "sha256:" + "a" * 64,
    }
    before["gateway_service_record"] = {
        "source": "systemctl_show",
        "properties": {
            **properties,
            "MainPID": before["gateway_pid"],
            "ExecMainStartTimestampMonotonic": "1000",
        },
        "process": {
            **process,
            "pid": before["gateway_pid"],
            "start_identity": "1000",
        },
        "release": release,
    }
    after["gateway_service_record"] = {
        "source": "systemctl_show",
        "properties": {
            **properties,
            "MainPID": after["gateway_pid"],
            "ExecMainStartTimestampMonotonic": "2000",
        },
        "process": {
            **process,
            "pid": after["gateway_pid"],
            "start_identity": "2000",
        },
        "release": release,
    }
    after["old_gateway_process_exit"] = {
        "source": "procfs",
        "pid": before["gateway_pid"],
        "start_identity": "1000",
        "proc_stat_after": None,
        "probe_exit_code": 0,
    }
    after.pop("gateway_previous_process_confirmed_exited", None)
    gates = {
        "authoritative_gateway_service": True,
        "gateway_runtime_identity": True,
        "old_gateway_process_exited": True,
        "process_identity_bound": True,
    }
    value["gate_results"].update(gates)
    value.update(gates)
    _save(path, value)


def _fixture(root: Path, name: str) -> tuple[Path, dict]:
    return _evidence(root, f"phase10-live-r25-drawing-{name}-20260730.json")


def _tamper(root: Path, case: str) -> None:
    if case in {"a_radius", "b_near_slot", "c_valid_geometry"}:
        name = case[0]
        path, value = _fixture(root, name)
        nodes = value["scene"]["sections"]["nodes"]["items"]
        if case == "a_radius":
            next(
                node
                for node in nodes
                if node["geometry"].get("kind") == "circle"
                and node["geometry"].get("radius") == 3.0
            )["geometry"]["radius"] = 5.0
        elif case == "b_near_slot":
            next(
                node
                for node in nodes
                if node["geometry"].get("kind") == "polyline"
            )["geometry"]["vertices"][0]["x"] = 70.5
        else:
            valid_circle = next(
                node["node_id"]
                for node in nodes
                if node["geometry"].get("kind") == "circle"
                and node["geometry"].get("radius") == 2.0
            )
            value["scene"]["sections"]["issues"]["items"][0][
                "source_node_ids"
            ].append(valid_circle)
        _save(path, value)
        return

    if case == "raw_semantics":
        path, value = _fixture(root, "a")
        value["scene"]["sections"]["features"]["items"] = [
            item
            for item in value["scene"]["sections"]["features"]["items"]
            if item["feature_type"] != "repeated_hole_pattern"
        ]
    elif case == "scene_identity":
        path, value = _fixture(root, "b")
        value["scene"]["scene_id"] = "scn_tampered"
    elif case == "cleanup_binding":
        path, value = _evidence(
            root, "phase10-live-cleanup-workflow-20260730.json"
        )
        value["report"]["issue_count"] += 1
    elif case in {"restart_pid", "restart_exit", "restart_start"}:
        path, value = _evidence(
            root, "phase10-live-gateway-restart-20260730.json"
        )
        if case == "restart_pid":
            value["gateway_process_after"]["gateway_pid"] = value[
                "gateway_process_before"
            ]["gateway_pid"]
        elif case == "restart_exit":
            value["gateway_process_after"]["old_gateway_process_exit"][
                "proc_stat_after"
            ] = "still-running"
        else:
            value["gateway_process_after"]["gateway_service_record"]["process"][
                "start_identity"
            ] = "1000"
    else:
        path, value = _evidence(
            root, "phase10-live-no-effect-db-20260730.json"
        )
        if case == "db_anchor_owner":
            value["anchor_jobs"][0]["owner_subject"] = "other-owner"
        elif case == "db_scene_binding":
            value["scenes"][0]["source_digest"] = "sha256:" + "0" * 64
        elif case == "db_snapshot":
            value["write_snapshot"]["tables"]["write_jobs"].append(
                {"job_id": "tampered-write"}
            )
        elif case == "db_migration":
            value["database_checks"]["schema_migrations"][0][
                "checksum"
            ] = "0" * 64
        else:
            raise AssertionError(case)
    _save(path, value)


def test_validator_accepts_cross_bound_raw_restart_evidence(
    tmp_path: Path,
) -> None:
    VALIDATOR.validate(_repo(tmp_path / "valid"))


def test_validator_rejects_legacy_restart_boolean(tmp_path: Path) -> None:
    root = _repo(tmp_path / "legacy")
    path, value = _evidence(
        root, "phase10-live-gateway-restart-20260730.json"
    )
    before = value["gateway_process_before"]
    after = value["gateway_process_after"]
    before.pop("gateway_service_record")
    after.pop("gateway_service_record")
    after.pop("old_gateway_process_exit")
    after["gateway_previous_process_confirmed_exited"] = True
    _save(path, value)
    with pytest.raises(ValueError, match="authoritative service records"):
        VALIDATOR.validate(root)


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("a_radius", "non-pattern circle exclusion"),
        ("b_near_slot", "tolerance-negative feature exclusions"),
        ("c_valid_geometry", "valid geometry exclusion"),
        ("raw_semantics", "features metadata"),
        ("scene_identity", "nodes metadata"),
        ("cleanup_binding", "not bound read-only"),
        ("restart_pid", "process/start identities"),
        ("restart_exit", "old process exit"),
        ("restart_start", "process/start identities"),
        ("db_anchor_owner", "anchor job is not cross-bound"),
        ("db_scene_binding", "scene record is not cross-bound"),
        ("db_snapshot", "durable no-write snapshot"),
        ("db_migration", "migration proof"),
    ),
)
def test_validator_rejects_tampered_self_reported_passes(
    tmp_path: Path, case: str, message: str
) -> None:
    root = _repo(tmp_path / case)
    _tamper(root, case)
    with pytest.raises(ValueError, match=message):
        VALIDATOR.validate(root)
