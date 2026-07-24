from __future__ import annotations

import pytest

from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.repositories import (
    RepositoryConflict,
    SqliteRepository,
)


@pytest.mark.asyncio
async def test_runtime_host_evidence_remains_owner_scoped(tmp_path):
    database = SqliteDatabase(tmp_path / "gateway.db")
    await database.open()
    repository = SqliteRepository(database)
    try:
        await repository.seed_device(
            owner_subject="owner-a",
            device_id="device-a",
            display_name="Full R25",
            capabilities=["observe", "runtime.managed_dotnet"],
            fixture_auth_ref="fixture:device-a",
        )
        await repository.seed_device(
            owner_subject="owner-b",
            device_id="device-b",
            display_name="LT compatibility",
            capabilities=["observe", "runtime.autolisp_file_ipc"],
            fixture_auth_ref="fixture:device-b",
        )
        job = await repository.create_job(
            owner_subject="owner-a",
            device_id="device-a",
            kind="observe",
            effect_class="read",
            payload={"observation_level": "summary"},
            idempotency_key="runtime-evidence-a",
            deadline_at=None,
        )
        result = {
            "execution_evidence": {
                "runtime_id": "managed_dotnet",
                "runtime_role": "primary",
                "host_family": "R25",
                "host_version": "0.1.0",
                "package_hash": "sha256:" + ("a" * 64),
            }
        }
        await repository.finalize_job_result(
            job_id=job["job_id"],
            device_id="device-a",
            command_id=job["command_id"],
            payload_hash=job["payload_hash"],
            target="failed",
            result=result,
            error_code="lab_fixture",
            error_summary="bounded fixture",
        )

        owned = await repository.get_job("owner-a", job["job_id"])
        assert owned is not None
        assert owned["result"]["execution_evidence"]["runtime_id"] == "managed_dotnet"
        assert await repository.get_job("owner-b", job["job_id"]) is None
        assert await repository.list_events("owner-b", job["job_id"]) == ([], None)
        with pytest.raises(RepositoryConflict, match="not_found"):
            await repository.create_job(
                owner_subject="owner-b",
                device_id="device-a",
                kind="observe",
                effect_class="read",
                payload={"observation_level": "summary"},
                idempotency_key="cross-owner-runtime-evidence",
                deadline_at=None,
            )
    finally:
        await database.close()
