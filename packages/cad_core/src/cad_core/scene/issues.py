"""Read-only deterministic scene issue detectors."""

from __future__ import annotations

import math

from .canonical import stable_id
from .contours import TOPOLOGY_RELATIONS
from .contours import contour_polygon
from .models import (
    CircleGeometry,
    ArcGeometry,
    LineGeometry,
    PolylineGeometry,
    SceneComponent,
    SceneContour,
    SceneIssue,
    SceneNode,
    SceneRelation,
)
from .tolerances import SceneBudgetExceeded, SceneBudgets, ToleranceProfile


def detect_issues(
    nodes: tuple[SceneNode, ...],
    relations: tuple[SceneRelation, ...],
    contours: tuple[SceneContour, ...],
    components: tuple[SceneComponent, ...],
    tolerance: ToleranceProfile,
    budgets: SceneBudgets,
) -> tuple[SceneIssue, ...]:
    issues: list[SceneIssue] = []
    by_id = {node.node_id: node for node in nodes}
    for node in nodes:
        if node.geometry_status != "exact":
            code = (
                "truncated_geometry"
                if node.geometry_status == "truncated"
                else "invalid_geometry"
                if node.geometry_status == "invalid"
                else "unsupported_geometry"
            )
            issues.append(
                _issue(
                    code,
                    "warning" if node.geometry_status != "invalid" else "error",
                    (node.node_id,),
                    (),
                    (node.geometry_reason or node.geometry_status,),
                    "inspect_source_geometry",
                    by_id,
                )
            )
        elif isinstance(node.geometry, LineGeometry) and math.dist(
            node.geometry.start, node.geometry.end
        ) <= tolerance.endpoint:
            issues.append(
                _issue(
                    "degenerate_geometry",
                    "error",
                    (node.node_id,),
                    (),
                    ("zero_length_line",),
                    "review_degenerate_entity",
                    by_id,
                )
            )
        elif isinstance(node.geometry, CircleGeometry) and node.geometry.radius <= tolerance.radius:
            issues.append(
                _issue(
                    "degenerate_geometry",
                    "error",
                    (node.node_id,),
                    (),
                    ("near_zero_radius",),
                    "review_degenerate_entity",
                    by_id,
                )
            )
        elif isinstance(node.geometry, ArcGeometry) and (
            node.geometry.end_angle_radians - node.geometry.start_angle_radians
        ) % math.tau <= tolerance.angular:
            issues.append(
                _issue(
                    "degenerate_geometry",
                    "error",
                    (node.node_id,),
                    (),
                    ("near_zero_arc_sweep",),
                    "review_degenerate_entity",
                    by_id,
                )
            )
        if isinstance(node.geometry, PolylineGeometry) and node.geometry.closed:
            if _self_intersects(node.geometry, tolerance.endpoint, budgets.max_relation_candidates):
                issues.append(
                    _issue(
                        "self_intersection",
                        "error",
                        (node.node_id,),
                        (),
                        ("non_adjacent_segments_intersect",),
                        "review_contour",
                        by_id,
                    )
                )

    for relation in relations:
        if relation.relation_type == "duplicate_geometry":
            issues.append(
                _issue(
                    "duplicate_geometry",
                    "warning",
                    relation.source_node_ids,
                    (relation.relation_id,),
                    ("exact_geometry_duplicate",),
                    "review_duplicate",
                    by_id,
                )
            )

    contour_nodes = {
        node_id for contour in contours for node_id in contour.source_node_ids
    }
    topology_nodes = {
        node_id
        for relation in relations
        if relation.relation_type in TOPOLOGY_RELATIONS
        for node_id in relation.source_node_ids
    }
    for component in components:
        line_nodes = tuple(
            node_id
            for node_id in component.source_node_ids
            if isinstance(by_id[node_id].geometry, LineGeometry)
        )
        if (
            line_nodes
            and topology_nodes.intersection(line_nodes)
            and not set(line_nodes).issubset(contour_nodes)
        ):
            issues.append(
                _issue(
                    "open_contour",
                    "warning",
                    line_nodes,
                    (),
                    ("connected_line_component_is_open",),
                    "close_or_review_contour",
                    by_id,
                )
            )

    for contour in contours:
        polygon = contour_polygon(contour, by_id, tolerance.endpoint)
        if polygon is None:
            continue
        circles = [
            node
            for node in nodes
            if isinstance(node.geometry, CircleGeometry)
            and _point_in_polygon(node.geometry.center, polygon)
        ]
        mismatch = _inconsistent_row(circles, tolerance)
        if mismatch:
            issues.append(
                _issue(
                    "inconsistent_repeated_feature",
                    "warning",
                    tuple(node.node_id for node in mismatch),
                    (),
                    ("collinear_equal_spacing_radius_mismatch",),
                    "review_repeated_feature",
                    by_id,
                )
            )

    unique = {item.issue_id: item for item in issues}
    if len(unique) > budgets.max_issues:
        raise SceneBudgetExceeded("scene exceeds issue budget")
    return tuple(sorted(unique.values(), key=lambda item: item.issue_id))


