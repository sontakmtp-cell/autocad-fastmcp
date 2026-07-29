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
from autocad_gateway.workflows.runner import WorkflowRunner


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
    replayed_control = await service.control(
        owner_subject="owner-a",
        run_id=first["run_id"],
        action="submit_input",
        expected_state_version=first["state_version"],
        idempotency_key="finish-cleanup",
        payload={"decision": "continue"},
    )
    assert replayed_control == completed


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
async def test_submit_input_recovers_after_wait_was_already_resolved(service):
    created = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="drawing.cleanup-audit",
        version=None,
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_cleanup_inputs(),
        idempotency_key="recover-wait-start",
        scopes=("autocad.read",),
    )
    payload = {"decision": "continue"}
    await service.repository.begin_control_command(
        owner_subject="owner-a",
        run_id=created["run_id"],
        action="submit_input",
        expected_state_version=created["state_version"],
        idempotency_key="recover-wait-control",
        payload=payload,
    )
    wait = await service.repository.current_wait("owner-a", created["run_id"])
    await service.repository.resolve_wait(
        owner_subject="owner-a",
        run_id=created["run_id"],
        wait_id=wait["wait_id"],
        expected_state_version=created["state_version"],
        response_schema_digest=wait["response_schema_digest"],
        response=payload,
        idempotency_key="recover-wait-control",
    )
    recovered = await service.control(
        owner_subject="owner-a",
        run_id=created["run_id"],
        action="submit_input",
        expected_state_version=created["state_version"],
        idempotency_key="recover-wait-control",
        payload=payload,
        scopes=("autocad.read",),
    )
    assert recovered["state"] == "succeeded"


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

    repository = Phase9Repository(database)
    commit_states = [
        {"state": "queued", "intent_id": "intent-a", "job_id": "job-a"},
        {
            "state": "succeeded",
            "intent_id": "intent-a",
            "job_id": "job-a",
            "receipt_id": "receipt-a",
        },
    ]

    async def commit_status(owner, intent_id):
        assert (owner, intent_id) == ("owner-a", "intent-a")
        return commit_states.pop(0)

    class ActionPort:
        async def dispatch(self, action_kind, payload, *, idempotency_key):
            if action_kind == "preview":
                return await preview()
            return await commit(
                payload["owner_subject"], payload["preview_id"], idempotency_key
            )

        async def reconcile(self, action_kind, child_ref, *, idempotency_key):
            return {
                "state": "succeeded",
                **await self.dispatch(
                    action_kind,
                    child_ref["payload"],
                    idempotency_key=idempotency_key,
                ),
            }

    value = WorkflowApplicationService(
        repository,
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
        action_runner=WorkflowRunner(
            repository, ActionPort(), worker_id="test-worker"
        ),
        commit_status_resolver=commit_status,
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
    actions = await repository.list_actions("owner-a", result["run_id"])
    assert [action["state"] for action in actions] == ["completed", "completed"]
    assert actions[-1]["child_ref"]["intent_id"] == "intent-a"

    with pytest.raises(WorkflowServiceError, match="insufficient_scope"):
        await value.control(
            owner_subject="owner-a",
            run_id=result["run_id"],
            action="resume",
            expected_state_version=result["state_version"],
            idempotency_key="read-only-control",
            scopes=("autocad.read",),
        )
    await value.maintenance_once()
    waiting = await value.get("owner-a", result["run_id"])
    assert waiting["run"]["state"] == "waiting_for_job"
    await value.maintenance_once()
    completed = await value.get("owner-a", result["run_id"])
    assert completed["run"]["state"] == "succeeded"
    await database.close()


@pytest.mark.asyncio
async def test_security_revocation_between_preview_and_commit_fails_closed(
    tmp_path,
):
    database = SqliteDatabase(tmp_path / "revoked.sqlite")
    await database.open()
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)
    catalog_repository = SkillCatalogRepository(database)
    commit_calls = []

    async def preview(*args):
        return {
            "observe": {"snapshot_id": "snapshot-a"},
            "query": {"entities": []},
            "pure": {"semantic_digest": "sha256:" + "1" * 64},
            "prepare": {"program_id": "program-a", "program_revision": 1},
            "preview": {"preview_id": "preview-a", "state": "succeeded"},
        }

    async def commit(*args):
        commit_calls.append(args)
        return {}

    async def write_device(owner, device):
        return {
            "capabilities": {"cad.program.v1.compile"},
            "operation_packs": {"cad.program/1.0-create-core"},
            "runtime_release_verified": True,
            "capability_evidence_verified": True,
            "identity_generation": 1,
        }

    value = WorkflowApplicationService(
        Phase9Repository(database),
        catalog_repository,
        catalog,
        enabled=True,
        catalog_enabled=True,
        policy_epoch=9,
        write_enabled=True,
        enabled_skills={"mechanical.auto-dimension-overall"},
        device_resolver=write_device,
        snapshot_resolver=lambda owner, device, snapshot: _snapshot(
            owner, device, snapshot
        ),
        write_preview_executor=preview,
        commit_request_executor=commit,
    )
    value.initialize_catalog()
    started = await value.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="mechanical.auto-dimension-overall",
        version="1.0.0",
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs={
            "source_snapshot_id": "snapshot-a",
            "document_revision": "revision-a",
            "layer": "DIM",
            "entity_ids": ["entity-a"],
            "profile": "mechanical_mm",
            "offset": 10.0,
        },
        idempotency_key="revocation-start",
        scopes=("autocad.read", "autocad.write"),
    )
    assert started["state"] == "waiting_for_user"
    catalog_repository.transition(
        "mechanical.auto-dimension-overall",
        "1.0.0",
        "published",
        "security_revoked",
        "security-operator",
    )
    with pytest.raises(WorkflowServiceError, match="skill_security_revoked"):
        await value.control(
            owner_subject="owner-a",
            run_id=started["run_id"],
            action="submit_input",
            expected_state_version=started["state_version"],
            idempotency_key="revoked-control",
            payload={"decision": "continue"},
            scopes=("autocad.read", "autocad.write"),
        )
    assert (
        await value.repository.get_run("owner-a", started["run_id"])
    )["state"] == "needs_attention"
    assert commit_calls == []
    await database.close()
