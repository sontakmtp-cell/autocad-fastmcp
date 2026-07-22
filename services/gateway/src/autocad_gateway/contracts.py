"""Strict public v1 contracts kept independent from FastMCP request types."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CONTRACT_VERSION = "cad.mcp/1.0"
PHASE3_CONTRACT_VERSION = "cad.mcp/1.1"
MAX_ENTITY_TYPE_LENGTH = 64
MAX_LAYER_NAME_LENGTH = 255
MAX_FILTER_BYTES = 4096
MAX_IDEMPOTENCY_KEY_LENGTH = 128
_PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CURSOR = re.compile(r"^[A-Za-z0-9_-]+$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Principal(StrictModel):
    subject: str = Field(min_length=1, max_length=256)
    scopes: tuple[str, ...] = ()


class CadListDevicesInput(StrictModel):
    online_only: bool = False
    capability: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("capability")
    @classmethod
    def canonicalize_capability(cls, value: str | None) -> str | None:
        if value is None:
            return None
        canonical = value.strip().lower()
        if not canonical:
            raise ValueError("capability must not be empty")
        return canonical


class DeviceInfo(StrictModel):
    device_id: str
    display_name: str
    status: Literal["online", "offline"]
    capabilities: list[str]


class CadListDevicesOutput(StrictModel):
    contract_version: str = CONTRACT_VERSION
    correlation_id: str
    devices: list[DeviceInfo]
    default_device_id: str | None = None


class CadObserveInput(StrictModel):
    device_id: str = Field(min_length=1, max_length=128)
    observation_level: Literal["summary", "detail"] = "summary"
    include_preview_image: bool = False

    @field_validator("device_id")
    @classmethod
    def validate_device_id(cls, value: str) -> str:
        return _bounded_public_id(value, "device_id")


class CadObserveInputDurable(CadObserveInput):
    """Additive Phase 3 input; the local Phase 2 schema stays frozen."""

    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_IDEMPOTENCY_KEY_LENGTH,
    )

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        canonical = value.strip()
        if not canonical or any(character.isspace() for character in canonical):
            raise ValueError("idempotency_key is malformed")
        return canonical


class ArtifactRef(StrictModel):
    artifact_id: str
    uri: str
    mime_type: str


class CadEntity(StrictModel):
    entity_id: str
    entity_type: str
    layer: str
    geometry: dict[str, Any] = Field(default_factory=dict)


class CadObserveOutput(StrictModel):
    contract_version: str = CONTRACT_VERSION
    correlation_id: str
    device_id: str
    snapshot_id: str
    document_revision: str
    observation_level: Literal["summary", "detail"]
    entity_count: int = Field(ge=0)
    summary_uri: str
    entities_uri: str
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)


class CadObserveOutputDurable(CadObserveOutput):
    """Additive Phase 3 observe result; the local Phase 2 schema is unchanged."""

    contract_version: str = PHASE3_CONTRACT_VERSION
    job_id: str | None = None


class CadQueryInput(StrictModel):
    snapshot_id: str = Field(min_length=1, max_length=128)
    types: list[str] = Field(default_factory=list, max_length=16)
    layers: list[str] = Field(default_factory=list, max_length=16)
    cursor: str | None = Field(default=None, max_length=512)
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("snapshot_id")
    @classmethod
    def validate_snapshot_id(cls, value: str) -> str:
        return _bounded_public_id(value, "snapshot_id")

    @field_validator("types")
    @classmethod
    def canonicalize_types(cls, values: list[str]) -> list[str]:
        return _canonical_filter(values, item_limit=MAX_ENTITY_TYPE_LENGTH, uppercase=True)

    @field_validator("layers")
    @classmethod
    def canonicalize_layers(cls, values: list[str]) -> list[str]:
        return _canonical_filter(values, item_limit=MAX_LAYER_NAME_LENGTH, uppercase=False)

    @field_validator("cursor")
    @classmethod
    def validate_cursor(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or not _CURSOR.fullmatch(value):
            raise ValueError("cursor is malformed")
        return value

    @model_validator(mode="after")
    def validate_total_filter_bytes(self) -> "CadQueryInput":
        total = sum(len(item.encode("utf-8")) for item in [*self.types, *self.layers])
        if total > MAX_FILTER_BYTES:
            raise ValueError("filters exceed the total byte limit")
        return self


class CadQueryOutput(StrictModel):
    contract_version: str = CONTRACT_VERSION
    correlation_id: str
    snapshot_id: str
    document_revision: str
    entities: list[CadEntity]
    total: int = Field(ge=0)
    next_cursor: str | None = None
    resource_uri: str


class CadGetJobInput(StrictModel):
    job_id: str = Field(min_length=1, max_length=128)
    event_cursor: str | None = Field(default=None, max_length=32)
    event_limit: int = Field(default=50, ge=1, le=100)


class CadJobEvent(StrictModel):
    sequence: int = Field(ge=1)
    event_type: Literal["state", "progress"]
    state: str | None = None
    progress: dict[str, Any] | None = None
    error_code: str | None = None
    result: dict[str, Any] | None = None
    created_at: str


class CadGetJobOutput(StrictModel):
    contract_version: str = PHASE3_CONTRACT_VERSION
    correlation_id: str
    job_id: str
    device_id: str
    kind: str
    state: Literal[
        "queued",
        "dispatched",
        "acknowledged",
        "running",
        "succeeded",
        "failed",
        "reconnect_pending",
        "cancel_requested",
        "cancelled",
        "outcome_unknown",
        "needs_attention",
    ]
    progress: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_summary: str | None = None
    events: list[CadJobEvent] = Field(default_factory=list, max_length=100)
    next_event_cursor: str | None = None
    snapshot_id: str | None = None


def _bounded_public_id(value: str, field_name: str) -> str:
    canonical = value.strip()
    if not canonical or not _PUBLIC_ID.fullmatch(canonical):
        raise ValueError(f"{field_name} is malformed")
    return canonical


def _canonical_filter(
    values: list[str], *, item_limit: int, uppercase: bool
) -> list[str]:
    canonical: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item or len(item) > item_limit or len(item.encode("utf-8")) > item_limit * 4:
            raise ValueError("filter item is empty or too long")
        if uppercase:
            item = item.upper()
        if item not in seen:
            canonical.append(item)
            seen.add(item)
    return sorted(canonical)
