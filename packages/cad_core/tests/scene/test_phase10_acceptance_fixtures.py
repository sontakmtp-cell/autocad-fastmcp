from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

from cad_core.scene import SceneBuildContext, ToleranceProfile, build_scene


FIXTURES = Path(__file__).with_name("fixtures")
CONTEXT = SceneBuildContext(
    "phase10-headless",
    "device-headless",
    "document-headless",
    "revision-headless",
    source_capabilities=(
        "entity.geometry.arc/1",
        "entity.geometry.circle/1",
        "entity.geometry.line/1",
        "entity.geometry.polyline/1",
    ),
    drawing_units="mm",
)


def load_fixture(name: str) -> dict:
    return json.loads(
        (FIXTURES / f"phase10-drawing-{name.lower()}.json").read_text(
            encoding="utf-8"
        )
    )


def source_ids(scene, node_ids) -> set[str]:
    by_node = {node.node_id: node.source_entity_id for node in scene.nodes}
    return {by_node[node_id] for node_id in node_ids}


def test_drawing_a_plate_hole_pattern_positive_and_negative_evidence():
    fixture = load_fixture("a")
    scene = build_scene(fixture["entities"], CONTEXT)
    holes = [item for item in scene.features if item.feature_type == "hole"]
    patterns = [
        item for item in scene.features if item.feature_type == "repeated_hole_pattern"
    ]
    parts = [item for item in scene.features if item.feature_type == "part"]

    assert scene.complete
    assert len(scene.contours) == 1
    assert len(parts) == 1
    assert parts[0].evidence_strength == "bounded_heuristic"
    assert parts[0].limitations == ("part_semantics_not_proven",)
    assert len(holes) == 4
    assert all(item.evidence_strength == "derived_exact" for item in holes)
    assert {next(iter(source_ids(scene, item.source_node_ids))) for item in holes} == {
        "A-H1",
        "A-H2",
        "A-H3",
        "A-H4",
    }
    assert len(patterns) == 1
    assert dict(patterns[0].geometry_summary)["quantity"] == 4
    assert patterns[0].feature_id == fixture["stable_ids"]["repeated_pattern_feature"]
    assert "A-OUTSIDE" not in source_ids(scene, parts[0].source_node_ids)
    assert all(
        "A-OUTSIDE" not in source_ids(scene, relation.source_node_ids)
        for relation in scene.relations
        if relation.relation_type in {"inside", "contains"}
    )
    plate = next(node for node in scene.nodes if node.source_entity_id == "A-PLATE")
    assert plate.node_id == fixture["stable_ids"]["plate_node"]


def test_drawing_b_exact_slots_concentric_group_and_non_slot_control():
    fixture = load_fixture("b")
    scene = build_scene(fixture["entities"], CONTEXT)
    slots = [item for item in scene.features if item.feature_type == "slot"]
    concentric = [
        item for item in scene.features if item.feature_type == "concentric_group"
    ]

    assert {node.entity_type for node in scene.nodes} == {
        "ARC",
        "CIRCLE",
        "LINE",
        "LWPOLYLINE",
    }
    assert all(node.geometry_status == "exact" for node in scene.nodes)
    assert {item.feature_id for item in slots} == {
        fixture["stable_ids"]["arc_slot_feature"],
        fixture["stable_ids"]["polyline_slot_feature"],
    }
    assert all(
        item.evidence_strength == "derived_exact"
        and item.confidence == 1
        and not item.limitations
        for item in slots
    )
    assert all(
        "B-NOT-SLOT" not in source_ids(scene, item.source_node_ids)
        for item in slots
    )
    assert len(concentric) == 1
    assert concentric[0].feature_id == fixture["stable_ids"]["concentric_feature"]
    assert source_ids(scene, concentric[0].source_node_ids) == {
        "B-CONCENTRIC-1",
        "B-CONCENTRIC-2",
    }


def test_drawing_c_cleanup_issues_are_exactly_scoped_and_read_only():
    fixture = load_fixture("c")
    scene = build_scene(fixture["entities"], CONTEXT)
    duplicate = next(
        item for item in scene.relations if item.relation_type == "duplicate_geometry"
    )
    duplicate_issue = next(
        item for item in scene.issues if item.code == "duplicate_geometry"
    )
    zero_line_issue = next(
        item
        for item in scene.issues
        if item.code == "degenerate_geometry"
        and source_ids(scene, item.source_node_ids) == {"C-ZERO-LINE"}
    )

    assert duplicate.relation_id == fixture["stable_ids"]["duplicate_relation"]
    assert source_ids(scene, duplicate.source_node_ids) == {"C-DUP-A", "C-DUP-B"}
    assert "C-DISTINCT" not in source_ids(scene, duplicate.source_node_ids)
    assert duplicate_issue.issue_id == fixture["stable_ids"]["duplicate_issue"]
    assert zero_line_issue.issue_id == fixture["stable_ids"]["zero_line_issue"]
    assert {"duplicate_geometry", "degenerate_geometry", "open_contour"} <= {
        item.code for item in scene.issues
    }
    assert all(item.write_authority is False for item in scene.issues)


def test_drawing_a_radius_tolerance_accepts_boundary_pair_only():
    fixture = load_fixture("a")
    entities = copy.deepcopy(fixture["entities"])
    radii = {
        "A-H1": 5.0049,
        "A-H2": 5.0051,
        "A-H3": 5.02,
        "A-H4": 5.04,
    }
    for entity in entities:
        if entity["entity_id"] in radii:
            entity["geometry"]["radius"] = radii[entity["entity_id"]]
    tolerance = ToleranceProfile(
        "mechanical-2d/1",
        "mm",
        1e-6,
        1e-9,
        1e-6,
        0.01,
        0.01,
        0.01,
        0.01,
    )

    scene = build_scene(entities, CONTEXT, tolerance=tolerance)
    patterns = [
        item for item in scene.features if item.feature_type == "repeated_hole_pattern"
    ]

    assert len(patterns) == 1
    assert source_ids(scene, patterns[0].source_node_ids) == {"A-H1", "A-H2"}


@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_fixture_scene_digest_and_ids_are_stable_under_shuffle(name):
    fixture = load_fixture(name)
    shuffled = copy.deepcopy(fixture["entities"])
    random.Random(10).shuffle(shuffled)
    first = build_scene(fixture["entities"], CONTEXT)
    second = build_scene(shuffled, CONTEXT)

    assert first.scene_digest == fixture["stable_ids"]["scene_digest"]
    assert second.scene_digest == first.scene_digest
    assert [item.node_id for item in second.nodes] == [
        item.node_id for item in first.nodes
    ]
    assert [item.relation_id for item in second.relations] == [
        item.relation_id for item in first.relations
    ]
    assert [item.feature_id for item in second.features] == [
        item.feature_id for item in first.features
    ]
    assert [item.issue_id for item in second.issues] == [
        item.issue_id for item in first.issues
    ]
