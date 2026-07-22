"""MCP-independent contracts for the Phase 0 facade."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CONTRACT_VERSION = "cad.mcp/0.1"

StrictBoolean = Annotated[bool, Field(strict=True)]
DeviceId = Annotated[str, Field(strict=True, min_length=1, max_length=128)]
JobId = Annotated[str, Field(strict=True, min_length=1, max_length=128)]
EventCursor = Annotated[str, Field(strict=True, max_length=128)]
ObservationLevel = Literal["summary", "detail"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CadListDevicesInput(StrictModel):
    online_only: StrictBoolean = False
    capability: str | None = None


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
    device_id: DeviceId
    observation_level: ObservationLevel = "summary"
    include_preview_image: StrictBoolean = False


class ArtifactRef(StrictModel):
    artifact_id: str
    uri: str
    mime_type: str


class CadObserveOutput(StrictModel):
    contract_version: str = CONTRACT_VERSION
    correlation_id: str
    device_id: str
    snapshot_id: str
    document_revision: str
    summary_uri: str
    # Empty is allowed for structured-only observations. The public handler must
    # explicitly fail with preview_unavailable when an image was requested.
    artifact_refs: list[ArtifactRef]


class CadGetJobInput(StrictModel):
    job_id: JobId
    event_cursor: EventCursor | None = None


class JobError(StrictModel):
    code: str
    message: str


class CadGetJobOutput(StrictModel):
    contract_version: str = CONTRACT_VERSION
    correlation_id: str
    job_id: str
    state: Literal["queued", "running", "completed", "failed"]
    progress: float = Field(ge=0.0, le=1.0)
    result: dict[str, Any] | None = None
    error: JobError | None = None
    next_cursor: str | None = None
