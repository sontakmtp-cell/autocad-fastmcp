"""Strict, dependency-light ``cad.agent/1`` wire protocol models."""

from __future__ import annotations

import copy
import json
import math
import re
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal, TypeAlias, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)


PROTOCOL_VERSION = "cad.agent/1"
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

_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


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

    package_id: str = Field(min_length=1, max_length=128, pattern=_CAPABILITY_PATTERN.pattern)
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


class HelloMessage(AgentEnvelope):
    message_type: Literal["hello"] = "hello"
    device_id: str = Field(min_length=1, max_length=128)
    protocol_min_version: str = Field(default=PROTOCOL_VERSION, min_length=1, max_length=32)
    protocol_max_version: str = Field(default=PROTOCOL_VERSION, min_length=1, max_length=32)
    fixture_proof: str = Field(min_length=1, max_length=256)
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

    @model_validator(mode="after")
    def _processed_sequence_is_not_future(self) -> "HeartbeatMessage":
        if self.last_processed_sequence >= self.sequence:
            raise ValueError("last_processed_sequence must precede heartbeat sequence")
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

    @model_validator(mode="after")
    def _reconcile_fields_match_status(self) -> "ReconcileResultMessage":
        terminal_fields = (
            self.result_status,
            self.result,
            self.error_code,
            self.error_message,
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
    HeartbeatMessage,
    CommandMessage,
    AckMessage,
    ProgressMessage,
    ResultMessage,
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


def negotiate_protocol(protocol_min_version: str, protocol_max_version: str) -> str | None:
    if protocol_min_version <= PROTOCOL_VERSION <= protocol_max_version:
        return PROTOCOL_VERSION
    return None


def message_dict(message: AgentMessage) -> dict[str, Any]:
    value = message.model_dump(mode="json", exclude_none=True)
    if len(canonical_json(value).encode("utf-8")) > MAX_WEBSOCKET_MESSAGE_BYTES:
        raise ValueError("Agent message exceeds the protocol byte limit")
    return value
