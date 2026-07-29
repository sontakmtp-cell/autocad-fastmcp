"""Fail-closed Phase 4 Agent configuration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit


class RuntimeMode(str, Enum):
    AUTO = "auto"
    MANAGED_DOTNET = "managed_dotnet"
    AUTOLISP_COMPAT = "autolisp_compat"
    EZDXF = "ezdxf"


class IdentityMode(str, Enum):
    LAB_CREDENTIAL = "lab_credential"
    BROWSER_PAIRING = "browser_pairing"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    if raw == "1":
        return True
    if raw == "0":
        return False
    raise ValueError(f"{name} must be 0 or 1")


def _env_device_ids(name: str) -> frozenset[str]:
    raw = os.environ.get(name, "")
    values = frozenset(item.strip() for item in raw.split(",") if item.strip())
    if any(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", item) is None
        for item in values
    ):
        raise ValueError(f"{name} contains a malformed device ID")
    return values


def _env_operation_packs(name: str) -> frozenset[str]:
    raw = os.environ.get(name, "")
    values = frozenset(item.strip() for item in raw.split(",") if item.strip())
    if any(
        re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
            r"|[A-Za-z0-9][A-Za-z0-9._-]{0,62}/"
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}",
            item,
        )
        is None
        for item in values
    ):
        raise ValueError(f"{name} contains a malformed operation pack")
    return values


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
    runtime_mode: RuntimeMode = RuntimeMode.AUTOLISP_COMPAT
    managed_host_enabled: bool = False
    allow_full_compat_fallback: bool = False
    lt_runtime_enabled: bool = True
    program_v0_enabled: bool = False
    managed_write_enabled: bool = False
    lt_write_enabled: bool = False
    write_lock_enabled: bool = False
    phase6_allowed_device_ids: frozenset[str] = frozenset()
    program_policy_version: str = ""
    identity_mode: IdentityMode = IdentityMode.LAB_CREDENTIAL
    gateway_http_url: str = ""
    portal_url: str = ""
    phase7_c2_enabled: bool = False
    trusted_approval_enabled: bool = False
    device_local_approval_enabled: bool = False
    program_v1_source_enabled: bool = False
    program_v1_create_pack_enabled: bool = False
    program_v1_transform_pack_enabled: bool = False
    program_v1_topology_pack_enabled: bool = False
    program_v1_delete_pack_enabled: bool = False
    checkpoint_v2_enabled: bool = False
    operation_pack_allowlist: frozenset[str] = frozenset()
    rollout_policy_epoch: int = 0

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
            runtime_mode=RuntimeMode(
                os.environ.get("AUTOCAD_MCP_RUNTIME_MODE", "autolisp_compat").strip()
            ),
            managed_host_enabled=_env_flag("AUTOCAD_MCP_MANAGED_HOST_ENABLED", False),
            allow_full_compat_fallback=_env_flag(
                "AUTOCAD_MCP_ALLOW_FULL_COMPAT_FALLBACK",
                False,
            ),
            lt_runtime_enabled=_env_flag("AUTOCAD_MCP_LT_RUNTIME_ENABLED", True),
            program_v0_enabled=_env_flag(
                "AUTOCAD_MCP_PROGRAM_V0_ENABLED",
                False,
            ),
            managed_write_enabled=_env_flag(
                "AUTOCAD_MCP_MANAGED_WRITE_ENABLED",
                False,
            ),
            lt_write_enabled=_env_flag("AUTOCAD_MCP_LT_WRITE_ENABLED", False),
            write_lock_enabled=_env_flag(
                "AUTOCAD_AGENT_WRITE_LOCK_ENABLED",
                False,
            ),
            phase6_allowed_device_ids=_env_device_ids(
                "AUTOCAD_MCP_PHASE6_ALLOWED_DEVICE_IDS"
            ),
            program_policy_version=os.environ.get(
                "AUTOCAD_MCP_PROGRAM_POLICY_VERSION",
                "",
            ).strip(),
            identity_mode=IdentityMode(
                os.environ.get(
                    "AUTOCAD_AGENT_IDENTITY_MODE",
                    "lab_credential",
                ).strip()
            ),
            gateway_http_url=os.environ.get(
                "AUTOCAD_AGENT_GATEWAY_HTTP_URL",
                "",
            ).strip(),
            portal_url=os.environ.get(
                "AUTOCAD_AGENT_PORTAL_URL",
                "",
            ).strip(),
            phase7_c2_enabled=_env_flag(
                "AUTOCAD_MCP_PHASE7_C2_ENABLED",
                False,
            ),
            trusted_approval_enabled=_env_flag(
                "AUTOCAD_MCP_TRUSTED_APPROVAL_ENABLED",
                False,
            ),
            device_local_approval_enabled=_env_flag(
                "AUTOCAD_MCP_DEVICE_LOCAL_APPROVAL_ENABLED",
                False,
            ),
            program_v1_source_enabled=_env_flag(
                "AUTOCAD_MCP_PROGRAM_V1_SOURCE_ENABLED",
                False,
            ),
            program_v1_create_pack_enabled=_env_flag(
                "AUTOCAD_MCP_PROGRAM_V1_CREATE_PACK_ENABLED",
                False,
            ),
            program_v1_transform_pack_enabled=_env_flag(
                "AUTOCAD_MCP_PROGRAM_V1_TRANSFORM_PACK_ENABLED",
                False,
            ),
            program_v1_topology_pack_enabled=_env_flag(
                "AUTOCAD_MCP_PROGRAM_V1_TOPOLOGY_PACK_ENABLED",
                False,
            ),
            program_v1_delete_pack_enabled=_env_flag(
                "AUTOCAD_MCP_PROGRAM_V1_DELETE_PACK_ENABLED",
                False,
            ),
            checkpoint_v2_enabled=_env_flag(
                "AUTOCAD_MCP_CHECKPOINT_V2_ENABLED",
                False,
            ),
            operation_pack_allowlist=_env_operation_packs(
                "AUTOCAD_MCP_OPERATION_PACK_ALLOWLIST"
            ),
            rollout_policy_epoch=int(
                os.environ.get("AUTOCAD_MCP_ROLLOUT_POLICY_EPOCH", "0")
            ),
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
        if self.device_id and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
            self.device_id,
        ):
            raise ValueError("device_id is malformed")
        if (
            self.identity_mode == IdentityMode.LAB_CREDENTIAL
            and not self.device_id
        ):
            raise ValueError("device_id is required for lab credential mode")
        if not self.device_name or len(self.device_name) > 128:
            raise ValueError("device_name is required and bounded")
        if not re.fullmatch(r"[0-9a-f]{64}", self.package_sha256):
            raise ValueError("package_sha256 must be 64 lowercase hex characters")
        if not 1 <= self.heartbeat_seconds <= 300:
            raise ValueError("heartbeat_seconds must be between 1 and 300")
        if not 1 <= self.queue_size <= 64:
            raise ValueError("queue_size must be between 1 and 64")
        if not isinstance(self.runtime_mode, RuntimeMode):
            raise ValueError("runtime_mode is invalid")
        if not isinstance(self.identity_mode, IdentityMode):
            raise ValueError("identity_mode is invalid")
        if self.identity_mode == IdentityMode.BROWSER_PAIRING:
            gateway = urlsplit(self.gateway_http_url)
            if gateway.scheme not in {"https", "http"} or not gateway.netloc:
                raise ValueError(
                    "browser pairing requires an absolute gateway_http_url"
                )
            if (
                gateway.scheme == "http"
                and gateway.hostname not in {"127.0.0.1", "localhost", "::1"}
            ):
                raise ValueError("non-local browser pairing requires HTTPS")
            if gateway.path not in {"", "/"} or gateway.query or gateway.fragment:
                raise ValueError("gateway_http_url must not contain a path")
            portal = urlsplit(self.portal_url or self.gateway_http_url)
            if portal.scheme not in {"https", "http"} or not portal.netloc:
                raise ValueError("browser pairing requires an absolute portal_url")
            if (
                portal.scheme == "http"
                and portal.hostname not in {"127.0.0.1", "localhost", "::1"}
            ):
                raise ValueError("non-local browser pairing Portal requires HTTPS")
            if portal.path not in {"", "/"} or portal.query or portal.fragment:
                raise ValueError("portal_url must not contain a path")
        if self.runtime_mode == RuntimeMode.MANAGED_DOTNET and not self.managed_host_enabled:
            raise ValueError("managed_dotnet runtime requires AUTOCAD_MCP_MANAGED_HOST_ENABLED=1")
        if self.runtime_mode == RuntimeMode.AUTOLISP_COMPAT and not self.lt_runtime_enabled:
            raise ValueError("autolisp_compat runtime requires AUTOCAD_MCP_LT_RUNTIME_ENABLED=1")
        if self.lt_write_enabled:
            raise ValueError("AutoCAD LT write is unavailable in Phase 6")
        if self.program_v1_topology_pack_enabled:
            raise ValueError("Phase 8 topology write packs are not available")
        if self.program_v1_delete_pack_enabled:
            raise ValueError("Phase 8 delete write packs are not available")
        if (
            self.program_v1_create_pack_enabled
            or self.program_v1_transform_pack_enabled
            or self.checkpoint_v2_enabled
        ) and not self.program_v1_source_enabled:
            raise ValueError("Phase 8 packs require CAD Program v1 source")
        if self.program_v1_transform_pack_enabled and not self.checkpoint_v2_enabled:
            raise ValueError("Phase 8 transform packs require checkpoint v2")
        if (
            self.program_v1_create_pack_enabled
            or self.program_v1_transform_pack_enabled
        ) and not self.operation_pack_allowlist:
            raise ValueError("Phase 8 packs require an explicit operation pack allowlist")
        if not 0 <= self.rollout_policy_epoch <= 9_223_372_036_854_775_807:
            raise ValueError("Phase 8 rollout policy epoch is invalid")
        if (
            self.program_v1_create_pack_enabled
            or self.program_v1_transform_pack_enabled
        ) and self.rollout_policy_epoch == 0:
            raise ValueError("Phase 8 packs require a rollout policy epoch")
        if self.managed_write_enabled and not (
            (self.program_v0_enabled or self.program_v1_source_enabled)
            and self.managed_host_enabled
        ):
            raise ValueError(
                "managed write requires an enabled CAD Program and Managed Host"
            )
        if self.managed_write_enabled and not self.program_policy_version:
            raise ValueError(
                "managed write requires AUTOCAD_MCP_PROGRAM_POLICY_VERSION"
            )
        return self

    @property
    def local_approval_enabled(self) -> bool:
        return (
            self.phase7_c2_enabled
            and self.trusted_approval_enabled
            and self.device_local_approval_enabled
            and self.identity_mode == IdentityMode.BROWSER_PAIRING
        )

    @property
    def package(self) -> dict[str, str]:
        return {
            "package_id": self.package_id,
            "version": self.package_version,
            "sha256": self.package_sha256,
        }
