"""Evidence-backed mechanical feature inference for scene v0."""

from __future__ import annotations

import math
from collections import defaultdict

from .canonical import stable_id
from .contours import contour_polygon
from .models import (
    ArcGeometry,
    CircleGeometry,
    LineGeometry,
    Point,
    PolylineGeometry,
    SceneComponent,
    SceneContour,
    SceneFeature,
    SceneNode,
    SceneRelation,
)
from .projection import bulge_arc
from .relations import arc_endpoints
from .tolerances import SceneBudgetExceeded, SceneBudgets, ToleranceProfile


def infer_features(
    nodes: tuple[SceneNode, ...],
    relations: tuple[SceneRelation, ...],
    contours: tuple[SceneContour, ...],
    components: tuple[SceneComponent, ...],
    tolerance: ToleranceProfile,
    budgets: SceneBudgets,
) -> tuple[SceneFeature, ...]:
    by_id = {node.node_id: node for node in nodes}
    features: list[SceneFeature] = []
    for contour in contours:
        polygon = contour_polygon(contour, by_id, tolerance.endpoint)
        if polygon is None:
            continue
        holes = [
            node
            for node in nodes
            if isinstance(node.geometry, CircleGeometry)
            and _circle_inside(node.geometry, polygon, tolerance.endpoint)
        ]
        source_ids = tuple(sorted((*contour.source_node_ids, *(item.node_id for item in holes))))
        features.append(
            _feature(
                "part",
                source_ids,
                (),
                ("closed_contour_part_candidate",),
                ("part_semantics_not_proven",),
                (("hole_count", len(holes)),),
                strength="bounded_heuristic",
                confidence=0.75,
                source_entity_ids=_source_entity_ids(source_ids, by_id),
            )
        )
        for hole in holes:
            features.append(
                _feature(
                    "hole",
                    (hole.node_id,),
                    (),
                    ("inside_contour", "exact_circle"),
                    ("depth_thread_and_through_state_unknown",),
                    (("diameter", hole.geometry.radius * 2),),
                    source_entity_ids=(hole.source_entity_id,),
                )
            )
        for group in _radius_groups(holes, tolerance.radius):
            if len(group) < 2:
                continue
            radius = sum(item.geometry.radius for item in group if isinstance(item.geometry, CircleGeometry)) / len(group)
            features.append(
                _feature(
                    "repeated_hole_pattern",
                    tuple(item.node_id for item in group),
                    (),
                    ("inside_contour", "equal_radius"),
                    ("pattern_classification_not_claimed",),
                    (("quantity", len(group)), ("diameter", radius * 2)),
                    source_entity_ids=tuple(item.source_entity_id for item in group),
                )
            )

    concentric = [item for item in relations if item.relation_type == "concentric"]
    concentric_groups = _relation_groups(concentric)
    for node_ids, relation_ids in concentric_groups:
        if len(node_ids) >= 2:
            features.append(
                _feature(
                    "concentric_group",
                    node_ids,
                    relation_ids,
                    ("exact_centers_within_tolerance",),
                    (),
                    source_entity_ids=_source_entity_ids(node_ids, by_id),
                )
            )

    features.extend(_slot_features(nodes, relations, components, tolerance))
    for node in nodes:
        if isinstance(node.geometry, LineGeometry) and "CENTER" in node.layer.upper():
            features.append(
                _feature(
                    "centerline_candidate",
                    (node.node_id,),
                    (),
                    ("center_layer_name",),
                    ("layer_name_is_untrusted_heuristic",),
                    strength="bounded_heuristic",
                    confidence=0.7,
                    source_entity_ids=(node.source_entity_id,),
                )
            )

    unique = {item.feature_id: item for item in features}
    if len(unique) > budgets.max_features:
        raise SceneBudgetExceeded("scene exceeds feature budget")
    return tuple(sorted(unique.values(), key=lambda item: item.feature_id))


def _feature(
    feature_type: str,
    node_ids: tuple[str, ...],
    relation_ids: tuple[str, ...],
    evidence: tuple[str, ...],
    limitations: tuple[str, ...],
    summary: tuple[tuple[str, object], ...] = (),
    *,
    strength: str = "derived_exact",
    confidence: float = 1.0,
    source_entity_ids: tuple[str, ...] = (),
) -> SceneFeature:
    nodes = tuple(sorted(set(node_ids)))
    relations = tuple(sorted(set(relation_ids)))
    evidence_ids = [
        stable_id(
            "evd",
            "cad.scene-evidence-id/1",
            {
                "evidence_type": item,
                "source_entity_ids": sorted(source_entity_ids),
                "algorithm_version": "scene-features/2",
            },
        )
        for item in sorted(evidence)
    ]
    feature_id = stable_id(
        "fea",
        "cad.scene-feature-id/1",
        {
            "feature_type": feature_type,
            "source_evidence_ids": evidence_ids,
            "algorithm_version": "scene-features/2",
        },
    )
    return SceneFeature(
        feature_id,
        feature_type,
        nodes,
        relations,
        confidence,
        tuple(sorted(evidence)),
        tuple(sorted(limitations)),
        tuple(sorted(summary)),
        strength,  # type: ignore[arg-type]
    )


