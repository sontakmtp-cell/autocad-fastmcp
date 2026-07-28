"""Pure, bounded Phase 9 reference planners and template rendering.

This module intentionally has no gateway, runtime, filesystem, or network imports.
It returns sealed-input-shaped CAD Program v1 dictionaries only; Phase 6--8 remain
the authority that validates, previews, approves, commits, recovers, and rolls back.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

MAX_AUDIT_ENTITIES = 512
MAX_PATTERN_ROWS = 32
MAX_PATTERN_COLUMNS = 32
MAX_PATTERN_HOLES = 256


class Phase9PlannerError(ValueError):
    """Raised for invalid, unsupported, or over-budget pure planner input."""


def _canonical_digest(domain: str, value: Any) -> str:
    # This mirrors the Phase 8 domain-digest envelope while keeping cad_core
    # independent from the contracts package.
    encoded = json.dumps({"domain": domain, "payload": value}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Phase9PlannerError(f"{name} must be a number")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise Phase9PlannerError(f"{name} must be finite")
    if positive and result <= 0:
        raise Phase9PlannerError(f"{name} must be positive")
    return result


def _point(value: Any, name: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise Phase9PlannerError(f"{name} must be a two-dimensional point")
    return _number(value[0], f"{name}.x"), _number(value[1], f"{name}.y")


def _literal(value: float, kind: str = "length", unit: str | None = "mm") -> dict[str, Any]:
    text = format(value, ".9f").rstrip("0").rstrip(".") or "0"
    result: dict[str, Any] = {"op": "literal", "value": {"type": kind, "value": text}}
    if unit is not None:
        result["value"]["unit"] = unit
    return result


def _point_expr(x: Any, y: Any) -> dict[str, Any]:
    return {"x": x if isinstance(x, dict) else _literal(x), "y": y if isinstance(y, dict) else _literal(y), "z": _literal(0)}


def _layer_ref(operation_id: str) -> dict[str, str]:
    return {"operation_id": operation_id, "output": "layer"}


def _base_program(context: dict[str, Any], program_id: str, operations: list[dict[str, Any]], *, variables: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    for name in ("device_id", "source_snapshot_id", "document_id", "expected_document_revision"):
        if not isinstance(context.get(name), str) or not context[name]:
            raise Phase9PlannerError(f"{name} is required")
    return {
        "schema_version": "cad.program/1.0",
        "registry_version": "cad.program/1.0-create-core",
        "program_id": program_id,
        "program_revision": 1,
        "device_id": context["device_id"],
        "source_snapshot_id": context["source_snapshot_id"],
        "document_id": context["document_id"],
        "expected_document_revision": context["expected_document_revision"],
        "variables": variables or [],
        "operations": operations,
        # Supply every Phase 8 budget field before hashing.  The existing
        # contract does not hash omitted defaults as absent fields.
        "budgets": {"max_source_operations": 64, "max_expanded_operations": 256, "max_entities": 256, "max_vertices": 4096, "max_expression_nodes": 1024, "max_coordinate_abs_mm": "1000000000", "max_text_bytes": 65536},
        "required_capabilities": ["cad.program.v1.compile"],
        "validation_profiles": ["phase9.create_only.v1"],
        "artifact_refs": [],
        "component_refs": [],
    }


def _seal(program: dict[str, Any]) -> dict[str, Any]:
    program["semantic_digest"] = _canonical_digest("cad.program.source/1", program)
    return program


def plan_auto_dimension_overall(context: dict[str, Any], entities: list[dict[str, Any]], inputs: dict[str, Any]) -> dict[str, Any]:
    """Plan exactly the requested overall width/height dimensions from explicit geometry."""
    if not isinstance(entities, list) or not entities or len(entities) > 128:
        raise Phase9PlannerError("explicit entity selection must contain 1..128 entities")
    if inputs.get("profile", "mechanical_mm") != "mechanical_mm":
        raise Phase9PlannerError("only mechanical_mm is supported")
    if inputs.get("include_width", True) is not True or inputs.get("include_height", True) is not True:
        raise Phase9PlannerError("overall width and height are both required in version 1.0.0")
    offset = _number(inputs.get("offset", 10), "offset", positive=True)
    layer = inputs.get("target_layer", "DIM")
    if not isinstance(layer, str) or not layer or len(layer) > 255:
        raise Phase9PlannerError("target_layer is invalid")
    points: list[tuple[float, float]] = []
    ids: set[str] = set()
    for entity in entities:
        if not isinstance(entity, dict) or entity.get("type") not in {"LINE", "LWPOLYLINE"}:
            raise Phase9PlannerError("only explicit LINE and LWPOLYLINE entities are supported")
        entity_id = entity.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id or entity_id in ids:
            raise Phase9PlannerError("explicit entity IDs must be unique")
        ids.add(entity_id)
        raw_points = entity.get("points")
        if entity["type"] == "LINE":
            raw_points = [entity.get("start"), entity.get("end")]
        if not isinstance(raw_points, list) or len(raw_points) < 2:
            raise Phase9PlannerError("selected geometry is missing bounded points")
        points.extend(_point(point, "entity point") for point in raw_points)
    min_x, max_x = min(point[0] for point in points), max(point[0] for point in points)
    min_y, max_y = min(point[1] for point in points), max(point[1] for point in points)
    if min_x == max_x or min_y == max_y:
        raise Phase9PlannerError("selected geometry must have non-zero width and height")
    layer_id = "ensure_dimension_layer"
    operations = [
        {"operation_id": layer_id, "kind": "ensure_layer", "name": layer},
        {"operation_id": "overall_width", "kind": "create_dimension_linear", "layer": _layer_ref(layer_id), "extension_line1_point": _point_expr(min_x, min_y), "extension_line2_point": _point_expr(max_x, min_y), "dimension_line_point": _point_expr((min_x + max_x) / 2, min_y - offset)},
        {"operation_id": "overall_height", "kind": "create_dimension_linear", "layer": _layer_ref(layer_id), "extension_line1_point": _point_expr(min_x, min_y), "extension_line2_point": _point_expr(min_x, max_y), "dimension_line_point": _point_expr(min_x - offset, (min_y + max_y) / 2)},
    ]
    return _seal(_base_program(context, "phase9.auto-dimension-overall", operations))


def audit_cleanup(entities: list[dict[str, Any]], *, max_candidates: int = 64) -> dict[str, Any]:
    """Return a read-only, snapshot-bound audit without commands or effects."""
    if not isinstance(entities, list) or len(entities) > MAX_AUDIT_ENTITIES:
        raise Phase9PlannerError(f"audit accepts at most {MAX_AUDIT_ENTITIES} entities")
    if not isinstance(max_candidates, int) or not 1 <= max_candidates <= 128:
        raise Phase9PlannerError("max_candidates must be 1..128")
    normalized: list[tuple[str, str, dict[str, Any]]] = []
    degenerate: list[str] = []
    unsupported: Counter[str] = Counter()
    for entity in entities:
        if not isinstance(entity, dict) or not isinstance(entity.get("entity_id"), str):
            raise Phase9PlannerError("audit entities require stable entity_id")
        entity_type = entity.get("type")
        if entity_type not in {"LINE", "CIRCLE", "LWPOLYLINE", "DIMENSION"}:
            unsupported[str(entity_type)] += 1
        geometry = entity.get("geometry", {})
        key = _canonical_digest("cad.cleanup.geometry/1", {"type": entity_type, "geometry": geometry})
        normalized.append((key, entity["entity_id"], entity))
        if entity_type == "LINE" and geometry.get("start") == geometry.get("end"):
            degenerate.append(entity["entity_id"])
        if entity_type == "CIRCLE" and geometry.get("radius") == 0:
            degenerate.append(entity["entity_id"])
    groups: dict[str, list[str]] = {}
    for key, entity_id, _ in normalized:
        groups.setdefault(key, []).append(entity_id)
    duplicates = [sorted(ids) for ids in groups.values() if len(ids) > 1]
    duplicates.sort()
    report = {"schema_version": "cad.cleanup-audit-report/1", "duplicate_groups": duplicates[:max_candidates], "degenerate_entity_ids": sorted(degenerate)[:max_candidates], "unsupported_entity_summary": dict(sorted(unsupported.items())), "truncated": len(duplicates) > max_candidates or len(degenerate) > max_candidates, "effect": "none"}
    report["report_digest"] = _canonical_digest("cad.cleanup-audit-report/1", report)
    return report


def render_plate_hole_pattern(context: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    """Render the fixed first-party plate template using only typed Program v1 constructs."""
    width, height = _number(inputs.get("width"), "width", positive=True), _number(inputs.get("height"), "height", positive=True)
    diameter = _number(inputs.get("hole_diameter"), "hole_diameter", positive=True)
    rows, columns = inputs.get("rows"), inputs.get("columns")
    if isinstance(rows, bool) or not isinstance(rows, int) or not 1 <= rows <= MAX_PATTERN_ROWS:
        raise Phase9PlannerError("rows must be 1..32")
    if isinstance(columns, bool) or not isinstance(columns, int) or not 1 <= columns <= MAX_PATTERN_COLUMNS:
        raise Phase9PlannerError("columns must be 1..32")
    if rows * columns > MAX_PATTERN_HOLES:
        raise Phase9PlannerError("hole repeat exceeds 256-hole budget")
    margin_x, margin_y = _number(inputs.get("margin_x"), "margin_x", positive=True), _number(inputs.get("margin_y"), "margin_y", positive=True)
    if 2 * margin_x >= width or 2 * margin_y >= height:
        raise Phase9PlannerError("margins must leave a positive plate interior")
    layer = inputs.get("layer", "MECH")
    if not isinstance(layer, str) or not layer:
        raise Phase9PlannerError("layer is invalid")
    variables = [{"name": name, "value": {"type": "length", "value": format(value, ".9f").rstrip("0").rstrip("."), "unit": "mm"}} for name, value in (("width", width), ("height", height), ("hole_radius", diameter / 2), ("margin_x", margin_x), ("margin_y", margin_y))]
    layer_id = "ensure_plate_layer"
    operations: list[dict[str, Any]] = [
        {"operation_id": layer_id, "kind": "ensure_layer", "name": layer},
        {"operation_id": "plate_outline", "kind": "create_rectangle", "layer": _layer_ref(layer_id), "first_corner": _point_expr(0, 0), "opposite_corner": _point_expr({"op": "variable", "name": "width"}, {"op": "variable", "name": "height"})},
        {"operation_id": "hole", "kind": "create_circle", "layer": _layer_ref(layer_id), "center": _point_expr({"op": "variable", "name": "margin_x"}, {"op": "variable", "name": "margin_y"}), "radius": {"op": "variable", "name": "hole_radius"}, "repeat": {"kind": "rectangular", "rows": _literal(rows, "integer", None), "columns": _literal(columns, "integer", None), "row_offset": _point_expr(0, (height - 2 * margin_y) / max(rows - 1, 1)), "column_offset": _point_expr((width - 2 * margin_x) / max(columns - 1, 1), 0)}},
    ]
    if inputs.get("include_overall_dimensions", False):
        operations.extend([
            {"operation_id": "plate_width", "kind": "create_dimension_linear", "layer": _layer_ref(layer_id), "extension_line1_point": _point_expr(0, 0), "extension_line2_point": _point_expr({"op": "variable", "name": "width"}, 0), "dimension_line_point": _point_expr(width / 2, -10)},
            {"operation_id": "plate_height", "kind": "create_dimension_linear", "layer": _layer_ref(layer_id), "extension_line1_point": _point_expr(0, 0), "extension_line2_point": _point_expr(0, {"op": "variable", "name": "height"}), "dimension_line_point": _point_expr(-10, height / 2)},
        ])
    return _seal(_base_program(context, "phase9.plate-hole-pattern", operations, variables=variables))


PLANNER_REGISTRY = {"mechanical.auto-dimension-overall/1": plan_auto_dimension_overall, "drawing.cleanup-audit/1": audit_cleanup}
PLANNER_REGISTRY_DIGEST = _canonical_digest("cad.phase9.planner-registry/1", sorted(PLANNER_REGISTRY))


def run_planner(planner_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return PLANNER_REGISTRY[planner_id](*args, **kwargs)
    except KeyError as error:
        raise Phase9PlannerError("planner is not allowlisted") from error


TEMPLATE_REGISTRY = {"mechanical.plate-hole-pattern/1": render_plate_hole_pattern}
TEMPLATE_REGISTRY_DIGEST = _canonical_digest("cad.phase9.template-registry/1", sorted(TEMPLATE_REGISTRY))


def render_template(template_id: str, context: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    try:
        return TEMPLATE_REGISTRY[template_id](context, inputs)
    except KeyError as error:
        raise Phase9PlannerError("template is not allowlisted") from error
