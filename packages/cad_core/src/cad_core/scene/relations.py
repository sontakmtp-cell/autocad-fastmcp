"""Bounded exact 2D relation calculations for the supported v0 subset."""

from __future__ import annotations

import math
from collections.abc import Iterable

from .canonical import stable_id
from .models import (
    ArcGeometry,
    CircleGeometry,
    LineGeometry,
    Point,
    PolylineGeometry,
    SceneNode,
    SceneRelation,
)
from .tolerances import SceneBudgetExceeded, SceneBudgets, ToleranceProfile


def build_relations(
    nodes: tuple[SceneNode, ...],
    candidates: Iterable[tuple[str, str]],
    tolerance: ToleranceProfile,
    budgets: SceneBudgets,
) -> tuple[SceneRelation, ...]:
    by_id = {node.node_id: node for node in nodes}
    relations: list[SceneRelation] = []
    for left_id, right_id in candidates:
        left, right = by_id[left_id], by_id[right_id]
        if left.geometry is None or right.geometry is None:
            continue
        for relation_type, metrics, used in _pair_relations(left, right, tolerance):
            relation = _relation(relation_type, left_id, right_id, metrics, used)
            relations.append(relation)
            if len(relations) > budgets.max_relations:
                raise SceneBudgetExceeded("scene exceeds relation budget")
        if _contains(left, right, tolerance.endpoint):
            relations.extend(
                (
                    _directed_relation("contains", left_id, right_id, tolerance.endpoint),
                    _directed_relation("inside", right_id, left_id, tolerance.endpoint),
                )
            )
        elif _contains(right, left, tolerance.endpoint):
            relations.extend(
                (
                    _directed_relation("contains", right_id, left_id, tolerance.endpoint),
                    _directed_relation("inside", left_id, right_id, tolerance.endpoint),
                )
            )
        if len(relations) > budgets.max_relations:
            raise SceneBudgetExceeded("scene exceeds relation budget")
    return tuple(sorted(set(relations), key=lambda item: item.relation_id))


def _pair_relations(
    left: SceneNode,
    right: SceneNode,
    tolerance: ToleranceProfile,
):
    first, second = left.geometry, right.geometry
    assert first is not None and second is not None
    if type(first) is type(second) and _geometry_equal(
        first, second, tolerance.duplicate
    ):
        yield "duplicate_geometry", (), tolerance.duplicate

    if _connected(first, second, tolerance.endpoint):
        yield "connected_endpoint", (), tolerance.endpoint

    first_lines, second_lines = _straight_segments(first), _straight_segments(second)
    for line_a in first_lines:
        for line_b in second_lines:
            cross = _segment_relation(line_a, line_b, tolerance.endpoint)
            if cross:
                yield cross, (), tolerance.endpoint
            angle = _angle_delta(line_a, line_b)
            if min(angle, abs(math.pi - angle)) <= tolerance.angular:
                yield "parallel", (("angle_delta", min(angle, abs(math.pi - angle))),), tolerance.angular
                if _collinear(line_a, line_b, tolerance.endpoint):
                    yield "aligned", (("offset", _line_distance(line_a, line_b[0])),), tolerance.endpoint
            if abs(angle - math.pi / 2) <= tolerance.angular:
                yield "perpendicular", (("angle_delta", abs(angle - math.pi / 2)),), tolerance.angular

    center_a = _center_radius(first)
    center_b = _center_radius(second)
    if center_a and center_b:
        center_delta = math.dist(center_a[0], center_b[0])
        if center_delta <= tolerance.endpoint:
            yield "concentric", (("center_delta", center_delta),), tolerance.endpoint
    circle_a = _full_circle(first)
    circle_b = _full_circle(second)
    if circle_a and circle_b:
        center_delta = math.dist(circle_a[0], circle_b[0])
        distance = center_delta
        outer, inner = circle_a[1] + circle_b[1], abs(circle_a[1] - circle_b[1])
        if abs(distance - outer) <= tolerance.endpoint or abs(distance - inner) <= tolerance.endpoint:
            yield "touch", (("center_distance", distance),), tolerance.endpoint
        elif inner + tolerance.endpoint < distance < outer - tolerance.endpoint:
            yield "intersect", (("center_distance", distance),), tolerance.endpoint

    for line in first_lines:
        if circle_b:
            relation = _line_circle_relation(line, circle_b, tolerance.endpoint)
            if relation:
                yield relation, (), tolerance.endpoint
    for line in second_lines:
        if circle_a:
            relation = _line_circle_relation(line, circle_a, tolerance.endpoint)
            if relation:
                yield relation, (), tolerance.endpoint


