from pathlib import Path

import pytest

from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.phase9_repository import Phase9Repository
from autocad_gateway.skills.catalog import SkillCatalog
from autocad_gateway.skills.catalog_repository import SkillCatalogRepository
from autocad_gateway.workflows.service import (
    WorkflowApplicationService,
    WorkflowServiceError,
)


CATALOG_ROOT = Path(__file__).resolve().parents[4] / "packages" / "skill_catalog"


@pytest.fixture
async def service(tmp_path):
    database = SqliteDatabase(tmp_path / "phase9.sqlite")
    await database.open()
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)
    catalog_repository = SkillCatalogRepository(database)
    value = WorkflowApplicationService(
        Phase9Repository(database),
        catalog_repository,
        catalog,
        enabled=True,
        catalog_enabled=True,
        policy_epoch=9,
        write_enabled=False,
        enabled_skills={"drawing.cleanup-audit"},
        device_resolver=lambda owner, device: _device(owner, device),
        snapshot_resolver=lambda owner, device, snapshot: _snapshot(
            owner, device, snapshot
        ),
    )
    value.initialize_catalog()
    yield value
    await database.close()


async def _device(owner: str, device: str) -> dict:
    if owner != "owner-a" or device != "device-a":
        raise ValueError("not_found")
    return {
        "capabilities": set(),
        "operation_packs": set(),
        "runtime_release_verified": True,
        "capability_evidence_verified": True,
        "identity_generation": 1,
    }


async def _snapshot(owner: str, device: str, snapshot: str) -> dict:
    if (owner, device, snapshot) != ("owner-a", "device-a", "snapshot-a"):
        raise ValueError("not_found")
    return {
        "snapshot_id": snapshot,
        "device_id": device,
        "document_id": "document-a",
        "document_revision": "revision-a",
        "entities": [],
    }


def _cleanup_inputs() -> dict:
    return {
        "source_snapshot_id": "snapshot-a",
        "document_revision": "revision-a",
        "layer": "0",
        "page_size": 50,
        "max_candidates": 20,
    }


@pytest.mark.asyncio
async def test_catalog_start_materializes_pinned_dag_and_replays(service):
    listing = await service.list_skills(
        owner_subject="owner-a", device_id="device-a"
    )
    assert [skill["skill_id"] for skill in listing["skills"]] == [
        "drawing.cleanup-audit"
    ]
    first = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="drawing.cleanup-audit",
        version=None,
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_cleanup_inputs(),
        idempotency_key="start-cleanup",
        scopes=("autocad.read",),
    )
    replay = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="drawing.cleanup-audit",
        version=None,
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_cleanup_inputs(),
        idempotency_key="start-cleanup",
        scopes=("autocad.read",),
    )
    assert first["state"] == "waiting_for_user"
    assert first["pins"]["workflow_digest"].startswith("sha256:")
    assert replay["run_id"] == first["run_id"]
    assert replay["replayed"] is True
    detail = await service.get("owner-a", first["run_id"])
    assert len(detail["steps"]) == 5
    assert sum(step["state"] == "waiting" for step in detail["steps"]) == 1
    completed = await service.control(
        owner_subject="owner-a",
        run_id=first["run_id"],
        action="submit_input",
        expected_state_version=first["state_version"],
        idempotency_key="finish-cleanup",
        payload={"decision": "continue"},
    )
    assert completed["state"] == "succeeded"


@pytest.mark.asyncio
async def test_input_device_owner_and_write_flags_fail_closed(service):
    invalid = _cleanup_inputs()
    invalid["command"] = "erase all"
    with pytest.raises(WorkflowServiceError, match="invalid_request"):
        await service.start(
            owner_subject="owner-a",
            actor_subject="owner-a",
            skill_id="drawing.cleanup-audit",
            version=None,
            device_id="device-a",
            source_snapshot_id="snapshot-a",
            inputs=invalid,
            idempotency_key="invalid",
            scopes=("autocad.read",),
        )
    with pytest.raises(WorkflowServiceError, match="not_found"):
        await service.list_skills(owner_subject="owner-b", device_id="device-a")
    with pytest.raises(WorkflowServiceError, match="not_found"):
        await service.start(
            owner_subject="owner-a",
            actor_subject="owner-a",
            skill_id="mechanical.plate-hole-pattern",
            version=None,
            device_id="device-a",
            source_snapshot_id="snapshot-a",
            inputs={},
            idempotency_key="write-disabled",
            scopes=("autocad.read", "autocad.write"),
        )


@pytest.mark.asyncio
async def test_cross_owner_run_is_not_found(service):
    created = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="drawing.cleanup-audit",
        version=None,
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_cleanup_inputs(),
        idempotency_key="owner-check",
        scopes=("autocad.read",),
    )
    with pytest.raises(WorkflowServiceError, match="not_found"):
        await service.get("owner-b", created["run_id"])


@pytest.mark.asyncio
async def test_write_workflow_reuses_preview_and_trusted_approval_ports(tmp_path):
    database = SqliteDatabase(tmp_path / "write.sqlite")
    await database.open()
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)

    async def write_device(owner: str, device: str) -> dict:
        assert (owner, device) == ("owner-a", "device-a")
        return {
            "capabilities": {"cad.program.v1.compile"},
            "operation_packs": {"cad.program/1.0-create-core"},
            "runtime_release_verified": True,
            "capability_evidence_verified": True,
            "identity_generation": 1,
        }

    async def preview(*args) -> dict:
        return {
            "pure": {"semantic_digest": "sha256:" + "1" * 64},
            "prepare": {"program_id": "program-a", "program_revision": 1},
            "preview": {"preview_id": "preview-a", "state": "succeeded"},
        }

    async def commit(owner: str, preview_id: str, key: str) -> dict:
        assert owner == "owner-a"
        assert preview_id == "preview-a"
        assert key
        return {
            "admission_status": "approval_required",
            "intent_id": "intent-a",
            "consent_id": "consent-a",
        }

    value = WorkflowApplicationService(
        Phase9Repository(database),
        SkillCatalogRepository(database),
        catalog,
        enabled=True,
        catalog_enabled=True,
        policy_epoch=9,
        write_enabled=True,
        enabled_skills={"mechanical.plate-hole-pattern"},
        device_resolver=write_device,
        snapshot_resolver=lambda owner, device, snapshot: _snapshot(
            owner, device, snapshot
        ),
        write_preview_executor=preview,
        commit_request_executor=commit,
    )
    value.initialize_catalog()
    result = await value.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="mechanical.plate-hole-pattern",
        version="1.0.0",
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs={
            "source_snapshot_id": "snapshot-a",
            "document_revision": "revision-a",
            "layer": "MECH",
            "width": 100.0,
            "height": 60.0,
            "hole_diameter": 8.0,
            "rows": 2,
            "columns": 3,
            "margin_x": 10.0,
            "margin_y": 10.0,
            "include_overall_dimensions": True,
        },
        idempotency_key="plate-start",
        scopes=("autocad.read", "autocad.write"),
    )
    assert result["state"] == "waiting_for_trusted_approval"
    detail = await value.get("owner-a", result["run_id"])
    assert detail["current_wait"]["wait_kind"] == "trusted_approval"
    assert next(
        step for step in detail["steps"] if step["step_id"] == "commit"
    )["state"] == "waiting"
    await database.close()
