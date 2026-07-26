"""Protect the legacy LT adapter while Phase 5 adds managed runtime support."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from autocad_contracts import CommandMessage, canonical_payload_hash

from autocad_desktop_agent.executor import (
    DrawingInfoExecutor,
    SafeFileIPCCadReadPort,
)


PACKAGE = {
    "package_id": "autocad.lisp.drawing_info",
    "version": "3.3-c1",
    "sha256": "a" * 64,
}


@dataclass
class Result:
    ok: bool
    payload: dict | None = None
    error_code: str | None = None


class ReadPort:
    async def health(self):
        return Result(True, {})

    async def drawing_info(self):
        return Result(
            True,
            {
                "document_name": r"C:\private\lt-fixture.dwg",
                "entity_count": 3,
                "layers": ["0"],
                "layer_count": 1,
                "truncated": False,
                "dispatcher_version": PACKAGE["version"],
                "package_id": PACKAGE["package_id"],
                "package_version": PACKAGE["version"],
            },
        )


async def test_safe_file_ipc_adapter_remains_narrow_and_disables_raw_lisp(monkeypatch):
    calls: list[object] = []

    class Backend:
        def __init__(self, *, allow_execute_lisp):
            calls.append(("init", allow_execute_lisp))

        async def health(self):
            calls.append("health")
            return Result(True, {})

        async def drawing_info(self):
            calls.append("drawing_info")
            return Result(True, {"document_name": "lt-fixture.dwg"})

    monkeypatch.setattr(
        "autocad_mcp.backends.safe_file_ipc.SafeFileIPCBackend",
        Backend,
    )

    adapter = SafeFileIPCCadReadPort()
    assert (await adapter.health()).ok is True
    assert (await adapter.drawing_info()).ok is True
    assert not hasattr(adapter, "execute_lisp")
    assert calls == [("init", False), "health", "drawing_info"]


async def test_legacy_executor_accepts_additive_runtime_context_in_payload():
    payload = {
        "observation_level": "summary",
        "include_preview_image": False,
        "package": PACKAGE,
        "runtime_context": {
            "runtime_id": "autolisp_file_ipc",
            "revision_strength": "compatibility",
        },
    }
    command = CommandMessage(
        session_id="session-lt",
        device_id="device-lt",
        job_id="job-lt",
        command_id="command-lt",
        idempotency_key="idem-lt",
        payload_hash=canonical_payload_hash(payload),
        payload=payload,
        deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
    )

    result = await DrawingInfoExecutor(ReadPort(), PACKAGE, "0.1.0").execute(command)

    assert result["snapshot"]["drawing"]["document_name"] == "lt-fixture.dwg"
    assert result["snapshot"]["entities"] == []
    assert result["snapshot"]["revision_evidence"] == {
        "revision_schema": "cad.revision/1",
        "revision_strength": "summary_only",
        "commit_safe": False,
    }
