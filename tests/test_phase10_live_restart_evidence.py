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
