from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from autocad_contracts import (
    ProgramCommandMessage,
    canonical_preview_digest,
    canonical_receipt_id,
    canonical_payload_hash,
    canonical_program_digest,
)

from autocad_desktop_agent.executor import AgentExecutionError
from autocad_desktop_agent.program_executor import (
    ProgramCommandExecutor,
    program_command_payload,
)


def _program() -> dict:
    return {
        "schema_version": "cad.program/0.2",
        "registry_version": "cad.program/0.2",
        "program_id": "program-1",
        "program_revision": 1,
        "device_id": "device-1",
        "source_snapshot_id": "snapshot-1",
        "document_id": "doc-1",
        "expected_document_revision": "42",
        "operations": [
            {
                "kind": "ensure_layer",
                "operation_id": "layer-1",
                "name": "PHASE6",
                "color_index": 2,
            },
            {
                "kind": "create_line",
                "operation_id": "line-1",
                "layer": {"operation_id": "layer-1", "output": "layer"},
                "start": {"x": 0.0, "y": 0.0, "z": 0.0},
                "end": {"x": 10.0, "y": 0.0, "z": 0.0},
            },
        ],
    }


def command(kind: str = "program_preview", **updates) -> ProgramCommandMessage:
    program = _program()
    binding = {
        "program_digest": canonical_program_digest(program),
        "execution_digest": f"sha256:{'2' * 64}",
        "document_id": "doc-1",
        "document_revision": "42",
        "runtime_id": "managed_dotnet",
        "runtime_role": "primary",
        "host_family": "R25",
        "host_version": "0.2.0",
        "package_id": "autocad.managed_host.r25",
        "package_version": "0.2.0",
        "package_hash": f"sha256:{'3' * 64}",
        "capability_manifest_hash": f"sha256:{'4' * 64}",
        "operation_registry_version": "cad.program/0.2",
        "operation_registry_hash": f"sha256:{'5' * 64}",
        "policy_version": "phase6-low-risk-v1",
    }
    values = {
        "session_id": "session-1",
        "device_id": "device-1",
        "job_id": f"job-{kind}",
        "command_id": f"command-{kind}",
        "idempotency_key": f"idem-{kind}",
        "payload_hash": "0" * 64,
        "kind": kind,
        "effect_class": "read" if kind == "program_validate" else "write",
        "binding": binding,
        "deadline_at": (
            datetime.now(timezone.utc) + timedelta(minutes=1)
        ).isoformat(),
    }
    if kind == "program_preview":
        values["program"] = program
        values["preview_id"] = "preview-1"
    elif kind == "program_commit":
        values.update(
            program=program,
            preview_id="preview-1",
            preview_digest=canonical_preview_digest("preview-1", binding),
            receipt_id=canonical_receipt_id("preview-1"),
        )
    else:
        values["validation"] = {
            "validation_id": "validation-1",
            "receipt_id": "receipt-1",
            "expected_entity_count": 1,
        }
    values.update(updates)
    parsed = ProgramCommandMessage.model_validate(values)
    return parsed.model_copy(
        update={"payload_hash": canonical_payload_hash(program_command_payload(parsed))}
    )


