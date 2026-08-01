from __future__ import annotations

import copy
import importlib.util
import json
import shutil
from datetime import timedelta
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
    upgraded_fixtures: dict[str, dict] = {}
    for name in VALIDATOR.FIXTURES:
        fixture_path, fixture_value = _fixture(path, name)
        _upgrade_fixture_runtime_identity(fixture_path, fixture_value)
        upgraded_fixtures[name] = fixture_value
    db_path, db_value = _evidence(
        path, "phase10-live-no-effect-db-20260730.json"
    )
    _upgrade_db_artifact(db_path, db_value, upgraded_fixtures)
    return path


def _evidence(root: Path, name: str) -> tuple[Path, dict]:
    path = root / "docs" / "architecture" / "evidence" / name
    return path, json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _upgrade_db_artifact(
    path: Path, value: dict, fixtures: dict[str, dict]
) -> None:
    pre = value["restart_comparison"]["pre_restart_write_snapshot"]
    post = value["write_snapshot"]
    digest = VALIDATOR._canonical_digest(post["tables"])
    pre["sha256"] = digest
    post["sha256"] = digest
    value["restart_comparison"]["post_restart_write_snapshot_sha256"] = digest
    value["restart_comparison"]["sha256_unchanged"] = True
    value["restart_comparison"]["tables_byte_identical"] = True
    for fixture_value in fixtures.values():
        session_id = fixture_value["runtime_identity"]["agent_session"][
            "session_id"
        ]
        connected_at = fixture_value["runtime_identity"]["agent_session"][
            "connected_at"
        ]
        for record in value["agent_sessions"]:
            if record.get("session_id") == session_id:
                record["connected_at"] = connected_at
    _save(path, value)


