from __future__ import annotations

import pytest

from autocad_contracts import AckMessage, ProgressMessage, ReconcileResultMessage, ResultMessage
from autocad_gateway.application.job_service import DurableJobError, DurableJobService
from autocad_gateway.infrastructure.agent_transport.connection_registry import AgentConnection, ConnectionRegistry
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.repositories import SqliteRepository


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, value):
        self.messages.append(value)

    async def close(self, **kwargs):
        return None


@pytest.fixture
async def job_context(tmp_path):
    database = SqliteDatabase(tmp_path / "jobs.db")
    await database.open()
    repository = SqliteRepository(database)
    await repository.seed_device(
        owner_subject="owner",
        device_id="device-a",
        display_name="Device A",
        capabilities=["observe"],
        fixture_auth_ref="fixture:device-a",
    )
    registry = ConnectionRegistry()
    websocket = FakeWebSocket()
    connection = AgentConnection("device-a", "session-a", websocket, "cad.agent/1")
    await registry.add(connection)
    service = DurableJobService(repository, registry)
    yield repository, service, connection, websocket
    await database.close()


@pytest.mark.asyncio
async def test_dispatch_ack_progress_result_and_reconcile(job_context):
    repository, service, connection, websocket = job_context
    job = await repository.create_job(
        owner_subject="owner", device_id="device-a", kind="observe", effect_class="read",
        payload={"observation_level": "summary"}, idempotency_key="dispatch", deadline_at=None,
    )
    await service.dispatch(job["job_id"], correlation_id="c1")
    command = websocket.messages[-1]
    await service.handle_message(connection, AckMessage(**{
        "session_id": "session-a", "device_id": "device-a", "job_id": job["job_id"],
        "command_id": job["command_id"], "status": "accepted", "idempotency_key": job["idempotency_key"],
        "payload_hash": job["payload_hash"],
    }))
    await service.handle_message(connection, ProgressMessage(
        session_id="session-a", device_id="device-a", job_id=job["job_id"], command_id=job["command_id"],
        sequence=1, phase="complete", percent=100, message="done",
    ))
    await service.handle_message(connection, ResultMessage(
        session_id="session-a", device_id="device-a", job_id=job["job_id"], command_id=job["command_id"],
        status="succeeded", payload_hash=job["payload_hash"], result={"ok": True},
    ))
    assert (await repository.get_job("owner", job["job_id"]))["state"] == "succeeded"
    assert command["device_id"] == "device-a"


@pytest.mark.asyncio
async def test_started_write_disconnect_becomes_unknown_and_is_never_redispatched(job_context):
    repository, service, connection, websocket = job_context
    job = await repository.create_job(
        owner_subject="owner", device_id="device-a", kind="write_fixture", effect_class="write",
        payload={"operation": "fixture"}, idempotency_key="write", deadline_at=None,
    )
    await service.dispatch(job["job_id"], correlation_id="c1")
    await repository.transition_job(job["job_id"], "acknowledged")
    await repository.transition_job(job["job_id"], "running")
    await service.handle_disconnect("device-a")
    value = await repository.get_job("owner", job["job_id"])
    assert value["state"] == "outcome_unknown"
    sent_before = len(websocket.messages)
    with pytest.raises(DurableJobError, match="outcome_unknown"):
        await service.dispatch(job["job_id"], correlation_id="retry")
    assert len(websocket.messages) == sent_before


@pytest.mark.asyncio
async def test_read_reconnect_not_started_is_queued_then_dispatched_again(job_context):
    repository, service, connection, websocket = job_context
    job = await repository.create_job(
        owner_subject="owner", device_id="device-a", kind="observe", effect_class="read",
        payload={}, idempotency_key="reconnect", deadline_at=None,
    )
    await service.dispatch(job["job_id"], correlation_id="c1")
    await repository.transition_job(job["job_id"], "reconnect_pending")
    await service.handle_reconcile_result(connection, ReconcileResultMessage(
        session_id="session-a", device_id="device-a", command_id=job["command_id"],
        status="not_started", payload_hash=job["payload_hash"],
    ))
    assert (await repository.get_job("owner", job["job_id"]))["state"] == "dispatched"
    assert len(websocket.messages) == 2
