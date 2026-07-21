from __future__ import annotations

from datetime import timedelta

import pytest

from autocad_gateway.application.job_service import DurableJobService
from autocad_gateway.infrastructure.agent_transport.connection_registry import AgentConnection, ConnectionRegistry, utc_now
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.repositories import SqliteRepository


class FakeWebSocket:
    async def send_json(self, value):
        return None

    async def close(self, **kwargs):
        return None


@pytest.mark.asyncio
async def test_stale_presence_is_detectable_without_affecting_health(tmp_path):
    registry = ConnectionRegistry(stale_after_seconds=2)
    connection = AgentConnection("device-a", "session-a", FakeWebSocket(), "cad.agent/1")
    connection.last_heartbeat = utc_now() - timedelta(seconds=10)
    await registry.add(connection)
    assert await registry.stale_devices() == ["device-a"]
    assert await registry.is_fresh("device-a") is False


@pytest.mark.asyncio
async def test_deadline_sweeper_creates_failed_audit_state(tmp_path):
    database = SqliteDatabase(tmp_path / "deadline.db")
    await database.open()
    repository = SqliteRepository(database)
    await repository.seed_device(
        owner_subject="owner", device_id="device-a", display_name="Device A",
        capabilities=["observe"], fixture_auth_ref="fixture:device-a",
    )
    service = DurableJobService(repository, ConnectionRegistry())
    job = await repository.create_job(
        owner_subject="owner", device_id="device-a", kind="observe", effect_class="read",
        payload={}, idempotency_key="deadline", deadline_at="2000-01-01T00:00:00+00:00",
    )
    await service.sweep_deadlines()
    value = await repository.get_job("owner", job["job_id"])
    assert value["state"] == "failed"
    assert value["error_code"] == "deadline_expired"
    await database.close()