def _upgrade_fixture_runtime_identity(path: Path, value: dict) -> None:
    captured_at = VALIDATOR._time(value["captured_at"], "fixture captured_at")
    identity = value["runtime_identity"]
    autocad = identity["autocad_process"]
    autocad["executable_sha256"] = "sha256:" + "e" * 64
    autocad["started_at"] = (captured_at - timedelta(seconds=180)).isoformat()
    session = identity["agent_session"]
    session["disconnected_at"] = None
    session["connected_at"] = (captured_at - timedelta(seconds=120)).isoformat()
    managed_host = session["managed_host"]
    managed_host.update(
        {
            "executable": r"C:\host\autocad_managed_host.exe",
            "executable_sha256": "sha256:" + "d" * 64,
            "process_id": 45678,
            "started_at": (captured_at - timedelta(seconds=150)).isoformat(),
        }
    )
    identity["desktop_agent_process"]["started_at"] = (
        captured_at - timedelta(seconds=130)
    ).isoformat()
    value["gate_results"]["runtime_identity_bound"] = True
    jobs = [
        value["source"]["observation_before"]["job"]["job_id"],
        value["source"]["observation_after"]["job"]["job_id"],
    ]
    value["session_binding"] = {
        "session_id": session["session_id"],
        "device_id": value["public_path"]["device_id"],
        "document_id": value["scene"]["document_id"],
        "document_revision": value["scene"]["document_revision"],
        "scene_id": value["scene"]["scene_id"],
        "observation_job_ids": jobs,
        "captured_at": captured_at.isoformat(),
    }
    invocations: list[dict] = []
    fixture_letter = value["fixture"]["fixture_id"].split("/")[0].rsplit("-", 1)[-1]

    def invoke(
        tool: str,
        arguments: dict,
        index: int,
        *,
        job_id: str | None = None,
    ) -> None:
        started = (
            captured_at - timedelta(seconds=90) + timedelta(seconds=5 * index)
        ).isoformat()
        completed = (
            captured_at - timedelta(seconds=90) + timedelta(seconds=5 * (index + 1))
        ).isoformat()
        invocations.append(
            {
                "tool": tool,
                "arguments": arguments,
                "started_at": started,
                "completed_at": completed,
                "outcome": "succeeded",
                "job_id": job_id,
            }
        )

    scene_id = value["scene"]["scene_id"]
    invoke("cad_list_devices", {"online_only": True}, 0)
    invoke(
        "cad_observe",
        {
            "device_id": value["public_path"]["device_id"],
            "observation_level": "detail",
            "include_preview_image": False,
            "idempotency_key": (
                f"phase10-drawing-{fixture_letter}-before-20260730T000000"
            ),
        },
        1,
        job_id=jobs[0],
    )
    invoke("cad_get_job", {"job_id": jobs[0]}, 2, job_id=jobs[0])
    invoke(
        "cad_build_scene",
        {
            "source_snapshot_id": value["source"]["snapshot_id"],
            "idempotency_key": f"phase10-drawing-{fixture_letter}-scene-20260730T000000",
            "analysis_profile": "mechanical-2d/1",
            "space": "model",
            "include_sections": list(VALIDATOR.SECTIONS),
        },
        3,
    )
    invoke(
        "cad_build_scene",
        {
            "source_snapshot_id": value["source"]["snapshot_id"],
            "idempotency_key": f"phase10-drawing-{fixture_letter}-scene-repeat-20260730T000000",
            "analysis_profile": "mechanical-2d/1",
            "space": "model",
            "include_sections": list(VALIDATOR.SECTIONS),
        },
        4,
    )
    for index, section in enumerate(VALIDATOR.SECTIONS):
        invoke(
            "cad_query_scene",
            {"scene_id": scene_id, "section": section, "limit": 200},
            5 + index,
        )
    invoke(
        "read_resource",
        {"uri": f"cad://scenes/{scene_id}/summary"},
        11,
    )
    invoke(
        "cad_observe",
        {
            "device_id": value["public_path"]["device_id"],
            "observation_level": "detail",
            "include_preview_image": False,
            "idempotency_key": (
                f"phase10-drawing-{fixture_letter}-after-20260730T000000"
            ),
        },
        12,
        job_id=jobs[1],
    )
    invoke("cad_get_job", {"job_id": jobs[1]}, 13, job_id=jobs[1])
    invocations[-1]["completed_at"] = captured_at.isoformat()
    value["public_path"]["tool_invocations"] = invocations
    value["public_path"]["invoked_tools"] = [
        item["tool"] for item in invocations
    ]
    value["public_path"]["write_tools_invoked"] = sorted(
        {
            item["tool"]
            for item in invocations
            if item["tool"] in VALIDATOR.CAPTURE.WRITE_TOOLS
        }
    )
    _save(path, value)


def _restamp_invocations(invocations: list[dict], captured_at: str) -> None:
    captured = VALIDATOR._time(captured_at, "captured_at")
    for index, item in enumerate(invocations):
        item["started_at"] = (
            captured - timedelta(seconds=90) + timedelta(seconds=5 * index)
        ).isoformat()
        item["completed_at"] = (
            captured
            - timedelta(seconds=90)
            + timedelta(seconds=5 * (index + 1))
        ).isoformat()
    invocations[-1]["completed_at"] = captured.isoformat()


