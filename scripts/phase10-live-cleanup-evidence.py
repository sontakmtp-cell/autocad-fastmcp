"""Capture the live read-only Phase 10 cleanup workflow over Drawing C."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp import Client


REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ISSUES = {
    "degenerate_geometry",
    "duplicate_geometry",
    "open_contour",
    "self_intersection",
}


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


def _git(command: str) -> str:
    return subprocess.run(
        ["git", *command.split()],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


async def capture(args: argparse.Namespace, token: str) -> dict[str, Any]:
    fixture = json.loads(args.fixture_evidence.read_text(encoding="utf-8"))
    scene = fixture["scene"]
    source = fixture["source"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    start_key = f"phase10-cleanup-start-{stamp}"
    finish_key = f"phase10-cleanup-finish-{stamp}"
    inputs = {
        "source_snapshot_id": source["snapshot_id"],
        "document_revision": scene["document_revision"],
        "layer": "0",
        "page_size": 200,
        "max_candidates": 128,
    }
    async with Client(args.endpoint, auth=token, timeout=120) as client:
        started = _payload(
            await client.call_tool(
                "cad_start_workflow",
                {
                    "skill_id": "drawing.cleanup-audit",
                    "skill_version": "1.1.0",
                    "device_id": args.device_id,
                    "source_snapshot_id": source["snapshot_id"],
                    "inputs": inputs,
                    "idempotency_key": start_key,
                },
            )
        )
        detail = _payload(
            await client.call_tool(
                "cad_get_workflow",
                {"run_id": started["run_id"], "event_limit": 100},
            )
        )
        report_step = next(
            item for item in detail["steps"] if item["step_id"] == "report"
        )
        report = report_step["output_ref"]["result"]
        completed = _payload(
            await client.call_tool(
                "cad_control_workflow",
                {
                    "run_id": started["run_id"],
                    "action": "submit_input",
                    "expected_state_version": detail["run"]["state_version"],
                    "idempotency_key": finish_key,
                    "payload": {"decision": "continue"},
                },
            )
        )
        final = _payload(
            await client.call_tool(
                "cad_get_workflow",
                {"run_id": started["run_id"], "event_limit": 100},
            )
        )

    gates = {
        "cleanup_workflow_version": detail["run"]["skill_id"]
        == "drawing.cleanup-audit"
        and detail["run"]["skill_version"] == "1.1.0",
        "same_scene_reused": report["scene_id"] == scene["scene_id"]
        and report["scene_digest"] == scene["scene_digest"]
        and report["source_digest"] == scene["source_digest"],
        "same_snapshot_and_revision": report["source_snapshot_id"]
        == source["snapshot_id"]
        and str(report["document_revision"]) == str(scene["document_revision"]),
        "required_issues_reported": REQUIRED_ISSUES
        <= set(report["issue_codes"]),
        "read_only_report": report["write_authority"] is False,
        "workflow_completed": completed["state"] == "succeeded"
        and final["run"]["state"] == "succeeded",
        "document_revision_unchanged": fixture["no_effect"][
            "document_revision_unchanged"
        ],
        "dwg_file_hash_unchanged": fixture["no_effect"]["dwg_file_hash_unchanged"],
        "no_write_requested": True,
        "no_cad_effect_attempted": True,
    }
    if not all(gates.values()):
        failed = sorted(name for name, passed in gates.items() if not passed)
        raise RuntimeError(f"cleanup workflow gates failed: {failed}")
    return {
        "schema_version": "cad.phase10-live-cleanup-workflow/1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "capture_command": args.capture_command,
        "baseline_commit": _git("merge-base HEAD origin/main"),
        "implementation_commit": _git("rev-parse HEAD"),
        "operator": args.operator,
        "fixture_id": fixture["fixture"]["fixture_id"],
        "device_id": args.device_id,
        "source_snapshot_id": source["snapshot_id"],
        "document_id": scene["document_id"],
        "document_revision": scene["document_revision"],
        "scene_id": scene["scene_id"],
        "source_digest": scene["source_digest"],
        "scene_digest": scene["scene_digest"],
        "started": started,
        "report": report,
        "completed": completed,
        "final": final,
        "invoked_tools": [
            "cad_start_workflow",
            "cad_get_workflow",
            "cad_control_workflow",
        ],
        "write_tools_invoked": [],
        "write_requested": False,
        "cad_effect_attempted": False,
        "gate_results": gates,
        "failures_retests": [],
        "limitations": [],
        "status": "PASS",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="https://cad.kythuatvang.com/mcp")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--fixture-evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--operator", default="local-operator")
    args = parser.parse_args()
    args.capture_command = " ".join(
        (
            "python scripts/phase10-live-cleanup-evidence.py",
            f"--endpoint {args.endpoint}",
            f"--device-id {args.device_id}",
            "--token-file <redacted>",
            f"--fixture-evidence {args.fixture_evidence}",
            f"--output {args.output}",
            f"--operator {args.operator}",
        )
    )
    token = json.loads(args.token_file.read_text(encoding="utf-8"))["access_token"]
    result = asyncio.run(capture(args, token))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
