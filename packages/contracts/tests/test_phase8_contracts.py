from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    CAD_EXECUTION_PLAN_SCHEMA_VERSION,
    CAD_PROGRAM_SCHEMA_VERSION,
    CadProgram,
    build_execution_binding_v1,
    cad_execution_binding_v1_json_schema,
    cad_execution_plan_v1_json_schema,
    cad_program_v1_json_schema,
    canonical_checkpoint_strategy_digest,
    canonical_compiler_digest,
    canonical_effect_digest,
    canonical_execution_binding_digest,
    canonical_execution_plan_digest,
    canonical_expansion_digest,
    canonical_hard_budgets_digest,
    canonical_source_digest,
    canonical_target_refs_digest,
    canonical_validation_profiles_digest,
    compile_cad_program_v1,
    parse_cad_program_v1,
    parse_execution_binding_v1,
    parse_execution_plan_v1,
    seal_cad_program_v1,
    verify_execution_binding_v1,
)


def literal(value_type: str, value: str, unit: str | None = None) -> dict:
    typed = {"type": value_type, "value": value}
    if unit is not None:
        typed["unit"] = unit
    return {"op": "literal", "value": typed}


def variable(name: str) -> dict:
    return {"op": "variable", "name": name}


def point(x: dict, y: dict, z: dict | None = None) -> dict:
    return {
        "x": x,
        "y": y,
        "z": z or literal("length", "0", "mm"),
    }


def pins() -> dict:
    return {
        "runtime_id": "managed_dotnet",
        "runtime_role": "primary",
        "host_family": "R25",
        "host_version": "0.8.0",
        "package_id": "autocad.managed_host.r25",
        "package_version": "0.8.0",
        "package_hash": "sha256:" + "1" * 64,
        "capability_manifest_hash": "sha256:" + "2" * 64,
        "operation_registry_version": "cad.program.1.create.core",
        "operation_registry_hash": "sha256:" + "3" * 64,
        "policy_version": "phase8.policy.1",
        "rollout_policy_digest": "sha256:" + "6" * 64,
    }


def compiler_package_hash() -> str:
    return "sha256:" + "7" * 64


def source_payload() -> dict:
    mm_zero = literal("length", "0", "mm")
    return {
        "schema_version": "cad.program/1.0",
        "registry_version": "cad.program/1.0-create-core",
        "program_id": "phase8-golden",
        "program_revision": 2,
        "parent_revision": 1,
        "device_id": "device-001",
        "source_snapshot_id": "snapshot-001",
        "document_id": "document-001",
        "expected_document_revision": "revision-007",
        "variables": [
            {
                "name": "pitch",
                "value": {"type": "length", "value": "1", "unit": "in"},
            },
            {
                "name": "radius",
                "value": {"type": "length", "value": "2.54", "unit": "cm"},
            },
            {
                "name": "quarter",
                "value": {"type": "angle", "value": "90", "unit": "deg"},
            },
        ],
        "operations": [
            {
                "kind": "ensure_layer",
                "operation_id": "layer-main",
                "name": "MCP-PHASE8",
                "color_index": 3,
            },
            {
                "kind": "create_line",
                "operation_id": "line-grid",
                "layer": {"operation_id": "layer-main", "output": "layer"},
                "start": point(mm_zero, mm_zero),
                "end": point(variable("pitch"), mm_zero),
                "repeat": {
                    "kind": "rectangular",
                    "rows": literal("integer", "2"),
                    "columns": literal("integer", "2"),
                    "row_offset": point(mm_zero, variable("pitch")),
                    "column_offset": point(variable("pitch"), mm_zero),
                },
            },
            {
                "kind": "create_circle",
                "operation_id": "circle-polar",
                "layer": {"operation_id": "layer-main", "output": "layer"},
                "center": point(variable("pitch"), mm_zero),
                "radius": {
                    "op": "div",
                    "left": variable("radius"),
                    "right": literal("scalar", "2"),
                },
                "repeat": {
                    "kind": "polar",
                    "count": literal("integer", "4"),
                    "center": point(mm_zero, mm_zero),
                    "total_angle": {
                        "op": "mul",
                        "left": variable("quarter"),
                        "right": literal("integer", "4"),
                    },
                },
            },
            {
                "kind": "create_text",
                "operation_id": "text-linear",
                "layer": "MCP-PHASE8",
                "position": point(mm_zero, variable("pitch")),
                "text": "Phase 8 — bản vẽ Δ",
                "height": literal("length", "0.1", "in"),
                "rotation": literal("angle", "0", "rad"),
                "repeat": {
                    "kind": "linear",
                    "count": literal("integer", "2"),
                    "offset": point(variable("pitch"), mm_zero),
                },
            },
        ],
        "budgets": {
            "max_source_operations": 16,
            "max_expanded_operations": 32,
            "max_entities": 16,
            "max_vertices": 64,
            "max_expression_nodes": 256,
            "max_coordinate_abs_mm": "10000",
            "max_text_bytes": 1024,
        },
        "required_capabilities": [
            "cad.program.v1.compile",
            "cad.program.v1.repeat.polar",
        ],
        "validation_profiles": ["geometry.basic.1", "document.revision.1"],
        "artifact_refs": [],
        "component_refs": [],
    }


