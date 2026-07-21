from __future__ import annotations

import asyncio

import pytest

from autocad_gateway.infrastructure.sqlite.database import DatabaseError, SqliteDatabase
from autocad_gateway.infrastructure.sqlite.repositories import RepositoryConflict, SqliteRepository


@pytest.fixture
async def repository(tmp_path):
    database = SqliteDatabase(tmp_path / "gateway.db")
    await database.open()
    repository = SqliteRepository(database)
    await repository.seed_device(
        owner_subject="owner",
        device_id="device-a",
        display_name="Device A",
        capabilities=["observe"],
        fixture_auth_ref="fixture:device-a",
    )
    yield repository
    await database.close()


@pytest.mark.asyncio
async def test_migration_is_idempotent_and_owner_filter_is_fail_closed(repository):
    assert [item["device_id"] for item in await repository.list_devices("owner")] == ["device-a"]
    assert await repository.list_devices("other") == []


@pytest.mark.asyncio
async def test_job_idempotency_same_hash_reuses_job_and_different_hash_conflicts(repository):
    first = await repository.create_job(
        owner_subject="owner",
        device_id="device-a",
        kind="observe",
        effect_class="read",
        payload={"level": "summary"},
        idempotency_key="same-request",
        deadline_at=None,
    )
    second = await repository.create_job(
        owner_subject="owner",
        device_id="device-a",
        kind="observe",
        effect_class="read",
        payload={"level": "summary"},
        idempotency_key="same-request",
        deadline_at=None,
    )
    assert second["existing"] is True
    assert second["job_id"] == first["job_id"]
    with pytest.raises(RepositoryConflict, match="payload_mismatch"):
        await repository.create_job(
            owner_subject="owner",
            device_id="device-a",
            kind="observe",
            effect_class="read",
            payload={"level": "detail"},
            idempotency_key="same-request",
            deadline_at=None,
        )


@pytest.mark.asyncio
async def test_cas_claim_and_backup_restore(repository, tmp_path):
    job = await repository.create_job(
        owner_subject="owner",
        device_id="device-a",
        kind="observe",
        effect_class="read",
        payload={},
        idempotency_key="cas",
        deadline_at=None,
    )
    first, second = await asyncio.gather(
        repository.claim_job(job["job_id"]), repository.claim_job(job["job_id"])
    )
    assert sum(value is not None for value in (first, second)) == 1
    await repository.transition_job(job["job_id"], "acknowledged")
    await repository.transition_job(job["job_id"], "running")
    await repository.transition_job(job["job_id"], "succeeded", result={"ok": True}, evidence=True)
    await repository.database.backup_to(tmp_path / "backup.db")
    restored_db = SqliteDatabase(tmp_path / "backup.db")
    await restored_db.open()
    restored = SqliteRepository(restored_db)
    value = await restored.get_job("owner", job["job_id"])
    assert value["state"] == "succeeded"
    events, _ = await restored.list_events("owner", job["job_id"])
    assert [event["state"] for event in events] == ["queued", "dispatched", "acknowledged", "running", "succeeded"]
    await restored_db.close()


@pytest.mark.asyncio
async def test_migration_checksum_mismatch_fails(tmp_path):
    database = SqliteDatabase(tmp_path / "gateway.db")
    await database.open()
    await database.close()
    altered = tmp_path / "altered.sql"
    altered.write_text("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL);", encoding="utf-8")
    broken = SqliteDatabase(tmp_path / "gateway.db", migration_path=altered)
    with pytest.raises(DatabaseError, match="checksum mismatch"):
        await broken.open()