def _radius_groups(nodes: list[SceneNode], tolerance: float):
    ordered = sorted(
        nodes,
        key=lambda item: (
            item.geometry.radius
            if isinstance(item.geometry, CircleGeometry)
            else 0.0,
            item.node_id,
        ),
    )
    groups: list[list[SceneNode]] = []
    for node in ordered:
        assert isinstance(node.geometry, CircleGeometry)
        if not groups:
            groups.append([node])
            continue
        first = groups[-1][0]
        assert isinstance(first.geometry, CircleGeometry)
        if node.geometry.radius - first.geometry.radius <= tolerance:
            groups[-1].append(node)
        else:
            groups.append([node])
    return [sorted(group, key=lambda item: item.node_id) for group in groups]


def _circle_inside(circle: CircleGeometry, polygon: tuple[Point, ...], tolerance: float) -> bool:
    if not _point_in_polygon(circle.center, polygon):
        return False
    return all(
        _point_segment_distance(circle.center, edge) >= circle.radius - tolerance
        for edge in zip(polygon, polygon[1:] + polygon[:1])
    )


def _relation_groups(relations: list[SceneRelation]):
    parent: dict[str, str] = {}
    relation_ids: dict[str, set[str]] = defaultdict(set)

    def find(value: str) -> str:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    for relation in relations:
        left, right = relation.source_node_ids
        first, second = find(left), find(right)
        if first != second:
            parent[max(first, second)] = min(first, second)
    groups: dict[str, set[str]] = defaultdict(set)
    for node_id in parent:
        groups[find(node_id)].add(node_id)
    for relation in relations:
        relation_ids[find(relation.source_node_ids[0])].add(relation.relation_id)
    return [
        (tuple(sorted(nodes)), tuple(sorted(relation_ids[root])))
        for root, nodes in sorted(groups.items())
    ]


def _slot_features(
    nodes: tuple[SceneNode, ...],
    relations: tuple[SceneRelation, ...],
    components: tuple[SceneComponent, ...],
    tolerance: ToleranceProfile,
) -> list[SceneFeature]:
    result = [
        feature
        for node in nodes
        if (feature := _polyline_slot(node, tolerance)) is not None
    ]
    by_id = {node.node_id: node for node in nodes}
    relation_ids = {
        (relation.relation_type, frozenset(relation.source_node_ids)): relation.relation_id
        for relation in relations
    }
    for component in components:
        members = [by_id[node_id] for node_id in component.source_node_ids]
        arcs = [node for node in members if isinstance(node.geometry, ArcGeometry)]
        lines = [node for node in members if isinstance(node.geometry, LineGeometry)]
        for index, left in enumerate(arcs):
            for right in arcs[index + 1 :]:
                signature = _arc_line_slot(left, right, lines, tolerance)
                if signature is None:
                    continue
                slot_lines, length, width = signature
                source_ids = tuple(sorted((left.node_id, right.node_id, *(item.node_id for item in slot_lines))))
                used_relations = tuple(
                    sorted(
                        value
                        for (kind, members_), value in relation_ids.items()
                        if kind in {"connected_endpoint", "parallel"}
                        and members_.issubset(source_ids)
                    )
                )
                result.append(
                    _feature(
                        "slot",
                        source_ids,
                        used_relations,
                        ("two_semicircular_arcs", "two_tangent_parallel_lines"),
                        (),
                        (("length", length), ("width", width)),
                        source_entity_ids=tuple(
                            by_id[node_id].source_entity_id for node_id in source_ids
                        ),
                    )
                )
    return result


def _polyline_slot(node: SceneNode, tolerance: ToleranceProfile) -> SceneFeature | None:
    geometry = node.geometry
    if not isinstance(geometry, PolylineGeometry) or not geometry.closed or len(geometry.vertices) != 4:
        return None
    curved = [index for index, vertex in enumerate(geometry.vertices) if abs(vertex.bulge) > tolerance.angular]
    straight = [index for index, vertex in enumerate(geometry.vertices) if abs(vertex.bulge) <= tolerance.angular]
    if len(curved) != 2 or len(straight) != 2 or (curved[0] - curved[1]) % 2 != 0:
        return None
    arcs = [
        bulge_arc(geometry.vertices[index], geometry.vertices[(index + 1) % 4])
        for index in curved
    ]
    if any(abs(abs(geometry.vertices[index].bulge) - 1.0) > tolerance.angular for index in curved):
        return None
    if abs(arcs[0][1] - arcs[1][1]) > tolerance.radius:
        return None
    line_lengths = [
        math.dist(
            geometry.vertices[index].xy,
            geometry.vertices[(index + 1) % 4].xy,
        )
        for index in straight
    ]
    if abs(line_lengths[0] - line_lengths[1]) > tolerance.endpoint:
        return None
    return _feature(
        "slot",
        (node.node_id,),
        (),
        ("closed_obround_polyline", "two_semicircular_bulges"),
        (),
        (("length", line_lengths[0] + 2 * arcs[0][1]), ("width", 2 * arcs[0][1])),
        source_entity_ids=(node.source_entity_id,),
    )