def test_v1_source_is_strict_immutable_and_v02_contract_is_unchanged():
    payload = source_payload()
    source = seal_cad_program_v1(payload)

    assert source.semantic_digest == canonical_source_digest(source)
    with pytest.raises(ValidationError):
        source.program_revision = 3

    injected = deepcopy(payload)
    injected["path"] = r"C:\unsafe.dll"
    injected["semantic_digest"] = canonical_source_digest(injected)
    with pytest.raises(ValidationError):
        parse_cad_program_v1(injected)

    assert CAD_PROGRAM_SCHEMA_VERSION == "cad.program/0.2"
    assert CadProgram.model_fields["schema_version"].default == "cad.program/0.2"


def test_compiler_normalizes_units_expands_stable_ids_and_seals_all_digests():
    source = seal_cad_program_v1(source_payload())
    plan = compile_cad_program_v1(
        source, pins(), compiler_package_hash=compiler_package_hash()
    )

    assert plan.schema_version == CAD_EXECUTION_PLAN_SCHEMA_VERSION
    assert [operation.operation_id for operation in plan.operations] == [
        "layer-main",
        "line-grid.r000c000",
        "line-grid.r000c001",
        "line-grid.r001c000",
        "line-grid.r001c001",
        "circle-polar.p000",
        "circle-polar.p001",
        "circle-polar.p002",
        "circle-polar.p003",
        "text-linear.l000",
        "text-linear.l001",
    ]
    assert plan.operations[1].end.x_mm == "25.4"
    assert plan.operations[2].start.x_mm == "25.4"
    assert plan.operations[5].radius_mm == "12.7"
    assert plan.operations[6].center.y_mm == "25.4"
    assert plan.operations[9].height_mm == "2.54"

    assert plan.compiler.compiler_digest == canonical_compiler_digest()
    assert plan.expansion_digest == canonical_expansion_digest(list(plan.operations))
    assert plan.effect_manifest_digest == canonical_effect_digest(plan.effect_manifest)
    assert plan.execution_plan_digest == canonical_execution_plan_digest(plan)
    assert plan.effect_manifest.creates == 10
    assert plan.effect_manifest.modifies == 0
    assert plan.effect_manifest.erases == 0
    assert plan.target_refs_digest == canonical_target_refs_digest([])
    assert plan.validation_profiles_digest == canonical_validation_profiles_digest(
        list(plan.validation_profiles)
    )
    assert plan.checkpoint_strategy_digest == canonical_checkpoint_strategy_digest(
        plan.checkpoint_strategy
    )
    assert plan.hard_budgets_digest == canonical_hard_budgets_digest(plan.budgets)
    assert plan.compiler.compiler_package_hash == compiler_package_hash()
    binding = build_execution_binding_v1(plan)
    assert binding.source_digest == source.semantic_digest
    assert binding.execution_plan_digest == plan.execution_plan_digest
    assert binding.rollout_policy_digest == pins()["rollout_policy_digest"]
    assert binding.execution_binding_digest == canonical_execution_binding_digest(
        binding
    )
    assert plan.budgets.estimated_operations == 11
    assert plan.budgets.estimated_entities == 10


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda payload: payload["operations"][1].update(
                {"end": point(literal("length", "1", "mm"), {"op": "variable", "name": "missing"})}
            ),
            "unknown variable",
        ),
        (
            lambda payload: payload["operations"][2].update(
                {
                    "radius": {
                        "op": "div",
                        "left": variable("radius"),
                        "right": literal("scalar", "0"),
                    }
                }
            ),
            "division by zero",
        ),
        (
            lambda payload: payload["operations"][1]["repeat"].update(
                {"rows": literal("integer", "9"), "columns": literal("integer", "9")}
            ),
            "rectangular repeat exceeds bound",
        ),
    ],
)
def test_compiler_rejects_unknown_variables_numeric_errors_and_expansion_overflow(
    mutation, error
):
    payload = source_payload()
    mutation(payload)
    source = seal_cad_program_v1(payload)
    with pytest.raises(ValueError, match=error):
        compile_cad_program_v1(
            source, pins(), compiler_package_hash=compiler_package_hash()
        )


