from pathlib import Path

import pytest

from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.phase9_repository import Phase9Repository
from autocad_gateway.skills.catalog import SkillCatalog
from autocad_gateway.skills.catalog_repository import SkillCatalogRepository
from autocad_gateway.workflows.runner import WorkflowRunner
from autocad_gateway.workflows.service import WorkflowApplicationService


CATALOG_ROOT = Path(__file__).resolve().parents[4] / "packages" / "skill_catalog"
SOURCE_DIGEST = "sha256:" + "a" * 64
SCENE_DIGEST = "sha256:" + "b" * 64


class BasePort:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def dispatch(self, action_kind, payload, *, idempotency_key):
        self.calls.append(action_kind)
        raise AssertionError("scene workflows must not call the Phase 9 effect port")

    async def reconcile(self, action_kind, child_ref, *, idempotency_key):
        return None


class ScenePort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def source_digest(
        self,
        *,
        owner_subject,
        device_id,
        source_snapshot_id,
        document_revision,
        analysis_profile,
    ):
        assert (
            owner_subject,
            device_id,
            source_snapshot_id,
            document_revision,
            analysis_profile,
        ) == (
            "owner-a",
            "device-a",
            "snapshot-a",
            "revision-a",
            "mechanical-2d/1",
        )
        return SOURCE_DIGEST

    async def dispatch(self, action_kind, payload, *, idempotency_key):
        self.calls.append((action_kind, idempotency_key))
        common = {
            "scene_id": "scn_scene_a",
            "scene_digest": SCENE_DIGEST,
            "source_digest": SOURCE_DIGEST,
            "source_snapshot_id": "snapshot-a",
            "document_revision": "revision-a",
        }
        if action_kind == "build_scene":
            return common
        if action_kind == "query_scene":
            return {
                **common,
                "items": [
                    {
                        "code": "duplicate_geometry",
                        "evidence": [{"entity_id": "entity-a"}],
                    }
                ],
                "total": 1,
            }
        if action_kind == "validate_scene":
            return {**common, "valid": True}
        raise AssertionError(f"unexpected scene action: {action_kind}")

    async def reconcile(self, action_kind, child_ref, *, idempotency_key):
        return None


async def _device(owner_subject: str, device_id: str) -> dict:
    assert (owner_subject, device_id) == ("owner-a", "device-a")
    return {
        "capabilities": {"scene.core/1"},
        "operation_packs": set(),
        "runtime_release_verified": True,
        "capability_evidence_verified": True,
        "identity_generation": 1,
    }


async def _snapshot(owner_subject: str, device_id: str, snapshot_id: str) -> dict:
    assert (owner_subject, device_id, snapshot_id) == (
        "owner-a",
        "device-a",
        "snapshot-a",
    )
    return {
        "snapshot_id": "snapshot-a",
        "device_id": "device-a",
        "document_id": "document-a",
        "document_revision": "revision-a",
        "entities": [],
    }


def _service(
    database: SqliteDatabase,
    base_port: BasePort,
    scene_port: ScenePort,
) -> tuple[WorkflowApplicationService, Phase9Repository]:
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)
    catalog_repository = SkillCatalogRepository(database)
    repository = Phase9Repository(database)
    runner = WorkflowRunner(repository, base_port, worker_id="worker")
    service = WorkflowApplicationService(
        repository,
        catalog_repository,
        catalog,
        enabled=True,
        catalog_enabled=True,
        policy_epoch=10,
        write_enabled=False,
        enabled_skills={"drawing.cleanup-audit"},
        device_resolver=_device,
        snapshot_resolver=_snapshot,
        action_runner=runner,
        scene_port=scene_port,
    )
    service.initialize_catalog()
    return service, repository


@pytest.mark.asyncio
async def test_scene_cleanup_is_read_only_and_restart_retains_exact_child_refs(
    tmp_path,
):
    database = SqliteDatabase(tmp_path / "phase10-workflow.sqlite")
    await database.open()
    base_port = BasePort()
    scene_port = ScenePort()
    service, repository = _service(database, base_port, scene_port)

    started = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="drawing.cleanup-audit",
        version="1.1.0",
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs={
            "source_snapshot_id": "snapshot-a",
            "document_revision": "revision-a",
            "layer": "0",
            "page_size": 50,
            "max_candidates": 20,
        },
        idempotency_key="scene-cleanup-a",
        scopes=("autocad.read",),
    )

    assert started["state"] == "waiting_for_user"
    assert base_port.calls == []
    assert [call[0] for call in scene_port.calls] == [
        "build_scene",
        "query_scene",
        "validate_scene",
    ]
    detail = await service.get("owner-a", started["run_id"])
    steps = {step["step_id"]: step for step in detail["steps"]}
    report = steps["report"]["output_ref"]["result"]
    assert report == {
        "status": "issues_found",
        "scene_id": "scn_scene_a",
        "scene_digest": SCENE_DIGEST,
        "source_digest": SOURCE_DIGEST,
        "source_snapshot_id": "snapshot-a",
        "document_revision": "revision-a",
        "issue_count": 1,
        "issue_codes": ["duplicate_geometry"],
        "validation_ok": True,
        "write_authority": False,
    }
    actions = await repository.list_actions("owner-a", started["run_id"])
    assert len(actions) == 3
    assert {action["effect_class"] for action in actions} == {"read"}
    for action in actions:
        assert action["idempotency_key"].endswith(":" + SOURCE_DIGEST)
        assert action["child_ref"] == {
            "scene_id": "scn_scene_a",
            "scene_digest": SCENE_DIGEST,
            "source_digest": SOURCE_DIGEST,
            "source_snapshot_id": "snapshot-a",
            "document_revision": "revision-a",
            "idempotency_key": action["idempotency_key"],
        }

    await database.close()
    await database.open()
    restarted_base_port = BasePort()
    restarted_scene_port = ScenePort()
    restarted, restarted_repository = _service(
        database, restarted_base_port, restarted_scene_port
    )
    restored = await restarted.get("owner-a", started["run_id"])
    restored_actions = await restarted_repository.list_actions(
        "owner-a", started["run_id"]
    )
    assert restored["run"]["state"] == "waiting_for_user"
    assert len(restored_actions) == 3
    assert restarted_scene_port.calls == []
    assert [
        action["child_ref"] for action in restored_actions
    ] == [action["child_ref"] for action in actions]

    completed = await restarted.control(
        owner_subject="owner-a",
        run_id=started["run_id"],
        action="submit_input",
        expected_state_version=restored["run"]["state_version"],
        idempotency_key="finish-scene-cleanup-a",
        payload={"decision": "continue"},
    )
    assert completed["state"] == "succeeded"
    assert restarted_base_port.calls == []
    assert restarted_scene_port.calls == []
    await database.close()
