from __future__ import annotations

import asyncio
import inspect

import pytest
import websockets
from websockets.exceptions import ConnectionClosed

import autocad_phase3_sim_agent
from autocad_contracts import (
    AckMessage,
    CancelMessage,
    CommandMessage,
    HelloMessage,
    ProgressMessage,
    ReconcileCommandDescriptor,
    ReconcileMessage,
    ReconcileResultMessage,
    ResultMessage,
    WelcomeMessage,
    canonical_json,
    canonical_payload_hash,
    message_dict,
    parse_agent_message,
)
from autocad_phase3_sim_agent.agent import LedgerEntry, SimulatedAgent
from autocad_phase3_sim_agent.scenarios import SCENARIOS, validate_scenario


class RecordingWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.closed = False

    async def send(self, value: str) -> None:
        self.messages.append(value)

    async def close(self, **_: object) -> None:
        self.closed = True


def _command(session_id: str = "session-a", *, command_id: str = "command-a") -> CommandMessage:
    payload = {"observation_level": "summary", "include_preview_image": False}
    return CommandMessage(
        session_id=session_id,
        device_id="device-a",
        job_id="job-a",
        command_id=command_id,
        idempotency_key="request-a",
        payload_hash=canonical_payload_hash(payload),
        payload=payload,
    )


async def _send(websocket: object, message: object) -> None:
    await websocket.send(canonical_json(message_dict(message)))


async def _recv(websocket: object):
    return parse_agent_message(await websocket.recv())


def test_scenarios_are_explicit_and_agent_has_no_gateway_import():
    assert validate_scenario("success") == "success"
    assert "autocad_gateway" not in inspect.getsource(SimulatedAgent)
    assert "autocad_mcp" not in inspect.getsource(SimulatedAgent)
    assert SCENARIOS == autocad_phase3_sim_agent.SCENARIOS
    assert {
        "success",
        "drop_before_ack",
        "drop_after_ack_before_start",
        "drop_after_start_before_result",
        "reconnect_not_started",
        "reconnect_started",
        "reconnect_terminal",
        "duplicate_ack",
        "duplicate_progress",
        "duplicate_result",
        "out_of_order_progress",
        "payload_hash_mismatch",
        "stale_heartbeat",
        "cancel_before_start",
        "cancel_while_running",
        "cancel_too_late",
        "delay_before_ack",
        "delay_result",
    } == SCENARIOS


def test_snapshot_revision_uses_full_geometry_and_snapshot_identity_is_fresh():
    agent = SimulatedAgent("ws://invalid", "device-a", "token-a")
    command = _command()
    first = agent._result_payload(command)["snapshot"]
    second = agent._result_payload(command)["snapshot"]
    assert first["snapshot_id"] != second["snapshot_id"]
    assert first["document_revision"] == second["document_revision"]
    assert all(entity["geometry"] == {} for entity in first["entities"])

    agent.set_fixture_variant(3)
    changed = agent._result_payload(command)["snapshot"]
    assert changed["document_revision"] != first["document_revision"]

    detailed = agent._result_payload(
        command.model_copy(update={"payload": {"observation_level": "detail", "include_preview_image": False}})
    )["snapshot"]
    assert any(entity["geometry"] for entity in detailed["entities"])
    assert detailed["document_revision"] == changed["document_revision"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "ack_count", "progress_count", "result_count"),
    [
        ("duplicate_ack", 2, 2, 1),
        ("duplicate_progress", 1, 3, 1),
        ("duplicate_result", 1, 2, 2),
        ("out_of_order_progress", 1, 3, 1),
    ],
)
async def test_duplicate_and_order_scenarios_execute_effect_once(
    scenario: str,
    ack_count: int,
    progress_count: int,
    result_count: int,
):
    agent = SimulatedAgent("ws://invalid", "device-a", "token-a", scenario=scenario)
    websocket = RecordingWebSocket()
    task = await agent._accept_command(websocket, _command())
    assert task is not None
    await task
    messages = [parse_agent_message(value) for value in websocket.messages]
    assert sum(isinstance(item, AckMessage) for item in messages) == ack_count
    assert sum(isinstance(item, ProgressMessage) for item in messages) == progress_count
    assert sum(isinstance(item, ResultMessage) for item in messages) == result_count
    assert agent.execution_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["success", "delay_before_ack", "delay_result"])
