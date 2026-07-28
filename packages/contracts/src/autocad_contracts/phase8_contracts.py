"""Strict CAD Program v1 source compiler and sealed execution-plan contracts.

This module is deliberately compile-only.  It has no AutoCAD/runtime adapter and
only expands the existing create-only CAD Program primitives.
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
from typing import Annotated, Any, Literal, TypeAlias, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

CAD_PROGRAM_V1_SCHEMA_VERSION = "cad.program/1.0"
CAD_EXECUTION_PLAN_SCHEMA_VERSION = "cad.execution-plan/1"
CAD_EFFECT_MANIFEST_SCHEMA_VERSION = "cad.effect-manifest/1"
CAD_PROGRAM_V1_REGISTRY_VERSION = "cad.program/1.0-create-core"
CAD_PROGRAM_V1_PHASE8_REGISTRY_VERSION = "cad.program/1.0-phase8-core"
CAD_PROGRAM_V1_COMPILER_ID = "autocad-mcp.gateway.cad-program-v1"
CAD_PROGRAM_V1_COMPILER_VERSION = "1.1.0"
SOURCE_DIGEST_DOMAIN = "cad.program.source/1"
COMPILER_DIGEST_DOMAIN = "cad.program.compiler/1"
EXPANSION_DIGEST_DOMAIN = "cad.program.expansion/1"
EFFECT_DIGEST_DOMAIN = "cad.effect-manifest/1"
TARGET_REFS_DIGEST_DOMAIN = "cad.target-refs/1"
VALIDATION_PROFILES_DIGEST_DOMAIN = "cad.validation-profiles/1"
CHECKPOINT_STRATEGY_DIGEST_DOMAIN = "cad.checkpoint-strategy/1"
HARD_BUDGETS_DIGEST_DOMAIN = "cad.execution-budgets/1"
PLAN_DIGEST_DOMAIN = "cad.execution-plan/1"
EXECUTION_BINDING_DIGEST_DOMAIN = "cad.execution-binding/1"

MAX_SOURCE_OPERATIONS = 256
MAX_EXPANDED_OPERATIONS = 1024
MAX_VARIABLES = 64
MAX_EXPRESSION_DEPTH = 16
MAX_EXPRESSION_NODES = 1024
MAX_REPEAT_COUNT = 64
MAX_VERTICES = 4096
MAX_TEXT_BYTES = 65_536
MAX_SOURCE_BYTES = 1_048_576
MAX_PLAN_BYTES = 2_097_152
MAX_LITERAL_ABS = Decimal("1000000000000")
MAX_NORMALIZED_ABS = Decimal("1000000000")

_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_REVISION = r"^\S{1,256}$"
_DECIMAL = r"^-?(?:0|[1-9][0-9]{0,18})(?:\.[0-9]{1,18})?$"
_SHA256 = r"^sha256:[0-9a-f]{64}$"
_LAYER_NAME = r'^[^<>/\\":;?*|=`\x00-\x1f]{1,255}$'
_QUANTUM = Decimal("0.000000001")
_PI = Decimal("3.1415926535897932384626433832795028841971693993751")

Identifier = Annotated[str, Field(pattern=_IDENTIFIER)]
RevisionToken = Annotated[str, Field(pattern=_REVISION)]
Digest = Annotated[str, Field(pattern=_SHA256)]
DecimalText = Annotated[str, Field(pattern=_DECIMAL, max_length=40)]
CanonicalDecimalText = Annotated[
    str,
    Field(pattern=r"^-?(?:0|[1-9][0-9]{0,18})(?:\.[0-9]{0,17}[1-9])?$", max_length=40),
]
LayerName = Annotated[str, Field(pattern=_LAYER_NAME)]

NumericType: TypeAlias = Literal["integer", "scalar", "length", "angle"]
LengthUnit: TypeAlias = Literal["mm", "cm", "m", "in", "ft"]
AngleUnit: TypeAlias = Literal["rad", "deg"]
ProgramV1RegistryVersion: TypeAlias = Literal[
    "cad.program/1.0-create-core",
    "cad.program/1.0-phase8-core",
]
TargetEntityType: TypeAlias = Literal["LINE", "CIRCLE", "LWPOLYLINE"]


class Phase8Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class NumericValue(Phase8Model):
    type: NumericType
    value: DecimalText
    unit: LengthUnit | AngleUnit | None = None

    @model_validator(mode="after")
    def _unit_matches_type(self) -> "NumericValue":
        if self.type == "length" and self.unit not in ("mm", "cm", "m", "in", "ft"):
            raise ValueError("length values require an explicit length unit")
        if self.type == "angle" and self.unit not in ("rad", "deg"):
            raise ValueError("angle values require an explicit angle unit")
        if self.type in ("integer", "scalar") and self.unit is not None:
            raise ValueError("integer/scalar values cannot carry a unit")
        parsed = _parse_decimal(self.value)
        if parsed == 0 and self.value.startswith("-"):
            raise ValueError("negative zero is not canonical")
        if self.type == "integer" and parsed != parsed.to_integral_value():
            raise ValueError("integer values must be integral")
        return self


class Expression(Phase8Model):
    op: Literal[
        "literal",
        "variable",
        "index",
        "neg",
        "abs",
        "add",
        "sub",
        "mul",
        "div",
        "min",
        "max",
        "convert",
    ]
    value: NumericValue | None = None
    name: Identifier | None = None
    operand: "Expression | None" = None
    left: "Expression | None" = None
    right: "Expression | None" = None
    arguments: list["Expression"] | None = Field(default=None, min_length=2, max_length=8)
    unit: LengthUnit | AngleUnit | None = None

    @model_validator(mode="after")
    def _shape_matches_operator(self) -> "Expression":
        present = {
            "value": self.value is not None,
            "name": self.name is not None,
            "operand": self.operand is not None,
            "left": self.left is not None,
            "right": self.right is not None,
            "arguments": self.arguments is not None,
            "unit": self.unit is not None,
        }
        expected = {
            "literal": {"value"},
            "variable": {"name"},
            "index": set(),
            "neg": {"operand"},
            "abs": {"operand"},
            "add": {"left", "right"},
            "sub": {"left", "right"},
            "mul": {"left", "right"},
            "div": {"left", "right"},
            "min": {"arguments"},
            "max": {"arguments"},
            "convert": {"operand", "unit"},
        }[self.op]
        if {key for key, value in present.items() if value} != expected:
            raise ValueError(f"expression fields do not match operator {self.op}")
        return self


class Variable(Phase8Model):
    name: Identifier
    value: NumericValue


class OpaqueArtifactRef(Phase8Model):
    artifact_id: Identifier
    owner_id: Identifier
    content_type: Literal["application/vnd.autocad-mcp.cad-program+json"]
    byte_length: int = Field(ge=1, le=5_242_880)
    artifact_digest: Digest


class OpaqueComponentRef(Phase8Model):
    component_id: Identifier
    owner_id: Identifier
    component_version: Identifier
    content_type: Literal["application/vnd.autocad-mcp.component+json"]
    byte_length: int = Field(ge=1, le=5_242_880)
    component_digest: Digest


class ExpressionPoint3d(Phase8Model):
    x: Expression
    y: Expression
    z: Expression


class LayerOutputRefV1(Phase8Model):
    operation_id: Identifier
    output: Literal["layer"] = "layer"


LayerTargetV1: TypeAlias = Union[LayerName, LayerOutputRefV1]


class LinearRepeat(Phase8Model):
    kind: Literal["linear"]
    count: Expression
    offset: ExpressionPoint3d


class RectangularRepeat(Phase8Model):
    kind: Literal["rectangular"]
    rows: Expression
    columns: Expression
    row_offset: ExpressionPoint3d
    column_offset: ExpressionPoint3d


class PolarRepeat(Phase8Model):
    kind: Literal["polar"]
    count: Expression
    center: ExpressionPoint3d
    total_angle: Expression


RepeatSpec: TypeAlias = Annotated[
    Union[LinearRepeat, RectangularRepeat, PolarRepeat],
    Field(discriminator="kind"),
]


class SourceOperation(Phase8Model):
    operation_id: Identifier
    repeat: RepeatSpec | None = None


class EnsureLayerSource(SourceOperation):
    kind: Literal["ensure_layer"]
    name: LayerName
    color_index: int | None = Field(default=None, ge=1, le=255)


class CreateLineSource(SourceOperation):
    kind: Literal["create_line"]
    layer: LayerTargetV1
    start: ExpressionPoint3d
    end: ExpressionPoint3d


class CreateCircleSource(SourceOperation):
    kind: Literal["create_circle"]
    layer: LayerTargetV1
    center: ExpressionPoint3d
    radius: Expression


class CreatePolylineSource(SourceOperation):
    kind: Literal["create_polyline"]
    layer: LayerTargetV1
    vertices: list[ExpressionPoint3d] = Field(min_length=2, max_length=MAX_VERTICES)
    closed: bool = False


class CreateRectangleSource(SourceOperation):
    kind: Literal["create_rectangle"]
    layer: LayerTargetV1
    first_corner: ExpressionPoint3d
    opposite_corner: ExpressionPoint3d


class CreateTextSource(SourceOperation):
    kind: Literal["create_text"]
    layer: LayerTargetV1
    position: ExpressionPoint3d
    text: str = Field(min_length=1, max_length=MAX_TEXT_BYTES)
    height: Expression
    rotation: Expression

    @model_validator(mode="after")
    def _text_is_bounded(self) -> "CreateTextSource":
        if len(self.text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("text exceeds UTF-8 byte limit")
        return self


class CreateDimensionLinearSource(SourceOperation):
    kind: Literal["create_dimension_linear"]
    layer: LayerTargetV1
    extension_line1_point: ExpressionPoint3d
    extension_line2_point: ExpressionPoint3d
    dimension_line_point: ExpressionPoint3d
    text_override: str | None = Field(default=None, max_length=1024)


class CopyEntitySource(SourceOperation):
    kind: Literal["copy_entity"]
    target_ref_id: Identifier
    displacement: ExpressionPoint3d


class OffsetEntitySource(SourceOperation):
    kind: Literal["offset_entity"]
    target_ref_id: Identifier
    signed_distance: Expression


class MoveEntitySource(SourceOperation):
    kind: Literal["move_entity"]
    target_ref_id: Identifier
    displacement: ExpressionPoint3d


CadSourceOperation: TypeAlias = Annotated[
    Union[
        EnsureLayerSource,
        CreateLineSource,
        CreateCircleSource,
        CreatePolylineSource,
        CreateRectangleSource,
        CreateTextSource,
        CreateDimensionLinearSource,
        CopyEntitySource,
        OffsetEntitySource,
        MoveEntitySource,
    ],
    Field(discriminator="kind"),
]


class ProgramV1Budgets(Phase8Model):
    max_source_operations: int = Field(default=MAX_SOURCE_OPERATIONS, ge=1, le=MAX_SOURCE_OPERATIONS)
    max_expanded_operations: int = Field(
        default=MAX_EXPANDED_OPERATIONS, ge=1, le=MAX_EXPANDED_OPERATIONS
    )
    max_entities: int = Field(default=MAX_EXPANDED_OPERATIONS, ge=1, le=MAX_EXPANDED_OPERATIONS)
    max_vertices: int = Field(default=MAX_VERTICES, ge=2, le=MAX_VERTICES)
    max_expression_nodes: int = Field(
        default=MAX_EXPRESSION_NODES, ge=1, le=MAX_EXPRESSION_NODES
    )
    max_coordinate_abs_mm: DecimalText = "1000000000"
    max_text_bytes: int = Field(default=MAX_TEXT_BYTES, ge=1, le=MAX_TEXT_BYTES)

    @model_validator(mode="after")
    def _coordinate_limit_is_bounded(self) -> "ProgramV1Budgets":
        value = _parse_decimal(self.max_coordinate_abs_mm)
        if value <= 0 or value > MAX_NORMALIZED_ABS:
            raise ValueError("coordinate limit exceeds compiler ceiling")
        return self


class CadProgramV1Source(Phase8Model):
    schema_version: Literal["cad.program/1.0"] = CAD_PROGRAM_V1_SCHEMA_VERSION
    registry_version: ProgramV1RegistryVersion = CAD_PROGRAM_V1_REGISTRY_VERSION
    program_id: Identifier
    program_revision: int = Field(ge=1)
    parent_revision: int | None = Field(default=None, ge=1)
    device_id: Identifier
    source_snapshot_id: Identifier
    document_id: Identifier
    expected_document_revision: RevisionToken
    variables: list[Variable] = Field(default_factory=list, max_length=MAX_VARIABLES)
    operations: list[CadSourceOperation] = Field(min_length=1, max_length=MAX_SOURCE_OPERATIONS)
    budgets: ProgramV1Budgets = Field(default_factory=ProgramV1Budgets)
    required_capabilities: list[Identifier] = Field(default_factory=list, max_length=64)
    validation_profiles: list[Identifier] = Field(min_length=1, max_length=16)
    artifact_refs: list[OpaqueArtifactRef] = Field(default_factory=list, max_length=32)
    component_refs: list[OpaqueComponentRef] = Field(default_factory=list, max_length=32)
    semantic_digest: Digest

    @model_validator(mode="after")
    def _source_is_closed_and_bounded(self) -> "CadProgramV1Source":
        if self.parent_revision is not None and self.parent_revision >= self.program_revision:
            raise ValueError("parent_revision must precede program_revision")
        names = [item.name for item in self.variables]
        if len(names) != len(set(names)):
            raise ValueError("variable names must be unique")
        operation_ids = [item.operation_id for item in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("operation IDs must be unique")
        if len(self.required_capabilities) != len(set(self.required_capabilities)):
            raise ValueError("required capabilities must be unique")
        if "cad.program.v1.compile" not in self.required_capabilities:
            raise ValueError("cad.program.v1.compile capability is required")
        if len(self.validation_profiles) != len(set(self.validation_profiles)):
            raise ValueError("validation profiles must be unique")
        artifact_ids = [item.artifact_id for item in self.artifact_refs]
        component_ids = [item.component_id for item in self.component_refs]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("artifact refs must be unique")
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("component refs must be unique")

        known_layers: set[str] = set()
        node_count = 0
        for operation in self.operations:
            if isinstance(operation, EnsureLayerSource):
                if operation.repeat is not None:
                    raise ValueError("ensure_layer cannot be repeated")
                known_layers.add(operation.operation_id)
            elif isinstance(
                operation,
                (
                    CreateLineSource,
                    CreateCircleSource,
                    CreatePolylineSource,
                    CreateRectangleSource,
                    CreateTextSource,
                    CreateDimensionLinearSource,
                ),
            ):
                layer = operation.layer
                if isinstance(layer, LayerOutputRefV1) and layer.operation_id not in known_layers:
                    raise ValueError("layer reference must target an earlier ensure_layer output")
            if isinstance(
                operation,
                (CopyEntitySource, OffsetEntitySource, MoveEntitySource),
            ):
                if self.registry_version != CAD_PROGRAM_V1_PHASE8_REGISTRY_VERSION:
                    raise ValueError(
                        "materialized-ref operations require the Phase 8 core registry"
                    )
                if operation.repeat is not None:
                    raise ValueError("materialized-ref operations cannot use source repeat")
            if isinstance(operation.repeat, PolarRepeat) and isinstance(
                operation, CreateRectangleSource
            ):
                raise ValueError("polar repeat cannot represent a rotated rectangle primitive")
            if (
                isinstance(operation.repeat, PolarRepeat)
                and "cad.program.v1.repeat.polar" not in self.required_capabilities
            ):
                raise ValueError("polar repeat capability is required")
            for expression in _operation_expressions(operation):
                count, depth = _expression_shape(expression)
                node_count += count
                if depth > MAX_EXPRESSION_DEPTH:
                    raise ValueError("expression exceeds maximum depth")
        if node_count > self.budgets.max_expression_nodes:
            raise ValueError("expressions exceed node budget")
        if self.semantic_digest != canonical_source_digest(self):
            raise ValueError("semantic_digest does not match canonical source")
        encoded = _canonical_json(canonical_source(self)).encode("utf-8")
        if len(encoded) > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds byte budget")
        return self


class ConcretePoint3d(Phase8Model):
    x_mm: CanonicalDecimalText
    y_mm: CanonicalDecimalText
    z_mm: CanonicalDecimalText


class ConcreteVector3d(Phase8Model):
    x_mm: CanonicalDecimalText
    y_mm: CanonicalDecimalText
    z_mm: CanonicalDecimalText


class ConcreteOperation(Phase8Model):
    kind: Literal[
        "ensure_layer",
        "create_line",
        "create_circle",
        "create_polyline",
        "create_rectangle",
        "create_text",
        "create_dimension_linear",
        "copy_entity",
        "offset_entity",
        "move_entity",
    ]
    operation_version: Literal[1]
    operation_id: Identifier
    source_operation_id: Identifier
    layer: LayerName | None = None
    name: LayerName | None = None
    color_index: int | None = Field(default=None, ge=1, le=255)
    start: ConcretePoint3d | None = None
    end: ConcretePoint3d | None = None
    center: ConcretePoint3d | None = None
    radius_mm: CanonicalDecimalText | None = None
    vertices: list[ConcretePoint3d] | None = Field(default=None, min_length=2, max_length=MAX_VERTICES)
    closed: bool | None = None
    first_corner: ConcretePoint3d | None = None
    opposite_corner: ConcretePoint3d | None = None
    position: ConcretePoint3d | None = None
    text: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT_BYTES)
    height_mm: CanonicalDecimalText | None = None
    rotation_rad: CanonicalDecimalText | None = None
    extension_line1_point: ConcretePoint3d | None = None
    extension_line2_point: ConcretePoint3d | None = None
    dimension_line_point: ConcretePoint3d | None = None
    text_override: str | None = Field(default=None, max_length=1024)
    target_ref_id: Identifier | None = None
    displacement_mm: ConcreteVector3d | None = None
    signed_distance_mm: CanonicalDecimalText | None = None
    output_id: Identifier | None = None

    @model_validator(mode="after")
    def _concrete_shape_matches_kind(self) -> "ConcreteOperation":
        common = {"kind", "operation_version", "operation_id", "source_operation_id"}
        allowed = {
            "ensure_layer": {"name", "color_index"},
            "create_line": {"layer", "start", "end"},
            "create_circle": {"layer", "center", "radius_mm"},
            "create_polyline": {"layer", "vertices", "closed"},
            "create_rectangle": {"layer", "first_corner", "opposite_corner"},
            "create_text": {
                "layer",
                "position",
                "text",
                "height_mm",
                "rotation_rad",
            },
            "create_dimension_linear": {
                "layer",
                "extension_line1_point",
                "extension_line2_point",
                "dimension_line_point",
                "text_override",
            },
            "copy_entity": {
                "target_ref_id",
                "displacement_mm",
                "output_id",
            },
            "offset_entity": {
                "target_ref_id",
                "signed_distance_mm",
                "output_id",
            },
            "move_entity": {
                "target_ref_id",
                "displacement_mm",
            },
        }[self.kind]
        dumped = self.model_dump(exclude_none=True)
        if set(dumped) - common != {name for name in allowed if name in dumped}:
            raise ValueError("concrete operation contains fields for another kind")
        required = allowed - {"color_index", "text_override"}
        if not required <= set(dumped):
            raise ValueError("concrete operation is missing required fields")
        if self.radius_mm is not None and _parse_decimal(self.radius_mm) <= 0:
            raise ValueError("concrete radius must be positive")
        if self.height_mm is not None and _parse_decimal(self.height_mm) <= 0:
            raise ValueError("concrete text height must be positive")
        if self.signed_distance_mm is not None and _parse_decimal(
            self.signed_distance_mm
        ) == 0:
            raise ValueError("concrete offset distance must be non-zero")
        if self.displacement_mm is not None and all(
            _parse_decimal(value) == 0
            for value in (
                self.displacement_mm.x_mm,
                self.displacement_mm.y_mm,
                self.displacement_mm.z_mm,
            )
        ):
            raise ValueError("concrete displacement must be non-zero")
        return self


class EffectEntry(Phase8Model):
    operation_id: Identifier
    operation_kind: str = Field(min_length=1, max_length=64)
    operation_version: Literal[1]
    effect_class: Literal["create_only", "modify_in_place", "ensure_non_entity"]
    entity_type: Literal[
        "LAYER",
        "LINE",
        "CIRCLE",
        "LWPOLYLINE",
        "RECTANGLE",
        "TEXT",
        "DIMENSION_LINEAR",
    ]
    creates: int = Field(ge=0, le=1)
    modifies: int = Field(ge=0, le=1)
    erases: Literal[0]
    checkpoint_strategy: Literal[
        "none",
        "cad.rollback.checkpoint/1-created-entities",
        "cad.rollback.checkpoint/2",
    ]


class EffectManifest(Phase8Model):
    schema_version: Literal["cad.effect-manifest/1"]
    entries: list[EffectEntry] = Field(min_length=1, max_length=MAX_EXPANDED_OPERATIONS)
    creates: int = Field(ge=0, le=MAX_EXPANDED_OPERATIONS)
    modifies: int = Field(ge=0, le=MAX_EXPANDED_OPERATIONS)
    erases: Literal[0]
    ensures_non_entity: int = Field(ge=0, le=MAX_SOURCE_OPERATIONS)
    risk_floor: Literal["low", "medium"]
    checkpoint_strategy: Literal[
        "cad.rollback.checkpoint/1-created-entities",
        "cad.rollback.checkpoint/2",
    ]

    @model_validator(mode="after")
    def _totals_match_entries(self) -> "EffectManifest":
        if self.creates != sum(item.creates for item in self.entries):
            raise ValueError("effect create total does not match entries")
        if self.modifies != sum(item.modifies for item in self.entries):
            raise ValueError("effect modify total does not match entries")
        if self.ensures_non_entity != sum(
            item.effect_class == "ensure_non_entity" for item in self.entries
        ):
            raise ValueError("effect ensure total does not match entries")
        expected_strategy = (
            "cad.rollback.checkpoint/2"
            if self.modifies
            else "cad.rollback.checkpoint/1-created-entities"
        )
        if self.checkpoint_strategy != expected_strategy:
            raise ValueError("effect checkpoint strategy does not match effect classes")
        if self.risk_floor != ("medium" if self.modifies else "low"):
            raise ValueError("effect risk floor does not match effect classes")
        return self


class CompilerBinding(Phase8Model):
    compiler_id: Literal["autocad-mcp.gateway.cad-program-v1"]
    compiler_version: Literal["1.1.0"]
    compiler_digest: Digest
    compiler_package_hash: Digest


class MaterializedTargetRef(Phase8Model):
    ref_id: Identifier
    owner_id: Identifier
    device_id: Identifier
    document_id: Identifier
    snapshot_id: Identifier
    document_revision: RevisionToken
    entity_id: Identifier
    entity_type: TargetEntityType
    fingerprint: Digest


class ExecutionPins(Phase8Model):
    runtime_id: Identifier
    runtime_role: Literal["primary"]
    host_family: Identifier
    host_version: Identifier
    package_id: Identifier
    package_version: Identifier
    package_hash: Digest
    capability_manifest_hash: Digest
    operation_registry_version: RevisionToken
    operation_registry_hash: Digest
    policy_version: RevisionToken
    rollout_policy_digest: Digest


class ExecutionBindingV1(ExecutionPins):
    schema_version: Literal["cad.execution-binding/1"]
    action: Literal["compile_only", "preview", "commit"]
    source_schema_version: Literal["cad.program/1.0"]
    source_registry_version: ProgramV1RegistryVersion
    source_program_id: Identifier
    source_program_revision: int = Field(ge=1)
    source_digest: Digest
    compiler_id: Literal["autocad-mcp.gateway.cad-program-v1"]
    compiler_version: Literal["1.1.0"]
    compiler_digest: Digest
    compiler_package_hash: Digest
    plan_schema_version: Literal["cad.execution-plan/1"]
    execution_plan_digest: Digest
    expansion_digest: Digest
    effect_manifest_digest: Digest
    target_refs_digest: Digest
    validation_profiles_digest: Digest
    checkpoint_strategy_digest: Digest
    hard_budgets_digest: Digest
    device_id: Identifier
    document_id: Identifier
    source_snapshot_id: Identifier
    document_revision: RevisionToken
    preview_id: Identifier | None = None
    preview_expires_at: str | None = Field(
        default=None,
        min_length=20,
        max_length=64,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$",
    )
    receipt_id: Identifier | None = None
    execution_binding_digest: Digest

    @model_validator(mode="after")
    def _action_fields_and_digest_match(self) -> "ExecutionBindingV1":
        if self.action == "compile_only":
            if any((self.preview_id, self.preview_expires_at, self.receipt_id)):
                raise ValueError("compile-only binding cannot carry preview/receipt fields")
        elif self.action == "preview":
            if self.preview_id is None or self.preview_expires_at is None or self.receipt_id:
                raise ValueError("preview binding requires preview identity and expiry only")
        elif (
            self.preview_id is None
            or self.preview_expires_at is None
            or self.receipt_id is None
        ):
            raise ValueError("commit binding requires preview and receipt identity")
        if self.execution_binding_digest != canonical_execution_binding_digest(self):
            raise ValueError("execution binding digest does not match binding")
        return self


class Phase8ApprovalBinding(Phase8Model):
    """Phase 7 approval proof bound to one exact Phase 8 commit dispatch."""

    schema_version: Literal["cad.phase8-approval-binding/1"]
    action: Literal["program_commit"]
    intent_id: Identifier
    consent_id: Identifier
    intent_digest: Digest
    approval_proof_digest: Digest
    device_id: Identifier
    document_id: Identifier
    document_revision: RevisionToken
    job_id: Identifier
    command_id: Identifier
    idempotency_key: Identifier
    source_digest: Digest
    execution_plan_digest: Digest
    execution_binding_digest: Digest
    expansion_digest: Digest
    effect_manifest_digest: Digest
    target_refs_digest: Digest
    validation_profiles_digest: Digest
    checkpoint_strategy_digest: Digest
    hard_budgets_digest: Digest
    preview_id: Identifier
    preview_digest: Digest
    preview_expires_at: str = Field(
        min_length=20,
        max_length=64,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$",
    )
    receipt_id: Identifier

    @model_validator(mode="after")
    def _preview_expiry_is_timezone_aware(self) -> "Phase8ApprovalBinding":
        _parse_timestamp(self.preview_expires_at, "preview_expires_at")
        return self


class Phase8CapabilityEvidence(Phase8Model):
    """One server-authoritative capability claim admitted for dispatch."""

    schema_version: Literal["cad.capability-evidence/1"]
    evidence_id: Identifier
    evidence_authority: Literal["gateway_server"]
    device_id: Identifier
    capability_key: Identifier
    operation_pack: RevisionToken
    runtime_id: Identifier
    host_family: Identifier
    entity_type: Identifier
    support_state: Literal[
        "unsupported",
        "contract_only",
        "preview_only",
        "lab_commit",
        "certified",
    ]
    package_hash: Digest
    capability_manifest_hash: Digest
    operation_registry_hash: Digest
    package_signature_verified: Literal[True]
    agent_evidence_digest: Digest
    host_evidence_digest: Digest
    cohort: Identifier
    evidence_version: RevisionToken
    issued_at: str = Field(
        min_length=20,
        max_length=64,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$",
    )
    valid_until: str = Field(
        min_length=20,
        max_length=64,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$",
    )
    evidence_digest: Digest

    @model_validator(mode="after")
    def _timestamps_and_digest_match(self) -> "Phase8CapabilityEvidence":
        issued_at = _parse_timestamp(self.issued_at, "issued_at")
        valid_until = _parse_timestamp(self.valid_until, "valid_until")
        if valid_until <= issued_at:
            raise ValueError("capability evidence expiry must follow issue time")
        if self.evidence_digest != canonical_phase8_capability_evidence_digest(self):
            raise ValueError("capability evidence digest does not match claim")
        return self


class ExecutionPlanBudgets(Phase8Model):
    estimated_operations: int = Field(ge=1, le=MAX_EXPANDED_OPERATIONS)
    hard_max_operations: int = Field(ge=1, le=MAX_EXPANDED_OPERATIONS)
    estimated_entities: int = Field(ge=0, le=MAX_EXPANDED_OPERATIONS)
    hard_max_entities: int = Field(ge=1, le=MAX_EXPANDED_OPERATIONS)
    estimated_vertices: int = Field(ge=0, le=MAX_VERTICES)
    hard_max_vertices: int = Field(ge=2, le=MAX_VERTICES)
    estimated_text_bytes: int = Field(ge=0, le=MAX_TEXT_BYTES)
    hard_max_text_bytes: int = Field(ge=1, le=MAX_TEXT_BYTES)


class CadExecutionPlanV1(Phase8Model):
    schema_version: Literal["cad.execution-plan/1"]
    plan_id: Identifier
    source_schema_version: Literal["cad.program/1.0"]
    source_registry_version: ProgramV1RegistryVersion
    source_program_id: Identifier
    source_program_revision: int = Field(ge=1)
    source_digest: Digest
    compiler: CompilerBinding
    device_id: Identifier
    document_id: Identifier
    source_snapshot_id: Identifier
    expected_document_revision: RevisionToken
    operations: list[ConcreteOperation] = Field(min_length=1, max_length=MAX_EXPANDED_OPERATIONS)
    expansion_digest: Digest
    effect_manifest: EffectManifest
    effect_manifest_digest: Digest
    materialized_target_refs: list[MaterializedTargetRef] = Field(
        default_factory=list, max_length=MAX_EXPANDED_OPERATIONS
    )
    target_refs_digest: Digest
    validation_profiles_digest: Digest
    checkpoint_strategy_digest: Digest
    hard_budgets_digest: Digest
    execution_pins: ExecutionPins
    budgets: ExecutionPlanBudgets
    required_capabilities: list[Identifier] = Field(default_factory=list, max_length=64)
    validation_profiles: list[Identifier] = Field(min_length=1, max_length=16)
    artifact_refs: list[OpaqueArtifactRef] = Field(default_factory=list, max_length=32)
    component_refs: list[OpaqueComponentRef] = Field(default_factory=list, max_length=32)
    checkpoint_strategy: Literal[
        "cad.rollback.checkpoint/1-created-entities",
        "cad.rollback.checkpoint/2",
    ]
    execution_plan_digest: Digest

    @model_validator(mode="after")
    def _plan_digests_and_budgets_match(self) -> "CadExecutionPlanV1":
        if self.compiler.compiler_digest != canonical_compiler_digest():
            raise ValueError("compiler digest does not match compiler contract")
        if self.expansion_digest != canonical_expansion_digest(self.operations):
            raise ValueError("expansion digest does not match operations")
        if self.effect_manifest_digest != canonical_effect_digest(self.effect_manifest):
            raise ValueError("effect manifest digest does not match manifest")
        if len(self.effect_manifest.entries) != len(self.operations) or any(
            entry.operation_id != operation.operation_id
            or entry.operation_kind != operation.kind
            for entry, operation in zip(self.effect_manifest.entries, self.operations)
        ):
            raise ValueError("effect entries do not match ordered operations")
        if self.effect_manifest.checkpoint_strategy != self.checkpoint_strategy:
            raise ValueError("plan checkpoint strategy does not match effect manifest")
        refs_by_id = {item.ref_id: item for item in self.materialized_target_refs}
        if len(refs_by_id) != len(self.materialized_target_refs):
            raise ValueError("materialized target ref IDs must be unique")
        if len({item.owner_id for item in self.materialized_target_refs}) > 1:
            raise ValueError("materialized target refs must have one owner")
        if list(refs_by_id) != sorted(refs_by_id):
            raise ValueError("materialized target refs must be sorted by ref_id")
        used_ref_ids = {
            item.target_ref_id
            for item in self.operations
            if item.target_ref_id is not None
        }
        if used_ref_ids != set(refs_by_id):
            raise ValueError("materialized target refs must exactly match operation targets")
        if used_ref_ids and (
            self.source_registry_version != CAD_PROGRAM_V1_PHASE8_REGISTRY_VERSION
        ):
            raise ValueError("target operations require the Phase 8 core registry")
        if len(self.required_capabilities) != len(set(self.required_capabilities)):
            raise ValueError("required plan capabilities must be unique")
        derived_capabilities = _derived_target_capabilities(
            list(self.operations),
            refs_by_id,
        )
        if any(
            capability not in self.required_capabilities
            for capability in derived_capabilities
        ):
            raise ValueError("target operation capability is missing from sealed plan")
        for ref in self.materialized_target_refs:
            if (
                ref.device_id != self.device_id
                or ref.document_id != self.document_id
                or ref.snapshot_id != self.source_snapshot_id
                or ref.document_revision != self.expected_document_revision
            ):
                raise ValueError("materialized target ref does not match plan context")
        static_entity_types = {
            "ensure_layer": "LAYER",
            "create_line": "LINE",
            "create_circle": "CIRCLE",
            "create_polyline": "LWPOLYLINE",
            "create_rectangle": "RECTANGLE",
            "create_text": "TEXT",
            "create_dimension_linear": "DIMENSION_LINEAR",
        }
        target_usage: dict[str, list[str]] = {}
        modified_ref_ids: set[str] = set()
        for operation, effect in zip(self.operations, self.effect_manifest.entries):
            if operation.target_ref_id is not None:
                ref = refs_by_id[operation.target_ref_id]
                target_usage.setdefault(operation.target_ref_id, []).append(operation.kind)
                expected_entity_type = ref.entity_type
            else:
                expected_entity_type = static_entity_types[operation.kind]
            if effect.entity_type != expected_entity_type:
                raise ValueError("effect entity type does not match materialized target")
            expected_effect = (
                "ensure_non_entity"
                if operation.kind == "ensure_layer"
                else "modify_in_place"
                if operation.kind == "move_entity"
                else "create_only"
            )
            if effect.effect_class != expected_effect:
                raise ValueError("effect class does not match target operation")
            expected_creates = int(
                operation.kind not in {"ensure_layer", "move_entity"}
            )
            expected_modifies = int(operation.kind == "move_entity")
            expected_checkpoint = (
                "none"
                if operation.kind == "ensure_layer"
                else "cad.rollback.checkpoint/2"
                if expected_modifies
                else "cad.rollback.checkpoint/1-created-entities"
            )
            if (
                effect.creates != expected_creates
                or effect.modifies != expected_modifies
                or effect.checkpoint_strategy != expected_checkpoint
            ):
                raise ValueError("effect counts or checkpoint do not match operation")
            if operation.kind == "move_entity":
                if operation.target_ref_id in modified_ref_ids:
                    raise ValueError("a target ref cannot be modified more than once")
                modified_ref_ids.add(operation.target_ref_id)
        if any(
            "move_entity" in kinds and len(kinds) != 1
            for kinds in target_usage.values()
        ):
            raise ValueError(
                "an in-place target cannot be reused by another operation in one plan"
            )
        if self.target_refs_digest != canonical_target_refs_digest(
            self.materialized_target_refs
        ):
            raise ValueError("target refs digest does not match materialized refs")
        if self.validation_profiles_digest != canonical_validation_profiles_digest(
            self.validation_profiles
        ):
            raise ValueError("validation profiles digest does not match plan")
        if self.checkpoint_strategy_digest != canonical_checkpoint_strategy_digest(
            self.checkpoint_strategy
        ):
            raise ValueError("checkpoint strategy digest does not match plan")
        if self.hard_budgets_digest != canonical_hard_budgets_digest(self.budgets):
            raise ValueError("hard budgets digest does not match plan")
        if self.execution_plan_digest != canonical_execution_plan_digest(self):
            raise ValueError("execution plan digest does not match plan")
        if self.budgets.estimated_operations != len(self.operations):
            raise ValueError("estimated operation count does not match plan")
        if (
            self.effect_manifest.creates + self.effect_manifest.modifies
            != self.budgets.estimated_entities
        ):
            raise ValueError("effect entity count does not match plan budget")
        estimated_vertices = sum(
            len(item.vertices or []) + (4 if item.kind == "create_rectangle" else 0)
            for item in self.operations
        )
        estimated_text = sum(
            len((item.text or item.text_override or "").encode("utf-8"))
            for item in self.operations
        )
        if self.budgets.estimated_vertices != estimated_vertices:
            raise ValueError("estimated vertex count does not match plan")
        if self.budgets.estimated_text_bytes != estimated_text:
            raise ValueError("estimated text bytes do not match plan")
        if (
            self.budgets.estimated_operations > self.budgets.hard_max_operations
            or self.budgets.estimated_entities > self.budgets.hard_max_entities
            or self.budgets.estimated_vertices > self.budgets.hard_max_vertices
            or self.budgets.estimated_text_bytes > self.budgets.hard_max_text_bytes
        ):
            raise ValueError("estimated plan usage exceeds hard budget")
        if len(_canonical_json(canonical_execution_plan(self)).encode("utf-8")) > MAX_PLAN_BYTES:
            raise ValueError("execution plan exceeds byte budget")
        return self


@dataclass(frozen=True)
class _Evaluated:
    type: NumericType
    value: Decimal


@dataclass(frozen=True)
class _Transform:
    dx: Decimal = Decimal(0)
    dy: Decimal = Decimal(0)
    dz: Decimal = Decimal(0)
    center: tuple[Decimal, Decimal, Decimal] | None = None
    angle: Decimal = Decimal(0)


_SOURCE_ADAPTER = TypeAdapter(CadProgramV1Source)
_PLAN_ADAPTER = TypeAdapter(CadExecutionPlanV1)
_BINDING_ADAPTER = TypeAdapter(ExecutionBindingV1)


def _parse_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid canonical decimal") from exc
    if not parsed.is_finite() or abs(parsed) > MAX_LITERAL_ABS:
        raise ValueError("decimal is non-finite or exceeds literal ceiling")
    return parsed


def _normalize(value: Decimal) -> str:
    if not value.is_finite() or abs(value) > MAX_NORMALIZED_ABS:
        raise ValueError("normalized number exceeds compiler ceiling")
    with localcontext() as context:
        context.prec = 50
        rounded = value.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
    if rounded == 0:
        return "0"
    return format(rounded, "f").rstrip("0").rstrip(".")


def _normalize_value(value: NumericValue) -> _Evaluated:
    raw = _parse_decimal(value.value)
    if value.type == "length":
        factor = {
            "mm": Decimal(1),
            "cm": Decimal(10),
            "m": Decimal(1000),
            "in": Decimal("25.4"),
            "ft": Decimal("304.8"),
        }[value.unit]
        raw *= factor
    elif value.type == "angle" and value.unit == "deg":
        raw = raw * _PI / Decimal(180)
    return _Evaluated(value.type, Decimal(_normalize(raw)))


def _expression_shape(expression: Expression) -> tuple[int, int]:
    children = [
        child
        for child in (
            expression.operand,
            expression.left,
            expression.right,
            *(expression.arguments or []),
        )
        if child is not None
    ]
    if not children:
        return 1, 1
    shapes = [_expression_shape(child) for child in children]
    return 1 + sum(item[0] for item in shapes), 1 + max(item[1] for item in shapes)


def _operation_expressions(operation: CadSourceOperation) -> list[Expression]:
    expressions: list[Expression] = []

    def point(value: ExpressionPoint3d) -> None:
        expressions.extend((value.x, value.y, value.z))

    for name in (
        "start",
        "end",
        "center",
        "first_corner",
        "opposite_corner",
        "position",
        "extension_line1_point",
        "extension_line2_point",
        "dimension_line_point",
        "displacement",
    ):
        value = getattr(operation, name, None)
        if isinstance(value, ExpressionPoint3d):
            point(value)
    for value in getattr(operation, "vertices", None) or []:
        point(value)
    for name in ("radius", "height", "rotation", "signed_distance"):
        value = getattr(operation, name, None)
        if isinstance(value, Expression):
            expressions.append(value)
    repeat = operation.repeat
    if isinstance(repeat, LinearRepeat):
        expressions.append(repeat.count)
        point(repeat.offset)
    elif isinstance(repeat, RectangularRepeat):
        expressions.extend((repeat.rows, repeat.columns))
        point(repeat.row_offset)
        point(repeat.column_offset)
    elif isinstance(repeat, PolarRepeat):
        expressions.extend((repeat.count, repeat.total_angle))
        point(repeat.center)
    return expressions


def _eval(
    expression: Expression,
    variables: dict[str, _Evaluated],
    index: int,
) -> _Evaluated:
    if expression.op == "literal":
        return _normalize_value(expression.value)
    if expression.op == "variable":
        if expression.name not in variables:
            raise ValueError(f"unknown variable: {expression.name}")
        return variables[expression.name]
    if expression.op == "index":
        return _Evaluated("integer", Decimal(index))
    if expression.op in ("neg", "abs"):
        value = _eval(expression.operand, variables, index)
        result = -value.value if expression.op == "neg" else abs(value.value)
        return _Evaluated(value.type, Decimal(_normalize(result)))
    if expression.op in ("min", "max"):
        values = [_eval(item, variables, index) for item in expression.arguments]
        if len({item.type for item in values}) != 1:
            raise ValueError("min/max arguments must have one type")
        result = min(item.value for item in values) if expression.op == "min" else max(
            item.value for item in values
        )
        return _Evaluated(values[0].type, result)
    if expression.op == "convert":
        value = _eval(expression.operand, variables, index)
        expected = "length" if expression.unit in ("mm", "cm", "m", "in", "ft") else "angle"
        if value.type != expected:
            raise ValueError("conversion unit does not match expression type")
        # Evaluation is always in canonical mm/rad.  Convert is an explicit type check.
        return value

    left = _eval(expression.left, variables, index)
    right = _eval(expression.right, variables, index)
    if expression.op in ("add", "sub"):
        if left.type != right.type:
            raise ValueError("add/sub operands must have one type")
        result = left.value + right.value if expression.op == "add" else left.value - right.value
        return _Evaluated(left.type, Decimal(_normalize(result)))
    if expression.op == "mul":
        if left.type in ("scalar", "integer"):
            return _Evaluated(right.type, Decimal(_normalize(left.value * right.value)))
        if right.type in ("scalar", "integer"):
            return _Evaluated(left.type, Decimal(_normalize(left.value * right.value)))
        raise ValueError("multiplication requires a scalar/integer operand")
    if right.value == 0:
        raise ValueError("division by zero")
    if right.type in ("scalar", "integer"):
        return _Evaluated(left.type, Decimal(_normalize(left.value / right.value)))
    if left.type == right.type:
        return _Evaluated("scalar", Decimal(_normalize(left.value / right.value)))
    raise ValueError("division operands have incompatible types")


def _expect(
    expression: Expression,
    expected: NumericType,
    variables: dict[str, _Evaluated],
    index: int,
) -> Decimal:
    value = _eval(expression, variables, index)
    if expected == "integer" and value.type in ("integer", "scalar"):
        if value.value != value.value.to_integral_value():
            raise ValueError("repeat count must be integral")
        return value.value
    if value.type != expected:
        raise ValueError(f"expression must evaluate to {expected}")
    return value.value


def _point(
    value: ExpressionPoint3d,
    variables: dict[str, _Evaluated],
    index: int,
    transform: _Transform = _Transform(),
) -> ConcretePoint3d:
    x = _expect(value.x, "length", variables, index)
    y = _expect(value.y, "length", variables, index)
    z = _expect(value.z, "length", variables, index)
    if transform.center is not None:
        cx, cy, cz = transform.center
        angle = float(transform.angle)
        from math import cos, sin

        relative_x = x - cx
        relative_y = y - cy
        x = cx + Decimal(str(cos(angle))) * relative_x - Decimal(str(sin(angle))) * relative_y
        y = cy + Decimal(str(sin(angle))) * relative_x + Decimal(str(cos(angle))) * relative_y
        z = cz + (z - cz)
    return ConcretePoint3d(
        x_mm=_normalize(x + transform.dx),
        y_mm=_normalize(y + transform.dy),
        z_mm=_normalize(z + transform.dz),
    )


def _vector(
    value: ExpressionPoint3d,
    variables: dict[str, _Evaluated],
    index: int,
) -> ConcreteVector3d:
    return ConcreteVector3d(
        x_mm=_normalize(_expect(value.x, "length", variables, index)),
        y_mm=_normalize(_expect(value.y, "length", variables, index)),
        z_mm=_normalize(_expect(value.z, "length", variables, index)),
    )


def _positive(value: Decimal, name: str) -> str:
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return _normalize(value)


def _repeat_transforms(
    repeat: RepeatSpec | None,
    variables: dict[str, _Evaluated],
) -> list[tuple[str, int, _Transform]]:
    if repeat is None:
        return [("", 0, _Transform())]
    if isinstance(repeat, LinearRepeat):
        count = int(_expect(repeat.count, "integer", variables, 0))
        if not 1 <= count <= MAX_REPEAT_COUNT:
            raise ValueError("linear repeat count exceeds bound")
        offset = (
            _expect(repeat.offset.x, "length", variables, 0),
            _expect(repeat.offset.y, "length", variables, 0),
            _expect(repeat.offset.z, "length", variables, 0),
        )
        return [
            (
                f".l{item:03d}",
                item,
                _Transform(offset[0] * item, offset[1] * item, offset[2] * item),
            )
            for item in range(count)
        ]
    if isinstance(repeat, RectangularRepeat):
        rows = int(_expect(repeat.rows, "integer", variables, 0))
        columns = int(_expect(repeat.columns, "integer", variables, 0))
        if rows < 1 or columns < 1 or rows * columns > MAX_REPEAT_COUNT:
            raise ValueError("rectangular repeat exceeds bound")
        row = tuple(
            _expect(value, "length", variables, 0)
            for value in (repeat.row_offset.x, repeat.row_offset.y, repeat.row_offset.z)
        )
        column = tuple(
            _expect(value, "length", variables, 0)
            for value in (
                repeat.column_offset.x,
                repeat.column_offset.y,
                repeat.column_offset.z,
            )
        )
        return [
            (
                f".r{r:03d}c{c:03d}",
                r * columns + c,
                _Transform(*(row[axis] * r + column[axis] * c for axis in range(3))),
            )
            for r in range(rows)
            for c in range(columns)
        ]
    count = int(_expect(repeat.count, "integer", variables, 0))
    if not 1 <= count <= MAX_REPEAT_COUNT:
        raise ValueError("polar repeat count exceeds bound")
    center = tuple(
        _expect(value, "length", variables, 0)
        for value in (repeat.center.x, repeat.center.y, repeat.center.z)
    )
    total = _expect(repeat.total_angle, "angle", variables, 0)
    return [
        (
            f".p{item:03d}",
            item,
            _Transform(center=center, angle=Decimal(_normalize(total * item / count))),
        )
        for item in range(count)
    ]


def _layer_name(layer: LayerTargetV1, layers: dict[str, str]) -> str:
    if isinstance(layer, str):
        return layer
    try:
        return layers[layer.operation_id]
    except KeyError as exc:
        raise ValueError("layer output is not available") from exc


def _compile_operation(
    operation: CadSourceOperation,
    operation_id: str,
    index: int,
    transform: _Transform,
    variables: dict[str, _Evaluated],
    layers: dict[str, str],
) -> ConcreteOperation:
    common = {
        "kind": operation.kind,
        "operation_version": 1,
        "operation_id": operation_id,
        "source_operation_id": operation.operation_id,
    }
    if isinstance(operation, EnsureLayerSource):
        layers[operation.operation_id] = operation.name
        return ConcreteOperation(**common, name=operation.name, color_index=operation.color_index)
    if isinstance(operation, CopyEntitySource):
        return ConcreteOperation(
            **common,
            target_ref_id=operation.target_ref_id,
            displacement_mm=_vector(operation.displacement, variables, index),
            output_id=operation_id,
        )
    if isinstance(operation, OffsetEntitySource):
        return ConcreteOperation(
            **common,
            target_ref_id=operation.target_ref_id,
            signed_distance_mm=_normalize(
                _expect(operation.signed_distance, "length", variables, index)
            ),
            output_id=operation_id,
        )
    if isinstance(operation, MoveEntitySource):
        return ConcreteOperation(
            **common,
            target_ref_id=operation.target_ref_id,
            displacement_mm=_vector(operation.displacement, variables, index),
        )
    layer = _layer_name(operation.layer, layers)
    if isinstance(operation, CreateLineSource):
        return ConcreteOperation(
            **common,
            layer=layer,
            start=_point(operation.start, variables, index, transform),
            end=_point(operation.end, variables, index, transform),
        )
    if isinstance(operation, CreateCircleSource):
        return ConcreteOperation(
            **common,
            layer=layer,
            center=_point(operation.center, variables, index, transform),
            radius_mm=_positive(_expect(operation.radius, "length", variables, index), "radius"),
        )
    if isinstance(operation, CreatePolylineSource):
        return ConcreteOperation(
            **common,
            layer=layer,
            vertices=[_point(item, variables, index, transform) for item in operation.vertices],
            closed=operation.closed,
        )
    if isinstance(operation, CreateRectangleSource):
        return ConcreteOperation(
            **common,
            layer=layer,
            first_corner=_point(operation.first_corner, variables, index, transform),
            opposite_corner=_point(operation.opposite_corner, variables, index, transform),
        )
    if isinstance(operation, CreateTextSource):
        rotation = _expect(operation.rotation, "angle", variables, index) + transform.angle
        return ConcreteOperation(
            **common,
            layer=layer,
            position=_point(operation.position, variables, index, transform),
            text=operation.text,
            height_mm=_positive(_expect(operation.height, "length", variables, index), "height"),
            rotation_rad=_normalize(rotation),
        )
    return ConcreteOperation(
        **common,
        layer=layer,
        extension_line1_point=_point(
            operation.extension_line1_point, variables, index, transform
        ),
        extension_line2_point=_point(
            operation.extension_line2_point, variables, index, transform
        ),
        dimension_line_point=_point(
            operation.dimension_line_point, variables, index, transform
        ),
        text_override=operation.text_override,
    )


def _effect_manifest(
    operations: list[ConcreteOperation],
    refs_by_id: dict[str, MaterializedTargetRef],
) -> EffectManifest:
    entity_types = {
        "ensure_layer": "LAYER",
        "create_line": "LINE",
        "create_circle": "CIRCLE",
        "create_polyline": "LWPOLYLINE",
        "create_rectangle": "RECTANGLE",
        "create_text": "TEXT",
        "create_dimension_linear": "DIMENSION_LINEAR",
    }
    entries: list[EffectEntry] = []
    for item in operations:
        if item.target_ref_id is not None:
            entity_type = refs_by_id[item.target_ref_id].entity_type
        else:
            entity_type = entity_types[item.kind]
        modifies = 1 if item.kind == "move_entity" else 0
        creates = 0 if item.kind in {"ensure_layer", "move_entity"} else 1
        entries.append(
            EffectEntry(
                operation_id=item.operation_id,
                operation_kind=item.kind,
                operation_version=1,
                effect_class=(
                    "ensure_non_entity"
                    if item.kind == "ensure_layer"
                    else "modify_in_place"
                    if modifies
                    else "create_only"
                ),
                entity_type=entity_type,
                creates=creates,
                modifies=modifies,
                erases=0,
                checkpoint_strategy=(
                    "none"
                    if item.kind == "ensure_layer"
                    else "cad.rollback.checkpoint/2"
                    if modifies
                    else "cad.rollback.checkpoint/1-created-entities"
                ),
            )
        )
    modifies = sum(item.modifies for item in entries)
    return EffectManifest(
        schema_version=CAD_EFFECT_MANIFEST_SCHEMA_VERSION,
        entries=entries,
        creates=sum(item.creates for item in entries),
        modifies=modifies,
        erases=0,
        ensures_non_entity=sum(item.effect_class == "ensure_non_entity" for item in entries),
        risk_floor="medium" if modifies else "low",
        checkpoint_strategy=(
            "cad.rollback.checkpoint/2"
            if modifies
            else "cad.rollback.checkpoint/1-created-entities"
        ),
    )


def _plan_budget(operations: list[ConcreteOperation], source: CadProgramV1Source) -> ExecutionPlanBudgets:
    vertices = sum(
        len(item.vertices or [])
        + (4 if item.kind == "create_rectangle" else 0)
        for item in operations
    )
    text_bytes = sum(
        len((item.text or item.text_override or "").encode("utf-8")) for item in operations
    )
    entities = sum(item.kind != "ensure_layer" for item in operations)
    if len(operations) > source.budgets.max_expanded_operations:
        raise ValueError("expanded operation count exceeds budget")
    if entities > source.budgets.max_entities:
        raise ValueError("expanded entity count exceeds budget")
    if vertices > source.budgets.max_vertices:
        raise ValueError("expanded vertex count exceeds budget")
    if text_bytes > source.budgets.max_text_bytes:
        raise ValueError("expanded text bytes exceed budget")
    coordinate_limit = _parse_decimal(source.budgets.max_coordinate_abs_mm)
    for operation in operations:
        for point in _concrete_points(operation):
            if any(
                abs(_parse_decimal(value)) > coordinate_limit
                for value in (point.x_mm, point.y_mm, point.z_mm)
            ):
                raise ValueError("expanded coordinate exceeds budget")
        if operation.displacement_mm is not None and any(
            abs(_parse_decimal(value)) > coordinate_limit
            for value in (
                operation.displacement_mm.x_mm,
                operation.displacement_mm.y_mm,
                operation.displacement_mm.z_mm,
            )
        ):
            raise ValueError("expanded displacement exceeds budget")
        if (
            operation.signed_distance_mm is not None
            and abs(_parse_decimal(operation.signed_distance_mm)) > coordinate_limit
        ):
            raise ValueError("expanded offset distance exceeds budget")
    return ExecutionPlanBudgets(
        estimated_operations=len(operations),
        hard_max_operations=source.budgets.max_expanded_operations,
        estimated_entities=entities,
        hard_max_entities=source.budgets.max_entities,
        estimated_vertices=vertices,
        hard_max_vertices=source.budgets.max_vertices,
        estimated_text_bytes=text_bytes,
        hard_max_text_bytes=source.budgets.max_text_bytes,
    )


def _concrete_points(operation: ConcreteOperation) -> list[ConcretePoint3d]:
    points = [
        value
        for value in (
            operation.start,
            operation.end,
            operation.center,
            operation.first_corner,
            operation.opposite_corner,
            operation.position,
            operation.extension_line1_point,
            operation.extension_line2_point,
            operation.dimension_line_point,
        )
        if value is not None
    ]
    points.extend(operation.vertices or [])
    return points


def _materialize_target_refs(
    source: CadProgramV1Source,
    supplied: list[MaterializedTargetRef | dict[str, Any]] | None,
    expected_owner_id: str | None,
) -> list[MaterializedTargetRef]:
    target_operations = [
        item
        for item in source.operations
        if isinstance(item, (CopyEntitySource, OffsetEntitySource, MoveEntitySource))
    ]
    refs = TypeAdapter(list[MaterializedTargetRef]).validate_python(supplied or [])
    if target_operations:
        if expected_owner_id is None:
            raise ValueError("trusted materialized owner identity is required")
        parsed_owner_id = TypeAdapter(Identifier).validate_python(expected_owner_id)
    else:
        if expected_owner_id is not None:
            raise ValueError("materialized owner identity requires target operations")
        parsed_owner_id = None
    refs_by_id = {item.ref_id: item for item in refs}
    if len(refs_by_id) != len(refs):
        raise ValueError("materialized target ref IDs must be unique")
    requested = {item.target_ref_id for item in target_operations}
    if requested != set(refs_by_id):
        raise ValueError(
            "trusted materialized refs must exactly match source target_ref_id values"
        )
    for ref in refs:
        if (
            ref.owner_id != parsed_owner_id
            or ref.device_id != source.device_id
            or ref.document_id != source.document_id
            or ref.snapshot_id != source.source_snapshot_id
            or ref.document_revision != source.expected_document_revision
        ):
            raise ValueError("materialized target ref does not match source context")
    operations_by_ref: dict[str, list[CadSourceOperation]] = {}
    for operation in target_operations:
        operations_by_ref.setdefault(operation.target_ref_id, []).append(operation)
    for operations in operations_by_ref.values():
        if any(isinstance(item, MoveEntitySource) for item in operations) and len(
            operations
        ) != 1:
            raise ValueError(
                "an in-place target cannot be reused by another operation in one plan"
            )
    return sorted(refs, key=lambda item: item.ref_id)


def _derived_target_capabilities(
    operations: list[ConcreteOperation],
    refs_by_id: dict[str, MaterializedTargetRef],
) -> list[str]:
    capabilities: list[str] = []
    for operation in operations:
        if operation.target_ref_id is None:
            continue
        entity = refs_by_id[operation.target_ref_id].entity_type.lower()
        capability = f"cad.op.{operation.kind.removesuffix('_entity')}.{entity}.v1"
        if capability not in capabilities:
            capabilities.append(capability)
    return capabilities


def compile_cad_program_v1(
    source: CadProgramV1Source | dict[str, Any],
    pins: ExecutionPins | dict[str, Any],
    *,
    compiler_package_hash: Digest,
    materialized_target_refs: (
        list[MaterializedTargetRef | dict[str, Any]] | None
    ) = None,
    materialized_owner_id: str | None = None,
) -> CadExecutionPlanV1:
    parsed = source if isinstance(source, CadProgramV1Source) else parse_cad_program_v1(source)
    parsed_pins = pins if isinstance(pins, ExecutionPins) else ExecutionPins.model_validate(pins)
    if parsed.artifact_refs or parsed.component_refs:
        raise ValueError(
            "artifact/component refs require trusted Gateway materialization before compile"
        )
    target_refs = _materialize_target_refs(
        parsed,
        materialized_target_refs,
        materialized_owner_id,
    )
    refs_by_id = {item.ref_id: item for item in target_refs}
    variables = {item.name: _normalize_value(item.value) for item in parsed.variables}
    layers: dict[str, str] = {}
    operations: list[ConcreteOperation] = []
    for source_operation in parsed.operations:
        for suffix, index, transform in _repeat_transforms(source_operation.repeat, variables):
            operation_id = f"{source_operation.operation_id}{suffix}"
            if len(operation_id) > 128 or not re.fullmatch(_IDENTIFIER, operation_id):
                raise ValueError("expanded operation ID is invalid")
            operations.append(
                _compile_operation(
                    source_operation,
                    operation_id,
                    index,
                    transform,
                    variables,
                    layers,
                )
            )
    budget = _plan_budget(operations, parsed)
    manifest = _effect_manifest(operations, refs_by_id)
    expansion_digest = canonical_expansion_digest(operations)
    effect_digest = canonical_effect_digest(manifest)
    target_refs_digest = canonical_target_refs_digest(target_refs)
    compiler_digest = canonical_compiler_digest()
    validation_profiles_digest = canonical_validation_profiles_digest(
        parsed.validation_profiles
    )
    checkpoint_strategy = manifest.checkpoint_strategy
    checkpoint_strategy_digest = canonical_checkpoint_strategy_digest(
        checkpoint_strategy
    )
    hard_budgets_digest = canonical_hard_budgets_digest(budget)
    payload = {
        "schema_version": CAD_EXECUTION_PLAN_SCHEMA_VERSION,
        "plan_id": f"{parsed.program_id}.r{parsed.program_revision}",
        "source_schema_version": CAD_PROGRAM_V1_SCHEMA_VERSION,
        "source_registry_version": parsed.registry_version,
        "source_program_id": parsed.program_id,
        "source_program_revision": parsed.program_revision,
        "source_digest": parsed.semantic_digest,
        "compiler": {
            "compiler_id": CAD_PROGRAM_V1_COMPILER_ID,
            "compiler_version": CAD_PROGRAM_V1_COMPILER_VERSION,
            "compiler_digest": compiler_digest,
            "compiler_package_hash": compiler_package_hash,
        },
        "device_id": parsed.device_id,
        "document_id": parsed.document_id,
        "source_snapshot_id": parsed.source_snapshot_id,
        "expected_document_revision": parsed.expected_document_revision,
        "operations": [item.model_dump(mode="json", exclude_none=True) for item in operations],
        "expansion_digest": expansion_digest,
        "effect_manifest": manifest.model_dump(mode="json"),
        "effect_manifest_digest": effect_digest,
        "materialized_target_refs": [
            item.model_dump(mode="json") for item in target_refs
        ],
        "target_refs_digest": target_refs_digest,
        "validation_profiles_digest": validation_profiles_digest,
        "checkpoint_strategy_digest": checkpoint_strategy_digest,
        "hard_budgets_digest": hard_budgets_digest,
        "execution_pins": parsed_pins.model_dump(mode="json"),
        "budgets": budget.model_dump(mode="json"),
        "required_capabilities": [
            *parsed.required_capabilities,
            *[
                item
                for item in _derived_target_capabilities(operations, refs_by_id)
                if item not in parsed.required_capabilities
            ],
        ],
        "validation_profiles": parsed.validation_profiles,
        "artifact_refs": [],
        "component_refs": [],
        "checkpoint_strategy": checkpoint_strategy,
    }
    payload["execution_plan_digest"] = _domain_digest(PLAN_DIGEST_DOMAIN, payload)
    return CadExecutionPlanV1.model_validate(payload)


def canonical_source(source: CadProgramV1Source | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, CadProgramV1Source):
        payload = source.model_dump(mode="json", exclude_none=True)
    else:
        payload = dict(source)
    payload.pop("semantic_digest", None)
    return payload


def canonical_source_digest(source: CadProgramV1Source | dict[str, Any]) -> str:
    return _domain_digest(SOURCE_DIGEST_DOMAIN, canonical_source(source))


def seal_cad_program_v1(source: dict[str, Any]) -> CadProgramV1Source:
    payload = dict(source)
    payload.setdefault("schema_version", CAD_PROGRAM_V1_SCHEMA_VERSION)
    payload.setdefault("registry_version", CAD_PROGRAM_V1_REGISTRY_VERSION)
    payload.setdefault("variables", [])
    payload.setdefault("required_capabilities", [])
    payload.setdefault("artifact_refs", [])
    payload.setdefault("component_refs", [])
    payload["variables"] = [
        item.model_dump(mode="json", exclude_none=True)
        for item in TypeAdapter(list[Variable]).validate_python(payload["variables"])
    ]
    payload["operations"] = [
        item.model_dump(mode="json", exclude_none=True)
        for item in TypeAdapter(list[CadSourceOperation]).validate_python(payload["operations"])
    ]
    payload["budgets"] = ProgramV1Budgets.model_validate(
        payload.get("budgets", {})
    ).model_dump(mode="json", exclude_none=True)
    payload["semantic_digest"] = canonical_source_digest(payload)
    return CadProgramV1Source.model_validate(payload)


def parse_cad_program_v1(value: str | bytes | dict[str, Any]) -> CadProgramV1Source:
    if isinstance(value, (str, bytes)):
        return _SOURCE_ADAPTER.validate_python(_strict_json_loads(value))
    return _SOURCE_ADAPTER.validate_python(value)


def canonical_compiler_manifest() -> dict[str, Any]:
    return {
        "compiler_id": CAD_PROGRAM_V1_COMPILER_ID,
        "compiler_version": CAD_PROGRAM_V1_COMPILER_VERSION,
        "expression_ast": "bounded-v1",
        "numeric_model": "decimal-mm-rad-1e-9-half-even",
        "repeat_model": "linear-rectangular-polar-stable-id-v1",
        "operation_registry_versions": [
            CAD_PROGRAM_V1_REGISTRY_VERSION,
            CAD_PROGRAM_V1_PHASE8_REGISTRY_VERSION,
        ],
        "materialized_ref_model": "gateway-sealed-exact-closure-v1",
        "target_operation_model": "copy-offset-move-v1",
    }


def canonical_compiler_digest() -> str:
    return _domain_digest(COMPILER_DIGEST_DOMAIN, canonical_compiler_manifest())


def canonical_expansion_digest(operations: list[ConcreteOperation] | list[dict[str, Any]]) -> str:
    payload = [
        item.model_dump(mode="json", exclude_none=True)
        if isinstance(item, ConcreteOperation)
        else item
        for item in operations
    ]
    return _domain_digest(EXPANSION_DIGEST_DOMAIN, {"operations": payload})


def canonical_effect_digest(manifest: EffectManifest | dict[str, Any]) -> str:
    payload = manifest.model_dump(mode="json") if isinstance(manifest, EffectManifest) else manifest
    return _domain_digest(EFFECT_DIGEST_DOMAIN, payload)


def canonical_target_refs_digest(
    refs: list[MaterializedTargetRef] | list[dict[str, Any]],
) -> str:
    payload = [
        item.model_dump(mode="json") if isinstance(item, MaterializedTargetRef) else item
        for item in refs
    ]
    return _domain_digest(TARGET_REFS_DIGEST_DOMAIN, {"target_refs": payload})


def canonical_validation_profiles_digest(profiles: list[str]) -> str:
    return _domain_digest(
        VALIDATION_PROFILES_DIGEST_DOMAIN,
        {"validation_profiles": profiles},
    )


def canonical_checkpoint_strategy_digest(strategy: str) -> str:
    return _domain_digest(
        CHECKPOINT_STRATEGY_DIGEST_DOMAIN,
        {"checkpoint_strategy": strategy},
    )


def canonical_hard_budgets(
    budgets: ExecutionPlanBudgets | dict[str, Any],
) -> dict[str, int]:
    payload = (
        budgets.model_dump(mode="json")
        if isinstance(budgets, ExecutionPlanBudgets)
        else budgets
    )
    return {
        "max_operations": payload["hard_max_operations"],
        "max_entities": payload["hard_max_entities"],
        "max_vertices": payload["hard_max_vertices"],
        "max_text_bytes": payload["hard_max_text_bytes"],
    }


def canonical_hard_budgets_digest(
    budgets: ExecutionPlanBudgets | dict[str, Any],
) -> str:
    return _domain_digest(
        HARD_BUDGETS_DIGEST_DOMAIN,
        canonical_hard_budgets(budgets),
    )


def canonical_execution_plan(plan: CadExecutionPlanV1 | dict[str, Any]) -> dict[str, Any]:
    payload = (
        plan.model_dump(mode="json", exclude_none=True)
        if isinstance(plan, CadExecutionPlanV1)
        else dict(plan)
    )
    payload.pop("execution_plan_digest", None)
    return payload


def canonical_execution_plan_digest(plan: CadExecutionPlanV1 | dict[str, Any]) -> str:
    return _domain_digest(PLAN_DIGEST_DOMAIN, canonical_execution_plan(plan))


def parse_execution_plan_v1(value: str | bytes | dict[str, Any]) -> CadExecutionPlanV1:
    if isinstance(value, (str, bytes)):
        return _PLAN_ADAPTER.validate_python(_strict_json_loads(value))
    return _PLAN_ADAPTER.validate_python(value)


def canonical_execution_binding(
    binding: ExecutionBindingV1 | dict[str, Any],
) -> dict[str, Any]:
    payload = (
        binding.model_dump(mode="json", exclude_none=True)
        if isinstance(binding, ExecutionBindingV1)
        else {key: value for key, value in binding.items() if value is not None}
    )
    payload.pop("execution_binding_digest", None)
    return payload


def canonical_execution_binding_digest(
    binding: ExecutionBindingV1 | dict[str, Any],
) -> str:
    return _domain_digest(
        EXECUTION_BINDING_DIGEST_DOMAIN,
        canonical_execution_binding(binding),
    )


def canonical_phase8_capability_evidence(
    evidence: Phase8CapabilityEvidence | dict[str, Any],
) -> dict[str, Any]:
    payload = (
        evidence.model_dump(mode="json")
        if isinstance(evidence, Phase8CapabilityEvidence)
        else dict(evidence)
    )
    payload.pop("evidence_digest", None)
    return payload


def canonical_phase8_capability_evidence_digest(
    evidence: Phase8CapabilityEvidence | dict[str, Any],
) -> str:
    return _domain_digest(
        "cad.capability-evidence/1",
        canonical_phase8_capability_evidence(evidence),
    )


def build_execution_binding_v1(
    plan: CadExecutionPlanV1 | dict[str, Any],
    *,
    action: Literal["compile_only", "preview", "commit"] = "compile_only",
    preview_id: str | None = None,
    preview_expires_at: str | None = None,
    receipt_id: str | None = None,
) -> ExecutionBindingV1:
    parsed = plan if isinstance(plan, CadExecutionPlanV1) else parse_execution_plan_v1(plan)
    payload = {
        "schema_version": "cad.execution-binding/1",
        "action": action,
        "source_schema_version": parsed.source_schema_version,
        "source_registry_version": parsed.source_registry_version,
        "source_program_id": parsed.source_program_id,
        "source_program_revision": parsed.source_program_revision,
        "source_digest": parsed.source_digest,
        "compiler_id": parsed.compiler.compiler_id,
        "compiler_version": parsed.compiler.compiler_version,
        "compiler_digest": parsed.compiler.compiler_digest,
        "compiler_package_hash": parsed.compiler.compiler_package_hash,
        "plan_schema_version": parsed.schema_version,
        "execution_plan_digest": parsed.execution_plan_digest,
        "expansion_digest": parsed.expansion_digest,
        "effect_manifest_digest": parsed.effect_manifest_digest,
        "target_refs_digest": parsed.target_refs_digest,
        "validation_profiles_digest": parsed.validation_profiles_digest,
        "checkpoint_strategy_digest": parsed.checkpoint_strategy_digest,
        "hard_budgets_digest": parsed.hard_budgets_digest,
        **parsed.execution_pins.model_dump(mode="json"),
        "device_id": parsed.device_id,
        "document_id": parsed.document_id,
        "source_snapshot_id": parsed.source_snapshot_id,
        "document_revision": parsed.expected_document_revision,
    }
    if preview_id is not None:
        payload["preview_id"] = preview_id
    if preview_expires_at is not None:
        payload["preview_expires_at"] = preview_expires_at
    if receipt_id is not None:
        payload["receipt_id"] = receipt_id
    payload["execution_binding_digest"] = canonical_execution_binding_digest(payload)
    return ExecutionBindingV1.model_validate(payload)


def parse_execution_binding_v1(
    value: str | bytes | dict[str, Any],
) -> ExecutionBindingV1:
    if isinstance(value, (str, bytes)):
        return _BINDING_ADAPTER.validate_python(_strict_json_loads(value))
    return _BINDING_ADAPTER.validate_python(value)


def verify_execution_binding_v1(
    binding: ExecutionBindingV1 | dict[str, Any],
    plan: CadExecutionPlanV1 | dict[str, Any],
    *,
    expected_action: Literal["compile_only", "preview", "commit"] = "compile_only",
    expected_preview_id: str | None = None,
    expected_preview_expires_at: str | None = None,
    expected_receipt_id: str | None = None,
) -> ExecutionBindingV1:
    parsed_binding = (
        binding
        if isinstance(binding, ExecutionBindingV1)
        else parse_execution_binding_v1(binding)
    )
    parsed_plan = (
        plan if isinstance(plan, CadExecutionPlanV1) else parse_execution_plan_v1(plan)
    )
    expected = {
        "source_schema_version": parsed_plan.source_schema_version,
        "source_registry_version": parsed_plan.source_registry_version,
        "source_program_id": parsed_plan.source_program_id,
        "source_program_revision": parsed_plan.source_program_revision,
        "source_digest": parsed_plan.source_digest,
        "compiler_id": parsed_plan.compiler.compiler_id,
        "compiler_version": parsed_plan.compiler.compiler_version,
        "compiler_digest": parsed_plan.compiler.compiler_digest,
        "compiler_package_hash": parsed_plan.compiler.compiler_package_hash,
        "plan_schema_version": parsed_plan.schema_version,
        "execution_plan_digest": parsed_plan.execution_plan_digest,
        "expansion_digest": parsed_plan.expansion_digest,
        "effect_manifest_digest": parsed_plan.effect_manifest_digest,
        "target_refs_digest": parsed_plan.target_refs_digest,
        "validation_profiles_digest": parsed_plan.validation_profiles_digest,
        "checkpoint_strategy_digest": parsed_plan.checkpoint_strategy_digest,
        "hard_budgets_digest": parsed_plan.hard_budgets_digest,
        "device_id": parsed_plan.device_id,
        "document_id": parsed_plan.document_id,
        "source_snapshot_id": parsed_plan.source_snapshot_id,
        "document_revision": parsed_plan.expected_document_revision,
        **parsed_plan.execution_pins.model_dump(mode="json"),
    }
    actual = parsed_binding.model_dump(mode="json")
    if any(actual[field] != value for field, value in expected.items()):
        raise ValueError("execution binding does not match sealed plan")
    if (
        parsed_binding.action != expected_action
        or parsed_binding.preview_id != expected_preview_id
        or parsed_binding.preview_expires_at != expected_preview_expires_at
        or parsed_binding.receipt_id != expected_receipt_id
    ):
        raise ValueError("execution binding does not match requested action")
    return parsed_binding


def cad_program_v1_json_schema() -> dict[str, Any]:
    schema = CadProgramV1Source.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://schemas.kythuatvang.local/cad.program/1.0/schema.json"
    schema["title"] = "Immutable CAD Program 1.0 source contract"
    return schema


def cad_execution_plan_v1_json_schema() -> dict[str, Any]:
    schema = CadExecutionPlanV1.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://schemas.kythuatvang.local/cad.execution-plan/1/schema.json"
    schema["title"] = "Sealed CAD execution plan 1 contract"
    return schema


def cad_execution_binding_v1_json_schema() -> dict[str, Any]:
    schema = ExecutionBindingV1.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://schemas.kythuatvang.local/cad.execution-binding/1/schema.json"
    schema["title"] = "Exact CAD execution binding 1 contract"
    return schema


def _digest(payload: Any) -> str:
    return f"sha256:{sha256(_canonical_json(payload).encode('utf-8')).hexdigest()}"


def _domain_digest(domain: str, payload: Any) -> str:
    return _digest({"domain": domain, "payload": payload})


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed


def _canonical_json(value: Any) -> str:
    # Imported lazily so the Phase 8 types can be used by agent_protocol without
    # creating a module-import cycle. The protocol remains the single canonical
    # JSON implementation for all wire digests.
    from .agent_protocol import canonical_json

    return canonical_json(value)


def _strict_json_loads(value: str | bytes) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(
        value,
        object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
    )
