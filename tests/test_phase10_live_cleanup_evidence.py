from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "phase10-live-cleanup-evidence.py"
DIGEST = "sha256:" + "a" * 64


def _load_script(monkeypatch):
    fastmcp = types.ModuleType("fastmcp")
    fastmcp.Client = object
    monkeypatch.setitem(sys.modules, "fastmcp", fastmcp)
    spec = importlib.util.spec_from_file_location(
        "phase10_live_cleanup_evidence", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capture_uses_full_workflow_detail_shape(monkeypatch, tmp_path):
    module = _load_script(monkeypatch)
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "fixture": {"fixture_id": "phase10-drawing-c-r25/1"},
                "source": {"snapshot_id": "snapshot-c"},
                "scene": {
                    "scene_id": "scene-c",
                    "scene_digest": DIGEST,
                    "source_digest": DIGEST,
                    "document_id": "document-c",
                    "document_revision": "revision-c",
                },
                "no_effect": {
                    "document_revision_unchanged": True,
                    "dwg_file_hash_unchanged": True,
                },
            }
        ),
        encoding="utf-8",
    )

    class FakeClient:
        calls = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, payload):
            self.calls.append((name, payload))
            if name == "cad_start_workflow":
                return {
                    "run_id": "run-c",
                    "pins": {},
                    "state": "waiting_for_user",
                    "state_version": 3,
                    "current_step_id": "review",
                    "required_next_action": "submit_input",
                    "replayed": False,
                    "resource_uri": "cad://workflows/run-c",
                }
            if name == "cad_get_workflow" and len(self.calls) == 2:
                return {
                    "run": {
                        "run_id": "run-c",
                        "skill_id": "drawing.cleanup-audit",
                        "skill_version": "1.1.0",
                        "state": "waiting_for_user",
                        "state_version": 4,
                    },
                    "steps": [
                        {
                            "step_id": "report",
                            "output_ref": {
                                "result": {
                                    "scene_id": "scene-c",
                                    "scene_digest": DIGEST,
                                    "source_digest": DIGEST,
                                    "source_snapshot_id": "snapshot-c",
                                    "document_revision": "revision-c",
                                    "issue_codes": sorted(module.REQUIRED_ISSUES),
                                    "write_authority": False,
                                }
                            },
                        }
                    ],
                }
            if name == "cad_control_workflow":
                assert payload["expected_state_version"] == 4
                return {"run_id": "run-c", "state": "succeeded"}
            return {"run": {"run_id": "run-c", "state": "succeeded"}}

    monkeypatch.setattr(module, "Client", FakeClient)
    monkeypatch.setattr(module, "_git", lambda command: "commit")
    args = argparse.Namespace(
        fixture_evidence=fixture_path,
        endpoint="https://example.invalid/mcp",
        device_id="device-c",
        capture_command="test",
        operator="tester",
    )

    result = asyncio.run(module.capture(args, "token"))

    assert result["status"] == "PASS"
    assert result["gate_results"]["cleanup_workflow_version"] is True
    assert result["failures_retests"][0]["failure"].startswith(
        "The first live start was rejected with capability_missing"
    )