def _source_entity_ids(
    node_ids: tuple[str, ...],
    by_id: dict[str, SceneNode],
) -> tuple[str, ...]:
    return tuple(sorted(by_id[node_id].source_entity_id for node_id in node_ids))


def _arc_line_slot(
    left: SceneNode,
    right: SceneNode,
    lines: list[SceneNode],
    tolerance: ToleranceProfile,
):
    first, second = left.geometry, right.geometry
    assert isinstance(first, ArcGeometry) and isinstance(second, ArcGeometry)
    if abs(
        ((first.end_angle_radians - first.start_angle_radians) % math.tau)
        - math.pi
    ) > tolerance.angular:
        return None
    if abs(
        ((second.end_angle_radians - second.start_angle_radians) % math.tau)
        - math.pi
    ) > tolerance.angular:
        return None
    if abs(first.radius - second.radius) > tolerance.radius:
        return None
    first_ends, second_ends = arc_endpoints(first), arc_endpoints(second)
    matches: list[SceneNode] = []
    for pairing in ((0, 0, 1, 1), (0, 1, 1, 0)):
        matches = []
        for first_index, second_index in zip(pairing[::2], pairing[1::2]):
            line = next(
                (
                    item
                    for item in lines
                    if _connects(
                        item.geometry,
                        first_ends[first_index],
                        second_ends[second_index],
                        tolerance.endpoint,
                    )
                ),
                None,
            )
            if line is None:
                break
            matches.append(line)
        if len(matches) == 2:
            break
    if len(matches) != 2:
        return None
    line_a, line_b = (item.geometry for item in matches)
    assert isinstance(line_a, LineGeometry) and isinstance(line_b, LineGeometry)
    if _parallel_delta(line_a, line_b) > tolerance.angular:
        return None
    if not all(
        _tangent(line.geometry, arc, tolerance.angular)
        for line in matches
        for arc in (first, second)
        if any(
            math.dist(endpoint, arc_endpoint) <= tolerance.endpoint
            for endpoint in (line.geometry.start, line.geometry.end)
            for arc_endpoint in arc_endpoints(arc)
        )
    ):
        return None
    center_distance = math.dist(first.center, second.center)
    return tuple(matches), center_distance + first.radius * 2, first.radius * 2


def _connects(geometry, first: Point, second: Point, tolerance: float) -> bool:
    return isinstance(geometry, LineGeometry) and (
        math.dist(geometry.start, first) <= tolerance
        and math.dist(geometry.end, second) <= tolerance
        or math.dist(geometry.end, first) <= tolerance
        and math.dist(geometry.start, second) <= tolerance
    )


def _parallel_delta(first: LineGeometry, second: LineGeometry) -> float:
    a = math.atan2(first.end[1] - first.start[1], first.end[0] - first.start[0])
    b = math.atan2(second.end[1] - second.start[1], second.end[0] - second.start[0])
    delta = abs((a - b + math.pi) % (2 * math.pi) - math.pi)
    return min(delta, abs(math.pi - delta))


def _tangent(line: LineGeometry, arc: ArcGeometry, tolerance: float) -> bool:
    endpoint = min(
        (line.start, line.end),
        key=lambda value: min(math.dist(value, item) for item in arc_endpoints(arc)),
    )
    line_vector = (line.end[0] - line.start[0], line.end[1] - line.start[1])
    radius_vector = (endpoint[0] - arc.center[0], endpoint[1] - arc.center[1])
    scale = max(math.hypot(*line_vector) * math.hypot(*radius_vector), 1e-300)
    return abs(line_vector[0] * radius_vector[0] + line_vector[1] * radius_vector[1]) / scale <= tolerance


def _point_in_polygon(point: Point, polygon: tuple[Point, ...]) -> bool:
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


def _point_segment_distance(point: Point, segment) -> float:
    dx, dy = segment[1][0] - segment[0][0], segment[1][1] - segment[0][1]
    squared = dx * dx + dy * dy
    if squared == 0:
        return math.dist(point, segment[0])
    value = max(
        0.0,
        min(
            1.0,
            ((point[0] - segment[0][0]) * dx + (point[1] - segment[0][1]) * dy)
            / squared,
        ),
    )
    return math.dist(point, (segment[0][0] + value * dx, segment[0][1] + value * dy))
