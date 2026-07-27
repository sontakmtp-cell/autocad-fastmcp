"""Gateway adapter for the shared ``cad.program/0.2`` contract.

The shared package owns the wire contract.  This module keeps Gateway business
logic isolated from concrete model names while Subagent 1 evolves that package.
The bounded fallback mirrors the Phase 6 create-only envelope and is removed
from the execution path automatically when the shared validator is available.
"""

from __future__ import annotations

import importlib
import math
import re
from copy import deepcopy
from hashlib import sha256
from typing import Any, Callable

from autocad_contracts import (
    ProgramCommandMessage,
    canonical_json,
    normalize_sha256_digest,
    program_command_payload_hash,
    validate_bounded_json,
)


SCHEMA_VERSION = "cad.program/0.2"
MAX_OPERATIONS = 256
MAX_VERTICES = 4096
MAX_TEXT_BYTES = 16_384
MAX_PAYLOAD_BYTES = 512_000
MAX_RESULT_BYTES = 256_000
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_ENTITY_COUNT = 256
MAX_LAYER_COUNT = 64
MAX_COORDINATE = 1_000_000_000.0
MAX_MEASURE = 1_000_000_000.0
MAX_EXECUTION_SECONDS = 300
MAX_PREVIEW_TTL_SECONDS = 1800

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LAYER_FORBIDDEN = re.compile(r'[<>/\\":;?*|=`]')
_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_ALLOWED_KINDS = frozenset(
    {
        "ensure_layer",
        "create_line",
        "create_circle",
        "create_polyline",
        "create_rectangle",
        "create_text",
        "create_dimension_linear",
    }
)
_CREATE_ENTITY_KINDS = _ALLOWED_KINDS - {"ensure_layer"}
_FIELDS: dict[str, frozenset[str]] = {
    "ensure_layer": frozenset({"kind", "operation_id", "name", "color_index"}),
    "create_line": frozenset({"kind", "operation_id", "layer", "start", "end"}),
    "create_circle": frozenset(
        {"kind", "operation_id", "layer", "center", "radius"}
    ),
    "create_polyline": frozenset(
        {"kind", "operation_id", "layer", "vertices", "closed"}
    ),
    "create_rectangle": frozenset(
        {"kind", "operation_id", "layer", "first_corner", "opposite_corner"}
    ),
    "create_text": frozenset(
        {
            "kind",
            "operation_id",
            "layer",
            "position",
            "text",
            "height",
            "rotation_radians",
        }
    ),
    "create_dimension_linear": frozenset(
        {
            "kind",
            "operation_id",
            "layer",
            "extension_line1_point",
            "extension_line2_point",
            "dimension_line_point",
            "text_override",
        }
    ),
}
_REQUIRED: dict[str, frozenset[str]] = {
    "ensure_layer": frozenset({"kind", "operation_id", "name"}),
    "create_line": _FIELDS["create_line"],
    "create_circle": _FIELDS["create_circle"],
    "create_polyline": _FIELDS["create_polyline"],
    "create_rectangle": _FIELDS["create_rectangle"],
    "create_text": _FIELDS["create_text"] - {"rotation_radians"},
    "create_dimension_linear": _FIELDS["create_dimension_linear"] - {"text_override"},
}
_DEFAULT_BUDGETS: dict[str, int] = {
    "max_operations": MAX_OPERATIONS,
    "max_entities": MAX_ENTITY_COUNT,
    "max_layers": MAX_LAYER_COUNT,
    "max_vertices": MAX_VERTICES,
    "max_text_bytes": MAX_TEXT_BYTES,
    "max_payload_bytes": MAX_PAYLOAD_BYTES,
    "max_result_bytes": MAX_RESULT_BYTES,
    "max_artifact_bytes": MAX_ARTIFACT_BYTES,
    "execution_deadline_seconds": MAX_EXECUTION_SECONDS,
    "preview_ttl_seconds": 900,
}


class ProgramContractError(ValueError):
    """The semantic program is not valid ``cad.program/0.2``."""


def _shared_validator() -> Callable[[dict[str, Any]], Any] | None:
    candidates = (
        ("autocad_contracts.cad_program", "validate_program"),
        ("autocad_contracts.cad_program", "validate_cad_program"),
        ("autocad_contracts", "validate_cad_program"),
    )
    for module_name, attribute in candidates:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        validator = getattr(module, attribute, None)
        if callable(validator):
            return validator
    return None


def _shared_model() -> type[Any] | None:
    candidates = (
        ("autocad_contracts.cad_program", "CadProgramV02"),
        ("autocad_contracts.cad_program", "CadProgram"),
        ("autocad_contracts", "CadProgramV02"),
    )
    for module_name, attribute in candidates:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        model = getattr(module, attribute, None)
        if isinstance(model, type) and hasattr(model, "model_validate"):
            return model
    return None


