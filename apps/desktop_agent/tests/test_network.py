from __future__ import annotations

import asyncio
import hashlib
import json
import socket

import pytest
import websockets
from autocad_contracts import (
    AckMessage,
    CommandMessage,
    HelloMessage,
    ReconcileCommandDescriptor,
    ReconcileMessage,
    ReconcileResultMessage,
    ResultMessage,
    WelcomeMessage,
    canonical_payload_hash,
    message_dict,
    parse_agent_message,
)

from autocad_desktop_agent.config import AgentConfig
from autocad_desktop_agent.core import AgentCore
from autocad_desktop_agent.ledger import CommandLedger
from autocad_desktop_agent.state import AgentIntent


class Credentials:
    def load(self):
        return "lab-secret"


class Executor:
    def validate_command(self, command):
        return None

    async def execute(self, command):
        return {"snapshot": {"drawing": {"document_name": "e2e.dwg"}}}

    async def probe(self):
        class Presence:
            runtime_state = "online_idle"
            autocad_state = "Đã kết nối"
            document_name = "e2e.dwg"
        return Presence()


class BlockingExecutor(Executor):
    def __init__(self):
        self.calls = 0

    async def execute(self, command):
        self.calls += 1
        await asyncio.Event().wait()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.asyncio
async def test_real_outbound_websocket_handshake_and_command(tmp_path):
    package_path = tmp_path / "mcp_dispatch.lsp"
    package_path.write_text("phase4-e2e", encoding="utf-8")
    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    port = free_port()
    config = AgentConfig(
        gateway_ws_url=f"ws://127.0.0.1:{port}/agent/ws",
        device_id="device-lab",
        device_name="Máy Lab",
        ledger_path=tmp_path / "agent.db",
        package_path=package_path,
        package_sha256=digest,
        heartbeat_seconds=1,
    )
    core = AgentCore(config, Credentials(), CommandLedger(config.ledger_path), Executor())
    completed = asyncio.Event()

    async def gateway(websocket):
        hello = parse_agent_message(await websocket.recv())
        assert isinstance(hello, HelloMessage)
        assert hello.device_id == "device-lab"
        assert hello.device_proof and hello.device_proof != "lab-secret"
        await websocket.send(json.dumps(message_dict(WelcomeMessage(session_id="session-e2e"))))
        payload = {
            "observation_level": "summary",
            "include_preview_image": False,
            "package": core.package,
        }
        command = CommandMessage(
            session_id="session-e2e",
            device_id="device-lab",
            job_id="job-e2e",
            command_id="command-e2e",
            idempotency_key="idem-e2e",
            payload_hash=canonical_payload_hash(payload),
            payload=payload,
        )
        await websocket.send(json.dumps(message_dict(command)))
        received = []
        while True:
            message = parse_agent_message(await websocket.recv())
            received.append(message)
            if isinstance(message, ResultMessage):
                assert message.status == "succeeded"
                assert message.result["snapshot"]["drawing"]["document_name"] == "e2e.dwg"
                break
        assert [item.message_type for item in received] == ["ack", "result"]
        core.handle_intent(AgentIntent.EXIT)
        completed.set()

    async with websockets.serve(gateway, "127.0.0.1", port):
        runner = asyncio.create_task(core.run_forever())
        await asyncio.wait_for(completed.wait(), timeout=5)
        await asyncio.wait_for(runner, timeout=5)


@pytest.mark.asyncio
async def test_real_websocket_reconnect_reconciles_started_without_reexecution(tmp_path):
    package_path = tmp_path / "mcp_dispatch.lsp"
    package_path.write_text("phase4-reconnect", encoding="utf-8")
    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    port = free_port()
    config = AgentConfig(
        gateway_ws_url=f"ws://127.0.0.1:{port}/agent/ws",
        device_id="device-lab",
        device_name="Máy Lab",
        ledger_path=tmp_path / "agent.db",
        package_path=package_path,
        package_sha256=digest,
        heartbeat_seconds=1,
    )
    executor = BlockingExecutor()
    core = AgentCore(config, Credentials(), CommandLedger(config.ledger_path), executor)
    payload = {
        "observation_level": "summary",
        "include_preview_image": False,
        "package": core.package,
    }
    command = CommandMessage(
        session_id="session-started",
        device_id="device-lab",
        job_id="job-started",
        command_id="command-started",
        idempotency_key="idem-started",
        payload_hash=canonical_payload_hash(payload),
        payload=payload,
    )
    connection_count = 0
    completed = asyncio.Event()

    async def gateway(websocket):
        nonlocal connection_count
        connection_count += 1
        hello = parse_agent_message(await websocket.recv())
        assert isinstance(hello, HelloMessage)
        if connection_count == 1:
            await websocket.send(
                json.dumps(message_dict(WelcomeMessage(session_id="session-started")))
            )
            await websocket.send(json.dumps(message_dict(command)))
            ack = parse_agent_message(await websocket.recv())
            assert isinstance(ack, AckMessage)
            assert ack.status == "accepted"
            await websocket.close(code=1011, reason="failure injection after ack")
            return

        await websocket.send(
            json.dumps(message_dict(WelcomeMessage(session_id="session-reconnect")))
        )
        await websocket.send(
            json.dumps(
                message_dict(
                    ReconcileMessage(
                        session_id="session-reconnect",
                        device_id="device-lab",
                        commands=[
                            ReconcileCommandDescriptor(
                                job_id=command.job_id,
                                command_id=command.command_id,
                                payload_hash=command.payload_hash,
                            )
                        ],
                    )
                )
            )
        )
        reply = parse_agent_message(await websocket.recv())
        assert isinstance(reply, ReconcileResultMessage)
        assert reply.status == "started"
        core.handle_intent(AgentIntent.EXIT)
        completed.set()

    async with websockets.serve(gateway, "127.0.0.1", port):
        runner = asyncio.create_task(core.run_forever())
        await asyncio.wait_for(completed.wait(), timeout=8)
        await asyncio.wait_for(runner, timeout=5)

    assert connection_count == 2
    assert executor.calls == 1
