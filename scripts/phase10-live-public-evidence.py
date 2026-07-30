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

from fastmcp import Client


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
    before = json.loads(args.before.read_text(encoding="utf-8"))
    process_before = json.loads(args.process_before.read_text(encoding="utf-8"))
    process_after = json.loads(args.process_after.read_text(encoding="utf-8"))
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
    same_sections = all(
        sections[name]["items"] == scene["sections"][name]["items"]
        and sections[name]["total"] == scene["sections"][name]["total"]
        for name in SECTIONS
    )
    gates = {
        "actual_gateway_process_restart": process_before["gateway_pid"]
        != process_after["gateway_pid"],
        "gateway_public_reconnect": process_before["agent_session_id"]
        != process_after["agent_session_id"],
        "standalone_desktop_agent": process_before["desktop_agent_pid"]
        == process_after["desktop_agent_pid"],
        "same_scene_retrieved": same_sections
        and scene["scene_digest"] == process_after["scene_digest"],
        "public_query_succeeded": True,
        "dwg_file_hash_unchanged": before["no_effect"]["dwg_file_hash_unchanged"],
        "document_revision_unchanged": before["no_effect"][
            "document_revision_unchanged"
        ],
        "no_write_requested": True,
        "no_cad_effect_attempted": True,
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
    args = parser.parse_args()
    if args.action == "capture" and any(
        value is None
        for value in (args.fixture, args.drawing, args.process_identity)
    ):
        parser.error("capture requires --fixture, --drawing and --process-identity")
    if args.action == "restart-query" and any(
        value is None
        for value in (args.before, args.process_before, args.process_after)
    ):
        parser.error(
            "restart-query requires --before, --process-before and --process-after"
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
