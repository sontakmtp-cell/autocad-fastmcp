from __future__ import annotations

import asyncio
import json
import re
import socket
from datetime import timedelta
from typing import Any

import httpx
import pytest
import uvicorn
import websockets
from fastmcp import Client

from autocad_contracts import (
    AckMessage,
    HelloMessage,
    ReconcileResultMessage,
    ResultMessage,
    WelcomeMessage,
    canonical_capability_hash,
    parse_agent_message,
)
from autocad_gateway.app import GatewayConfig, build_mcp_server, create_app
from autocad_gateway.contracts import CadObserveInputDurable, Principal
from autocad_gateway.durable_services import DurableGatewayServices
from autocad_gateway.infrastructure.agent_transport.connection_registry import (
    AgentConnection,
    ConnectionRegistry,
)
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.services import GatewayError


OWNER = "phase3-fixture-user"
PRINCIPAL = Principal(subject=OWNER, scopes=("autocad.read",))


class RecordingWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.sent = asyncio.Event()

    async def send_json(self, value: dict[str, Any]) -> None:
        self.messages.append(value)
        self.sent.set()

    async def close(self, **kwargs: Any) -> None:
        del kwargs


async def _connected_services(
    tmp_path: Any,
    *,
    request_wait_timeout_seconds: float = 1.0,
    capabilities: tuple[str, ...] = ("observe", "query"),
    maintenance_interval_seconds: float | None = None,
) -> tuple[DurableGatewayServices, AgentConnection, RecordingWebSocket]:
    registry = ConnectionRegistry()
    services = DurableGatewayServices(
        SqliteDatabase(tmp_path / "phase31-application.db"),
        registry,
        device_tokens={"device-a": "token-a"},
        request_wait_timeout_seconds=request_wait_timeout_seconds,
        maintenance_interval_seconds=maintenance_interval_seconds,
    )
    await services.initialize()
    websocket = RecordingWebSocket()
    connection = AgentConnection(
        "device-a",
        "session-a",
        websocket,
        "cad.agent/1",
        capabilities=capabilities,
        capability_hash=canonical_capability_hash(capabilities),
    )
    await registry.add(connection)
    await services.on_agent_connected(connection)
    return services, connection, websocket


async def _command_at(websocket: RecordingWebSocket, index: int) -> dict[str, Any]:
    for _ in range(200):
        commands = [
            message
            for message in websocket.messages
            if message.get("message_type") == "command"
        ]
        if len(commands) > index:
            return commands[index]
        websocket.sent.clear()
        try:
            await asyncio.wait_for(websocket.sent.wait(), timeout=0.05)
        except asyncio.TimeoutError:
            pass
    raise AssertionError(f"command {index} was not dispatched")


def _snapshot(marker: str) -> dict[str, Any]:
    return {
        "snapshot_id": f"snapshot-{marker}",
        "document_revision": f"revision-{marker}",
        "observation_level": "summary",
        "drawing": {"name": marker, "entity_count": 1, "layers": ["0"]},
        "entity_summary": {"LINE": 1},
        "entities": [
            {
                "entity_id": f"line-{marker}",
                "entity_type": "Line",
                "layer": "0",
                "geometry": {"start": [0, 0], "end": [1, 0]},
            }
        ],
    }


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _complete_observe(
    services: DurableGatewayServices,
    connection: AgentConnection,
    command: dict[str, Any],
    marker: str,
    *,
    sequence: int,
) -> None:
    await services.job_service.handle_message(
        connection,
        AckMessage(
            session_id=connection.session_id,
            device_id=connection.device_id,
            job_id=command["job_id"],
            command_id=command["command_id"],
            sequence=sequence,
            status="accepted",
            idempotency_key=command["idempotency_key"],
            payload_hash=command["payload_hash"],
        ),
    )
    await services.job_service.handle_message(
        connection,
        ResultMessage(
            session_id=connection.session_id,
            device_id=connection.device_id,
            job_id=command["job_id"],
            command_id=command["command_id"],
            sequence=sequence + 1,
            status="succeeded",
            payload_hash=command["payload_hash"],
            result={"snapshot": _snapshot(marker)},
        ),
    )