class Adapter:
    def __init__(self, *, health_error: str | None = None) -> None:
        self.health_error = health_error
        self.calls: list[tuple[str, dict]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def health(self):
        if self.health_error:
            return SimpleNamespace(
                ok=False,
                error_code=self.health_error,
                payload=None,
            )
        return SimpleNamespace(
            ok=True,
            payload={
                "active_document_id": "doc-1",
                "active_document_revision": "42",
            },
        )

    async def program_command(self, kind, *, arguments, deadline_at):
        self.calls.append((kind, arguments))
        self.started.set()
        if self.block:
            await self.release.wait()
        payloads = {
            "program_preview": {
                "preview_id": arguments.get("preview_id", "preview-1"),
                "preview_digest": canonical_preview_digest(
                    arguments.get("preview_id", "preview-1"),
                    arguments["execution_binding"],
                ),
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
                "planned_operation_count": 2,
                "planned_entity_count": 1,
                "planned_layer_count": 1,
                "transaction_aborted": True,
                "drawing_unchanged": True,
            },
            "program_commit": {
                "receipt_id": arguments.get(
                    "receipt_id",
                    canonical_receipt_id("preview-1"),
                ),
                "receipt_digest": f"sha256:{'7' * 64}",
                "document_revision_before": "42",
                "document_revision_after": "43",
                "created_entity_count": 1,
                "duplicate": False,
            },
            "program_validate": {
                "validation_id": arguments.get("validation", {}).get(
                    "validation_id",
                    "validation-1",
                ),
                "valid": True,
                "document_revision": "43",
                "checks": ["entity_count"],
                "failures": [],
            },
        }
        return SimpleNamespace(ok=True, payload=payloads[kind], error_code=None)


class Broker:
    def __init__(self, adapter: Adapter) -> None:
        self.adapter = adapter
        self.calls = []

    async def select_write_runtime(self, binding, **kwargs):
        self.calls.append((binding, kwargs))
        return SimpleNamespace(adapter=self.adapter)


async def test_preview_uses_typed_managed_host_arguments():
    adapter = Adapter()
    broker = Broker(adapter)
    result = await ProgramCommandExecutor(broker).execute(
        command(),
        write_lock_enabled=True,
    )

    assert result["transaction_aborted"] is True
    assert adapter.calls[0][0] == "program_preview"
    assert set(adapter.calls[0][1]) == {
        "program",
        "execution_binding",
        "preview_id",
    }
    assert adapter.calls[0][1]["preview_id"] == "preview-1"
    assert broker.calls[0][1]["required_capability"] == "cad.program.preview"
    assert broker.calls[0][1]["write_required"] is True


async def test_commit_and_validate_forward_exact_gateway_ids_to_host():
    adapter = Adapter()
    executor = ProgramCommandExecutor(Broker(adapter))

    await executor.execute(command("program_commit"), write_lock_enabled=True)
    await executor.execute(command("program_validate"), write_lock_enabled=True)

    commit_arguments = adapter.calls[0][1]
    assert commit_arguments["preview_binding"]["preview_id"] == "preview-1"
    assert commit_arguments["receipt_id"] == canonical_receipt_id("preview-1")
    validation_arguments = adapter.calls[1][1]["validation"]
    assert validation_arguments["validation_id"] == "validation-1"
    assert validation_arguments["receipt_id"] == "receipt-1"


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"payload_hash": "f" * 64}, "payload_mismatch"),
        (
            {
                "issued_at": (
                    datetime.now(timezone.utc) - timedelta(seconds=2)
                ).isoformat(),
                "deadline_at": (
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                ).isoformat(),
            },
            "deadline_expired",
        ),
    ],
)
async def test_invalid_command_never_selects_runtime(updates, code):
    broker = Broker(Adapter())
    cmd = command().model_copy(update=updates)
    with pytest.raises(AgentExecutionError, match=code):
        await ProgramCommandExecutor(broker).execute(
            cmd,
            write_lock_enabled=True,
        )
    assert broker.calls == []


@pytest.mark.parametrize(
    ("health_error", "code"),
    [
        ("managed_host_unavailable", "managed_host_unavailable"),
        ("autocad_busy", "autocad_busy"),
        ("modal_dialog_active", "modal_dialog_active"),
    ],
)
async def test_host_admission_failures_are_closed(health_error, code):
    executor = ProgramCommandExecutor(Broker(Adapter(health_error=health_error)))
    with pytest.raises(AgentExecutionError, match=code):
        await executor.execute(command(), write_lock_enabled=True)


async def test_document_switch_and_stale_revision_fail_before_program_dispatch():
    adapter = Adapter()
    adapter.health = lambda: _health(
        {
            "active_document_id": "other-doc",
            "active_document_revision": "42",
        }
    )
    with pytest.raises(AgentExecutionError, match="active_document_changed"):
        await ProgramCommandExecutor(Broker(adapter)).execute(
            command(),
            write_lock_enabled=True,
        )
    assert adapter.calls == []


async def _health(payload):
    return SimpleNamespace(ok=True, payload=payload)


async def test_per_document_write_mutex_rejects_parallel_write():
    adapter = Adapter()
    adapter.block = True
    executor = ProgramCommandExecutor(Broker(adapter))
    first = asyncio.create_task(
        executor.execute(command(), write_lock_enabled=True)
    )
    await adapter.started.wait()
    second_command = command().model_copy(
        update={
            "command_id": "command-second",
            "job_id": "job-second",
            "idempotency_key": "idem-second",
        }
    )
    with pytest.raises(AgentExecutionError, match="agent_busy"):
        await executor.execute(second_command, write_lock_enabled=True)
    adapter.release.set()
    await first