def test_source_rejects_arbitrary_operation_and_expression_shapes():
    payload = source_payload()
    payload["operations"][1] = {
        "kind": "load_assembly",
        "operation_id": "unsafe",
        "path": r"C:\unsafe.dll",
    }
    payload["semantic_digest"] = canonical_source_digest(payload)
    with pytest.raises(ValidationError):
        parse_cad_program_v1(payload)

    payload = source_payload()
    payload["operations"][1]["end"]["x"] = {
        "op": "literal",
        "value": {"type": "length", "value": "1", "unit": "mm"},
        "name": "also-present",
    }
    with pytest.raises(ValidationError, match="expression fields"):
        seal_cad_program_v1(payload)


def test_plan_parser_rejects_extra_fields_and_digest_tampering():
    plan = compile_cad_program_v1(
        seal_cad_program_v1(source_payload()),
        pins(),
        compiler_package_hash=compiler_package_hash(),
    )
    payload = plan.model_dump(mode="json")
    payload["command"] = "_.ERASE ALL"
    with pytest.raises(ValidationError):
        parse_execution_plan_v1(payload)

    payload = plan.model_dump(mode="json")
    payload["operations"][1]["end"]["x_mm"] = "999"
    with pytest.raises(ValidationError, match="expansion digest"):
        parse_execution_plan_v1(payload)

    binding = build_execution_binding_v1(plan).model_dump(mode="json")
    binding["source_digest"] = "sha256:" + "9" * 64
    binding["execution_binding_digest"] = canonical_execution_binding_digest(binding)
    parsed_binding = parse_execution_binding_v1(binding)
    with pytest.raises(ValueError, match="sealed plan"):
        verify_execution_binding_v1(parsed_binding, plan)


def test_domain_separation_unicode_numeric_and_opaque_ref_mutations():
    payload = source_payload()
    source = seal_cad_program_v1(payload)
    plan = compile_cad_program_v1(
        source, pins(), compiler_package_hash=compiler_package_hash()
    )
    binding = build_execution_binding_v1(plan)
    digests = {
        source.semantic_digest,
        plan.compiler.compiler_digest,
        plan.expansion_digest,
        plan.effect_manifest_digest,
        plan.target_refs_digest,
        plan.execution_plan_digest,
        binding.execution_binding_digest,
    }
    assert len(digests) == 7

    unicode_mutation = source_payload()
    unicode_mutation["operations"][3]["text"] = "Phase 8 — ban ve Δ"
    assert (
        seal_cad_program_v1(unicode_mutation).semantic_digest
        != source.semantic_digest
    )

    numeric_source = source_payload()
    numeric_source["variables"][0]["value"]["value"] = 1.0
    with pytest.raises(ValidationError):
        seal_cad_program_v1(numeric_source)

    numeric_plan = plan.model_dump(mode="json")
    numeric_plan["operations"][1]["end"]["x_mm"] = "25.400"
    numeric_plan["expansion_digest"] = canonical_expansion_digest(
        numeric_plan["operations"]
    )
    numeric_plan["execution_plan_digest"] = canonical_execution_plan_digest(
        numeric_plan
    )
    with pytest.raises(ValidationError):
        parse_execution_plan_v1(numeric_plan)

    path_ref = source_payload()
    path_ref["artifact_refs"] = [
        {
            "artifact_id": "artifact-program-input",
            "owner_id": "owner-001",
            "content_type": "application/vnd.autocad-mcp.cad-program+json",
            "byte_length": 128,
            "artifact_digest": "sha256:" + "4" * 64,
            "path": r"C:\payload.json",
        }
    ]
    with pytest.raises(ValidationError):
        seal_cad_program_v1(path_ref)

    url_ref = source_payload()
    url_ref["component_refs"] = [
        {
            "component_id": "component-grid",
            "owner_id": "owner-001",
            "component_version": "1.0.0",
            "content_type": "application/vnd.autocad-mcp.component+json",
            "byte_length": 128,
            "component_digest": "sha256:" + "5" * 64,
            "url": "https://example.invalid/component",
        }
    ]
    with pytest.raises(ValidationError):
        seal_cad_program_v1(url_ref)

    unresolved_ref = source_payload()
    unresolved_ref["artifact_refs"] = [
        {
            "artifact_id": "artifact-program-input",
            "owner_id": "owner-001",
            "content_type": "application/vnd.autocad-mcp.cad-program+json",
            "byte_length": 128,
            "artifact_digest": "sha256:" + "4" * 64,
        }
    ]
    with pytest.raises(ValueError, match="trusted Gateway materialization"):
        compile_cad_program_v1(
            seal_cad_program_v1(unresolved_ref),
            pins(),
            compiler_package_hash=compiler_package_hash(),
        )


