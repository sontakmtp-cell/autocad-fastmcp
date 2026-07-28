import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages" / "cad_core" / "src"), str(ROOT / "packages" / "contracts" / "src")]

from cad_core.phase9_workflows import (  # noqa: E402
    PLANNER_REGISTRY_DIGEST,
    TEMPLATE_REGISTRY_DIGEST,
    Phase9PlannerError,
    audit_cleanup,
    plan_auto_dimension_overall,
    render_plate_hole_pattern,
    render_template,
    run_planner,
)


CONTEXT = {"run_id": "run_1", "device_id": "device_1", "source_snapshot_id": "snapshot_1", "document_id": "document_1", "expected_document_revision": "revision_1"}


def test_auto_dimension_is_deterministic_and_exactly_two_create_dimensions():
    entities = [
        {"entity_id": "line_1", "entity_type": "LINE", "layer": "PART", "geometry": {"start": [0, 0], "end": [100, 0]}},
        {"entity_id": "poly_1", "entity_type": "LWPOLYLINE", "layer": "PART", "geometry": {"points": [[0, 0], [0, 60], [100, 60]]}},
    ]
    first = plan_auto_dimension_overall(CONTEXT, entities, {"offset": 10, "target_layer": "DIM"})
    second = run_planner("mechanical.auto-dimension-overall/1", CONTEXT, entities, {"offset": 10, "target_layer": "DIM"})
    assert first == second
    assert [op["kind"] for op in first["operations"]] == ["ensure_layer", "create_dimension_linear", "create_dimension_linear"]
    assert first["semantic_digest"].startswith("sha256:")
    assert PLANNER_REGISTRY_DIGEST.startswith("sha256:")


def test_auto_dimension_rejects_implicit_or_unsupported_selection():
    with pytest.raises(Phase9PlannerError, match="only explicit"):
        plan_auto_dimension_overall(CONTEXT, [{"entity_id": "circle", "type": "CIRCLE", "points": [[0, 0], [1, 1]]}], {})


def test_cleanup_audit_is_read_only_and_reports_bounded_candidates():
    report = audit_cleanup([
        {"entity_id": "a", "type": "LINE", "geometry": {"start": [0, 0], "end": [1, 0]}},
        {"entity_id": "b", "type": "LINE", "geometry": {"start": [0, 0], "end": [1, 0]}},
        {"entity_id": "c", "type": "LINE", "geometry": {"start": [2, 2], "end": [2, 2]}},
        {"entity_id": "d", "type": "CUSTOM", "geometry": {}},
    ])
    assert report["effect"] == "none"
    assert report["duplicate_groups"] == [["a", "b"]]
    assert report["degenerate_entity_ids"] == ["c"]
    assert report["unsupported_entity_summary"] == {"CUSTOM": 1}
    assert "delete" not in repr(report).lower()


def test_plate_pattern_is_create_only_with_bounded_rectangular_repeat():
    program = render_plate_hole_pattern(CONTEXT, {"width": 100, "height": 60, "hole_diameter": 8, "rows": 2, "columns": 3, "margin_x": 15, "margin_y": 10, "layer": "MECH", "include_overall_dimensions": True})
    kinds = [op["kind"] for op in program["operations"]]
    assert kinds == ["ensure_layer", "create_rectangle", "create_circle", "create_dimension_linear", "create_dimension_linear"]
    assert program["operations"][2]["repeat"]["kind"] == "rectangular"
    assert not any("target_ref" in operation or operation["kind"] in {"move_entity", "copy_entity", "offset_entity"} for operation in program["operations"])
    assert render_template("mechanical.plate-hole-pattern/1", CONTEXT, {"width": 100, "height": 60, "hole_diameter": 8, "rows": 2, "columns": 3, "margin_x": 15, "margin_y": 10, "layer": "MECH", "include_overall_dimensions": True}) == program
    assert TEMPLATE_REGISTRY_DIGEST.startswith("sha256:")


def test_plate_pattern_rejects_unbounded_repeat_and_invalid_margins():
    base = {"width": 100, "height": 60, "hole_diameter": 8, "rows": 2, "columns": 3, "margin_x": 15, "margin_y": 10}
    with pytest.raises(Phase9PlannerError, match="rows"):
        render_plate_hole_pattern(CONTEXT, {**base, "rows": 33})
    with pytest.raises(Phase9PlannerError, match="margins"):
        render_plate_hole_pattern(CONTEXT, {**base, "margin_x": 50})
