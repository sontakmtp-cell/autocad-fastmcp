"""Closed contours and exact topology components."""

from __future__ import annotations

from collections import defaultdict

from .canonical import quantize, stable_id
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
    contours.extend(_line_loop_contours(nodes, tolerance.endpoint))
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
    endpoint_map: dict[tuple[int, int], list[tuple[str, Point]]] = defaultdict(list)
    for node_id, line in lines.items():
        assert isinstance(line, LineGeometry)
        endpoint_map[_key(line.start, tolerance)].append((node_id, line.start))
        endpoint_map[_key(line.end, tolerance)].append((node_id, line.end))
    start_id = min(lines)
    first = lines[start_id]
    assert isinstance(first, LineGeometry)
    points = [first.start, first.end]
    used = {start_id}
    current_key = _key(first.end, tolerance)
    while len(used) < len(lines):
        choices = sorted(
            (item for item in endpoint_map[current_key] if item[0] not in used),
            key=lambda item: item[0],
        )
        if not choices:
            return None
        node_id = choices[0][0]
        line = lines[node_id]
        assert isinstance(line, LineGeometry)
        used.add(node_id)
        next_point = line.end if _key(line.start, tolerance) == current_key else line.start
        points.append(next_point)
        current_key = _key(next_point, tolerance)
    return tuple(points[:-1]) if _key(points[-1], tolerance) == _key(points[0], tolerance) else None


def _polyline_contour(node: SceneNode) -> SceneContour:
    assert node.bounds is not None
    return SceneContour(
        stable_id(
            "ctr",
            "cad.scene-contour-id/1",
            {
                "source_node_ids": [node.node_id],
                "algorithm_version": "scene-contours/1",
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
) -> list[SceneContour]:
    lines = {
        node.node_id: node
        for node in nodes
        if isinstance(node.geometry, LineGeometry)
        and node.geometry.start != node.geometry.end
    }
    endpoint_map: dict[tuple[int, int], set[str]] = defaultdict(set)
    for node_id, node in lines.items():
        assert isinstance(node.geometry, LineGeometry)
        endpoint_map[_key(node.geometry.start, tolerance)].add(node_id)
        endpoint_map[_key(node.geometry.end, tolerance)].add(node_id)
    adjacency: dict[str, set[str]] = defaultdict(set)
    for members in endpoint_map.values():
        for node_id in members:
            adjacency[node_id].update(members - {node_id})
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
        degrees: dict[tuple[int, int], int] = defaultdict(int)
        for node_id in member_ids:
            geometry = lines[node_id].geometry
            assert isinstance(geometry, LineGeometry)
            degrees[_key(geometry.start, tolerance)] += 1
            degrees[_key(geometry.end, tolerance)] += 1
        if len(member_ids) < 3 or any(value != 2 for value in degrees.values()):
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
                        "algorithm_version": "scene-contours/1",
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


def _key(point: Point, tolerance: float) -> tuple[int, int]:
    return quantize(point[0], tolerance), quantize(point[1], tolerance)


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
