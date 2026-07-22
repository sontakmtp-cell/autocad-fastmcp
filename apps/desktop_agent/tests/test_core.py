from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest
from autocad_contracts import (
    CommandMessage,
    ReconcileCommandDescriptor,
    ReconcileMessage,
    ReconcileResultMessage,
    canonical_payload_hash,
    parse_agent_message,
)

from autocad_desktop_agent.config import AgentConfig
from autocad_desktop_agent.core import AgentCore
from autocad_desktop_agent.ledger import CommandLedger
from autocad_desktop_agent.state import AgentIntent, RuntimeState


class Credentials:
    def load(self):
        return "secret"


class Executor:
    def __init__(self):
        self.calls = 0

    def validate_command(self, command):
        return None

    async def execute(self, command):
        self.calls += 1
        return {
            "snapshot": {
                "drawing": {"document_name": "demo.dwg"},
            }
        }

    async def probe(self):
        class Presence:
            runtime_state = "online_idle"
            autocad_state = "Đã kết nối"
            document_name = "demo.dwg"
        return Presence()


class Socket:
    def __init__(self):
        self.messages = []

    async def send(self, value):
        self.messages.append(parse_agent_message(value))


def make_core(tmp_path):
    package_path = tmp_path / "mcp_dispatch.lsp"
    package_path.write_text("phase4", encoding="utf-8")
    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    config = AgentConfig(
        gateway_ws_url="ws://127.0.0.1/agent/ws",
        device_id="device-1",
        device_name="Máy Lab",
        ledger_path=tmp_path / "agent.db",
        package_path=package_path,
        package_sha256=digest,
    )
    executor = Executor()
    core = AgentCore(config, Credentials(), CommandLedger(config.ledger_path), executor)
    core._session_id = "session-1"
    return core, executor


def make_command(core):
    payload = {"observation_level": "summary", "include_preview_image": False, "package": core.package}
    return CommandMessage(
        session_id="session-1",
        device_id="device-1",
        job_id="job-1",
        command_id="command-1",
        idempotency_key="idem-1",
        payload_hash=canonical_payload_hash(payload),
        payload=payload,
    )


@pytest.mark.asyncio
async def test_terminal_is_persisted_and_duplicate_is_not_executed(tmp_path):
    core, executor = make_core(tmp_path)
    socket = Socket()
    command = make_command(core)
    await core._handle_command(socket, command)
    assert core.ledger.get("command-1").state == "succeeded"
    assert executor.calls == 1
    await core._handle_command(socket, command)
    assert executor.calls == 1
    assert [item.message_type for item in socket.messages] == [
        "ack", "result", "ack", "result"
    ]


@pytest.mark.asyncio
async def test_hard_pause_rejects_before_executor_and_persists(tmp_path):
    core, executor = make_core(tmp_path)
    core.handle_intent(AgentIntent.PAUSE)
    assert core.view_state.runtime_state == RuntimeState.PAUSED
    await core._handle_command(Socket(), make_command(core))
    assert executor.calls == 0
    reopened = CommandLedger(core.config.ledger_path)
    assert reopened.is_paused() is True


def test_diagnostics_is_allowlist_only(tmp_path):
    core, _ = make_core(tmp_path)
    target = tmp_path / "diagnostics.json"
    core._last_ids = {"job_id": "job-1", "token": "must-not-leak", "full_path": r"C:\secret.dwg"}
    core.handle_intent(AgentIntent.EXPORT_DIAGNOSTICS, target)
    text = target.read_text(encoding="utf-8")
    assert "job-1" in text
    assert "must-not-leak" not in text
    assert "secret.dwg" not in text


def test_package_mismatch_is_visible_and_fail_closed(tmp_path):
    core, _ = make_core(tmp_path)
    core.config.package_path.write_text("tampered", encoding="utf-8")
    assert core._refresh_package() is False
    core._publish(runtime_state=RuntimeState.INCOMPATIBLE, support_code="C1-PKG-001")
    assert core.view_state.runtime_state == RuntimeState.INCOMPATIBLE
    assert core.view_state.support_code == "C1-PKG-001"


@pytest.mark.asyncio
async def test_normal_exit_closes_ledger(tmp_path):
    core, _ = make_core(tmp_path)
    core.handle_intent(AgentIntent.EXIT)
    await core.run_forever()
    with pytest.raises(sqlite3.ProgrammingError):
        core.ledger.last_sequence()


@pytest.mark.asyncio
async def test_restart_reconciles_not_started_started_and_terminal_without_reexecution(tmp_path):
    core, executor = make_core(tmp_path)
    base = make_command(core)
    started = base.model_copy(
        update={
            "job_id": "job-started",
            "command_id": "command-started",
            "idempotency_key": "idem-started",
        }
    )
    terminal = base.model_copy(
        update={
            "job_id": "job-terminal",
            "command_id": "command-terminal",
            "idempotency_key": "idem-terminal",
        }
    )
    for command in (started, terminal):
        core.ledger.record_received(
            command_id=command.command_id,
            job_id=command.job_id,
            idempotency_key=command.idempotency_key,
            payload_hash=command.payload_hash,
            package=core.package,
            session_id=command.session_id,
            device_id=command.device_id,
        )
        core.ledger.transition(command.command_id, "accepted")
        core.ledger.transition(command.command_id, "started")
    terminal_result = {"snapshot": {"drawing": {"document_name": "demo.dwg"}}}
    core.ledger.transition(terminal.command_id, "succeeded", result=terminal_result)
    core.ledger.close()

    restarted, restarted_executor = make_core(tmp_path)
    socket = Socket()
    missing = base.model_copy(
        update={
            "job_id": "job-missing",
            "command_id": "command-missing",
            "idempotency_key": "idem-missing",
        }
    )
    await restarted._handle_reconcile(
        socket,
        ReconcileMessage(
            session_id="session-1",
            device_id="device-1",
            commands=[
                ReconcileCommandDescriptor(
                    job_id=command.job_id,
                    command_id=command.command_id,
                    payload_hash=command.payload_hash,
                )
                for command in (missing, started, terminal)
            ],
        ),
    )

    replies = socket.messages
    assert all(isinstance(reply, ReconcileResultMessage) for reply in replies)
    assert [reply.status for reply in replies] == ["not_started", "started", "terminal"]
    assert replies[-1].result_status == "succeeded"
    assert replies[-1].result == terminal_result
    assert executor.calls == restarted_executor.calls == 0
