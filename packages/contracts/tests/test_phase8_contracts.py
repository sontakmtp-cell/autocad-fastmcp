from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    CAD_EXECUTION_PLAN_SCHEMA_VERSION,
    CAD_PROGRAM_V1_PHASE8_REGISTRY_VERSION,
    CAD_PROGRAM_SCHEMA_VERSION,
    CadProgram,
    build_execution_binding_v1,
    cad_execution_binding_v1_json_schema,
    cad_execution_plan_v1_json_schema,
    cad_program_v1_json_schema,
    canonical_checkpoint_strategy_digest,
    canonical_compiler_digest,
    canonical_compiler_manifest,
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


def target_source_payload() -> dict:
    zero = literal("length", "0", "mm")
    return {
        "schema_version": "cad.program/1.0",
        "registry_version": CAD_PROGRAM_V1_PHASE8_REGISTRY_VERSION,
        "program_id": "phase8-target-golden",
        "program_revision": 1,
        "device_id": "device-001",
        "source_snapshot_id": "snapshot-001",
        "document_id": "document-001",
        "expected_document_revision": "revision-007",
        "variables": [],
        "operations": [
            {
                "kind": "copy_entity",
                "operation_id": "copy-line",
                "target_ref_id": "ref-line",
                "displacement": point(
                    literal("length", "25.4", "mm"),
                    zero,
                ),
            },
            {
                "kind": "offset_entity",
                "operation_id": "offset-circle",
                "target_ref_id": "ref-circle",
                "signed_distance": literal("length", "-5", "mm"),
            },
            {
                "kind": "move_entity",
                "operation_id": "move-polyline",
                "target_ref_id": "ref-polyline",
                "displacement": point(
                    zero,
                    literal("length", "10", "mm"),
                ),
            },
        ],
        "budgets": {
            "max_source_operations": 8,
            "max_expanded_operations": 8,
            "max_entities": 8,
            "max_vertices": 8,
            "max_expression_nodes": 32,
            "max_coordinate_abs_mm": "1000",
            "max_text_bytes": 128,
        },
        "required_capabilities": ["cad.program.v1.compile"],
        "validation_profiles": ["geometry.basic.1"],
        "artifact_refs": [],
        "component_refs": [],
    }


def target_refs() -> list[dict]:
    common = {
        "owner_id": "owner-001",
        "device_id": "device-001",
        "document_id": "document-001",
        "snapshot_id": "snapshot-001",
        "document_revision": "revision-007",
    }
    return [
        {
            **common,
            "ref_id": "ref-polyline",
            "entity_id": "entity-polyline",
            "entity_type": "LWPOLYLINE",
            "fingerprint": "sha256:" + "c" * 64,
        },
        {
            **common,
            "ref_id": "ref-line",
            "entity_id": "entity-line",
            "entity_type": "LINE",
            "fingerprint": "sha256:" + "a" * 64,
        },
        {
            **common,
            "ref_id": "ref-circle",
            "entity_id": "entity-circle",
            "entity_type": "CIRCLE",
            "fingerprint": "sha256:" + "b" * 64,
        },
    ]


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


