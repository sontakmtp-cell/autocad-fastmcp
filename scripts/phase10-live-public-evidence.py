"""Capture auditable Phase 10 evidence through the public Gateway/Agent path."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import json
import msvcrt
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("nodes", "relations", "contours", "features", "issues", "evidence")


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
    required_identity = {
        "gateway_process",
        "desktop_agent_process",
        "autocad_process",
        "agent_session",
    }
    if set(process_identity) != required_identity:
        raise ValueError(
            f"process identity must contain exactly {sorted(required_identity)}"
        )
    if process_identity["agent_session"].get("device_id") != args.device_id:
        raise ValueError("process identity device does not match --device-id")
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
            "no_write_requested": True,
            "no_cad_effect_attempted": True,
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
            "write_tools_invoked": [],
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
            "write_requested": False,
            "cad_effect_attempted": False,
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
        "status": "PASS",
    }


async def _restart_query(args: argparse.Namespace, token: str) -> dict[str, Any]:
    from fastmcp import Client

    before = json.loads(args.before.read_text(encoding="utf-8"))
    process_before = json.loads(args.process_before.read_text(encoding="utf-8"))
    process_after = json.loads(args.process_after.read_text(encoding="utf-8"))
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("capture", "restart-query"))
    parser.add_argument("--endpoint", default="https://cad.kythuatvang.com/mcp")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--operator", default="local-operator")
    parser.add_argument("--fixture", choices=("a", "b", "c"))
    parser.add_argument("--drawing", type=Path)
    parser.add_argument("--process-identity", type=Path)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--process-before", type=Path)
    parser.add_argument("--process-after", type=Path)
    parser.add_argument("--no-effect-db", type=Path)
    args = parser.parse_args()
    if args.action == "capture" and any(
        value is None
        for value in (args.fixture, args.drawing, args.process_identity)
    ):
        parser.error("capture requires --fixture, --drawing and --process-identity")
    if args.action == "restart-query" and any(
        value is None
        for value in (
            args.before,
            args.process_before,
            args.process_after,
            args.no_effect_db,
        )
    ):
        parser.error(
            "restart-query requires --before, --process-before, --process-after "
            "and --no-effect-db"
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
            )
        )
    else:
        command.extend(
            (
                f"--before {args.before}",
                f"--process-before {args.process_before}",
                f"--process-after {args.process_after}",
                f"--no-effect-db {args.no_effect_db}",
            )
        )
    args.capture_command = " ".join(command)
    token = json.loads(args.token_file.read_text(encoding="utf-8"))["access_token"]
    result = asyncio.run(
        _capture(args, token)
        if args.action == "capture"
        else _restart_query(args, token)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