@pytest.mark.asyncio
async def test_fresh_observe_does_not_reuse_terminal_job_after_fixture_changes(tmp_path):
    services, connection, websocket = await _connected_services(tmp_path)
    request = CadObserveInputDurable(device_id="device-a")
    try:
        first_task = asyncio.create_task(services.observe(request, PRINCIPAL, "corr-a"))
        first_command = await _command_at(websocket, 0)
        await _complete_observe(services, connection, first_command, "a", sequence=1)
        first = await first_task

        second_task = asyncio.create_task(services.observe(request, PRINCIPAL, "corr-b"))
        second_command = await _command_at(websocket, 1)
        await _complete_observe(services, connection, second_command, "b", sequence=3)
        second = await second_task

        assert first.job_id != second.job_id
        assert first.snapshot_id == "snapshot-a"
        assert second.snapshot_id == "snapshot-b"
        assert first.document_revision == "revision-a"
        assert second.document_revision == "revision-b"
        with services.database.read_connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
    finally:
        await services.shutdown()


@pytest.mark.asyncio
async def test_concurrent_explicit_retry_shares_waiter_and_dispatches_once(tmp_path):
    services, connection, websocket = await _connected_services(tmp_path)
    request = CadObserveInputDurable(
        device_id="device-a", idempotency_key="request-identity-1"
    )
    try:
        first_task = asyncio.create_task(services.observe(request, PRINCIPAL, "corr-a"))
        second_task = asyncio.create_task(services.observe(request, PRINCIPAL, "corr-b"))
        command = await _command_at(websocket, 0)
        await asyncio.sleep(0)
        assert sum(
            message.get("message_type") == "command"
            for message in websocket.messages
        ) == 1

        await _complete_observe(services, connection, command, "shared", sequence=1)
        first, second = await asyncio.gather(first_task, second_task)
        assert first.job_id == second.job_id == command["job_id"]
        assert first.snapshot_id == second.snapshot_id == "snapshot-shared"
        assert services.job_service._waiters == {}
        with services.database.read_connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    finally:
        await services.shutdown()


@pytest.mark.asyncio
async def test_mcp_wait_timeout_exposes_job_id_and_late_result_succeeds(tmp_path):
    services, connection, websocket = await _connected_services(
        tmp_path, request_wait_timeout_seconds=0.01
    )
    server = build_mcp_server(services, correlation_id_factory=lambda: "corr-timeout")
    try:
        async with Client(server) as client:
            pending = await client.call_tool(
                "cad_observe", {"device_id": "device-a"}, raise_on_error=False
            )
            assert pending.is_error
            text = pending.content[0].text
            assert "job_in_progress" in text
            assert "job_state=dispatched" in text
            match = re.search(r"job_id=([^;]+)", text)
            assert match is not None
            job_id = match.group(1)

            before = await services.repository.get_job(OWNER, job_id)
            assert before is not None and before["state"] == "dispatched"
            assert before["error_code"] is None

            command = await _command_at(websocket, 0)
            assert command["job_id"] == job_id
            await _complete_observe(services, connection, command, "late", sequence=1)

            completed = await client.call_tool("cad_get_job", {"job_id": job_id})
            assert completed.structured_content["state"] == "succeeded"
            assert completed.structured_content["snapshot_id"] == "snapshot-late"
            with services.database.read_connection() as conn:
                assert conn.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE job_id = ?", (job_id,)
                ).fetchone()[0] == 1
    finally:
        await services.shutdown()


