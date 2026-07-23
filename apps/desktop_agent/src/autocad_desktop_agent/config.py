"""Fail-closed Phase 4 Agent configuration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AgentConfig:
    gateway_ws_url: str
    device_id: str
    device_name: str
    ledger_path: Path
    package_path: Path
    package_id: str = "autocad.lisp.drawing_info"
    package_version: str = "3.3-c1"
    package_sha256: str = ""
    heartbeat_seconds: int = 10
    reconnect_max_seconds: int = 30
    queue_size: int = 8

    @classmethod
    def from_env(cls) -> "AgentConfig":
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Kythuatvang" / "AutoCADAgent"
        config = cls(
            gateway_ws_url=os.environ.get("AUTOCAD_AGENT_GATEWAY_WS_URL", "").strip(),
            device_id=os.environ.get("AUTOCAD_AGENT_DEVICE_ID", "").strip(),
            device_name=os.environ.get("AUTOCAD_AGENT_DEVICE_NAME", "Máy AutoCAD Lab").strip(),
            ledger_path=Path(os.environ.get("AUTOCAD_AGENT_LEDGER_PATH", str(local / "agent.db"))),
            package_path=Path(
                os.environ.get(
                    "AUTOCAD_AGENT_PACKAGE_PATH",
                    str(local / "packages" / "autocad.lisp.drawing_info" / "3.3-c1" / "mcp_dispatch.lsp"),
                )
            ),
            package_sha256=os.environ.get("AUTOCAD_AGENT_PACKAGE_SHA256", "").strip(),
            heartbeat_seconds=int(os.environ.get("AUTOCAD_AGENT_HEARTBEAT_SECONDS", "10")),
        )
        return config.validate()

    def validate(self) -> "AgentConfig":
        parsed = urlsplit(self.gateway_ws_url)
        if parsed.scheme not in {"wss", "ws"} or not parsed.netloc:
            raise ValueError("gateway_ws_url must be an absolute WebSocket URL")
        if parsed.scheme == "ws" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("non-local Agent connections require wss")
        if parsed.path != "/agent/ws" or parsed.query or parsed.fragment:
            raise ValueError("gateway_ws_url must use the canonical /agent/ws path")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.device_id):
            raise ValueError("device_id is malformed")
        if not self.device_name or len(self.device_name) > 128:
            raise ValueError("device_name is required and bounded")
        if not re.fullmatch(r"[0-9a-f]{64}", self.package_sha256):
            raise ValueError("package_sha256 must be 64 lowercase hex characters")
        if not 1 <= self.heartbeat_seconds <= 300:
            raise ValueError("heartbeat_seconds must be between 1 and 300")
        if not 1 <= self.queue_size <= 64:
            raise ValueError("queue_size must be between 1 and 64")
        return self

    @property
    def package(self) -> dict[str, str]:
        return {
            "package_id": self.package_id,
            "version": self.package_version,
            "sha256": self.package_sha256,
        }
