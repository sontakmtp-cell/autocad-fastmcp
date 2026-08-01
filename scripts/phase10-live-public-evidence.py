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
    "executable_sha256",
    "file_version",
    "host_family",
    "process_id",
    "product",
    "release_year",
    "series",
    "started_at",
}
_MANAGED_HOST_KEYS = {
    "executable",
    "executable_sha256",
    "framework",
    "package_hash",
    "package_id",
    "package_version",
    "process_id",
    "runtime_id",
    "started_at",
}
_AGENT_SESSION_KEYS = {
    "agent_version",
    "connected_at",
    "device_id",
    "disconnected_at",
    "managed_host",
    "protocol_version",
    "session_id",
}
_CAPTURE_WINDOW_SECONDS = 300
_FIXTURE_ID_RE = re.compile(r"^phase10-drawing-([abc])-r25/1$")
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
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return "sha256:" + digest.hexdigest()


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


def _fixture_letter_from_id(fixture_id: str) -> str:
    if not isinstance(fixture_id, str):
        raise ValueError("fixture_id is invalid")
    match = _FIXTURE_ID_RE.fullmatch(fixture_id)
    if match is None:
        raise ValueError("fixture_id is invalid")
    return match.group(1)


def _phase10_key(fixture_letter: str, phase: str, stamp: str) -> str:
    if fixture_letter not in {"a", "b", "c"}:
        raise ValueError("fixture letter is invalid")
    if phase not in {"before", "after", "scene", "scene-repeat"}:
        raise ValueError("fixture phase is invalid")
    if re.fullmatch(r"\d{14}", stamp) is None:
        raise ValueError("capture stamp is invalid")
    return f"phase10-drawing-{fixture_letter}-{phase}-{stamp}"


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
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("command"), list)
            or not all(isinstance(item, str) for item in record["command"])
        ):
            raise ValueError("identity capture command record is invalid")
        key = tuple(record["command"])
        if key in records:
            raise ValueError(
                f"identity capture contains duplicate command {' '.join(key)!r}"
            )
        records[key] = record
    capture_window = _parse_timestamp(
        identity.get("captured_at"), "identity capture captured_at"
    )
    for command, record in records.items():
        stamp = _parse_timestamp(
            record.get("captured_at"), "identity command captured_at"
        )
        if abs((stamp - capture_window).total_seconds()) > _CAPTURE_WINDOW_SECONDS:
            raise ValueError("identity command timestamp is outside the capture window")

    service = identity.get("service") or SERVICE_UNIT
    systemctl = records.get(
        (
            "systemctl",
            "show",
            "--no-pager",
            "--property=Id,ActiveState,SubState,MainPID,"
            "ExecMainStartTimestampMonotonic,WorkingDirectory,ExecStart",
            service,
        )
    )
    if systemctl is None:
        raise ValueError(
            "identity capture is missing the exact systemctl show command"
        )
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

    exe = records.get(("readlink", "-f", f"/proc/{pid}/exe"))
    if exe is None:
        raise ValueError(
            "identity capture is missing readlink for the service PID"
        )
    if exe.get("exit_code") != 0:
        raise ValueError("readlink of the service executable failed")
    executable = exe.get("stdout", "").strip()
    if not executable.startswith("/"):
        raise ValueError("proc executable identity is unavailable")

    digest = records.get(("sha256sum", executable))
    if digest is None:
        raise ValueError(
            "identity capture is missing sha256sum for the exact executable"
        )
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

    working_directory = properties.get("WorkingDirectory", "")
    release = records.get(
        ("git", "-C", working_directory, "rev-parse", "HEAD")
    )
    if release is None:
        raise ValueError(
            "identity capture is missing git rev-parse in the service "
            "WorkingDirectory"
        )
    if release.get("exit_code") != 0:
        raise ValueError("git rev-parse failed in the service working directory")
    commit = release.get("stdout", "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("deployed commit identity is invalid")

    stat = records.get(("awk", "{print $22}", f"/proc/{pid}/stat"))
    if stat is None:
        raise ValueError(
            "identity capture is missing the exact proc start-time command"
        )
    if stat.get("exit_code") != 0:
        raise ValueError("proc start-time command failed")
    start_identity = stat.get("stdout", "").strip()
    if not start_identity.isdigit():
        raise ValueError("proc start identity is unavailable")

    exit_probe: dict[str, Any] | None = None
    for record in commands:
        command = tuple(record.get("command", []))
        if command and command[0] == "test":
            if (
                len(command) != 4
                or command[1] != "!"
                or command[2] != "-e"
                or not re.fullmatch(r"/proc/[1-9][0-9]*/stat", command[3])
            ):
                raise ValueError(
                    "old process absence probe must be exactly: "
                    "test ! -e /proc/<pid>/stat"
                )
            if record.get("exit_code") != 0:
                raise ValueError("old Gateway process absence probe failed")
            exit_probe = {
                "source": "procfs",
                "pid": int(command[3].split("/")[2]),
                "command": list(command),
            }
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
                "working_directory": working_directory,
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
    if not _SHA256_RE.fullmatch(autocad["executable_sha256"]):
        raise ValueError("runtime identity autocad executable hash is invalid")
    _parse_timestamp(autocad["started_at"], "runtime identity autocad started_at")
    managed_host = session.get("managed_host")
    if not isinstance(managed_host, dict) or set(managed_host) != _MANAGED_HOST_KEYS:
        raise ValueError("runtime identity managed_host is incomplete")
    if managed_host.get("runtime_id") != "managed_dotnet":
        raise ValueError("runtime identity managed host is not managed_dotnet")
    if not isinstance(managed_host["process_id"], int) or managed_host["process_id"] <= 0:
        raise ValueError("runtime identity managed host process_id is invalid")
    if not isinstance(managed_host["executable"], str) or not managed_host["executable"]:
        raise ValueError("runtime identity managed host executable is invalid")
    if not _SHA256_RE.fullmatch(managed_host["executable_sha256"]):
        raise ValueError("runtime identity managed host executable hash is invalid")
    _parse_timestamp(managed_host["started_at"], "runtime identity host started_at")
    if session.get("device_id") != device_id:
        raise ValueError("runtime identity session device differs from capture")
    if not isinstance(session.get("session_id"), str) or not session["session_id"]:
        raise ValueError("runtime identity session_id is missing")
    if session.get("protocol_version") != "cad.agent/2":
        raise ValueError("runtime identity protocol version is unexpected")
    if session.get("disconnected_at") is not None:
        raise ValueError("runtime identity session is not active")
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


async def _observe(
    client: Client,
    device_id: str,
    key: str,
    *,
    record,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
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
    record(
        "cad_observe",
        {
            "device_id": device_id,
            "observation_level": "detail",
            "include_preview_image": False,
            "idempotency_key": key,
        },
        started_at,
        job_id=started.get("job_id"),
    )
    job_id = started["job_id"]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 120
    while loop.time() < deadline:
        poll_started_at = datetime.now(timezone.utc).isoformat()
        job = _payload(
            await client.call_tool("cad_get_job", {"job_id": job_id})
        )
        record(
            "cad_get_job",
            {"job_id": job_id},
            poll_started_at,
            job_id=job_id,
        )
        if job["state"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
            if job["state"] != "succeeded":
                raise RuntimeError(f"observation ended as {job['state']}")
            return {"request": started, "job": job}
        await asyncio.sleep(0.25)
    raise TimeoutError("observation did not finish")


async def _query_sections(
    client: Client, scene_id: str, *, record
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for section in SECTIONS:
        started_at = datetime.now(timezone.utc).isoformat()
        page = _payload(
            await client.call_tool(
                "cad_query_scene",
                {"scene_id": scene_id, "section": section, "limit": 200},
            )
        )
        record(
            "cad_query_scene",
            {"scene_id": scene_id, "section": section, "limit": 200},
            started_at,
        )
        if page.get("next_cursor") is not None:
            raise RuntimeError(f"{section} evidence unexpectedly paginated")
        result[section] = page
    return result


def _validate_restart_invocations(
    invocations: list[dict[str, Any]], *, scene_id: str
) -> list[str]:
    expected = [
        ("cad_list_devices", {"online_only": True}),
        *[
            (
                "cad_query_scene",
                {"scene_id": scene_id, "section": section, "limit": 200},
            )
            for section in SECTIONS
        ],
        ("read_resource", {"uri": f"cad://scenes/{scene_id}/summary"}),
    ]
    if not isinstance(invocations, list) or len(invocations) != len(expected):
        raise ValueError("post-restart invocation trace is incomplete")
    for index, (item, (tool, arguments)) in enumerate(zip(invocations, expected)):
        if (
            not isinstance(item, dict)
            or set(item)
            != {
                "tool",
                "arguments",
                "started_at",
                "completed_at",
                "outcome",
                "job_id",
            }
            or item.get("tool") != tool
            or item.get("arguments") != arguments
            or item.get("outcome") != "succeeded"
            or item.get("job_id") is not None
        ):
            raise ValueError("post-restart invocation trace is invalid")
        started = _parse_timestamp(
            item.get("started_at"), "post-restart invocation started_at"
        )
        completed = _parse_timestamp(
            item.get("completed_at"), "post-restart invocation completed_at"
        )
        if completed < started:
            raise ValueError("post-restart invocation completed before it started")
        if index and _parse_timestamp(
            invocations[index - 1].get("completed_at"),
            "post-restart invocation completed_at",
        ) > started:
            raise ValueError("post-restart invocations overlap")
    return sorted(
        {item["tool"] for item in invocations if item["tool"] in WRITE_TOOLS}
    )


def _validate_restart_window(
    invocations: list[dict[str, Any]],
    no_effect_db: dict[str, Any],
    *,
    gateway_after_at: str,
    identity_after_at: str,
    restart_captured_at: str,
) -> None:
    if not invocations:
        raise ValueError("post-restart invocation trace is empty")
    restart_floor = max(
        _parse_timestamp(gateway_after_at, "Gateway restart after captured_at"),
        _parse_timestamp(identity_after_at, "identity-after captured_at"),
    )
    first_started = _parse_timestamp(
        invocations[0]["started_at"], "first post-restart invocation"
    )
    last_completed = _parse_timestamp(
        invocations[-1]["completed_at"], "last post-restart invocation"
    )
    restart_captured = _parse_timestamp(
        restart_captured_at, "restart captured_at"
    )
    if not (restart_floor <= first_started and last_completed <= restart_captured):
        raise ValueError(
            "Gateway restart: public invocations are outside the post-restart window"
        )
    scope = no_effect_db.get("scope", {})
    window_start = _parse_timestamp(scope.get("window_start"), "DB window_start")
    window_end = _parse_timestamp(scope.get("window_end"), "DB window_end")
    db_captured = _parse_timestamp(no_effect_db.get("captured_at"), "DB captured_at")
    if not (
        window_start <= first_started
        and last_completed <= window_end
        and window_end <= db_captured
    ):
        raise ValueError(
            "Gateway restart: DB window does not cover the fixture capture or post-restart invocations"
        )


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


async def _capture_public(
    args: argparse.Namespace,
    token: str,
    *,
    client_factory=None,
) -> dict[str, Any]:
    if client_factory is None:
        from fastmcp import Client

        client_factory = Client
    drawing = args.drawing.resolve()
    process_identity = json.loads(args.process_identity.read_text(encoding="utf-8"))
    capture_commit = _git_head()
    _validate_runtime_identity(
        process_identity,
        device_id=args.device_id,
        implementation_commit=capture_commit,
    )
    expected_name = f"phase10-drawing-{args.fixture}.dwg"
    if drawing.name.lower() != expected_name:
        raise ValueError(f"expected {expected_name}")
    hash_before = _sha256(drawing)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    before_key = _phase10_key(args.fixture, "before", stamp)
    after_key = _phase10_key(args.fixture, "after", stamp)
    scene_key = _phase10_key(args.fixture, "scene", stamp)
    scene_repeat_key = _phase10_key(args.fixture, "scene-repeat", stamp)
    invocations: list[dict[str, Any]] = []

    def record(
        tool: str,
        arguments: dict[str, Any],
        started_at: str,
        *,
        job_id: str | None = None,
    ) -> None:
        invocations.append(
            {
                "tool": tool,
                "arguments": arguments,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "outcome": "succeeded",
                "job_id": job_id,
            }
        )

    async with client_factory(args.endpoint, auth=token, timeout=120) as client:
        tools = sorted(tool.name for tool in await client.list_tools())
        for required in ("cad_build_scene", "cad_query_scene"):
            if required not in tools:
                raise RuntimeError(f"{required} is not enabled")
        started_at = datetime.now(timezone.utc).isoformat()
        devices = _payload(
            await client.call_tool("cad_list_devices", {"online_only": True})
        )
        record("cad_list_devices", {"online_only": True}, started_at)
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
            client,
            args.device_id,
            before_key,
            record=record,
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
            "idempotency_key": scene_key,
            "analysis_profile": "mechanical-2d/1",
            "space": "model",
            "include_sections": list(SECTIONS),
        }
        started_at = datetime.now(timezone.utc).isoformat()
        built = _payload(
            await client.call_tool(
                "cad_build_scene",
                build_input,
            )
        )
        record("cad_build_scene", build_input, started_at)
        scene = built["scene"]
        repeat_input = {
            **build_input,
            "idempotency_key": scene_repeat_key,
        }
        started_at = datetime.now(timezone.utc).isoformat()
        repeated = _payload(
            await client.call_tool(
                "cad_build_scene",
                repeat_input,
            )
        )
        record("cad_build_scene", repeat_input, started_at)
        sections = await _query_sections(
            client, scene["scene_id"], record=record
        )
        started_at = datetime.now(timezone.utc).isoformat()
        resource = _resource_payload(
            await client.read_resource(
                f"cad://scenes/{scene['scene_id']}/summary"
            )
        )
        record(
            "read_resource",
            {"uri": f"cad://scenes/{scene['scene_id']}/summary"},
            started_at,
        )
        after = await _observe(
            client,
            args.device_id,
            after_key,
            record=record,
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
    observation_job_ids = [
        before["request"]["job_id"],
        after["request"]["job_id"],
    ]
    invoked_tools = [invocation["tool"] for invocation in invocations]
    write_tools_invoked = sorted(
        {
            invocation["tool"]
            for invocation in invocations
            if invocation["tool"] in WRITE_TOOLS
        }
    )
    gates = _fixture_gates(args.fixture, sections)
    nodes = sections["nodes"]["items"]
    runtime = before["job"].get("runtime_evidence", {}).get("runtime", {})
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
        }
    )
    if not all(gates.values()):
        failed = sorted(name for name, passed in gates.items() if not passed)
        raise RuntimeError(f"fixture gates failed: {failed}")
    if _git_head() != capture_commit:
        raise RuntimeError("repository HEAD changed during capture")
    session_id = process_identity["agent_session"]["session_id"]
    return {
        "schema_version": "cad.phase10-live-public-fixture-provisional/1",
        "status": "PROVISIONAL",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "capture_command": args.capture_command,
        "baseline_commit": _git_baseline(),
        "implementation_commit": capture_commit,
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
        "session_binding": {
            "session_id": session_id,
            "device_id": args.device_id,
            "document_id": job_snapshot.get("drawing", {}).get("document_id"),
            "document_revision": revision_before,
            "scene_id": scene["scene_id"],
            "observation_job_ids": observation_job_ids,
            "captured_at": invocations[-1]["completed_at"],
        },
        "public_path": {
            "endpoint": args.endpoint,
            "device_id": args.device_id,
            "standalone_desktop_agent": True,
            "invoked_tools": invoked_tools,
            "write_tools_invoked": write_tools_invoked,
            "tool_invocations": invocations,
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
    }


def _validate_invocation_graph(
    invocations: list[dict[str, Any]],
    *,
    observation_job_ids: list[str],
    scene_id: str,
    source_snapshot_id: str,
    device_id: str,
    fixture_id: str,
) -> None:
    if not isinstance(invocations, list) or not invocations:
        raise ValueError("tool invocation records are missing")
    parsed_started_at: dict[int, datetime] = {}
    for item in invocations:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("tool"), str)
            or not isinstance(item.get("arguments"), dict)
            or item.get("outcome") != "succeeded"
            or not isinstance(item.get("started_at"), str)
            or not isinstance(item.get("completed_at"), str)
        ):
            raise ValueError("tool invocation record is invalid")
        if _parse_timestamp(
            item["started_at"], "invocation started_at"
        ) > _parse_timestamp(item["completed_at"], "invocation completed_at"):
            raise ValueError("tool invocation is not chronologically ordered")
    parsed_started_at = {
        index: _parse_timestamp(item["started_at"], "invocation started_at")
        for index, item in enumerate(invocations)
    }
    ordered = sorted(
        invocations,
        key=lambda item: parsed_started_at[invocations.index(item)],
    )
    if ordered != invocations:
        raise ValueError("tool invocations are not stored chronologically")
    for previous, current in zip(ordered, ordered[1:]):
        if _parse_timestamp(
            previous["completed_at"], "invocation completed_at"
        ) > _parse_timestamp(current["started_at"], "invocation started_at"):
            raise ValueError("tool invocations overlap in time")
    fixture_letter = _fixture_letter_from_id(fixture_id)
    key_re = re.compile(
        rf"^phase10-drawing-{fixture_letter}-"
        r"(before|after|scene|scene-repeat)-(\d{14})$"
    )
    phases = [
        ("cad_list_devices", None),
        ("cad_observe", observation_job_ids[0], "before"),
        ("cad_get_job", observation_job_ids[0]),
        ("cad_build_scene", None, "scene"),
        ("cad_build_scene", None, "scene-repeat"),
        ("cad_query_scene", None),
        ("read_resource", None),
        ("cad_observe", observation_job_ids[1], "after"),
        ("cad_get_job", observation_job_ids[1]),
    ]
    phase_index = 0
    visited: set[int] = set()
    observed_jobs: list[str] = []
    queried_sections: set[str] = set()
    key_values: list[str] = []
    key_stamps: list[str] = []
    for item in ordered:
        tool = item["tool"]
        job_id = item.get("job_id")
        while phase_index < len(phases):
            expected = phases[phase_index]
            if (
                expected[0] == tool
                and (len(expected) < 2 or expected[1] is None or expected[1] == job_id)
            ):
                break
            phase_index += 1
        if phase_index >= len(phases) or phases[phase_index][0] != tool:
            raise ValueError(f"tool invocation {tool} is out of phase")
        visited.add(phase_index)
        arguments = item.get("arguments", {})
        if tool == "cad_list_devices":
            if arguments != {"online_only": True}:
                raise ValueError("cad_list_devices invocation arguments are invalid")
        elif tool == "cad_observe":
            expected_phase = phases[phase_index][2]
            key = arguments.get("idempotency_key")
            key_match = key_re.fullmatch(key) if isinstance(key, str) else None
            if (
                set(arguments)
                != {
                    "device_id",
                    "observation_level",
                    "include_preview_image",
                    "idempotency_key",
                }
                or arguments.get("device_id") != device_id
                or arguments.get("observation_level") != "detail"
                or arguments.get("include_preview_image") is not False
                or key_match is None
                or key_match.group(1) != expected_phase
            ):
                raise ValueError("cad_observe invocation arguments are invalid")
            if job_id not in observation_job_ids:
                raise ValueError(
                    "cad_observe invocation job ID differs from the retained request"
                )
            observed_jobs.append(job_id)
            key_values.append(key)
            key_stamps.append(key_match.group(2))
        elif tool == "cad_get_job":
            if arguments != {"job_id": job_id} or job_id not in observation_job_ids:
                raise ValueError("cad_get_job invocation arguments are invalid")
        elif tool == "cad_build_scene":
            expected_phase = phases[phase_index][2]
            key = arguments.get("idempotency_key")
            key_match = key_re.fullmatch(key) if isinstance(key, str) else None
            if (
                arguments.get("source_snapshot_id") != source_snapshot_id
                or arguments.get("analysis_profile") != "mechanical-2d/1"
                or arguments.get("space") != "model"
                or arguments.get("include_sections") != list(SECTIONS)
                or key_match is None
                or key_match.group(1) != expected_phase
            ):
                raise ValueError("cad_build_scene invocation arguments are invalid")
            key_values.append(key)
            key_stamps.append(key_match.group(2))
        elif tool == "cad_query_scene":
            section = arguments.get("section")
            if (
                arguments
                != {
                    "scene_id": scene_id,
                    "section": section,
                    "limit": 200,
                }
                or section not in SECTIONS
                or section in queried_sections
            ):
                raise ValueError(
                    "cad_query_scene invocation arguments are invalid or duplicated"
                )
            queried_sections.add(section)
        else:
            if arguments != {"uri": f"cad://scenes/{scene_id}/summary"}:
                raise ValueError("summary resource invocation arguments are invalid")
        if tool not in {"cad_get_job", "cad_query_scene"}:
            phase_index += 1
    if visited != set(range(len(phases))):
        missing = sorted(set(range(len(phases))) - visited)
        raise ValueError(
            "tool invocation trace is missing expected phase(s): "
            + ", ".join(str(index) for index in missing)
        )
    if observed_jobs != observation_job_ids:
        raise ValueError(
            "cad_observe invocations are not bound to the observation job IDs"
        )
    if queried_sections != set(SECTIONS):
        raise ValueError(
            "expected exactly one cad_query_scene per retained section"
        )
    if len(set(key_values)) != 4:
        raise ValueError("cad_observe idempotency keys are reused")
    if len(set(key_stamps)) != 1:
        raise ValueError("invocations are not bound to one capture run")


def _bind_no_effect_db(
    evidence: dict[str, Any],
    no_effect_db: dict[str, Any],
    *,
    device_id: str,
    implementation_commit: str,
) -> None:
    if no_effect_db.get("schema_version") != "cad.phase10-live-db-evidence/1":
        raise ValueError("no-effect DB schema mismatch")
    if no_effect_db.get("implementation_commit") != implementation_commit:
        raise ValueError("no-effect DB commit differs from the capture")
    db_captured_at = _parse_timestamp(
        no_effect_db.get("captured_at"), "no-effect DB captured_at"
    )
    scope = no_effect_db.get("scope", {})
    scope_owner = scope.get("owner_subject")
    if scope.get("device_id") != device_id:
        raise ValueError("no-effect DB scope device differs from the fixture")
    if not isinstance(scope_owner, str) or not scope_owner:
        raise ValueError("no-effect DB scope owner is invalid")
    window_start = _parse_timestamp(
        scope.get("window_start"), "no-effect DB window_start"
    )
    window_end = _parse_timestamp(
        scope.get("window_end"), "no-effect DB window_end"
    )
    invocations = evidence["public_path"]["tool_invocations"]
    first_invocation = _parse_timestamp(
        invocations[0]["started_at"], "first invocation started_at"
    )
    last_invocation = _parse_timestamp(
        invocations[-1]["completed_at"], "last invocation completed_at"
    )
    if not (window_start <= first_invocation and last_invocation <= window_end):
        raise ValueError("no-effect DB window does not cover the fixture capture")
    if not window_end <= db_captured_at:
        raise ValueError("no-effect DB was captured before the audit window closed")
    observation_job_ids = evidence["session_binding"]["observation_job_ids"]
    scope_anchor_job_ids = scope.get("anchor_job_ids")
    if (
        not isinstance(scope_anchor_job_ids, list)
        or len(scope_anchor_job_ids) != len(set(scope_anchor_job_ids))
        or not set(observation_job_ids) <= set(scope_anchor_job_ids)
    ):
        raise ValueError(
            "fixture observation jobs are absent from the no-effect DB scope"
        )
    scene_id = evidence["scene"]["scene_id"]
    if scene_id not in scope.get("scene_ids", []):
        raise ValueError("fixture scene is absent from the no-effect DB scope")
    anchor_jobs = no_effect_db.get("anchor_jobs", [])
    anchor_by_id = {
        item.get("job_id"): item
        for item in anchor_jobs
        if isinstance(item, dict)
    }
    for job_id in observation_job_ids:
        job = anchor_by_id.get(job_id)
        if job is None:
            raise ValueError(
                "no-effect DB evidence does not cover the observation jobs"
            )
        if (
            job.get("effect_class") != "read"
            or job.get("state") != "succeeded"
            or job.get("device_id") != device_id
            or job.get("owner_subject") != scope_owner
        ):
            raise ValueError("observation anchor job is not cross-bound")
        if (
            _parse_timestamp(job.get("created_at"), "anchor job created_at")
            > db_captured_at
            or _parse_timestamp(job.get("updated_at"), "anchor job updated_at")
            > db_captured_at
        ):
            raise ValueError("anchor job timestamp exceeds the DB capture time")
    scene_rows = [
        item
        for item in no_effect_db.get("scenes", [])
        if isinstance(item, dict) and item.get("scene_id") == scene_id
    ]
    if len(scene_rows) != 1:
        raise ValueError(
            "no-effect DB scene record is missing for the fixture scene"
        )
    if (
        scene_rows[0].get("device_id") != device_id
        or scene_rows[0].get("owner_subject") != scope_owner
    ):
        raise ValueError("no-effect DB scene record is not cross-bound")
    if (
        _parse_timestamp(scene_rows[0].get("created_at"), "DB scene created_at")
        > db_captured_at
    ):
        raise ValueError("scene timestamp exceeds the DB capture time")
    session = evidence["runtime_identity"]["agent_session"]
    session_id = session.get("session_id")
    db_sessions = {
        item.get("session_id"): item
        for item in no_effect_db.get("agent_sessions", [])
        if isinstance(item, dict)
    }
    session_record = db_sessions.get(session_id)
    if session_record is None:
        raise ValueError(
            "process identity session is absent from the no-effect DB evidence"
        )
    if (
        session_record.get("device_id") != device_id
        or session_record.get("owner_subject") != scope_owner
    ):
        raise ValueError("no-effect DB session is not cross-bound")
    session_connected = _parse_timestamp(
        session_record.get("connected_at"), "DB session connected_at"
    )
    if session_connected != _parse_timestamp(
        session["connected_at"], "runtime identity session connected_at"
    ):
        raise ValueError(
            "session connected_at differs between runtime identity and DB"
        )
    if session_connected > first_invocation:
        raise ValueError("session connected after the first public invocation")
    session_disconnected = session_record.get("disconnected_at")
    if not (
        session_disconnected is None
        or _parse_timestamp(
            session_disconnected, "DB session disconnected_at"
        )
        >= last_invocation
    ):
        raise ValueError("DB session was not active throughout the fixture capture")
    if session_disconnected is not None and _parse_timestamp(
        session_disconnected, "DB session disconnected_at"
    ) > db_captured_at:
        raise ValueError("session disconnect timestamp exceeds the DB capture time")
    if no_effect_db.get("retrospective_no_write_events") != []:
        raise ValueError("no-effect DB contains write events in the audit window")


async def _finalize_fixture(args: argparse.Namespace, token: str) -> dict[str, Any]:
    provisional = json.loads(args.fixture_evidence.read_text(encoding="utf-8"))
    if provisional.get("schema_version") != (
        "cad.phase10-live-public-fixture-provisional/1"
    ):
        raise ValueError("fixture evidence is not a provisional capture-public artifact")
    if provisional.get("status") != "PROVISIONAL":
        raise ValueError("fixture evidence is not in PROVISIONAL status")
    capture_commit = provisional.get("implementation_commit")
    if not isinstance(capture_commit, str) or not re.fullmatch(
        r"[0-9a-f]{40}", capture_commit
    ):
        raise ValueError("provisional capture implementation_commit is invalid")
    finalizer_commit = _git_head()
    if finalizer_commit != capture_commit:
        raise ValueError("finalizer commit differs from provisional capture commit")
    implementation_commit = capture_commit
    no_effect_db = json.loads(args.no_effect_db.read_text(encoding="utf-8"))
    invocations = provisional["public_path"]["tool_invocations"]
    _validate_invocation_graph(
        invocations,
        observation_job_ids=provisional["session_binding"]["observation_job_ids"],
        scene_id=provisional["scene"]["scene_id"],
        source_snapshot_id=provisional["source"]["snapshot_id"],
        device_id=args.device_id,
        fixture_id=provisional["fixture"]["fixture_id"],
    )
    _bind_no_effect_db(
        provisional,
        no_effect_db,
        device_id=args.device_id,
        implementation_commit=implementation_commit,
    )
    observation_job_ids = provisional["session_binding"]["observation_job_ids"]
    write_tools_invoked = sorted(
        {
            item["tool"]
            for item in invocations
            if item["tool"] in WRITE_TOOLS
        }
    )
    anchor_jobs = no_effect_db.get("anchor_jobs", [])
    write_snapshot = no_effect_db.get("write_snapshot", {})
    pre_snapshot = (
        no_effect_db.get("restart_comparison", {}).get(
            "pre_restart_write_snapshot", {}
        )
        if isinstance(no_effect_db.get("restart_comparison"), dict)
        else {}
    )
    no_write_events_in_window = no_effect_db.get(
        "retrospective_no_write_events"
    ) == []
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
        and provisional["fixture"]["dwg_file_hash_before"]
        == provisional["fixture"]["dwg_file_hash_after"]
        and provisional["source"]["document_revision_before"]
        == provisional["source"]["document_revision_after"]
        and write_snapshot_unchanged
    )
    session = provisional["runtime_identity"]["agent_session"]
    runtime = provisional["source"]["observation_before"]["job"].get(
        "runtime_evidence", {}
    ).get("runtime", {})
    session_record = {
        item.get("session_id"): item
        for item in no_effect_db.get("agent_sessions", [])
        if isinstance(item, dict)
    }[session["session_id"]]
    session_disconnected = session_record.get("disconnected_at")
    first_invocation = _parse_timestamp(
        invocations[0]["started_at"], "first invocation started_at"
    )
    runtime_identity_bound = (
        provisional["runtime_identity"]["gateway_process"]["release_commit"]
        == implementation_commit
        and session.get("device_id") == args.device_id
        and session.get("protocol_version") == "cad.agent/2"
        and session.get("disconnected_at") is None
        and session["managed_host"].get("runtime_id") == "managed_dotnet"
        and session["managed_host"].get("package_hash")
        == runtime.get("package_hash")
        and isinstance(session["managed_host"].get("process_id"), int)
        and session["managed_host"]["process_id"] > 0
        and _SHA256_RE.fullmatch(
            session["managed_host"].get("executable_sha256", "")
        )
        and _parse_timestamp(
            session["managed_host"]["started_at"], "host started_at"
        )
        <= first_invocation
        and isinstance(
            provisional["runtime_identity"]["autocad_process"].get("process_id"),
            int,
        )
        and provisional["runtime_identity"]["autocad_process"]["process_id"] > 0
        and _SHA256_RE.fullmatch(
            provisional["runtime_identity"]["autocad_process"].get(
                "executable_sha256", ""
            )
        )
        and _parse_timestamp(
            provisional["runtime_identity"]["autocad_process"]["started_at"],
            "autocad started_at",
        )
        <= first_invocation
        and isinstance(
            provisional["runtime_identity"]["desktop_agent_process"].get(
                "process_id"
            ),
            int,
        )
        and provisional["runtime_identity"]["desktop_agent_process"][
            "process_id"
        ]
        > 0
        and _SHA256_RE.fullmatch(
            provisional["runtime_identity"]["desktop_agent_process"].get(
                "executable_sha256", ""
            )
        )
        and _parse_timestamp(
            provisional["runtime_identity"]["desktop_agent_process"][
                "started_at"
            ],
            "desktop started_at",
        )
        <= first_invocation
        and _parse_timestamp(
            session["connected_at"], "session connected_at"
        )
        == _parse_timestamp(
            session_record.get("connected_at"), "DB session connected_at"
        )
        and (
            session_disconnected is None
            or _parse_timestamp(
                session_disconnected, "DB session disconnected_at"
            )
            >= _parse_timestamp(
                invocations[-1]["completed_at"], "last invocation completed_at"
            )
        )
    )
    gates = {
        **provisional["gate_results"],
        "no_write_events_in_window": no_write_events_in_window,
        "anchor_jobs_read_only": anchor_jobs_read_only,
        "write_snapshot_unchanged": write_snapshot_unchanged,
        "no_write_requested": no_write_requested,
        "no_cad_effect_attempted": no_cad_effect_attempted,
        "runtime_identity_bound": runtime_identity_bound,
    }
    if not all(gates.values()):
        failed = sorted(name for name, passed in gates.items() if not passed)
        raise RuntimeError(f"fixture gates failed: {failed}")
    return {
        **provisional,
        "schema_version": "cad.phase10-live-public-fixture/1",
        "status": "PASS",
        "finalization": {
            "implementation_commit": finalizer_commit,
            "finalized_at": datetime.now(timezone.utc).isoformat(),
        },
        "no_effect": {
            "dwg_file_hash_unchanged": gates["dwg_file_hash_unchanged"],
            "document_revision_unchanged": gates["document_revision_unchanged"],
            "entity_count_unchanged": gates["entity_count_unchanged"],
            "write_requested": not no_write_requested,
            "cad_effect_attempted": not no_cad_effect_attempted,
        },
        "no_effect_db_binding": _db_evidence_binding(
            args.no_effect_db, no_effect_db
        ),
        "gate_results": gates,
    }