async def test_success_and_delay_scenarios_reach_one_terminal_effect(scenario: str):
    agent = SimulatedAgent("ws://invalid", "device-a", "token-a", scenario=scenario)
    websocket = RecordingWebSocket()
    task = await agent._accept_command(websocket, _command())
    assert task is not None
    await task
    assert agent.execution_count == 1
    assert next(iter(agent.ledger.values())).result_status == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_types"),
    [
        ("success", (AckMessage, ProgressMessage, ProgressMessage, ResultMessage)),
        ("delay_before_ack", (AckMessage, ProgressMessage, ProgressMessage, ResultMessage)),
        ("delay_result", (AckMessage, ProgressMessage, ProgressMessage, ResultMessage)),
        (
            "duplicate_ack",
            (AckMessage, AckMessage, ProgressMessage, ProgressMessage, ResultMessage),
        ),
        (
            "duplicate_progress",
            (AckMessage, ProgressMessage, ProgressMessage, ProgressMessage, ResultMessage),
        ),
        (
            "duplicate_result",
            (AckMessage, ProgressMessage, ProgressMessage, ResultMessage, ResultMessage),
        ),
        (
            "out_of_order_progress",
            (AckMessage, ProgressMessage, ProgressMessage, ProgressMessage, ResultMessage),
        ),
        (
            "payload_hash_mismatch",
            (AckMessage, ProgressMessage, ProgressMessage, ResultMessage),
        ),
    ],
)
async def test_failure_matrix_runs_over_real_websocket_with_one_effect(
    scenario: str,
    expected_types: tuple[type, ...],
):
    received: list[object] = []

    async def handler(websocket):
        hello = await _recv(websocket)
        assert isinstance(hello, HelloMessage)
        await _send(
            websocket,
            WelcomeMessage(session_id="session-matrix", heartbeat_interval_seconds=300),
        )
        await _send(websocket, _command("session-matrix"))
        while len(received) < len(expected_types):
            received.append(await _recv(websocket))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        agent = SimulatedAgent(
            f"ws://127.0.0.1:{port}",
            "device-a",
            "token-a",
            scenario=scenario,
        )
        await asyncio.wait_for(agent.run(stop_after_terminal=True), timeout=5)

    assert tuple(type(message) for message in received) == expected_types
    assert agent.execution_count == 1
    assert next(iter(agent.ledger.values())).result_status == "succeeded"
    if scenario == "out_of_order_progress":
        assert received[2].sequence < received[1].sequence
    if scenario == "payload_hash_mismatch":
        assert received[0].payload_hash == "0" * 64


@pytest.mark.asyncio
async def test_payload_hash_mismatch_scenario_emits_bounded_wrong_hash():
    agent = SimulatedAgent(
        "ws://invalid",
        "device-a",
        "token-a",
        scenario="payload_hash_mismatch",
    )
    websocket = RecordingWebSocket()
    task = await agent._accept_command(websocket, _command())
    assert task is not None
    await task
    first = parse_agent_message(websocket.messages[0])
    assert isinstance(first, AckMessage)
    assert first.payload_hash == "0" * 64