def test_materialized_ref_operations_compile_to_one_exact_sealed_plan():
    source = seal_cad_program_v1(target_source_payload())
    plan = compile_cad_program_v1(
        source,
        pins(),
        compiler_package_hash=compiler_package_hash(),
        materialized_target_refs=target_refs(),
        materialized_owner_id="owner-001",
    )

    assert [item.kind for item in plan.operations] == [
        "copy_entity",
        "offset_entity",
        "move_entity",
    ]
    assert plan.operations[0].target_ref_id == "ref-line"
    assert plan.operations[0].displacement_mm.x_mm == "25.4"
    assert plan.operations[0].output_id == "copy-line"
    assert plan.operations[1].signed_distance_mm == "-5"
    assert plan.operations[2].displacement_mm.y_mm == "10"
    assert [item.ref_id for item in plan.materialized_target_refs] == [
        "ref-circle",
        "ref-line",
        "ref-polyline",
    ]
    assert plan.effect_manifest.creates == 2
    assert plan.effect_manifest.modifies == 1
    assert plan.effect_manifest.erases == 0
    assert plan.effect_manifest.risk_floor == "medium"
    assert plan.checkpoint_strategy == "cad.rollback.checkpoint/2"
    assert [item.checkpoint_strategy for item in plan.effect_manifest.entries] == [
        "cad.rollback.checkpoint/1-created-entities",
        "cad.rollback.checkpoint/1-created-entities",
        "cad.rollback.checkpoint/2",
    ]
    assert plan.required_capabilities[-3:] == [
        "cad.op.copy.line.v1",
        "cad.op.offset.circle.v1",
        "cad.op.move.lwpolyline.v1",
    ]
    assert plan.target_refs_digest == canonical_target_refs_digest(
        list(plan.materialized_target_refs)
    )

    binding = build_execution_binding_v1(plan)
    assert binding.source_registry_version == CAD_PROGRAM_V1_PHASE8_REGISTRY_VERSION
    assert binding.execution_plan_digest == plan.execution_plan_digest
    assert verify_execution_binding_v1(binding, plan) == binding


@pytest.mark.parametrize(
    ("ref_index", "entity_type", "capability"),
    [
        (1, "LINE", "cad.op.move.line.v1"),
        (2, "CIRCLE", "cad.op.move.circle.v1"),
        (0, "LWPOLYLINE", "cad.op.move.lwpolyline.v1"),
    ],
)
def test_move_entity_is_exactly_typed_for_supported_entities(
    ref_index, entity_type, capability
):
    ref = target_refs()[ref_index]
    payload = target_source_payload()
    move = deepcopy(payload["operations"][2])
    move["target_ref_id"] = ref["ref_id"]
    payload["operations"] = [move]
    plan = compile_cad_program_v1(
        seal_cad_program_v1(payload),
        pins(),
        compiler_package_hash=compiler_package_hash(),
        materialized_target_refs=[ref],
        materialized_owner_id="owner-001",
    )

    assert plan.operations[0].kind == "move_entity"
    assert plan.effect_manifest.entries[0].entity_type == entity_type
    assert plan.effect_manifest.entries[0].effect_class == "modify_in_place"
    assert plan.checkpoint_strategy == "cad.rollback.checkpoint/2"
    assert capability in plan.required_capabilities


def test_create_only_program_still_uses_checkpoint_v1():
    plan = compile_cad_program_v1(
        seal_cad_program_v1(source_payload()),
        pins(),
        compiler_package_hash=compiler_package_hash(),
    )
    assert plan.source_registry_version == "cad.program/1.0-create-core"
    assert plan.effect_manifest.modifies == 0
    assert plan.checkpoint_strategy == "cad.rollback.checkpoint/1-created-entities"


@pytest.mark.parametrize("forbidden", ["handle", "path", "url", "restore_payload"])
def test_source_and_materialized_refs_reject_raw_authority_fields(forbidden):
    source_payload_value = target_source_payload()
    source_payload_value["operations"][0][forbidden] = "attacker-controlled"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        seal_cad_program_v1(source_payload_value)

    ref_values = target_refs()
    ref_values[0][forbidden] = "attacker-controlled"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        compile_cad_program_v1(
            seal_cad_program_v1(target_source_payload()),
            pins(),
            compiler_package_hash=compiler_package_hash(),
            materialized_target_refs=ref_values,
            materialized_owner_id="owner-001",
        )


