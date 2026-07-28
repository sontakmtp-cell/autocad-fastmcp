"""Strict, dependency-light ``cad.agent/1`` wire protocol models."""

from __future__ import annotations

import copy
import json
import math
import re
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeAlias, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)


PROTOCOL_VERSION = "cad.agent/1"
PHASE5_PROTOCOL_VERSION = "cad.agent/2"
REVISION_SCHEMA = "cad.revision/1"
MAX_MESSAGE_TEXT = 2048
MAX_PAYLOAD_ITEMS = 64
MAX_RESULT_ITEMS = 128
MAX_CAPABILITIES = 64
MAX_CAPABILITY_BYTES = 64
MAX_RECONCILE_COMMANDS = 64
MAX_WEBSOCKET_MESSAGE_BYTES = 1_048_576
MAX_JSON_DEPTH = 16
MAX_JSON_CONTAINER_ITEMS = 10_000
MAX_JSON_STRING_BYTES = 65_536
MAX_JSON_KEY_BYTES = 256
MAX_SEQUENCE = 1_000_000_000
MAX_PACKAGES = 32
MAX_APPROVAL_MESSAGE_BYTES = 65_536
MAX_APPROVAL_WARNINGS = 16

_PACKAGE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_CAPABILITY_PATTERN = re.compile(
    r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*(?:/[1-9][0-9]*)?$"
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_PREFIXED_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_BOUND_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
_NONCE_PATTERN = r"^[A-Za-z0-9_-]{32,128}$"
_PHASE6_STATE_FIELDS = {
    "write_lock_enabled",
    "hard_pause",
    "active_document_id",
    "active_document_revision",
    "active_job_id",
    "support_id",
    "mismatch_reason",
    "outcome_unknown",
}

if TYPE_CHECKING:
    from .runtime import CapabilityManifest


def _message_id() -> str:
    return str(uuid.uuid4())


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timezone_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError("timestamp must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat()


def validate_bounded_json(value: Any, *, _depth: int = 0) -> None:
    """Reject JSON values that are deep or individually expensive to materialize."""

    if _depth > MAX_JSON_DEPTH:
        raise ValueError("JSON nesting exceeds the protocol limit")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_JSON_STRING_BYTES:
            raise ValueError("JSON string exceeds the protocol limit")
        return
    if isinstance(value, list):
        if len(value) > MAX_JSON_CONTAINER_ITEMS:
            raise ValueError("JSON list exceeds the protocol limit")
        for item in value:
            validate_bounded_json(item, _depth=_depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_JSON_CONTAINER_ITEMS:
            raise ValueError("JSON object exceeds the protocol limit")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            if not key or len(key.encode("utf-8")) > MAX_JSON_KEY_BYTES:
                raise ValueError("JSON object key exceeds the protocol limit")
            validate_bounded_json(item, _depth=_depth + 1)
        return
    raise ValueError("value is not bounded JSON")


def canonical_json(value: Any) -> str:
    validate_bounded_json(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def normalize_sha256_digest(
    value: str,
    *,
    allow_legacy_raw: bool = True,
) -> str:
    """Normalize an explicit SHA-256 value to the Phase 6 wire representation.

    Legacy capability/package manifests use lowercase raw 64-hex values.
    Phase 6 execution bindings use ``sha256:<64hex>`` and stay strict.
    """

    if not isinstance(value, str):
        raise ValueError("SHA-256 digest must be a string")
    if re.fullmatch(_PREFIXED_SHA256_PATTERN, value):
        return value
    if allow_legacy_raw and re.fullmatch(_SHA256_PATTERN, value):
        return f"sha256:{value}"
    raise ValueError("SHA-256 digest must be lowercase sha256:<64hex>")


def canonical_capabilities(capabilities: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if len(capabilities) > MAX_CAPABILITIES:
        raise ValueError("capability list exceeds the protocol limit")
    normalized: set[str] = set()
    for capability in capabilities:
        if not isinstance(capability, str):
            raise ValueError("capability names must be strings")
        value = capability.strip().lower()
        if (
            not value
            or len(value.encode("utf-8")) > MAX_CAPABILITY_BYTES
            or _CAPABILITY_PATTERN.fullmatch(value) is None
        ):
            raise ValueError("capability name is invalid")
        normalized.add(value)
    return tuple(sorted(normalized))


def canonical_capability_hash(capabilities: list[str] | tuple[str, ...]) -> str:
    manifest = list(canonical_capabilities(capabilities))
    return sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


class PackageManifestEntry(BaseModel):
    """One immutable package advertised by a real Desktop Agent."""

    model_config = ConfigDict(extra="forbid", strict=True)

    package_id: str = Field(min_length=1, max_length=128, pattern=_PACKAGE_ID_PATTERN.pattern)
    version: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=_SHA256_PATTERN)


def canonical_packages(
    packages: list[PackageManifestEntry | dict[str, Any]] | tuple[PackageManifestEntry, ...],
) -> tuple[PackageManifestEntry, ...]:
    if len(packages) > MAX_PACKAGES:
        raise ValueError("package manifest exceeds the protocol limit")
    normalized = [
        item if isinstance(item, PackageManifestEntry) else PackageManifestEntry.model_validate(item)
        for item in packages
    ]
    keys = [(item.package_id, item.version) for item in normalized]
    if len(keys) != len(set(keys)):
        raise ValueError("package manifest entries must be unique")
    return tuple(sorted(normalized, key=lambda item: (item.package_id, item.version, item.sha256)))


def canonical_package_manifest_hash(
    packages: list[PackageManifestEntry | dict[str, Any]] | tuple[PackageManifestEntry, ...],
) -> str:
    manifest = [item.model_dump(mode="json") for item in canonical_packages(packages)]
    return sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def revision_payload(
    *,
    document_identity: dict[str, Any],
    drawing: dict[str, Any],
    entities: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the golden ``cad.revision/1`` order-independent drawing state."""

    validate_bounded_json(document_identity)
    validate_bounded_json(drawing)
    validate_bounded_json(entities)
    try:
        ordered_entities = sorted(copy.deepcopy(entities), key=lambda item: item["entity_id"])
    except (KeyError, TypeError) as error:
        raise ValueError("revision entities require string entity_id values") from error
    if any(not isinstance(item.get("entity_id"), str) or not item["entity_id"] for item in ordered_entities):
        raise ValueError("revision entities require string entity_id values")
    return {
        "revision_schema": REVISION_SCHEMA,
        "document_identity": copy.deepcopy(document_identity),
        "drawing": copy.deepcopy(drawing),
        "entities": ordered_entities,
    }


def document_revision(
    *,
    document_identity: dict[str, Any],
    drawing: dict[str, Any],
    entities: list[dict[str, Any]],
) -> str:
    payload = revision_payload(
        document_identity=document_identity,
        drawing=drawing,
        entities=entities,
    )
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def _bounded_model_json(self) -> "AgentModel":
        encoded = canonical_json(self.model_dump(mode="json", exclude_none=True))
        if len(encoded.encode("utf-8")) > MAX_WEBSOCKET_MESSAGE_BYTES:
            raise ValueError("Agent message exceeds the protocol byte limit")
        return self


class AgentEnvelope(AgentModel):
    protocol_version: str = Field(default=PROTOCOL_VERSION, min_length=1, max_length=32)
    message_type: str = Field(min_length=1, max_length=32)
    message_id: str = Field(default_factory=_message_id, min_length=1, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    device_id: str | None = Field(default=None, max_length=128)
    job_id: str | None = Field(default=None, max_length=128)
    command_id: str | None = Field(default=None, max_length=128)
    sequence: int = Field(default=0, ge=0, le=MAX_SEQUENCE)
    issued_at: str = Field(default_factory=_timestamp, min_length=1, max_length=64)
    deadline_at: str | None = Field(default=None, max_length=64)

    @field_validator("issued_at", "deadline_at")
    @classmethod
    def _validate_timestamp(cls, value: str | None) -> str | None:
        return _timezone_timestamp(value) if value is not None else None

    @model_validator(mode="after")
    def _deadline_follows_issue(self) -> "AgentEnvelope":
        if self.deadline_at is not None:
            issued = datetime.fromisoformat(self.issued_at)
            deadline = datetime.fromisoformat(self.deadline_at)
            if deadline < issued:
                raise ValueError("deadline_at must not precede issued_at")
        return self


def _validate_phase6_presence(message: AgentEnvelope) -> None:
    # Pydantic's default ``model_dump()`` includes optional fields as ``null``.
    # Treat those null placeholders as absent so a cad.agent/1 message can be
    # serialized and parsed again without accidentally opting into Phase 6.
    present = {
        field
        for field in _PHASE6_STATE_FIELDS
        if field in message.model_fields_set and getattr(message, field, None) is not None
    }
    if present and message.protocol_version != PHASE5_PROTOCOL_VERSION:
        raise ValueError("Phase 6 presence fields require cad.agent/2")
    active_document_id = getattr(message, "active_document_id", None)
    active_document_revision = getattr(message, "active_document_revision", None)
    if (active_document_id is None) != (active_document_revision is None):
        raise ValueError("active document identity and revision must be provided together")
    paused = getattr(message, "paused", None)
    hard_pause = getattr(message, "hard_pause", None)
    if paused is not None and hard_pause is not None and paused != hard_pause:
        raise ValueError("hard_pause must match legacy paused state")
    current_job_id = getattr(message, "current_job_id", None)
    active_job_id = getattr(message, "active_job_id", None)
    if (
        current_job_id is not None
        and active_job_id is not None
        and current_job_id != active_job_id
    ):
        raise ValueError("active_job_id must match legacy current_job_id")


class HelloMessage(AgentEnvelope):
    message_type: Literal["hello"] = "hello"
    device_id: str = Field(min_length=1, max_length=128)
    protocol_min_version: str = Field(default=PROTOCOL_VERSION, min_length=1, max_length=32)
    protocol_max_version: str = Field(default=PROTOCOL_VERSION, min_length=1, max_length=32)
    fixture_proof: str | None = Field(default=None, min_length=1, max_length=256)
    capability_hash: str = Field(pattern=_SHA256_PATTERN)
    capabilities: list[str] = Field(default_factory=list, max_length=MAX_CAPABILITIES)
    last_processed_sequence: int = Field(default=0, ge=0, le=MAX_SEQUENCE)
    device_proof: str | None = Field(default=None, min_length=1, max_length=256)
    agent_version: str | None = Field(default=None, min_length=1, max_length=64)
    runtime_state: str | None = Field(default=None, min_length=1, max_length=64)
    document_name: str | None = Field(default=None, max_length=255)
    paused: bool | None = None
    current_command_id: str | None = Field(default=None, max_length=128)
    packages: list[PackageManifestEntry] = Field(default_factory=list, max_length=MAX_PACKAGES)
    package_manifest_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    capability_manifest: "CapabilityManifest | None" = None
    capability_manifest_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    write_lock_enabled: bool | None = None
    hard_pause: bool | None = None
    active_document_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^\S+$",
    )
    active_document_revision: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^\S+$",
    )
    active_job_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^\S+$",
    )
    support_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^\S+$",
    )
    mismatch_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    outcome_unknown: bool | None = None

    @model_validator(mode="after")
    def _proof_matches_protocol(self) -> "HelloMessage":
        if self.protocol_version == PROTOCOL_VERSION and self.fixture_proof is None:
            raise ValueError("cad.agent/1 requires fixture_proof")
        if self.protocol_version == PHASE5_PROTOCOL_VERSION:
            if self.fixture_proof is not None or self.device_proof is None:
                raise ValueError("cad.agent/2 requires only device_proof")
        return self

    @model_validator(mode="after")
    def _phase6_presence_is_consistent(self) -> "HelloMessage":
        _validate_phase6_presence(self)
        return self

    @field_validator("capabilities", mode="before")
    @classmethod
    def _canonicalize_capabilities(cls, value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("capabilities must be a list")
        return list(canonical_capabilities(value))

    @field_validator("packages", mode="before")
    @classmethod
    def _canonicalize_packages(cls, value: Any) -> list[PackageManifestEntry]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("packages must be a list")
        return list(canonical_packages(value))

    @model_validator(mode="after")
    def _package_hash_matches(self) -> "HelloMessage":
        if self.package_manifest_hash is not None:
            expected = canonical_package_manifest_hash(self.packages)
            if self.package_manifest_hash != expected:
                raise ValueError("package manifest hash does not match its canonical content")
        return self

    @model_validator(mode="after")
    def _capability_manifest_hash_matches(self) -> "HelloMessage":
        if self.capability_manifest is None:
            if self.capability_manifest_hash is not None:
                raise ValueError("capability manifest hash requires a manifest")
            return self
        if self.capability_manifest_hash is None:
            raise ValueError("capability manifest requires its canonical hash")
        from .runtime import canonical_capability_manifest_hash

        expected = canonical_capability_manifest_hash(self.capability_manifest)
        if self.capability_manifest_hash != expected:
            raise ValueError("capability manifest hash does not match its canonical content")
        return self


class WelcomeMessage(AgentEnvelope):
    message_type: Literal["welcome"] = "welcome"
    session_id: str = Field(min_length=1, max_length=128)
    selected_version: str = Field(default=PROTOCOL_VERSION, min_length=1, max_length=32)
    heartbeat_interval_seconds: int = Field(default=10, ge=1, le=300)
    server_time: str = Field(default_factory=_timestamp, min_length=1, max_length=64)

    @field_validator("server_time")
    @classmethod
    def _validate_server_time(cls, value: str) -> str:
        return _timezone_timestamp(value)


class ApprovalTrustedSummary(AgentModel):
    """Server-derived fields that the local operator may trust."""

    operation: Literal["program_commit", "rollback_commit"]
    operation_summary: str = Field(min_length=1, max_length=512)
    document_name: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^[^/\\:\x00-\x1f]+$",
    )
    document_id: str = Field(min_length=1, max_length=256, pattern=_BOUND_ID_PATTERN)
    operation_count: int = Field(ge=1, le=256)
    entity_count: int = Field(ge=0, le=256)
    runtime_label: str = Field(min_length=1, max_length=128)
    runtime_id: str = Field(min_length=1, max_length=128, pattern=_BOUND_ID_PATTERN)
    package_id: str = Field(min_length=1, max_length=128, pattern=_BOUND_ID_PATTERN)
    package_version: str = Field(min_length=1, max_length=128)
    registry_version: str = Field(min_length=1, max_length=128)
    risk_class: Literal["low", "medium", "high", "destructive"]
    preview_created_at: str = Field(min_length=1, max_length=64)
    warnings: list[str] = Field(default_factory=list, max_length=MAX_APPROVAL_WARNINGS)
    support_id: str = Field(min_length=1, max_length=128, pattern=_BOUND_ID_PATTERN)

    @field_validator("preview_created_at")
    @classmethod
    def _validate_preview_timestamp(cls, value: str) -> str:
        return _timezone_timestamp(value)

    @field_validator("warnings")
    @classmethod
    def _validate_warnings(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 512 for value in values):
            raise ValueError("approval warnings must be non-empty and bounded")
        return values


def approval_request_digest(value: "ApprovalRequestMessage | dict[str, Any]") -> str:
    payload = (
        value.model_dump(mode="json", exclude_none=True)
        if isinstance(value, BaseModel)
        else copy.deepcopy(value)
    )
    payload.pop("approval_request_digest", None)
    payload.setdefault("protocol_version", PHASE5_PROTOCOL_VERSION)
    payload.setdefault("message_type", "approval_request")
    return f"sha256:{sha256(canonical_json(payload).encode('utf-8')).hexdigest()}"


class ApprovalRequestMessage(AgentEnvelope):
    """Exact-device trusted local confirmation request; never a CAD command."""

    protocol_version: Literal["cad.agent/2"] = PHASE5_PROTOCOL_VERSION
    message_type: Literal["approval_request"] = "approval_request"
    message_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128, pattern=_BOUND_ID_PATTERN)
    job_id: None = None
    command_id: None = None
    sequence: int = Field(ge=0, le=MAX_SEQUENCE)
    issued_at: str = Field(min_length=1, max_length=64)
    approval_request_id: str = Field(
        min_length=1, max_length=256, pattern=_BOUND_ID_PATTERN
    )
    intent_id: str = Field(min_length=1, max_length=256, pattern=_BOUND_ID_PATTERN)
    consent_id: str = Field(min_length=1, max_length=256, pattern=_BOUND_ID_PATTERN)
    intent_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    challenge_nonce: str = Field(pattern=_NONCE_PATTERN)
    expires_at: str = Field(min_length=1, max_length=64)
    required_assurance: Literal["device_local_confirmation"]
    device_identity_generation: int = Field(ge=1, le=2_147_483_647)
    device_key_thumbprint: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    trusted_summary: ApprovalTrustedSummary
    approval_request_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)

    @field_validator("expires_at")
    @classmethod
    def _validate_expiry(cls, value: str) -> str:
        return _timezone_timestamp(value)

    @model_validator(mode="after")
    def _validate_request(self) -> "ApprovalRequestMessage":
        if datetime.fromisoformat(self.expires_at) <= datetime.now(timezone.utc):
            raise ValueError("approval request has expired")
        if datetime.fromisoformat(self.expires_at) <= datetime.fromisoformat(self.issued_at):
            raise ValueError("approval expiry must follow issue time")
        if (
            self.deadline_at is not None
            and datetime.fromisoformat(self.deadline_at)
            != datetime.fromisoformat(self.expires_at)
        ):
            raise ValueError("approval deadline must equal approval expiry")
        if self.approval_request_digest != approval_request_digest(self):
            raise ValueError("approval request digest does not match exact request")
        encoded = canonical_json(self.model_dump(mode="json", exclude_none=True)).encode("utf-8")
        if len(encoded) > MAX_APPROVAL_MESSAGE_BYTES:
            raise ValueError("approval request exceeds the byte limit")
        return self


def approval_decision_proof_payload(
    *,
    approval_request_id: str,
    approval_request_digest: str,
    session_id: str,
    device_id: str,
    device_identity_generation: int,
    device_key_thumbprint: str,
    consent_id: str,
    intent_id: str,
    intent_digest: str,
    challenge_nonce: str,
    decision: Literal["approve", "deny"],
    decided_at: str,
) -> str:
    """Canonical bytes-as-text signed by the paired Ed25519 device key."""

    payload = {
        "approval_request_id": approval_request_id,
        "approval_request_digest": approval_request_digest,
        "challenge_nonce": challenge_nonce,
        "consent_id": consent_id,
        "decided_at": _timezone_timestamp(decided_at),
        "decision": decision,
        "device_id": device_id,
        "device_identity_generation": device_identity_generation,
        "device_key_thumbprint": device_key_thumbprint,
        "intent_digest": intent_digest,
        "intent_id": intent_id,
        "session_id": session_id,
    }
    return f"cad.agent.approval-decision/1\n{canonical_json(payload)}"


class ApprovalDecisionMessage(AgentEnvelope):
    """One device-signed response to one exact approval request."""

    protocol_version: Literal["cad.agent/2"] = PHASE5_PROTOCOL_VERSION
    message_type: Literal["approval_decision"] = "approval_decision"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128, pattern=_BOUND_ID_PATTERN)
    job_id: None = None
    command_id: None = None
    approval_request_id: str = Field(
        min_length=1, max_length=256, pattern=_BOUND_ID_PATTERN
    )
    approval_request_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    intent_id: str = Field(min_length=1, max_length=256, pattern=_BOUND_ID_PATTERN)
    consent_id: str = Field(min_length=1, max_length=256, pattern=_BOUND_ID_PATTERN)
    intent_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    challenge_nonce: str = Field(pattern=_NONCE_PATTERN)
    decision: Literal["approve", "deny"]
    decided_at: str = Field(min_length=1, max_length=64)
    device_identity_generation: int = Field(ge=1, le=2_147_483_647)
    device_key_thumbprint: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    device_session_proof: str = Field(
        min_length=80,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    @field_validator("decided_at")
    @classmethod
    def _validate_decision_timestamp(cls, value: str) -> str:
        return _timezone_timestamp(value)

    @model_validator(mode="after")
    def _bounded_decision(self) -> "ApprovalDecisionMessage":
        encoded = canonical_json(self.model_dump(mode="json", exclude_none=True)).encode("utf-8")
        if len(encoded) > MAX_APPROVAL_MESSAGE_BYTES:
            raise ValueError("approval decision exceeds the byte limit")
        return self


class HeartbeatMessage(AgentEnvelope):
    message_type: Literal["heartbeat"] = "heartbeat"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1, le=MAX_SEQUENCE)
    busy: bool = False
    last_processed_sequence: int = Field(default=0, ge=0, le=MAX_SEQUENCE)
    current_job_id: str | None = Field(default=None, max_length=128)
    runtime_state: str | None = Field(default=None, min_length=1, max_length=64)
    document_name: str | None = Field(default=None, max_length=255)
    paused: bool | None = None
    current_command_id: str | None = Field(default=None, max_length=128)
    write_lock_enabled: bool | None = None
    hard_pause: bool | None = None
    active_document_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^\S+$",
    )
    active_document_revision: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^\S+$",
    )
    active_job_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^\S+$",
    )
    support_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^\S+$",
    )
    mismatch_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    outcome_unknown: bool | None = None

    @model_validator(mode="after")
    def _processed_sequence_is_not_future(self) -> "HeartbeatMessage":
        if self.last_processed_sequence >= self.sequence:
            raise ValueError("last_processed_sequence must precede heartbeat sequence")
        _validate_phase6_presence(self)
        return self


class CommandMessage(AgentEnvelope):
    message_type: Literal["command"] = "command"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    payload_hash: str = Field(pattern=_SHA256_PATTERN)
    kind: Literal["observe", "write_fixture"] = "observe"
    effect_class: Literal["read", "write"] = "read"
    payload: dict[str, Any] = Field(default_factory=dict, max_length=MAX_PAYLOAD_ITEMS)


class ProgramExecutionBinding(AgentModel):
    """Server-selected binding. It is intentionally outside CAD Program."""

    program_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    execution_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    document_id: str = Field(min_length=1, max_length=128)
    document_revision: str = Field(min_length=1, max_length=256, pattern=r"^\S+$")
    runtime_id: str = Field(min_length=1, max_length=64)
    runtime_role: Literal["primary"]
    host_family: str = Field(min_length=1, max_length=32)
    host_version: str = Field(min_length=1, max_length=64)
    package_id: str = Field(min_length=1, max_length=128)
    package_version: str = Field(min_length=1, max_length=64)
    package_hash: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    capability_manifest_hash: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    operation_registry_version: str = Field(min_length=1, max_length=64)
    operation_registry_hash: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    policy_version: str = Field(min_length=1, max_length=64)

    @field_validator(
        "program_digest",
        "execution_digest",
        "package_hash",
        "capability_manifest_hash",
        "operation_registry_hash",
        mode="before",
    )
    @classmethod
    def _wire_digests_are_prefixed(cls, value: str) -> str:
        return normalize_sha256_digest(value, allow_legacy_raw=False)


def canonical_preview_digest(
    preview_id: str,
    binding: ProgramExecutionBinding | dict[str, Any],
) -> str:
    """Return the cross-language digest used to bind preview to commit."""

    parsed = (
        binding
        if isinstance(binding, ProgramExecutionBinding)
        else ProgramExecutionBinding.model_validate(binding)
    )
    value = {
        "preview_id": preview_id,
        "program_digest": parsed.program_digest,
        "document_id": parsed.document_id,
        "document_revision": parsed.document_revision,
        "runtime_id": parsed.runtime_id,
        "runtime_role": parsed.runtime_role,
        "host_family": parsed.host_family,
        "host_version": parsed.host_version,
        "package_id": parsed.package_id,
        "package_version": parsed.package_version,
        "package_hash": parsed.package_hash,
        "capability_manifest_hash": parsed.capability_manifest_hash,
        "operation_registry_version": parsed.operation_registry_version,
        "operation_registry_hash": parsed.operation_registry_hash,
        "policy_version": parsed.policy_version,
    }
    return f"sha256:{canonical_payload_hash(value)}"


def canonical_receipt_id(preview_id: str) -> str:
    """Return the durable DWG receipt ID derived from the exact preview ID."""

    digest = sha256(preview_id.encode("utf-8")).hexdigest()[:32]
    return f"AUTOCAD_MCP_PROGRAM_{digest}"


class ProgramValidationRequest(AgentModel):
    validation_id: str = Field(min_length=1, max_length=128)
    receipt_id: str = Field(min_length=1, max_length=128)
    expected_entity_count: int | None = Field(default=None, ge=0, le=256)
    expected_entity_types: list[
        Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")]
    ] = Field(default_factory=list, max_length=16)
    expected_layers: list[Annotated[str, Field(min_length=1, max_length=255)]] = Field(
        default_factory=list,
        max_length=64,
    )


class ProgramCommandMessage(AgentEnvelope):
    protocol_version: Literal["cad.agent/2"] = PHASE5_PROTOCOL_VERSION
    message_type: Literal["command"] = "command"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    payload_hash: str = Field(pattern=_SHA256_PATTERN)
    kind: Literal["program_preview", "program_commit", "program_validate"]
    effect_class: Literal["write", "read"]
    binding: "ProgramExecutionBinding | ExecutionBindingV1"
    program: "CadProgram | None" = None
    execution_plan: "CadExecutionPlanV1 | None" = None
    approval_binding: "Phase8ApprovalBinding | None" = None
    capability_evidence: "list[Phase8CapabilityEvidence] | None" = Field(
        default=None,
        min_length=1,
        max_length=MAX_CAPABILITIES,
    )
    preview_id: str | None = Field(default=None, min_length=1, max_length=128)
    expires_at: str | None = Field(default=None, min_length=1, max_length=64)
    preview_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    receipt_id: str | None = Field(default=None, min_length=1, max_length=128)
    validation: ProgramValidationRequest | None = None

    @field_validator("expires_at")
    @classmethod
    def _preview_expiry_is_timezone_aware(cls, value: str | None) -> str | None:
        return _timezone_timestamp(value) if value is not None else None

    @model_validator(mode="after")
    def _fields_match_kind(self) -> "ProgramCommandMessage":
        if isinstance(self.binding, ExecutionBindingV1):
            return self._validate_phase8_command()
        return self._validate_legacy_command()

    def _validate_legacy_command(self) -> "ProgramCommandMessage":
        if {
            "execution_plan",
            "approval_binding",
            "capability_evidence",
        } & self.model_fields_set:
            raise ValueError("legacy and Phase 8 command fields cannot be mixed")
        if self.kind in {"program_preview", "program_commit"}:
            if self.effect_class != "write" or self.program is None:
                raise ValueError("preview and commit require write effect and program")
        if self.kind == "program_preview":
            if (
                self.preview_id is None
                or self.expires_at is None
                or {"preview_digest", "receipt_id", "validation"} & self.model_fields_set
            ):
                raise ValueError(
                    "preview requires its exact ID and expiry and no prior result fields"
                )
        elif self.kind == "program_commit":
            if (
                self.preview_id is None
                or self.preview_digest is None
                or self.receipt_id is None
                or self.validation is not None
                or "expires_at" in self.model_fields_set
            ):
                raise ValueError("commit requires exact preview and receipt binding")
        else:
            if self.effect_class != "read" or self.program is not None or self.validation is None:
                raise ValueError("validate requires read effect and validation request")
            if {
                "program",
                "preview_id",
                "expires_at",
                "preview_digest",
                "receipt_id",
            } & self.model_fields_set:
                raise ValueError("validate cannot include preview fields")
        if self.program is not None:
            from .program import canonical_program_digest

            if canonical_program_digest(self.program) != self.binding.program_digest:
                raise ValueError("binding program_digest does not match program")
            if self.program.document_id != self.binding.document_id:
                raise ValueError("binding document_id does not match program")
            if self.program.expected_document_revision != self.binding.document_revision:
                raise ValueError("binding document revision does not match program")
        return self

    def _validate_phase8_command(self) -> "ProgramCommandMessage":
        from .phase8_contracts import verify_execution_binding_v1

        if self.kind == "program_validate":
            raise ValueError("Phase 8 binding cannot be used for program_validate")
        if "program" in self.model_fields_set or self.program is not None:
            raise ValueError("Phase 8 command cannot carry source program")
        if self.execution_plan is None:
            raise ValueError("Phase 8 command requires sealed execution plan")
        if self.effect_class != "write":
            raise ValueError("Phase 8 preview and commit require write effect")
        if self.device_id != self.execution_plan.device_id:
            raise ValueError("command device_id does not match sealed plan")

        expected_action: Literal["preview", "commit"] = (
            "preview" if self.kind == "program_preview" else "commit"
        )
        if self.kind == "program_preview":
            if (
                self.preview_id is None
                or self.expires_at is None
                or {"preview_digest", "receipt_id", "validation", "approval_binding"}
                & self.model_fields_set
            ):
                raise ValueError(
                    "Phase 8 preview requires exact identity and no commit fields"
                )
            expected_receipt_id = None
        else:
            if (
                self.preview_id is None
                or self.expires_at is None
                or self.preview_digest is None
                or self.receipt_id is None
                or self.approval_binding is None
                or self.validation is not None
            ):
                raise ValueError(
                    "Phase 8 commit requires preview, receipt, and approval binding"
                )
            expected_receipt_id = self.receipt_id

        verify_execution_binding_v1(
            self.binding,
            self.execution_plan,
            expected_action=expected_action,
            expected_preview_id=self.preview_id,
            expected_preview_expires_at=self.expires_at,
            expected_receipt_id=expected_receipt_id,
        )
        self._validate_phase8_capability_evidence()
        if self.kind == "program_commit":
            self._validate_phase8_approval()
        return self

    def _validate_phase8_capability_evidence(self) -> None:
        required = set(self.execution_plan.required_capabilities)
        evidence = self.capability_evidence or []
        if not required:
            if "capability_evidence" in self.model_fields_set:
                raise ValueError("Phase 8 command cannot carry unrequired capability evidence")
            return
        if {item.capability_key for item in evidence} != required or len(evidence) != len(
            required
        ):
            raise ValueError("capability evidence must exactly cover required capabilities")

        pins = self.execution_plan.execution_pins
        issued_at = datetime.fromisoformat(self.issued_at)
        allowed_states = (
            {"preview_only", "lab_commit", "certified"}
            if self.kind == "program_preview"
            else {"lab_commit", "certified"}
        )
        for item in evidence:
            if (
                item.device_id != self.device_id
                or item.runtime_id != pins.runtime_id
                or item.host_family != pins.host_family
                or item.package_hash != pins.package_hash
                or item.capability_manifest_hash != pins.capability_manifest_hash
                or item.operation_registry_hash != pins.operation_registry_hash
                or item.support_state not in allowed_states
            ):
                raise ValueError("capability evidence does not match sealed plan")
            if not (
                datetime.fromisoformat(item.issued_at) <= issued_at
                < datetime.fromisoformat(item.valid_until)
            ):
                raise ValueError("capability evidence is not valid at command issue time")

    def _validate_phase8_approval(self) -> None:
        approval = self.approval_binding
        plan = self.execution_plan
        binding = self.binding
        expected = {
            "device_id": self.device_id,
            "document_id": plan.document_id,
            "document_revision": plan.expected_document_revision,
            "job_id": self.job_id,
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
            "source_digest": plan.source_digest,
            "execution_plan_digest": plan.execution_plan_digest,
            "execution_binding_digest": binding.execution_binding_digest,
            "expansion_digest": plan.expansion_digest,
            "effect_manifest_digest": plan.effect_manifest_digest,
            "target_refs_digest": plan.target_refs_digest,
            "validation_profiles_digest": plan.validation_profiles_digest,
            "checkpoint_strategy_digest": plan.checkpoint_strategy_digest,
            "hard_budgets_digest": plan.hard_budgets_digest,
            "preview_id": self.preview_id,
            "preview_digest": self.preview_digest,
            "preview_expires_at": self.expires_at,
            "receipt_id": self.receipt_id,
        }
        if any(getattr(approval, field) != value for field, value in expected.items()):
            raise ValueError("approval binding does not match Phase 8 command identity")


def program_command_payload(
    command: ProgramCommandMessage | dict[str, Any],
) -> dict[str, Any]:
    """Return the only canonical projection covered by Program payload_hash."""

    parsed = (
        command
        if isinstance(command, ProgramCommandMessage)
        else ProgramCommandMessage.model_validate(command)
    )
    payload: dict[str, Any] = {
        "kind": parsed.kind,
        "effect_class": parsed.effect_class,
        "binding": parsed.binding.model_dump(mode="json"),
    }
    if parsed.program is not None:
        payload["program"] = parsed.program.model_dump(mode="json", exclude_none=True)
    if parsed.execution_plan is not None:
        payload["execution_plan"] = parsed.execution_plan.model_dump(mode="json")
    if parsed.approval_binding is not None:
        payload["approval_binding"] = parsed.approval_binding.model_dump(mode="json")
    if parsed.capability_evidence is not None:
        payload["capability_evidence"] = [
            item.model_dump(mode="json") for item in parsed.capability_evidence
        ]
    if parsed.preview_id is not None:
        payload["preview_id"] = parsed.preview_id
    if parsed.expires_at is not None:
        payload["expires_at"] = parsed.expires_at
    if parsed.preview_digest is not None:
        payload["preview_digest"] = parsed.preview_digest
    if parsed.receipt_id is not None:
        payload["receipt_id"] = parsed.receipt_id
    if parsed.validation is not None:
        payload["validation"] = parsed.validation.model_dump(
            mode="json",
            exclude_none=True,
        )
    return payload


def program_command_payload_hash(
    command: ProgramCommandMessage | dict[str, Any],
) -> str:
    """Return the raw lowercase 64-hex hash used by Agent envelope payload_hash."""

    return canonical_payload_hash(program_command_payload(command))


class ProgramPreviewResult(AgentModel):
    preview_id: str = Field(min_length=1, max_length=128)
    preview_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expires_at: str = Field(min_length=1, max_length=64)
    planned_operation_count: int = Field(ge=1, le=256)
    planned_entity_count: int = Field(ge=0, le=256)
    planned_layer_count: int = Field(ge=0, le=64)
    transaction_aborted: Literal[True]
    drawing_unchanged: Literal[True]


class HostCheckpointEntity(AgentModel):
    handle: str = Field(pattern=r"^[0-9A-F]{1,32}$")
    entity_type: str = Field(min_length=1, max_length=128)
    layer: str = Field(min_length=1, max_length=255)
    canonical_fingerprint: str = Field(pattern=_PREFIXED_SHA256_PATTERN)


class HostRollbackCheckpoint(AgentModel):
    schema_version: Literal["cad.rollback.checkpoint/1"]
    checkpoint_id: str = Field(min_length=1, max_length=128)
    original_receipt_id: str = Field(min_length=1, max_length=128)
    original_receipt_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    program_id: str = Field(min_length=1, max_length=128)
    program_revision: int = Field(ge=1, le=2_147_483_647)
    program_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    preview_id: str = Field(min_length=1, max_length=128)
    preview_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    execution_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    document_id: str = Field(min_length=1, max_length=128)
    document_revision_before: str = Field(min_length=1, max_length=256)
    document_revision_after: str = Field(min_length=1, max_length=256)
    created_entities: list[HostCheckpointEntity] = Field(min_length=1, max_length=256)
    non_entity_object_created: bool
    runtime_and_policy_pins: ProgramExecutionBinding
    created_at: str = Field(min_length=1, max_length=64)
    checkpoint_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def _created_at_is_timezone_aware(cls, value: str) -> str:
        return _timezone_timestamp(value)


class ProgramCommitResult(AgentModel):
    receipt_id: str = Field(min_length=1, max_length=128)
    receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    document_revision_before: str = Field(min_length=1, max_length=256, pattern=r"^\S+$")
    document_revision_after: str = Field(min_length=1, max_length=256, pattern=r"^\S+$")
    created_entity_count: int = Field(ge=0, le=256)
    rollback_eligible: bool = False
    checkpoint_id: str | None = Field(default=None, min_length=1, max_length=128)
    checkpoint_digest: str | None = Field(
        default=None, pattern=_PREFIXED_SHA256_PATTERN
    )
    checkpoint: HostRollbackCheckpoint | None = None
    milestone: Literal["effect_and_receipt_committed"] | None = None
    duplicate: bool = False

    @model_validator(mode="after")
    def _checkpoint_fields_are_consistent(self) -> "ProgramCommitResult":
        present = (
            self.checkpoint_id is not None,
            self.checkpoint_digest is not None,
            self.checkpoint is not None,
        )
        if self.rollback_eligible != all(present):
            raise ValueError("rollback eligibility requires the exact checkpoint")
        if self.checkpoint is not None and (
            self.checkpoint.checkpoint_id != self.checkpoint_id
            or self.checkpoint.checkpoint_digest != self.checkpoint_digest
            or self.checkpoint.original_receipt_id != self.receipt_id
            or self.checkpoint.original_receipt_digest != self.receipt_digest
        ):
            raise ValueError("checkpoint does not bind the exact commit receipt")
        if self.rollback_eligible and self.milestone != "effect_and_receipt_committed":
            raise ValueError("rollback-safe commit requires terminal Host milestone")
        return self


class ReceiptLookupArguments(AgentModel):
    receipt_id: str = Field(min_length=1, max_length=128)


class CheckpointLookupArguments(AgentModel):
    checkpoint_id: str = Field(min_length=1, max_length=128)


class RollbackPreviewArguments(AgentModel):
    checkpoint_id: str = Field(min_length=1, max_length=128)
    checkpoint_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    rollback_plan_id: str = Field(min_length=1, max_length=128)
    rollback_execution_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    expires_at: str = Field(min_length=1, max_length=64)

    @field_validator("expires_at")
    @classmethod
    def _expiry_is_timezone_aware(cls, value: str) -> str:
        return _timezone_timestamp(value)


class RollbackCommitArguments(RollbackPreviewArguments):
    rollback_plan_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    rollback_receipt_id: str = Field(min_length=1, max_length=128)


class RollbackValidateArguments(AgentModel):
    rollback_receipt_id: str = Field(min_length=1, max_length=128)


RollbackArguments: TypeAlias = Annotated[
    Union[
        ReceiptLookupArguments,
        CheckpointLookupArguments,
        RollbackPreviewArguments,
        RollbackCommitArguments,
        RollbackValidateArguments,
    ],
    Field(union_mode="left_to_right"),
]


class RollbackCommandMessage(AgentEnvelope):
    protocol_version: Literal["cad.agent/2"] = PHASE5_PROTOCOL_VERSION
    message_type: Literal["command"] = "command"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    payload_hash: str = Field(pattern=_SHA256_PATTERN)
    kind: Literal[
        "receipt_lookup",
        "checkpoint_lookup",
        "rollback_preview",
        "rollback_commit",
        "rollback_validate",
    ]
    effect_class: Literal["read", "write"]
    binding: ProgramExecutionBinding
    arguments: RollbackArguments
    intent_id: str | None = Field(default=None, min_length=1, max_length=128)
    intent_digest: str | None = Field(default=None, pattern=_PREFIXED_SHA256_PATTERN)

    @model_validator(mode="after")
    def _rollback_fields_match_kind(self) -> "RollbackCommandMessage":
        expected = {
            "receipt_lookup": ReceiptLookupArguments,
            "checkpoint_lookup": CheckpointLookupArguments,
            "rollback_preview": RollbackPreviewArguments,
            "rollback_commit": RollbackCommitArguments,
            "rollback_validate": RollbackValidateArguments,
        }[self.kind]
        if not isinstance(self.arguments, expected):
            raise ValueError("rollback arguments do not match command kind")
        if self.effect_class != ("write" if self.kind == "rollback_commit" else "read"):
            raise ValueError("rollback effect class does not match command kind")
        if self.kind == "rollback_commit":
            if self.intent_id is None or self.intent_digest is None:
                raise ValueError("rollback commit requires released intent binding")
        elif self.intent_id is not None or self.intent_digest is not None:
            raise ValueError("read-only rollback commands cannot carry approval binding")
        return self


def rollback_command_payload(
    command: RollbackCommandMessage | dict[str, Any],
) -> dict[str, Any]:
    parsed = (
        command
        if isinstance(command, RollbackCommandMessage)
        else RollbackCommandMessage.model_validate(
            {**command, "payload_hash": "0" * 64}
        )
    )
    value: dict[str, Any] = {
        "kind": parsed.kind,
        "effect_class": parsed.effect_class,
        "binding": parsed.binding.model_dump(mode="json"),
        "arguments": parsed.arguments.model_dump(mode="json"),
    }
    if parsed.intent_id is not None:
        value["intent_id"] = parsed.intent_id
        value["intent_digest"] = parsed.intent_digest
    return value


def rollback_command_payload_hash(
    command: RollbackCommandMessage | dict[str, Any],
) -> str:
    return canonical_payload_hash(rollback_command_payload(command))


class HostRemovedEntityEvidence(AgentModel):
    handle: str = Field(pattern=r"^[0-9A-F]{1,32}$")
    entity_type: str = Field(min_length=1, max_length=128)
    prior_fingerprint: str = Field(pattern=_PREFIXED_SHA256_PATTERN)


class HostRollbackReceipt(AgentModel):
    schema_version: Literal["cad.rollback.receipt/1"]
    rollback_receipt_id: str = Field(min_length=1, max_length=128)
    original_receipt_id: str = Field(min_length=1, max_length=128)
    original_receipt_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    checkpoint_id: str = Field(min_length=1, max_length=128)
    checkpoint_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    rollback_plan_id: str = Field(min_length=1, max_length=128)
    rollback_plan_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    rollback_execution_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    document_id: str = Field(min_length=1, max_length=128)
    document_revision_before: str = Field(min_length=1, max_length=256)
    document_revision_after: str = Field(min_length=1, max_length=256)
    removed_entities: list[HostRemovedEntityEvidence] = Field(
        min_length=1, max_length=256
    )
    runtime_and_policy_pins: ProgramExecutionBinding
    created_at: str = Field(min_length=1, max_length=64)
    receipt_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def _receipt_created_at_is_timezone_aware(cls, value: str) -> str:
        return _timezone_timestamp(value)


class RollbackPreviewResult(AgentModel):
    checkpoint_id: str = Field(min_length=1, max_length=128)
    checkpoint_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    current_document_revision: str = Field(min_length=1, max_length=256)
    eligible: bool
    conflicts: list[dict[str, Any]] = Field(default_factory=list, max_length=256)
    milestone: Literal["transaction_aborted"]
    runtime_pins: dict[str, Any]
    policy_pins: dict[str, Any]


class RollbackCommitResult(AgentModel):
    rollback_receipt_id: str = Field(min_length=1, max_length=128)
    receipt_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    original_receipt_id: str = Field(min_length=1, max_length=128)
    checkpoint_id: str = Field(min_length=1, max_length=128)
    checkpoint_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    rollback_plan_id: str = Field(min_length=1, max_length=128)
    rollback_plan_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    rollback_execution_digest: str = Field(pattern=_PREFIXED_SHA256_PATTERN)
    document_revision_before: str = Field(min_length=1, max_length=256)
    document_revision_after: str = Field(min_length=1, max_length=256)
    removed_entity_count: int = Field(ge=1, le=256)
    receipt: HostRollbackReceipt
    milestone: Literal["effect_and_receipt_committed"]
    duplicate: bool = False


class RollbackValidateResult(AgentModel):
    rollback_receipt_id: str = Field(min_length=1, max_length=128)
    valid: bool
    document_revision: str = Field(min_length=1, max_length=256)
    checks: list[str] = Field(default_factory=list, max_length=64)
    failures: list[str] = Field(default_factory=list, max_length=64)


RollbackResultPayload: TypeAlias = Annotated[
    Union[RollbackPreviewResult, RollbackCommitResult, RollbackValidateResult, dict[str, Any]],
    Field(union_mode="left_to_right"),
]


class RollbackResultMessage(AgentEnvelope):
    protocol_version: Literal["cad.agent/2"] = PHASE5_PROTOCOL_VERSION
    message_type: Literal["result"] = "result"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1, le=MAX_SEQUENCE)
    kind: Literal[
        "receipt_lookup",
        "checkpoint_lookup",
        "rollback_preview",
        "rollback_commit",
        "rollback_validate",
    ]
    status: Literal["succeeded", "failed", "cancelled", "outcome_unknown"]
    payload_hash: str = Field(pattern=_SHA256_PATTERN)
    binding: ProgramExecutionBinding
    result: RollbackResultPayload | None = None
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=MAX_MESSAGE_TEXT)

    @model_validator(mode="after")
    def _rollback_terminal_fields_match(self) -> "RollbackResultMessage":
        if self.status == "succeeded":
            expected = {
                "rollback_preview": RollbackPreviewResult,
                "rollback_commit": RollbackCommitResult,
                "rollback_validate": RollbackValidateResult,
            }.get(self.kind, dict)
            if expected is dict:
                if not isinstance(self.result, dict):
                    raise ValueError("lookup result must be a bounded object")
            elif not isinstance(self.result, expected):
                raise ValueError("rollback result payload does not match kind")
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("successful rollback result cannot include errors")
        elif self.result is not None:
            raise ValueError("non-successful rollback result cannot include payload")
        elif self.status == "failed" and self.error_code is None:
            raise ValueError("failed rollback result requires error_code")
        elif self.status == "outcome_unknown" and self.kind != "rollback_commit":
            raise ValueError("only rollback commit may have outcome_unknown")
        return self


class ProgramValidateResult(AgentModel):
    validation_id: str = Field(min_length=1, max_length=128)
    valid: bool
    document_revision: str = Field(min_length=1, max_length=256, pattern=r"^\S+$")
    checks: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list,
        max_length=64,
    )
    failures: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list,
        max_length=64,
    )


ProgramResultPayload: TypeAlias = Annotated[
    Union[ProgramPreviewResult, ProgramCommitResult, ProgramValidateResult],
    Field(union_mode="left_to_right"),
]


class ProgramResultMessage(AgentEnvelope):
    protocol_version: Literal["cad.agent/2"] = PHASE5_PROTOCOL_VERSION
    message_type: Literal["result"] = "result"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1, le=MAX_SEQUENCE)
    kind: Literal["program_preview", "program_commit", "program_validate"]
    status: Literal["succeeded", "failed", "cancelled", "outcome_unknown"]
    payload_hash: str = Field(pattern=_SHA256_PATTERN)
    binding: ProgramExecutionBinding
    result: ProgramResultPayload | None = None
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=MAX_MESSAGE_TEXT)

    @model_validator(mode="after")
    def _terminal_fields_match_status_and_kind(self) -> "ProgramResultMessage":
        if self.status == "succeeded":
            expected = {
                "program_preview": ProgramPreviewResult,
                "program_commit": ProgramCommitResult,
                "program_validate": ProgramValidateResult,
            }[self.kind]
            if not isinstance(self.result, expected):
                raise ValueError("successful program result payload does not match kind")
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("successful result cannot include error fields")
        elif self.result is not None:
            raise ValueError("non-successful program result cannot include result payload")
        elif self.status == "failed" and self.error_code is None:
            raise ValueError("failed program result requires error_code")
        elif self.status == "outcome_unknown" and self.kind != "program_commit":
            raise ValueError("only commit may have outcome_unknown")
        return self


class AckMessage(AgentEnvelope):
    message_type: Literal["ack"] = "ack"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1, le=MAX_SEQUENCE)
    status: Literal["accepted", "duplicate", "rejected", "already_terminal"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    payload_hash: str = Field(pattern=_SHA256_PATTERN)
    reason: str | None = Field(default=None, max_length=MAX_MESSAGE_TEXT)


class ProgressMessage(AgentEnvelope):
    message_type: Literal["progress"] = "progress"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1, le=MAX_SEQUENCE)
    payload_hash: str = Field(pattern=_SHA256_PATTERN)
    phase: str = Field(min_length=1, max_length=64)
    percent: int = Field(ge=0, le=100)
    message: str = Field(default="", max_length=MAX_MESSAGE_TEXT)


class ResultMessage(AgentEnvelope):
    message_type: Literal["result"] = "result"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1, le=MAX_SEQUENCE)
    status: Literal["succeeded", "failed", "cancelled"]
    payload_hash: str = Field(pattern=_SHA256_PATTERN)
    result: dict[str, Any] | None = Field(default=None, max_length=MAX_RESULT_ITEMS)
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=MAX_MESSAGE_TEXT)

    @model_validator(mode="after")
    def _terminal_fields_match_status(self) -> "ResultMessage":
        if self.status == "failed" and not self.error_code:
            raise ValueError("failed result requires error_code")
        if self.status != "failed" and (self.error_code is not None or self.error_message is not None):
            raise ValueError("only failed result may include error fields")
        return self


class CancelMessage(AgentEnvelope):
    message_type: Literal["cancel"] = "cancel"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="cancelled by gateway", max_length=MAX_MESSAGE_TEXT)


class ReconcileCommandDescriptor(AgentModel):
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    payload_hash: str = Field(pattern=_SHA256_PATTERN)


class ReconcileMessage(AgentEnvelope):
    message_type: Literal["reconcile"] = "reconcile"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    commands: list[ReconcileCommandDescriptor] = Field(
        min_length=1,
        max_length=MAX_RECONCILE_COMMANDS,
    )

    @model_validator(mode="after")
    def _command_ids_are_unique(self) -> "ReconcileMessage":
        command_ids = [item.command_id for item in self.commands]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("reconcile command IDs must be unique")
        return self


class ReconcileResultMessage(AgentEnvelope):
    message_type: Literal["reconcile_result"] = "reconcile_result"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1, le=MAX_SEQUENCE)
    status: Literal["not_started", "started", "terminal"]
    payload_hash: str = Field(pattern=_SHA256_PATTERN)
    result_status: Literal["succeeded", "failed", "cancelled"] | None = None
    result: dict[str, Any] | None = Field(default=None, max_length=MAX_RESULT_ITEMS)
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=MAX_MESSAGE_TEXT)
    kind: Literal[
        "program_preview",
        "program_commit",
        "program_validate",
        "receipt_lookup",
        "checkpoint_lookup",
        "rollback_preview",
        "rollback_commit",
        "rollback_validate",
    ] | None = None
    binding: ProgramExecutionBinding | None = None

    @model_validator(mode="after")
    def _reconcile_fields_match_status(self) -> "ReconcileResultMessage":
        terminal_fields = (
            self.result_status,
            self.result,
            self.error_code,
            self.error_message,
            self.kind,
            self.binding,
        )
        if self.status != "terminal":
            if any(value is not None for value in terminal_fields):
                raise ValueError("non-terminal reconciliation cannot include terminal fields")
            return self
        if self.result_status is None:
            raise ValueError("terminal reconciliation requires result_status")
        if self.result_status == "failed" and not self.error_code:
            raise ValueError("failed reconciliation requires error_code")
        if self.result_status != "failed" and (
            self.error_code is not None or self.error_message is not None
        ):
            raise ValueError("only failed reconciliation may include error fields")
        if (self.kind is None) != (self.binding is None):
            raise ValueError("typed reconciliation requires kind and binding together")
        return self


class ErrorMessage(AgentEnvelope):
    message_type: Literal["error"] = "error"
    code: Literal[
        "auth_failed",
        "incompatible",
        "invalid_message",
        "payload_mismatch",
        "sequence_rejected",
        "deadline_expired",
        "capability_mismatch",
        "binding_mismatch",
        "message_too_large",
        "internal_error",
    ]
    message: str = Field(max_length=MAX_MESSAGE_TEXT)


AgentMessage: TypeAlias = Union[
    HelloMessage,
    WelcomeMessage,
    ApprovalRequestMessage,
    ApprovalDecisionMessage,
    HeartbeatMessage,
    CommandMessage,
    ProgramCommandMessage,
    RollbackCommandMessage,
    AckMessage,
    ProgressMessage,
    ResultMessage,
    ProgramResultMessage,
    RollbackResultMessage,
    CancelMessage,
    ReconcileMessage,
    ReconcileResultMessage,
    ErrorMessage,
]

_MESSAGE_ADAPTER = TypeAdapter(AgentMessage)


def parse_agent_message(value: str | bytes | dict[str, Any]) -> AgentMessage:
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_WEBSOCKET_MESSAGE_BYTES:
            raise ValueError("Agent message exceeds the protocol byte limit")
        return _MESSAGE_ADAPTER.validate_json(value)
    if isinstance(value, bytes):
        if len(value) > MAX_WEBSOCKET_MESSAGE_BYTES:
            raise ValueError("Agent message exceeds the protocol byte limit")
        return _MESSAGE_ADAPTER.validate_json(value)
    validate_bounded_json(value)
    if len(canonical_json(value).encode("utf-8")) > MAX_WEBSOCKET_MESSAGE_BYTES:
        raise ValueError("Agent message exceeds the protocol byte limit")
    return _MESSAGE_ADAPTER.validate_python(value)


def negotiate_protocol(
    protocol_min_version: str,
    protocol_max_version: str,
    *,
    supported_versions: tuple[str, ...] = (PROTOCOL_VERSION,),
) -> str | None:
    for version in reversed(supported_versions):
        if protocol_min_version <= version <= protocol_max_version:
            return version
    return None


def message_dict(message: AgentMessage) -> dict[str, Any]:
    value = message.model_dump(mode="json", exclude_none=True)
    if len(canonical_json(value).encode("utf-8")) > MAX_WEBSOCKET_MESSAGE_BYTES:
        raise ValueError("Agent message exceeds the protocol byte limit")
    return value


def agent_approval_json_schema() -> dict[str, Any]:
    schema = TypeAdapter(
        Annotated[
            Union[ApprovalRequestMessage, ApprovalDecisionMessage],
            Field(discriminator="message_type"),
        ]
    ).json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        "https://schemas.kythuatvang.local/cad-agent-2-approval.schema.json"
    )
    schema["title"] = "cad.agent/2 trusted local approval control messages"
    return schema


def agent_program_command_json_schema() -> dict[str, Any]:
    schema = ProgramCommandMessage.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://schemas.kythuatvang.local/cad.agent/2/program-command.schema.json"
    schema["title"] = "cad.agent/2 typed CAD Program command"
    legacy_binding = {"$ref": "#/$defs/ProgramExecutionBinding"}
    phase8_binding = {"$ref": "#/$defs/ExecutionBindingV1"}
    phase8_fields = [
        "execution_plan",
        "approval_binding",
        "capability_evidence",
    ]
    schema["allOf"] = [
        {
            "if": {
                "properties": {"kind": {"const": "program_preview"}},
                "required": ["kind"],
            },
            "then": {
                "required": ["preview_id", "expires_at"],
                "properties": {
                    "effect_class": {"const": "write"},
                    "preview_id": {"type": "string"},
                    "expires_at": {"type": "string"},
                },
                "not": {
                    "anyOf": [
                        {"required": ["preview_digest"]},
                        {"required": ["receipt_id"]},
                        {"required": ["validation"]},
                        {"required": ["approval_binding"]},
                    ]
                },
                "oneOf": [
                    {
                        "required": ["program", "binding"],
                        "properties": {"binding": legacy_binding},
                        "not": {
                            "anyOf": [
                                {"required": [field]} for field in phase8_fields
                            ]
                        },
                    },
                    {
                        "required": [
                            "execution_plan",
                            "binding",
                            "capability_evidence",
                        ],
                        "properties": {"binding": phase8_binding},
                        "not": {"required": ["program"]},
                    },
                ],
            },
        },
        {
            "if": {
                "properties": {"kind": {"const": "program_commit"}},
                "required": ["kind"],
            },
            "then": {
                "required": ["preview_id", "preview_digest", "receipt_id"],
                "properties": {
                    "effect_class": {"const": "write"},
                    "preview_id": {"type": "string"},
                    "preview_digest": {"type": "string"},
                    "receipt_id": {"type": "string"},
                },
                "not": {"required": ["validation"]},
                "oneOf": [
                    {
                        "required": ["program", "binding"],
                        "properties": {"binding": legacy_binding},
                        "not": {
                            "anyOf": [
                                {"required": ["expires_at"]},
                                *[
                                    {"required": [field]}
                                    for field in phase8_fields
                                ],
                            ]
                        },
                    },
                    {
                        "required": [
                            "execution_plan",
                            "binding",
                            "capability_evidence",
                            "approval_binding",
                            "expires_at",
                        ],
                        "properties": {"binding": phase8_binding},
                        "not": {"required": ["program"]},
                    },
                ],
            },
        },
        {
            "if": {
                "properties": {"kind": {"const": "program_validate"}},
                "required": ["kind"],
            },
            "then": {
                "required": ["validation"],
                "properties": {
                    "effect_class": {"const": "read"},
                    "binding": legacy_binding,
                    "validation": {"$ref": "#/$defs/ProgramValidationRequest"},
                },
                "not": {
                    "anyOf": [
                        {"required": ["program"]},
                        {"required": ["preview_id"]},
                        {"required": ["expires_at"]},
                        {"required": ["preview_digest"]},
                        {"required": ["receipt_id"]},
                        *[
                            {"required": [field]}
                            for field in phase8_fields
                        ],
                    ]
                },
            },
        },
    ]
    return schema


def agent_program_result_json_schema() -> dict[str, Any]:
    schema = ProgramResultMessage.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://schemas.kythuatvang.local/cad.agent/2/program-result.schema.json"
    schema["title"] = "cad.agent/2 typed CAD Program result"
    schema["allOf"] = [
        {
            "if": {
                "properties": {
                    "kind": {"const": kind},
                    "status": {"const": "succeeded"},
                },
                "required": ["kind", "status"],
            },
            "then": {
                "required": ["result"],
                "properties": {"result": {"$ref": f"#/$defs/{definition}"}},
            },
        }
        for kind, definition in (
            ("program_preview", "ProgramPreviewResult"),
            ("program_commit", "ProgramCommitResult"),
            ("program_validate", "ProgramValidateResult"),
        )
    ]
    schema["allOf"].extend(
        [
            {
                "if": {
                    "properties": {"status": {"const": "failed"}},
                    "required": ["status"],
                },
                "then": {
                    "required": ["error_code"],
                    "properties": {"result": {"type": "null"}},
                },
            },
            {
                "if": {
                    "properties": {"status": {"enum": ["cancelled", "outcome_unknown"]}},
                    "required": ["status"],
                },
                "then": {"properties": {"result": {"type": "null"}}},
            },
            {
                "if": {
                    "properties": {"status": {"const": "outcome_unknown"}},
                    "required": ["status"],
                },
                "then": {"properties": {"kind": {"const": "program_commit"}}},
            },
        ]
    )
    return schema


def agent_rollback_json_schema() -> dict[str, Any]:
    schema = TypeAdapter(
        Annotated[
            Union[RollbackCommandMessage, RollbackResultMessage],
            Field(discriminator="message_type"),
        ]
    ).json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://schemas.kythuatvang.local/cad-agent-2-rollback.schema.json"
    schema["title"] = "cad.agent/2 typed rollback command and result messages"
    return schema


from .runtime import CapabilityManifest
from .program import CadProgram
from .phase8_contracts import (
    CadExecutionPlanV1,
    ExecutionBindingV1,
    Phase8ApprovalBinding,
    Phase8CapabilityEvidence,
)

HelloMessage.model_rebuild()
ProgramCommandMessage.model_rebuild()
RollbackCommandMessage.model_rebuild()
RollbackResultMessage.model_rebuild()
