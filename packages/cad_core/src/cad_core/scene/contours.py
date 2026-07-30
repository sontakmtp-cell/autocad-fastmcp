"""Closed contours and exact topology components."""

from __future__ import annotations

import math
from collections import defaultdict

from .canonical import stable_id
from .models import (
    Bounds,
    LineGeometry,
    Point,
    PolylineGeometry,
    SceneComponent,
    SceneContour,
    SceneNode,
    SceneRelation,
)
from .tolerances import SceneBudgetExceeded, SceneBudgets, ToleranceProfile

TOPOLOGY_RELATIONS = frozenset(
    {"connected_endpoint", "touch", "intersect", "overlap", "duplicate_geometry"}
)


def build_contours_and_components(
    nodes: tuple[SceneNode, ...],
    relations: tuple[SceneRelation, ...],
    tolerance: ToleranceProfile,
    budgets: SceneBudgets,
) -> tuple[tuple[SceneContour, ...], tuple[SceneComponent, ...]]:
    contours = [
        _polyline_contour(node)
        for node in nodes
        if isinstance(node.geometry, PolylineGeometry) and node.geometry.closed
    ]
    contours.extend(
        _line_loop_contours(
            nodes,
            tolerance.endpoint,
            budgets.max_relation_candidates,
        )
    )
    contours.sort(key=lambda item: item.contour_id)
    if len(contours) > budgets.max_contours:
        raise SceneBudgetExceeded("scene exceeds contour budget")
    components = _components(nodes, relations)
    return tuple(contours), components


def contour_polygon(
    contour: SceneContour,
    nodes: dict[str, SceneNode],
    tolerance: float,
) -> tuple[Point, ...] | None:
    if contour.kind == "polyline":
        geometry = nodes[contour.source_node_ids[0]].geometry
        if not isinstance(geometry, PolylineGeometry) or any(
            vertex.bulge for vertex in geometry.vertices
        ):
            return None
        return tuple(vertex.xy for vertex in geometry.vertices)
    lines = {
        node_id: nodes[node_id].geometry
        for node_id in contour.source_node_ids
    }
    if not all(isinstance(line, LineGeometry) for line in lines.values()):
        return None
    matches = _endpoint_matches(lines, tolerance, 250_000)
    start_id = min(lines)
    first = lines[start_id]
    assert isinstance(first, LineGeometry)
    points = [first.start, first.end]
    used = {start_id}
    current = (start_id, 1)
    while len(used) < len(lines):
        choices = sorted(
            (item for item in matches[current] if item[0] not in used),
            key=lambda item: (item[0], item[1]),
        )
        if not choices:
            return None
        node_id, matched_side = choices[0]
        line = lines[node_id]
        assert isinstance(line, LineGeometry)
        used.add(node_id)
        next_side = 1 - matched_side
        next_point = line.end if next_side else line.start
        points.append(next_point)
        current = (node_id, next_side)
    return tuple(points[:-1]) if (start_id, 0) in matches[current] else None


def _polyline_contour(node: SceneNode) -> SceneContour:
    assert node.bounds is not None
    return SceneContour(
        stable_id(
            "ctr",
            "cad.scene-contour-id/1",
            {
                "source_node_ids": [node.node_id],
                "algorithm_version": "scene-contours/2",
            },
        ),
        (node.node_id,),
        True,
        "polyline",
        node.bounds,
    )


def _line_loop_contours(
    nodes: tuple[SceneNode, ...],
    tolerance: float,
    max_candidates: int,
) -> list[SceneContour]:
    lines = {
        node.node_id: node
        for node in nodes
        if isinstance(node.geometry, LineGeometry)
        and node.geometry.start != node.geometry.end
    }
    geometries = {
        node_id: node.geometry
        for node_id, node in lines.items()
        if isinstance(node.geometry, LineGeometry)
    }
    matches = _endpoint_matches(geometries, tolerance, max_candidates)
    adjacency: dict[str, set[str]] = defaultdict(set)
    for occurrence, connected in matches.items():
        adjacency[occurrence[0]].update(item[0] for item in connected)
    result: list[SceneContour] = []
    unseen = set(lines)
    while unseen:
        stack = [min(unseen)]
        member_ids: set[str] = set()
        while stack:
            current = stack.pop()
            if current in member_ids:
                continue
            member_ids.add(current)
            stack.extend(sorted(adjacency[current] - member_ids, reverse=True))
        unseen -= member_ids
        occurrences = [
            (node_id, side)
            for node_id in member_ids
            for side in (0, 1)
        ]
        if len(member_ids) < 3 or any(
            len(
                {
                    other
                    for other in matches[occurrence]
                    if other[0] in member_ids
                }
            )
            != 1
            for occurrence in occurrences
        ):
            continue
        ordered = tuple(sorted(member_ids))
        bounds = _union(lines[node_id].bounds for node_id in ordered)
        result.append(
            SceneContour(
                stable_id(
                    "ctr",
                    "cad.scene-contour-id/1",
                    {
                        "source_node_ids": list(ordered),
                        "algorithm_version": "scene-contours/2",
                    },
                ),
                ordered,
                True,
                "line_loop",
                bounds,
            )
        )
    return result


def _components(
    nodes: tuple[SceneNode, ...],
    relations: tuple[SceneRelation, ...],
) -> tuple[SceneComponent, ...]:
    parent = {node.node_id: node.node_id for node in nodes}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        first, second = find(left), find(right)
        if first != second:
            parent[max(first, second)] = min(first, second)

    for relation in relations:
        if relation.relation_type in TOPOLOGY_RELATIONS:
            union(*relation.source_node_ids)
    members: dict[str, list[str]] = defaultdict(list)
    for node_id in sorted(parent):
        members[find(node_id)].append(node_id)
    return tuple(
        SceneComponent(
            stable_id(
                "cmp",
                "cad.scene-component/1",
                (tuple(values), "scene-components/1"),
            ),
            tuple(values),
        )
        for values in sorted(members.values())
    )


def _endpoint_matches(
    lines: dict[str, LineGeometry],
    tolerance: float,
    max_candidates: int,
) -> dict[tuple[str, int], set[tuple[str, int]]]:
    records = [
        ((node_id, side), point)
        for node_id, line in sorted(lines.items())
        for side, point in enumerate((line.start, line.end))
    ]
    buckets: dict[tuple[int, int], list[tuple[tuple[str, int], Point]]] = defaultdict(list)
    matches: dict[tuple[str, int], set[tuple[str, int]]] = defaultdict(set)
    candidates = 0
    for occurrence, point in records:
        key = (
            math.floor(point[0] / tolerance),
            math.floor(point[1] / tolerance),
        )
        for x in range(key[0] - 1, key[0] + 2):
            for y in range(key[1] - 1, key[1] + 2):
                for other, other_point in buckets[(x, y)]:
                    if other[0] == occurrence[0]:
                        continue
                    candidates += 1
                    if candidates > max_candidates:
                        raise SceneBudgetExceeded(
                            "scene exceeds endpoint candidate budget"
                        )
                    if math.dist(point, other_point) <= tolerance:
                        matches[occurrence].add(other)
                        matches[other].add(occurrence)
        buckets[key].append((occurrence, point))
        matches[occurrence]
    return matches


def _union(values) -> Bounds:
    iterator = iter(values)
    result = next(iterator)
    assert result is not None
    for item in iterator:
        assert item is not None
        result = Bounds(
            min(result.min_x, item.min_x),
            min(result.min_y, item.min_y),
            max(result.max_x, item.max_x),
            max(result.max_y, item.max_y),
        )
    return result
