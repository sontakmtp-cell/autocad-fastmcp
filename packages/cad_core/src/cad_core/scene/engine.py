"""One stable entrypoint for the pure deterministic scene engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict
import time
from typing import Any

from .canonical import canonical_json, digest
from .contours import build_contours_and_components
from .features import infer_features
from .issues import detect_issues
from .models import Bounds, SceneArtifact, SceneBuildContext, SceneNode, SceneStats
from .projection import project_entities
from .relations import build_relations
from .spatial_index import build_candidate_index
from .tolerances import (
    SceneBudgetExceeded,
    SceneBudgets,
    ToleranceProfile,
    mechanical_tolerance,
    validate_tolerance,
)


def build_scene(
    entities: Iterable[Mapping[str, Any]],
    context: SceneBuildContext,
    *,
    tolerance: ToleranceProfile | None = None,
    budgets: SceneBudgets | None = None,
) -> SceneArtifact:
    """Build an immutable scene or fail before publication on budget overflow."""

    source = tuple(entities)
    started = time.monotonic()
    policy = budgets or SceneBudgets()
    if len(source) > policy.max_source_entities:
        raise SceneBudgetExceeded("scene exceeds source entity budget")
    if context.space != "model":
        raise ValueError("scene v0 supports model space only")
    nodes = project_entities(source)
    projected_bytes = len(
        canonical_json([node.to_dict() for node in nodes]).encode("utf-8")
    )
    if projected_bytes > policy.max_projected_bytes:
        raise SceneBudgetExceeded("scene exceeds projected byte budget")
    _check_time(started, policy)
    drawing_bounds = _drawing_bounds(nodes)
    selected_tolerance = tolerance or mechanical_tolerance(
        drawing_bounds,
        drawing_units=context.drawing_units,
    )
    validate_tolerance(selected_tolerance)
    index = build_candidate_index(nodes, policy)
    relations = build_relations(nodes, index.pairs, selected_tolerance, policy)
    _check_time(started, policy)
    contours, components = build_contours_and_components(
        nodes,
        relations,
        selected_tolerance,
        policy,
    )
    features = infer_features(
        nodes,
        relations,
        contours,
        components,
        selected_tolerance,
        policy,
    )
    _check_time(started, policy)
    issues = detect_issues(
        nodes,
        relations,
        contours,
        components,
        selected_tolerance,
        policy,
    )
    complete = all(node.geometry_status == "exact" for node in nodes)
    context_payload = asdict(context)
    context_payload["source_capabilities"] = sorted(context.source_capabilities)
    context_payload["build_options"] = sorted(context.build_options)
    source_digest = digest(
        "cad.scene-source/1",
        {
            "context": context_payload,
            "nodes": [node.to_dict() for node in nodes],
            "tolerance": asdict(selected_tolerance),
        },
    )
    scene_sections = {
        "source_digest": source_digest,
        "engine_version": "scene-engine/1.0.0",
        "nodes": [node.to_dict() for node in nodes],
        "relations": [item.to_dict() for item in relations],
        "contours": [item.to_dict() for item in contours],
        "components": [asdict(item) for item in components],
        "features": [item.to_dict() for item in features],
        "issues": [asdict(item) for item in issues],
        "complete": complete,
    }
    scene_bytes = len(canonical_json(scene_sections).encode("utf-8"))
    if scene_bytes > policy.max_scene_bytes:
        raise SceneBudgetExceeded("scene exceeds serialized byte budget")
    scene_digest = digest("cad.scene/1", scene_sections)
    build_seconds = time.monotonic() - started
    if build_seconds > policy.max_build_seconds:
        raise SceneBudgetExceeded("scene exceeds build time budget")
    stats = SceneStats(
        len(source),
        len(nodes),
        index.cell_count,
        len(index.pairs),
        len(relations),
        len(contours),
        len(components),
        len(features),
        len(issues),
        projected_bytes,
        scene_bytes,
        build_seconds,
    )
    return SceneArtifact(
        context,
        source_digest,
        scene_digest,
        selected_tolerance,
        nodes,
        relations,
        contours,
        components,
        features,
        issues,
        stats,
        complete,
    )


def _drawing_bounds(nodes: tuple[SceneNode, ...]) -> Bounds | None:
    values = [node.bounds for node in nodes if node.bounds is not None]
    if not values:
        return None
    result = values[0]
    for item in values[1:]:
        result = Bounds(
            min(result.min_x, item.min_x),
            min(result.min_y, item.min_y),
            max(result.max_x, item.max_x),
            max(result.max_y, item.max_y),
        )
    return result


def _check_time(started: float, budgets: SceneBudgets) -> None:
    if time.monotonic() - started > budgets.max_build_seconds:
        raise SceneBudgetExceeded("scene exceeds build time budget")