@pytest.mark.asyncio
async def test_cancel_intent_survives_reconnect_without_redispatch(tmp_path):
    services, connection, websocket = await _connected_services(tmp_path)
    try:
        job = await services.repository.create_job(
            owner_subject=OWNER,
            device_id="device-a",
            kind="observe",
            effect_class="read",
            payload={},
            idempotency_key="cancel-reconnect",
            deadline_at=None,
        )
        await services.job_service.dispatch(job["job_id"], correlation_id="corr")
        await services.job_service.handle_disconnect("device-a")
        await services.job_service.cancel(
            job["job_id"], owner_subject=OWNER, reason="test cancellation"
        )
        recovering = await services.repository.get_job(OWNER, job["job_id"])
        assert recovering["state"] == "reconnect_pending"
        assert recovering["cancel_requested_at"] is not None
        command_count = sum(
            message.get("message_type") == "command"
            for message in websocket.messages
        )

        await services.job_service.handle_reconcile_result(
            connection,
            ReconcileResultMessage(
                session_id=connection.session_id,
                device_id=connection.device_id,
                job_id=job["job_id"],
                command_id=job["command_id"],
                sequence=1,
                status="not_started",
                payload_hash=job["payload_hash"],
            ),
        )
        cancelled = await services.repository.get_job(OWNER, job["job_id"])
        assert cancelled["state"] == "cancelled"
        assert sum(
            message.get("message_type") == "command"
            for message in websocket.messages
        ) == command_count
    finally:
        await services.shutdown()


@pytest.mark.asyncio
async def test_cancel_wins_reconcile_not_started_cas_race_without_redispatch(
    tmp_path, monkeypatch
):
    services, connection, websocket = await _connected_services(tmp_path)
    try:
        job = await services.repository.create_job(
            owner_subject=OWNER,
            device_id="device-a",
            kind="observe",
            effect_class="read",
            payload={},
            idempotency_key="cancel-reconcile-race",
            deadline_at=None,
        )
        await services.job_service.dispatch(job["job_id"], correlation_id="corr")
        await services.job_service.handle_disconnect("device-a")
        command_count = sum(
            message.get("message_type") == "command"
            for message in websocket.messages
        )
        original_transition = services.repository.transition_job
        injected = False

        async def transition_with_cancel_race(*args, **kwargs):
            nonlocal injected
            if kwargs.get("expected_version") is not None and not injected:
                injected = True
                await services.repository.request_job_cancel(job["job_id"])
            return await original_transition(*args, **kwargs)

        monkeypatch.setattr(
            services.repository, "transition_job", transition_with_cancel_race
        )
        await services.job_service.handle_reconcile_result(
            connection,
            ReconcileResultMessage(
                session_id=connection.session_id,
                device_id=connection.device_id,
                job_id=job["job_id"],
                command_id=job["command_id"],
                sequence=1,
                status="not_started",
                payload_hash=job["payload_hash"],
            ),
        )
        assert injected is True
        assert (await services.repository.get_job(OWNER, job["job_id"]))[
            "state"
        ] == "cancelled"
        assert sum(
            message.get("message_type") == "command"
            for message in websocket.messages
        ) == command_count
    finally:
        await services.shutdown()


@pytest.mark.asyncio
async def test_offline_cancel_keeps_unknown_write_and_never_redispatches(tmp_path):
    services, connection, websocket = await _connected_services(tmp_path)
    try:
        job = await services.repository.create_job(
            owner_subject=OWNER,
            device_id="device-a",
            kind="write_fixture",
            effect_class="write",
            payload={"fixture": "write-like"},
            idempotency_key="offline-unknown-cancel",
            deadline_at=None,
        )
        await services.job_service.dispatch(job["job_id"], correlation_id="corr")
        await services.repository.transition_job(job["job_id"], "acknowledged")
        await services.repository.transition_job(job["job_id"], "running")
        await services.job_service.handle_disconnect("device-a")
        await services.registry.remove(connection.device_id, connection.session_id)

        requested = await services.job_service.cancel(
            job["job_id"], owner_subject=OWNER, reason="offline cancellation"
        )
        assert requested["state"] == "outcome_unknown"
        assert requested["cancel_requested_at"] is not None
        command_count = sum(
            message.get("message_type") == "command"
            for message in websocket.messages
        )

        replacement_socket = RecordingWebSocket()
        replacement = AgentConnection(
            "device-a",
            "session-b",
            replacement_socket,
            "cad.agent/1",
            capabilities=("observe", "query"),
            capability_hash=canonical_capability_hash(("observe", "query")),
        )
        await services.registry.add(replacement)
        await services.on_agent_connected(replacement)
        assert [
            message["message_type"] for message in replacement_socket.messages
        ] == ["reconcile"]

        await services.job_service.handle_reconcile_result(
            replacement,
            ReconcileResultMessage(
                session_id=replacement.session_id,
                device_id=replacement.device_id,
                job_id=job["job_id"],
                command_id=job["command_id"],
                sequence=1,
                status="started",
                payload_hash=job["payload_hash"],
            ),
        )
        assert (await services.repository.get_job(OWNER, job["job_id"]))[
            "state"
        ] == "outcome_unknown"
        assert [
            message["message_type"] for message in replacement_socket.messages
        ] == ["reconcile", "cancel"]
        assert sum(
            message.get("message_type") == "command"
            for message in websocket.messages + replacement_socket.messages
        ) == command_count
    finally:
        await services.shutdown()


