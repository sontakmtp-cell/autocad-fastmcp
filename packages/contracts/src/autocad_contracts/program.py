"""Strict, runtime-neutral ``cad.program/0.2`` contracts."""

from __future__ import annotations

import re
from hashlib import sha256
from typing import Annotated, Any, Literal, TypeAlias, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from .agent_protocol import canonical_json


CAD_PROGRAM_SCHEMA_VERSION = "cad.program/0.2"
CAD_PROGRAM_REGISTRY_VERSION = "cad.program/0.2"
MAX_PROGRAM_OPERATIONS = 256
MAX_PROGRAM_ENTITIES = 256
MAX_PROGRAM_LAYERS = 64
MAX_PROGRAM_VERTICES = 4096
MAX_PROGRAM_TEXT_BYTES = 65_536
MAX_PROGRAM_PAYLOAD_BYTES = 1_048_576
MAX_PROGRAM_RESULT_BYTES = 1_048_576
MAX_PROGRAM_ARTIFACT_BYTES = 5_242_880
MAX_COORDINATE_ABS = 1_000_000_000.0
MAX_POSITIVE_MEASURE = 1_000_000_000.0

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LAYER_NAME = re.compile(r'^[^<>/\\":;?*|=`\x00-\x1f]+$')
_SHA256 = r"^sha256:[0-9a-f]{64}$"


class ProgramModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=_IDENTIFIER.pattern)]
RevisionToken = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^\S+$")]
LayerName = Annotated[str, Field(min_length=1, max_length=255, pattern=_LAYER_NAME.pattern)]
FiniteCoordinate = Annotated[
    float,
    Field(ge=-MAX_COORDINATE_ABS, le=MAX_COORDINATE_ABS, allow_inf_nan=False),
]
PositiveMeasure = Annotated[
    float,
    Field(gt=0, le=MAX_POSITIVE_MEASURE, allow_inf_nan=False),
]


class Point3d(ProgramModel):
    x: FiniteCoordinate
    y: FiniteCoordinate
    z: FiniteCoordinate


class LayerOutputRef(ProgramModel):
    operation_id: Identifier
    output: Literal["layer"] = "layer"


LayerTarget: TypeAlias = Union[LayerName, LayerOutputRef]


class EnsureLayerOperation(ProgramModel):
    kind: Literal["ensure_layer"]
    operation_id: Identifier
    name: LayerName
    color_index: int | None = Field(default=None, ge=1, le=255)


class CreateLineOperation(ProgramModel):
    kind: Literal["create_line"]
    operation_id: Identifier
    layer: LayerTarget
    start: Point3d
    end: Point3d


class CreateCircleOperation(ProgramModel):
    kind: Literal["create_circle"]
    operation_id: Identifier
    layer: LayerTarget
    center: Point3d
    radius: PositiveMeasure


class CreatePolylineOperation(ProgramModel):
    kind: Literal["create_polyline"]
    operation_id: Identifier
    layer: LayerTarget
    vertices: list[Point3d] = Field(min_length=2, max_length=MAX_PROGRAM_VERTICES)
    closed: bool = False


class CreateRectangleOperation(ProgramModel):
    kind: Literal["create_rectangle"]
    operation_id: Identifier
    layer: LayerTarget
    first_corner: Point3d
    opposite_corner: Point3d


