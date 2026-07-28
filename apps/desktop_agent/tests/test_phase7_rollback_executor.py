from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from autocad_desktop_agent.executor import AgentExecutionError
from autocad_desktop_agent.program_executor import (
    RollbackCommandExecutor,
    rollback_command_payload_hash,
)


def command(kind: str = "rollback_preview", **updates):
    arguments = {
        "receipt_lookup": {"receipt_id": "receipt-1"},
        "checkpoint_lookup": {"checkpoint_id": "checkpoint-1"},
        "rollback_preview": {
            "checkpoint_id": "checkpoint-1",
            "checkpoint_digest": f"sha256:{'1' * 64}",
            "rollback_plan_id": "plan-1",
            "rollback_execution_digest": f"sha256:{'2' * 64}",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=2)
            ).isoformat(),
        },
        "rollback_commit": {
            "checkpoint_id": "checkpoint-1",
            "checkpoint_digest": f"sha256:{'1' * 64}",
            "rollback_plan_id": "plan-1",
            "rollback_plan_digest": f"sha256:{'3' * 64}",
            "rollback_execution_digest": f"sha256:{'2' * 64}",
            "rollback_receipt_id": "rollback-receipt-1",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=2)
            ).isoformat(),
        },
        "rollback_validate": {"rollback_receipt_id": "rollback-receipt-1"},
    }[kind]
    value = {
        "session_id": "session-1",
        "device_id": "device-1",
        "job_id": "job-1",
        "command_id": "command-1",
        "idempotency_key": "idempotency-1",
        "payload_hash": "",
        "kind": kind,
        "effect_class": "write" if kind == "rollback_commit" else "read",
        "binding": {
            "program_digest": f"sha256:{'8' * 64}",
            "execution_digest": f"sha256:{'9' * 64}",
            "document_id": "document-1",
            "document_revision": "42",
            "runtime_id": "managed_dotnet",
            "runtime_role": "primary",
            "host_family": "R25",
            "host_version": "0.2.0",
            "package_id": "autocad.managed_host.r25",
            "package_version": "0.2.0",
            "package_hash": f"sha256:{'4' * 64}",
            "capability_manifest_hash": f"sha256:{'5' * 64}",
            "operation_registry_version": "cad.program/0.2",
            "operation_registry_hash": f"sha256:{'6' * 64}",
            "policy_version": "phase6-policy/1",
        },
        "deadline_at": (
            datetime.now(timezone.utc) + timedelta(minutes=1)
        ).isoformat(),
        "arguments": arguments,
    }
    if kind == "rollback_commit":
        value["intent_id"] = "intent-1"
        value["intent_digest"] = f"sha256:{'7' * 64}"
    value.update(updates)
    value["payload_hash"] = rollback_command_payload_hash(value)
    return value


class Adapter:
    def __init__(self):
        self.calls = []

    async def health(self):
        return SimpleNamespace(
            ok=True,
            payload={
                "active_document_id": "document-1",
                "active_document_revision": "42",
            },
            error_code=None,
        )

    async def program_command(self, kind, *, arguments, deadline_at):
        self.calls.append((kind, arguments, deadline_at))
        return SimpleNamespace(
            ok=True,
            payload={"kind": kind, "duplicate": False},
            error_code=None,
        )


class Broker:
    def __init__(self, adapter):
        self.adapter = adapter
        self.calls = []

    async def select_write_runtime(self, binding, **kwargs):
        self.calls.append((binding, kwargs))
        return SimpleNamespace(adapter=self.adapter)


@pytest.mark.parametrize(
    "kind",
    [
        "receipt_lookup",
        "checkpoint_lookup",
        "rollback_preview",
        "rollback_commit",
        "rollback_validate",
    ],
)
async def test_typed_rollback_commands_select_exact_capability(kind):
    adapter = Adapter()
    broker = Broker(adapter)
    result = await RollbackCommandExecutor(broker).execute(
        command(kind),
        write_lock_enabled=True,
    )
    assert result["kind"] == kind
    assert broker.calls[0][1]["required_capability"].startswith("cad.")
    assert broker.calls[0][1]["write_required"] is (kind == "rollback_commit")


async def test_raw_handles_generic_undo_and_payload_tamper_fail_before_host():
    adapter = Adapter()
    executor = RollbackCommandExecutor(Broker(adapter))
    unsafe = command()
    unsafe["arguments"]["entity_handles"] = ["1A"]
    with pytest.raises(ValueError):
        rollback_command_payload_hash(unsafe)

    tampered = command("rollback_commit")
    tampered["arguments"]["rollback_plan_id"] = "other-plan"
    with pytest.raises(AgentExecutionError, match="payload_mismatch"):
        await executor.execute(tampered, write_lock_enabled=True)
    assert adapter.calls == []


async def test_lt_or_fallback_runtime_is_never_selected():
    adapter = Adapter()
    executor = RollbackCommandExecutor(Broker(adapter))
    value = command()
    value["binding"]["runtime_id"] = "autolisp_file_ipc"
    value["payload_hash"] = rollback_command_payload_hash(value)
    with pytest.raises(AgentExecutionError, match="runtime_mismatch"):
        await executor.execute(value, write_lock_enabled=True)
    assert adapter.calls == []