async def _restart_query(
    args: argparse.Namespace,
    token: str,
    *,
    client_factory=None,
) -> dict[str, Any]:
    if client_factory is None:
        from fastmcp import Client

        client_factory = Client

    before = json.loads(args.before.read_text(encoding="utf-8"))
    restart_commit = _git_head()
    if before.get("implementation_commit") != restart_commit:
        raise ValueError("restart checkout differs from fixture capture commit")
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
    exit_probe = derived_after["exit_probe"]
    exit_command = tuple(exit_probe.get("command", []))
    if exit_probe.get("pid") != old_pid:
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
    scene = before["scene"]
    scene_id = scene["scene_id"]
    invocations: list[dict[str, Any]] = []

    def record(
        tool: str,
        arguments: dict[str, Any],
        started_at: str,
        *,
        job_id: str | None = None,
    ) -> None:
        invocations.append(
            {
                "tool": tool,
                "arguments": arguments,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "outcome": "succeeded",
                "job_id": job_id,
            }
        )

    async with client_factory(args.endpoint, auth=token, timeout=120) as client:
        started_at = datetime.now(timezone.utc).isoformat()
        devices = _payload(
            await client.call_tool("cad_list_devices", {"online_only": True})
        )
        record("cad_list_devices", {"online_only": True}, started_at)
        if not any(
            item["device_id"] == args.device_id for item in devices["devices"]
        ):
            raise RuntimeError("Agent did not reconnect")
        sections = await _query_sections(client, scene_id, record=record)
        started_at = datetime.now(timezone.utc).isoformat()
        resource = _resource_payload(
            await client.read_resource(f"cad://scenes/{scene_id}/summary")
        )
        record(
            "read_resource",
            {"uri": f"cad://scenes/{scene_id}/summary"},
            started_at,
        )
    write_tools_invoked = _validate_restart_invocations(
        invocations, scene_id=scene_id
    )
    restart_captured_at = datetime.now(timezone.utc).isoformat()
    service_gates = _service_restart_gates(
        process_before,
        process_after,
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
    }
    if not all(gates.values()):
        failed = sorted(name for name, passed in gates.items() if not passed)
        raise RuntimeError(f"restart gates failed: {failed}")
    if _git_head() != restart_commit:
        raise RuntimeError("repository HEAD changed during restart capture")
    return {
        "schema_version": "cad.phase10-live-gateway-restart-provisional/1",
        "status": "PROVISIONAL",
        "captured_at": restart_captured_at,
        "capture_command": args.capture_command,
        "baseline_commit": _git_baseline(),
        "implementation_commit": restart_commit,
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
        "post_restart_public_path": {
            "invoked_tools": [item["tool"] for item in invocations],
            "write_tools_invoked": write_tools_invoked,
            "tool_invocations": invocations,
        },
        "gate_results": gates,
        **gates,
        "failures_retests": [],
        "limitations": [],
    }