class CreateTextOperation(ProgramModel):
    kind: Literal["create_text"]
    operation_id: Identifier
    layer: LayerTarget
    position: Point3d
    text: str = Field(min_length=1, max_length=MAX_PROGRAM_TEXT_BYTES)
    height: PositiveMeasure
    rotation_radians: float = Field(
        default=0.0,
        ge=-6.283185307179586,
        le=6.283185307179586,
        allow_inf_nan=False,
    )

    @field_validator("text")
    @classmethod
    def _text_is_bounded_utf8(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_PROGRAM_TEXT_BYTES:
            raise ValueError("text exceeds the UTF-8 byte limit")
        return value


class CreateDimensionLinearOperation(ProgramModel):
    kind: Literal["create_dimension_linear"]
    operation_id: Identifier
    layer: LayerTarget
    extension_line1_point: Point3d
    extension_line2_point: Point3d
    dimension_line_point: Point3d
    text_override: str | None = Field(default=None, max_length=1024)

    @field_validator("text_override")
    @classmethod
    def _override_is_bounded_utf8(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 4096:
            raise ValueError("dimension text override exceeds the UTF-8 byte limit")
        return value


CadOperation: TypeAlias = Annotated[
    Union[
        EnsureLayerOperation,
        CreateLineOperation,
        CreateCircleOperation,
        CreatePolylineOperation,
        CreateRectangleOperation,
        CreateTextOperation,
        CreateDimensionLinearOperation,
    ],
    Field(discriminator="kind"),
]


OPERATION_REGISTRY = (
    "ensure_layer",
    "create_line",
    "create_circle",
    "create_polyline",
    "create_rectangle",
    "create_text",
    "create_dimension_linear",
)


class OperationRegistryEntry(ProgramModel):
    kind: str = Field(min_length=1, max_length=64)
    effect: Literal["create"]
    output: Literal["layer", "entity"]


def canonical_operation_registry() -> dict[str, Any]:
    entries = [
        OperationRegistryEntry(
            kind=kind,
            effect="create",
            output="layer" if kind == "ensure_layer" else "entity",
        ).model_dump(mode="json")
        for kind in OPERATION_REGISTRY
    ]
    return {"registry_version": CAD_PROGRAM_REGISTRY_VERSION, "operations": entries}


def operation_registry_digest() -> str:
    encoded = canonical_json(canonical_operation_registry()).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


class DocumentRevisionPrecondition(ProgramModel):
    kind: Literal["document_revision_equals"]
    document_id: Identifier
    expected_document_revision: RevisionToken


class EntityCountPostcondition(ProgramModel):
    kind: Literal["entity_count"]
    expected_created: int = Field(ge=0, le=MAX_PROGRAM_ENTITIES)


class LayerExistsPostcondition(ProgramModel):
    kind: Literal["layer_exists"]
    layer: LayerTarget


ProgramPrecondition: TypeAlias = Annotated[
    DocumentRevisionPrecondition,
    Field(discriminator="kind"),
]
ProgramPostcondition: TypeAlias = Annotated[
    Union[EntityCountPostcondition, LayerExistsPostcondition],
    Field(discriminator="kind"),
]


class ProgramBudgets(ProgramModel):
    max_operations: int = Field(default=MAX_PROGRAM_OPERATIONS, ge=1, le=MAX_PROGRAM_OPERATIONS)
    max_entities: int = Field(default=MAX_PROGRAM_ENTITIES, ge=1, le=MAX_PROGRAM_ENTITIES)
    max_layers: int = Field(default=MAX_PROGRAM_LAYERS, ge=1, le=MAX_PROGRAM_LAYERS)
    max_vertices: int = Field(default=MAX_PROGRAM_VERTICES, ge=2, le=MAX_PROGRAM_VERTICES)
    max_text_bytes: int = Field(default=MAX_PROGRAM_TEXT_BYTES, ge=1, le=MAX_PROGRAM_TEXT_BYTES)
    max_payload_bytes: int = Field(
        default=MAX_PROGRAM_PAYLOAD_BYTES,
        ge=1024,
        le=MAX_PROGRAM_PAYLOAD_BYTES,
    )
    max_result_bytes: int = Field(
        default=MAX_PROGRAM_RESULT_BYTES,
        ge=1024,
        le=MAX_PROGRAM_RESULT_BYTES,
    )
    max_artifact_bytes: int = Field(
        default=MAX_PROGRAM_ARTIFACT_BYTES,
        ge=1024,
        le=MAX_PROGRAM_ARTIFACT_BYTES,
    )
    max_coordinate_abs: float = Field(
        default=MAX_COORDINATE_ABS,
        gt=0,
        le=MAX_COORDINATE_ABS,
        allow_inf_nan=False,
    )
    max_radius: float = Field(
        default=MAX_POSITIVE_MEASURE,
        gt=0,
        le=MAX_POSITIVE_MEASURE,
        allow_inf_nan=False,
    )
    max_text_height: float = Field(
        default=MAX_POSITIVE_MEASURE,
        gt=0,
        le=MAX_POSITIVE_MEASURE,
        allow_inf_nan=False,
    )
    execution_deadline_seconds: int = Field(default=120, ge=1, le=300)
    preview_ttl_seconds: int = Field(default=900, ge=1, le=3600)


class CadProgram(ProgramModel):
    """Gateway-owned semantic program.

    Runtime, package, capability, registry hash, policy, and execution digest
    deliberately do not exist in this runtime-neutral model.
    """

    schema_version: Literal["cad.program/0.2"] = CAD_PROGRAM_SCHEMA_VERSION
    registry_version: Literal["cad.program/0.2"] = CAD_PROGRAM_REGISTRY_VERSION
    program_id: Identifier
    program_revision: int = Field(ge=1)
    device_id: Identifier
    source_snapshot_id: Identifier
    document_id: Identifier
    expected_document_revision: RevisionToken
    operations: list[CadOperation] = Field(min_length=1, max_length=MAX_PROGRAM_OPERATIONS)
    preconditions: list[ProgramPrecondition] = Field(default_factory=list, max_length=16)
    postconditions: list[ProgramPostcondition] = Field(default_factory=list, max_length=64)
    budgets: ProgramBudgets = Field(default_factory=ProgramBudgets)

    @model_validator(mode="after")
    def _validate_program_semantics(self) -> "CadProgram":
        operation_ids = [operation.operation_id for operation in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("operation_id values must be unique")
        for precondition in self.preconditions:
            if (
                precondition.document_id != self.document_id
                or precondition.expected_document_revision != self.expected_document_revision
            ):
                raise ValueError("document revision precondition must match program binding")

        seen: dict[str, str] = {}
        vertices = 0
        text_bytes = 0
        entity_count = 0
        layer_count = 0
        points: list[Point3d] = []
        for operation in self.operations:
            layer = getattr(operation, "layer", None)
            if isinstance(layer, LayerOutputRef):
                if seen.get(layer.operation_id) != "layer":
                    raise ValueError("layer reference must target an earlier ensure_layer output")

            if isinstance(operation, EnsureLayerOperation):
                layer_count += 1
                output = "layer"
            else:
                entity_count += 1
                output = "entity"

            if isinstance(operation, CreateLineOperation):
                points.extend((operation.start, operation.end))
            elif isinstance(operation, CreateCircleOperation):
                points.append(operation.center)
                if operation.radius > self.budgets.max_radius:
                    raise ValueError("circle radius exceeds program budget")
            elif isinstance(operation, CreatePolylineOperation):
                vertices += len(operation.vertices)
                points.extend(operation.vertices)
            elif isinstance(operation, CreateRectangleOperation):
                vertices += 4
                points.extend((operation.first_corner, operation.opposite_corner))
            elif isinstance(operation, CreateTextOperation):
                points.append(operation.position)
                text_bytes += len(operation.text.encode("utf-8"))
                if operation.height > self.budgets.max_text_height:
                    raise ValueError("text height exceeds program budget")
            elif isinstance(operation, CreateDimensionLinearOperation):
                points.extend(
                    (
                        operation.extension_line1_point,
                        operation.extension_line2_point,
                        operation.dimension_line_point,
                    )
                )
                if operation.text_override is not None:
                    text_bytes += len(operation.text_override.encode("utf-8"))

            seen[operation.operation_id] = output

        for postcondition in self.postconditions:
            if isinstance(postcondition, LayerExistsPostcondition):
                layer = postcondition.layer
                if isinstance(layer, LayerOutputRef) and seen.get(layer.operation_id) != "layer":
                    raise ValueError("postcondition layer reference must target ensure_layer output")

        if len(self.operations) > self.budgets.max_operations:
            raise ValueError("operation count exceeds program budget")
        if entity_count > self.budgets.max_entities:
            raise ValueError("entity count exceeds program budget")
        if layer_count > self.budgets.max_layers:
            raise ValueError("layer count exceeds program budget")
        if vertices > self.budgets.max_vertices:
            raise ValueError("vertex count exceeds program budget")
        if text_bytes > self.budgets.max_text_bytes:
            raise ValueError("text bytes exceed program budget")
        if any(
            abs(coordinate) > self.budgets.max_coordinate_abs
            for point in points
            for coordinate in (point.x, point.y, point.z)
        ):
            raise ValueError("coordinate exceeds program budget")

        canonical = canonical_json(self.model_dump(mode="json", exclude_none=True))
        if len(canonical.encode("utf-8")) > self.budgets.max_payload_bytes:
            raise ValueError("program payload exceeds program budget")
        return self


class SealedCadProgram(ProgramModel):
    program: CadProgram
    program_digest: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _digest_matches(self) -> "SealedCadProgram":
        if self.program_digest != canonical_program_digest(self.program):
            raise ValueError("program_digest does not match canonical program")
        return self


_PROGRAM_ADAPTER = TypeAdapter(CadProgram)


def parse_cad_program(value: str | bytes | dict[str, Any]) -> CadProgram:
    if isinstance(value, (str, bytes)):
        return _PROGRAM_ADAPTER.validate_json(value)
    return _PROGRAM_ADAPTER.validate_python(value)


def canonical_program(program: CadProgram | dict[str, Any]) -> dict[str, Any]:
    parsed = program if isinstance(program, CadProgram) else CadProgram.model_validate(program)
    return parsed.model_dump(mode="json", exclude_none=True)


def canonical_program_digest(program: CadProgram | dict[str, Any]) -> str:
    encoded = canonical_json(canonical_program(program)).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def seal_program(program: CadProgram | dict[str, Any]) -> SealedCadProgram:
    parsed = program if isinstance(program, CadProgram) else CadProgram.model_validate(program)
    return SealedCadProgram(program=parsed, program_digest=canonical_program_digest(parsed))


def cad_program_json_schema() -> dict[str, Any]:
    schema = CadProgram.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://schemas.kythuatvang.local/cad.program/0.2/schema.json"
    schema["title"] = "CAD Program 0.2 create-only runtime-neutral contract"
    return schema