@pytest.mark.asyncio
async def test_terminal_cancelled_reconcile_resolves_unknown_write(tmp_path):
    services, connection, websocket = await _connected_services(tmp_path)
    try:
        job = await services.repository.create_job(
            owner_subject=OWNER,
            device_id="device-a",
            kind="write_fixture",
            effect_class="write",
            payload={"fixture": "write-like"},
            idempotency_key="write-cancelled",
            deadline_at=None,
        )
        await services.job_service.dispatch(job["job_id"], correlation_id="corr")
        await services.repository.transition_job(job["job_id"], "acknowledged")
        await services.repository.transition_job(job["job_id"], "running")
        await services.job_service.cancel(
            job["job_id"], owner_subject=OWNER, reason="cancel write fixture"
        )
        await services.job_service.handle_disconnect("device-a")
        assert (await services.repository.get_job(OWNER, job["job_id"]))[
            "state"
        ] == "outcome_unknown"

        await services.job_service.handle_reconcile_result(
            connection,
            ReconcileResultMessage(
                session_id=connection.session_id,
                device_id=connection.device_id,
                job_id=job["job_id"],
                command_id=job["command_id"],
                sequence=1,
                status="terminal",
                result_status="cancelled",
                payload_hash=job["payload_hash"],
            ),
        )
        assert (await services.repository.get_job(OWNER, job["job_id"]))[
            "state"
        ] == "cancelled"

        success_job = await services.repository.create_job(
            owner_subject=OWNER,
            device_id="device-a",
            kind="write_fixture",
            effect_class="write",
            payload={"fixture": "success-before-cancel-ack"},
            idempotency_key="write-success-race",
            deadline_at=None,
        )
        await services.job_service.dispatch(success_job["job_id"], correlation_id="corr")
        await services.repository.transition_job(success_job["job_id"], "acknowledged")
        await services.repository.transition_job(success_job["job_id"], "running")
        await services.job_service.cancel(
            success_job["job_id"], owner_subject=OWNER, reason="late cancellation"
        )
        await services.job_service.handle_message(
            connection,
            ResultMessage(
                session_id=connection.session_id,
                device_id=connection.device_id,
                job_id=success_job["job_id"],
                command_id=success_job["command_id"],
                sequence=1,
                status="succeeded",
                payload_hash=success_job["payload_hash"],
                result={"fixture": "success-won"},
            ),
        )
        assert (await services.repository.get_job(OWNER, success_job["job_id"]))[
            "state"
        ] == "succeeded"

        failed_job = await services.repository.create_job(
            owner_subject=OWNER,
            device_id="device-a",
            kind="write_fixture",
            effect_class="write",
            payload={"fixture": "terminal-failed"},
            idempotency_key="write-terminal-failed",
            deadline_at=None,
        )
        await services.job_service.dispatch(failed_job["job_id"], correlation_id="corr")
        await services.repository.transition_job(failed_job["job_id"], "acknowledged")
        await services.repository.transition_job(failed_job["job_id"], "running")
        await services.job_service.handle_disconnect("device-a")
        await services.job_service.handle_reconcile_result(
            connection,
            ReconcileResultMessage(
                session_id=connection.session_id,
                device_id=connection.device_id,
                job_id=failed_job["job_id"],
                command_id=failed_job["command_id"],
                sequence=1,
                status="terminal",
                result_status="failed",
                payload_hash=failed_job["payload_hash"],
                error_code="agent_rejected",
                error_message="private fixture path must not escape",
            ),
        )
        failed = await services.repository.get_job(OWNER, failed_job["job_id"])
        assert failed["state"] == "failed"
        assert failed["error_summary"] == "Agent rejected the command"
        assert "private fixture" not in failed["error_summary"]
    finally:
        await services.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_state", "expected_followup"),
    [
        ("rejected", "failed", None),
        ("duplicate", "reconnect_pending", "reconcile"),
        ("already_terminal", "reconnect_pending", "reconcile"),
    ],
)
async def test_ack_statuses_have_explicit_durable_semantics(
    tmp_path, status, expected_state, expected_followup
):
    services, connection, websocket = await _connected_services(tmp_path)
    try:
        job = await services.repository.create_job(
            owner_subject=OWNER,
            device_id="device-a",
            kind="observe",
            effect_class="read",
            payload={},
            idempotency_key=f"ack-{status}",
            deadline_at=None,
        )
        await services.job_service.dispatch(job["job_id"], correlation_id="corr")
        await services.job_service.handle_message(
            connection,
            AckMessage(
                session_id=connection.session_id,
                device_id=connection.device_id,
                job_id=job["job_id"],
                command_id=job["command_id"],
                sequence=1,
                status=status,
                idempotency_key=job["idempotency_key"],
                payload_hash=job["payload_hash"],
            ),
        )
        assert (await services.repository.get_job(OWNER, job["job_id"]))[
            "state"
        ] == expected_state
        followups = websocket.messages[1:]
        if expected_followup is None:
            assert followups == []
        else:
            assert [message["message_type"] for message in followups] == [
                expected_followup
            ]
    finally:
        await services.shutdown()