def test_all_create_core_shapes_and_loop_index_compile_to_concrete_plan():
    payload = source_payload()
    zero = literal("length", "0", "mm")
    ten = literal("length", "10", "mm")
    layer = {"operation_id": "layer-main", "output": "layer"}
    payload["operations"].extend(
        [
            {
                "kind": "create_polyline",
                "operation_id": "polyline-core",
                "layer": layer,
                "vertices": [
                    point(zero, zero),
                    point(ten, zero),
                    point(ten, ten),
                ],
                "closed": True,
            },
            {
                "kind": "create_rectangle",
                "operation_id": "rectangle-core",
                "layer": layer,
                "first_corner": point(zero, zero),
                "opposite_corner": point(ten, ten),
            },
            {
                "kind": "create_dimension_linear",
                "operation_id": "dimension-core",
                "layer": layer,
                "extension_line1_point": point(zero, zero),
                "extension_line2_point": point(ten, zero),
                "dimension_line_point": point(ten, literal("length", "-5", "mm")),
                "text_override": "10 mm",
            },
            {
                "kind": "create_line",
                "operation_id": "line-index",
                "layer": layer,
                "start": point(
                    {
                        "op": "mul",
                        "left": {"op": "index"},
                        "right": variable("pitch"),
                    },
                    zero,
                ),
                "end": point(
                    {
                        "op": "add",
                        "left": {
                            "op": "mul",
                            "left": {"op": "index"},
                            "right": variable("pitch"),
                        },
                        "right": variable("pitch"),
                    },
                    zero,
                ),
                "repeat": {
                    "kind": "linear",
                    "count": literal("integer", "3"),
                    "offset": point(zero, zero),
                },
            },
        ]
    )
    plan = compile_cad_program_v1(
        seal_cad_program_v1(payload),
        pins(),
        compiler_package_hash=compiler_package_hash(),
    )

    assert [item.kind for item in plan.operations[-6:]] == [
        "create_polyline",
        "create_rectangle",
        "create_dimension_linear",
        "create_line",
        "create_line",
        "create_line",
    ]
    assert [item.start.x_mm for item in plan.operations[-3:]] == [
        "0",
        "25.4",
        "50.8",
    ]


def test_expression_depth_is_hard_bounded():
    payload = source_payload()
    expression = variable("pitch")
    for _ in range(17):
        expression = {"op": "abs", "operand": expression}
    payload["operations"][1]["end"]["x"] = expression
    with pytest.raises(ValueError, match="nesting|maximum depth"):
        seal_cad_program_v1(payload)


def test_checked_in_json_schemas_match_runtime_models():
    schema_root = Path(__file__).parents[1] / "schemas"
    assert json.loads(
        (schema_root / "cad-program-1.0.schema.json").read_text(encoding="utf-8")
    ) == cad_program_v1_json_schema()
    assert json.loads(
        (schema_root / "cad-execution-plan-1.schema.json").read_text(
            encoding="utf-8"
        )
    ) == cad_execution_plan_v1_json_schema()
    assert json.loads(
        (schema_root / "cad-execution-binding-1.schema.json").read_text(
            encoding="utf-8"
        )
    ) == cad_execution_binding_v1_json_schema()
