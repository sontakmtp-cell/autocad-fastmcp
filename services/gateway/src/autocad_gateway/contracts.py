"""Strict public v1 contracts kept independent from FastMCP request types."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CONTRACT_VERSION = "cad.mcp/1.0"
PHASE3_CONTRACT_VERSION = "cad.mcp/1.1"
PHASE4_CONTRACT_VERSION = "cad.mcp/1.2"
PHASE6_CONTRACT_VERSION = "cad.mcp/1.3"
PHASE7_CONTRACT_VERSION = "cad.mcp/1.4"
PHASE8_CONTRACT_VERSION = "cad.mcp/1.5"
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


class PackageEvidence(StrictModel):
    package_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DeviceInfoC1(DeviceInfo):
    status: Literal["online", "offline", "incompatible"]
    runtime_state: str | None = Field(default=None, max_length=64)
    document_name: str | None = Field(default=None, max_length=255)
    last_seen_at: str | None = Field(default=None, max_length=64)
    agent_version: str | None = Field(default=None, max_length=64)
    package_summary: list[PackageEvidence] = Field(default_factory=list, max_length=32)
    paused: bool = False


class CadListDevicesOutput(StrictModel):
    contract_version: str = CONTRACT_VERSION
    correlation_id: str
    devices: list[DeviceInfo]
    default_device_id: str | None = None


class CadListDevicesOutputC1(CadListDevicesOutput):
    contract_version: str = PHASE4_CONTRACT_VERSION
    devices: list[DeviceInfoC1]


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


class RevisionEvidence(StrictModel):
    revision_schema: Literal["cad.revision/1"] = "cad.revision/1"
    revision_strength: Literal["summary_only"] = "summary_only"
    commit_safe: Literal[False] = False


class ExecutionEvidence(StrictModel):
    agent_version: str = Field(min_length=1, max_length=64)
    command_id: str = Field(min_length=1, max_length=128)
    package: PackageEvidence
    runtime_state: str | None = Field(default=None, max_length=64)


class CadObserveOutputC1(CadObserveOutputDurable):
    contract_version: str = PHASE4_CONTRACT_VERSION
    job_id: str
    revision_evidence: RevisionEvidence
    execution_evidence: ExecutionEvidence


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


class CadGetJobOutputC1(CadGetJobOutput):
    contract_version: str = PHASE4_CONTRACT_VERSION
    agent_version: str | None = Field(default=None, max_length=64)
    command_id: str = Field(min_length=1, max_length=128)
    package: PackageEvidence | None = None
    runtime_evidence: dict[str, Any] | None = None


class CadPrepareProgramInput(StrictModel):
    device_id: str = Field(min_length=1, max_length=128)
    source_snapshot_id: str = Field(min_length=1, max_length=128)
    operations: list[dict[str, Any]] = Field(min_length=1, max_length=256)
    postconditions: list[dict[str, Any]] = Field(default_factory=list, max_length=64)
    budget_overrides: dict[str, int] = Field(default_factory=dict, max_length=16)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("device_id", "source_snapshot_id")
    @classmethod
    def validate_public_ids(cls, value: str, info: Any) -> str:
        return _bounded_public_id(value, info.field_name)

    @field_validator("idempotency_key")
    @classmethod
    def validate_prepare_key(cls, value: str | None) -> str | None:
        return _idempotency_key(value)


class CadPrepareProgramOutput(StrictModel):
    contract_version: str = PHASE6_CONTRACT_VERSION
    correlation_id: str
    program_id: str
    program_revision: int = Field(ge=1)
    program_digest: str
    document_id: str
    expected_document_revision: str
    execution_binding: dict[str, str]
    risk_class: Literal["low"]
    missing_capabilities: list[str] = Field(max_length=256)
    resource_uri: str
    ready_for_preview: bool


class CadPrepareProgramV1Output(StrictModel):
    contract_version: str = PHASE8_CONTRACT_VERSION
    schema_version: Literal["cad.program/1.0"] = "cad.program/1.0"
    correlation_id: str
    program_id: str
    program_revision: int = Field(ge=1)
    source_digest: str
    execution_plan_id: str
    execution_plan_digest: str
    execution_binding: dict[str, Any]
    effect_manifest_digest: str
    document_id: str
    expected_document_revision: str
    risk_class: Literal["low", "medium"]
    resource_uri: str
    ready_for_preview: bool


class CadPrepareProgramV1RevisionRequest(StrictModel):
    kind: Literal["patch", "rebase"]
    program_id: str = Field(min_length=1, max_length=128)
    source_revision: int = Field(ge=1)
    changes: dict[str, Any] | None = Field(default=None, max_length=8)
    new_snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("program_id", "new_snapshot_id")
    @classmethod
    def validate_revision_ids(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _bounded_public_id(value, info.field_name)

    @model_validator(mode="after")
    def validate_revision_shape(self) -> "CadPrepareProgramV1RevisionRequest":
        editable = {
            "variables",
            "operations",
            "budgets",
            "required_capabilities",
            "validation_profiles",
            "artifact_refs",
            "component_refs",
        }
        if self.kind == "patch":
            if not self.changes or self.new_snapshot_id is not None:
                raise ValueError("patch requires changes only")
            if not set(self.changes).issubset(editable):
                raise ValueError("patch contains a non-editable field")
        elif self.changes is not None or self.new_snapshot_id is None:
            raise ValueError("rebase requires new_snapshot_id only")
        return self


class CadPrepareProgramV1ConflictOutput(StrictModel):
    contract_version: str = PHASE8_CONTRACT_VERSION
    schema_version: Literal["cad.program/1.0"] = "cad.program/1.0"
    correlation_id: str
    program_id: str
    program_revision: int = Field(ge=1)
    lineage_kind: Literal["patch", "rebase"]
    conflict_report_id: str
    conflicts_digest: str
    resource_uri: str
    ready_for_preview: Literal[False] = False


class CadPreviewInput(StrictModel):
    program_id: str = Field(min_length=1, max_length=128)
    program_revision: int = Field(default=1, ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("program_id")
    @classmethod
    def validate_program_id(cls, value: str) -> str:
        return _bounded_public_id(value, "program_id")

    @field_validator("idempotency_key")
    @classmethod
    def validate_preview_key(cls, value: str | None) -> str | None:
        return _idempotency_key(value)


class CadPreviewOutput(StrictModel):
    contract_version: str = PHASE6_CONTRACT_VERSION
    correlation_id: str
    program_id: str
    program_revision: int = Field(ge=1)
    preview_id: str | None = None
    job_id: str
    state: str
    program_digest: str
    execution_digest: str
    binding_digest: str
    planned_operation_count: int | None = Field(default=None, ge=0)
    planned_entity_count: int | None = Field(default=None, ge=0)
    planned_layer_count: int | None = Field(default=None, ge=0)
    validation: dict[str, Any] | None = None
    expires_at: str
    job_uri: str
    resource_uri: str | None = None


class CadCommitInput(StrictModel):
    preview_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("preview_id")
    @classmethod
    def validate_preview_id(cls, value: str) -> str:
        return _bounded_public_id(value, "preview_id")

    @field_validator("idempotency_key")
    @classmethod
    def validate_commit_key(cls, value: str | None) -> str | None:
        return _idempotency_key(value)


class CadCommitOutput(StrictModel):
    contract_version: str = PHASE6_CONTRACT_VERSION
    correlation_id: str
    program_id: str
    program_revision: int = Field(ge=1)
    preview_id: str
    receipt_id: str | None = None
    job_id: str | None = None
    state: str
    program_digest: str
    execution_digest: str
    binding_digest: str
    document_revision_before: str
    document_revision_after: str | None = None
    effect_summary: dict[str, Any] | None = None
    duplicate: bool = False
    job_uri: str | None = None
    resource_uri: str | None = None
    admission_status: Literal[
        "approval_required", "released", "current_job", "receipt"
    ] | None = None
    intent_id: str | None = None
    consent_id: str | None = None
    required_assurance: Literal[
        "none",
        "device_local_confirmation",
        "user_recent_auth",
        "user_recent_auth_plus_device_local",
    ] | None = None
    intent_uri: str | None = None
    consent_uri: str | None = None


class CadPreviewRollbackInput(StrictModel):
    receipt_id: str | None = Field(default=None, min_length=1, max_length=128)
    checkpoint_id: str | None = Field(default=None, min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("receipt_id", "checkpoint_id")
    @classmethod
    def validate_rollback_source(cls, value: str | None) -> str | None:
        return _bounded_public_id(value, "rollback_source") if value is not None else None

    @field_validator("idempotency_key")
    @classmethod
    def validate_rollback_preview_key(cls, value: str) -> str:
        return _idempotency_key(value) or ""

    @model_validator(mode="after")
    def exactly_one_source(self) -> "CadPreviewRollbackInput":
        if (self.receipt_id is None) == (self.checkpoint_id is None):
            raise ValueError("exactly one receipt_id or checkpoint_id is required")
        return self


class CadPreviewRollbackOutput(StrictModel):
    contract_version: str = PHASE7_CONTRACT_VERSION
    rollback_plan_id: str
    checkpoint_id: str
    original_receipt_id: str
    eligible: bool
    conflicts: list[dict[str, Any]] = Field(default_factory=list, max_length=256)
    current_document_revision: str
    expires_at: str
    duplicate: bool = False
    resource_uri: str


class CadCommitRollbackInput(StrictModel):
    rollback_plan_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("rollback_plan_id")
    @classmethod
    def validate_rollback_plan_id(cls, value: str) -> str:
        return _bounded_public_id(value, "rollback_plan_id")

    @field_validator("idempotency_key")
    @classmethod
    def validate_rollback_commit_key(cls, value: str) -> str:
        return _idempotency_key(value) or ""


class CadCommitRollbackOutput(StrictModel):
    contract_version: str = PHASE7_CONTRACT_VERSION
    rollback_plan_id: str
    checkpoint_id: str
    state: str
    duplicate: bool = False
    intent_id: str | None = None
    consent_id: str | None = None
    job_id: str | None = None
    rollback_receipt_id: str | None = None
    intent_uri: str | None = None
    consent_uri: str | None = None
    job_uri: str | None = None
    resource_uri: str


class Phase7ConsentDecisionInput(StrictModel):
    intent_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    consent_version: int = Field(ge=1, le=2_147_483_647)
    challenge_nonce: str = Field(min_length=32, max_length=256)
    decision: Literal["approve", "deny"]


class CadValidateInput(StrictModel):
    receipt_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("receipt_id")
    @classmethod
    def validate_receipt_id(cls, value: str) -> str:
        return _bounded_public_id(value, "receipt_id")

    @field_validator("idempotency_key")
    @classmethod
    def validate_validate_key(cls, value: str | None) -> str | None:
        return _idempotency_key(value)


class CadValidateOutput(StrictModel):
    contract_version: str = PHASE6_CONTRACT_VERSION
    correlation_id: str
    program_id: str
    program_revision: int = Field(ge=1)
    receipt_id: str
    validation_id: str | None = None
    job_id: str
    state: str
    execution_digest: str
    binding_digest: str
    passed: bool | None = None
    report: dict[str, Any] | None = None
    job_uri: str
    resource_uri: str | None = None


def _bounded_public_id(value: str, field_name: str) -> str:
    canonical = value.strip()
    if not canonical or not _PUBLIC_ID.fullmatch(canonical):
        raise ValueError(f"{field_name} is malformed")
    return canonical


def _idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    canonical = value.strip()
    if not canonical or any(character.isspace() for character in canonical):
        raise ValueError("idempotency_key is malformed")
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
