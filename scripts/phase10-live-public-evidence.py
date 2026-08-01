"""Capture auditable Phase 10 evidence through the public Gateway/Agent path."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if os.name == "nt":
    import ctypes
    import msvcrt

REPO_ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("nodes", "relations", "contours", "features", "issues", "evidence")
SERVICE_UNIT = "autocad-mcp-phase4.service"
WRITE_TOOLS = {
    "cad_commit",
    "cad_commit_rollback",
    "cad_control_workflow",
    "cad_prepare_program",
    "cad_preview",
    "cad_preview_rollback",
    "cad_start_workflow",
}
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_GATEWAY_PROCESS_KEYS = {
    "executable",
    "executable_sha256",
    "process_id",
    "release_commit",
    "service",
    "working_directory",
}
_DESKTOP_AGENT_KEYS = {
    "executable",
    "executable_sha256",
    "process_id",
    "standalone",
    "started_at",
}
_AUTOCAD_PROCESS_KEYS = {
    "edition",
    "executable",
    "file_version",
    "host_family",
    "process_id",
    "product",
    "release_year",
    "series",
}
_AGENT_SESSION_KEYS = {
    "agent_version",
    "connected_at",
    "device_id",
    "managed_host",
    "protocol_version",
    "session_id",
}
_CLAIMED_SERVICE_KEYS = (
    "gateway_service_record",
    "gateway_previous_process_confirmed_exited",
    "gateway_service",
    "old_gateway_process_exit",
)


def _payload(result: Any) -> Any:
    if isinstance(result, list):
        return [_payload(item) for item in result]
    if isinstance(result, dict):
        return {key: _payload(value) for key, value in result.items()}
    if getattr(result, "structured_content", None) is not None:
        return result.structured_content
    if getattr(result, "data", None) is not None:
        return result.data
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return result


def _resource_payload(result: Any) -> Any:
    value = _payload(result)
    if (
        isinstance(value, list)
        and len(value) == 1
        and isinstance(value[0], dict)
        and isinstance(value[0].get("text"), str)
    ):
        return json.loads(value[0]["text"])
    return value


def _sha256(path: Path) -> str:
    if os.name != "nt":
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    handle = ctypes.windll.kernel32.CreateFileW(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000007,  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
        None,
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError()
    with os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY), "rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_baseline() -> str:
    return subprocess.run(
        ["git", "merge-base", "HEAD", "origin/main"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _snapshot_digest(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _normalize_timestamp(text: str) -> str:
    match = re.search(r"\.\d+", text)
    if match:
        digits = match.group(0)[1:]
        normalized = (digits + "000000")[:6]
        text = text[: match.start()] + "." + normalized + text[match.end() :]
    return text


def _parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label}: timestamp is required")
    text = _normalize_timestamp(value.replace("Z", "+00:00"))
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{label}: invalid timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label}: timestamp must be timezone-aware")
    return parsed


def _run_raw_command(command: list[str], timeout: int = 30) -> dict[str, Any]:
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout
    )
    return {
        "command": list(command),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "exit_code": completed.returncode,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def _parse_systemctl_properties(stdout: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            properties[key] = value
    return properties


def _derive_gateway_identity(identity: dict[str, Any]) -> dict[str, Any]:
    if identity.get("schema_version") != "cad.phase10-live-identity/1":
        raise ValueError("identity capture schema_version is invalid")
    commands = identity.get("commands")
    if not isinstance(commands, list) or not commands:
        raise ValueError("identity capture contains no raw commands")
    records: dict[tuple[str, ...], dict[str, Any]] = {}
    for record in commands:
        if not isinstance(record, dict) or not isinstance(
            record.get("command"), list
        ):
            raise ValueError("identity capture command record is invalid")
        records[tuple(record["command"])] = record

    def find(prefix: tuple[str, ...]) -> dict[str, Any]:
        for command, record in records.items():
            if command[: len(prefix)] == prefix:
                return record
        raise ValueError(
            f"identity capture is missing raw command {' '.join(prefix)!r}"
        )

    service = identity.get("service") or SERVICE_UNIT
    systemctl = find(("systemctl", "show"))
    if systemctl.get("exit_code") != 0:
        raise ValueError("systemctl show failed; service identity is unavailable")
    properties = _parse_systemctl_properties(systemctl.get("stdout", ""))
    required_properties = {
        "Id",
        "ActiveState",
        "SubState",
        "MainPID",
        "ExecMainStartTimestampMonotonic",
        "WorkingDirectory",
        "ExecStart",
    }
    if not required_properties <= set(properties):
        raise ValueError("systemctl show output is incomplete")
    if properties.get("Id") != service:
        raise ValueError(
            f"systemctl reports service {properties.get('Id')!r}, expected {service!r}"
        )
    try:
        pid = int(properties["MainPID"])
    except (TypeError, ValueError):
        raise ValueError("systemctl MainPID is not an integer") from None
    if pid <= 0:
        raise ValueError("systemctl MainPID is not running")
    properties["MainPID"] = pid

    stat = find(("awk",))
    if stat.get("exit_code") != 0 or f"/proc/{pid}/stat" not in tuple(
        stat.get("command", [])
    ):
        raise ValueError("proc start identity was not read from the service PID")
    start_identity = stat.get("stdout", "").strip()
    if not start_identity.isdigit():
        raise ValueError("proc start identity is unavailable")

    exe = find(("readlink",))
    executable = exe.get("stdout", "").strip()
    if exe.get("exit_code") != 0 or not executable.startswith("/"):
        raise ValueError("proc executable identity is unavailable")
    if f"/proc/{pid}/exe" not in tuple(exe.get("command", [])):
        raise ValueError("proc executable was not read from the service PID")

    digest = find(("sha256sum",))
    if digest.get("exit_code") != 0:
        raise ValueError("executable hash command failed")
    fields = digest.get("stdout", "").split()
    raw_hash = fields[0] if fields else ""
    if re.fullmatch(r"[0-9a-f]{64}", raw_hash):
        executable_hash = "sha256:" + raw_hash
    elif _SHA256_RE.fullmatch(raw_hash):
        executable_hash = raw_hash
    else:
        raise ValueError("executable hash is not a sha256 digest")

    release = find(("git", "-C"))
    if release.get("exit_code") != 0:
        raise ValueError("git rev-parse failed in the service working directory")
    commit = release.get("stdout", "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("deployed commit identity is invalid")

    exit_probe: dict[str, Any] | None = None
    for record in commands:
        command = tuple(record.get("command", []))
        if command and command[0] == "test":
            if record.get("exit_code") != 0:
                raise ValueError("old Gateway process absence probe failed")
            exit_probe = {"source": "procfs", "command": list(command)}
            break
    return {
        "gateway_pid": pid,
        "gateway_service_record": {
            "source": "systemctl_show",
            "properties": properties,
            "process": {
                "source": "procfs",
                "pid": pid,
                "start_identity": start_identity,
                "executable": executable,
                "executable_sha256": executable_hash,
            },
            "release": {
                "source": "git_rev_parse",
                "working_directory": properties.get("WorkingDirectory", ""),
                "commit": commit,
            },
        },
        "exit_probe": exit_probe,
    }


def _reject_claimed_service_records(
    process: dict[str, Any], label: str
) -> None:
    claimed = sorted(key for key in _CLAIMED_SERVICE_KEYS if key in process)
    if claimed:
        raise ValueError(
            f"{label}: caller-supplied process JSON claims {claimed}; "
            "service/process identity must come from a raw identity capture"
        )


def _validate_runtime_identity(
    identity: dict[str, Any],
    *,
    device_id: str,
    implementation_commit: str,
) -> None:
    for name, keys in (
        ("gateway_process", _GATEWAY_PROCESS_KEYS),
        ("desktop_agent_process", _DESKTOP_AGENT_KEYS),
        ("autocad_process", _AUTOCAD_PROCESS_KEYS),
        ("agent_session", _AGENT_SESSION_KEYS),
    ):
        value = identity.get(name)
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError(f"runtime identity {name} is incomplete")
    gateway = identity["gateway_process"]
    desktop = identity["desktop_agent_process"]
    autocad = identity["autocad_process"]
    session = identity["agent_session"]
    if not isinstance(gateway["process_id"], int) or gateway["process_id"] <= 0:
        raise ValueError("runtime identity gateway process_id is invalid")
    if (
        not isinstance(gateway["executable"], str)
        or not gateway["executable"].startswith("/")
    ):
        raise ValueError("runtime identity gateway executable is invalid")
    if not _SHA256_RE.fullmatch(gateway["executable_sha256"]):
        raise ValueError("runtime identity gateway executable hash is invalid")
    if gateway["service"] != SERVICE_UNIT:
        raise ValueError("runtime identity gateway service is unexpected")
    if not re.fullmatch(r"[0-9a-f]{40}", gateway["release_commit"]):
        raise ValueError("runtime identity gateway release commit is invalid")
    if gateway["release_commit"] != implementation_commit:
        raise ValueError("runtime identity gateway commit differs from capture")
    if (
        not isinstance(gateway["working_directory"], str)
        or implementation_commit[:7] not in gateway["working_directory"]
    ):
        raise ValueError("runtime identity gateway working directory is invalid")
    if not isinstance(desktop["process_id"], int) or desktop["process_id"] <= 0:
        raise ValueError("runtime identity desktop process_id is invalid")
    if not isinstance(desktop["executable"], str) or not desktop["executable"]:
        raise ValueError("runtime identity desktop executable is invalid")
    if not _SHA256_RE.fullmatch(desktop["executable_sha256"]):
        raise ValueError("runtime identity desktop executable hash is invalid")
    if desktop.get("standalone") is not True:
        raise ValueError("runtime identity desktop agent is not standalone")
    _parse_timestamp(desktop["started_at"], "runtime identity desktop started_at")
    if not isinstance(autocad["process_id"], int) or autocad["process_id"] <= 0:
        raise ValueError("runtime identity autocad process_id is invalid")
    if not isinstance(autocad["executable"], str) or not autocad["executable"]:
        raise ValueError("runtime identity autocad executable is invalid")
    if session.get("device_id") != device_id:
        raise ValueError("runtime identity session device differs from capture")
    if not isinstance(session.get("session_id"), str) or not session["session_id"]:
        raise ValueError("runtime identity session_id is missing")
    if session.get("protocol_version") != "cad.agent/2":
        raise ValueError("runtime identity protocol version is unexpected")
    managed_host = session.get("managed_host")
    if (
        not isinstance(managed_host, dict)
        or managed_host.get("runtime_id") != "managed_dotnet"
    ):
        raise ValueError("runtime identity managed host is not managed_dotnet")
    _parse_timestamp(session["connected_at"], "runtime identity session connected_at")


def _service_restart_gates(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    implementation_commit: str,
) -> dict[str, bool]:
    old = before.get("gateway_service_record", {})
    new = after.get("gateway_service_record", {})
    old_properties = old.get("properties", {})
    new_properties = new.get("properties", {})
    old_process = old.get("process", {})
    new_process = new.get("process", {})
    old_release = old.get("release", {})
    new_release = new.get("release", {})
    exit_proof = after.get("old_gateway_process_exit", {})
    old_pid = old_properties.get("MainPID")
    new_pid = new_properties.get("MainPID")
    old_start = old_process.get("start_identity")
    new_start = new_process.get("start_identity")
    executable = new_process.get("executable")
    executable_hash = new_process.get("executable_sha256")
    return {
        "old_gateway_process_exited": (
            exit_proof.get("source") == "procfs"
            and exit_proof.get("pid") == old_pid
            and exit_proof.get("start_identity") == old_start
            and "proc_stat_after" in exit_proof
            and exit_proof.get("proc_stat_after") is None
            and exit_proof.get("probe_exit_code") == 0
        ),
        "actual_gateway_process_restart": (
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
        ),
        "authoritative_gateway_service": (
            old.get("source") == new.get("source") == "systemctl_show"
            and old_properties.get("Id")
            == new_properties.get("Id")
            == "autocad-mcp-phase4.service"
            and new_properties.get("ActiveState") == "active"
            and new_properties.get("SubState") == "running"
            and isinstance(old_properties.get("ExecMainStartTimestampMonotonic"), str)
            and bool(old_properties["ExecMainStartTimestampMonotonic"])
            and isinstance(new_properties.get("ExecMainStartTimestampMonotonic"), str)
            and bool(new_properties["ExecMainStartTimestampMonotonic"])
            and old_properties.get("ExecMainStartTimestampMonotonic")
            != new_properties.get("ExecMainStartTimestampMonotonic")
            and before.get("gateway_pid") == old_pid
            and after.get("gateway_pid") == new_pid
        ),
        "gateway_runtime_identity": (
            isinstance(executable, str)
            and executable.startswith("/")
            and old_process.get("executable") == executable
            and old_properties.get("ExecStart") == new_properties.get("ExecStart")
            and executable in str(new_properties.get("ExecStart", ""))
            and isinstance(executable_hash, str)
            and executable_hash.startswith("sha256:")
            and len(executable_hash) == 71
            and old_process.get("executable_sha256") == executable_hash
            and old_release.get("source")
            == new_release.get("source")
            == "git_rev_parse"
            and old_release.get("working_directory")
            == new_release.get("working_directory")
            == new_properties.get("WorkingDirectory")
            and old_release.get("commit")
            == new_release.get("commit")
            == implementation_commit
            and implementation_commit[:7]
            in str(new_properties.get("WorkingDirectory", ""))
        ),
    }


def _db_restart_gates(
    evidence: dict[str, Any],
    process_before: dict[str, Any],
    process_after: dict[str, Any],
    *,
    device_id: str,
    scene_id: str,
    implementation_commit: str,
) -> dict[str, bool]:
    scope = evidence.get("scope", {})
    comparison = evidence.get("restart_comparison", {})
    pre_snapshot = comparison.get("pre_restart_write_snapshot", {})
    post_snapshot = evidence.get("write_snapshot", {})
    pre_tables = pre_snapshot.get("tables")
    post_tables = post_snapshot.get("tables")
    pre_session = comparison.get("pre_restart_active_agent_session_id")
    post_session = comparison.get("post_restart_active_agent_session_id")
    sessions = {
        item.get("session_id"): item
        for item in evidence.get("agent_sessions", [])
        if isinstance(item, dict)
    }
    pre_record = sessions.get(pre_session, {})
    post_record = sessions.get(post_session, {})
    return {
        "db_evidence_provenance": (
            evidence.get("schema_version") == "cad.phase10-live-db-evidence/1"
            and evidence.get("implementation_commit") == implementation_commit
        ),
        "gateway_public_reconnect": (
            isinstance(pre_session, str)
            and isinstance(post_session, str)
            and pre_session != post_session
            and process_before.get("agent_session_id") == pre_session
            and process_after.get("agent_session_id") == post_session
            and evidence.get("active_agent_session_id") == post_session
            and pre_record.get("device_id") == post_record.get("device_id") == device_id
            and pre_record.get("disconnected_at") is not None
            and post_record.get("disconnected_at") is None
        ),
        "db_scope_bound": (
            scope.get("device_id") == device_id
            and isinstance(scope.get("owner_subject"), str)
            and bool(scope.get("owner_subject"))
            and scene_id in scope.get("scene_ids", [])
        ),
        "no_write_events_in_window": evidence.get("retrospective_no_write_events")
        == [],
        "write_snapshot_unchanged": (
            isinstance(pre_tables, dict)
            and isinstance(post_tables, dict)
            and pre_tables == post_tables
            and pre_snapshot.get("sha256") == _snapshot_digest(pre_tables)
            and post_snapshot.get("sha256") == _snapshot_digest(post_tables)
            and comparison.get("post_restart_write_snapshot_sha256")
            == post_snapshot.get("sha256")
        ),
        "anchor_jobs_read_only": bool(evidence.get("anchor_jobs"))
        and all(
            item.get("device_id") == device_id
            and item.get("effect_class") == "read"
            and item.get("state") == "succeeded"
            for item in evidence.get("anchor_jobs", [])
        ),
    }


def _public_scene_gates(
    scene: dict[str, Any],
    sections: dict[str, Any],
    resource: dict[str, Any],
    devices: dict[str, Any],
    *,
    device_id: str,
) -> dict[str, bool]:
    device = next(
        (
            item
            for item in devices.get("devices", [])
            if item.get("device_id") == device_id
        ),
        None,
    )
    same_sections = all(
        sections.get(name, {}).get("items") == scene["sections"][name]["items"]
        and sections.get(name, {}).get("total") == scene["sections"][name]["total"]
        and sections.get(name, {}).get("scene_id") == scene["scene_id"]
        and sections.get(name, {}).get("scene_digest") == scene["scene_digest"]
        for name in SECTIONS
    )
    return {
        "public_device_online": (
            isinstance(device, dict)
            and device.get("status") == "online"
            and device.get("paused") is False
            and device.get("runtime_state") == "online_idle"
        ),
        "same_scene_retrieved": (
            same_sections
            and resource.get("scene_id") == scene["scene_id"]
            and resource.get("scene_digest") == scene["scene_digest"]
            and resource.get("source_digest") == scene["source_digest"]
            and resource.get("document_revision") == scene["document_revision"]
            and resource.get("counts") == scene["counts"]
        ),
        "public_query_succeeded": same_sections
        and resource.get("schema_version") == "cad.scene/1",
    }


def _db_evidence_binding(path: Path, evidence: dict[str, Any]) -> dict[str, Any]:
    session_ids = {
        evidence.get("restart_comparison", {}).get(
            "pre_restart_active_agent_session_id"
        ),
        evidence.get("restart_comparison", {}).get(
            "post_restart_active_agent_session_id"
        ),
    }
    return {
        "artifact": str(path),
        "artifact_sha256": _sha256(path),
        "schema_version": evidence.get("schema_version"),
        "scope": evidence.get("scope"),
        "retrospective_no_write_events": evidence.get(
            "retrospective_no_write_events"
        ),
        "anchor_jobs": [
            {
                key: item.get(key)
                for key in ("job_id", "device_id", "effect_class", "state")
            }
            for item in evidence.get("anchor_jobs", [])
        ],
        "agent_sessions": [
            {
                key: item.get(key)
                for key in ("session_id", "device_id", "connected_at", "disconnected_at")
            }
            for item in evidence.get("agent_sessions", [])
            if item.get("session_id") in session_ids
        ],
        "active_agent_session_id": evidence.get("active_agent_session_id"),
        "pre_restart_write_snapshot_sha256": evidence.get(
            "restart_comparison", {}
        )
        .get("pre_restart_write_snapshot", {})
        .get("sha256"),
        "post_restart_write_snapshot_sha256": evidence.get(
            "write_snapshot", {}
        ).get("sha256"),
    }


def _process_identity_bound(
    before: dict[str, Any],
    after: dict[str, Any],
    expected: dict[str, Any],
) -> bool:
    return all(
        before.get(name) == after.get(name) == value
        for name, value in expected.items()
    )


async def _observe(client: Client, device_id: str, key: str) -> dict[str, Any]:
    started = _payload(
        await client.call_tool(
            "cad_observe",
            {
                "device_id": device_id,
                "observation_level": "detail",
                "include_preview_image": False,
                "idempotency_key": key,
            },
        )
    )
    job_id = started["job_id"]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 120
    while loop.time() < deadline:
        job = _payload(await client.call_tool("cad_get_job", {"job_id": job_id}))
        if job["state"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
            if job["state"] != "succeeded":
                raise RuntimeError(f"observation ended as {job['state']}")
            return {"request": started, "job": job}
        await asyncio.sleep(0.25)
    raise TimeoutError("observation did not finish")


async def _query_sections(client: Client, scene_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for section in SECTIONS:
        page = _payload(
            await client.call_tool(
                "cad_query_scene",
                {"scene_id": scene_id, "section": section, "limit": 200},
            )
        )
        if page.get("next_cursor") is not None:
            raise RuntimeError(f"{section} evidence unexpectedly paginated")
        result[section] = page
    return result


def _node_map(sections: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["node_id"]: item for item in sections["nodes"]["items"]}


def _fixture_gates(fixture: str, sections: dict[str, Any]) -> dict[str, bool]:
    nodes = _node_map(sections)
    features = sections["features"]["items"]
    issues = sections["issues"]["items"]
    if fixture == "a":
        holes = [item for item in features if item["feature_type"] == "hole"]
        patterns = [
            item for item in features if item["feature_type"] == "repeated_hole_pattern"
        ]
        pattern_nodes = {
            node_id for item in patterns for node_id in item["source_node_ids"]
        }
        non_pattern = [
            item
            for item in nodes.values()
            if item["entity_type"] == "CIRCLE"
            and item.get("geometry", {}).get("radius") == 3.0
        ]
        return {
            "hole": len(holes) == 5,
            "repeated_hole_pattern": len(patterns) == 1
            and len(patterns[0]["source_node_ids"]) == 4,
            "non_pattern_circle_excluded": len(non_pattern) == 1
            and non_pattern[0]["node_id"] not in pattern_nodes,
        }
    if fixture == "b":
        slots = [item for item in features if item["feature_type"] == "slot"]
        groups = [
            item for item in features if item["feature_type"] == "concentric_group"
        ]
        group_nodes = {
            node_id for item in groups for node_id in item["source_node_ids"]
        }
        near_concentric = [
            item
            for item in nodes.values()
            if item["entity_type"] == "CIRCLE"
            and item.get("geometry", {}).get("radius") == 9.0
        ]
        near_slot = [
            item
            for item in nodes.values()
            if item["entity_type"] == "LWPOLYLINE"
        ]
        slot_nodes = {node_id for item in slots for node_id in item["source_node_ids"]}
        return {
            "slot": len(slots) == 1,
            "near_slot_excluded": len(near_slot) == 1
            and near_slot[0]["node_id"] not in slot_nodes,
            "concentric_group": len(groups) == 1
            and len(groups[0]["source_node_ids"]) == 2,
            "near_concentric_outside_tolerance": len(near_concentric) == 1
            and near_concentric[0]["node_id"] not in group_nodes,
        }
    codes = {item["code"] for item in issues}
    valid_nodes = {
        item["node_id"]
        for item in nodes.values()
        if (
            item["entity_type"] == "CIRCLE"
            and item.get("geometry", {}).get("center") == {"x": 95.0, "y": 5.0}
            and item.get("geometry", {}).get("radius") == 2.0
        )
        or (
            item["entity_type"] == "LWPOLYLINE"
            and item.get("geometry", {}).get("vertices")
            == [
                {"x": 85.0, "y": 0.0},
                {"x": 105.0, "y": 0.0},
                {"x": 105.0, "y": 10.0},
                {"x": 85.0, "y": 10.0},
            ]
        )
    }
    cleanup_nodes = {
        node_id
        for item in issues
        if item["code"]
        in {
            "degenerate_geometry",
            "duplicate_geometry",
            "open_contour",
            "self_intersection",
        }
        for node_id in item["source_node_ids"]
    }
    return {
        "degenerate_geometry": "degenerate_geometry" in codes,
        "duplicate_geometry": "duplicate_geometry" in codes,
        "open_contour": "open_contour" in codes,
        "invalid_or_self_intersecting_contour": "self_intersection" in codes,
        "valid_geometry_not_flagged_for_cleanup": bool(valid_nodes)
        and valid_nodes.isdisjoint(cleanup_nodes),
    }


async def _capture(args: argparse.Namespace, token: str) -> dict[str, Any]:
    from fastmcp import Client

    drawing = args.drawing.resolve()
    process_identity = json.loads(args.process_identity.read_text(encoding="utf-8"))
    no_effect_db = json.loads(args.no_effect_db.read_text(encoding="utf-8"))
    implementation_commit = _git_head()
    _validate_runtime_identity(
        process_identity,
        device_id=args.device_id,
        implementation_commit=implementation_commit,
    )
    db_sessions = {
        item.get("session_id")
        for item in no_effect_db.get("agent_sessions", [])
        if isinstance(item, dict)
    }
    if process_identity["agent_session"]["session_id"] not in db_sessions:
        raise ValueError(
            "process identity session is absent from the no-effect DB evidence"
        )
    expected_name = f"phase10-drawing-{args.fixture}.dwg"
    if drawing.name.lower() != expected_name:
        raise ValueError(f"expected {expected_name}")
    hash_before = _sha256(drawing)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    invoked_tools = [
        "cad_list_devices",
        "cad_observe",
        "cad_get_job",
        "cad_build_scene",
        "cad_query_scene",
    ]
    async with Client(args.endpoint, auth=token, timeout=120) as client:
        tools = sorted(tool.name for tool in await client.list_tools())
        for required in ("cad_build_scene", "cad_query_scene"):
            if required not in tools:
                raise RuntimeError(f"{required} is not enabled")
        devices = _payload(
            await client.call_tool("cad_list_devices", {"online_only": True})
        )
        device = next(
            (
                item
                for item in devices["devices"]
                if item["device_id"] == args.device_id
            ),
            None,
        )
        if device is None:
            raise RuntimeError("standalone Agent is not online")
        before = await _observe(
            client, args.device_id, f"phase10-{args.fixture}-before-{stamp}"
        )
        request = before["request"]
        job_snapshot = before["job"].get("result", {}).get("snapshot", {})
        document_name = (
            job_snapshot.get("drawing", {}).get("document_name")
            or job_snapshot.get("document_name")
            or device.get("document_name")
        )
        if str(document_name).lower() != expected_name:
            raise RuntimeError(
                f"active public document is {document_name!r}, expected {expected_name!r}"
            )
        build_input = {
            "source_snapshot_id": request["snapshot_id"],
            "idempotency_key": f"phase10-{args.fixture}-scene-{stamp}",
            "analysis_profile": "mechanical-2d/1",
            "space": "model",
            "include_sections": list(SECTIONS),
        }
        built = _payload(
            await client.call_tool(
                "cad_build_scene",
                build_input,
            )
        )
        scene = built["scene"]
        repeated = _payload(
            await client.call_tool(
                "cad_build_scene",
                {
                    **build_input,
                    "idempotency_key": f"phase10-{args.fixture}-scene-repeat-{stamp}",
                },
            )
        )
        sections = await _query_sections(client, scene["scene_id"])
        resource = _resource_payload(
            await client.read_resource(
                f"cad://scenes/{scene['scene_id']}/summary"
            )
        )
        after = await _observe(
            client, args.device_id, f"phase10-{args.fixture}-after-{stamp}"
        )
    hash_after = _sha256(drawing)
    revision_before = str(request["document_revision"])
    revision_after = str(after["request"]["document_revision"])
    entity_count_before = int(
        before["job"].get("result", {}).get("snapshot", {}).get("returned_count")
        or len(before["job"].get("result", {}).get("snapshot", {}).get("entities", []))
    )
    entity_count_after = int(
        after["job"].get("result", {}).get("snapshot", {}).get("returned_count")
        or len(after["job"].get("result", {}).get("snapshot", {}).get("entities", []))
    )
    gates = _fixture_gates(args.fixture, sections)
    nodes = sections["nodes"]["items"]
    runtime = before["job"].get("runtime_evidence", {}).get("runtime", {})
    write_tools_invoked = sorted(
        tool for tool in invoked_tools if tool in WRITE_TOOLS
    )
    db_events = no_effect_db.get("retrospective_no_write_events")
    anchor_jobs = no_effect_db.get("anchor_jobs", [])
    write_snapshot = no_effect_db.get("write_snapshot", {})
    pre_snapshot = (
        no_effect_db.get("restart_comparison", {}).get(
            "pre_restart_write_snapshot", {}
        )
        if isinstance(no_effect_db.get("restart_comparison"), dict)
        else {}
    )
    no_write_events_in_window = db_events == []
    anchor_jobs_read_only = (
        isinstance(anchor_jobs, list)
        and bool(anchor_jobs)
        and all(
            isinstance(item, dict)
            and item.get("effect_class") == "read"
            and item.get("state") == "succeeded"
            for item in anchor_jobs
        )
    )
    write_snapshot_unchanged = (
        isinstance(write_snapshot, dict)
        and isinstance(write_snapshot.get("tables"), dict)
        and isinstance(pre_snapshot, dict)
        and write_snapshot.get("tables") == pre_snapshot.get("tables")
        and write_snapshot.get("sha256")
        == _snapshot_digest(write_snapshot.get("tables"))
        and pre_snapshot.get("sha256")
        == _snapshot_digest(pre_snapshot.get("tables"))
    )
    no_write_requested = (
        write_tools_invoked == []
        and no_write_events_in_window
        and anchor_jobs_read_only
    )
    no_cad_effect_attempted = (
        no_write_requested
        and hash_before == hash_after
        and revision_before == revision_after
        and write_snapshot_unchanged
    )
    gates.update(
        {
            "dwg_file_hash_unchanged": hash_before == hash_after,
            "document_revision_unchanged": revision_before == revision_after,
            "entity_count_unchanged": entity_count_before == entity_count_after,
            "stable_scene_reuse": repeated["scene"]["scene_id"] == scene["scene_id"]
            and repeated["scene"]["source_digest"] == scene["source_digest"]
            and repeated["scene"]["scene_digest"] == scene["scene_digest"]
            and repeated["reused"] is True,
            "managed_dotnet_runtime": runtime.get("id") == "managed_dotnet"
            and runtime.get("role") == "primary"
            and before["job"]["runtime_evidence"].get("degraded") is False,
            "source_runtime_managed": bool(nodes)
            and all(item.get("source_runtime") == "managed_dotnet" for item in nodes),
            "source_capabilities_present": bool(nodes)
            and all(item.get("source_capabilities") for item in nodes),
            "no_write_events_in_window": no_write_events_in_window,
            "anchor_jobs_read_only": anchor_jobs_read_only,
            "write_snapshot_unchanged": write_snapshot_unchanged,
            "no_write_requested": no_write_requested,
            "no_cad_effect_attempted": no_cad_effect_attempted,
            "runtime_identity_bound": True,
        }
    )
    if not all(gates.values()):
        failed = sorted(name for name, passed in gates.items() if not passed)
        raise RuntimeError(f"fixture gates failed: {failed}")
    return {
        "schema_version": "cad.phase10-live-public-fixture/1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "capture_command": args.capture_command,
        "baseline_commit": _git_baseline(),
        "implementation_commit": _git_head(),
        "operator": args.operator,
        "fixture": {
            "fixture_id": f"phase10-drawing-{args.fixture}-r25/1",
            "fixture_file": str(drawing),
            "document_name": expected_name,
            "document_id": job_snapshot.get("drawing", {}).get("document_id"),
            "dwg_file_hash_before": hash_before,
            "dwg_file_hash_after": hash_after,
        },
        "runtime_identity": process_identity,
        "public_path": {
            "endpoint": args.endpoint,
            "device_id": args.device_id,
            "standalone_desktop_agent": True,
            "invoked_tools": invoked_tools,
            "write_tools_invoked": write_tools_invoked,
            "devices": devices,
        },
        "source": {
            "snapshot_id": request["snapshot_id"],
            "document_revision_before": revision_before,
            "document_revision_after": revision_after,
            "entity_count_before": entity_count_before,
            "entity_count_after": entity_count_after,
            "observation_before": before,
            "observation_after": after,
        },
        "scene": {
            **scene,
            "repeat_build": repeated,
            "sections": sections,
            "summary_resource": resource,
            "feature_types": sorted(
                {item["feature_type"] for item in sections["features"]["items"]}
            ),
            "relation_types": sorted(
                {item["relation_type"] for item in sections["relations"]["items"]}
            ),
            "issue_codes": sorted(
                {item["code"] for item in sections["issues"]["items"]}
            ),
            "evidence_strengths": sorted(
                {
                    item["evidence_strength"]
                    for item in sections["evidence"]["items"]
                }
            ),
            "source_capabilities": sorted(
                {
                    capability
                    for item in nodes
                    for capability in item["source_capabilities"]
                }
            ),
        },
        "no_effect": {
            "dwg_file_hash_unchanged": gates["dwg_file_hash_unchanged"],
            "document_revision_unchanged": gates["document_revision_unchanged"],
            "entity_count_unchanged": gates["entity_count_unchanged"],
            "write_requested": not no_write_requested,
            "cad_effect_attempted": not no_cad_effect_attempted,
        },
        "no_effect_db_binding": _db_evidence_binding(args.no_effect_db, no_effect_db),
        "gate_results": gates,
        "failures_retests": (
            [
                {
                    "failure": "The original 1e-7 tiny-circle fixture was rejected by the signed payload boundary with payload_mismatch.",
                    "resolution": "Retained the fail-closed boundary and replaced the live degenerate case with an exact zero-length LINE; the final capture passed.",
                },
                {
                    "failure": "The first valid-geometry assertion depended on a raw layer name that is privacy-hashed in the public projection.",
                    "resolution": "Changed the gate to exact valid geometry identity; the final capture passed without weakening privacy.",
                },
            ]
            if args.fixture == "c"
            else []
        ),
        "limitations": (
            [
                "tiny_circle_omitted: R25 rejected the 1e-7 projection at the signed payload boundary; zero-length LINE supplies the mandatory live degenerate case"
            ]
            if args.fixture == "c"
            else []
        ),
        "status": "PASS",
    }


async def _restart_query(args: argparse.Namespace, token: str) -> dict[str, Any]:
    from fastmcp import Client

    before = json.loads(args.before.read_text(encoding="utf-8"))
    process_before = json.loads(args.process_before.read_text(encoding="utf-8"))
    process_after = json.loads(args.process_after.read_text(encoding="utf-8"))
    _reject_claimed_service_records(process_before, "process-before")
    _reject_claimed_service_records(process_after, "process-after")
    identity_before = json.loads(args.identity_before.read_text(encoding="utf-8"))
    identity_after = json.loads(args.identity_after.read_text(encoding="utf-8"))
    if (
        _parse_timestamp(
            identity_before.get("captured_at"), "identity-before captured_at"
        )
        >= _parse_timestamp(
            identity_after.get("captured_at"), "identity-after captured_at"
        )
    ):
        raise ValueError("identity-after was not captured after identity-before")
    derived_before = _derive_gateway_identity(identity_before)
    derived_after = _derive_gateway_identity(identity_after)
    if derived_after["exit_probe"] is None:
        raise ValueError("identity-after did not probe the old Gateway process")
    old_pid = derived_before["gateway_pid"]
    exit_command = tuple(derived_after["exit_probe"].get("command", []))
    if f"/proc/{old_pid}/stat" not in exit_command:
        raise ValueError(
            "old process exit probe does not target the pre-restart Gateway PID"
        )
    old_start = derived_before["gateway_service_record"]["process"]["start_identity"]
    process_before = {
        **process_before,
        "gateway_pid": old_pid,
        "gateway_service_record": derived_before["gateway_service_record"],
    }
    process_after = {
        **process_after,
        "gateway_pid": derived_after["gateway_pid"],
        "gateway_service_record": derived_after["gateway_service_record"],
        "old_gateway_process_exit": {
            "source": "procfs",
            "pid": old_pid,
            "start_identity": old_start,
            "proc_stat_after": None,
            "probe_exit_code": 0,
            "command": list(exit_command),
        },
    }
    no_effect_db = json.loads(args.no_effect_db.read_text(encoding="utf-8"))
    scene = before["scene"]
    scene_id = scene["scene_id"]
    async with Client(args.endpoint, auth=token, timeout=120) as client:
        devices = _payload(
            await client.call_tool("cad_list_devices", {"online_only": True})
        )
        if not any(
            item["device_id"] == args.device_id for item in devices["devices"]
        ):
            raise RuntimeError("Agent did not reconnect")
        sections = await _query_sections(client, scene_id)
        resource = _resource_payload(
            await client.read_resource(f"cad://scenes/{scene_id}/summary")
        )
    service_gates = _service_restart_gates(
        process_before,
        process_after,
        implementation_commit=before["implementation_commit"],
    )
    db_gates = _db_restart_gates(
        no_effect_db,
        process_before,
        process_after,
        device_id=args.device_id,
        scene_id=scene_id,
        implementation_commit=before["implementation_commit"],
    )
    public_gates = _public_scene_gates(
        scene, sections, resource, devices, device_id=args.device_id
    )
    dwg_unchanged = (
        before["fixture"]["dwg_file_hash_before"]
        == before["fixture"]["dwg_file_hash_after"]
    )
    revision_unchanged = (
        before["source"]["document_revision_before"]
        == before["source"]["document_revision_after"]
    )
    expected_process_identity = {
        "device_id": args.device_id,
        "fixture_id": before["fixture"]["fixture_id"],
        "scene_id": scene_id,
        "source_digest": scene["source_digest"],
        "scene_digest": scene["scene_digest"],
        "document_id": scene["document_id"],
        "document_revision": scene["document_revision"],
    }
    gates = {
        **service_gates,
        **db_gates,
        **public_gates,
        "standalone_desktop_agent": process_before["desktop_agent_pid"]
        == process_after["desktop_agent_pid"]
        and process_before["desktop_agent_executable"]
        == process_after["desktop_agent_executable"]
        and process_before["desktop_agent_sha256"]
        == process_after["desktop_agent_sha256"],
        "process_identity_bound": _process_identity_bound(
            process_before, process_after, expected_process_identity
        ),
        "dwg_file_hash_unchanged": dwg_unchanged,
        "document_revision_unchanged": revision_unchanged,
        "no_write_requested": (
            before["public_path"].get("write_tools_invoked") == []
            and db_gates["no_write_events_in_window"]
            and db_gates["anchor_jobs_read_only"]
        ),
        "no_cad_effect_attempted": (
            dwg_unchanged
            and revision_unchanged
            and db_gates["no_write_events_in_window"]
            and db_gates["write_snapshot_unchanged"]
        ),
    }
    if not all(gates.values()):
        failed = sorted(name for name, passed in gates.items() if not passed)
        raise RuntimeError(f"restart gates failed: {failed}")
    return {
        "schema_version": "cad.phase10-live-gateway-restart/1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "capture_command": args.capture_command,
        "baseline_commit": _git_baseline(),
        "implementation_commit": _git_head(),
        "operator": args.operator,
        "fixture_id": before["fixture"]["fixture_id"],
        "scene_id": scene_id,
        "source_digest": scene["source_digest"],
        "scene_digest": scene["scene_digest"],
        "gateway_process_before": process_before,
        "gateway_process_after": process_after,
        "identity_capture_before": identity_before,
        "identity_capture_after": identity_after,
        "post_restart_sections": sections,
        "post_restart_summary_resource": resource,
        "no_effect_db_binding": _db_evidence_binding(
            args.no_effect_db, no_effect_db
        ),
        "write_requested": False,
        "cad_effect_attempted": False,
        "gate_results": gates,
        **gates,
        "failures_retests": [],
        "limitations": [],
        "status": "PASS",
    }


async def _capture_identity(args: argparse.Namespace, token: str) -> dict[str, Any]:
    import shutil

    if shutil.which("systemctl") is None:
        raise RuntimeError(
            "systemctl is unavailable; capture-identity must run on the Gateway VM"
        )
    service = args.service or SERVICE_UNIT
    systemctl = _run_raw_command(
        [
            "systemctl",
            "show",
            "--no-pager",
            "--property=Id,ActiveState,SubState,MainPID,"
            "ExecMainStartTimestampMonotonic,WorkingDirectory,ExecStart",
            service,
        ]
    )
    if systemctl["exit_code"] != 0:
        raise RuntimeError("systemctl show failed on the Gateway VM")
    properties = _parse_systemctl_properties(systemctl["stdout"])
    try:
        pid = int(properties["MainPID"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("systemctl MainPID is missing or invalid") from None
    if pid <= 0:
        raise RuntimeError("Gateway service is not running (MainPID=0)")
    working_directory = properties.get("WorkingDirectory", "")
    if not working_directory:
        raise RuntimeError("systemctl WorkingDirectory is missing")
    executable = _run_raw_command(["readlink", "-f", f"/proc/{pid}/exe"])
    if executable["exit_code"] != 0:
        raise RuntimeError("readlink of /proc/<pid>/exe failed on the Gateway VM")
    records = [systemctl, executable]
    records.append(_run_raw_command(["sha256sum", executable["stdout"].strip()]))
    records.append(
        _run_raw_command(
            ["git", "-C", working_directory, "rev-parse", "HEAD"]
        )
    )
    records.append(_run_raw_command(["awk", "{print $22}", f"/proc/{pid}/stat"]))
    if args.old_pid is not None:
        probe = _run_raw_command(["test", "!", "-e", f"/proc/{args.old_pid}/stat"])
        if probe["exit_code"] != 0:
            raise RuntimeError("old Gateway process is still alive on the VM")
        records.append(probe)
    for record in records[1:]:
        if record["exit_code"] != 0:
            raise RuntimeError(
                f"authoritative capture command failed: {record['command'][0]}"
            )
    return {
        "schema_version": "cad.phase10-live-identity/1",
        "service": service,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "capture_command": args.capture_command,
        "operator": args.operator,
        "commands": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("capture", "capture-identity", "restart-query")
    )
    parser.add_argument("--endpoint", default="https://cad.kythuatvang.com/mcp")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--operator", default="local-operator")
    parser.add_argument("--service", default=SERVICE_UNIT)
    parser.add_argument("--old-pid", type=int)
    parser.add_argument("--fixture", choices=("a", "b", "c"))
    parser.add_argument("--drawing", type=Path)
    parser.add_argument("--process-identity", type=Path)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--process-before", type=Path)
    parser.add_argument("--process-after", type=Path)
    parser.add_argument("--identity-before", type=Path)
    parser.add_argument("--identity-after", type=Path)
    parser.add_argument("--no-effect-db", type=Path)
    args = parser.parse_args()
    if args.action == "capture" and any(
        value is None
        for value in (
            args.fixture,
            args.drawing,
            args.process_identity,
            args.no_effect_db,
        )
    ):
        parser.error(
            "capture requires --fixture, --drawing, --process-identity "
            "and --no-effect-db"
        )
    if args.action == "restart-query" and any(
        value is None
        for value in (
            args.before,
            args.process_before,
            args.process_after,
            args.identity_before,
            args.identity_after,
            args.no_effect_db,
        )
    ):
        parser.error(
            "restart-query requires --before, --process-before, --process-after, "
            "--identity-before, --identity-after and --no-effect-db"
        )
    command = [
        "python scripts/phase10-live-public-evidence.py",
        args.action,
        f"--endpoint {args.endpoint}",
        f"--device-id {args.device_id}",
        "--token-file <redacted>",
        f"--output {args.output}",
        f"--operator {args.operator}",
    ]
    if args.action == "capture":
        command.extend(
            (
                f"--fixture {args.fixture}",
                f"--drawing {args.drawing}",
                f"--process-identity {args.process_identity}",
                f"--no-effect-db {args.no_effect_db}",
            )
        )
    elif args.action == "capture-identity":
        command.append(f"--service {args.service}")
        if args.old_pid is not None:
            command.append(f"--old-pid {args.old_pid}")
    else:
        command.extend(
            (
                f"--before {args.before}",
                f"--process-before {args.process_before}",
                f"--process-after {args.process_after}",
                f"--identity-before {args.identity_before}",
                f"--identity-after {args.identity_after}",
                f"--no-effect-db {args.no_effect_db}",
            )
        )
    args.capture_command = " ".join(command)
    token = json.loads(args.token_file.read_text(encoding="utf-8"))["access_token"]
    runner = {
        "capture": _capture,
        "capture-identity": _capture_identity,
        "restart-query": _restart_query,
    }[args.action]
    result = asyncio.run(runner(args, token))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