def test_source_cannot_manufacture_or_bypass_gateway_materialized_refs():
    source = seal_cad_program_v1(target_source_payload())
    with pytest.raises(ValueError, match="owner identity is required"):
        compile_cad_program_v1(
            source,
            pins(),
            compiler_package_hash=compiler_package_hash(),
        )
    with pytest.raises(ValueError, match="exactly match source target_ref_id"):
        compile_cad_program_v1(
            source,
            pins(),
            compiler_package_hash=compiler_package_hash(),
            materialized_owner_id="owner-001",
        )

    missing = target_refs()[1:]
    with pytest.raises(ValueError, match="exactly match source target_ref_id"):
        compile_cad_program_v1(
            source,
            pins(),
            compiler_package_hash=compiler_package_hash(),
            materialized_target_refs=missing,
            materialized_owner_id="owner-001",
        )

    extra = target_refs()
    extra.append(
        {
            **extra[0],
            "ref_id": "ref-unused",
            "entity_id": "entity-unused",
        }
    )
    with pytest.raises(ValueError, match="exactly match source target_ref_id"):
        compile_cad_program_v1(
            source,
            pins(),
            compiler_package_hash=compiler_package_hash(),
            materialized_target_refs=extra,
            materialized_owner_id="owner-001",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner_id", "owner-other"),
        ("device_id", "device-other"),
        ("document_id", "document-other"),
        ("snapshot_id", "snapshot-other"),
        ("document_revision", "revision-other"),
    ],
)
def test_materialized_ref_context_must_match_source(field, value):
    refs = target_refs()
    refs[0][field] = value
    with pytest.raises(ValueError, match="does not match source context"):
        compile_cad_program_v1(
            seal_cad_program_v1(target_source_payload()),
            pins(),
            compiler_package_hash=compiler_package_hash(),
            materialized_target_refs=refs,
            materialized_owner_id="owner-001",
        )


def test_target_type_duplicate_ref_and_in_place_reuse_are_rejected():
    unsupported = target_refs()
    unsupported[0]["entity_type"] = "HATCH"
    with pytest.raises(ValidationError):
        compile_cad_program_v1(
            seal_cad_program_v1(target_source_payload()),
            pins(),
            compiler_package_hash=compiler_package_hash(),
            materialized_target_refs=unsupported,
            materialized_owner_id="owner-001",
        )

    duplicate = target_refs()
    duplicate.append(deepcopy(duplicate[0]))
    with pytest.raises(ValueError, match="must be unique"):
        compile_cad_program_v1(
            seal_cad_program_v1(target_source_payload()),
            pins(),
            compiler_package_hash=compiler_package_hash(),
            materialized_target_refs=duplicate,
            materialized_owner_id="owner-001",
        )

    reused_source = target_source_payload()
    reused_source["operations"][0]["target_ref_id"] = "ref-polyline"
    reused_refs = target_refs()
    with pytest.raises(ValueError, match="in-place target cannot be reused"):
        compile_cad_program_v1(
            seal_cad_program_v1(reused_source),
            pins(),
            compiler_package_hash=compiler_package_hash(),
            materialized_target_refs=[reused_refs[0], reused_refs[2]],
            materialized_owner_id="owner-001",
        )


@pytest.mark.parametrize(
    ("operation_index", "field", "value", "error"),
    [
        (
            0,
            "displacement",
            {
                "x": {"op": "literal", "value": {"type": "length", "value": "1001", "unit": "mm"}},
                "y": {"op": "literal", "value": {"type": "length", "value": "0", "unit": "mm"}},
                "z": {"op": "literal", "value": {"type": "length", "value": "0", "unit": "mm"}},
            },
            "displacement exceeds budget",
        ),
        (
            1,
            "signed_distance",
            {"op": "literal", "value": {"type": "length", "value": "0", "unit": "mm"}},
            "offset distance must be non-zero",
        ),
        (
            2,
            "displacement",
            {
                "x": {"op": "literal", "value": {"type": "length", "value": "0", "unit": "mm"}},
                "y": {"op": "literal", "value": {"type": "length", "value": "0", "unit": "mm"}},
                "z": {"op": "literal", "value": {"type": "length", "value": "0", "unit": "mm"}},
            },
            "displacement must be non-zero",
        ),
    ],
)
def test_target_operation_numeric_parameters_are_bounded(
    operation_index, field, value, error
):
    payload = target_source_payload()
    payload["operations"][operation_index][field] = value
    with pytest.raises(ValueError, match=error):
        compile_cad_program_v1(
            seal_cad_program_v1(payload),
            pins(),
            compiler_package_hash=compiler_package_hash(),
            materialized_target_refs=target_refs(),
            materialized_owner_id="owner-001",
        )