def canonical_digest(value: dict[str, Any]) -> str:
    return "sha256:" + sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_program_digest_value(value: dict[str, Any]) -> str:
    try:
        module = importlib.import_module("autocad_contracts")
        digest = getattr(module, "canonical_program_digest")
    except (ImportError, AttributeError):
        return canonical_digest(value)
    return str(digest(value))


def program_command_fields(
    *,
    kind: str,
    effect_class: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Project one durable Program job onto the shared typed wire contract."""

    execution = payload["execution"]
    pins = execution["pins"]
    binding = {
        "program_digest": normalize_sha256_digest(
            execution["program_digest"], allow_legacy_raw=False
        ),
        "execution_digest": normalize_sha256_digest(
            execution["execution_digest"], allow_legacy_raw=False
        ),
        "document_id": execution["document_id"],
        "document_revision": execution["expected_document_revision"],
        "runtime_id": pins["runtime_id"],
        "runtime_role": pins["runtime_role"],
        "host_family": pins["host_family"],
        "host_version": pins["host_version"],
        "package_id": pins["package_id"],
        "package_version": pins["package_version"],
        "package_hash": normalize_sha256_digest(pins["package_hash"]),
        "capability_manifest_hash": normalize_sha256_digest(
            pins["capability_manifest_hash"]
        ),
        "operation_registry_version": pins["registry_version"],
        "operation_registry_hash": normalize_sha256_digest(
            pins["operation_registry_hash"]
        ),
        "policy_version": pins["policy_version"],
    }
    values: dict[str, Any] = {
        "kind": kind,
        "effect_class": effect_class,
        "binding": binding,
    }
    if kind in {"program_preview", "program_commit"}:
        values["program"] = payload["program"]
    if kind == "program_preview":
        values["preview_id"] = execution["preview_id"]
    if kind == "program_commit":
        values["preview_id"] = execution["preview_id"]
        values["preview_digest"] = normalize_sha256_digest(
            execution["preview_digest"], allow_legacy_raw=False
        )
        values["receipt_id"] = execution["receipt_id"]
    if kind == "program_validate":
        values["validation"] = payload["validation"]
    return values


def program_wire_payload_hash(
    *,
    kind: str,
    effect_class: str,
    payload: dict[str, Any],
) -> str:
    """Hash exactly the shared ProgramCommand projection consumed by the Agent."""

    command = ProgramCommandMessage(
        protocol_version="cad.agent/2",
        session_id="hash-session",
        device_id="hash-device",
        job_id="hash-job",
        command_id="hash-command",
        idempotency_key="hash-idempotency",
        payload_hash="0" * 64,
        **program_command_fields(
            kind=kind,
            effect_class=effect_class,
            payload=payload,
        ),
    )
    return program_command_payload_hash(command)


def merge_budgets(overrides: dict[str, Any] | None) -> dict[str, Any]:
    overrides = dict(overrides or {})
    try:
        module = importlib.import_module("autocad_contracts")
        budget_model = getattr(module, "ProgramBudgets")
    except (ImportError, AttributeError):
        budget_model = None
    if budget_model is not None:
        try:
            return budget_model.model_validate(overrides, strict=True).model_dump(
                mode="json"
            )
        except Exception as error:
            raise ProgramContractError(
                "budget override violates cad.program/0.2"
            ) from error
    unknown = set(overrides) - set(_DEFAULT_BUDGETS)
    if unknown:
        raise ProgramContractError("unknown budget field")
    result = dict(_DEFAULT_BUDGETS)
    for key, value in overrides.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ProgramContractError("budget values must be positive integers")
        if value > _DEFAULT_BUDGETS[key]:
            raise ProgramContractError("budget override exceeds the hard ceiling")
        result[key] = value
    if result["preview_ttl_seconds"] > MAX_PREVIEW_TTL_SECONDS:
        raise ProgramContractError("preview TTL exceeds the hard ceiling")
    return result


def build_semantic_program(
    *,
    program_id: str,
    program_revision: int,
    device_id: str,
    source_snapshot_id: str,
    document_id: str,
    expected_document_revision: str,
    registry_version: str,
    operations: list[dict[str, Any]],
    postconditions: list[dict[str, Any]] | None,
    budget_overrides: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    budgets = merge_budgets(budget_overrides)
    semantic = {
        "schema_version": SCHEMA_VERSION,
        "program_id": _identifier(program_id, "program_id"),
        "program_revision": program_revision,
        "device_id": _identifier(device_id, "device_id"),
        "source_snapshot_id": _identifier(source_snapshot_id, "source_snapshot_id"),
        "document_id": _identifier(document_id, "document_id"),
        "expected_document_revision": _bounded_text(
            expected_document_revision, "expected_document_revision", 256
        ),
        "registry_version": _bounded_text(registry_version, "registry_version", 64),
        "operations": deepcopy(operations),
        "preconditions": [
            {
                "kind": "document_revision_equals",
                "document_id": document_id,
                "expected_document_revision": expected_document_revision,
            }
        ],
        "postconditions": deepcopy(postconditions or []),
        "budgets": budgets,
    }
    if not isinstance(program_revision, int) or isinstance(program_revision, bool) or program_revision < 1:
        raise ProgramContractError("program_revision must be a positive integer")
    canonical_shared = _canonicalize_with_shared_contract(semantic)
    if canonical_shared is None:
        semantic["operations"] = _validate_operations(operations, budgets)
        semantic["postconditions"] = _validate_postconditions(postconditions or [])
        validate_bounded_json(semantic)
        if len(canonical_json(semantic).encode("utf-8")) > budgets["max_payload_bytes"]:
            raise ProgramContractError("program payload exceeds its byte budget")
    else:
        semantic = canonical_shared
        budgets = dict(semantic["budgets"])
    required = sorted(
        {"program." + operation["kind"] for operation in semantic["operations"]}
        | {"program.preview", "program.commit", "program.validate"}
    )
    return semantic, budgets, required


def validate_pin_set(pins: dict[str, Any]) -> dict[str, str]:
    required = {
        "runtime_id",
        "runtime_role",
        "host_family",
        "host_version",
        "package_id",
        "package_version",
        "package_hash",
        "capability_manifest_hash",
        "operation_registry_hash",
        "registry_version",
        "policy_version",
    }
    if set(pins) != required:
        raise ProgramContractError("execution pin set is incomplete")
    result = {
        key: _bounded_text(str(value), key, 128)
        for key, value in pins.items()
    }
    for key in {
        "package_hash",
        "capability_manifest_hash",
        "operation_registry_hash",
    }:
        if _SHA256.fullmatch(result[key]) is None:
            raise ProgramContractError(f"{key} is malformed")
    if result["runtime_id"] != "managed_dotnet" or result["host_family"] != "R25":
        raise ProgramContractError("Phase 6 write requires Managed .NET R25")
    return result


def operation_registry_digest_value() -> str | None:
    try:
        module = importlib.import_module("autocad_contracts")
        digest = getattr(module, "operation_registry_digest")
    except (ImportError, AttributeError):
        return None
    return str(digest())


def binding_digest(
    *,
    program_digest: str,
    document_id: str,
    expected_document_revision: str,
    pins: dict[str, str],
) -> str:
    return canonical_digest(
        {
            "program_digest": program_digest,
            "document_id": document_id,
            "expected_document_revision": expected_document_revision,
            "pins": validate_pin_set(pins),
        }
    )


def execution_digest(
    *,
    action: str,
    program_digest: str,
    binding_digest_value: str,
    nonce_id: str,
) -> str:
    if action not in {"preview", "commit", "validate"}:
        raise ProgramContractError("unsupported execution action")
    return canonical_digest(
        {
            "action": action,
            "program_digest": program_digest,
            "binding_digest": binding_digest_value,
            "nonce_id": _identifier(nonce_id, "nonce_id"),
        }
    )


def _canonicalize_with_shared_contract(
    semantic: dict[str, Any],
) -> dict[str, Any] | None:
    validator = _shared_validator()
    model = _shared_model()
    try:
        if validator is not None:
            value = validator(deepcopy(semantic))
            if not isinstance(value, dict):
                raise ProgramContractError("shared validator did not return canonical JSON")
            return value
        elif model is not None:
            parsed = model.model_validate(deepcopy(semantic), strict=True)
            return parsed.model_dump(mode="json", exclude_none=True)
    except Exception as error:
        raise ProgramContractError("shared cad.program/0.2 validation failed") from error
    return None


def _validate_operations(
    operations: list[dict[str, Any]], budgets: dict[str, int]
) -> list[dict[str, Any]]:
    if not isinstance(operations, list) or not 1 <= len(operations) <= budgets["max_operations"]:
        raise ProgramContractError("operation count is outside the budget")
    if len(operations) > MAX_OPERATIONS:
        raise ProgramContractError("operation count exceeds the hard ceiling")
    result: list[dict[str, Any]] = []
    operation_ids: set[str] = set()
    vertices = 0
    text_bytes = 0
    entity_count = 0
    layer_names: set[str] = set()
    for raw in operations:
        if not isinstance(raw, dict):
            raise ProgramContractError("operations must be objects")
        kind = raw.get("kind")
        if kind not in _ALLOWED_KINDS:
            raise ProgramContractError("unsupported operation")
        if set(raw) - _FIELDS[kind] or not _REQUIRED[kind].issubset(raw):
            raise ProgramContractError("operation fields do not match the strict contract")
        item = deepcopy(raw)
        operation_id = _identifier(item.get("operation_id"), "operation_id")
        if operation_id in operation_ids:
            raise ProgramContractError("operation_id values must be unique")
        operation_ids.add(operation_id)
        if kind == "ensure_layer":
            _layer(item.get("name"))
            color = item.get("color_index")
            if color is not None and (
                isinstance(color, bool) or not isinstance(color, int) or not 1 <= color <= 255
            ):
                raise ProgramContractError("color_index is invalid")
            layer_names.add(item["name"])
        else:
            _layer(item.get("layer"))
            layer_names.add(item["layer"])
            entity_count += 1
        for field in (
            "start",
            "end",
            "center",
            "position",
            "first_corner",
            "opposite_corner",
            "extension_line1_point",
            "extension_line2_point",
            "dimension_line_point",
        ):
            if field in item:
                _point(item[field], field)
        if kind == "create_circle":
            _positive_measure(item.get("radius"), "radius")
        if kind == "create_polyline":
            values = item.get("vertices")
            if not isinstance(values, list) or not 2 <= len(values) <= MAX_VERTICES:
                raise ProgramContractError("polyline vertices are invalid")
            for point in values:
                _point(point, "vertices")
            vertices += len(values)
            if not isinstance(item.get("closed"), bool):
                raise ProgramContractError("closed must be boolean")
        if kind == "create_text":
            text = _bounded_text(item.get("text"), "text", MAX_TEXT_BYTES)
            text_bytes += len(text.encode("utf-8"))
            _positive_measure(item.get("height"), "height")
            rotation = item.get("rotation_radians", 0.0)
            _finite_number(rotation, "rotation_radians", minimum=-math.tau, maximum=math.tau)
            item.setdefault("rotation_radians", 0.0)
        if kind == "create_dimension_linear" and item.get("text_override") is not None:
            override = _bounded_text(item["text_override"], "text_override", 4096)
            text_bytes += len(override.encode("utf-8"))
        result.append(item)
    if entity_count > budgets["max_entities"] or entity_count > MAX_ENTITY_COUNT:
        raise ProgramContractError("entity budget exceeded")
    if len(layer_names) > budgets["max_layers"] or len(layer_names) > MAX_LAYER_COUNT:
        raise ProgramContractError("layer budget exceeded")
    if vertices > budgets["max_vertices"] or vertices > MAX_VERTICES:
        raise ProgramContractError("vertex budget exceeded")
    if text_bytes > budgets["max_text_bytes"] or text_bytes > MAX_TEXT_BYTES:
        raise ProgramContractError("text byte budget exceeded")
    return result


def _validate_postconditions(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(values, list) or len(values) > 64:
        raise ProgramContractError("postconditions exceed the bounded limit")
    result: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            raise ProgramContractError("postconditions must be objects")
        validate_bounded_json(value)
        result.append(deepcopy(value))
    return result


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value.strip()) is None:
        raise ProgramContractError(f"{field} is malformed")
    return value.strip()


def _bounded_text(value: Any, field: str, byte_limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > byte_limit
    ):
        raise ProgramContractError(f"{field} is invalid")
    return value


def _layer(value: Any) -> str:
    result = _bounded_text(value, "layer", 255)
    if _LAYER_FORBIDDEN.search(result):
        raise ProgramContractError("layer name is invalid")
    return result


def _point(value: Any, field: str) -> dict[str, float | int]:
    if not isinstance(value, dict) or set(value) != {"x", "y", "z"}:
        raise ProgramContractError(f"{field} must be a strict 3D point")
    for coordinate in value.values():
        _finite_number(
            coordinate,
            field,
            minimum=-MAX_COORDINATE,
            maximum=MAX_COORDINATE,
        )
    return value


def _positive_measure(value: Any, field: str) -> float | int:
    return _finite_number(value, field, minimum=0.0, maximum=MAX_MEASURE, exclusive=True)


def _finite_number(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
    exclusive: bool = False,
) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProgramContractError(f"{field} must be numeric")
    if not math.isfinite(float(value)):
        raise ProgramContractError(f"{field} must be finite")
    if (value <= minimum if exclusive else value < minimum) or value > maximum:
        raise ProgramContractError(f"{field} is outside the allowed range")
    return value
