"""MCP-independent contracts for the Phase 0 facade."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CONTRACT_VERSION = "cad.mcp/0.1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CadListDevicesInput(StrictModel):
    online_only: bool = False
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
    device_id: str = Field(min_length=1, max_length=128)
    observation_level: Literal["summary", "detail"] = "summary"
    include_preview_image: bool = False


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
    artifact_refs: list[ArtifactRef]


class CadGetJobInput(StrictModel):
    job_id: str = Field(min_length=1, max_length=128)
    event_cursor: str | None = Field(default=None, max_length=128)


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
