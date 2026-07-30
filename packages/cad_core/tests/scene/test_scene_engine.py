from __future__ import annotations

import math
import random
from dataclasses import replace

import pytest

from cad_core.scene import (
    SceneBudgetExceeded,
    SceneBudgets,
    SceneBuildContext,
    build_scene,
    project_entities,
)
from cad_core.scene import engine as scene_engine
from cad_core.scene.spatial_index import build_candidate_index


def context() -> SceneBuildContext:
    return SceneBuildContext(
        "snapshot-1",
        "device-1",
        "document-1",
        "42",
        source_capabilities=(
            "entity.geometry.arc/1",
            "entity.geometry.circle/1",
            "entity.geometry.line/1",
            "entity.geometry.polyline/1",
        ),
        drawing_units="mm",
    )


def line(entity_id, start, end, *, layer="0"):
    return {
        "entity_id": entity_id,
        "entity_type": "LINE",
        "layer": layer,
        "space": "model",
        "geometry": {"start": start, "end": end},
        "fingerprint": f"sha256:{entity_id:0>64}",
        "source_runtime": "fixture",
        "source_capabilities": ["entity.geometry.line/1"],
    }


def circle(entity_id, center, radius):
    return {
        "entity_id": entity_id,
        "entity_type": "CIRCLE",
        "layer": "0",
        "space": "model",
        "geometry": {
            "center": center,
            "radius": radius,
            "normal": [0, 0, 1],
        },
        "fingerprint": f"sha256:{entity_id:0>64}",
        "source_runtime": "fixture",
        "source_capabilities": ["entity.geometry.circle/1"],
    }


def arc(entity_id, center, radius, start, end):
    return {
        "entity_id": entity_id,
        "entity_type": "ARC",
        "layer": "0",
        "space": "model",
        "geometry": {
            "center": center,
            "radius": radius,
            "start_angle_radians": start,
            "end_angle_radians": end,
            "normal": [0, 0, 1],
        },
        "fingerprint": f"sha256:{entity_id:0>64}",
        "source_runtime": "fixture",
        "source_capabilities": ["entity.geometry.arc/1"],
    }


def polyline(entity_id, vertices, *, closed=True):
    return {
        "entity_id": entity_id,
        "entity_type": "LWPOLYLINE",
        "layer": "0",
        "space": "model",
        "geometry": {
            "vertices": [
                {"x": value[0], "y": value[1], "bulge": value[2] if len(value) > 2 else 0}
                for value in vertices
            ],
            "closed": closed,
            "elevation": 0,
            "normal": [0, 0, 1],
        },
        "fingerprint": f"sha256:{entity_id:0>64}",
        "source_runtime": "fixture",
        "source_capabilities": ["entity.geometry.polyline/1"],
    }


def test_scene_digest_ids_and_semantics_are_entity_order_invariant():
    entities = [
        polyline("P", [(0, 0), (100, 0), (100, 60), (0, 60)]),
        circle("C1", (20, 20), 5),
        circle("C2", (80, 20), 5),
        circle("C3", (20, 20), 9),
        line("CL", (0, 20), (100, 20), layer="CENTER"),
    ]
    first = build_scene(entities, context())
    shuffled = list(entities)
    random.Random(7).shuffle(shuffled)
    second = build_scene(shuffled, context())
    reversed_capabilities = build_scene(
        shuffled,
        replace(
            context(),
            source_capabilities=tuple(reversed(context().source_capabilities)),
        ),
    )

    assert first.source_digest == second.source_digest
    assert first.source_digest == reversed_capabilities.source_digest
    assert first.scene_digest == second.scene_digest
    assert first.stats.projected_bytes == second.stats.projected_bytes
    assert first.stats.scene_bytes == second.stats.scene_bytes
    assert [item.node_id for item in first.nodes] == [item.node_id for item in second.nodes]
    assert [item.relation_id for item in first.relations] == [
        item.relation_id for item in second.relations
    ]
    feature_types = {item.feature_type for item in first.features}
    assert {
        "part",
        "hole",
        "repeated_hole_pattern",
        "concentric_group",
        "centerline_candidate",
    }.issubset(feature_types)
    relation_types = {item.relation_type for item in first.relations}
    assert {"inside", "contains", "concentric"}.issubset(relation_types)
    assert first.to_dict()["stats"]["source_entities"] == 5


def test_required_supported_relations_contours_and_cleanup_issues():
    entities = [
        line("L1", (0, 0), (10, 0)),
        line("L2", (10, 0), (10, 10)),
        line("L3", (10, 10), (0, 10)),
        line("L4", (0, 10), (0, 0)),
        line("BASE", (0, 15), (10, 15)),
        line("DUP", (10, 15), (0, 15)),
        line("CROSS1", (4, -1), (4, 11)),
        line("CROSS2", (2, 5), (8, 5)),
        line("OPEN1", (20, 0), (25, 0)),
        line("OPEN2", (25, 0), (30, 3)),
        polyline("BOW", [(40, 0), (50, 10), (40, 10), (50, 0)]),
        circle("T1", (60, 0), 5),
        circle("T2", (70, 0), 5),
    ]
    result = build_scene(entities, context())
    relation_types = {item.relation_type for item in result.relations}
    issue_codes = {item.code for item in result.issues}

    assert {
        "connected_endpoint",
        "touch",
        "intersect",
        "overlap",
        "duplicate_geometry",
        "parallel",
        "perpendicular",
        "aligned",
    }.issubset(relation_types)
    assert any(contour.kind == "line_loop" for contour in result.contours)
    assert {"duplicate_geometry", "open_contour", "self_intersection"}.issubset(
        issue_codes
    )
    assert all(issue.write_authority is False for issue in result.issues)


