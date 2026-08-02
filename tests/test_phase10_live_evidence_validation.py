from __future__ import annotations

import copy
import importlib.util
import json
import shutil
from datetime import datetime, timedelta, timezone
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
FIXED_COMMIT = "a" * 40
TEST_STAMP = "20260730000000"


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
    for name, fixture_value in upgraded_fixtures.items():
        fixture_path, _ = _fixture(path, name)
        _save(fixture_path, fixture_value)
    _patch_evidence_commits(path)
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
        fixture_value["finalization"]["finalized_at"] = (
            VALIDATOR._time(value["captured_at"], "DB captured_at")
            + timedelta(seconds=1)
        ).isoformat()
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
    value["finalization"] = {
        "implementation_commit": value["implementation_commit"],
        "finalized_at": (captured_at + timedelta(seconds=1)).isoformat(),
    }
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
    fixture_letter = VALIDATOR.CAPTURE._fixture_letter_from_id(
        value["fixture"]["fixture_id"]
    )

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
        item = {
            "tool": tool,
            "arguments": arguments,
            "started_at": started,
            "completed_at": completed,
            "outcome": "succeeded",
            "job_id": job_id,
        }
        if tool == "cad_get_job":
            item["job_state"] = "succeeded"
            item["job_result"] = {"job_id": job_id, "state": "succeeded"}
        invocations.append(item)

    scene_id = value["scene"]["scene_id"]
    invoke("cad_list_devices", {"online_only": True}, 0)
    invoke(
        "cad_observe",
        {
            "device_id": value["public_path"]["device_id"],
            "observation_level": "detail",
            "include_preview_image": False,
            "idempotency_key": VALIDATOR.CAPTURE._phase10_key(
                fixture_letter, "before", TEST_STAMP
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
            "idempotency_key": VALIDATOR.CAPTURE._phase10_key(
                fixture_letter, "scene", TEST_STAMP
            ),
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
            "idempotency_key": VALIDATOR.CAPTURE._phase10_key(
                fixture_letter, "scene-repeat", TEST_STAMP
            ),
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
            "idempotency_key": VALIDATOR.CAPTURE._phase10_key(
                fixture_letter, "after", TEST_STAMP
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


def _patch_evidence_commits(root: Path) -> None:
    for name in EVIDENCE_FILES:
        path, value = _evidence(root, name)
        value["implementation_commit"] = FIXED_COMMIT
        value["baseline_commit"] = FIXED_COMMIT
        identity = value.get("runtime_identity")
        if isinstance(identity, dict):
            gateway = identity.get("gateway_process")
            if isinstance(gateway, dict):
                gateway["release_commit"] = FIXED_COMMIT
                working_directory = gateway.get("working_directory")
                if isinstance(working_directory, str):
                    gateway["working_directory"] = working_directory.replace(
                        "165de04", FIXED_COMMIT[:7]
                    )
        finalization = value.get("finalization")
        if isinstance(finalization, dict):
            finalization["implementation_commit"] = FIXED_COMMIT
        for key in ("gateway_process_before", "gateway_process_after"):
            record = value.get(key)
            if not isinstance(record, dict):
                continue
            working_directory = record.get("gateway_working_directory")
            if isinstance(working_directory, str):
                record["gateway_working_directory"] = working_directory.replace(
                    "165de04", FIXED_COMMIT[:7]
                )
            service = record.get("gateway_service_record")
            if isinstance(service, dict):
                release = service.get("release")
                if isinstance(release, dict):
                    release["commit"] = FIXED_COMMIT
                    release_working_directory = release.get(
                        "working_directory"
                    )
                    if isinstance(release_working_directory, str):
                        release["working_directory"] = (
                            release_working_directory.replace(
                                "165de04", FIXED_COMMIT[:7]
                            )
                        )
                properties = service.get("properties")
                if isinstance(properties, dict):
                    properties_working_directory = properties.get(
                        "WorkingDirectory"
                    )
                    if isinstance(properties_working_directory, str):
                        properties["WorkingDirectory"] = (
                            properties_working_directory.replace(
                                "165de04", FIXED_COMMIT[:7]
                            )
                        )
        for capture_key in ("identity_capture_before", "identity_capture_after"):
            capture = value.get(capture_key)
            if not isinstance(capture, dict):
                continue
            for record in capture.get("commands", []):
                if not isinstance(record, dict):
                    continue
                if isinstance(record.get("command"), list):
                    record["command"] = [
                        item.replace("165de04", FIXED_COMMIT[:7])
                        if isinstance(item, str)
                        else item
                        for item in record["command"]
                    ]
                if record.get("command", [None])[0] == "git":
                    record["stdout"] = FIXED_COMMIT + "\n"
                elif isinstance(record.get("stdout"), str):
                    record["stdout"] = record["stdout"].replace(
                        "165de04", FIXED_COMMIT[:7]
                    )
        _save(path, value)


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
    _, drawing_c = _fixture(root, "c")
    value["scene"] = drawing_c["scene"]
    value["post_restart_devices"] = drawing_c["public_path"]["devices"]
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
    captured_at = VALIDATOR._time(value["captured_at"], "restart captured_at")
    identity_before_at = captured_at - timedelta(minutes=14)
    identity_after_at = captured_at - timedelta(minutes=13)
    before["captured_at"] = identity_before_at.isoformat()
    after["captured_at"] = identity_after_at.isoformat()
    value["identity_capture_before"] = _identity_capture_from_record(
        before["gateway_service_record"], captured_at=before["captured_at"]
    )
    value["identity_capture_after"] = _identity_capture_from_record(
        after["gateway_service_record"],
        captured_at=after["captured_at"],
        old_pid=before["gateway_pid"],
    )
    invocations = []
    expected = [
        ("cad_list_devices", {"online_only": True}),
        *[
            (
                "cad_query_scene",
                {
                    "scene_id": value["scene_id"],
                    "section": section,
                    "limit": 200,
                },
            )
            for section in VALIDATOR.SECTIONS
        ],
        (
            "read_resource",
            {"uri": f"cad://scenes/{value['scene_id']}/summary"},
        ),
    ]
    for index, (tool, arguments) in enumerate(expected):
        started_at = captured_at - timedelta(minutes=12) + timedelta(seconds=2 * index)
        invocations.append(
            {
                "tool": tool,
                "arguments": arguments,
                "started_at": started_at.isoformat(),
                "completed_at": (started_at + timedelta(seconds=1)).isoformat(),
                "outcome": "succeeded",
                "job_id": None,
            }
        )
    value["post_restart_public_path"] = {
        "invoked_tools": [item["tool"] for item in invocations],
        "write_tools_invoked": [],
        "tool_invocations": invocations,
    }
    value["finalization"] = {
        "implementation_commit": value["implementation_commit"],
        "finalized_at": (captured_at + timedelta(minutes=2)).isoformat(),
    }
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
        "key_fixture_letter",
        "key_old_format",
        "key_stamp_mismatch",
        "key_reused",
        "key_bad_stamp",
        "finalization_commit",
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
        elif case == "key_fixture_letter":
            next(
                item
                for item in value["public_path"]["tool_invocations"]
                if item["tool"] == "cad_observe"
            )["arguments"]["idempotency_key"] = (
                "phase10-drawing-b-before-20260730000000"
            )
        elif case == "key_old_format":
            next(
                item
                for item in value["public_path"]["tool_invocations"]
                if item["tool"] == "cad_observe"
            )["arguments"]["idempotency_key"] = (
                "phase10-a-before-20260730000000"
            )
        elif case == "key_stamp_mismatch":
            next(
                item
                for item in value["public_path"]["tool_invocations"]
                if item["tool"] == "cad_observe"
                and item.get("job_id")
                == value["session_binding"]["observation_job_ids"][1]
            )["arguments"]["idempotency_key"] = (
                "phase10-drawing-a-after-20260730000001"
            )
        elif case == "key_reused":
            builds = [
                item
                for item in value["public_path"]["tool_invocations"]
                if item["tool"] == "cad_build_scene"
            ]
            builds[1]["arguments"]["idempotency_key"] = builds[0][
                "arguments"
            ]["idempotency_key"]
        elif case == "key_bad_stamp":
            next(
                item
                for item in value["public_path"]["tool_invocations"]
                if item["tool"] == "cad_observe"
            )["arguments"]["idempotency_key"] = (
                "phase10-drawing-a-before-2026"
            )
        elif case == "finalization_commit":
            value["finalization"]["implementation_commit"] = "b" * 40
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
        elif case == "invocation_terminal_result":
            next(
                item
                for item in invocations
                if item["tool"] == "cad_get_job"
                and item.get("job_id")
                == value["session_binding"]["observation_job_ids"][1]
            ).pop("job_result", None)
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
    elif case in {
        "restart_pid",
        "restart_exit",
        "restart_start",
        "restart_public_trace",
    }:
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
        elif case == "restart_start":
            value["gateway_process_after"]["gateway_service_record"]["process"][
                "start_identity"
            ] = "1000"
        else:
            value["post_restart_public_path"]["tool_invocations"].pop()
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
    elif case in {
        "db_scope_device",
        "db_job_device",
        "db_scene_device",
        "db_session_device",
        "db_scope_scene",
        "db_owner_mismatch",
    }:
        path, value = _evidence(
            root, "phase10-live-no-effect-db-20260730.json"
        )
        _, fixture = _fixture(root, "a")
        if case == "db_scope_device":
            value["scope"]["device_id"] = "wrong-device"
        elif case == "db_job_device":
            job_ids = set(fixture["session_binding"]["observation_job_ids"])
            next(
                item for item in value["anchor_jobs"] if item["job_id"] in job_ids
            )["device_id"] = "wrong-device"
        elif case == "db_scene_device":
            next(
                item
                for item in value["scenes"]
                if item["scene_id"] == fixture["scene"]["scene_id"]
            )["device_id"] = "wrong-device"
        elif case == "db_session_device":
            session_id = fixture["runtime_identity"]["agent_session"]["session_id"]
            next(
                item
                for item in value["agent_sessions"]
                if item["session_id"] == session_id
            )["device_id"] = "wrong-device"
        elif case == "db_scope_scene":
            value["scope"]["scene_ids"].remove(fixture["scene"]["scene_id"])
        else:
            session_id = fixture["runtime_identity"]["agent_session"]["session_id"]
            next(
                item
                for item in value["agent_sessions"]
                if item["session_id"] == session_id
            )["owner_subject"] = "other-owner"
    elif case == "db_commit":
        path, value = _evidence(
            root, "phase10-live-no-effect-db-20260730.json"
        )
        value["implementation_commit"] = "b" * 40
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


class _FakeMCPClient:
    def __init__(self, retained: dict):
        self._retained = retained
        self._observe_calls = 0
        self._tool_names = [
            "cad_build_scene",
            "cad_query_scene",
            "cad_list_devices",
            "cad_observe",
            "cad_get_job",
        ]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def list_tools(self):
        return [type("Tool", (), {"name": name})() for name in self._tool_names]

    async def call_tool(self, name: str, arguments: dict):
        if name == "cad_list_devices":
            device = dict(self._retained["public_path"]["devices"]["devices"][0])
            device.update(
                {
                    "status": "online",
                    "paused": False,
                    "runtime_state": "online_idle",
                }
            )
            return {"devices": [device]}
        if name == "cad_observe":
            moment = (
                "observation_before"
                if self._observe_calls == 0
                else "observation_after"
            )
            self._observe_calls += 1
            return self._retained["source"][moment]["request"]
        if name == "cad_get_job":
            before = self._retained["source"]["observation_before"]["job"]
            after = self._retained["source"]["observation_after"]["job"]
            return before if arguments["job_id"] == before["job_id"] else after
        if name == "cad_build_scene":
            scene = {
                key: item
                for key, item in self._retained["scene"].items()
                if key
                not in {
                    "sections",
                    "repeat_build",
                    "summary_resource",
                    "feature_types",
                    "relation_types",
                    "issue_codes",
                    "evidence_strengths",
                    "source_capabilities",
                }
            }
            return {
                "contract_version": "cad.mcp/1.6",
                "correlation_id": self._retained["scene"]["repeat_build"][
                    "correlation_id"
                ],
                "scene": scene,
                "reused": "-scene-repeat-" in arguments["idempotency_key"],
            }
        if name == "cad_query_scene":
            return self._retained["scene"]["sections"][arguments["section"]]
        raise AssertionError(f"unexpected tool {name}")

    async def read_resource(self, uri: str):
        return self._retained["scene"]["summary_resource"]


def _capture_public_args(tmp_path: Path, fixture_value: dict):
    import argparse

    upgraded = copy.deepcopy(fixture_value)
    _upgrade_fixture_runtime_identity(
        tmp_path / "upgraded-copy.json", upgraded
    )
    process_identity = upgraded["runtime_identity"]
    process_path = tmp_path / "process-identity.json"
    process_path.write_text(json.dumps(process_identity), encoding="utf-8")
    drawing_path = tmp_path / "phase10-drawing-a.dwg"
    shutil.copy2(
        ROOT / "fixtures" / "phase10" / "live" / "phase10-drawing-a.dwg",
        drawing_path,
    )
    return argparse.Namespace(
        endpoint="https://example.invalid/mcp",
        device_id=process_identity["agent_session"]["device_id"],
        token_file=tmp_path / "token.json",
        output=tmp_path / "provisional-a.json",
        operator="test",
        fixture="a",
        drawing=drawing_path,
        process_identity=process_path,
        capture_command="capture-public (test)",
    )


def _provisional_capture(tmp_path: Path, retained: dict) -> dict:
    provisional = copy.deepcopy(retained)
    provisional["schema_version"] = (
        "cad.phase10-live-public-fixture-provisional/1"
    )
    provisional["status"] = "PROVISIONAL"
    provisional.pop("no_effect", None)
    provisional.pop("no_effect_db_binding", None)
    provisional.pop("finalization", None)
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
    return provisional


def test_finalize_fixture_orchestrates_provisional_to_pass(
    tmp_path: Path, monkeypatch
) -> None:
    import argparse
    import asyncio

    monkeypatch.setattr(
        VALIDATOR.CAPTURE, "_git_head", lambda: FIXED_COMMIT
    )
    root = _repo(tmp_path / "orchestrate")
    fixture_path, fixture_value = _fixture(root, "a")
    provisional = _provisional_capture(tmp_path, fixture_value)
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


def test_finalize_fixture_rejects_future_db_capture(
    tmp_path: Path, monkeypatch
) -> None:
    import argparse
    import asyncio

    monkeypatch.setattr(VALIDATOR.CAPTURE, "_git_head", lambda: FIXED_COMMIT)
    root = _repo(tmp_path / "future-fixture-db")
    _, fixture = _fixture(root, "a")
    provisional = _provisional_capture(tmp_path, fixture)
    provisional_path = tmp_path / "provisional.json"
    provisional_path.write_text(json.dumps(provisional), encoding="utf-8")
    _, db = _evidence(root, "phase10-live-no-effect-db-20260730.json")
    future = datetime.now(timezone.utc) + timedelta(days=1)
    db["scope"]["window_end"] = future.isoformat()
    db["captured_at"] = future.isoformat()
    db_path = tmp_path / "future-db.json"
    db_path.write_text(json.dumps(db), encoding="utf-8")
    with pytest.raises(ValueError, match="captured after fixture finalization"):
        asyncio.run(
            VALIDATOR.CAPTURE._finalize_fixture(
                argparse.Namespace(
                    fixture_evidence=provisional_path,
                    no_effect_db=db_path,
                    device_id=fixture["public_path"]["device_id"],
                ),
                "token",
            )
        )


@pytest.mark.parametrize("tamper", ("section", "summary", "source"))
def test_finalize_fixture_recomputes_provisional_gates(
    tmp_path: Path, monkeypatch, tamper: str
) -> None:
    import argparse
    import asyncio

    monkeypatch.setattr(VALIDATOR.CAPTURE, "_git_head", lambda: FIXED_COMMIT)
    root = _repo(tmp_path / tamper)
    _, fixture = _fixture(root, "a")
    provisional = _provisional_capture(tmp_path, fixture)
    if tamper == "section":
        provisional["scene"]["sections"]["nodes"]["items"].pop()
    elif tamper == "summary":
        provisional["scene"]["summary_resource"]["scene_digest"] = "sha256:tampered"
    else:
        provisional["scene"]["source_digest"] = "sha256:tampered"
    assert all(provisional["gate_results"].values())
    provisional_path = tmp_path / "provisional.json"
    provisional_path.write_text(json.dumps(provisional), encoding="utf-8")
    db_path, _ = _evidence(root, "phase10-live-no-effect-db-20260730.json")
    with pytest.raises(RuntimeError, match="fixture gates failed"):
        asyncio.run(
            VALIDATOR.CAPTURE._finalize_fixture(
                argparse.Namespace(
                    fixture_evidence=provisional_path,
                    no_effect_db=db_path,
                    device_id=fixture["public_path"]["device_id"],
                ),
                "token",
            )
        )


@pytest.mark.parametrize(
    "tamper",
    (
        "section_total",
        "duplicate_identity",
        "dangling_reference",
        "scene_counts",
        "semantic_summary",
        "repeat_identity",
    ),
)
def test_finalize_fixture_rejects_internally_inconsistent_scene(
    tmp_path: Path, monkeypatch, tamper: str
) -> None:
    import argparse
    import asyncio

    monkeypatch.setattr(VALIDATOR.CAPTURE, "_git_head", lambda: FIXED_COMMIT)
    root = _repo(tmp_path / tamper)
    _, fixture = _fixture(root, "a")
    provisional = _provisional_capture(tmp_path, fixture)
    scene = provisional["scene"]
    sections = scene["sections"]
    if tamper == "section_total":
        sections["nodes"]["total"] += 1
    elif tamper == "duplicate_identity":
        evidence = sections["evidence"]["items"]
        evidence[1]["evidence_id"] = evidence[0]["evidence_id"]
    elif tamper == "dangling_reference":
        sections["relations"]["items"][0]["evidence_ids"][0] = "evd_" + "f" * 64
    elif tamper == "scene_counts":
        scene["counts"]["nodes"] += 1
        scene["summary_resource"]["counts"]["nodes"] += 1
        scene["repeat_build"]["scene"]["counts"]["nodes"] += 1
    elif tamper == "semantic_summary":
        scene["feature_types"] = []
    else:
        scene["repeat_build"]["scene"]["engine_version"] = "tampered/1"
    provisional_path = tmp_path / "provisional.json"
    provisional_path.write_text(json.dumps(provisional), encoding="utf-8")
    db_path, _ = _evidence(root, "phase10-live-no-effect-db-20260730.json")
    with pytest.raises(RuntimeError, match="scene"):
        asyncio.run(
            VALIDATOR.CAPTURE._finalize_fixture(
                argparse.Namespace(
                    fixture_evidence=provisional_path,
                    no_effect_db=db_path,
                    device_id=fixture["public_path"]["device_id"],
                ),
                "token",
            )
        )


@pytest.mark.parametrize(
    "states",
    (("failed", "succeeded"), ("succeeded", "running")),
)
def test_validator_rejects_poll_after_terminal_state(
    tmp_path: Path, states: tuple[str, str]
) -> None:
    root = _repo(tmp_path / "-".join(states))
    fixture_path, fixture = _fixture(root, "a")
    invocations = fixture["public_path"]["tool_invocations"]
    poll_index = next(
        index for index, item in enumerate(invocations) if item["tool"] == "cad_get_job"
    )
    first = invocations[poll_index]
    second = copy.deepcopy(first)
    first["completed_at"] = first["started_at"]
    for item, state in zip((first, second), states):
        item["job_state"] = state
        item["job_result"] = {"job_id": item["job_id"], "state": state}
    invocations.insert(poll_index + 1, second)
    fixture["public_path"]["invoked_tools"] = [
        item["tool"] for item in invocations
    ]
    _save(fixture_path, fixture)
    with pytest.raises(ValueError, match="final cad_get_job poll"):
        VALIDATOR.validate(root)


def test_validator_rejects_job_result_bound_to_another_job(tmp_path: Path) -> None:
    root = _repo(tmp_path / "job-result-id")
    fixture_path, fixture = _fixture(root, "a")
    poll = next(
        item
        for item in fixture["public_path"]["tool_invocations"]
        if item["tool"] == "cad_get_job"
    )
    poll["job_result"]["job_id"] = "job-other"
    _save(fixture_path, fixture)
    with pytest.raises(ValueError, match="cad_get_job invocation arguments"):
        VALIDATOR.validate(root)


def test_validator_rejects_future_dated_evidence_bundle(tmp_path: Path) -> None:
    root = _repo(tmp_path / "future-bundle")
    future = datetime.now(timezone.utc) + timedelta(days=1)
    db_path, db = _evidence(root, "phase10-live-no-effect-db-20260730.json")
    db["captured_at"] = future.isoformat()
    _save(db_path, db)
    for name in VALIDATOR.FIXTURES:
        fixture_path, fixture = _fixture(root, name)
        fixture["finalization"]["finalized_at"] = (
            future + timedelta(seconds=1)
        ).isoformat()
        _save(fixture_path, fixture)
    restart_path, restart = _evidence(
        root, "phase10-live-gateway-restart-20260730.json"
    )
    restart["finalization"]["finalized_at"] = (
        future + timedelta(seconds=1)
    ).isoformat()
    _save(restart_path, restart)
    with pytest.raises(ValueError, match="future"):
        VALIDATOR.validate(root)


def test_capture_public_to_finalize_to_validator(tmp_path: Path, monkeypatch):
    import argparse
    import asyncio

    monkeypatch.setattr(
        VALIDATOR.CAPTURE, "_git_head", lambda: FIXED_COMMIT
    )
    monkeypatch.setattr(
        VALIDATOR.CAPTURE, "_git_baseline", lambda: FIXED_COMMIT
    )
    root = _repo(tmp_path / "e2e")
    fixture_path, fixture_value = _fixture(root, "a")
    capture_args = _capture_public_args(tmp_path, fixture_value)
    process_identity = json.loads(
        capture_args.process_identity.read_text(encoding="utf-8")
    )
    retained = fixture_value
    provisional = asyncio.run(
        VALIDATOR.CAPTURE._capture_public(
            capture_args,
            "token",
            client_factory=lambda endpoint, auth, timeout: _FakeMCPClient(
                retained
            ),
        )
    )
    provisional_path = tmp_path / "provisional-a.json"
    provisional_path.write_text(json.dumps(provisional), encoding="utf-8")
    db_path, db_value = _evidence(
        root, "phase10-live-no-effect-db-20260730.json"
    )
    invocations = provisional["public_path"]["tool_invocations"]
    first = VALIDATOR._time(
        invocations[0]["started_at"], "first invocation started_at"
    )
    last = VALIDATOR._time(
        invocations[-1]["completed_at"], "last invocation completed_at"
    )
    window_start = min(
        VALIDATOR._time(
            db_value["scope"]["window_start"], "DB window start"
        ),
        first - timedelta(seconds=60),
    )
    collection_at = datetime.now(timezone.utc)
    window_end = collection_at
    db_value["scope"]["window_start"] = window_start.isoformat()
    db_value["scope"]["window_end"] = window_end.isoformat()
    db_value["captured_at"] = collection_at.isoformat()
    session_id = process_identity["agent_session"]["session_id"]
    for record in db_value["agent_sessions"]:
        if record.get("session_id") == session_id:
            record["disconnected_at"] = None
    for index, job in enumerate(db_value["anchor_jobs"]):
        job["created_at"] = (first - timedelta(seconds=30 + index)).isoformat()
        job["updated_at"] = (last - timedelta(seconds=5 + index)).isoformat()
    for scene in db_value["scenes"]:
        scene["created_at"] = (first - timedelta(seconds=20)).isoformat()
    _save(db_path, db_value)
    args = argparse.Namespace(
        fixture_evidence=provisional_path,
        no_effect_db=db_path,
        device_id=provisional["public_path"]["device_id"],
    )
    final = asyncio.run(VALIDATOR.CAPTURE._finalize_fixture(args, "token"))
    assert final["finalization"]["implementation_commit"] == FIXED_COMMIT
    _save(fixture_path, final)
    for name in VALIDATOR.FIXTURES:
        if name == "a":
            continue
        other_path, other_value = _evidence(
            root, f"phase10-live-r25-drawing-{name}-20260730.json"
        )
        other_value["finalization"]["finalized_at"] = (
            collection_at + timedelta(seconds=1)
        ).isoformat()
        _save(other_path, other_value)
    restart_path, restart_value = _evidence(
        root, "phase10-live-gateway-restart-20260730.json"
    )
    restart_value["finalization"]["finalized_at"] = (
        VALIDATOR._time(db_value["captured_at"], "DB captured_at")
        + timedelta(seconds=1)
    ).isoformat()
    _save(restart_path, restart_value)
    VALIDATOR.validate(root)


def test_capture_public_rejects_head_change(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    root = _repo(tmp_path / "head-change")
    _, fixture_value = _fixture(root, "a")
    capture_args = _capture_public_args(tmp_path, fixture_value)
    heads = iter((FIXED_COMMIT, "b" * 40))
    monkeypatch.setattr(VALIDATOR.CAPTURE, "_git_head", lambda: next(heads))
    monkeypatch.setattr(
        VALIDATOR.CAPTURE, "_git_baseline", lambda: FIXED_COMMIT
    )
    with pytest.raises(RuntimeError, match="HEAD changed during capture"):
        asyncio.run(
            VALIDATOR.CAPTURE._capture_public(
                capture_args,
                "token",
                client_factory=lambda endpoint, auth, timeout: _FakeMCPClient(
                    fixture_value
                ),
            )
        )


def test_restart_query_records_public_producer_path(
    tmp_path: Path, monkeypatch
) -> None:
    import argparse
    import asyncio

    monkeypatch.setattr(
        VALIDATOR.CAPTURE, "_git_head", lambda: FIXED_COMMIT
    )
    monkeypatch.setattr(
        VALIDATOR.CAPTURE, "_git_baseline", lambda: FIXED_COMMIT
    )
    root = _repo(tmp_path / "restart-producer")
    _, fixture = _fixture(root, "c")
    _, restart = _evidence(
        root, "phase10-live-gateway-restart-20260730.json"
    )
    db_path, db_value = _evidence(
        root, "phase10-live-no-effect-db-20260730.json"
    )

    def write(name: str, value: dict) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    process_before = copy.deepcopy(restart["gateway_process_before"])
    process_after = copy.deepcopy(restart["gateway_process_after"])
    for process in (process_before, process_after):
        for key in VALIDATOR.CAPTURE._CLAIMED_SERVICE_KEYS:
            process.pop(key, None)
    args = argparse.Namespace(
        endpoint="https://example.invalid/mcp",
        device_id=fixture["public_path"]["device_id"],
        before=write("before.json", fixture),
        process_before=write("process-before.json", process_before),
        process_after=write("process-after.json", process_after),
        identity_before=write(
            "identity-before.json", restart["identity_capture_before"]
        ),
        identity_after=write(
            "identity-after.json", restart["identity_capture_after"]
        ),
        capture_command="restart-query (test)",
        operator="test",
    )
    result = asyncio.run(
        VALIDATOR.CAPTURE._restart_query(
            args,
            "token",
            client_factory=lambda endpoint, auth, timeout: _FakeMCPClient(
                fixture
            ),
        )
    )
    invocations = result["post_restart_public_path"]["tool_invocations"]
    assert [item["tool"] for item in invocations] == [
        "cad_list_devices",
        *("cad_query_scene" for _ in VALIDATOR.SECTIONS),
        "read_resource",
    ]
    assert result["post_restart_public_path"]["write_tools_invoked"] == []
    assert result["status"] == "PROVISIONAL"
    collection_at = datetime.now(timezone.utc)
    db_value["scope"]["window_start"] = invocations[0]["started_at"]
    db_value["scope"]["window_end"] = collection_at.isoformat()
    db_value["captured_at"] = collection_at.isoformat()
    db_path = tmp_path / "restart-db.json"
    db_path.write_text(json.dumps(db_value), encoding="utf-8")
    provisional_path = tmp_path / "restart-provisional.json"
    provisional_path.write_text(json.dumps(result), encoding="utf-8")
    before_path = tmp_path / "restart-before.json"
    before_path.write_text(json.dumps(fixture), encoding="utf-8")
    final = asyncio.run(
        VALIDATOR.CAPTURE._finalize_restart(
            argparse.Namespace(
                restart_evidence=provisional_path,
                before=before_path,
                no_effect_db=db_path,
                device_id=fixture["public_path"]["device_id"],
            ),
            "token",
        )
    )
    assert final["status"] == "PASS"
    assert VALIDATOR._time(invocations[-1]["completed_at"], "last") <= collection_at


@pytest.mark.parametrize("tamper", ("before_identity", "after_capture"))
def test_validator_rejects_restart_trace_outside_capture_window(
    tmp_path: Path, tamper: str
) -> None:
    root = _repo(tmp_path / tamper)
    path, restart = _evidence(
        root, "phase10-live-gateway-restart-20260730.json"
    )
    invocations = restart["post_restart_public_path"]["tool_invocations"]
    if tamper == "before_identity":
        before = VALIDATOR._time(
            restart["identity_capture_after"]["captured_at"], "identity-after"
        ) - timedelta(seconds=1)
        invocations[0]["started_at"] = before.isoformat()
        invocations[0]["completed_at"] = (before + timedelta(milliseconds=1)).isoformat()
    else:
        after = VALIDATOR._time(restart["captured_at"], "restart captured_at")
        invocations[-1]["started_at"] = after.isoformat()
        invocations[-1]["completed_at"] = (after + timedelta(milliseconds=1)).isoformat()
    _save(path, restart)
    with pytest.raises(ValueError, match="outside the post-restart window"):
        VALIDATOR.validate(root)


def test_validator_rejects_future_restart_db_capture(tmp_path: Path) -> None:
    root = _repo(tmp_path / "future-db-validator")
    restart_path, restart = _evidence(
        root, "phase10-live-gateway-restart-20260730.json"
    )
    _, db = _evidence(root, "phase10-live-no-effect-db-20260730.json")
    future = datetime.now(timezone.utc) + timedelta(days=1)
    db["scope"]["window_end"] = future.isoformat()
    db["captured_at"] = future.isoformat()
    _save(root / "docs" / "architecture" / "evidence" / "phase10-live-no-effect-db-20260730.json", db)
    with pytest.raises(ValueError, match="captured after fixture finalization"):
        VALIDATOR.validate(root)


def test_finalize_restart_rejects_future_db_capture(
    tmp_path: Path, monkeypatch
) -> None:
    import argparse
    import asyncio

    monkeypatch.setattr(VALIDATOR.CAPTURE, "_git_head", lambda: FIXED_COMMIT)
    root = _repo(tmp_path / "future-db-finalizer")
    _, fixture = _fixture(root, "c")
    _, restart = _evidence(root, "phase10-live-gateway-restart-20260730.json")
    restart["schema_version"] = "cad.phase10-live-gateway-restart-provisional/1"
    restart["status"] = "PROVISIONAL"
    restart.pop("finalization", None)
    restart_path = tmp_path / "restart-provisional.json"
    restart_path.write_text(json.dumps(restart), encoding="utf-8")
    _, db = _evidence(root, "phase10-live-no-effect-db-20260730.json")
    future = datetime.now(timezone.utc) + timedelta(days=1)
    db["scope"]["window_end"] = future.isoformat()
    db["captured_at"] = future.isoformat()
    db_path = tmp_path / "future-db.json"
    db_path.write_text(json.dumps(db), encoding="utf-8")
    before_path = tmp_path / "before.json"
    before_path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(ValueError, match="DB window"):
        asyncio.run(
            VALIDATOR.CAPTURE._finalize_restart(
                argparse.Namespace(
                    restart_evidence=restart_path,
                    before=before_path,
                    no_effect_db=db_path,
                    device_id=fixture["public_path"]["device_id"],
                ),
                "token",
            )
        )


@pytest.mark.parametrize("tamper", ("gateway", "section", "summary"))
def test_finalize_restart_recomputes_provisional_gates(
    tmp_path: Path, monkeypatch, tamper: str
) -> None:
    import argparse
    import asyncio

    monkeypatch.setattr(VALIDATOR.CAPTURE, "_git_head", lambda: FIXED_COMMIT)
    root = _repo(tmp_path / tamper)
    _, fixture = _fixture(root, "c")
    _, restart = _evidence(root, "phase10-live-gateway-restart-20260730.json")
    restart["schema_version"] = "cad.phase10-live-gateway-restart-provisional/1"
    restart["status"] = "PROVISIONAL"
    restart.pop("finalization", None)
    if tamper == "gateway":
        restart["gateway_process_after"]["gateway_service_record"]["process"][
            "executable_sha256"
        ] = "sha256:" + "f" * 64
    elif tamper == "section":
        restart["post_restart_sections"]["nodes"]["items"].pop()
    else:
        restart["post_restart_summary_resource"]["scene_digest"] = "sha256:tampered"
    assert all(restart["gate_results"].values())
    restart_path = tmp_path / "restart-provisional.json"
    restart_path.write_text(json.dumps(restart), encoding="utf-8")
    _, db = _evidence(root, "phase10-live-no-effect-db-20260730.json")
    db["captured_at"] = datetime.now(timezone.utc).isoformat()
    db_path = tmp_path / "db.json"
    db_path.write_text(json.dumps(db), encoding="utf-8")
    before_path = tmp_path / "before.json"
    before_path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(RuntimeError, match="restart gates failed"):
        asyncio.run(
            VALIDATOR.CAPTURE._finalize_restart(
                argparse.Namespace(
                    restart_evidence=restart_path,
                    before=before_path,
                    no_effect_db=db_path,
                    device_id=fixture["public_path"]["device_id"],
                ),
                "token",
            )
        )


def test_finalize_rejects_observation_job_outside_db_scope(
    tmp_path: Path, monkeypatch
) -> None:
    import argparse
    import asyncio

    monkeypatch.setattr(VALIDATOR.CAPTURE, "_git_head", lambda: FIXED_COMMIT)
    root = _repo(tmp_path / "anchor-scope")
    _, fixture = _fixture(root, "a")
    provisional = _provisional_capture(tmp_path, fixture)
    provisional_path = tmp_path / "provisional-anchor.json"
    provisional_path.write_text(json.dumps(provisional), encoding="utf-8")
    _, db = _evidence(root, "phase10-live-no-effect-db-20260730.json")
    db["scope"]["anchor_job_ids"] = [
        provisional["session_binding"]["observation_job_ids"][0]
    ]
    db_path = tmp_path / "db-anchor.json"
    db_path.write_text(json.dumps(db), encoding="utf-8")
    args = argparse.Namespace(
        fixture_evidence=provisional_path,
        no_effect_db=db_path,
        device_id=provisional["public_path"]["device_id"],
    )
    with pytest.raises(ValueError, match="absent from the no-effect DB scope"):
        asyncio.run(VALIDATOR.CAPTURE._finalize_fixture(args, "token"))


def test_cli_finalize_fixture_runs_without_token_file(
    tmp_path: Path, monkeypatch
) -> None:
    import sys

    monkeypatch.setattr(
        VALIDATOR.CAPTURE, "_git_head", lambda: FIXED_COMMIT
    )
    root = _repo(tmp_path / "cli")
    fixture_path, fixture_value = _fixture(root, "a")
    provisional = _provisional_capture(tmp_path, fixture_value)
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
    parser.parse_args(
        [
            "finalize-restart",
            "--restart-evidence",
            "restart.json",
            "--before",
            "before.json",
            "--no-effect-db",
            "db.json",
            "--device-id",
            "device",
            "--output",
            "final-restart.json",
        ]
    )
    parser.parse_args(
        [
            "restart-query",
            "--device-id",
            "device",
            "--token-file",
            "token.json",
            "--output",
            "restart.json",
            "--before",
            "before.json",
            "--process-before",
            "process-before.json",
            "--process-after",
            "process-after.json",
            "--identity-before",
            "identity-before.json",
            "--identity-after",
            "identity-after.json",
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


def test_fixture_letter_parser_and_key_builder() -> None:
    parser = VALIDATOR.CAPTURE._fixture_letter_from_id
    builder = VALIDATOR.CAPTURE._phase10_key
    assert parser("phase10-drawing-a-r25/1") == "a"
    assert parser("phase10-drawing-c-r25/1") == "c"
    assert (
        builder("a", "before", "20260730000000")
        == "phase10-drawing-a-before-20260730000000"
    )
    for invalid in (
        "phase10-drawing-r25/1",
        "phase10-drawing-a-r26/1",
        "phase10-drawing-a-r25/2",
        "phase10-drawing-d-r25/1",
        "not-a-fixture",
    ):
        with pytest.raises(ValueError):
            parser(invalid)
    with pytest.raises(ValueError):
        builder("r25", "before", "20260730000000")
    with pytest.raises(ValueError):
        builder("a", "unknown", "20260730000000")
    with pytest.raises(ValueError):
        builder("a", "before", "2026")


def test_finalize_rejects_cross_commit_finalization(
    tmp_path: Path, monkeypatch
) -> None:
    import argparse
    import asyncio

    monkeypatch.setattr(
        VALIDATOR.CAPTURE, "_git_head", lambda: FIXED_COMMIT
    )
    root = _repo(tmp_path / "cross-commit")
    _, fixture_value = _fixture(root, "a")
    provisional = _provisional_capture(tmp_path, fixture_value)
    provisional_path = tmp_path / "provisional-a.json"
    provisional_path.write_text(json.dumps(provisional), encoding="utf-8")
    db_path, _ = _evidence(root, "phase10-live-no-effect-db-20260730.json")
    args = argparse.Namespace(
        fixture_evidence=provisional_path,
        no_effect_db=db_path,
        device_id=provisional["public_path"]["device_id"],
    )
    monkeypatch.setattr(
        VALIDATOR.CAPTURE, "_git_head", lambda: "b" * 40
    )
    with pytest.raises(ValueError, match="finalizer commit differs"):
        asyncio.run(VALIDATOR.CAPTURE._finalize_fixture(args, "token"))


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
        ("restart_public_trace", "invocation trace is incomplete"),
        ("restart_raw", "raw identity capture ordering"),
        ("restart_raw_service", "reports service"),
        ("identity_hash", "standalone Agent identity differs"),
        ("identity_pid", "AutoCAD runtime identity is invalid"),
        ("identity_session", "absent from the no-effect DB evidence"),
        ("identity_commit", "gateway runtime identity is not cross-bound"),
        ("identity_time", "not before capture"),
        ("identity_start_after_invocation", "not before capture"),
        ("session_connected_mismatch", "connected_at differs between runtime identity and DB"),
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
        ("invocation_terminal_result", "cad_get_job invocation arguments"),
        ("invocation_reordered", "not stored chronologically"),
        ("invocation_after_before_resource", "out of phase"),
        ("invocation_query_before_build", "out of phase"),
        ("invocation_second_summary", "out of phase"),
        ("invocation_interleaved_poll", "out of phase"),
        ("key_fixture_letter", "cad_observe invocation arguments"),
        ("key_old_format", "cad_observe invocation arguments"),
        ("key_stamp_mismatch", "not bound to one capture run"),
        ("key_reused", "cad_build_scene invocation arguments"),
        ("key_bad_stamp", "cad_observe invocation arguments"),
        ("finalization_commit", "finalization provenance is invalid"),
        ("db_commit", "commit provenance differs"),
        ("session_binding", "session binding is not cross-bound"),
        ("db_window_end", "window does not cover the fixture capture"),
        ("db_sha", "durable no-write snapshot"),
        ("db_freshness", "before the audit window closed"),
        ("db_row_freshness", "anchor job timestamp exceeds the DB capture time"),
        ("db_anchor_owner", "anchor job is not cross-bound"),
        ("db_scene_binding", "scene record is not cross-bound"),
        ("db_scope_device", "scope device differs"),
        ("db_job_device", "anchor job is not cross-bound"),
        ("db_scene_device", "scene record is not cross-bound"),
        ("db_session_device", "session is not cross-bound"),
        ("db_scope_scene", "scene is absent"),
        ("db_owner_mismatch", "session is not cross-bound"),
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
