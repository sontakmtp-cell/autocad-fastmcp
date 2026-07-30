from pathlib import Path

import pytest

from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.phase9_repository import Phase9Repository
from autocad_gateway.skills.catalog import SkillCatalog
from autocad_gateway.skills.catalog_repository import SkillCatalogRepository
from autocad_gateway.workflows.runner import WorkflowRunner
from autocad_gateway.workflows.service import WorkflowApplicationService


CATALOG_ROOT = Path(__file__).resolve().parents[4] / "packages" / "skill_catalog"
SOURCE_DIGEST = "sha256:" + "c" * 64
SCENE_DIGEST = "sha256:" + "d" * 64


class WritePort:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.preview_inputs: dict | None = None

    async def dispatch(self, action_kind, payload, *, idempotency_key):
        self.calls.append(action_kind)
        if action_kind == "commit":
            return {
                "state": "awaiting_approval",
                "admission_status": "approval_required",
                "intent_id": "intent-a",
            }
        assert action_kind == "preview"
        self.preview_inputs = payload["inputs"]
        evidence = self.preview_inputs["_scene_selection_evidence"]
        assert evidence["scene_digest"] == SCENE_DIGEST
        assert evidence["write_authority"] is False
        return {
            "observe": {
                "snapshot_id": "snapshot-a",
                "document_revision": "revision-a",
            },
            "query": {"entity_ids": ["entity-a"], "entity_count": 1},
            "pure": {"semantic_digest": "sha256:" + "e" * 64},
            "prepare": {"program_id": "program-a", "program_revision": 1},
            "preview": {"preview_id": "preview-a", "state": "succeeded"},
        }

    async def reconcile(self, action_kind, child_ref, *, idempotency_key):
        return None


class AutoDimensionScenePort:
    def __init__(self, *, evidence_entity_ids=None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.evidence_entity_ids = (
            ["entity-a"]
            if evidence_entity_ids is None
            else evidence_entity_ids
        )

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
            "scene_id": "scn_auto_dimension_a",
            "scene_digest": SCENE_DIGEST,
            "source_digest": SOURCE_DIGEST,
            "source_snapshot_id": "snapshot-a",
            "document_revision": "revision-a",
        }
        if action_kind == "build_scene":
            assert payload["include_sections"] == [
                "nodes",
                "contours",
                "features",
                "evidence",
            ]
            return common
        assert action_kind == "query_scene"
        assert payload["section"] == "evidence"
        assert payload["source_entity_ids"] == ["entity-a"]
        return {
            **common,
            "items": [
                {
                    "evidence_id": "evd_entity_a",
                    "evidence_type": "contour",
                    "evidence_strength": "derived_exact",
                    "source_entity_ids": self.evidence_entity_ids,
                    "algorithm_version": "contours/1.0.0",
                    "limitations": [],
                }
            ],
            "total": 1,
            "next_cursor": None,
        }

    async def reconcile(self, action_kind, child_ref, *, idempotency_key):
        return None


async def _device(owner_subject: str, device_id: str) -> dict:
    assert (owner_subject, device_id) == ("owner-a", "device-a")
    return {
        "capabilities": {"cad.program.v1.compile", "scene.core/1"},
        "operation_packs": {"cad.program/1.0-create-core"},
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
    write_port: WritePort,
    scene_port: AutoDimensionScenePort,
) -> tuple[WorkflowApplicationService, Phase9Repository]:
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)
    catalog_repository = SkillCatalogRepository(database)
    repository = Phase9Repository(database)
    runner = WorkflowRunner(repository, write_port, worker_id="worker")
    service = WorkflowApplicationService(
        repository,
        catalog_repository,
        catalog,
        enabled=True,
        catalog_enabled=True,
        policy_epoch=10,
        write_enabled=True,
        enabled_skills={"mechanical.auto-dimension-overall"},
        device_resolver=_device,
        snapshot_resolver=_snapshot,
        write_preview_executor=lambda *args: None,
        commit_request_executor=lambda *args: None,
        action_runner=runner,
        scene_port=scene_port,
    )
    service.initialize_catalog()
    return service, repository


@pytest.mark.asyncio
async def test_scene_evidence_gates_planner_and_restart_does_not_commit(tmp_path):
    database = SqliteDatabase(tmp_path / "auto-dimension-scene.sqlite")
    await database.open()
    write_port = WritePort()
    scene_port = AutoDimensionScenePort()
    service, repository = _service(database, write_port, scene_port)

    started = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="mechanical.auto-dimension-overall",
        version="1.1.0",
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
        idempotency_key="auto-dimension-scene-a",
        scopes=("autocad.read", "autocad.write"),
    )

    assert started["state"] == "waiting_for_user"
    assert write_port.calls == ["preview"]
    assert [item[0] for item in scene_port.calls] == [
        "build_scene",
        "query_scene",
    ]
    assert write_port.preview_inputs["entity_ids"] == ["entity-a"]
    detail = await service.get("owner-a", started["run_id"])
    steps = {step["step_id"]: step for step in detail["steps"]}
    assert len(steps) == 12
    assert steps["build_scene"]["state"] == "succeeded"
    assert steps["query_scene"]["state"] == "succeeded"
    assert steps["review"]["state"] == "waiting"
    assert steps["commit"]["state"] == "pending"
    actions = await repository.list_actions("owner-a", started["run_id"])
    assert [action["action_kind"] for action in actions] == [
        "build_scene",
        "query_scene",
        "preview",
    ]
    scene_refs = [
        action["child_ref"]
        for action in actions
        if action["action_kind"] in {"build_scene", "query_scene"}
    ]

    await database.close()
    await database.open()
    restarted_write_port = WritePort()
    restarted_scene_port = AutoDimensionScenePort()
    restarted, restarted_repository = _service(
        database, restarted_write_port, restarted_scene_port
    )
    await restarted.reconcile_restart()
    restored = await restarted.get("owner-a", started["run_id"])
    restored_actions = await restarted_repository.list_actions(
        "owner-a", started["run_id"]
    )

    assert restored["run"]["state"] == "waiting_for_user"
    assert restarted_write_port.calls == []
    assert restarted_scene_port.calls == []
    assert [
        action["child_ref"]
        for action in restored_actions
        if action["action_kind"] in {"build_scene", "query_scene"}
    ] == scene_refs
    assert all(action["action_kind"] != "commit" for action in restored_actions)
    await database.close()


@pytest.mark.asyncio
async def test_missing_scene_selection_evidence_fails_before_planner(tmp_path):
    database = SqliteDatabase(tmp_path / "auto-dimension-no-evidence.sqlite")
    await database.open()
    write_port = WritePort()
    scene_port = AutoDimensionScenePort(evidence_entity_ids=["other-entity"])
    service, repository = _service(database, write_port, scene_port)

    started = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="mechanical.auto-dimension-overall",
        version="1.1.0",
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
        idempotency_key="auto-dimension-missing-evidence",
        scopes=("autocad.read", "autocad.write"),
    )

    assert started["state"] == "needs_attention"
    assert write_port.calls == []
    actions = await repository.list_actions("owner-a", started["run_id"])
    assert [action["action_kind"] for action in actions] == [
        "build_scene",
        "query_scene",
    ]
    assert all(action["action_kind"] != "preview" for action in actions)
    assert all(action["action_kind"] != "commit" for action in actions)
    await database.close()
