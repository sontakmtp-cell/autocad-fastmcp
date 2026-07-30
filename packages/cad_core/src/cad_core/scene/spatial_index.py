"""Deterministic bounded uniform-grid candidate generation."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from .models import Bounds, SceneNode
from .tolerances import SceneBudgetExceeded, SceneBudgets


@dataclass(frozen=True, slots=True)
class CandidateIndex:
    pairs: tuple[tuple[str, str], ...]
    cell_count: int
    cell_size: float


def build_candidate_index(
    nodes: tuple[SceneNode, ...],
    budgets: SceneBudgets,
) -> CandidateIndex:
    indexed = tuple(node for node in nodes if node.bounds is not None)
    if not indexed:
        return CandidateIndex((), 0, 1.0)
    drawing = _union(node.bounds for node in indexed if node.bounds is not None)
    span = max(drawing.max_x - drawing.min_x, drawing.max_y - drawing.min_y, 1.0)
    cell_size = span / max(1.0, math.sqrt(len(indexed)))
    cells: dict[tuple[int, int], list[str]] = defaultdict(list)
    for node in sorted(indexed, key=lambda item: item.node_id):
        assert node.bounds is not None
        occupied = _cells(node.bounds.expanded(cell_size), drawing, cell_size)
        if len(occupied) > budgets.max_cells_per_node:
            raise SceneBudgetExceeded("node exceeds spatial cell budget")
        for key in occupied:
            cells[key].append(node.node_id)
    if len(cells) > budgets.max_spatial_cells:
        raise SceneBudgetExceeded("scene exceeds spatial cell budget")

    pairs: set[tuple[str, str]] = set()
    per_node: dict[str, set[str]] = defaultdict(set)
    for key in sorted(cells):
        members = sorted(set(cells[key]))
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                per_node[left].add(right)
                per_node[right].add(left)
                if (
                    len(per_node[left]) > budgets.max_candidates_per_node
                    or len(per_node[right]) > budgets.max_candidates_per_node
                ):
                    raise SceneBudgetExceeded("node exceeds relation candidate budget")
                pairs.add((left, right))
                if len(pairs) > budgets.max_relation_candidates:
                    raise SceneBudgetExceeded("scene exceeds relation candidate budget")
    return CandidateIndex(tuple(sorted(pairs)), len(cells), cell_size)


def _cells(
    bounds: Bounds,
    drawing: Bounds,
    size: float,
) -> tuple[tuple[int, int], ...]:
    first_x = math.floor((bounds.min_x - drawing.min_x) / size)
    last_x = math.floor((bounds.max_x - drawing.min_x) / size)
    first_y = math.floor((bounds.min_y - drawing.min_y) / size)
    last_y = math.floor((bounds.max_y - drawing.min_y) / size)
    return tuple(
        (x, y)
        for x in range(first_x, last_x + 1)
        for y in range(first_y, last_y + 1)
    )


def _union(bounds):
    iterator = iter(bounds)
    result = next(iterator)
    for item in iterator:
        result = Bounds(
            min(result.min_x, item.min_x),
            min(result.min_y, item.min_y),
            max(result.max_x, item.max_x),
            max(result.max_y, item.max_y),
        )
    return result
