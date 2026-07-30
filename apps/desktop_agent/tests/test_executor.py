from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from autocad_contracts import CommandMessage, RuntimeEvidence, canonical_payload_hash

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


class DetailReadPort(ReadPort):
    async def entity_snapshot(self):
        return Result(
            True,
            {
                "document_id": "document-1",
                "database_fingerprint": "database-1",
                "revision": {"revision": 7},
                "scan_truncated": False,
                "entities": [
                    {
                        "handle": "2A",
                        "type": "LINE",
                        "layer": "0",
                        "space": "model",
                        "bounds": {"min": [0, 0, 0], "max": [10, 5, 0]},
                        "geometry": {"start": [0, 0], "end": [10, 5]},
                        "geometry_status": "exact",
                        "geometry_reason": None,
                        "source_capabilities": ["entity.geometry.line/1"],
                        "geometry_truncated": False,
                        "fingerprint": "sha256:" + "b" * 64,
                    }
                ],
            },
        )


class TruncatedDetailReadPort(DetailReadPort):
    async def entity_snapshot(self):
        result = await super().entity_snapshot()
        result.payload["scan_truncated"] = True
        return result


class ChangedRevisionReadPort(ReadPort):
    async def drawing_info(self):
        result = await super().drawing_info()
        result.payload["revision"] = {"revision": 7}
        return result

    async def entity_snapshot(self, *, expected_revision):
        assert expected_revision == 7
        result = await DetailReadPort().entity_snapshot()
        result.payload["revision"] = {"revision": 8}
        return result


class ManagedDetailReadPort(DetailReadPort):
    async def drawing_info(self):
        result = await super().drawing_info()
        result.payload["revision"] = {"revision": 7}
        return result

    async def entity_snapshot(self, *, expected_revision):
        assert expected_revision == 7
        return await super().entity_snapshot()


class ManagedBroker:
    def __init__(self, adapter):
        self.adapter = adapter

    async def select_read_runtime(self):
        return SimpleNamespace(
            adapter=self.adapter,
            evidence=RuntimeEvidence(id="managed_dotnet", role="primary"),
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
async def test_executor_returns_commit_safe_managed_detail_snapshot():
    port = DetailReadPort()
    cmd = command(
        payload={
            "observation_level": "detail",
            "include_preview_image": False,
            "package": PACKAGE,
        }
    )
    cmd = cmd.model_copy(update={"payload_hash": canonical_payload_hash(cmd.payload)})

    snapshot = (
        await DrawingInfoExecutor(port, PACKAGE, "0.1.0").execute(cmd)
    )["snapshot"]

    assert snapshot["document_revision"] == "7"
    assert snapshot["drawing"]["document_id"] == "document-1"
    assert snapshot["entities"][0]["geometry"] == {
        "start": [0, 0],
        "end": [10, 5],
    }
    assert snapshot["revision_evidence"] == {
        "revision_schema": "cad.revision/1",
        "revision_strength": "database_object_fingerprint",
        "commit_safe": True,
    }


@pytest.mark.asyncio
async def test_executor_preserves_managed_phase10_geometry_provenance():
    cmd = command(
        payload={
            "observation_level": "detail",
            "include_preview_image": False,
            "package": PACKAGE,
        }
    )
    cmd = cmd.model_copy(update={"payload_hash": canonical_payload_hash(cmd.payload)})

    snapshot = (
        await DrawingInfoExecutor(
            ReadPort(),
            PACKAGE,
            "0.1.0",
            runtime_broker=ManagedBroker(ManagedDetailReadPort()),
        ).execute(cmd)
    )["snapshot"]

    assert snapshot["entities"][0] == {
        "entity_id": "2A",
        "entity_type": "LINE",
        "layer": "0",
        "space": "model",
        "bounds": {"min": [0, 0, 0], "max": [10, 5, 0]},
        "geometry": {"start": [0, 0], "end": [10, 5]},
        "geometry_status": "exact",
        "geometry_reason": None,
        "geometry_truncated": False,
        "fingerprint": "sha256:" + "b" * 64,
        "source_runtime": "managed_dotnet",
        "source_capabilities": ["entity.geometry.line/1"],
    }


@pytest.mark.asyncio
async def test_executor_never_marks_truncated_detail_commit_safe():
    cmd = command(
        payload={
            "observation_level": "detail",
            "include_preview_image": False,
            "package": PACKAGE,
        }
    )
    cmd = cmd.model_copy(update={"payload_hash": canonical_payload_hash(cmd.payload)})

    snapshot = (
        await DrawingInfoExecutor(
            TruncatedDetailReadPort(), PACKAGE, "0.1.0"
        ).execute(cmd)
    )["snapshot"]

    assert snapshot["entity_summary"]["truncated"] is True
    assert snapshot["revision_evidence"]["commit_safe"] is False


@pytest.mark.asyncio
async def test_executor_rejects_summary_and_detail_from_different_revisions():
    cmd = command(
        payload={
            "observation_level": "detail",
            "include_preview_image": False,
            "package": PACKAGE,
        }
    )
    cmd = cmd.model_copy(update={"payload_hash": canonical_payload_hash(cmd.payload)})

    with pytest.raises(AgentExecutionError, match="active_document_changed"):
        await DrawingInfoExecutor(
            ReadPort(),
            PACKAGE,
            "0.1.0",
            runtime_broker=ManagedBroker(ChangedRevisionReadPort()),
        ).execute(cmd)


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