@pytest.mark.asyncio
async def test_capability_missing_fails_before_command_send(tmp_path):
    services, _connection, websocket = await _connected_services(
        tmp_path, capabilities=("query",)
    )
    try:
        with pytest.raises(GatewayError) as captured:
            await services.observe(
                CadObserveInputDurable(device_id="device-a"), PRINCIPAL, "corr"
            )
        assert captured.value.code == "capability_missing"
        assert websocket.messages == []
        with services.database.read_connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        await services.shutdown()


@pytest.mark.asyncio
async def test_real_websocket_payload_mismatch_fails_job_without_snapshot(tmp_path):
    port = _free_port()
    config = GatewayConfig(
        host="127.0.0.1",
        port=port,
        profile="phase3_poc",
        db_path=str(tmp_path / "payload-mismatch.db"),
        fixture_tokens=(("device-a", "token-a"),),
        allowed_hosts=("127.0.0.1:*",),
        request_wait_timeout_seconds=1,
    )
    services = DurableGatewayServices(
        SqliteDatabase(config.db_path),
        ConnectionRegistry(),
        device_tokens={"device-a": "token-a"},
        request_wait_timeout_seconds=1,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(services, config=config),
            host=config.host,
            port=port,
            log_level="error",
        )
    )
    server_task = asyncio.create_task(server.serve())
    observe_task: asyncio.Task[Any] | None = None
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/agent/ws",
            additional_headers={"Authorization": "Bearer token-a"},
            proxy=None,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    HelloMessage(
                        device_id="device-a",
                        fixture_proof="token-a",
                        capabilities=["observe", "query"],
                        capability_hash=canonical_capability_hash(
                            ["observe", "query"]
                        ),
                    ).model_dump(mode="json", exclude_none=True)
                )
            )
            assert isinstance(
                parse_agent_message(await websocket.recv()), WelcomeMessage
            )
            observe_task = asyncio.create_task(
                services.observe(
                    CadObserveInputDurable(device_id="device-a"),
                    PRINCIPAL,
                    "corr-mismatch",
                )
            )
            command = parse_agent_message(await websocket.recv())
            assert command.message_type == "command"
            await websocket.send(
                json.dumps(
                    ResultMessage(
                        session_id=command.session_id,
                        device_id=command.device_id,
                        job_id=command.job_id,
                        command_id=command.command_id,
                        sequence=1,
                        status="succeeded",
                        payload_hash="f" * 64,
                        result={"snapshot": _snapshot("must-not-persist")},
                    ).model_dump(mode="json", exclude_none=True)
                )
            )
            with pytest.raises(GatewayError) as captured:
                await observe_task
            assert captured.value.code == "payload_mismatch"
            job = await services.repository.get_job(OWNER, command.job_id)
            assert job is not None
            assert job["state"] == "failed"
            assert job["error_code"] == "payload_mismatch"
            with services.database.read_connection() as conn:
                assert conn.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE job_id = ?",
                    (command.job_id,),
                ).fetchone()[0] == 0
    finally:
        if observe_task is not None and not observe_task.done():
            observe_task.cancel()
            await asyncio.gather(observe_task, return_exceptions=True)
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10)


