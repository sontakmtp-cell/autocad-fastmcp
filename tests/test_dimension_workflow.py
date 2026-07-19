import base64

import pytest

from autocad_mcp.autodim import AutoDimensionOptions
from autocad_mcp.backends.ezdxf_backend import EzdxfBackend
from autocad_mcp.dimension_plans import DimensionPlanStore
from autocad_mcp.dimension_profiles import DimensionProfileStore
from autocad_mcp.dimension_workflow import (
    _dimension_to_lisp_data,
    build_dimension_candidates,
    collect_dimension_records,
    commit_dimension_plan,
    render_plan_preview,
)
from autocad_mcp.part_detection import detect_parts, select_records


async def _two_part_drawing():
    backend = EzdxfBackend()
    await backend.initialize()
    await backend.create_rectangle(0, 0, 100, 60, layer="PART")
    await backend.create_circle(25, 30, 5, layer="PART")
    await backend.create_circle(75, 30, 5, layer="PART")
    await backend.create_rectangle(200, 0, 240, 20, layer="PART")
    return backend


@pytest.mark.asyncio
async def test_preview_plan_targets_one_part_without_mutating_drawing():
    backend = await _two_part_drawing()
    options = AutoDimensionOptions.from_data({"source_layers": ["PART"]})
    profile = DimensionProfileStore().get("mechanical_mm")
    records = await collect_dimension_records(
        backend,
        dimension_layer=profile.layer,
        source_layers=options.source_layers,
    )
    parts = detect_parts(records)
    selected = select_records(records, {"target_part_id": "part_1"}, parts=parts)
    before = len(list(backend._msp))

    candidates, analysis = build_dimension_candidates(
        selected,
        options=options,
        profile=profile,
    )
    plan = DimensionPlanStore().create(candidates, profile_name=profile.name)
    preview = render_plan_preview(selected, plan)

    assert len(parts) == 2
    assert analysis["extents"] == {"min": [0.0, 0.0], "max": [100.0, 60.0]}
    assert len(list(backend._msp)) == before
    assert base64.b64decode(preview).startswith(b"\x89PNG")
    assert [item.dimension_id for item in plan.dimensions] == [
        f"D{index}" for index in range(1, len(plan.dimensions) + 1)
    ]


@pytest.mark.asyncio
async def test_commit_creates_grouped_hole_notation_and_dimensions():
    backend = await _two_part_drawing()
    options = AutoDimensionOptions.from_data({"source_layers": ["PART"]})
    profile = DimensionProfileStore().get("mechanical_mm")
    records = await collect_dimension_records(
        backend,
        dimension_layer=profile.layer,
        source_layers=options.source_layers,
    )
    selected = select_records(records, {"target_part_id": "part_1"})
    candidates, _ = build_dimension_candidates(selected, options=options, profile=profile)
    plan = DimensionPlanStore().create(candidates, profile_name=profile.name)

    result = await commit_dimension_plan(backend, plan, profile)

    assert result["instructions_failed"] == 0
    assert result["dimensions_created"] == len(plan.dimensions)
    assert sum(item.kind == "diameter" for item in plan.dimensions) == 1
    grouped = next(item for item in plan.dimensions if item.kind == "diameter")
    assert grouped.text == "2x %%c<>"
    assert len(grouped.metadata["entity_ids"]) == 2
    assert not any(
        str(entity.dxf.get("layer", "")).startswith("__MCP_DIM_STAGE_")
        for entity in backend._msp
    )
    for dimension in backend._msp.query("DIMENSION"):
        assert all(
            not str(entity.dxf.get("layer", "")).startswith("__MCP_DIM_STAGE_")
            for entity in dimension.virtual_entities()
        )


def test_file_ipc_plan_serialization_is_data_only_and_ascii():
    store = DimensionPlanStore()
    plan = store.create(
        [
            {
                "kind": "linear",
                "geometry": {"p1": [0, 0], "p2": [100, 0]},
                "placement": {"base": [0, -10], "angle": 0},
                "text": "4x %%c<>",
            }
        ],
        profile_name="mechanical_mm",
    )

    serialized = _dimension_to_lisp_data(plan.dimensions[0])

    serialized.encode("ascii")
    assert serialized.startswith('("linear"')
    assert "4x %%c<>" in serialized
    assert "(eval" not in serialized.lower()
