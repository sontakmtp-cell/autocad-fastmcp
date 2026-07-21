"""Strict, dependency-light ``cad.agent/1`` wire protocol models."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal, TypeAlias, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


PROTOCOL_VERSION = "cad.agent/1"
MAX_MESSAGE_TEXT = 2048
MAX_PAYLOAD_ITEMS = 64
MAX_RESULT_ITEMS = 128


def _message_id() -> str:
    return str(uuid.uuid4())


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AgentEnvelope(AgentModel):
    protocol_version: str = Field(default=PROTOCOL_VERSION, min_length=1, max_length=32)
    message_type: str = Field(min_length=1, max_length=32)
    message_id: str = Field(default_factory=_message_id, min_length=1, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    device_id: str | None = Field(default=None, max_length=128)
    job_id: str | None = Field(default=None, max_length=128)
    command_id: str | None = Field(default=None, max_length=128)
    sequence: int = Field(default=0, ge=0, le=1_000_000_000)
    issued_at: str = Field(default_factory=_timestamp, min_length=1, max_length=64)
    deadline_at: str | None = Field(default=None, max_length=64)


class HelloMessage(AgentEnvelope):
    message_type: Literal["hello"] = "hello"
    device_id: str = Field(min_length=1, max_length=128)
    protocol_min_version: str = Field(default=PROTOCOL_VERSION, min_length=1, max_length=32)
    protocol_max_version: str = Field(default=PROTOCOL_VERSION, min_length=1, max_length=32)
    fixture_proof: str = Field(min_length=1, max_length=256)
    capability_hash: str = Field(min_length=1, max_length=128)
    capabilities: list[str] = Field(default_factory=list, max_length=64)
    last_processed_sequence: int = Field(default=0, ge=0, le=1_000_000_000)


class WelcomeMessage(AgentEnvelope):
    message_type: Literal["welcome"] = "welcome"
    session_id: str = Field(min_length=1, max_length=128)
    selected_version: str = Field(default=PROTOCOL_VERSION, min_length=1, max_length=32)
    heartbeat_interval_seconds: int = Field(default=10, ge=1, le=300)
    server_time: str = Field(default_factory=_timestamp, min_length=1, max_length=64)


class HeartbeatMessage(AgentEnvelope):
    message_type: Literal["heartbeat"] = "heartbeat"
    device_id: str = Field(min_length=1, max_length=128)
    busy: bool = False
    last_processed_sequence: int = Field(default=0, ge=0, le=1_000_000_000)
    current_job_id: str | None = Field(default=None, max_length=128)


class CommandMessage(AgentEnvelope):
    message_type: Literal["command"] = "command"
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    payload_hash: str = Field(min_length=64, max_length=64)
    kind: Literal["observe", "write_fixture"] = "observe"
    effect_class: Literal["read", "write"] = "read"
    payload: dict[str, Any] = Field(default_factory=dict, max_length=MAX_PAYLOAD_ITEMS)


class AckMessage(AgentEnvelope):
    message_type: Literal["ack"] = "ack"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    status: Literal["accepted", "duplicate", "rejected", "already_terminal"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    payload_hash: str = Field(min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=MAX_MESSAGE_TEXT)


class ProgressMessage(AgentEnvelope):
    message_type: Literal["progress"] = "progress"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    phase: str = Field(min_length=1, max_length=64)
    percent: int = Field(ge=0, le=100)
    message: str = Field(default="", max_length=MAX_MESSAGE_TEXT)


class ResultMessage(AgentEnvelope):
    message_type: Literal["result"] = "result"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    status: Literal["succeeded", "failed", "cancelled"]
    payload_hash: str = Field(min_length=64, max_length=64)
    result: dict[str, Any] | None = Field(default=None, max_length=MAX_RESULT_ITEMS)
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=MAX_MESSAGE_TEXT)


class CancelMessage(AgentEnvelope):
    message_type: Literal["cancel"] = "cancel"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="cancelled by gateway", max_length=MAX_MESSAGE_TEXT)


class ReconcileMessage(AgentEnvelope):
    message_type: Literal["reconcile"] = "reconcile"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    command_ids: list[str] = Field(default_factory=list, max_length=64)


class ReconcileResultMessage(AgentEnvelope):
    message_type: Literal["reconcile_result"] = "reconcile_result"
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    status: Literal["not_started", "started", "terminal"]
    payload_hash: str = Field(min_length=64, max_length=64)
    result_status: Literal["succeeded", "failed", "cancelled"] | None = None
    result: dict[str, Any] | None = Field(default=None, max_length=MAX_RESULT_ITEMS)


class ErrorMessage(AgentEnvelope):
    message_type: Literal["error"] = "error"
    code: Literal[
        "auth_failed",
        "incompatible",
        "invalid_message",
        "payload_mismatch",
        "sequence_rejected",
        "deadline_expired",
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

_MESSAGE_ADAPTER = TypeAdapter(
    Union[
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
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def parse_agent_message(value: str | bytes | dict[str, Any]) -> AgentMessage:
    if isinstance(value, (str, bytes)):
        return _MESSAGE_ADAPTER.validate_json(value)
    return _MESSAGE_ADAPTER.validate_python(value)


def negotiate_protocol(
    protocol_min_version: str, protocol_max_version: str
) -> str | None:
    if protocol_min_version <= PROTOCOL_VERSION <= protocol_max_version:
        return PROTOCOL_VERSION
    return None


def message_dict(message: AgentMessage) -> dict[str, Any]:
    return message.model_dump(mode="json", exclude_none=True)