def _identity_capture_from_record(
    record: dict, *, captured_at: str, old_pid: int | None = None
) -> dict:
    properties = record["properties"]
    process = record["process"]
    release = record["release"]
    pid = process["pid"]
    executable = process["executable"]
    systemctl_stdout = "\n".join(
        (
            f"Id={properties['Id']}",
            f"ActiveState={properties['ActiveState']}",
            f"SubState={properties['SubState']}",
            f"MainPID={properties['MainPID']}",
            f"ExecMainStartTimestampMonotonic="
            f"{properties['ExecMainStartTimestampMonotonic']}",
            f"WorkingDirectory={properties['WorkingDirectory']}",
            f"ExecStart={properties['ExecStart']}",
            "",
        )
    )
    commands = [
        {
            "command": [
                "systemctl",
                "show",
                "--no-pager",
                "--property=Id,ActiveState,SubState,MainPID,"
                "ExecMainStartTimestampMonotonic,WorkingDirectory,ExecStart",
                properties["Id"],
            ],
            "stdout": systemctl_stdout,
            "stderr": "",
            "exit_code": 0,
            "captured_at": captured_at,
        },
        {
            "command": ["readlink", "-f", f"/proc/{pid}/exe"],
            "stdout": executable + "\n",
            "stderr": "",
            "exit_code": 0,
            "captured_at": captured_at,
        },
        {
            "command": ["sha256sum", executable],
            "stdout": f"{process['executable_sha256'][7:]}  {executable}\n",
            "stderr": "",
            "exit_code": 0,
            "captured_at": captured_at,
        },
        {
            "command": [
                "git",
                "-C",
                release["working_directory"],
                "rev-parse",
                "HEAD",
            ],
            "stdout": release["commit"] + "\n",
            "stderr": "",
            "exit_code": 0,
            "captured_at": captured_at,
        },
        {
            "command": ["awk", "{print $22}", f"/proc/{pid}/stat"],
            "stdout": process["start_identity"] + "\n",
            "stderr": "",
            "exit_code": 0,
            "captured_at": captured_at,
        },
    ]
    if old_pid is not None:
        commands.append(
            {
                "command": ["test", "!", "-e", f"/proc/{old_pid}/stat"],
                "stdout": "",
                "stderr": "",
                "exit_code": 0,
                "captured_at": captured_at,
            }
        )
    return {
        "schema_version": "cad.phase10-live-identity/1",
        "service": properties["Id"],
        "captured_at": captured_at,
        "capture_command": "capture-identity",
        "operator": "test",
        "commands": commands,
    }


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
        "command": ["test", "!", "-e", f"/proc/{before['gateway_pid']}/stat"],
    }
    value["identity_capture_before"] = _identity_capture_from_record(
        before["gateway_service_record"],
        captured_at=before.get("captured_at") or value["captured_at"],
    )
    value["identity_capture_after"] = _identity_capture_from_record(
        after["gateway_service_record"],
        captured_at=after.get("captured_at") or value["captured_at"],
        old_pid=before["gateway_pid"],
    )
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
    elif case in {
        "identity_hash",
        "identity_pid",
        "identity_commit",
        "identity_time",
        "source_revision",
        "identity_host_hash",
        "identity_host_start",
        "identity_autocad_hash",
        "identity_autocad_start",
        "session_disconnected",
        "identity_start_after_invocation",
        "session_connected_mismatch",
        "invocation_write",
        "invocation_missing_observe",
        "invocation_missing_section",
        "invocation_wrong_scene",
        "invocation_unknown_tool",
        "invoked_tools_tampered",
        "write_tools_tampered",
        "session_binding",
    }:
        path, value = _fixture(root, "c" if case == "identity_hash" else "a")
        identity = value["runtime_identity"]
        if case == "identity_hash":
            identity["desktop_agent_process"]["executable_sha256"] = (
                "sha256:" + "0" * 64
            )
        elif case == "identity_pid":
            identity["autocad_process"]["process_id"] = 0
        elif case == "identity_commit":
            identity["gateway_process"]["release_commit"] = "d" * 40
        elif case == "identity_time":
            identity["agent_session"]["connected_at"] = "2027-01-01T00:00:00Z"
        elif case == "identity_start_after_invocation":
            identity["desktop_agent_process"]["started_at"] = (
                VALIDATOR._time(value["captured_at"], "captured_at")
                - timedelta(seconds=10)
            ).isoformat()
        elif case == "session_connected_mismatch":
            identity["agent_session"]["connected_at"] = (
                VALIDATOR._time(value["captured_at"], "captured_at")
                - timedelta(seconds=300)
            ).isoformat()
        elif case == "identity_host_hash":
            identity["agent_session"]["managed_host"]["package_hash"] = (
                "sha256:" + "0" * 64
            )
        elif case == "identity_host_start":
            identity["agent_session"]["managed_host"]["started_at"] = (
                "2027-01-01T00:00:00Z"
            )
        elif case == "identity_autocad_hash":
            identity["autocad_process"]["executable_sha256"] = "not-a-hash"
        elif case == "identity_autocad_start":
            identity["autocad_process"]["started_at"] = "2027-01-01T00:00:00Z"
        elif case == "session_disconnected":
            identity["agent_session"]["disconnected_at"] = (
                "2026-07-30T00:00:00Z"
            )
        elif case == "invocation_write":
            last_completed = VALIDATOR._time(
                value["public_path"]["tool_invocations"][-1]["completed_at"],
                "last completed",
            )
            value["public_path"]["tool_invocations"].append(
                {
                    "tool": "cad_commit",
                    "arguments": {},
                    "started_at": (
                        last_completed + timedelta(seconds=1)
                    ).isoformat(),
                    "completed_at": (
                        last_completed + timedelta(seconds=2)
                    ).isoformat(),
                    "outcome": "succeeded",
                    "job_id": "job-write",
                }
            )
        elif case == "invocation_missing_observe":
            value["public_path"]["tool_invocations"] = [
                item
                for item in value["public_path"]["tool_invocations"]
                if not (
                    item["tool"] == "cad_observe"
                    and item.get("job_id")
                    == value["session_binding"]["observation_job_ids"][1]
                )
            ]
        elif case == "invocation_missing_section":
            value["public_path"]["tool_invocations"] = [
                item
                for item in value["public_path"]["tool_invocations"]
                if not (
                    item["tool"] == "cad_query_scene"
                    and item["arguments"].get("section") == "issues"
                )
            ]
        elif case == "invocation_wrong_scene":
            next(
                item
                for item in value["public_path"]["tool_invocations"]
                if item["tool"] == "cad_build_scene"
            )["arguments"]["source_snapshot_id"] = "snapshot-tampered"
        elif case == "invocation_unknown_tool":
            last_completed = VALIDATOR._time(
                value["public_path"]["tool_invocations"][-1]["completed_at"],
                "last completed",
            )
            value["public_path"]["tool_invocations"].append(
                {
                    "tool": "cad_unknown_tool",
                    "arguments": {},
                    "started_at": (
                        last_completed + timedelta(seconds=1)
                    ).isoformat(),
                    "completed_at": (
                        last_completed + timedelta(seconds=2)
                    ).isoformat(),
                    "outcome": "succeeded",
                    "job_id": None,
                }
            )
        elif case == "invoked_tools_tampered":
            value["public_path"]["invoked_tools"] = [
                tool
                for tool in value["public_path"]["invoked_tools"]
                if tool != "read_resource"
            ]
        elif case == "write_tools_tampered":
            value["public_path"]["write_tools_invoked"] = ["cad_commit"]
        elif case == "session_binding":
            value["session_binding"]["observation_job_ids"] = ["job-tampered"]
        else:
            value["source"]["document_revision_before"] = "999999"
    elif case.startswith("invocation_") and case != "invocation_write":
        path, value = _fixture(root, "a")
        invocations = value["public_path"]["tool_invocations"]
        observes = [item for item in invocations if item["tool"] == "cad_observe"]
        if case == "invocation_observe_device":
            for item in observes:
                item["arguments"]["device_id"] = "wrong-device"
        elif case == "invocation_observe_level":
            observes[0]["arguments"]["observation_level"] = "summary"
        elif case == "invocation_observe_preview":
            observes[0]["arguments"]["include_preview_image"] = True
        elif case == "invocation_observe_swapped":
            observes[0]["job_id"], observes[1]["job_id"] = (
                observes[1]["job_id"],
                observes[0]["job_id"],
            )
        elif case == "invocation_observe_key_reused":
            observes[1]["arguments"]["idempotency_key"] = observes[0][
                "arguments"
            ]["idempotency_key"]
        elif case == "invocation_observe_key_prefix":
            observes[0]["arguments"]["idempotency_key"] = "unrelated-key"
        elif case == "invocation_job_mismatch":
            observes[0]["job_id"] = "job-other"
        elif case == "invocation_reordered":
            invocations[1], invocations[2] = invocations[2], invocations[1]
        elif case == "invocation_after_before_resource":
            read_index = next(
                index
                for index, item in enumerate(invocations)
                if item["tool"] == "read_resource"
            )
            invocations[read_index], invocations[read_index + 1] = (
                invocations[read_index + 1],
                invocations[read_index],
            )
            _restamp_invocations(invocations, value["captured_at"])
        elif case == "invocation_query_before_build":
            query_index = next(
                index
                for index, item in enumerate(invocations)
                if item["tool"] == "cad_query_scene"
            )
            invocations[query_index], invocations[3] = (
                invocations[3],
                invocations[query_index],
            )
            _restamp_invocations(invocations, value["captured_at"])
        elif case == "invocation_second_summary":
            summary = copy.deepcopy(
                next(
                    item
                    for item in invocations
                    if item["tool"] == "read_resource"
                )
            )
            last_completed = VALIDATOR._time(
                invocations[-1]["completed_at"], "last completed"
            )
            summary["started_at"] = (
                last_completed + timedelta(seconds=1)
            ).isoformat()
            summary["completed_at"] = (
                last_completed + timedelta(seconds=2)
            ).isoformat()
            invocations.append(summary)
        else:  # invocation_interleaved_poll
            invocations[2], invocations[3] = invocations[3], invocations[2]
            _restamp_invocations(invocations, value["captured_at"])
        _save(path, value)
    elif case == "identity_session":
        path, value = _evidence(
            root, "phase10-live-no-effect-db-20260730.json"
        )
        fixture_path, fixture_value = _fixture(root, "a")
        session_id = fixture_value["runtime_identity"]["agent_session"][
            "session_id"
        ]
        value["agent_sessions"] = [
            item
            for item in value["agent_sessions"]
            if item.get("session_id") != session_id
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
    elif case in {"restart_raw", "restart_raw_service"}:
        path, value = _evidence(
            root, "phase10-live-gateway-restart-20260730.json"
        )
        if case == "restart_raw":
            value["identity_capture_before"]["captured_at"], value[
                "identity_capture_after"
            ]["captured_at"] = (
                value["identity_capture_after"]["captured_at"],
                value["identity_capture_before"]["captured_at"],
            )
        else:
            systemctl = value["identity_capture_before"]["commands"][0]
            systemctl["stdout"] = systemctl["stdout"].replace(
                "Id=autocad-mcp-phase4.service",
                "Id=other.service",
                1,
            )
    elif case in {"db_window_end", "db_sha", "db_freshness", "db_row_freshness"}:
        path, value = _evidence(
            root, "phase10-live-no-effect-db-20260730.json"
        )
        if case == "db_window_end":
            value["scope"]["window_end"] = "2026-07-30T14:24:00+07:00"
        elif case == "db_sha":
            fake = "sha256:" + "f" * 64
            value["write_snapshot"]["sha256"] = fake
            value["restart_comparison"]["pre_restart_write_snapshot"][
                "sha256"
            ] = fake
            value["restart_comparison"][
                "post_restart_write_snapshot_sha256"
            ] = fake
        elif case == "db_freshness":
            value["captured_at"] = "2026-07-30T14:30:00+07:00"
        else:
            value["anchor_jobs"][0]["updated_at"] = "2026-07-30T15:00:00+07:00"
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


def test_finalize_fixture_orchestrates_provisional_to_pass(
    tmp_path: Path,
) -> None:
    import argparse
    import asyncio

    root = _repo(tmp_path / "orchestrate")
    fixture_path, fixture_value = _fixture(root, "a")
    provisional = copy.deepcopy(fixture_value)
    provisional["schema_version"] = (
        "cad.phase10-live-public-fixture-provisional/1"
    )
    provisional["status"] = "PROVISIONAL"
    provisional.pop("no_effect", None)
    provisional.pop("no_effect_db_binding", None)
    provisional["gate_results"] = {
        key: item
        for key, item in provisional["gate_results"].items()
        if key
        not in {
            "no_write_events_in_window",
            "anchor_jobs_read_only",
            "write_snapshot_unchanged",
            "no_write_requested",
            "no_cad_effect_attempted",
            "runtime_identity_bound",
        }
    }
    provisional_path = tmp_path / "provisional-a.json"
    provisional_path.write_text(json.dumps(provisional), encoding="utf-8")
    db_path, db_value = _evidence(
        root, "phase10-live-no-effect-db-20260730.json"
    )
    args = argparse.Namespace(
        fixture_evidence=provisional_path,
        no_effect_db=db_path,
        device_id=provisional["public_path"]["device_id"],
    )
    final = asyncio.run(VALIDATOR.CAPTURE._finalize_fixture(args, "token"))
    assert final["schema_version"] == "cad.phase10-live-public-fixture/1"
    assert final["status"] == "PASS"
    assert final["gate_results"]["no_write_requested"] is True
    assert final["gate_results"]["no_cad_effect_attempted"] is True
    assert final["gate_results"]["runtime_identity_bound"] is True
    assert final["no_effect"]["write_requested"] is False
    assert final["no_effect"]["cad_effect_attempted"] is False
    assert final["no_effect_db_binding"]["artifact"] == str(db_path)


def test_finalized_artifact_passes_full_validator_without_mutation(
    tmp_path: Path,
) -> None:
    import argparse
    import asyncio

    root = _repo(tmp_path / "e2e")
    fixture_path, fixture_value = _fixture(root, "a")
    provisional = copy.deepcopy(fixture_value)
    provisional["schema_version"] = (
        "cad.phase10-live-public-fixture-provisional/1"
    )
    provisional["status"] = "PROVISIONAL"
    provisional.pop("no_effect", None)
    provisional.pop("no_effect_db_binding", None)
    provisional["gate_results"] = {
        key: item
        for key, item in provisional["gate_results"].items()
        if key
        not in {
            "no_write_events_in_window",
            "anchor_jobs_read_only",
            "write_snapshot_unchanged",
            "no_write_requested",
            "no_cad_effect_attempted",
            "runtime_identity_bound",
        }
    }
    provisional_path = tmp_path / "provisional-a.json"
    provisional_path.write_text(json.dumps(provisional), encoding="utf-8")
    db_path, _ = _evidence(root, "phase10-live-no-effect-db-20260730.json")
    args = argparse.Namespace(
        fixture_evidence=provisional_path,
        no_effect_db=db_path,
        device_id=provisional["public_path"]["device_id"],
    )
    final = asyncio.run(VALIDATOR.CAPTURE._finalize_fixture(args, "token"))
    # The final artifact is written into the retained evidence path with no
    # further upgrade or mutation, and must pass the full validator.
    _save(fixture_path, final)
    VALIDATOR.validate(root)


def test_cli_finalize_fixture_runs_without_token_file(tmp_path: Path) -> None:
    import sys

    root = _repo(tmp_path / "cli")
    fixture_path, fixture_value = _fixture(root, "a")
    provisional = copy.deepcopy(fixture_value)
    provisional["schema_version"] = (
        "cad.phase10-live-public-fixture-provisional/1"
    )
    provisional["status"] = "PROVISIONAL"
    provisional.pop("no_effect", None)
    provisional.pop("no_effect_db_binding", None)
    provisional["gate_results"] = {
        key: item
        for key, item in provisional["gate_results"].items()
        if key
        not in {
            "no_write_events_in_window",
            "anchor_jobs_read_only",
            "write_snapshot_unchanged",
            "no_write_requested",
            "no_cad_effect_attempted",
            "runtime_identity_bound",
        }
    }
    provisional_path = tmp_path / "provisional-a.json"
    provisional_path.write_text(json.dumps(provisional), encoding="utf-8")
    db_path, _ = _evidence(root, "phase10-live-no-effect-db-20260730.json")
    output_path = tmp_path / "final-a.json"
    sys.argv = [
        "phase10-live-public-evidence.py",
        "finalize-fixture",
        "--fixture-evidence",
        str(provisional_path),
        "--no-effect-db",
        str(db_path),
        "--device-id",
        provisional["public_path"]["device_id"],
        "--output",
        str(output_path),
    ]
    VALIDATOR.CAPTURE.main()
    final = json.loads(output_path.read_text(encoding="utf-8"))
    assert final["schema_version"] == "cad.phase10-live-public-fixture/1"
    assert final["status"] == "PASS"


def test_cli_action_specific_requirements() -> None:
    parser = VALIDATOR.CAPTURE.build_parser()
    parser.parse_args(
        [
            "capture-identity",
            "--output",
            "identity.json",
        ]
    )
    parser.parse_args(
        [
            "finalize-fixture",
            "--fixture-evidence",
            "provisional.json",
            "--no-effect-db",
            "db.json",
            "--device-id",
            "device",
            "--output",
            "final.json",
        ]
    )
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "capture-public",
                "--output",
                "capture.json",
            ]
        )


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
    "value",
    (
        "2026-07-30T07:31:14Z",
        "2026-07-30T07:31:14+00:00",
        "2026-07-30T07:31:14+07:00",
        "2026-07-30T07:31:14.1Z",
        "2026-07-30T07:31:14.123Z",
        "2026-07-30T07:31:14.123456Z",
        "2026-07-30T07:31:14.8026601Z",
        "2026-07-30T07:31:14.802660123Z",
    ),
)
def test_validator_time_accepts_rfc3339_fractional_seconds(value: str) -> None:
    assert VALIDATOR._time(value, "timestamp").tzinfo is not None


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
        ("restart_raw", "raw identity capture ordering"),
        ("restart_raw_service", "reports service"),
        ("identity_hash", "standalone Agent identity differs"),
        ("identity_pid", "AutoCAD runtime identity is invalid"),
        ("identity_session", "absent from session history"),
        ("identity_commit", "gateway runtime identity is not cross-bound"),
        ("identity_time", "not before capture"),
        ("identity_start_after_invocation", "not before capture"),
        ("session_connected_mismatch", "session was not active at capture"),
        ("source_revision", "source/scene no-effect binding"),
        ("identity_host_hash", "not bound to observation runtime evidence"),
        ("identity_host_start", "managed host runtime identity is invalid"),
        ("identity_autocad_hash", "AutoCAD executable hash/start identity"),
        ("identity_autocad_start", "AutoCAD executable hash/start identity"),
        ("session_disconnected", "agent session runtime identity is invalid"),
        ("invocation_write", "out of phase"),
        ("invocation_missing_observe", "missing expected phase"),
        ("invocation_missing_section", "per retained section"),
        ("invocation_wrong_scene", "cad_build_scene invocation arguments"),
        ("invocation_unknown_tool", "out of phase"),
        ("invoked_tools_tampered", "invoked_tools does not match"),
        ("write_tools_tampered", "write_tools_invoked does not match"),
        ("invocation_observe_device", "cad_observe invocation arguments"),
        ("invocation_observe_level", "cad_observe invocation arguments"),
        ("invocation_observe_preview", "cad_observe invocation arguments"),
        ("invocation_observe_swapped", "cad_observe invocation arguments"),
        ("invocation_observe_key_reused", "cad_observe invocation arguments"),
        ("invocation_observe_key_prefix", "cad_observe invocation arguments"),
        ("invocation_job_mismatch", "out of phase"),
        ("invocation_reordered", "not stored chronologically"),
        ("invocation_after_before_resource", "out of phase"),
        ("invocation_query_before_build", "out of phase"),
        ("invocation_second_summary", "out of phase"),
        ("invocation_interleaved_poll", "out of phase"),
        ("session_binding", "session binding is not cross-bound"),
        ("db_window_end", "outside the audit window"),
        ("db_sha", "durable no-write snapshot"),
        ("db_freshness", "before the audit window closed"),
        ("db_row_freshness", "anchor job is not cross-bound"),
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