def _issue(
    code: str,
    severity: str,
    node_ids: tuple[str, ...],
    relation_ids: tuple[str, ...],
    evidence: tuple[str, ...],
    action: str,
    by_id: dict[str, SceneNode],
) -> SceneIssue:
    nodes = tuple(sorted(set(node_ids)))
    relations = tuple(sorted(set(relation_ids)))
    evidence_ids = [
        stable_id(
            "evd",
            "cad.scene-evidence-id/1",
            {
                "evidence_type": item,
                "source_entity_ids": sorted(
                    by_id[node_id].source_entity_id for node_id in nodes
                ),
                "algorithm_version": "scene-issues/1",
            },
        )
        for item in sorted(evidence)
    ]
    issue_id = stable_id(
        "iss",
        "cad.scene-issue-id/1",
        {
            "issue_code": code,
            "source_evidence_ids": evidence_ids,
            "detector_version": "scene-issues/1",
        },
    )
    return SceneIssue(
        issue_id,
        code,
        severity,  # type: ignore[arg-type]
        nodes,
        relations,
        f"scene.issue.{code}",
        tuple(sorted(evidence)),
        1.0,
        action,
    )


def _self_intersects(
    geometry: PolylineGeometry,
    tolerance: float,
    budget: int,
) -> bool:
    if any(vertex.bulge for vertex in geometry.vertices):
        return False
    points = [vertex.xy for vertex in geometry.vertices]
    segments = list(zip(points, points[1:] + points[:1]))
    checked = 0
    for index, first in enumerate(segments):
        for other_index in range(index + 1, len(segments)):
            if other_index in {index + 1, len(segments) - 1 if index == 0 else -1}:
                continue
            checked += 1
            if checked > budget:
                raise SceneBudgetExceeded("polyline self-intersection budget exceeded")
            if _proper_intersection(first, segments[other_index], tolerance):
                return True
    return False


def _proper_intersection(first, second, tolerance: float) -> bool:
    values = (
        _cross(first[0], first[1], second[0]),
        _cross(first[0], first[1], second[1]),
        _cross(second[0], second[1], first[0]),
        _cross(second[0], second[1], first[1]),
    )
    return (
        values[0] * values[1] < -(tolerance * tolerance)
        and values[2] * values[3] < -(tolerance * tolerance)
    )


def _cross(start, end, point) -> float:
    return (end[0] - start[0]) * (point[1] - start[1]) - (
        end[1] - start[1]
    ) * (point[0] - start[0])


def _inconsistent_row(
    circles: list[SceneNode],
    tolerance: ToleranceProfile,
) -> tuple[SceneNode, ...]:
    if len(circles) < 3:
        return ()
    for axis in (0, 1):
        other = 1 - axis
        ordered = sorted(
            circles,
            key=lambda node: node.geometry.center[axis],  # type: ignore[union-attr]
        )
        centers = [node.geometry.center for node in ordered]  # type: ignore[union-attr]
        if max(point[other] for point in centers) - min(
            point[other] for point in centers
        ) > tolerance.endpoint * 5:
            continue
        gaps = [
            centers[index + 1][axis] - centers[index][axis]
            for index in range(len(centers) - 1)
        ]
        if min(gaps) <= tolerance.endpoint or max(gaps) - min(gaps) > tolerance.endpoint * 5:
            continue
        radii = [node.geometry.radius for node in ordered]  # type: ignore[union-attr]
        if max(radii) - min(radii) > tolerance.radius:
            return tuple(ordered)
    return ()


def _point_in_polygon(point, polygon) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if (current[1] > point[1]) != (previous[1] > point[1]):
            crossing = (
                (previous[0] - current[0])
                * (point[1] - current[1])
                / (previous[1] - current[1])
                + current[0]
            )
            if point[0] < crossing:
                inside = not inside
        previous = current
    return inside
