"""Bounded ``cad.host/1`` client for the local Managed .NET read host."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from autocad_contracts import CapabilityManifest, canonical_json

from .contracts import RuntimeProbe


PROTOCOL = "cad.host/1"
MAX_FRAME_BYTES = 65_536


@dataclass(frozen=True)
class CadPortResult:
    ok: bool
    payload: dict[str, Any] | None = None
    error_code: str | None = None
    details: dict[str, Any] | None = None


class HostTransport(Protocol):
    async def request(self, envelope: dict[str, Any]) -> dict[str, Any]: ...


class NamedPipeJsonTransport:
    """One authenticated Host session over a current-user Named Pipe."""

    def __init__(self, pipe_name: str) -> None:
        if os.name != "nt":
            raise OSError("Managed Host Named Pipe is only available on Windows")
        if not pipe_name or "\\" in pipe_name or "/" in pipe_name:
            raise ValueError("pipe_name must be a logical local name")
        self._path = rf"\\.\pipe\{pipe_name}"
        self._stream: Any | None = None
        self._lock = asyncio.Lock()

    async def request(self, envelope: dict[str, Any]) -> dict[str, Any]:
        body = canonical_json(envelope).encode("utf-8")
        if len(body) > MAX_FRAME_BYTES:
            raise ValueError("cad.host frame exceeds the bounded limit")
        async with self._lock:
            return await asyncio.to_thread(self._request_sync, body)

    def _request_sync(self, body: bytes) -> dict[str, Any]:
        if self._stream is None:
            self._stream = open(self._path, "r+b", buffering=0)  # noqa: SIM115
        try:
            self._stream.write(struct.pack("<I", len(body)) + body)
            size = struct.unpack("<I", self._read_exact(4))[0]
            if size <= 0 or size > MAX_FRAME_BYTES:
                raise ValueError("cad.host response frame is invalid")
            value = json.loads(self._read_exact(size).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("cad.host response must be an object")
            return value
        except Exception:
            self.close()
            raise

    def _read_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self._stream.read(size - len(chunks))
            if not chunk:
                raise EOFError("Managed Host disconnected")
            chunks.extend(chunk)
        return bytes(chunks)

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.close()


class ManagedDotNetCadReadPort:
    """Read-only adapter. It has no raw command, path, assembly, or write API."""

    runtime_id = "managed_dotnet"

    def __init__(
        self,
        transport: HostTransport,
        *,
        session_secret: bytes,
        agent_version: str,
        expected_host_family: str | None = None,
    ) -> None:
        if len(session_secret) < 32:
            raise ValueError("Managed Host session secret is too short")
        self._transport = transport
        self._secret = bytes(session_secret)
        self._agent_version = agent_version
        self._expected_host_family = expected_host_family
        self._session_id = f"agent-{uuid.uuid4().hex}"
        self._sequence = 0
        self._handshake: dict[str, Any] | None = None
        self._handshake_error: str | None = None

    @classmethod
    def from_bootstrap(
        cls,
        bootstrap_path: str | Path,
        *,
        agent_version: str,
        expected_host_family: str | None = None,
    ) -> "ManagedDotNetCadReadPort":
        value = json.loads(Path(bootstrap_path).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("protocol_version") != PROTOCOL:
            raise ValueError("Managed Host bootstrap protocol is invalid")
        pipe_name = value.get("pipe_name")
        encoded_secret = value.get("session_secret_base64")
        if not isinstance(pipe_name, str) or not isinstance(encoded_secret, str):
            raise ValueError("Managed Host bootstrap is incomplete")
        secret = base64.b64decode(encoded_secret, validate=True)
        return cls(
            NamedPipeJsonTransport(pipe_name),
            session_secret=secret,
            agent_version=agent_version,
            expected_host_family=expected_host_family,
        )

    @classmethod
    def from_default_bootstrap(
        cls,
        *,
        agent_version: str,
        expected_host_family: str | None = None,
    ) -> "ManagedDotNetCadReadPort":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise OSError("LOCALAPPDATA is unavailable")
        return cls.from_bootstrap(
            Path(local)
            / "KythuatVang"
            / "AutoCADMcp"
            / "managed-host-r25.json",
            agent_version=agent_version,
            expected_host_family=expected_host_family,
        )

    async def probe(self) -> RuntimeProbe:
        try:
            handshake = await self._ensure_handshake()
        except Exception as error:
            self._handshake_error = self._safe_error(error)
            return RuntimeProbe(
                runtime_id=self.runtime_id,
                available=False,
                reason=self._handshake_error,
            )
        return RuntimeProbe(
            runtime_id=self.runtime_id,
            available=True,
            product=handshake["product"],
            edition=handshake["edition"],
            release_year=handshake["release_year"],
            series=handshake.get("series"),
            active_document=handshake.get("active_document_id"),
        )

    async def health(self) -> CadPortResult:
        try:
            handshake = await self._ensure_handshake()
            result = await self._command("host.health", arguments={})
        except Exception as error:
            code = self._safe_error(error)
            self._handshake = None
            return CadPortResult(False, error_code=code, details={"handshake_state": "failed"})
        details = dict(result)
        details.setdefault("product", handshake["product"])
        details.setdefault("edition", handshake["edition"])
        details.setdefault("release_year", handshake["release_year"])
        details.setdefault("series", handshake.get("series"))
        details.setdefault(
            "active_document",
            result.get("active_document_name") or result.get("document_name"),
        )
        details["handshake_state"] = "connected"
        return CadPortResult(True, payload=details)

    async def drawing_info(self) -> CadPortResult:
        try:
            handshake = await self._ensure_handshake()
            result = await self._command(
                "drawing.observe.summary",
                document_id=handshake.get("active_document_id"),
                arguments={"include_layers": True, "max_layers": 256},
            )
        except Exception as error:
            code = self._safe_error(error)
            if code in {"managed_host_unavailable", "session_rejected"}:
                self._handshake = None
            return CadPortResult(False, error_code=code)
        value = dict(result)
        value.setdefault("layers", [])
        value.setdefault("layer_count", len(value["layers"]))
        value.setdefault("truncated", False)
        return CadPortResult(True, payload=value)

    def manifest(self, probe: RuntimeProbe) -> CapabilityManifest:
        if self._handshake is None:
            raise RuntimeError("managed_host_unavailable")
        handshake = self._handshake
        capabilities = [
            "observe.summary"
            for capability in handshake["capabilities"]
            if capability == "observe.summary"
        ]
        return CapabilityManifest.model_validate(
            {
                "schema_version": "cad.capability/1",
                "registry_version": "cad.program/0",
                "cad_products": [
                    {
                        "product": probe.product or handshake["product"],
                        "edition": "full",
                        "release_year": probe.release_year,
                        "series": probe.series,
                        "runtime": {
                            "id": self.runtime_id,
                            "role": "primary",
                            "host_family": handshake["host_family"],
                            "host_version": handshake["host_version"],
                            "framework": ".NET 8",
                            "package_id": handshake["package_id"],
                            "package_version": handshake["package_version"],
                            "package_hash": handshake["package_hash"],
                        },
                        "capabilities": capabilities,
                    }
                ],
            }
        )

    async def _ensure_handshake(self) -> dict[str, Any]:
        if self._handshake is not None:
            return self._handshake
        nonce = secrets.token_hex(16)
        payload = {
            "session_nonce": nonce,
            "agent_version": self._agent_version,
            "protocol_min": PROTOCOL,
            "protocol_max": PROTOCOL,
        }
        response = await self._transport.request(self._envelope("handshake", payload))
        value = self._validate_response(response, expected_type="handshake_result")
        required = {
            "selected_protocol",
            "host_family",
            "host_version",
            "package_id",
            "package_version",
            "package_hash",
            "session_proof",
            "product",
            "edition",
            "release_year",
            "capabilities",
        }
        if not required.issubset(value) or value["selected_protocol"] != PROTOCOL:
            raise RuntimeError("protocol_mismatch")
        expected_proof = hmac.new(
            self._secret,
            f"{PROTOCOL}\n{self._session_id}\n{nonce}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(str(value["session_proof"]), expected_proof):
            raise RuntimeError("session_rejected")
        if value["edition"] != "full":
            raise RuntimeError("protocol_mismatch")
        if (
            self._expected_host_family is not None
            and value["host_family"] != self._expected_host_family
        ):
            raise RuntimeError("runtime_version_mismatch")
        capabilities = value["capabilities"]
        if not isinstance(capabilities, list) or "observe.summary" not in capabilities:
            raise RuntimeError("capability_missing")
        self._handshake = value
        return value

    async def _command(
        self,
        operation_id: str,
        *,
        arguments: dict[str, Any],
        document_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operation_id": operation_id,
            "operation_version": 1,
            "arguments": arguments,
        }
        if document_id is not None:
            payload["document_id"] = document_id
        response = await self._transport.request(self._envelope("command", payload))
        value = self._validate_response(response, expected_type="result")
        if value.get("operation_id") != operation_id:
            raise RuntimeError("protocol_mismatch")
        result = value.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("protocol_mismatch")
        return result

    def _envelope(self, message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        command_id = f"{message_type}-{uuid.uuid4().hex}"
        deadline = datetime.now(timezone.utc) + timedelta(seconds=10)
        # .NET's round-trip "O" parser requires seven fractional-second
        # digits; Python's ISO formatter emits six.
        deadline_at = (
            deadline.strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{deadline.microsecond:06d}0+00:00"
        )
        envelope = {
            "protocol_version": PROTOCOL,
            "message_type": message_type,
            "session_id": self._session_id,
            "command_id": command_id,
            "sequence": self._sequence,
            "deadline_at": deadline_at,
            "payload_hash": hashlib.sha256(
                canonical_json(payload).encode("utf-8")
            ).hexdigest(),
            "payload": payload,
        }
        self._sequence += 1
        return envelope

    def _validate_response(
        self,
        response: dict[str, Any],
        *,
        expected_type: str,
    ) -> dict[str, Any]:
        if response.get("protocol_version") != PROTOCOL:
            raise RuntimeError("protocol_mismatch")
        if response.get("session_id") != self._session_id:
            raise RuntimeError("session_rejected")
        payload = response.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("protocol_mismatch")
        expected_hash = hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest()
        if response.get("payload_hash") != expected_hash:
            raise RuntimeError("payload_mismatch")
        if response.get("message_type") == "error":
            raise RuntimeError(str(payload.get("error_code", "internal_error")))
        if response.get("message_type") != expected_type:
            raise RuntimeError("protocol_mismatch")
        return payload

    @staticmethod
    def _safe_error(error: Exception) -> str:
        code = str(error)
        allowed = {
            "protocol_mismatch",
            "session_rejected",
            "payload_mismatch",
            "runtime_version_mismatch",
            "capability_missing",
            "no_active_document",
            "autocad_busy",
            "modal_dialog_active",
            "active_document_changed",
        }
        return code if code in allowed else "managed_host_unavailable"
