from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    CAD_PROGRAM_REGISTRY_VERSION,
    CAD_PROGRAM_SCHEMA_VERSION,
    OPERATION_REGISTRY,
    CadProgram,
    ProgramBudgets,
    cad_program_json_schema,
    canonical_operation_registry,
    canonical_program,
    canonical_program_digest,
    operation_registry_digest,
    parse_cad_program,
    seal_program,
)


ROOT = Path(__file__).parents[1]


def complete_program() -> dict:
    layer_ref = {"operation_id": "layer-main", "output": "layer"}
    return {
        "schema_version": "cad.program/0.2",
        "registry_version": "cad.program/0.2",
        "program_id": "program-001",
        "program_revision": 1,
        "device_id": "device-001",
        "source_snapshot_id": "snapshot-001",
        "document_id": "document-001",
        "expected_document_revision": "revision-007",
        "operations": [
            {
                "kind": "ensure_layer",
                "operation_id": "layer-main",
                "name": "MCP-PHASE6",
                "color_index": 3,
            },
            {
                "kind": "create_line",
                "operation_id": "line-001",
                "layer": layer_ref,
                "start": {"x": 0.0, "y": 0.0, "z": 0.0},
                "end": {"x": 100.0, "y": 0.0, "z": 0.0},
            },
            {
                "kind": "create_circle",
                "operation_id": "circle-001",
                "layer": layer_ref,
                "center": {"x": 25.0, "y": 25.0, "z": 0.0},
                "radius": 5.0,
            },
            {
                "kind": "create_polyline",
                "operation_id": "polyline-001",
                "layer": layer_ref,
                "vertices": [
                    {"x": 0.0, "y": 0.0, "z": 0.0},
                    {"x": 20.0, "y": 0.0, "z": 0.0},
                    {"x": 20.0, "y": 20.0, "z": 0.0},
                ],
                "closed": True,
            },
            {
                "kind": "create_rectangle",
                "operation_id": "rectangle-001",
                "layer": layer_ref,
                "first_corner": {"x": 0.0, "y": 0.0, "z": 0.0},
                "opposite_corner": {"x": 40.0, "y": 30.0, "z": 0.0},
            },
            {
                "kind": "create_text",
                "operation_id": "text-001",
                "layer": layer_ref,
                "position": {"x": 5.0, "y": 5.0, "z": 0.0},
                "text": "Phase 6",
                "height": 2.5,
                "rotation_radians": 0.0,
            },
            {
                "kind": "create_dimension_linear",
                "operation_id": "dimension-001",
                "layer": layer_ref,
                "extension_line1_point": {"x": 0.0, "y": 0.0, "z": 0.0},
                "extension_line2_point": {"x": 40.0, "y": 0.0, "z": 0.0},
                "dimension_line_point": {"x": 20.0, "y": -5.0, "z": 0.0},
            },
        ],
        "preconditions": [
            {
                "kind": "document_revision_equals",
                "document_id": "document-001",
                "expected_document_revision": "revision-007",
            }
        ],
        "postconditions": [
            {"kind": "entity_count", "expected_created": 6},
            {"kind": "layer_exists", "layer": layer_ref},
        ],
        "budgets": ProgramBudgets().model_dump(mode="json"),
    }


def test_required_registry_is_exact_and_digest_is_stable():
    assert OPERATION_REGISTRY == (
        "ensure_layer",
        "create_line",
        "create_circle",
        "create_polyline",
        "create_rectangle",
        "create_text",
        "create_dimension_linear",
    )
    assert canonical_operation_registry()["registry_version"] == CAD_PROGRAM_REGISTRY_VERSION
    assert operation_registry_digest() == (
        "sha256:5dee5cb2d709f06acff2b8678bb084cd9bfa5d1988e9712510c299d61ba30eb8"
    )