def test_target_operations_count_against_entity_budget():
    payload = target_source_payload()
    payload["budgets"]["max_entities"] = 2
    with pytest.raises(ValueError, match="entity count exceeds budget"):
        compile_cad_program_v1(
            seal_cad_program_v1(payload),
            pins(),
            compiler_package_hash=compiler_package_hash(),
            materialized_target_refs=target_refs(),
            materialized_owner_id="owner-001",
        )


def test_transform_plan_rejects_checkpoint_effect_and_target_tampering():
    plan = compile_cad_program_v1(
        seal_cad_program_v1(target_source_payload()),
        pins(),
        compiler_package_hash=compiler_package_hash(),
        materialized_target_refs=target_refs(),
        materialized_owner_id="owner-001",
    )

    checkpoint = plan.model_dump(mode="json")
    checkpoint["checkpoint_strategy"] = "cad.rollback.checkpoint/1-created-entities"
    checkpoint["checkpoint_strategy_digest"] = canonical_checkpoint_strategy_digest(
        checkpoint["checkpoint_strategy"]
    )
    checkpoint["execution_plan_digest"] = canonical_execution_plan_digest(checkpoint)
    with pytest.raises(ValidationError, match="effect manifest"):
        parse_execution_plan_v1(checkpoint)

    effect = plan.model_dump(mode="json")
    effect["effect_manifest"]["entries"][2]["modifies"] = 0
    effect["effect_manifest"]["modifies"] = 0
    effect["effect_manifest"]["risk_floor"] = "low"
    effect["effect_manifest"]["checkpoint_strategy"] = (
        "cad.rollback.checkpoint/1-created-entities"
    )
    effect["effect_manifest"]["entries"][2]["checkpoint_strategy"] = (
        "cad.rollback.checkpoint/1-created-entities"
    )
    effect["effect_manifest_digest"] = canonical_effect_digest(
        effect["effect_manifest"]
    )
    effect["execution_plan_digest"] = canonical_execution_plan_digest(effect)
    with pytest.raises(ValidationError, match="effect class|counts|checkpoint"):
        parse_execution_plan_v1(effect)

    target = plan.model_dump(mode="json", exclude_none=True)
    target["operations"][2]["target_ref_id"] = "ref-line"
    target["expansion_digest"] = canonical_expansion_digest(target["operations"])
    target["execution_plan_digest"] = canonical_execution_plan_digest(target)
    with pytest.raises(
        ValidationError,
        match="exactly match operation targets|reused|effect entity type",
    ):
        parse_execution_plan_v1(target)


def test_phase8_cross_runtime_target_vector_is_current():
    source = seal_cad_program_v1(target_source_payload())
    plan = compile_cad_program_v1(
        source,
        pins(),
        compiler_package_hash=compiler_package_hash(),
        materialized_target_refs=target_refs(),
        materialized_owner_id="owner-001",
    )
    binding = build_execution_binding_v1(plan)
    generated = {
        "fixture_version": "cad.phase8.cross-runtime-target-vector/1",
        "compiler_manifest": canonical_compiler_manifest(),
        "source": source.model_dump(mode="json", exclude_none=True),
        "materialized_owner_id": "owner-001",
        "gateway_materialized_target_refs": [
            item.model_dump(mode="json") for item in plan.materialized_target_refs
        ],
        "execution_plan": plan.model_dump(mode="json", exclude_none=True),
        "execution_binding": binding.model_dump(mode="json", exclude_none=True),
    }
    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "cad-program-1.0-phase8-target-vector.json"
        ).read_text(encoding="utf-8")
    )
    assert fixture == generated


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