def test_validated_arc_line_and_bulged_polyline_slots():
    entities = [
        arc("A1", (0, 0), 5, math.pi / 2, math.pi * 1.5),
        arc("A2", (20, 0), 5, math.pi * 1.5, math.pi / 2),
        line("TOP", (0, 5), (20, 5)),
        line("BOTTOM", (0, -5), (20, -5)),
        polyline(
            "PS",
            [
                (40, -5, 0),
                (60, -5, 1),
                (60, 5, 0),
                (40, 5, 1),
            ],
        ),
    ]
    result = build_scene(entities, context())
    slots = [item for item in result.features if item.feature_type == "slot"]

    assert len(slots) == 2
    assert any(len(item.source_node_ids) == 4 for item in slots)
    assert all(item.confidence == 1 for item in slots)
    assert all(item.evidence_strength == "derived_exact" for item in slots)


def test_arc_carrier_circle_does_not_create_false_intersection():
    result = build_scene(
        [
            arc("A", (0, 0), 10, 0, math.pi / 4),
            line("L", (-20, 0), (-5, 0)),
        ],
        context(),
    )
    assert not {
        "touch",
        "intersect",
    }.intersection(item.relation_type for item in result.relations)


def test_reversed_polyline_duplicate_is_canonical_and_partial_source_is_explicit():
    forward = polyline("P1", [(0, 0), (10, 0), (10, 10), (0, 10)])
    reverse = polyline("P2", [(0, 0), (0, 10), (10, 10), (10, 0)])
    unavailable = {
        "entity_id": "U",
        "entity_type": "ELLIPSE",
        "geometry_status": "unsupported",
        "geometry_reason": "entity_type_unsupported",
    }
    result = build_scene([forward, reverse, unavailable], context())

    assert result.complete is False
    assert any(
        item.relation_type == "duplicate_geometry" for item in result.relations
    )
    assert "unsupported_geometry" in {item.code for item in result.issues}


def test_inconsistent_equal_spacing_hole_row_is_read_only_issue():
    result = build_scene(
        [
            polyline("P", [(0, 0), (60, 0), (60, 30), (0, 30)]),
            circle("C1", (10, 15), 2),
            circle("C2", (30, 15), 2),
            circle("C3", (50, 15), 2.5),
        ],
        context(),
    )
    issue = next(
        item
        for item in result.issues
        if item.code == "inconsistent_repeated_feature"
    )
    assert issue.write_authority is False


def test_topology_is_preserved_by_translation_and_rotation():
    source = [
        line("A", (0, 0), (10, 0)),
        line("B", (10, 0), (10, 10)),
        line("C", (10, 10), (0, 10)),
        line("D", (0, 10), (0, 0)),
    ]
    translated = [
        line(item["entity_id"], [v + d for v, d in zip(item["geometry"]["start"], (100, -50))], [v + d for v, d in zip(item["geometry"]["end"], (100, -50))])
        for item in source
    ]
    rotated = [
        line(
            item["entity_id"],
            (-item["geometry"]["start"][1], item["geometry"]["start"][0]),
            (-item["geometry"]["end"][1], item["geometry"]["end"][0]),
        )
        for item in source
    ]

    relation_sets = [
        sorted(item.relation_type for item in build_scene(value, context()).relations)
        for value in (source, translated, rotated)
    ]
    assert relation_sets[0] == relation_sets[1] == relation_sets[2]


def test_dense_overlap_fails_safely_and_large_grid_is_bounded():
    dense = [circle(f"C{index}", (0, 0), 1) for index in range(300)]
    with pytest.raises(SceneBudgetExceeded, match="budget"):
        build_scene(dense, context())

    grid = [
        circle(f"G{index}", (index % 100, index // 100), 0.1)
        for index in range(5_000)
    ]
    nodes = project_entities(grid)
    index = build_candidate_index(nodes, SceneBudgets())
    assert index.cell_count <= 50_000
    assert len(index.pairs) <= 250_000


def test_budget_values_cannot_exceed_server_caps():
    with pytest.raises(ValueError, match="hard cap"):
        SceneBudgets(max_source_entities=10_001)
    with pytest.raises(ValueError, match="positive"):
        SceneBudgets(max_build_seconds=math.inf)
    with pytest.raises(SceneBudgetExceeded, match="projected byte"):
        build_scene(
            [line("LONG", (0, 0), (1, 1), layer="X" * 255)],
            context(),
            budgets=SceneBudgets(max_projected_bytes=1),
        )
    with pytest.raises(SceneBudgetExceeded, match="serialized byte"):
        build_scene(
            [line("L", (0, 0), (1, 1))],
            context(),
            budgets=SceneBudgets(max_scene_bytes=1),
        )


def test_build_deadline_is_checked_between_stages(monkeypatch):
    clock = iter((10.0, 11.0))
    monkeypatch.setattr(scene_engine.time, "monotonic", lambda: next(clock))

    with pytest.raises(SceneBudgetExceeded, match="build time"):
        build_scene(
            [line("L", (0, 0), (1, 1))],
            context(),
            budgets=SceneBudgets(max_build_seconds=0.1),
        )