def _relation(
    relation_type: str,
    left: str,
    right: str,
    metrics: tuple[tuple[str, float], ...],
    tolerance: float,
) -> SceneRelation:
    nodes = tuple(sorted((left, right)))
    canonical_metrics = tuple(sorted((key, round(value, 12)) for key, value in metrics))
    relation_id = stable_id(
        "rel",
        "cad.scene-relation-id/1",
        {
            "relation_type": relation_type,
            "directionality": "symmetric",
            "source_node_ids": list(nodes),
            "metrics": dict(canonical_metrics),
        },
    )
    return SceneRelation(
        relation_id,
        relation_type,
        nodes,
        "symmetric",
        "derived_exact",
        1.0,
        canonical_metrics,
        tolerance,
    )


def _directed_relation(
    relation_type: str,
    source: str,
    target: str,
    tolerance: float,
) -> SceneRelation:
    relation_id = stable_id(
        "rel",
        "cad.scene-relation-id/1",
        {
            "relation_type": relation_type,
            "directionality": "directed",
            "source_node_ids": [source, target],
            "metrics": {},
        },
    )
    return SceneRelation(
        relation_id,
        relation_type,
        (source, target),
        "directed",
        "derived_exact",
        1.0,
        (),
        tolerance,
    )


def _straight_segments(geometry) -> tuple[tuple[Point, Point], ...]:
    if isinstance(geometry, LineGeometry):
        return ((geometry.start, geometry.end),)
    if not isinstance(geometry, PolylineGeometry):
        return ()
    pairs = list(zip(geometry.vertices, geometry.vertices[1:]))
    if geometry.closed:
        pairs.append((geometry.vertices[-1], geometry.vertices[0]))
    return tuple((first.xy, second.xy) for first, second in pairs if first.bulge == 0)


def endpoints(geometry) -> tuple[Point, ...]:
    if isinstance(geometry, LineGeometry):
        return geometry.start, geometry.end
    if isinstance(geometry, ArcGeometry):
        return arc_endpoints(geometry)
    if isinstance(geometry, PolylineGeometry) and not geometry.closed:
        return geometry.vertices[0].xy, geometry.vertices[-1].xy
    return ()


def arc_endpoints(arc: ArcGeometry) -> tuple[Point, Point]:
    result = []
    for radians in (arc.start_angle_radians, arc.end_angle_radians):
        result.append(
            (
                arc.center[0] + arc.radius * math.cos(radians),
                arc.center[1] + arc.radius * math.sin(radians),
            )
        )
    return result[0], result[1]


def _connected(first, second, tolerance: float) -> bool:
    return any(
        math.dist(left, right) <= tolerance
        for left in endpoints(first)
        for right in endpoints(second)
    )


def _geometry_equal(first, second, tolerance: float) -> bool:
    if isinstance(first, LineGeometry) and isinstance(second, LineGeometry):
        return (
            math.dist(first.start, second.start) <= tolerance
            and math.dist(first.end, second.end) <= tolerance
        ) or (
            math.dist(first.start, second.end) <= tolerance
            and math.dist(first.end, second.start) <= tolerance
        )
    if isinstance(first, CircleGeometry) and isinstance(second, CircleGeometry):
        return (
            math.dist(first.center, second.center) <= tolerance
            and abs(first.radius - second.radius) <= tolerance
        )
    if isinstance(first, ArcGeometry) and isinstance(second, ArcGeometry):
        return (
            math.dist(first.center, second.center) <= tolerance
            and abs(first.radius - second.radius) <= tolerance
            and abs(first.start_angle_radians - second.start_angle_radians) <= 1e-12
            and abs(first.end_angle_radians - second.end_angle_radians) <= 1e-12
        )
    if not (
        isinstance(first, PolylineGeometry)
        and isinstance(second, PolylineGeometry)
        and first.closed == second.closed
        and len(first.vertices) == len(second.vertices)
    ):
        return False
    first_sequence = _polyline_sequences(first)[0]
    return any(
        all(
            math.dist(left[:2], right[:2]) <= tolerance
            and abs(left[2] - right[2]) <= 1e-12
            for left, right in zip(first_sequence, second_sequence)
        )
        for second_sequence in _polyline_sequences(second)
    )


def _polyline_sequences(
    geometry: PolylineGeometry,
) -> tuple[tuple[tuple[float, float, float], ...], ...]:
    forward = tuple(
        (vertex.x, vertex.y, vertex.bulge) for vertex in geometry.vertices
    )
    if not geometry.closed:
        reversed_vertices = tuple(
            (
                geometry.vertices[index].x,
                geometry.vertices[index].y,
                -geometry.vertices[index - 1].bulge if index > 0 else 0.0,
            )
            for index in reversed(range(len(geometry.vertices)))
        )
        return forward, reversed_vertices
    reversed_vertices = tuple(
        (
            geometry.vertices[index].x,
            geometry.vertices[index].y,
            -geometry.vertices[(index - 1) % len(geometry.vertices)].bulge,
        )
        for index in reversed(range(len(geometry.vertices)))
    )
    return tuple(
        sequence[index:] + sequence[:index]
        for sequence in (forward, reversed_vertices)
        for index in range(len(sequence))
    )