@pytest.mark.asyncio
async def test_cancel_while_running_is_consumed_concurrently_and_terminal_is_immutable():
    agent = SimulatedAgent(
        "ws://invalid",
        "device-a",
        "token-a",
        scenario="cancel_while_running",
    )
    websocket = RecordingWebSocket()
    command = _command()
    task = await agent._accept_command(websocket, command)
    assert task is not None
    for _ in range(100):
        if agent.ledger[command.command_id].status == "started":
            break
        await asyncio.sleep(0)
    await agent._handle_cancel(
        websocket,
        CancelMessage(
            session_id=command.session_id,
            device_id=command.device_id,
            job_id=command.job_id,
            command_id=command.command_id,
        ),
    )
    await task
    results = [
        item
        for item in (parse_agent_message(value) for value in websocket.messages)
        if isinstance(item, ResultMessage)
    ]
    assert [item.status for item in results] == ["cancelled"]
    assert agent.ledger[command.command_id].status == "terminal"
    assert agent.ledger[command.command_id].result_status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [("cancel_before_start", "cancelled"), ("cancel_too_late", "succeeded")],
)
async def test_cancel_before_start_and_too_late_have_deterministic_winner(
    scenario: str,
    expected_status: str,
):
    agent = SimulatedAgent("ws://invalid", "device-a", "token-a", scenario=scenario)
    websocket = RecordingWebSocket()
    command = _command()
    task = await agent._accept_command(websocket, command)
    assert task is not None
    await task
    await agent._handle_cancel(
        websocket,
        CancelMessage(
            session_id=command.session_id,
            device_id=command.device_id,
            job_id=command.job_id,
            command_id=command.command_id,
        ),
    )
    assert agent.ledger[command.command_id].result_status == expected_status
    last = parse_agent_message(websocket.messages[-1])
    assert isinstance(last, ResultMessage)
    assert last.status == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "cancel_after_progress", "expected_status"),
    [
        ("cancel_before_start", False, "cancelled"),
        ("cancel_while_running", True, "cancelled"),
        ("cancel_too_late", True, "succeeded"),
    ],
)
async def test_cancel_matrix_runs_over_real_websocket_with_deterministic_winner(
    scenario: str,
    cancel_after_progress: bool,
    expected_status: str,
):
    terminal_results: list[ResultMessage] = []
    agent_holder: dict[str, SimulatedAgent] = {}

    async def handler(websocket):
        hello = await _recv(websocket)
        assert isinstance(hello, HelloMessage)
        session_id = "session-cancel-matrix"
        await _send(
            websocket,
            WelcomeMessage(session_id=session_id, heartbeat_interval_seconds=300),
        )
        command = _command(session_id)
        await _send(websocket, command)
        assert isinstance(await _recv(websocket), AckMessage)
        if cancel_after_progress:
            assert isinstance(await _recv(websocket), ProgressMessage)
        if scenario == "cancel_too_late":
            while True:
                message = await _recv(websocket)
                if isinstance(message, ResultMessage):
                    terminal_results.append(message)
                    break
        await _send(
            websocket,
            CancelMessage(
                session_id=session_id,
                device_id="device-a",
                job_id=command.job_id,
                command_id=command.command_id,
            ),
        )
        while True:
            message = await _recv(websocket)
            if isinstance(message, ResultMessage):
                terminal_results.append(message)
                break
        agent_holder["value"].stop()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        agent = SimulatedAgent(
            f"ws://127.0.0.1:{port}",
            "device-a",
            "token-a",
            scenario=scenario,
        )
        agent_holder["value"] = agent
        await asyncio.wait_for(agent.run(), timeout=5)

    assert [result.status for result in terminal_results] == (
        ["succeeded", "succeeded"]
        if scenario == "cancel_too_late"
        else [expected_status]
    )
    assert agent.execution_count == (0 if scenario == "cancel_before_start" else 1)
    assert next(iter(agent.ledger.values())).result_status == expected_status


@pytest.mark.asyncio
async def test_terminal_cancelled_ledger_reconciles_with_evidence():
    agent = SimulatedAgent("ws://invalid", "device-a", "token-a")
    websocket = RecordingWebSocket()
    command = _command()
    agent.ledger[command.command_id] = LedgerEntry(
        command_id=command.command_id,
        job_id=command.job_id,
        idempotency_key=command.idempotency_key,
        payload_hash=command.payload_hash,
        status="terminal",
        result_status="cancelled",
    )
    await agent._handle_reconcile(
        websocket,
        ReconcileMessage(
            session_id=command.session_id,
            device_id=command.device_id,
            commands=[
                ReconcileCommandDescriptor(
                    job_id=command.job_id,
                    command_id=command.command_id,
                    payload_hash=command.payload_hash,
                )
            ],
        ),
    )
    reply = parse_agent_message(websocket.messages[-1])
    assert isinstance(reply, ReconcileResultMessage)
    assert reply.status == "terminal"
    assert reply.result_status == "cancelled"