def test_complete_program_round_trips_and_seals_with_server_digest():
    parsed = parse_cad_program(complete_program())
    assert parsed.schema_version == CAD_PROGRAM_SCHEMA_VERSION
    assert canonical_program(parsed) == complete_program()
    sealed = seal_program(parsed)
    assert sealed.program_digest == canonical_program_digest(parsed)
    assert sealed.program_digest == (
        "sha256:11ad7650bc721a2e109d14797d9c7d345d3e698e582ded8b8113594d4a277f60"
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update({"unknown": True}), "Extra inputs are not permitted"),
        (
            lambda value: value["operations"][1].update({"runtime_id": "managed_dotnet"}),
            "Extra inputs are not permitted",
        ),
        (
            lambda value: value.update({"operation_registry_hash": "sha256:" + "a" * 64}),
            "Extra inputs are not permitted",
        ),
        (
            lambda value: value.update({"execution_digest": "sha256:" + "a" * 64}),
            "Extra inputs are not permitted",
        ),
        (
            lambda value: value["operations"].append(
                {"kind": "erase", "operation_id": "erase-001", "entity_id": "1A"}
            ),
            "union_tag_invalid",
        ),
    ],
)
def test_unknown_runtime_selection_and_unsupported_operations_fail_closed(mutation, message):
    value = complete_program()
    mutation(value)
    with pytest.raises(ValidationError, match=message):
        CadProgram.model_validate(value)


def test_duplicate_forward_and_wrong_type_references_are_rejected():
    duplicate = complete_program()
    duplicate["operations"][1]["operation_id"] = "layer-main"
    with pytest.raises(ValidationError, match="operation_id values must be unique"):
        CadProgram.model_validate(duplicate)

    forward = complete_program()
    forward["operations"][1]["layer"] = {"operation_id": "future-layer", "output": "layer"}
    forward["operations"].append(
        {
            "kind": "ensure_layer",
            "operation_id": "future-layer",
            "name": "FUTURE",
            "color_index": None,
        }
    )
    with pytest.raises(ValidationError, match="earlier ensure_layer"):
        CadProgram.model_validate(forward)

    wrong_type = complete_program()
    wrong_type["operations"][2]["layer"] = {"operation_id": "line-001", "output": "layer"}
    with pytest.raises(ValidationError, match="earlier ensure_layer"):
        CadProgram.model_validate(wrong_type)


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_rejected(number):
    value = complete_program()
    value["operations"][1]["start"]["x"] = number
    with pytest.raises(ValidationError):
        CadProgram.model_validate(value)


def test_hard_and_declared_budgets_are_enforced():
    too_many = complete_program()
    template = too_many["operations"][1]
    too_many["operations"] = [
        {**copy.deepcopy(template), "operation_id": f"line-{index:03d}", "layer": "0"}
        for index in range(257)
    ]
    with pytest.raises(ValidationError):
        CadProgram.model_validate(too_many)

    vertices = complete_program()
    vertices["budgets"]["max_vertices"] = 6
    with pytest.raises(ValidationError, match="vertex count exceeds"):
        CadProgram.model_validate(vertices)

    text = complete_program()
    text["budgets"]["max_text_bytes"] = 4
    with pytest.raises(ValidationError, match="text bytes exceed"):
        CadProgram.model_validate(text)

    coordinate = complete_program()
    coordinate["budgets"]["max_coordinate_abs"] = 10.0
    with pytest.raises(ValidationError, match="coordinate exceeds"):
        CadProgram.model_validate(coordinate)

    payload = complete_program()
    payload["budgets"]["max_payload_bytes"] = 1024
    with pytest.raises(ValidationError, match="program payload exceeds"):
        CadProgram.model_validate(payload)


def test_schema_snapshot_is_current_and_strict():
    snapshot = json.loads(
        (ROOT / "schemas" / "cad-program-0.2.schema.json").read_text(encoding="utf-8")
    )
    assert snapshot == cad_program_json_schema()
    assert snapshot["additionalProperties"] is False
    assert snapshot["$schema"].endswith("2020-12/schema")
