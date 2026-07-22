from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from autocad_contracts import CommandMessage, canonical_payload_hash

from autocad_desktop_agent.executor import AgentExecutionError, DrawingInfoExecutor


PACKAGE = {"package_id": "autocad.lisp.drawing_info", "version": "3.3-c1", "sha256": "a" * 64}


@dataclass
class Result:
    ok: bool
    payload: dict | None = None
    error_code: str | None = None
    details: dict | None = None


class ReadPort:
    def __init__(self):
        self.health_calls = 0
        self.drawing_calls = 0

    async def health(self):
        self.health_calls += 1
        return Result(True, {})

    async def drawing_info(self):
        self.drawing_calls += 1
        return Result(
            True,
            {
                "document_name": r"C:\secret\mat-bich.dwg",
                "entity_count": 12,
                "layers": ["0", "DIM"],
                "layer_count": 2,
                "truncated": False,
                "dispatcher_version": "3.3-c1",
                "package_id": PACKAGE["package_id"],
                "package_version": PACKAGE["version"],
            },
        )


def command(**changes):
    payload = {
        "observation_level": "summary",
        "include_preview_image": False,
        "package": PACKAGE,
    }
    values = dict(
        session_id="session-1",
        device_id="device-1",
        job_id="job-1",
        command_id="command-1",
        idempotency_key="idem-1",
        payload_hash=canonical_payload_hash(payload),
        payload=payload,
        deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
    )
    values.update(changes)
    return CommandMessage(**values)


@pytest.mark.asyncio
async def test_executor_returns_summary_only_without_full_path():
    port = ReadPort()
    result = await DrawingInfoExecutor(port, PACKAGE, "0.1.0").execute(command())
    snapshot = result["snapshot"]
    assert snapshot["drawing"]["document_name"] == "mat-bich.dwg"
    assert snapshot["entity_summary"] == {"entity_count": 12, "detail_available": False}
    assert snapshot["entities"] == []
    assert snapshot["revision_evidence"]["commit_safe"] is False
    assert port.health_calls == port.drawing_calls == 1


@pytest.mark.asyncio
async def test_probe_maps_busy_without_reading_drawing():
    port = ReadPort()
    port.health = lambda: _result(Result(False, error_code="autocad_busy", details={
        "active_document": r"C:\\secret\\busy.dwg"
    }))
    presence = await DrawingInfoExecutor(port, PACKAGE, "0.1.0").probe()
    assert presence.runtime_state == "online_busy_user"
    assert presence.document_name == "busy.dwg"
    assert port.drawing_calls == 0


@pytest.mark.asyncio
async def test_probe_normalizes_missing_dispatcher_to_public_phase4_code():
    port = ReadPort()
    port.health = lambda: _result(
        Result(False, error_code="dispatcher_missing_in_active_document", details={})
    )

    presence = await DrawingInfoExecutor(port, PACKAGE, "0.1.0").probe()

    assert presence.runtime_state == "incompatible"
    assert presence.safe_error_code == "dispatcher_not_loaded"


async def _result(value):
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,code",
    [
        ({"kind": "write_fixture", "effect_class": "write"}, "capability_missing"),
        ({"payload": {"observation_level": "detail", "include_preview_image": False, "package": PACKAGE}}, "capability_missing"),
        ({"payload": {"observation_level": "summary", "include_preview_image": True, "package": PACKAGE}}, "capability_missing"),
        (
            {
                "issued_at": (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
                "deadline_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            },
            "deadline_expired",
        ),
    ],
)
async def test_forbidden_commands_never_touch_backend(changes, code):
    port = ReadPort()
    cmd = command(**changes)
    if "payload" in changes:
        cmd = cmd.model_copy(update={"payload_hash": canonical_payload_hash(cmd.payload)})
    with pytest.raises(AgentExecutionError, match=code):
        await DrawingInfoExecutor(port, PACKAGE, "0.1.0").execute(cmd)
    assert port.health_calls == port.drawing_calls == 0