async def _finalize_restart(args: argparse.Namespace, token: str) -> dict[str, Any]:
    provisional = json.loads(args.restart_evidence.read_text(encoding="utf-8"))
    if provisional.get("schema_version") != "cad.phase10-live-gateway-restart-provisional/1":
        raise ValueError("restart evidence is not a provisional restart-query artifact")
    if provisional.get("status") != "PROVISIONAL":
        raise ValueError("restart evidence is not provisional")
    implementation_commit = provisional.get("implementation_commit")
    if not isinstance(implementation_commit, str) or _git_head() != implementation_commit:
        raise ValueError("finalizer commit differs from provisional restart capture")
    no_effect_db = json.loads(args.no_effect_db.read_text(encoding="utf-8"))
    invocations = provisional["post_restart_public_path"]["tool_invocations"]
    _validate_restart_invocations(invocations, scene_id=provisional["scene_id"])
    _validate_restart_window(
        invocations,
        no_effect_db,
        gateway_after_at=provisional["gateway_process_after"]["captured_at"],
        identity_after_at=provisional["identity_capture_after"]["captured_at"],
        restart_captured_at=provisional["captured_at"],
    )
    db_gates = _db_restart_gates(
        no_effect_db,
        provisional["gateway_process_before"],
        provisional["gateway_process_after"],
        device_id=args.device_id,
        scene_id=provisional["scene_id"],
        implementation_commit=implementation_commit,
    )
    base_gates = dict(provisional.get("gate_results", {}))
    write_tools = provisional["post_restart_public_path"]["write_tools_invoked"]
    gates = {
        **base_gates,
        **db_gates,
        "no_write_requested": (
            write_tools == []
            and db_gates["no_write_events_in_window"]
            and db_gates["anchor_jobs_read_only"]
        ),
        "no_cad_effect_attempted": (
            provisional["dwg_file_hash_unchanged"]
            and provisional["document_revision_unchanged"]
            and db_gates["no_write_events_in_window"]
            and db_gates["write_snapshot_unchanged"]
        ),
    }
    if not all(gates.values()):
        raise RuntimeError(
            f"restart gates failed: {sorted(name for name, passed in gates.items() if not passed)}"
        )
    return {
        **provisional,
        "schema_version": "cad.phase10-live-gateway-restart/1",
        "status": "PASS",
        "finalization": {
            "implementation_commit": implementation_commit,
            "finalized_at": datetime.now(timezone.utc).isoformat(),
        },
        "no_effect_db_binding": _db_evidence_binding(args.no_effect_db, no_effect_db),
        "write_requested": not gates["no_write_requested"],
        "cad_effect_attempted": not gates["no_cad_effect_attempted"],
        "gate_results": gates,
        **gates,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)
    endpoint_default = "https://cad.kythuatvang.com/mcp"

    capture_public = subparsers.add_parser("capture-public")
    capture_public.add_argument("--endpoint", default=endpoint_default)
    capture_public.add_argument("--device-id", required=True)
    capture_public.add_argument("--token-file", required=True, type=Path)
    capture_public.add_argument("--output", required=True, type=Path)
    capture_public.add_argument("--operator", default="local-operator")
    capture_public.add_argument("--fixture", required=True, choices=("a", "b", "c"))
    capture_public.add_argument("--drawing", required=True, type=Path)
    capture_public.add_argument("--process-identity", required=True, type=Path)

    finalize = subparsers.add_parser("finalize-fixture")
    finalize.add_argument("--fixture-evidence", required=True, type=Path)
    finalize.add_argument("--no-effect-db", required=True, type=Path)
    finalize.add_argument("--device-id", required=True)
    finalize.add_argument("--output", required=True, type=Path)
    finalize.add_argument("--operator", default="local-operator")

    finalize_restart = subparsers.add_parser("finalize-restart")
    finalize_restart.add_argument("--restart-evidence", required=True, type=Path)
    finalize_restart.add_argument("--no-effect-db", required=True, type=Path)
    finalize_restart.add_argument("--device-id", required=True)
    finalize_restart.add_argument("--output", required=True, type=Path)
    finalize_restart.add_argument("--operator", default="local-operator")

    identity = subparsers.add_parser("capture-identity")
    identity.add_argument("--service", default=SERVICE_UNIT)
    identity.add_argument("--old-pid", type=int)
    identity.add_argument("--output", required=True, type=Path)
    identity.add_argument("--operator", default="local-operator")

    restart = subparsers.add_parser("restart-query")
    restart.add_argument("--endpoint", default=endpoint_default)
    restart.add_argument("--device-id", required=True)
    restart.add_argument("--token-file", required=True, type=Path)
    restart.add_argument("--output", required=True, type=Path)
    restart.add_argument("--operator", default="local-operator")
    restart.add_argument("--before", required=True, type=Path)
    restart.add_argument("--process-before", required=True, type=Path)
    restart.add_argument("--process-after", required=True, type=Path)
    restart.add_argument("--identity-before", required=True, type=Path)
    restart.add_argument("--identity-after", required=True, type=Path)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    command = [
        "python scripts/phase10-live-public-evidence.py",
        args.action,
        f"--output {args.output}",
        f"--operator {args.operator}",
    ]
    if args.action == "capture-public":
        command.extend(
            (
                f"--endpoint {args.endpoint}",
                f"--device-id {args.device_id}",
                "--token-file <redacted>",
                f"--fixture {args.fixture}",
                f"--drawing {args.drawing}",
                f"--process-identity {args.process_identity}",
            )
        )
    elif args.action == "finalize-fixture":
        command.extend(
            (
                f"--fixture-evidence {args.fixture_evidence}",
                f"--no-effect-db {args.no_effect_db}",
                f"--device-id {args.device_id}",
            )
        )
    elif args.action == "finalize-restart":
        command.extend(
            (
                f"--restart-evidence {args.restart_evidence}",
                f"--no-effect-db {args.no_effect_db}",
                f"--device-id {args.device_id}",
            )
        )
    elif args.action == "capture-identity":
        command.append(f"--service {args.service}")
        if args.old_pid is not None:
            command.append(f"--old-pid {args.old_pid}")
    else:
        command.extend(
            (
                f"--endpoint {args.endpoint}",
                f"--device-id {args.device_id}",
                "--token-file <redacted>",
                f"--before {args.before}",
                f"--process-before {args.process_before}",
                f"--process-after {args.process_after}",
                f"--identity-before {args.identity_before}",
                f"--identity-after {args.identity_after}",
            )
        )
    args.capture_command = " ".join(command)
    token: str | None = None
    if args.action in {"capture-public", "restart-query"}:
        token = json.loads(args.token_file.read_text(encoding="utf-8"))[
            "access_token"
        ]
    runner = {
        "capture-public": _capture_public,
        "finalize-fixture": _finalize_fixture,
        "finalize-restart": _finalize_restart,
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