@pytest.mark.asyncio
async def test_fatal_maintenance_failure_only_fails_readiness(tmp_path, monkeypatch):
    registry = ConnectionRegistry()
    services = DurableGatewayServices(
        SqliteDatabase(tmp_path / "fatal-maintenance.db"),
        registry,
        device_tokens={"device-a": "token-a"},
        maintenance_interval_seconds=0.01,
    )

    async def fatal_maintenance() -> None:
        raise RuntimeError("fatal maintenance fixture")

    monkeypatch.setattr(services, "_run_maintenance_once", fatal_maintenance)
    await services.initialize()
    try:
        for _ in range(100):
            if services._maintenance_task and services._maintenance_task.done():
                break
            await asyncio.sleep(0.01)
        assert services._maintenance_task is not None
        assert services._maintenance_task.done()

        config = GatewayConfig(
            profile="phase3_poc",
            db_path=str(tmp_path / "fatal-maintenance.db"),
            fixture_tokens=(("device-a", "token-a"),),
        )
        transport = httpx.ASGITransport(app=create_app(services, config=config))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", trust_env=False
        ) as client:
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.get("/readyz")).status_code == 503
    finally:
        await services.shutdown()


@pytest.mark.asyncio
async def test_stale_maintenance_snapshot_cannot_offline_replacement_session(
    tmp_path, monkeypatch
):
    services, stale_connection, _websocket = await _connected_services(tmp_path)
    try:
        stale_connection.last_heartbeat -= timedelta(hours=1)
        replacement_socket = RecordingWebSocket()
        replacement = AgentConnection(
            "device-a",
            "session-b",
            replacement_socket,
            "cad.agent/1",
            capabilities=("observe", "query"),
            capability_hash=canonical_capability_hash(("observe", "query")),
        )
        await services.registry.add(replacement)
        await services.on_agent_connected(replacement)
        job = await services.repository.create_job(
            owner_subject=OWNER,
            device_id="device-a",
            kind="observe",
            effect_class="read",
            payload={},
            idempotency_key="replacement-maintenance-race",
            deadline_at=None,
        )
        await services.job_service.dispatch(job["job_id"], correlation_id="corr")
        await services.repository.transition_job(job["job_id"], "acknowledged")
        await services.repository.transition_job(job["job_id"], "running")

        async def stale_snapshot() -> list[AgentConnection]:
            return [stale_connection]

        monkeypatch.setattr(
            services.registry, "stale_connections", stale_snapshot
        )
        await services._run_maintenance_once()

        active = await services.repository.get_active_session("device-a")
        device = await services.repository.get_device(OWNER, "device-a")
        assert active is not None and active["session_id"] == "session-b"
        assert device is not None and device["status"] == "online"
        assert await services.registry.is_current(replacement)
        assert (await services.repository.get_job(OWNER, job["job_id"]))[
            "state"
        ] == "running"
    finally:
        await services.shutdown()