def _segment_relation(first, second, tolerance: float) -> str | None:
    if _collinear(first, second, tolerance):
        axis = 0 if abs(first[1][0] - first[0][0]) >= abs(first[1][1] - first[0][1]) else 1
        left = sorted((first[0][axis], first[1][axis]))
        right = sorted((second[0][axis], second[1][axis]))
        overlap = min(left[1], right[1]) - max(left[0], right[0])
        if overlap > tolerance:
            return "overlap"
        if overlap >= -tolerance:
            return "touch"
        return None
    orientations = (
        _cross(first[0], first[1], second[0]),
        _cross(first[0], first[1], second[1]),
        _cross(second[0], second[1], first[0]),
        _cross(second[0], second[1], first[1]),
    )
    if orientations[0] * orientations[1] < 0 and orientations[2] * orientations[3] < 0:
        return "intersect"
    if any(
        _point_on_segment(point, line, tolerance)
        for point, line in (
            (second[0], first),
            (second[1], first),
            (first[0], second),
            (first[1], second),
        )
    ):
        return "touch"
    return None


def _line_circle_relation(line, circle, tolerance: float) -> str | None:
    distance, parameter = _point_segment_distance(circle[0], line)
    if parameter < 0 or parameter > 1:
        return None
    delta = abs(distance - circle[1])
    if delta <= tolerance:
        return "touch"
    return "intersect" if distance < circle[1] - tolerance else None


def _center_radius(geometry) -> tuple[Point, float] | None:
    if isinstance(geometry, (CircleGeometry, ArcGeometry)):
        return geometry.center, geometry.radius
    return None


def _full_circle(geometry) -> tuple[Point, float] | None:
    if isinstance(geometry, CircleGeometry):
        return geometry.center, geometry.radius
    return None


def _contains(outer: SceneNode, inner: SceneNode, tolerance: float) -> bool:
    if outer.bounds is None or inner.bounds is None or not outer.bounds.contains(inner.bounds, tolerance):
        return False
    polygon = _straight_closed_polygon(outer.geometry)
    if polygon is None:
        return False
    if isinstance(inner.geometry, CircleGeometry):
        if not _point_in_polygon(inner.geometry.center, polygon):
            return False
        return all(
            _point_segment_distance(inner.geometry.center, edge)[0]
            >= inner.geometry.radius - tolerance
            for edge in zip(polygon, polygon[1:] + polygon[:1])
        )
    nested = _straight_closed_polygon(inner.geometry)
    return nested is not None and all(_point_in_polygon(item, polygon) for item in nested)


def _straight_closed_polygon(geometry) -> tuple[Point, ...] | None:
    if not isinstance(geometry, PolylineGeometry) or not geometry.closed:
        return None
    if any(vertex.bulge for vertex in geometry.vertices):
        return None
    return tuple(vertex.xy for vertex in geometry.vertices)


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


def _angle_delta(first, second) -> float:
    a = math.atan2(first[1][1] - first[0][1], first[1][0] - first[0][0])
    b = math.atan2(second[1][1] - second[0][1], second[1][0] - second[0][0])
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


def _collinear(first, second, tolerance: float) -> bool:
    scale = max(math.dist(*first), math.dist(*second), 1.0)
    return (
        abs(_cross(first[0], first[1], second[0])) <= tolerance * scale
        and abs(_cross(first[0], first[1], second[1])) <= tolerance * scale
    )


def _line_distance(line, point: Point) -> float:
    length = max(math.dist(*line), 1e-300)
    return abs(_cross(line[0], line[1], point)) / length


def _cross(start: Point, end: Point, point: Point) -> float:
    return (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (point[0] - start[0])


def _point_on_segment(point: Point, segment, tolerance: float) -> bool:
    distance, parameter = _point_segment_distance(point, segment)
    return -tolerance <= parameter <= 1 + tolerance and distance <= tolerance


def _point_segment_distance(point: Point, segment) -> tuple[float, float]:
    dx, dy = segment[1][0] - segment[0][0], segment[1][1] - segment[0][1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.dist(point, segment[0]), 0.0
    parameter = ((point[0] - segment[0][0]) * dx + (point[1] - segment[0][1]) * dy) / length_squared
    nearest = (segment[0][0] + parameter * dx, segment[0][1] + parameter * dy)
    return math.dist(point, nearest), parameter
