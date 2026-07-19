import asyncio
import json

import pytest

from autocad_mcp.dimension_plans import (
    DimensionPlanConflictError,
    DimensionPlanError,
    DimensionPlanStore,
)


def _candidate(kind="linear", *, base=(0, -10)):
    return {
        "kind": kind,
        "geometry": {"p1": [0, 0], "p2": [100, 0]},
        "placement": {"base": list(base)},
    }


def test_plan_assigns_stable_preview_labels_and_supports_revision():
    store = DimensionPlanStore()
    plan = store.create(
        [_candidate(), _candidate("diameter", base=(30, 30))],
        profile_name="mechanical_mm",
        target={"part_id": "part_3"},
    )

    assert [item.dimension_id for item in plan.dimensions] == ["D1", "D2"]
    assert plan.dimensions[0].label == "D1"
    assert plan.dimensions[0].to_dict()["p1"] == [0, 0]
    json.dumps(plan.to_dict())
    assert plan.to_dict()["preview_labels"] == [
        {"text": "D1", "anchor": [0, -10]},
        {"text": "D2", "anchor": [30, 30]},
    ]

    revised = store.revise(
        plan.plan_id,
        expected_revision=1,
        remove_ids=["D1"],
        placement_overrides={"D2": {"base": [30, 45]}},
        add_candidates=[_candidate("radius", base=(60, 60))],
    )

    assert revised.revision == 2
    assert [item.dimension_id for item in revised.dimensions] == ["D2", "D3"]
    assert revised.dimensions[0].preview_label["anchor"] == [30, 45]


def test_plan_accepts_flat_backend_fields_for_all_supported_kinds():
    store = DimensionPlanStore()
    candidates = [
        {
            "kind": kind,
            "entity_id": f"E{index}",
            "p1": [0, 0],
            "p2": [10, 0],
            "base": [0, -5],
            "angle": 0,
            "text": "custom" if kind == "leader" else None,
        }
        for index, kind in enumerate(
            ("linear", "diameter", "radius", "center", "leader"), start=1
        )
    ]

    plan = store.create(candidates, profile_name="mechanical_mm")

    assert [item.kind for item in plan.dimensions] == [
        "linear",
        "diameter",
        "radius",
        "center",
        "leader",
    ]
    assert plan.dimensions[-1].to_dict()["text"] == "custom"


def test_stale_revision_or_revision_after_commit_is_rejected():
    store = DimensionPlanStore()
    plan = store.create([_candidate()], profile_name="iso_simple")
    store.revise(plan.plan_id, expected_revision=1, add_candidates=[_candidate()])

    with pytest.raises(DimensionPlanConflictError, match="revision is 2"):
        store.revise(plan.plan_id, expected_revision=1, remove_ids=["D1"])


@pytest.mark.asyncio
async def test_commit_is_whole_plan_serialized_and_idempotent():
    store = DimensionPlanStore()
    plan = store.create([_candidate(), _candidate()], profile_name="mechanical_mm")
    calls = []

    async def executor(plan_to_commit):
        calls.append([item.dimension_id for item in plan_to_commit.dimensions])
        await asyncio.sleep(0)
        return {"undo_group": "single", "dimensions_created": 2}

    first, retry = await asyncio.gather(
        store.commit(plan.plan_id, executor),
        store.commit(plan.plan_id, executor),
    )

    assert calls == [["D1", "D2"]]
    assert first.status == retry.status == "committed"
    assert retry.commit_result["undo_group"] == "single"
    with pytest.raises(DimensionPlanConflictError, match="Only a draft"):
        store.revise(plan.plan_id, expected_revision=1, remove_ids=["D1"])


@pytest.mark.asyncio
async def test_failed_commit_returns_plan_to_draft_for_safe_retry():
    store = DimensionPlanStore()
    plan = store.create([_candidate()], profile_name="mechanical_mm")

    async def fail(_plan):
        raise RuntimeError("AutoCAD unavailable")

    with pytest.raises(RuntimeError, match="unavailable"):
        await store.commit(plan.plan_id, fail)

    assert store.get(plan.plan_id).status == "draft"


@pytest.mark.asyncio
async def test_commit_rejects_stale_approval_revision():
    store = DimensionPlanStore()
    plan = store.create([_candidate()], profile_name="mechanical_mm")
    store.revise(plan.plan_id, expected_revision=1, add_candidates=[_candidate()])

    with pytest.raises(DimensionPlanConflictError, match="revision is 2"):
        await store.commit(plan.plan_id, lambda _plan: {}, expected_revision=1)


def test_plan_store_never_evicts_an_unapproved_draft():
    store = DimensionPlanStore(max_plans=2, max_dimensions=2)
    first = store.create([_candidate()], profile_name="mechanical_mm")
    second = store.create([_candidate()], profile_name="mechanical_mm")

    with pytest.raises(DimensionPlanError, match="active drafts"):
        store.create([_candidate()], profile_name="mechanical_mm")

    assert store.plan_ids() == {first.plan_id, second.plan_id}


@pytest.mark.asyncio
async def test_plan_store_evicts_oldest_completed_plan_before_a_draft():
    store = DimensionPlanStore(max_plans=2, max_dimensions=2)
    completed = store.create([_candidate()], profile_name="mechanical_mm")
    draft = store.create([_candidate()], profile_name="mechanical_mm")
    await store.commit(completed.plan_id, lambda _plan: {}, expected_revision=1)

    new_plan = store.create([_candidate()], profile_name="mechanical_mm")

    assert store.plan_ids() == {draft.plan_id, new_plan.plan_id}
