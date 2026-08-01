from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "phase10-live-public-evidence.py"
SPEC = importlib.util.spec_from_file_location("phase10_live_public_evidence", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

COMMIT = "a" * 40
DEVICE = "device-live"
SCENE_ID = "scene-live"


def _processes() -> tuple[dict, dict]:
    working_directory = f"/opt/releases/{COMMIT[:7]}/services/gateway"
    common_properties = {
        "Id": "autocad-mcp-phase4.service",
        "ActiveState": "active",
        "SubState": "running",
        "ExecStart": "{ path=/usr/bin/python3.12 ; argv[]=/usr/bin/python3.12 app.py }",
        "WorkingDirectory": working_directory,
    }
    common_process_record = {
        "source": "procfs",
        "executable": "/usr/bin/python3.12",
        "executable_sha256": "sha256:" + "b" * 64,
    }
    common_release = {
        "source": "git_rev_parse",
        "working_directory": working_directory,
        "commit": COMMIT,
    }
    common_service = {
        "source": "systemctl_show",
        "release": common_release,
    }
    common_process = {
        "desktop_agent_pid": 300,
        "desktop_agent_executable": "agent.exe",
        "desktop_agent_sha256": "sha256:" + "c" * 64,
        "device_id": DEVICE,
        "fixture_id": "phase10-drawing-c-r25/1",
        "scene_id": SCENE_ID,
        "source_digest": "source-digest",
        "scene_digest": "scene-digest",
        "document_id": "document-live",
        "document_revision": "revision-live",
    }
    before = {
        **common_process,
        "gateway_pid": 100,
        "agent_session_id": "session-before",
        "gateway_service_record": {
            **common_service,
            "properties": {
                **common_properties,
                "MainPID": 100,
                "ExecMainStartTimestampMonotonic": "1000",
            },
            "process": {
                **common_process_record,
                "pid": 100,
                "start_identity": "start-before",
            },
        },
    }
    after = {
        **common_process,
        "gateway_pid": 200,
        "agent_session_id": "session-after",
        "gateway_service_record": {
            **common_service,
            "properties": {
                **common_properties,
                "MainPID": 200,
                "ExecMainStartTimestampMonotonic": "2000",
            },
            "process": {
                **common_process_record,
                "pid": 200,
                "start_identity": "start-after",
            },
        },
        "old_gateway_process_exit": {
            "source": "procfs",
            "pid": 100,
            "start_identity": "start-before",
            "proc_stat_after": None,
            "probe_exit_code": 0,
        },
    }
    return before, after


def _db() -> dict:
    tables = {"write_jobs": [], "workflow_write_actions": []}
    digest = MODULE._snapshot_digest(tables)
    return {
        "schema_version": "cad.phase10-live-db-evidence/1",
        "implementation_commit": COMMIT,
        "scope": {
            "owner_subject": "owner-live",
            "device_id": DEVICE,
            "scene_ids": [SCENE_ID],
        },
        "retrospective_no_write_events": [],
        "anchor_jobs": [
            {
                "job_id": "job-read",
                "device_id": DEVICE,
                "effect_class": "read",
                "state": "succeeded",
            }
        ],
        "agent_sessions": [
            {
                "session_id": "session-before",
                "device_id": DEVICE,
                "disconnected_at": "2026-07-30T00:01:00+00:00",
            },
            {
                "session_id": "session-after",
                "device_id": DEVICE,
                "disconnected_at": None,
            },
        ],
        "active_agent_session_id": "session-after",
        "write_snapshot": {"sha256": digest, "tables": copy.deepcopy(tables)},
        "restart_comparison": {
            "pre_restart_active_agent_session_id": "session-before",
            "post_restart_active_agent_session_id": "session-after",
            "pre_restart_write_snapshot": {
                "sha256": digest,
                "tables": copy.deepcopy(tables),
            },
            "post_restart_write_snapshot_sha256": digest,
        },
    }


def _scene_query() -> tuple[dict, dict, dict, dict]:
    scene = {
        "scene_id": SCENE_ID,
        "scene_digest": "scene-digest",
        "source_digest": "source-digest",
        "document_revision": "revision-live",
        "counts": {
            "nodes": 1,
            "relations": 1,
            "contours": 1,
            "features": 1,
            "issues": 1,
            "evidence": 1,
            "omitted": 0,
        },
        "sections": {},
    }
    sections = {}
    for name in MODULE.SECTIONS:
        item = {"id": name}
        scene["sections"][name] = {"items": [item], "total": 1}
        sections[name] = {
            "items": [item],
            "total": 1,
            "scene_id": SCENE_ID,
            "scene_digest": "scene-digest",
        }
    resource = {
        "schema_version": "cad.scene/1",
        "scene_id": SCENE_ID,
        "scene_digest": "scene-digest",
        "source_digest": "source-digest",
        "document_revision": "revision-live",
        "counts": scene["counts"],
    }
    devices = {
        "devices": [
            {
                "device_id": DEVICE,
                "status": "online",
                "paused": False,
                "runtime_state": "online_idle",
            }
        ]
    }
    return scene, sections, resource, devices


def test_restart_inputs_derive_all_gates_from_raw_records() -> None:
    before, after = _processes()
    assert all(
        MODULE._service_restart_gates(
            before, after, implementation_commit=COMMIT
        ).values()
    )
    assert all(
        MODULE._db_restart_gates(
            _db(),
            before,
            after,
            device_id=DEVICE,
            scene_id=SCENE_ID,
            implementation_commit=COMMIT,
        ).values()
    )
    scene, sections, resource, devices = _scene_query()
    assert all(
        MODULE._public_scene_gates(
            scene, sections, resource, devices, device_id=DEVICE
        ).values()
    )
    expected = {
        name: before[name]
        for name in (
            "device_id",
            "fixture_id",
            "scene_id",
            "source_digest",
            "scene_digest",
            "document_id",
            "document_revision",
        )
    }
    assert MODULE._process_identity_bound(before, after, expected)
    after.pop("document_revision")
    assert not MODULE._process_identity_bound(before, after, expected)


@pytest.mark.parametrize(
    ("change", "failed_gate"),
    [
        (
            ("after", "old_gateway_process_exit", "proc_stat_after", "still-alive"),
            "old_gateway_process_exited",
        ),
        (("after", "properties", "MainPID", 100), "authoritative_gateway_service"),
        (("after", "process", "start_identity", "start-before"), "actual_gateway_process_restart"),
        (("after", "properties", "Id", "other.service"), "authoritative_gateway_service"),
        (("after", "release", "commit", "d" * 40), "gateway_runtime_identity"),
        (("after", "process", "executable", "python"), "gateway_runtime_identity"),
    ],
)
def test_service_restart_rejects_unproven_or_mismatched_identity(
    change: tuple[str, str, str, object], failed_gate: str
) -> None:
    before, after = _processes()
    target = before if change[0] == "before" else after
    if change[1] == "old_gateway_process_exit":
        target[change[1]][change[2]] = change[3]
    else:
        target["gateway_service_record"][change[1]][change[2]] = change[3]
    gates = MODULE._service_restart_gates(
        before, after, implementation_commit=COMMIT
    )
    assert gates[failed_gate] is False


@pytest.mark.parametrize(
    ("mutate", "failed_gate"),
    [
        ("write_event", "no_write_events_in_window"),
        ("snapshot", "write_snapshot_unchanged"),
        ("session", "gateway_public_reconnect"),
        ("write_job", "anchor_jobs_read_only"),
        ("scene", "db_scope_bound"),
        ("commit", "db_evidence_provenance"),
    ],
)
def test_db_restart_rejects_write_or_unbound_records(
    mutate: str, failed_gate: str
) -> None:
    before, after = _processes()
    evidence = _db()
    if mutate == "write_event":
        evidence["retrospective_no_write_events"] = [{"event": "write"}]
    elif mutate == "snapshot":
        evidence["write_snapshot"]["tables"]["write_jobs"] = [{"job_id": "write"}]
    elif mutate == "session":
        evidence["active_agent_session_id"] = "different-session"
    elif mutate == "write_job":
        evidence["anchor_jobs"][0]["effect_class"] = "write"
    elif mutate == "commit":
        evidence["implementation_commit"] = "d" * 40
    else:
        evidence["scope"]["scene_ids"] = ["other-scene"]
    gates = MODULE._db_restart_gates(
        evidence,
        before,
        after,
        device_id=DEVICE,
        scene_id=SCENE_ID,
        implementation_commit=COMMIT,
    )
    assert gates[failed_gate] is False


@pytest.mark.parametrize("mutate", ("section", "resource", "device"))
def test_public_query_rejects_partial_or_unbound_results(mutate: str) -> None:
    scene, sections, resource, devices = _scene_query()
    if mutate == "section":
        sections["issues"]["items"] = []
    elif mutate == "resource":
        resource["scene_digest"] = "wrong-digest"
    else:
        devices["devices"][0]["status"] = "offline"
    gates = MODULE._public_scene_gates(
        scene, sections, resource, devices, device_id=DEVICE
    )
    assert not all(gates.values())


def _identity_capture(
    pid: int,
    start_identity: str,
    monotonic: str,
    *,
    captured_at: str,
    old_pid: int | None = None,
) -> dict:
    working_directory = f"/opt/releases/{COMMIT[:7]}/services/gateway"
    executable = "/usr/bin/python3.12"
    executable_hex = "b" * 64
    systemctl_stdout = "\n".join(
        (
            "Id=autocad-mcp-phase4.service",
            "ActiveState=active",
            "SubState=running",
            f"MainPID={pid}",
            f"ExecMainStartTimestampMonotonic={monotonic}",
            f"WorkingDirectory={working_directory}",
            f"ExecStart={{ path={executable} ; argv[]={executable} app.py }}",
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
                "autocad-mcp-phase4.service",
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
            "stdout": f"{executable_hex}  {executable}\n",
            "stderr": "",
            "exit_code": 0,
            "captured_at": captured_at,
        },
        {
            "command": ["git", "-C", working_directory, "rev-parse", "HEAD"],
            "stdout": COMMIT + "\n",
            "stderr": "",
            "exit_code": 0,
            "captured_at": captured_at,
        },
        {
            "command": ["awk", "{print $22}", f"/proc/{pid}/stat"],
            "stdout": start_identity + "\n",
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
        "service": "autocad-mcp-phase4.service",
        "captured_at": captured_at,
        "capture_command": "capture-identity",
        "operator": "test",
        "commands": commands,
    }


def test_derive_gateway_identity_parses_raw_authoritative_output() -> None:
    before = _identity_capture(
        100,
        "12345",
        "1000",
        captured_at="2026-07-30T00:00:00+00:00",
    )
    after = _identity_capture(
        200,
        "67890",
        "2000",
        captured_at="2026-07-30T00:01:00+00:00",
        old_pid=100,
    )
    derived_before = MODULE._derive_gateway_identity(before)
    derived_after = MODULE._derive_gateway_identity(after)
    assert derived_before["gateway_pid"] == 100
    assert derived_after["gateway_pid"] == 200
    assert (
        derived_before["gateway_service_record"]["process"]["start_identity"]
        == "12345"
    )
    assert (
        derived_before["gateway_service_record"]["process"]["executable_sha256"]
        == "sha256:" + "b" * 64
    )
    assert (
        derived_before["gateway_service_record"]["release"]["commit"] == COMMIT
    )
    assert derived_after["exit_probe"] is not None
    assert f"/proc/100/stat" in tuple(derived_after["exit_probe"]["command"])


def test_derive_rejects_incomplete_or_fabricated_raw_capture() -> None:
    identity = _identity_capture(
        100, "start", "1000", captured_at="2026-07-30T00:00:00+00:00"
    )
    identity["commands"][0]["stdout"] = (
        "Id=other.service\nActiveState=active\nSubState=running\n"
        "MainPID=100\nExecMainStartTimestampMonotonic=1000\n"
        "WorkingDirectory=/opt/releases\nExecStart=x\n"
    )
    with pytest.raises(ValueError, match="reports service"):
        MODULE._derive_gateway_identity(identity)

    no_probe = _identity_capture(
        200, "67890", "2000", captured_at="2026-07-30T00:01:00+00:00"
    )
    assert MODULE._derive_gateway_identity(no_probe)["exit_probe"] is None

    tampered = _identity_capture(
        100, "12345", "1000", captured_at="2026-07-30T00:00:00+00:00"
    )
    tampered["commands"][1]["stdout"] = "python\n"
    with pytest.raises(ValueError, match="executable"):
        MODULE._derive_gateway_identity(tampered)


def test_derive_rejects_ambiguous_or_misbound_raw_commands() -> None:
    base = _identity_capture(
        100,
        "12345",
        "1000",
        captured_at="2026-07-30T00:00:00+00:00",
        old_pid=100,
    )

    probe_alive = copy.deepcopy(base)
    for record in probe_alive["commands"]:
        if record["command"][0] == "test":
            record["command"] = ["test", "-e", "/proc/100/stat"]
            record["exit_code"] = 0
    with pytest.raises(ValueError, match="absence probe"):
        MODULE._derive_gateway_identity(probe_alive)

    wrong_sha = copy.deepcopy(base)
    for record in wrong_sha["commands"]:
        if record["command"][0] == "sha256sum":
            record["command"] = ["sha256sum", "/usr/bin/python3.13"]
    with pytest.raises(ValueError, match="exact executable"):
        MODULE._derive_gateway_identity(wrong_sha)

    wrong_git = copy.deepcopy(base)
    for record in wrong_git["commands"]:
        if record["command"][0] == "git":
            record["command"] = ["git", "-C", "/opt/other", "rev-parse", "HEAD"]
    with pytest.raises(ValueError, match="WorkingDirectory"):
        MODULE._derive_gateway_identity(wrong_git)

    wrong_awk = copy.deepcopy(base)
    for record in wrong_awk["commands"]:
        if record["command"][0] == "awk":
            record["command"] = ["awk", "{print $1}", "/proc/100/stat"]
    with pytest.raises(ValueError, match="start-time"):
        MODULE._derive_gateway_identity(wrong_awk)

    stale = copy.deepcopy(base)
    stale["commands"][0]["captured_at"] = "2026-07-29T00:00:00+00:00"
    with pytest.raises(ValueError, match="outside the capture window"):
        MODULE._derive_gateway_identity(stale)

    duplicate = copy.deepcopy(base)
    duplicate["commands"].append(copy.deepcopy(duplicate["commands"][-1]))
    with pytest.raises(ValueError, match="duplicate command"):
        MODULE._derive_gateway_identity(duplicate)


def test_restart_inputs_reject_caller_supplied_service_records() -> None:
    process = {
        "device_id": DEVICE,
        "gateway_service_record": {"source": "systemctl_show"},
    }
    with pytest.raises(ValueError, match="caller-supplied"):
        MODULE._reject_claimed_service_records(process, "process-before")

    process = {
        "device_id": DEVICE,
        "old_gateway_process_exit": {"source": "procfs"},
    }
    with pytest.raises(ValueError, match="caller-supplied"):
        MODULE._reject_claimed_service_records(process, "process-after")

    assert MODULE._reject_claimed_service_records(
        {"device_id": DEVICE}, "process-before"
    ) is None
