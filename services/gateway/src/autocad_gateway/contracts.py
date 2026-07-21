"""Strict public v1 contracts kept independent from FastMCP request types."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CONTRACT_VERSION = "cad.mcp/1.0"
PHASE3_CONTRACT_VERSION = "cad.mcp/1.1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Principal(StrictModel):
    subject: str = Field(min_length=1, max_length=256)
    scopes: tuple[str, ...] = ()


class CadListDevicesInput(StrictModel):
    online_only: bool = False
    capability: str | None = Field(default=None, min_length=1, max_length=64)


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
