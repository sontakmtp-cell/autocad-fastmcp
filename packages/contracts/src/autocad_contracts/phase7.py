"""Strict, bounded Phase 7 approval, recovery, and rollback contracts."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Any, Literal, TypeAlias, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from .agent_protocol import canonical_json


EXECUTION_INTENT_VERSION = "cad.execution-intent/1"
CONSENT_VERSION = "cad.consent/1"
EVIDENCE_EVENT_VERSION = "cad.execution-evidence/1"
RECOVERY_CASE_VERSION = "cad.recovery-case/1"
ROLLBACK_CHECKPOINT_VERSION = "cad.rollback.checkpoint/1"
ROLLBACK_PLAN_VERSION = "cad.rollback.plan/1"
ROLLBACK_RECEIPT_VERSION = "cad.rollback.receipt/1"

MAX_EFFECT_SUMMARY_ITEMS = 32
MAX_EVIDENCE_DETAILS = 32
MAX_EVIDENCE_TIMELINE = 256
MAX_MISSING_EVIDENCE = 64
MAX_SAFE_ACTIONS = 16
MAX_OPERATOR_NOTES = 64
MAX_CHECKPOINT_ENTITIES = 256
MAX_ROLLBACK_CONFLICTS = 256
MAX_REMOVED_ENTITIES = 256
MAX_RECORD_BYTES = 1_048_576

_PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_REVISION = re.compile(r"^\S{1,256}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})$"
)
_HANDLE = re.compile(r"^[0-9A-F]{1,32}$")


class Phase7Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def _record_is_bounded(self) -> "Phase7Model":
        encoded = canonical_json(self.model_dump(mode="json", exclude_none=True)).encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            raise ValueError("Phase 7 record exceeds the byte limit")
        return self


PublicId = Annotated[str, Field(min_length=1, max_length=256, pattern=_PUBLIC_ID.pattern)]
RevisionToken = Annotated[str, Field(min_length=1, max_length=256, pattern=_REVISION.pattern)]
Digest = Annotated[str, Field(pattern=_SHA256.pattern)]
Timestamp = Annotated[str, Field(pattern=_TIMESTAMP.pattern)]
ShortText = Annotated[str, Field(min_length=1, max_length=512)]
NoteText = Annotated[str, Field(min_length=1, max_length=2048)]

ExecutionIntentState: TypeAlias = Literal[
    "awaiting_approval",
    "ready",
    "released",
    "denied",
    "expired",
    "invalidated",
    "cancelled",
]
ConsentState: TypeAlias = Literal[
    "requested",
    "approved",
    "denied",
    "expired",
    "invalidated",
    "consumed",
]
AssuranceLevel: TypeAlias = Literal[
    "none",
    "device_local_confirmation",
    "user_recent_auth",
    "user_recent_auth_plus_device_local",
]


def _validate_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonical_phase7_digest(
    value: BaseModel | dict[str, Any],
    *,
    exclude_fields: frozenset[str] = frozenset(),
) -> str:
    payload = (
        value.model_dump(mode="json", exclude_none=True)
        if isinstance(value, BaseModel)
        else {key: item for key, item in value.items() if item is not None}
    )
    for field in exclude_fields:
        payload.pop(field, None)
    encoded = canonical_json(payload).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


class ActorPrincipal(Phase7Model):
    issuer: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=512)


class RuntimePins(Phase7Model):
    runtime_id: PublicId
    runtime_role: str = Field(min_length=1, max_length=64)
    host_family: str = Field(min_length=1, max_length=64)
    host_version: str = Field(min_length=1, max_length=64)
    agent_package_id: PublicId
    agent_package_version: str = Field(min_length=1, max_length=128)
    agent_package_hash: Digest
    host_package_id: PublicId
    host_package_version: str = Field(min_length=1, max_length=128)
    host_package_hash: Digest


class PolicyPins(Phase7Model):
    capability_manifest_hash: Digest
    operation_registry_hash: Digest
    registry_version: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)


class EffectSummaryItem(Phase7Model):
    kind: Literal["create_entities", "erase_entities", "ensure_layers", "document_change"]
    count: int = Field(ge=0, le=256)
    summary: str = Field(min_length=1, max_length=512)


class ExecutionIntentRecord(Phase7Model):
    schema_version: Literal["cad.execution-intent/1"] = EXECUTION_INTENT_VERSION
    intent_id: PublicId
    intent_version: int = Field(ge=1, le=2_147_483_647)
    owner_subject: str = Field(min_length=1, max_length=512)
    actor_principal: ActorPrincipal
    action: Literal["program_commit", "rollback_commit"]
    state: ExecutionIntentState
    state_version: int = Field(ge=0, le=2_147_483_647)
    device_id: PublicId
    device_identity_generation: int = Field(ge=1, le=2_147_483_647)
    device_key_thumbprint: Digest
    document_id: PublicId
    expected_document_revision: RevisionToken
    program_id: PublicId
    program_revision: int = Field(ge=1, le=2_147_483_647)
    program_digest: Digest
    preview_id: PublicId
    preview_digest: Digest
    preview_execution_digest: Digest
    preview_expires_at: Timestamp
    deterministic_receipt_id: PublicId
    commit_execution_digest: Digest
    runtime_pins: RuntimePins
    policy_pins: PolicyPins
    risk_class: Literal["low", "medium", "high", "destructive"]
    required_assurance: AssuranceLevel
    trusted_effect_summary: list[EffectSummaryItem] = Field(
        min_length=1, max_length=MAX_EFFECT_SUMMARY_ITEMS
    )
    idempotency_key: str = Field(min_length=1, max_length=256)
    request_hash: Digest
    intent_digest: Digest
    created_at: Timestamp
    expires_at: Timestamp
    consent_id: PublicId | None = None
    released_job_id: PublicId | None = None

    _timestamp = field_validator(
        "preview_expires_at", "created_at", "expires_at"
    )(_validate_timestamp)

    @model_validator(mode="after")
    def _validate_intent(self) -> "ExecutionIntentRecord":
        if self.intent_digest != execution_intent_digest(self):
            raise ValueError("intent_digest does not match the immutable intent")
        if self.state == "released" and self.released_job_id is None:
            raise ValueError("released intent must bind released_job_id")
        if self.state != "released" and self.released_job_id is not None:
            raise ValueError("only released intent may bind released_job_id")
        if self.required_assurance == "none" and self.state == "awaiting_approval":
            raise ValueError("assurance none cannot await approval")
        if (
            _as_datetime(self.created_at) >= _as_datetime(self.expires_at)
            or _as_datetime(self.created_at) >= _as_datetime(self.preview_expires_at)
        ):
            raise ValueError("intent timestamps are not ordered")
        return self


_INTENT_MUTABLE_FIELDS = frozenset(
    {"intent_digest", "state", "state_version", "consent_id", "released_job_id"}
)


def execution_intent_digest(value: ExecutionIntentRecord | dict[str, Any]) -> str:
    return canonical_phase7_digest(value, exclude_fields=_INTENT_MUTABLE_FIELDS)


class ConsentRecord(Phase7Model):
    schema_version: Literal["cad.consent/1"] = CONSENT_VERSION
    consent_id: PublicId
    consent_version: int = Field(ge=1, le=2_147_483_647)
    owner_subject: str = Field(min_length=1, max_length=512)
    intent_id: PublicId
    intent_version: int = Field(ge=1, le=2_147_483_647)
    intent_digest: Digest
    required_assurance: AssuranceLevel
    state: ConsentState
    state_version: int = Field(ge=0, le=2_147_483_647)
    challenge_nonce_hash: Digest
    requested_at: Timestamp
    expires_at: Timestamp
    decided_at: Timestamp | None = None
    decision_source: Literal["device_local", "portal_recent_auth"] | None = None
    decision_principal: ActorPrincipal | None = None
    decision_device_id: PublicId | None = None
    decision_device_identity_generation: int | None = Field(
        default=None, ge=1, le=2_147_483_647
    )
    consumed_at: Timestamp | None = None

    _timestamp = field_validator(
        "requested_at", "expires_at", "decided_at", "consumed_at"
    )(_validate_timestamp)

    @model_validator(mode="after")
    def _validate_consent(self) -> "ConsentRecord":
        if _as_datetime(self.requested_at) >= _as_datetime(self.expires_at):
            raise ValueError("consent expiry must follow request")
        decision_fields = (
            self.decided_at,
            self.decision_source,
            self.decision_principal,
        )
        if self.state in {"approved", "denied", "consumed"}:
            if any(value is None for value in decision_fields):
                raise ValueError("decided consent requires decision evidence")
        elif any(value is not None for value in decision_fields):
            raise ValueError("undecided consent cannot include decision evidence")
        if self.state == "consumed" and self.consumed_at is None:
            raise ValueError("consumed consent requires consumed_at")
        if self.state != "consumed" and self.consumed_at is not None:
            raise ValueError("only consumed consent may include consumed_at")
        if self.decision_source == "device_local":
            if (
                self.decision_device_id is None
                or self.decision_device_identity_generation is None
            ):
                raise ValueError("device-local decision requires stable device evidence")
        elif (
            self.decision_device_id is not None
            or self.decision_device_identity_generation is not None
        ):
            raise ValueError("Portal decision cannot include device-local evidence")
        return self


EvidenceSource: TypeAlias = Literal["gateway", "agent", "host"]
EvidenceMilestone: TypeAlias = Literal[
    "host_admitted",
    "transaction_opened",
    "transaction_aborted",
    "effect_and_receipt_committed",
    "result_serialized",
    "received",
    "accepted",
    "host_dispatch_started",
    "host_result_received",
    "terminal_persisted",
    "gateway_result_acknowledged",
    "reconcile_query",
    "evidence_conflict",
]
Scalar: TypeAlias = Union[str, int, bool, None]


class EvidenceDetail(Phase7Model):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value: Scalar

    @field_validator("value")
    @classmethod
    def _bounded_value(cls, value: Scalar) -> Scalar:
        if isinstance(value, str) and len(value) > 512:
            raise ValueError("evidence detail value is too long")
        if isinstance(value, int) and not -(2**63) <= value < 2**63:
            raise ValueError("evidence detail integer is out of range")
        return value


class EvidencePayload(Phase7Model):
    milestone: EvidenceMilestone
    outcome: Literal[
        "observed",
        "not_started",
        "committed",
        "aborted",
        "rolled_back",
        "conflict",
        "inconclusive",
    ]
    summary: ShortText
    details: list[EvidenceDetail] = Field(default_factory=list, max_length=MAX_EVIDENCE_DETAILS)

    @model_validator(mode="after")
    def _unique_keys(self) -> "EvidencePayload":
        keys = [item.key for item in self.details]
        if len(keys) != len(set(keys)):
            raise ValueError("evidence detail keys must be unique")
        return self


class ExecutionEvidenceEvent(Phase7Model):
    schema_version: Literal["cad.execution-evidence/1"] = EVIDENCE_EVENT_VERSION
    event_id: PublicId
    owner_subject: str = Field(min_length=1, max_length=512)
    source: EvidenceSource
    source_sequence: int = Field(ge=0, le=9_223_372_036_854_775_807)
    job_id: PublicId
    command_id: PublicId | None = None
    intent_id: PublicId | None = None
    payload_digest: Digest | None = None
    execution_digest: Digest | None = None
    receipt_digest: Digest | None = None
    payload: EvidencePayload
    source_timestamp: Timestamp
    gateway_received_at: Timestamp
    event_digest: Digest

    _timestamp = field_validator("source_timestamp", "gateway_received_at")(
        _validate_timestamp
    )

    @model_validator(mode="after")
    def _validate_event(self) -> "ExecutionEvidenceEvent":
        if self.event_digest != execution_evidence_digest(self):
            raise ValueError("event_digest does not match evidence event")
        host_milestones = {
            "host_admitted",
            "transaction_opened",
            "transaction_aborted",
            "effect_and_receipt_committed",
            "result_serialized",
        }
        if self.payload.milestone in host_milestones and self.source != "host":
            raise ValueError("Host milestone must have host source")
        return self


def execution_evidence_digest(value: ExecutionEvidenceEvent | dict[str, Any]) -> str:
    return canonical_phase7_digest(value, exclude_fields=frozenset({"event_digest"}))


class RecoveryRuntimeState(Phase7Model):
    device_status: Literal["online", "offline", "revoked", "replaced", "unknown"]
    document_status: Literal["open", "closed", "unavailable", "identity_mismatch", "unknown"]
    document_revision: RevisionToken | None = None
    runtime_id: PublicId | None = None
    agent_package_hash: Digest | None = None
    host_package_hash: Digest | None = None


class RecoveryQueryResult(Phase7Model):
    outcome: Literal[
        "not_found",
        "committed",
        "rolled_back",
        "conflict",
        "aborted",
        "inconclusive",
        "unavailable",
        "malformed_ledger",
    ]
    source: EvidenceSource
    summary: ShortText
    queried_at: Timestamp
    receipt_digest: Digest | None = None

    _timestamp = field_validator("queried_at")(_validate_timestamp)


RecoverySafeAction: TypeAlias = Literal[
    "retry_exact_evidence_query",
    "reopen_exact_document",
    "collect_redacted_diagnostics",
    "materialize_from_exact_host_receipt",
    "mark_unresolved",
    "needs_support",
]


class OperatorNote(Phase7Model):
    note_id: PublicId
    actor: ActorPrincipal
    text: NoteText
    created_at: Timestamp

    _timestamp = field_validator("created_at")(_validate_timestamp)


class RecoveryCaseRecord(Phase7Model):
    schema_version: Literal["cad.recovery-case/1"] = RECOVERY_CASE_VERSION
    case_id: PublicId
    owner_subject: str = Field(min_length=1, max_length=512)
    state: Literal["open", "investigating", "resolved", "needs_support"]
    resolution_version: int = Field(ge=0, le=2_147_483_647)
    execution_binding_digest: Digest
    intent_id: PublicId
    consent_id: PublicId | None = None
    job_id: PublicId
    receipt_id: PublicId | None = None
    evidence_event_ids: list[PublicId] = Field(
        default_factory=list, max_length=MAX_EVIDENCE_TIMELINE
    )
    missing_evidence: list[ShortText] = Field(
        default_factory=list, max_length=MAX_MISSING_EVIDENCE
    )
    latest_query_result: RecoveryQueryResult | None = None
    current_state: RecoveryRuntimeState
    safe_actions: list[RecoverySafeAction] = Field(
        min_length=1, max_length=MAX_SAFE_ACTIONS
    )
    resolution: Literal[
        "exact_receipt_materialized",
        "proven_no_effect",
        "rolled_back",
        "unresolved",
        "support_required",
    ] | None = None
    operator_notes: list[OperatorNote] = Field(
        default_factory=list, max_length=MAX_OPERATOR_NOTES
    )
    created_at: Timestamp
    updated_at: Timestamp
    resolved_at: Timestamp | None = None

    _timestamp = field_validator("created_at", "updated_at", "resolved_at")(
        _validate_timestamp
    )

    @model_validator(mode="after")
    def _validate_recovery(self) -> "RecoveryCaseRecord":
        if len(self.evidence_event_ids) != len(set(self.evidence_event_ids)):
            raise ValueError("recovery evidence timeline cannot contain duplicates")
        if len(self.safe_actions) != len(set(self.safe_actions)):
            raise ValueError("safe_actions cannot contain duplicates")
        if self.state == "resolved":
            if self.resolution is None or self.resolved_at is None:
                raise ValueError("resolved recovery case requires resolution evidence")
        elif self.resolution is not None or self.resolved_at is not None:
            raise ValueError("unresolved recovery case cannot include resolution")
        return self


class CheckpointEntity(Phase7Model):
    handle: str = Field(pattern=_HANDLE.pattern)
    entity_type: str = Field(min_length=1, max_length=128)
    layer: str = Field(min_length=1, max_length=255)
    canonical_fingerprint: Digest


class RollbackCheckpointRecord(Phase7Model):
    schema_version: Literal["cad.rollback.checkpoint/1"] = ROLLBACK_CHECKPOINT_VERSION
    checkpoint_id: PublicId
    owner_subject: str = Field(min_length=1, max_length=512)
    original_receipt_id: PublicId
    original_receipt_digest: Digest
    program_id: PublicId
    program_revision: int = Field(ge=1, le=2_147_483_647)
    program_digest: Digest
    preview_id: PublicId
    preview_digest: Digest
    execution_digest: Digest
    document_id: PublicId
    document_revision_before: RevisionToken
    document_revision_after: RevisionToken
    created_entities: list[CheckpointEntity] = Field(
        min_length=1, max_length=MAX_CHECKPOINT_ENTITIES
    )
    non_entity_object_created: bool
    runtime_pins: RuntimePins
    policy_pins: PolicyPins
    checkpoint_digest: Digest
    created_at: Timestamp

    _timestamp = field_validator("created_at")(_validate_timestamp)

    @model_validator(mode="after")
    def _validate_checkpoint(self) -> "RollbackCheckpointRecord":
        handles = [entity.handle for entity in self.created_entities]
        if len(handles) != len(set(handles)):
            raise ValueError("checkpoint entity handles must be unique")
        if self.checkpoint_digest != rollback_checkpoint_digest(self):
            raise ValueError("checkpoint_digest does not match checkpoint")
        return self


def rollback_checkpoint_digest(
    value: RollbackCheckpointRecord | dict[str, Any],
) -> str:
    data = (
        value.model_dump(mode="json")
        if isinstance(value, RollbackCheckpointRecord)
        else deepcopy(value)
    )
    runtime = data["runtime_pins"]
    policy = data["policy_pins"]
    # This is deliberately the exact language-neutral Host record projection.
    # Gateway ownership and the Agent package pin remain authenticated metadata,
    # but are not part of the checkpoint persisted inside the DWG.
    host_record = {
        "checkpoint_id": data["checkpoint_id"],
        "original_receipt_id": data["original_receipt_id"],
        "original_receipt_digest": data["original_receipt_digest"],
        "program_id": data["program_id"],
        "program_revision": data["program_revision"],
        "program_digest": data["program_digest"],
        "preview_id": data["preview_id"],
        "preview_digest": data["preview_digest"],
        "execution_digest": data["execution_digest"],
        "document_id": data["document_id"],
        "document_revision_before": data["document_revision_before"],
        "document_revision_after": data["document_revision_after"],
        "created_entities": data["created_entities"],
        "non_entity_object_created": data["non_entity_object_created"],
        "runtime_and_policy_pins": {
            "program_digest": data["program_digest"],
            "execution_digest": data["execution_digest"],
            "document_id": data["document_id"],
            "document_revision": data["document_revision_before"],
            "runtime_id": runtime["runtime_id"],
            "runtime_role": runtime["runtime_role"],
            "host_family": runtime["host_family"],
            "host_version": runtime["host_version"],
            "package_id": runtime["host_package_id"],
            "package_version": runtime["host_package_version"],
            "package_hash": runtime["host_package_hash"],
            "capability_manifest_hash": policy["capability_manifest_hash"],
            "operation_registry_version": policy["registry_version"],
            "operation_registry_hash": policy["operation_registry_hash"],
            "policy_version": policy["policy_version"],
        },
        "created_at": data["created_at"],
        "schema_version": data["schema_version"],
    }
    return f"sha256:{sha256(canonical_json(host_record).encode('utf-8')).hexdigest()}"


class RollbackConflict(Phase7Model):
    code: Literal[
        "checkpoint_missing",
        "document_identity_mismatch",
        "document_revision_mismatch",
        "entity_missing",
        "entity_type_changed",
        "entity_layer_changed",
        "entity_fingerprint_changed",
        "dependency_unproven",
        "runtime_pin_mismatch",
        "plan_expired",
        "evidence_mismatch",
    ]
    handle: str | None = Field(default=None, pattern=_HANDLE.pattern)
    summary: ShortText


class RollbackPlanRecord(Phase7Model):
    schema_version: Literal["cad.rollback.plan/1"] = ROLLBACK_PLAN_VERSION
    plan_id: PublicId
    owner_subject: str = Field(min_length=1, max_length=512)
    checkpoint_id: PublicId
    checkpoint_digest: Digest
    original_receipt_id: PublicId
    document_id: PublicId
    current_document_revision: RevisionToken
    rollback_execution_digest: Digest
    entity_handles: list[str] = Field(
        min_length=1, max_length=MAX_CHECKPOINT_ENTITIES
    )
    conflicts: list[RollbackConflict] = Field(
        default_factory=list, max_length=MAX_ROLLBACK_CONFLICTS
    )
    eligible: bool
    runtime_pins: RuntimePins
    policy_pins: PolicyPins
    plan_digest: Digest
    created_at: Timestamp
    expires_at: Timestamp

    _timestamp = field_validator("created_at", "expires_at")(_validate_timestamp)

    @field_validator("entity_handles")
    @classmethod
    def _validate_handles(cls, values: list[str]) -> list[str]:
        if any(_HANDLE.fullmatch(value) is None for value in values):
            raise ValueError("rollback entity handle is invalid")
        if len(values) != len(set(values)):
            raise ValueError("rollback entity handles must be unique")
        return values

    @model_validator(mode="after")
    def _validate_plan(self) -> "RollbackPlanRecord":
        if self.eligible == bool(self.conflicts):
            raise ValueError("eligible rollback plan cannot contain conflicts")
        if _as_datetime(self.created_at) >= _as_datetime(self.expires_at):
            raise ValueError("rollback plan expiry must follow creation")
        if self.plan_digest != rollback_plan_digest(self):
            raise ValueError("plan_digest does not match rollback plan")
        return self


def rollback_plan_digest(value: RollbackPlanRecord | dict[str, Any]) -> str:
    return canonical_phase7_digest(value, exclude_fields=frozenset({"plan_digest"}))


class RemovedEntityEvidence(Phase7Model):
    handle: str = Field(pattern=_HANDLE.pattern)
    entity_type: str = Field(min_length=1, max_length=128)
    prior_fingerprint: Digest


class RollbackReceiptRecord(Phase7Model):
    schema_version: Literal["cad.rollback.receipt/1"] = ROLLBACK_RECEIPT_VERSION
    rollback_receipt_id: PublicId
    owner_subject: str = Field(min_length=1, max_length=512)
    original_receipt_id: PublicId
    original_receipt_digest: Digest
    program_digest: Digest
    original_execution_digest: Digest
    original_document_revision: RevisionToken
    checkpoint_id: PublicId
    checkpoint_digest: Digest
    rollback_plan_id: PublicId
    rollback_plan_digest: Digest
    rollback_job_id: PublicId
    rollback_execution_digest: Digest
    document_id: PublicId
    document_revision_before: RevisionToken
    document_revision_after: RevisionToken
    removed_entities: list[RemovedEntityEvidence] = Field(
        min_length=1, max_length=MAX_REMOVED_ENTITIES
    )
    runtime_pins: RuntimePins
    policy_pins: PolicyPins
    receipt_digest: Digest
    created_at: Timestamp

    _timestamp = field_validator("created_at")(_validate_timestamp)

    @model_validator(mode="after")
    def _validate_receipt(self) -> "RollbackReceiptRecord":
        handles = [entity.handle for entity in self.removed_entities]
        if len(handles) != len(set(handles)):
            raise ValueError("removed entity handles must be unique")
        if self.receipt_digest != rollback_receipt_digest(self):
            raise ValueError("receipt_digest does not match rollback receipt")
        return self


def rollback_receipt_digest(value: RollbackReceiptRecord | dict[str, Any]) -> str:
    data = (
        value.model_dump(mode="json")
        if isinstance(value, RollbackReceiptRecord)
        else deepcopy(value)
    )
    runtime = data["runtime_pins"]
    policy = data["policy_pins"]
    host_record = {
        "rollback_receipt_id": data["rollback_receipt_id"],
        "original_receipt_id": data["original_receipt_id"],
        "original_receipt_digest": data["original_receipt_digest"],
        "checkpoint_id": data["checkpoint_id"],
        "checkpoint_digest": data["checkpoint_digest"],
        "rollback_plan_id": data["rollback_plan_id"],
        "rollback_plan_digest": data["rollback_plan_digest"],
        "rollback_execution_digest": data["rollback_execution_digest"],
        "document_id": data["document_id"],
        "document_revision_before": data["document_revision_before"],
        "document_revision_after": data["document_revision_after"],
        "removed_entities": data["removed_entities"],
        "runtime_and_policy_pins": {
            "program_digest": data["program_digest"],
            "execution_digest": data["original_execution_digest"],
            "document_id": data["document_id"],
            "document_revision": data["original_document_revision"],
            "runtime_id": runtime["runtime_id"],
            "runtime_role": runtime["runtime_role"],
            "host_family": runtime["host_family"],
            "host_version": runtime["host_version"],
            "package_id": runtime["host_package_id"],
            "package_version": runtime["host_package_version"],
            "package_hash": runtime["host_package_hash"],
            "capability_manifest_hash": policy["capability_manifest_hash"],
            "operation_registry_version": policy["registry_version"],
            "operation_registry_hash": policy["operation_registry_hash"],
            "policy_version": policy["policy_version"],
        },
        "created_at": data["created_at"],
        "schema_version": data["schema_version"],
    }
    return f"sha256:{sha256(canonical_json(host_record).encode('utf-8')).hexdigest()}"


Phase7DomainRecord: TypeAlias = Annotated[
    Union[
        ExecutionIntentRecord,
        ConsentRecord,
        ExecutionEvidenceEvent,
        RecoveryCaseRecord,
        RollbackCheckpointRecord,
        RollbackPlanRecord,
        RollbackReceiptRecord,
    ],
    Field(discriminator="schema_version"),
]

_PHASE7_ADAPTER = TypeAdapter(Phase7DomainRecord)


def parse_phase7_domain_record(value: str | bytes | dict[str, Any]) -> Phase7DomainRecord:
    if isinstance(value, (str, bytes)):
        return _PHASE7_ADAPTER.validate_json(value)
    return _PHASE7_ADAPTER.validate_python(value)


def phase7_domain_json_schema() -> dict[str, Any]:
    schema = _PHASE7_ADAPTER.json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://schemas.kythuatvang.local/cad-phase7-domain.schema.json"
    schema["title"] = "AutoCAD MCP Phase 7 bounded domain records"
    return schema


__all__ = [
    "AssuranceLevel",
    "ConsentRecord",
    "ConsentState",
    "ExecutionEvidenceEvent",
    "ExecutionIntentRecord",
    "ExecutionIntentState",
    "Phase7DomainRecord",
    "RecoveryCaseRecord",
    "RollbackCheckpointRecord",
    "RollbackPlanRecord",
    "RollbackReceiptRecord",
    "canonical_phase7_digest",
    "execution_evidence_digest",
    "execution_intent_digest",
    "parse_phase7_domain_record",
    "phase7_domain_json_schema",
    "rollback_checkpoint_digest",
    "rollback_plan_digest",
    "rollback_receipt_digest",
]