@pytest.mark.asyncio
async def test_terminal_cancelled_reconciles_over_real_websocket():
    command = _command()
    replies: list[ReconcileResultMessage] = []

    async def handler(websocket):
        hello = await _recv(websocket)
        assert isinstance(hello, HelloMessage)
        await _send(
            websocket,
            WelcomeMessage(session_id="session-cancelled", heartbeat_interval_seconds=300),
        )
        await _send(
            websocket,
            ReconcileMessage(
                session_id="session-cancelled",
                device_id="device-a",
                commands=[
                    ReconcileCommandDescriptor(
                        job_id=command.job_id,
                        command_id=command.command_id,
                        payload_hash=command.payload_hash,
                    )
                ],
            ),
        )
        reply = await _recv(websocket)
        assert isinstance(reply, ReconcileResultMessage)
        replies.append(reply)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        agent = SimulatedAgent(f"ws://127.0.0.1:{port}", "device-a", "token-a")
        agent.ledger[command.command_id] = LedgerEntry(
            command_id=command.command_id,
            job_id=command.job_id,
            idempotency_key=command.idempotency_key,
            payload_hash=command.payload_hash,
            status="terminal",
            result_status="cancelled",
        )
        await asyncio.wait_for(agent.run(stop_after_terminal=True), timeout=3)

    assert replies[0].status == "terminal"
    assert replies[0].result_status == "cancelled"
    assert agent.execution_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_last_sequence"),
    [
        ("drop_before_ack", 0),
        ("drop_after_ack_before_start", 1),
        ("reconnect_not_started", 1),
    ],
)
async def test_real_reconnect_preserves_not_started_ledger_and_redispatches_once(
    scenario: str,
    expected_last_sequence: int,
):
    hellos: list[HelloMessage] = []
    reconciled: list[ReconcileResultMessage] = []
    terminal_results: list[ResultMessage] = []
    command_holder: dict[str, CommandMessage] = {}

    async def handler(websocket):
        hello = await _recv(websocket)
        assert isinstance(hello, HelloMessage)
        hellos.append(hello)
        session_id = f"session-{len(hellos)}"
        await _send(
            websocket,
            WelcomeMessage(
                session_id=session_id,
                heartbeat_interval_seconds=300,
            ),
        )
        if len(hellos) == 1:
            command = _command(session_id)
            command_holder["value"] = command
            await _send(websocket, command)
            if scenario != "drop_before_ack":
                ack = await _recv(websocket)
                assert isinstance(ack, AckMessage)
            await websocket.wait_closed()
            return

        original = command_holder["value"]
        descriptor = ReconcileCommandDescriptor(
            job_id=original.job_id,
            command_id=original.command_id,
            payload_hash=original.payload_hash,
        )
        await _send(
            websocket,
            ReconcileMessage(
                session_id=session_id,
                device_id="device-a",
                commands=[descriptor],
            ),
        )
        reply = await _recv(websocket)
        assert isinstance(reply, ReconcileResultMessage)
        reconciled.append(reply)
        await _send(websocket, original.model_copy(update={"session_id": session_id}))
        while True:
            message = await _recv(websocket)
            if isinstance(message, ResultMessage):
                terminal_results.append(message)
                return

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        agent = SimulatedAgent(
            f"ws://127.0.0.1:{port}",
            "device-a",
            "token-a",
            scenario=scenario,
        )
        await asyncio.wait_for(agent.run(stop_after_terminal=True), timeout=5)

    assert agent.session_count == 2
    assert agent.execution_count == 1
    assert hellos[1].last_processed_sequence == expected_last_sequence
    assert reconciled[0].status == "not_started"
    assert reconciled[0].payload_hash == command_holder["value"].payload_hash
    assert terminal_results[0].status == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    ["drop_after_start_before_result", "reconnect_started"],
)
async def test_real_reconnect_started_never_reexecutes_effect(scenario: str):
    hellos: list[HelloMessage] = []
    reconciled: list[ReconcileResultMessage] = []
    command_holder: dict[str, CommandMessage] = {}
    agent_holder: dict[str, SimulatedAgent] = {}

    async def handler(websocket):
        hello = await _recv(websocket)
        assert isinstance(hello, HelloMessage)
        hellos.append(hello)
        session_id = f"session-{len(hellos)}"
        await _send(
            websocket,
            WelcomeMessage(session_id=session_id, heartbeat_interval_seconds=300),
        )
        if len(hellos) == 1:
            command = _command(session_id)
            command_holder["value"] = command
            await _send(websocket, command)
            assert isinstance(await _recv(websocket), AckMessage)
            assert isinstance(await _recv(websocket), ProgressMessage)
            await websocket.wait_closed()
            return
        original = command_holder["value"]
        await _send(
            websocket,
            ReconcileMessage(
                session_id=session_id,
                device_id="device-a",
                commands=[
                    ReconcileCommandDescriptor(
                        job_id=original.job_id,
                        command_id=original.command_id,
                        payload_hash=original.payload_hash,
                    )
                ],
            ),
        )
        reply = await _recv(websocket)
        assert isinstance(reply, ReconcileResultMessage)
        reconciled.append(reply)
        agent_holder["value"].stop()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        agent = SimulatedAgent(
            f"ws://127.0.0.1:{port}",
            "device-a",
            "token-a",
            scenario=scenario,
        )
        agent_holder["value"] = agent
        await asyncio.wait_for(agent.run(), timeout=5)

    assert agent.session_count == 2
    assert agent.execution_count == 1
    assert reconciled[0].status == "started"


@pytest.mark.asyncio
async def test_real_reconnect_reports_terminal_ledger_without_second_effect():
    hellos: list[HelloMessage] = []
    terminal_reconcile: list[ReconcileResultMessage] = []
    command_holder: dict[str, CommandMessage] = {}

    async def handler(websocket):
        hello = await _recv(websocket)
        assert isinstance(hello, HelloMessage)
        hellos.append(hello)
        session_id = f"session-{len(hellos)}"
        await _send(
            websocket,
            WelcomeMessage(session_id=session_id, heartbeat_interval_seconds=300),
        )
        if len(hellos) == 1:
            command = _command(session_id)
            command_holder["value"] = command
            await _send(websocket, command)
            try:
                while True:
                    await _recv(websocket)
            except ConnectionClosed:
                return

        original = command_holder["value"]
        await _send(
            websocket,
            ReconcileMessage(
                session_id=session_id,
                device_id="device-a",
                commands=[
                    ReconcileCommandDescriptor(
                        job_id=original.job_id,
                        command_id=original.command_id,
                        payload_hash=original.payload_hash,
                    )
                ],
            ),
        )
        reply = await _recv(websocket)
        assert isinstance(reply, ReconcileResultMessage)
        terminal_reconcile.append(reply)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        agent = SimulatedAgent(
            f"ws://127.0.0.1:{port}",
            "device-a",
            "token-a",
            scenario="reconnect_terminal",
        )
        await asyncio.wait_for(agent.run(stop_after_terminal=True), timeout=5)

    assert agent.session_count == 2
    assert agent.execution_count == 1
    assert terminal_reconcile[0].status == "terminal"
    assert terminal_reconcile[0].result_status == "succeeded"


@pytest.mark.asyncio
async def test_stale_heartbeat_scenario_keeps_socket_but_sends_no_heartbeat():
    connected = asyncio.Event()
    agent_holder: dict[str, SimulatedAgent] = {}

    async def handler(websocket):
        hello = await _recv(websocket)
        assert isinstance(hello, HelloMessage)
        await _send(
            websocket,
            WelcomeMessage(session_id="session-stale", heartbeat_interval_seconds=1),
        )
        connected.set()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(websocket.recv(), timeout=1.1)
        agent_holder["value"].stop()
        await websocket.wait_closed()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        agent = SimulatedAgent(
            f"ws://127.0.0.1:{port}",
            "device-a",
            "token-a",
            scenario="stale_heartbeat",
        )
        agent_holder["value"] = agent
        task = asyncio.create_task(agent.run())
        await asyncio.wait_for(connected.wait(), timeout=2)
        await asyncio.wait_for(task, timeout=3)
