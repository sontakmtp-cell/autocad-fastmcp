"""FastMCP public v1 facade and local-only outer ASGI application."""

from __future__ import annotations

import os
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from ipaddress import ip_address
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError, ValidationError as FastMCPValidationError
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server.auth import RemoteAuthProvider, require_scopes
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware
from fastmcp.tools.tool import ToolResult
from mcp.types import PromptMessage, ResourceLink, TextContent
from pydantic import Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route, WebSocketRoute

from .contracts import (
    CadListDevicesInput,
    CadListDevicesOutput,
    CadListDevicesOutputC1,
    CadGetJobInput,
    CadGetJobOutput,
    CadGetJobOutputC1,
    CadObserveInput,
    CadObserveInputDurable,
    CadObserveOutput,
    CadObserveOutputDurable,
    CadObserveOutputC1,
    CadQueryInput,
    CadQueryOutput,
    CadPrepareProgramInput,
    CadPrepareProgramOutput,
    CadPrepareProgramV1ConflictOutput,
    CadPrepareProgramV1Output,
    CadPrepareProgramV1RevisionRequest,
    CadPreviewInput,
    CadPreviewOutput,
    CadCommitInput,
    CadCommitOutput,
    CadCommitRollbackInput,
    CadCommitRollbackOutput,
    CadPreviewRollbackInput,
    CadPreviewRollbackOutput,
    CadValidateInput,
    CadValidateOutput,
    Phase7ConsentDecisionInput,
    Principal,
)
from .services import (
    MAX_ENTITIES_DEFAULT,
    MAX_ENTITIES_UPPER,
    MAX_ENTITY_DETAIL_CALLS_DEFAULT,
    MAX_ENTITY_DETAIL_CALLS_UPPER,
    MAX_IMAGE_BYTES_UPPER,
    MAX_SNAPSHOT_BYTES_DEFAULT,
    MAX_SNAPSHOT_BYTES_UPPER,
    MAX_SNAPSHOT_COUNT_DEFAULT,
    MAX_SNAPSHOT_COUNT_UPPER,
    MAX_SNAPSHOT_STORE_BYTES_DEFAULT,
    MAX_SNAPSHOT_STORE_BYTES_UPPER,
    OBSERVATION_TIMEOUT_SECONDS_DEFAULT,
    OBSERVATION_TIMEOUT_SECONDS_UPPER,
    SNAPSHOT_TTL_SECONDS_DEFAULT,
    SNAPSHOT_TTL_SECONDS_UPPER,
    GatewayError,
    GatewayServices,
    LOCAL_SUBJECT,
)
from .workflows.service import WorkflowServiceError


CorrelationIdFactory = Callable[[], str]
_correlation_id: ContextVar[str | None] = ContextVar("cad_gateway_correlation_id", default=None)
logger = logging.getLogger(__name__)
_SAFE_PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_JOB_STATES = frozenset(
    {
        "queued",
        "dispatched",
        "acknowledged",
        "running",
        "cancel_requested",
        "reconnect_pending",
        "outcome_unknown",
        "succeeded",
        "failed",
        "cancelled",
        "needs_attention",
    }
)


async def _async_value(value: Any) -> Any:
    """Adapt a bounded synchronous catalog read to the async facade."""
    return value


@dataclass(frozen=True)
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/mcp"
    stateless_http: bool = False
    allowed_hosts: tuple[str, ...] = ("127.0.0.1:*", "localhost:*", "[::1]:*")
    allowed_origins: tuple[str, ...] = ()
    max_image_bytes: int = 5 * 1024 * 1024
    max_entities: int = MAX_ENTITIES_DEFAULT
    max_entity_detail_calls: int = MAX_ENTITY_DETAIL_CALLS_DEFAULT
    observation_timeout_seconds: float = OBSERVATION_TIMEOUT_SECONDS_DEFAULT
    max_snapshot_bytes: int = MAX_SNAPSHOT_BYTES_DEFAULT
    snapshot_ttl_seconds: float = SNAPSHOT_TTL_SECONDS_DEFAULT
    max_snapshot_count: int = MAX_SNAPSHOT_COUNT_DEFAULT
    max_snapshot_store_bytes: int = MAX_SNAPSHOT_STORE_BYTES_DEFAULT
    profile: Literal[
        "local",
        "phase3_poc",
        "phase4_c1",
        "phase5_identity",
        "phase6_program",
        "phase7_c2",
        "phase8_program",
        "phase9_workflow",
    ] = "local"
    db_path: str | None = None
    fixture_tokens: tuple[tuple[str, str], ...] = ()
    fixture_owner_subject: str = "phase3-fixture-user"
    stale_after_seconds: int = 45
    request_wait_timeout_seconds: float = 30.0
    job_deadline_seconds: float = 300.0
    # Backward-compatible constructor alias used by the original Phase 3 tests.
    command_timeout_seconds: float | None = None
    oauth_issuer: str | None = None
    oauth_audience: str | None = None
    oauth_jwks_uri: str | None = None
    public_origin: str | None = None
    required_package_id: str | None = None
    required_package_version: str | None = None
    required_package_sha256: str | None = None
    write_disabled: bool = True
    device_display_name: str = "Máy AutoCAD Lab"
    program_v0_enabled: bool = False
    managed_write_enabled: bool = False
    lt_write_enabled: bool = False
    high_risk_enabled: bool = False
    phase6_allowed_device_ids: tuple[str, ...] = ()
    phase6_policy_version: str = "phase6-policy/1"
    phase7_c2_enabled: bool = False
    trusted_approval_enabled: bool = False
    device_local_approval_enabled: bool = False
    portal_recent_auth_approval_enabled: bool = False
    public_rollback_enabled: bool = False
    recovery_cases_enabled: bool = False
    phase6_direct_commit_lab_enabled: bool = False
    program_v1_source_enabled: bool = False
    program_v1_compiler_enabled: bool = False
    program_v1_create_pack_enabled: bool = False
    program_v1_transform_pack_enabled: bool = False
    program_v1_topology_pack_enabled: bool = False
    program_v1_delete_pack_enabled: bool = False
    checkpoint_v2_enabled: bool = False
    scoped_rollback_revalidation_enabled: bool = False
    lt_portable_write_enabled: bool = False
    operation_pack_allowlist: tuple[str, ...] = ()
    phase8_rollout_policy_digest: str | None = None
    phase8_rollout_policy_epoch: int = 0
    phase8_compiler_package_hash: str | None = None
    phase8_runtime_id: str = "managed_dotnet"
    phase8_host_family: str = "R25"
    phase8_host_version: str = "2025"
    phase8_package_id: str | None = None
    phase8_package_version: str | None = None
    phase8_package_hash: str | None = None
    phase8_capability_manifest_hash: str | None = None
    phase8_operation_registry_version: str = "cad.program/1.0-create-core"
    phase8_operation_registry_hash: str | None = None
    phase8_policy_version: str = "phase8-policy/1"
    phase9_skill_catalog_enabled: bool = False
    phase9_workflow_engine_enabled: bool = False
    phase9_public_workflow_tools_enabled: bool = False
    phase9_auto_dimension_skill_enabled: bool = False
    phase9_cleanup_audit_skill_enabled: bool = False
    phase9_plate_pattern_skill_enabled: bool = False
    phase9_write_workflows_enabled: bool = False
    phase9_skill_allowlist: tuple[str, ...] = ()
    phase9_policy_epoch: int = 0

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        allowed_hosts = tuple(
            item.strip()
            for item in os.environ.get(
                "AUTOCAD_MCP_PUBLIC_V1_ALLOWED_HOSTS", "127.0.0.1:*;localhost:*;[::1]:*"
            ).split(";")
            if item.strip()
        )
        allowed_origins = tuple(
            item.strip()
            for item in os.environ.get("AUTOCAD_MCP_PUBLIC_V1_ALLOWED_ORIGINS", "").split(";")
            if item.strip()
        )
        fixture_tokens = tuple(
            (parts[0].strip(), parts[1].strip())
            for item in os.environ.get("AUTOCAD_MCP_PHASE3_FIXTURE_TOKENS", "").split(";")
            if "=" in item
            for parts in [item.split("=", 1)]
            if parts[0].strip() and parts[1].strip()
        )
        profile = os.environ.get("AUTOCAD_MCP_GATEWAY_PROFILE", "local").strip() or "local"
        phase6_allowed_device_ids = tuple(
            item.strip()
            for item in re.split(
                r"[;,]",
                os.environ.get(
                    "AUTOCAD_MCP_PHASE6_ALLOWED_DEVICE_IDS", ""
                ),
            )
            if item.strip()
        )
        if profile == "phase4_c1":
            device_id = os.environ.get("AUTOCAD_MCP_PHASE4_DEVICE_ID", "").strip()
            device_credential = os.environ.get(
                "AUTOCAD_MCP_PHASE4_DEVICE_CREDENTIAL", ""
            ).strip()
            fixture_tokens = (
                ((device_id, device_credential),)
                if device_id and device_credential
                else ()
            )
        config = cls(
            host=os.environ.get("AUTOCAD_MCP_PUBLIC_V1_HOST", "127.0.0.1").strip(),
            port=int(os.environ.get("AUTOCAD_MCP_PUBLIC_V1_PORT", "8765")),
            path=os.environ.get("AUTOCAD_MCP_PUBLIC_V1_PATH", "/mcp").strip(),
            stateless_http=os.environ.get("AUTOCAD_MCP_PUBLIC_V1_STATELESS_HTTP", "0")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            max_image_bytes=int(
                os.environ.get("AUTOCAD_MCP_MAX_IMAGE_BYTES", str(5 * 1024 * 1024))
            ),
            max_entities=int(
                os.environ.get("AUTOCAD_MCP_MAX_OBSERVATION_ENTITIES", str(MAX_ENTITIES_DEFAULT))
            ),
            max_entity_detail_calls=int(
                os.environ.get(
                    "AUTOCAD_MCP_MAX_ENTITY_DETAIL_CALLS",
                    str(MAX_ENTITY_DETAIL_CALLS_DEFAULT),
                )
            ),
            observation_timeout_seconds=float(
                os.environ.get(
                    "AUTOCAD_MCP_OBSERVATION_TIMEOUT_SECONDS",
                    str(OBSERVATION_TIMEOUT_SECONDS_DEFAULT),
                )
            ),
            max_snapshot_bytes=int(
                os.environ.get("AUTOCAD_MCP_MAX_SNAPSHOT_BYTES", str(MAX_SNAPSHOT_BYTES_DEFAULT))
            ),
            snapshot_ttl_seconds=float(
                os.environ.get("AUTOCAD_MCP_SNAPSHOT_TTL_SECONDS", str(SNAPSHOT_TTL_SECONDS_DEFAULT))
            ),
            max_snapshot_count=int(
                os.environ.get("AUTOCAD_MCP_MAX_SNAPSHOT_COUNT", str(MAX_SNAPSHOT_COUNT_DEFAULT))
            ),
            max_snapshot_store_bytes=int(
                os.environ.get(
                    "AUTOCAD_MCP_MAX_SNAPSHOT_STORE_BYTES",
                    str(MAX_SNAPSHOT_STORE_BYTES_DEFAULT),
                )
            ),
            profile=profile,
            db_path=(
                os.environ.get(
                    (
                        "AUTOCAD_MCP_PHASE7_DB_PATH"
                        if profile in {"phase7_c2", "phase8_program", "phase9_workflow"}
                        else "AUTOCAD_MCP_PHASE6_DB_PATH"
                    ),
                    os.environ.get(
                        "AUTOCAD_MCP_PHASE6_DB_PATH",
                        os.environ.get(
                        "AUTOCAD_MCP_PHASE5_DB_PATH",
                        os.environ.get("AUTOCAD_MCP_PHASE4_DB_PATH", ""),
                        ),
                    ),
                ).strip()
                if profile in {"phase6_program", "phase7_c2", "phase8_program", "phase9_workflow"}
                else (
                    os.environ.get(
                        "AUTOCAD_MCP_PHASE5_DB_PATH",
                        os.environ.get("AUTOCAD_MCP_PHASE4_DB_PATH", ""),
                    ).strip()
                    if profile == "phase5_identity"
                    else (
                        os.environ.get("AUTOCAD_MCP_PHASE4_DB_PATH", "").strip()
                        if profile == "phase4_c1"
                        else os.environ.get(
                            "AUTOCAD_MCP_PHASE3_DB_PATH", ""
                        ).strip()
                    )
                )
            ) or None,
            fixture_tokens=fixture_tokens,
            fixture_owner_subject=os.environ.get(
                (
                    "AUTOCAD_MCP_PHASE4_OWNER_SUBJECT"
                    if profile == "phase4_c1"
                    else "AUTOCAD_MCP_PHASE3_OWNER"
                ),
                "phase3-fixture-user",
            ).strip(),
            stale_after_seconds=int(os.environ.get("AUTOCAD_MCP_PHASE3_STALE_SECONDS", "45")),
            request_wait_timeout_seconds=float(
                os.environ.get(
                    "AUTOCAD_MCP_PHASE3_REQUEST_WAIT_TIMEOUT_SECONDS",
                    os.environ.get("AUTOCAD_MCP_PHASE3_TIMEOUT_SECONDS", "30"),
                )
            ),
            job_deadline_seconds=float(
                os.environ.get("AUTOCAD_MCP_PHASE3_JOB_DEADLINE_SECONDS", "300")
            ),
            oauth_issuer=os.environ.get("AUTOCAD_MCP_PHASE4_OAUTH_ISSUER", "").strip() or None,
            oauth_audience=os.environ.get("AUTOCAD_MCP_PHASE4_OAUTH_AUDIENCE", "").strip() or None,
            oauth_jwks_uri=os.environ.get("AUTOCAD_MCP_PHASE4_OAUTH_JWKS_URI", "").strip() or None,
            public_origin=os.environ.get("AUTOCAD_MCP_PHASE4_PUBLIC_ORIGIN", "").strip() or None,
            required_package_id=os.environ.get(
                "AUTOCAD_MCP_PHASE4_PACKAGE_ID", "autocad.lisp.drawing_info"
            ).strip() or None,
            required_package_version=os.environ.get(
                "AUTOCAD_MCP_PHASE4_PACKAGE_VERSION", "3.3-c1"
            ).strip() or None,
            required_package_sha256=os.environ.get(
                "AUTOCAD_MCP_PHASE4_PACKAGE_SHA256", ""
            ).strip() or None,
            write_disabled=os.environ.get("AUTOCAD_MCP_PHASE4_WRITE_DISABLED", "1")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
            device_display_name=os.environ.get(
                "AUTOCAD_MCP_PHASE4_DEVICE_DISPLAY_NAME", "Máy AutoCAD Lab"
            ).strip(),
            program_v0_enabled=os.environ.get(
                "AUTOCAD_MCP_PROGRAM_V0_ENABLED", "0"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            managed_write_enabled=os.environ.get(
                "AUTOCAD_MCP_MANAGED_WRITE_ENABLED", "0"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            lt_write_enabled=os.environ.get(
                "AUTOCAD_MCP_LT_WRITE_ENABLED", "0"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            high_risk_enabled=os.environ.get(
                "AUTOCAD_MCP_HIGH_RISK_ENABLED", "0"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            phase6_allowed_device_ids=phase6_allowed_device_ids,
            phase6_policy_version=os.environ.get(
                "AUTOCAD_MCP_PHASE6_POLICY_VERSION", "phase6-policy/1"
            ).strip(),
            phase7_c2_enabled=os.environ.get(
                "AUTOCAD_MCP_PHASE7_C2_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            trusted_approval_enabled=os.environ.get(
                "AUTOCAD_MCP_TRUSTED_APPROVAL_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            device_local_approval_enabled=os.environ.get(
                "AUTOCAD_MCP_DEVICE_LOCAL_APPROVAL_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            portal_recent_auth_approval_enabled=os.environ.get(
                "AUTOCAD_MCP_PORTAL_RECENT_AUTH_APPROVAL_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            public_rollback_enabled=os.environ.get(
                "AUTOCAD_MCP_PUBLIC_ROLLBACK_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            recovery_cases_enabled=os.environ.get(
                "AUTOCAD_MCP_RECOVERY_CASES_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            phase6_direct_commit_lab_enabled=os.environ.get(
                "AUTOCAD_MCP_PHASE6_DIRECT_COMMIT_LAB_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            program_v1_source_enabled=os.environ.get(
                "AUTOCAD_MCP_PROGRAM_V1_SOURCE_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            program_v1_compiler_enabled=os.environ.get(
                "AUTOCAD_MCP_PROGRAM_V1_COMPILER_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            program_v1_create_pack_enabled=os.environ.get(
                "AUTOCAD_MCP_PROGRAM_V1_CREATE_PACK_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            program_v1_transform_pack_enabled=os.environ.get(
                "AUTOCAD_MCP_PROGRAM_V1_TRANSFORM_PACK_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            program_v1_topology_pack_enabled=os.environ.get(
                "AUTOCAD_MCP_PROGRAM_V1_TOPOLOGY_PACK_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            program_v1_delete_pack_enabled=os.environ.get(
                "AUTOCAD_MCP_PROGRAM_V1_DELETE_PACK_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            checkpoint_v2_enabled=os.environ.get(
                "AUTOCAD_MCP_CHECKPOINT_V2_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            scoped_rollback_revalidation_enabled=os.environ.get(
                "AUTOCAD_MCP_SCOPED_ROLLBACK_REVALIDATION_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            lt_portable_write_enabled=os.environ.get(
                "AUTOCAD_MCP_LT_PORTABLE_WRITE_ENABLED", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
            operation_pack_allowlist=tuple(
                item.strip()
                for item in os.environ.get(
                    "AUTOCAD_MCP_OPERATION_PACK_ALLOWLIST", ""
                ).split(",")
                if item.strip()
            ),
            phase8_rollout_policy_digest=os.environ.get(
                "AUTOCAD_MCP_PHASE8_ROLLOUT_POLICY_DIGEST", ""
            ).strip()
            or None,
            phase8_rollout_policy_epoch=int(
                os.environ.get("AUTOCAD_MCP_PHASE8_ROLLOUT_POLICY_EPOCH", "0")
            ),
            phase8_compiler_package_hash=os.environ.get(
                "AUTOCAD_MCP_PHASE8_COMPILER_PACKAGE_HASH", ""
            ).strip()
            or None,
            phase8_runtime_id=os.environ.get(
                "AUTOCAD_MCP_PHASE8_RUNTIME_ID", "managed_dotnet"
            ).strip(),
            phase8_host_family=os.environ.get(
                "AUTOCAD_MCP_PHASE8_HOST_FAMILY", "R25"
            ).strip(),
            phase8_host_version=os.environ.get(
                "AUTOCAD_MCP_PHASE8_HOST_VERSION", "2025"
            ).strip(),
            phase8_package_id=os.environ.get(
                "AUTOCAD_MCP_PHASE8_PACKAGE_ID", ""
            ).strip()
            or None,
            phase8_package_version=os.environ.get(
                "AUTOCAD_MCP_PHASE8_PACKAGE_VERSION", ""
            ).strip()
            or None,
            phase8_package_hash=os.environ.get(
                "AUTOCAD_MCP_PHASE8_PACKAGE_HASH", ""
            ).strip()
            or None,
            phase8_capability_manifest_hash=os.environ.get(
                "AUTOCAD_MCP_PHASE8_CAPABILITY_MANIFEST_HASH", ""
            ).strip()
            or None,
            phase8_operation_registry_version=os.environ.get(
                "AUTOCAD_MCP_PHASE8_OPERATION_REGISTRY_VERSION",
                "cad.program/1.0-create-core",
            ).strip(),
            phase8_operation_registry_hash=os.environ.get(
                "AUTOCAD_MCP_PHASE8_OPERATION_REGISTRY_HASH", ""
            ).strip()
            or None,
            phase8_policy_version=os.environ.get(
                "AUTOCAD_MCP_PHASE8_POLICY_VERSION", "phase8-policy/1"
            ).strip(),
            phase9_skill_catalog_enabled=os.environ.get("AUTOCAD_MCP_PHASE9_SKILL_CATALOG_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"},
            phase9_workflow_engine_enabled=os.environ.get("AUTOCAD_MCP_PHASE9_WORKFLOW_ENGINE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"},
            phase9_public_workflow_tools_enabled=os.environ.get("AUTOCAD_MCP_PHASE9_PUBLIC_WORKFLOW_TOOLS_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"},
            phase9_auto_dimension_skill_enabled=os.environ.get("AUTOCAD_MCP_PHASE9_AUTO_DIMENSION_SKILL_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"},
            phase9_cleanup_audit_skill_enabled=os.environ.get("AUTOCAD_MCP_PHASE9_CLEANUP_AUDIT_SKILL_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"},
            phase9_plate_pattern_skill_enabled=os.environ.get("AUTOCAD_MCP_PHASE9_PLATE_PATTERN_SKILL_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"},
            phase9_write_workflows_enabled=os.environ.get("AUTOCAD_MCP_PHASE9_WRITE_WORKFLOWS_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"},
            phase9_skill_allowlist=tuple(item.strip() for item in os.environ.get("AUTOCAD_MCP_PHASE9_SKILL_ALLOWLIST", "").split(",") if item.strip()),
            phase9_policy_epoch=int(os.environ.get("AUTOCAD_MCP_PHASE9_POLICY_EPOCH", "0")),
        )
        return config.validate()

    def validate(self) -> "GatewayConfig":
        try:
            is_loopback = ip_address(self.host).is_loopback
        except ValueError:
            is_loopback = self.host.lower() == "localhost"
        if not is_loopback:
            raise ValueError("Phase 2 no-auth Gateway must bind to loopback")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if not self.path.startswith("/") or any(char.isspace() for char in self.path):
            raise ValueError("path must start with '/' and contain no whitespace")
        _validate_limit("max_image_bytes", self.max_image_bytes, MAX_IMAGE_BYTES_UPPER)
        _validate_limit("max_entities", self.max_entities, MAX_ENTITIES_UPPER)
        _validate_limit(
            "max_entity_detail_calls",
            self.max_entity_detail_calls,
            MAX_ENTITY_DETAIL_CALLS_UPPER,
        )
        _validate_limit(
            "observation_timeout_seconds",
            self.observation_timeout_seconds,
            OBSERVATION_TIMEOUT_SECONDS_UPPER,
        )
        _validate_limit("max_snapshot_bytes", self.max_snapshot_bytes, MAX_SNAPSHOT_BYTES_UPPER)
        _validate_limit(
            "snapshot_ttl_seconds", self.snapshot_ttl_seconds, SNAPSHOT_TTL_SECONDS_UPPER
        )
        _validate_limit(
            "max_snapshot_count", self.max_snapshot_count, MAX_SNAPSHOT_COUNT_UPPER
        )
        _validate_limit(
            "max_snapshot_store_bytes",
            self.max_snapshot_store_bytes,
            MAX_SNAPSHOT_STORE_BYTES_UPPER,
        )
        if self.max_snapshot_bytes > self.max_snapshot_store_bytes:
            raise ValueError("max_snapshot_bytes must not exceed max_snapshot_store_bytes")
        if self.profile not in {
            "local",
            "phase3_poc",
            "phase4_c1",
            "phase5_identity",
            "phase6_program",
            "phase7_c2",
            "phase8_program",
            "phase9_workflow",
        }:
            raise ValueError(
                "profile must be local, phase3_poc, phase4_c1, phase5_identity, "
                "phase6_program, phase7_c2, phase8_program or phase9_workflow"
            )
        if not 1 <= self.stale_after_seconds <= 3600:
            raise ValueError("stale_after_seconds must be between 1 and 3600")
        if not 0 < self.effective_request_wait_timeout_seconds <= 600:
            raise ValueError("request_wait_timeout_seconds must be between 0 and 600")
        if not 1 <= self.job_deadline_seconds <= 86_400:
            raise ValueError("job_deadline_seconds must be between 1 and 86400")
        if self.profile == "phase3_poc":
            if not self.db_path:
                raise ValueError("phase3_poc requires an explicit db_path")
            if not self.fixture_tokens:
                raise ValueError("phase3_poc requires fixture device tokens")
            if not self.fixture_owner_subject:
                raise ValueError("phase3_poc requires a fixture owner subject")
        if self.profile == "phase4_c1":
            required = {
                "db_path": self.db_path,
                "lab device credential": self.fixture_tokens,
                "lab owner subject": self.fixture_owner_subject,
                "OAuth issuer": self.oauth_issuer,
                "OAuth audience": self.oauth_audience,
                "OAuth JWKS URI": self.oauth_jwks_uri,
                "public origin": self.public_origin,
                "package ID": self.required_package_id,
                "package version": self.required_package_version,
                "package SHA-256": self.required_package_sha256,
                "device display name": self.device_display_name,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError("phase4_c1 requires " + ", ".join(missing))
            if len(self.fixture_tokens) != 1:
                raise ValueError("phase4_c1 requires exactly one lab device")
            if not self.write_disabled:
                raise ValueError("phase4_c1 requires write_disabled=true")
            if not re.fullmatch(r"[0-9a-f]{64}", self.required_package_sha256 or ""):
                raise ValueError("phase4_c1 package SHA-256 must be 64 lowercase hex characters")
            for name, value in {
                "OAuth issuer": self.oauth_issuer,
                "OAuth JWKS URI": self.oauth_jwks_uri,
                "public origin": self.public_origin,
            }.items():
                parsed = urlsplit(value or "")
                if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
                    raise ValueError(f"phase4_c1 {name} must be a canonical HTTPS URL")
            if urlsplit(self.public_origin or "").path not in {"", "/"}:
                raise ValueError("phase4_c1 public origin must not contain a path")
        if self.profile in {
            "phase5_identity",
            "phase6_program",
            "phase7_c2",
            "phase8_program",
            "phase9_workflow",
        }:
            required = {
                "db_path": self.db_path,
                "OAuth issuer": self.oauth_issuer,
                "OAuth audience": self.oauth_audience,
                "OAuth JWKS URI": self.oauth_jwks_uri,
                "public origin": self.public_origin,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"{self.profile} requires " + ", ".join(missing))
            if self.fixture_tokens:
                raise ValueError(f"{self.profile} forbids fixture device tokens")
            if not self.write_disabled:
                raise ValueError(f"{self.profile} requires write_disabled=true")
            for name, value in {
                "OAuth issuer": self.oauth_issuer,
                "OAuth JWKS URI": self.oauth_jwks_uri,
                "public origin": self.public_origin,
            }.items():
                parsed = urlsplit(value or "")
                if (
                    parsed.scheme != "https"
                    or not parsed.netloc
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError(
                        f"{self.profile} {name} must be a canonical HTTPS URL"
                    )
            if urlsplit(self.public_origin or "").path not in {"", "/"}:
                raise ValueError(
                    f"{self.profile} public origin must not contain a path"
                )
        if self.lt_write_enabled:
            raise ValueError("Phase 6 forbids LT write")
        if self.high_risk_enabled:
            raise ValueError("Phase 6 forbids high-risk write")
        if self.managed_write_enabled and not self.program_v0_enabled:
            raise ValueError("managed_write requires program_v0")
        if self.managed_write_enabled and not self.phase6_allowed_device_ids:
            raise ValueError("managed_write requires an explicit Phase 6 device allowlist")
        if self.phase6_direct_commit_lab_enabled:
            if self.profile != "phase6_program":
                raise ValueError(
                    "Phase 6 direct commit lab mode is forbidden outside phase6_program"
                )
            if not self.managed_write_enabled or not self.phase6_allowed_device_ids:
                raise ValueError(
                    "Phase 6 direct commit lab mode requires managed write and allowlist"
                )
        if self.profile == "phase7_c2" and self.phase6_direct_commit_lab_enabled:
            raise ValueError("Phase 6 direct commit lab mode is forbidden in C2")
        if (
            self.device_local_approval_enabled
            or self.portal_recent_auth_approval_enabled
        ) and not self.trusted_approval_enabled:
            raise ValueError("approval presenter requires trusted approval")
        if (
            self.trusted_approval_enabled
            or self.public_rollback_enabled
            or self.recovery_cases_enabled
        ) and not self.phase7_c2_enabled:
            raise ValueError("Phase 7 feature requires the Phase 7 C2 master flag")
        if (
            not self.phase6_policy_version
            or len(self.phase6_policy_version.encode("utf-8")) > 64
        ):
            raise ValueError("phase6 policy version is invalid")
        if self.program_v1_compiler_enabled and not self.program_v1_source_enabled:
            raise ValueError("Program v1 compiler requires the source feature")
        if (
            self.program_v1_create_pack_enabled
            or self.program_v1_transform_pack_enabled
            or self.program_v1_topology_pack_enabled
            or self.program_v1_delete_pack_enabled
        ) and not self.program_v1_compiler_enabled:
            raise ValueError("Program v1 operation packs require the compiler feature")
        if self.program_v1_transform_pack_enabled and not self.checkpoint_v2_enabled:
            raise ValueError("Program v1 transform pack requires checkpoint v2")
        if self.program_v1_topology_pack_enabled or self.program_v1_delete_pack_enabled:
            raise ValueError("Phase 8 destructive extension gate is not available")
        if self.lt_portable_write_enabled:
            raise ValueError("Phase 8 LT write certification gate is not available")
        if (
            self.scoped_rollback_revalidation_enabled
            and not self.checkpoint_v2_enabled
        ):
            raise ValueError("scoped rollback revalidation requires checkpoint v2")
        if len(set(self.operation_pack_allowlist)) != len(
            self.operation_pack_allowlist
        ) or any(
            not item
            or len(item.encode("utf-8")) > 128
            or any(character.isspace() for character in item)
            for item in self.operation_pack_allowlist
        ):
            raise ValueError("Phase 8 operation pack allowlist is invalid")
        if self.program_v1_compiler_enabled and self.phase8_rollout_policy_epoch < 1:
            raise ValueError("Phase 8 compiler requires a rollout policy epoch")
        if self.phase8_rollout_policy_digest is not None:
            digest = self.phase8_rollout_policy_digest
            if (
                len(digest) != 71
                or not digest.startswith("sha256:")
                or any(character not in "0123456789abcdef" for character in digest[7:])
            ):
                raise ValueError("Phase 8 rollout policy digest is invalid")
        elif self.phase8_rollout_policy_epoch < 0:
            raise ValueError("Phase 8 rollout policy epoch is invalid")
        if self.profile in {"phase8_program", "phase9_workflow"}:
            if not self.phase7_c2_enabled:
                raise ValueError(f"{self.profile} requires the Phase 7 C2 master flag")
            if not self.program_v1_source_enabled or not self.program_v1_compiler_enabled:
                raise ValueError(f"{self.profile} requires Program v1 source and compiler")
            trusted = {
                "compiler package hash": self.phase8_compiler_package_hash,
                "runtime ID": self.phase8_runtime_id,
                "host family": self.phase8_host_family,
                "host version": self.phase8_host_version,
                "package ID": self.phase8_package_id,
                "package version": self.phase8_package_version,
                "package hash": self.phase8_package_hash,
                "capability manifest hash": self.phase8_capability_manifest_hash,
                "operation registry version": self.phase8_operation_registry_version,
                "operation registry hash": self.phase8_operation_registry_hash,
                "policy version": self.phase8_policy_version,
            }
            missing = [name for name, value in trusted.items() if not value]
            if missing:
                raise ValueError(
                    f"{self.profile} requires trusted " + ", ".join(missing)
                )
            for name in (
                "phase8_compiler_package_hash",
                "phase8_package_hash",
                "phase8_capability_manifest_hash",
                "phase8_operation_registry_hash",
            ):
                value = getattr(self, name)
                if (
                    not isinstance(value, str)
                    or len(value) != 71
                    or not value.startswith("sha256:")
                    or any(
                        character not in "0123456789abcdef"
                        for character in value[7:]
                    )
                ):
                    raise ValueError(f"{name} must be a canonical SHA-256 digest")
        if self.phase9_public_workflow_tools_enabled and not (
            self.profile == "phase9_workflow" and self.phase9_skill_catalog_enabled
            and self.phase9_workflow_engine_enabled
        ):
            raise ValueError("Phase 9 public workflow tools require the Phase 9 catalog and engine")
        if self.phase9_write_workflows_enabled and not (
            self.phase9_workflow_engine_enabled and self.managed_write_enabled
            and self.phase7_c2_enabled and self.trusted_approval_enabled
        ):
            raise ValueError("Phase 9 write workflows require existing managed approval gates")
        if self.phase9_policy_epoch < 0:
            raise ValueError("Phase 9 policy epoch is invalid")
        return self

    @property
    def required_package(self) -> dict[str, str]:
        if not (
            self.required_package_id
            and self.required_package_version
            and self.required_package_sha256
        ):
            return {}
        return {
            "package_id": self.required_package_id,
            "version": self.required_package_version,
            "sha256": self.required_package_sha256,
        }

    @property
    def effective_request_wait_timeout_seconds(self) -> float:
        if self.command_timeout_seconds is not None:
            return float(self.command_timeout_seconds)
        return float(self.request_wait_timeout_seconds)


def current_correlation_id(factory: CorrelationIdFactory | None = None) -> str:
    value = _correlation_id.get()
    if value:
        return value
    return (factory or (lambda: str(uuid.uuid4())))()


def _validate_limit(name: str, value: float, upper: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= upper:
        raise ValueError(f"{name} must be between 1 and {upper}")


def _parse_authority(
    value: str, *, allow_wildcard_port: bool
) -> tuple[str, int | str | None]:
    authority = value.strip()
    if not authority or any(character.isspace() for character in authority):
        raise ValueError("invalid authority")
    if any(character in authority for character in "/?#@"):
        raise ValueError("invalid authority")
    port_text: str | None = None
    if authority.startswith("["):
        closing = authority.find("]")
        if closing < 0:
            raise ValueError("invalid authority")
        host_text = authority[1:closing]
        remainder = authority[closing + 1 :]
        if remainder:
            if not remainder.startswith(":") or not remainder[1:]:
                raise ValueError("invalid authority")
            port_text = remainder[1:]
        host_name = str(ip_address(host_text)).lower()
    else:
        if authority.count(":") > 1:
            raise ValueError("IPv6 Host must be bracketed")
        if ":" in authority:
            host_text, port_text = authority.rsplit(":", 1)
        else:
            host_text = authority
        if not host_text or host_text.endswith("."):
            raise ValueError("invalid authority")
        try:
            host_name = str(ip_address(host_text)).lower()
        except ValueError:
            host_name = host_text.lower()
            if any(not (character.isalnum() or character in ".-") for character in host_name):
                raise ValueError("invalid authority")
    if port_text is None:
        port: int | str | None = None
    elif port_text == "*" and allow_wildcard_port:
        port = "*"
    elif port_text.isascii() and port_text.isdigit() and 1 <= int(port_text) <= 65535:
        port = int(port_text)
    else:
        raise ValueError("invalid authority")
    return host_name, port


def _origin_matches(origin: str, allowed: str) -> bool:
    try:
        return _canonical_origin(origin) == _canonical_origin(allowed)
    except ValueError:
        return False


def _canonical_origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid origin")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("invalid origin") from error
    default_port = 80 if parsed.scheme.lower() == "http" else 443
    return parsed.scheme.lower(), parsed.hostname.lower(), port or default_port


class CorrelationMiddleware:
    """Create and clean up one correlation ID for each HTTP request."""

    def __init__(self, app: Any, factory: CorrelationIdFactory | None = None) -> None:
        self.app = app
        self.factory = factory or (lambda: str(uuid.uuid4()))

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        token: Token[str | None] = _correlation_id.set(self.factory())
        try:
            await self.app(scope, receive, send)
        finally:
            _correlation_id.reset(token)


class CorrelationErrorMiddleware(Middleware):
    """Give in-memory MCP calls a correlation context and map schema errors safely."""

    def __init__(self, factory: CorrelationIdFactory) -> None:
        self.factory = factory

    async def on_call_tool(self, context: Any, call_next: Any) -> Any:
        return await self._run_with_correlation(context, call_next)

    async def on_read_resource(self, context: Any, call_next: Any) -> Any:
        return await self._run_with_correlation(context, call_next)

    async def _run_with_correlation(self, context: Any, call_next: Any) -> Any:
        token: Token[str | None] | None = None
        if _correlation_id.get() is None:
            token = _correlation_id.set(self.factory())
        correlation_id = current_correlation_id(self.factory)
        try:
            return await call_next(context)
        except FastMCPValidationError:
            raise ToolError(
                f"invalid_request: request is invalid; correlation_id={correlation_id}"
            ) from None
        finally:
            if token is not None:
                _correlation_id.reset(token)


class OuterHostOriginGuard:
    """Reject a bad Host/Origin before FastMCP can create a session."""

    def __init__(
        self,
        app: Any,
        allowed_hosts: list[str],
        allowed_origins: list[str],
        protected_path: str = "/mcp",
    ) -> None:
        self.app = app
        self.allowed_hosts = tuple(allowed_hosts)
        self.allowed_origins = tuple(allowed_origins)
        self.protected_path = protected_path.rstrip("/") or "/"

    @staticmethod
    def _host_matches(host: str, allowed: str) -> bool:
        if allowed == "*":
            return True
        try:
            host_name, host_port = _parse_authority(host, allow_wildcard_port=False)
            allowed_name, allowed_port = _parse_authority(
                allowed, allow_wildcard_port=True
            )
        except ValueError:
            return False
        return host_name == allowed_name and (
            allowed_port == "*" or allowed_port == host_port
        )

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "websocket" and scope["path"] == "/agent/ws":
            headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
            host = headers.get("host", "")
            origin = headers.get("origin")
            host_allowed = any(self._host_matches(host, item) for item in self.allowed_hosts)
            origin_allowed = origin is None or any(
                _origin_matches(origin, item) for item in self.allowed_origins
            )
            if not host_allowed or not origin_allowed:
                await send(
                    {
                        "type": "websocket.close",
                        "code": 4403,
                        "reason": "host or origin is not allowed",
                    }
                )
                return
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http" or not (
            scope["path"] == self.protected_path
            or scope["path"].startswith(self.protected_path + "/")
            or scope["path"].startswith("/.well-known/")
        ):
            await self.app(scope, receive, send)
            return
        headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
        host = headers.get("host", "")
        if not self.allowed_hosts or not any(
            self._host_matches(host, item) for item in self.allowed_hosts
        ):
            await PlainTextResponse("Host is not allowed", status_code=403)(
                scope, receive, send
            )
            return
        origin = headers.get("origin")
        if origin and (
            not self.allowed_origins
            or not any(_origin_matches(origin, allowed) for allowed in self.allowed_origins)
        ):
            await PlainTextResponse("Origin is not allowed", status_code=403)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)


def _tool_annotations(
    *,
    idempotent: bool,
    read_only: bool = True,
    destructive: bool = False,
) -> dict[str, bool]:
    return {
        "readOnlyHint": read_only,
        "idempotentHint": idempotent,
        "openWorldHint": False,
        "destructiveHint": destructive,
    }


def _principal(
    auth: RemoteAuthProvider | None,
    services: Any | None = None,
    correlation_id: str | None = None,
) -> Principal:
    correlation_id = correlation_id or current_correlation_id()
    token = get_access_token()
    if token is None:
        if auth is not None:
            raise ToolError(
                f"invalid_token: access token required; correlation_id={correlation_id}"
            )
        return Principal(
            subject=getattr(services, "owner_subject", LOCAL_SUBJECT),
            scopes=("autocad.read",),
        )
    subject = token.claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ToolError(f"invalid_token: subject claim required; correlation_id={correlation_id}")
    if getattr(services, "profile", None) in {
        "phase5_identity",
        "phase6_program",
        "phase7_c2",
        "phase8_program",
        "phase9_workflow",
    }:
        issuer = token.claims.get("iss")
        if not isinstance(issuer, str) or not issuer:
            raise ToolError(
                f"invalid_token: issuer claim required; correlation_id={correlation_id}"
            )
        from .identity import owner_key

        subject = owner_key(issuer, subject)
    return Principal(subject=subject, scopes=tuple(token.scopes))


def _safe_error(error: GatewayError, correlation_id: str) -> ToolError:
    messages = {
        "invalid_request": "request is invalid",
        "not_found": "requested resource was not found",
        "backend_error": "CAD backend operation failed",
        "response_too_large": "response exceeds the configured size limit",
        "observation_too_large": "the CAD observation exceeds configured limits",
        "observation_budget_exceeded": "the CAD observation exceeded its execution budget",
        "preview_unavailable": "a valid PNG preview is unavailable",
        "device_offline": "the selected CAD device is offline",
        "capability_missing": "the selected device lacks the requested capability",
        "job_in_progress": "the job is still in progress",
        "deadline_expired": "the job deadline has expired",
        "dispatcher_timeout": "the Agent did not finish the job in time",
        "idempotency_conflict": "the request conflicts with an existing job",
        "payload_mismatch": "the command payload does not match the existing command",
        "agent_rejected": "the Agent rejected the command",
        "active_document_changed": "the active AutoCAD document changed during the read",
        "autocad_busy": "AutoCAD is running another command",
        "autocad_not_running": "AutoCAD is not running",
        "command_routing_failed": "the Agent could not route the read command to AutoCAD",
        "dispatcher_not_loaded": "the required AutoLISP dispatcher is not loaded",
        "package_mismatch": "the Agent package does not match the required version",
        "ipc_result_invalid": "AutoCAD returned invalid bounded read evidence",
        "modal_dialog_active": "AutoCAD has a modal dialog open",
        "no_active_document": "AutoCAD has no active document",
        "paused_by_user": "the local user paused remote tasks",
        "outcome_unknown": "the write-like operation has an unknown outcome",
        "internal_error": "operation failed",
        "feature_disabled": "the Phase 6 CAD Program feature is disabled",
        "insufficient_scope": "autocad.write scope is required",
        "stale_snapshot": "the source snapshot is not commit-safe or is stale",
        "stale_revision": "the active document revision changed",
        "binding_mismatch": "the exact runtime or preview binding changed",
        "preview_expired": "the exact preview has expired",
        "document_write_busy": "another write-like job is active for this document",
        "write_lock_disabled": "the Desktop Agent write lock is disabled",
        "policy_mismatch": "the Gateway write policy changed",
        "runtime_mismatch": "the pinned Managed R25 runtime changed",
        "rollback_unavailable": "the receipt has no eligible Phase 7 checkpoint",
        "rollback_conflict": "the rollback plan contains conflicts",
        "rollback_plan_expired": "the rollback plan has expired",
        "rollback_plan_stale": "the rollback plan binding changed",
        "intent_denied": "the execution intent was denied",
        "intent_expired": "the execution intent expired",
        "intent_invalidated": "the execution intent binding was invalidated",
        "intent_cancelled": "the execution intent was cancelled",
        "consent_expired": "the trusted consent expired",
        "consent_not_approved": "trusted consent has not been approved",
        "approval_replay": "the trusted approval was already decided",
        "approval_binding_mismatch": "the trusted approval binding does not match",
        "approval_session_replaced": "the trusted device session was replaced",
        "approval_proof_invalid": "the trusted device proof is invalid",
        "device_identity_invalid": "the stable paired device identity is unavailable",
        "version_conflict": "the approval state changed; reload before retrying",
    }
    public_code = error.code if error.code in messages else "internal_error"
    details: list[str] = []
    if error.job_id and _SAFE_PUBLIC_ID.fullmatch(error.job_id):
        details.append(f"job_id={error.job_id}")
    if error.job_state in _SAFE_JOB_STATES:
        details.append(f"job_state={error.job_state}")
    details.append(f"correlation_id={correlation_id}")
    return ToolError(
        f"{public_code}: {messages[public_code]}; " + "; ".join(details)
    )


async def _run(call: Callable[[], Any], correlation_id: str) -> Any:
    try:
        return await call()
    except ToolError:
        raise
    except ValidationError:
        raise ToolError(
            f"invalid_request: request is invalid; correlation_id={correlation_id}"
        ) from None
    except GatewayError as error:
        logger.info(
            "Gateway operation rejected",
            extra={"correlation_id": correlation_id, "error_code": error.code},
        )
        raise _safe_error(error, correlation_id) from None
    except Exception:
        logger.exception(
            "Unexpected Gateway operation failure",
            extra={"correlation_id": correlation_id},
        )
        raise ToolError(
            f"internal_error: operation failed; correlation_id={correlation_id}"
        ) from None


def build_mcp_server(
    services: GatewayServices,
    auth: RemoteAuthProvider | None = None,
    *,
    correlation_id_factory: CorrelationIdFactory | None = None,
) -> FastMCP:
    """Build exactly the public v1 read surface."""

    make_correlation_id = correlation_id_factory or (lambda: str(uuid.uuid4()))
    auth_check = require_scopes("autocad.read") if auth is not None else None
    phase3 = bool(getattr(services, "is_phase3", False))
    phase4 = bool(getattr(services, "is_phase4", False))
    phase6 = bool(getattr(services, "is_phase6", False))
    phase7 = bool(getattr(services, "is_phase7", False))
    workflow_service = getattr(services, "workflow_service", None)
    phase9_catalog = bool(getattr(services, "is_phase9", False)) and bool(
        getattr(workflow_service, "catalog_enabled", False)
    )
    phase9 = phase9_catalog and bool(getattr(workflow_service, "enabled", False))
    write_auth_check = require_scopes("autocad.write") if auth is not None else None
    mcp = FastMCP(
        name=(
            "AutoCAD Gateway public v1.4"
            if phase7
            else "AutoCAD Gateway public v1.3"
            if phase6
            else "AutoCAD Gateway public v1.2"
            if phase4
            else "AutoCAD Gateway public v1.1"
            if phase3
            else "AutoCAD Gateway public v1"
        ),
        version=(
            "0.7.0"
            if phase7
            else "0.6.0"
            if phase6
            else "0.4.0"
            if phase4
            else "0.3.0"
            if phase3
            else "0.2.0"
        ),
        auth=auth,
        mask_error_details=True,
    )
    mcp.add_middleware(CorrelationErrorMiddleware(make_correlation_id))

    @mcp.tool(
        name="cad_list_devices",
        title="List CAD devices",
        description="List the bounded local CAD devices available for read-only observation.",
        output_schema=(
            CadListDevicesOutputC1.model_json_schema()
            if phase4
            else CadListDevicesOutput.model_json_schema()
        ),
        annotations=_tool_annotations(idempotent=True),
        auth=auth_check,
    )
    async def cad_list_devices(
        online_only: bool = False,
        capability: str | None = None,
        *,
        ctx: Context,
    ) -> dict[str, Any]:
        del ctx
        correlation_id = current_correlation_id(make_correlation_id)
        result = await _run(
            lambda: services.list_devices(
                CadListDevicesInput(online_only=online_only, capability=capability),
                _principal(auth, services, correlation_id),
                correlation_id,
            ),
            correlation_id,
        )
        return result.model_dump(mode="json")

    if phase9:
        async def _workflow_call(operation: Any) -> Any:
            try:
                return await operation()
            except WorkflowServiceError as error:
                raise GatewayError(str(error)) from error

        @mcp.tool(name="cad_list_skills", title="List first-party CAD skills",
                  description="List bounded first-party workflow skills; no skill is an MCP tool.",
                  annotations=_tool_annotations(idempotent=True), auth=auth_check)
        async def cad_list_skills(
            device_id: str | None = None,
            query: str | None = None,
            domain: str | None = None,
            tags: list[str] | None = None,
            required_support: str | None = None,
            cursor: int = 0,
            limit: int = 20,
            *,
            ctx: Context,
        ) -> dict[str, Any]:
            del ctx
            correlation_id = current_correlation_id(make_correlation_id)
            principal = _principal(auth, services, correlation_id)
            result = await _run(
                lambda: _workflow_call(
                    lambda: workflow_service.list_skills(
                        owner_subject=principal.subject,
                        device_id=device_id,
                        query=query,
                        domain=domain,
                        tags=tuple(tags or ()),
                        required_support=required_support,
                        cursor=cursor,
                        limit=limit,
                    )
                ),
                correlation_id,
            )
            return {
                "contract_version": "cad.mcp/1.6",
                "correlation_id": correlation_id,
                **result,
            }

        @mcp.tool(name="cad_start_workflow", title="Start a CAD workflow",
                  description="Start one owner-scoped, digest-pinned workflow run.",
                  annotations=_tool_annotations(idempotent=False, read_only=False), auth=auth_check)
        async def cad_start_workflow(
            skill_id: str,
            device_id: str,
            inputs: dict[str, Any],
            idempotency_key: str,
            skill_version: str | None = None,
            source_snapshot_id: str | None = None,
            *,
            ctx: Context,
        ) -> dict[str, Any]:
            del ctx
            correlation_id = current_correlation_id(make_correlation_id)
            principal = _principal(auth, services, correlation_id)
            return await _run(lambda: _workflow_call(lambda: workflow_service.start(
                owner_subject=principal.subject, actor_subject=principal.subject, skill_id=skill_id,
                version=skill_version, device_id=device_id,
                source_snapshot_id=source_snapshot_id, inputs=inputs,
                idempotency_key=idempotency_key,
                scopes=principal.scopes)), correlation_id)

        @mcp.tool(name="cad_get_workflow", title="Get a CAD workflow",
                  description="Read one owner-scoped workflow run and bounded timeline.",
                  annotations=_tool_annotations(idempotent=True), auth=auth_check)
        async def cad_get_workflow(
            run_id: str,
            event_cursor: int = 0,
            event_limit: int = 50,
            *,
            ctx: Context,
        ) -> dict[str, Any]:
            del ctx
            correlation_id = current_correlation_id(make_correlation_id)
            principal = _principal(auth, services, correlation_id)
            return await _run(lambda: _workflow_call(lambda: workflow_service.get(
                principal.subject, run_id, event_cursor=event_cursor,
                event_limit=event_limit,
            )), correlation_id)

        @mcp.tool(name="cad_control_workflow", title="Control a CAD workflow",
                  description="Submit bounded input, attach a revision, resume, retry a safe step or cancel. Approval is intentionally unavailable here.",
                  annotations=_tool_annotations(idempotent=False, read_only=False), auth=auth_check)
        async def cad_control_workflow(
            run_id: str,
            action: Literal[
                "submit_input",
                "attach_program_revision",
                "resume",
                "retry_safe_step",
                "cancel",
            ],
            expected_state_version: int,
            idempotency_key: str,
            payload: dict[str, Any] | None = None,
            *,
            ctx: Context,
        ) -> dict[str, Any]:
            del ctx
            correlation_id = current_correlation_id(make_correlation_id)
            principal = _principal(auth, services, correlation_id)
            return await _run(lambda: _workflow_call(lambda: workflow_service.control(
                owner_subject=principal.subject, run_id=run_id, action=action,
                expected_state_version=expected_state_version,
                idempotency_key=idempotency_key, payload=payload,
            )), correlation_id)

    async def _call_cad_observe(
        request: CadObserveInput | CadObserveInputDurable,
    ) -> ToolResult:
        correlation_id = current_correlation_id(make_correlation_id)
        result = await _run(
            lambda: services.observe(
                request,
                _principal(auth, services, correlation_id),
                correlation_id,
            ),
            correlation_id,
        )
        content: list[Any] = [
            TextContent(type="text", text="CAD observation ready."),
            ResourceLink(
                type="resource_link",
                name="snapshot-summary",
                title="Snapshot summary",
                uri=result.summary_uri,
                mimeType="application/json",
            ),
            ResourceLink(
                type="resource_link",
                name="snapshot-entities",
                title="Snapshot entities",
                uri=result.entities_uri,
                mimeType="application/json",
            ),
        ]
        content.extend(
            ResourceLink(
                type="resource_link",
                name="snapshot-artifact",
                title="Snapshot preview image",
                uri=artifact.uri,
                mimeType=artifact.mime_type,
            )
            for artifact in result.artifact_refs
        )
        return ToolResult(
            content=content,
            structured_content=result.model_dump(mode="json"),
        )

    if phase3:

        @mcp.tool(
            name="cad_observe",
            title="Observe a CAD device",
            description="Create a bounded read-only CAD snapshot with stable revision and resource references.",
            output_schema=(
                CadObserveOutputC1.model_json_schema()
                if phase4
                else CadObserveOutputDurable.model_json_schema()
            ),
            annotations=_tool_annotations(idempotent=False),
            auth=auth_check,
        )
        async def cad_observe_durable(
            device_id: str,
            observation_level: Literal["summary", "detail"] = "summary",
            include_preview_image: bool = False,
            idempotency_key: Annotated[
                str | None,
                Field(min_length=1, max_length=128),
            ] = None,
            *,
            ctx: Context,
        ) -> ToolResult:
            del ctx
            return await _call_cad_observe(
                CadObserveInputDurable(
                    device_id=device_id,
                    observation_level=observation_level,
                    include_preview_image=include_preview_image,
                    idempotency_key=idempotency_key,
                )
            )

    else:

        @mcp.tool(
            name="cad_observe",
            title="Observe a CAD device",
            description="Create a bounded read-only CAD snapshot with stable revision and resource references.",
            output_schema=CadObserveOutput.model_json_schema(),
            annotations=_tool_annotations(idempotent=False),
            auth=auth_check,
        )
        async def cad_observe_local(
            device_id: str,
            observation_level: Literal["summary", "detail"] = "summary",
            include_preview_image: bool = False,
            *,
            ctx: Context,
        ) -> ToolResult:
            del ctx
            return await _call_cad_observe(
                CadObserveInput(
                    device_id=device_id,
                    observation_level=observation_level,
                    include_preview_image=include_preview_image,
                )
            )

    @mcp.tool(
        name="cad_query",
        title="Query a CAD snapshot",
        description="Query a known CAD snapshot by entity type or layer with stable bounded pagination.",
        output_schema=CadQueryOutput.model_json_schema(),
        annotations=_tool_annotations(idempotent=True),
        auth=auth_check,
    )
    async def cad_query(
        snapshot_id: str,
        types: list[str] | None = None,
        layers: list[str] | None = None,
        cursor: str | None = None,
        limit: int = 50,
        *,
        ctx: Context,
    ) -> dict[str, Any]:
        del ctx
        correlation_id = current_correlation_id(make_correlation_id)
        result = await _run(
            lambda: services.query(
                CadQueryInput(
                    snapshot_id=snapshot_id,
                    types=types or [],
                    layers=layers or [],
                    cursor=cursor,
                    limit=limit,
                ),
                _principal(auth, services, correlation_id),
                correlation_id,
            ),
            correlation_id,
        )
        return result.model_dump(mode="json")

    if phase3:

        @mcp.tool(
            name="cad_get_job",
            title="Get a CAD job",
            description="Read the bounded state, progress and ordered events for an observation job.",
            output_schema=(
                CadGetJobOutputC1.model_json_schema()
                if phase4
                else CadGetJobOutput.model_json_schema()
            ),
            annotations=_tool_annotations(idempotent=True),
            auth=auth_check,
        )
        async def cad_get_job(
            job_id: str,
            event_cursor: str | None = None,
            event_limit: int = 50,
            *,
            ctx: Context,
        ) -> dict[str, Any]:
            del ctx
            correlation_id = current_correlation_id(make_correlation_id)
            result = await _run(
                lambda: services.get_job(
                    CadGetJobInput(
                        job_id=job_id,
                        event_cursor=event_cursor,
                        event_limit=event_limit,
                    ),
                    _principal(auth, services, correlation_id),
                    correlation_id,
                ),
                correlation_id,
            )
            return result.model_dump(mode="json")

    if phase6:
        program_service = services.program_service
        if program_service is None:
            raise RuntimeError("Phase 6 profile requires ProgramGatewayService")

        async def _prepare_program(
            device_id: str | None,
            source_snapshot_id: str | None,
            operations: list[dict[str, Any]] | None,
            postconditions: list[dict[str, Any]] | None,
            budget_overrides: dict[str, int] | None,
            idempotency_key: str | None,
            schema_version: str,
            program_v1_source: dict[str, Any] | None,
            program_v1_revision_request: CadPrepareProgramV1RevisionRequest | None,
        ) -> dict[str, Any]:
            correlation_id = current_correlation_id(make_correlation_id)
            public_request = None
            if program_v1_revision_request is not None:
                if any(
                    value is not None
                    for value in (
                        device_id,
                        source_snapshot_id,
                        operations,
                        postconditions,
                        budget_overrides,
                        idempotency_key,
                        program_v1_source,
                    )
                ):
                    raise ToolError(
                        "invalid_request: revision request cannot include root "
                        f"source fields; correlation_id={correlation_id}"
                    )
            else:
                public_request = CadPrepareProgramInput(
                    device_id=device_id,
                    source_snapshot_id=source_snapshot_id,
                    operations=operations,
                    postconditions=postconditions or [],
                    budget_overrides=budget_overrides or {},
                    idempotency_key=idempotency_key,
                )
            result = await _run(
                lambda: services.prepare_program(
                    public_request,
                    _principal(auth, services, correlation_id),
                    correlation_id,
                    schema_version=schema_version,
                    program_v1_source=program_v1_source,
                    program_v1_revision_request=(
                        program_v1_revision_request.model_dump(
                            mode="json", exclude_none=True
                        )
                        if program_v1_revision_request is not None
                        else None
                    ),
                ),
                correlation_id,
            )
            return result.model_dump(mode="json")

        prepare_metadata = {
            "name": "cad_prepare_program",
            "title": "Prepare a CAD Program",
            "description": (
                "Validate and store one owner-scoped create-only CAD Program without "
                "dispatching it to the Desktop Agent."
            ),
            "annotations": _tool_annotations(idempotent=False, read_only=False),
            "auth": write_auth_check,
        }
        if bool(getattr(services, "is_phase8", False)):

            @mcp.tool(
                **prepare_metadata,
                output_schema={
                    "type": "object",
                    "oneOf": [
                        CadPrepareProgramOutput.model_json_schema(),
                        CadPrepareProgramV1Output.model_json_schema(),
                        CadPrepareProgramV1ConflictOutput.model_json_schema(),
                    ],
                },
            )
            async def cad_prepare_program(
                device_id: str | None = None,
                source_snapshot_id: str | None = None,
                operations: list[dict[str, Any]] | None = None,
                postconditions: list[dict[str, Any]] | None = None,
                budget_overrides: dict[str, int] | None = None,
                idempotency_key: Annotated[
                    str | None, Field(min_length=1, max_length=128)
                ] = None,
                schema_version: Literal[
                    "cad.program/0.2", "cad.program/1.0"
                ] = "cad.program/0.2",
                program_v1_source: dict[str, Any] | None = None,
                program_v1_revision_request: (
                    CadPrepareProgramV1RevisionRequest | None
                ) = None,
                *,
                ctx: Context,
            ) -> dict[str, Any]:
                del ctx
                return await _prepare_program(
                    device_id,
                    source_snapshot_id,
                    operations,
                    postconditions,
                    budget_overrides,
                    idempotency_key,
                    schema_version,
                    program_v1_source,
                    program_v1_revision_request,
                )

        else:

            @mcp.tool(
                **prepare_metadata,
                output_schema=CadPrepareProgramOutput.model_json_schema(),
            )
            async def cad_prepare_program(
                device_id: str,
                source_snapshot_id: str,
                operations: list[dict[str, Any]],
                postconditions: list[dict[str, Any]] | None = None,
                budget_overrides: dict[str, int] | None = None,
                idempotency_key: Annotated[
                    str | None, Field(min_length=1, max_length=128)
                ] = None,
                *,
                ctx: Context,
            ) -> dict[str, Any]:
                del ctx
                return await _prepare_program(
                    device_id,
                    source_snapshot_id,
                    operations,
                    postconditions,
                    budget_overrides,
                    idempotency_key,
                    "cad.program/0.2",
                    None,
                    None,
                )

        @mcp.tool(
            name="cad_preview",
            title="Preview a CAD Program",
            description=(
                "Dispatch an exact pinned preview that must abort its AutoCAD "
                "transaction and leave the drawing unchanged."
            ),
            output_schema=CadPreviewOutput.model_json_schema(),
            annotations=_tool_annotations(idempotent=False, read_only=False),
            auth=write_auth_check,
        )
        async def cad_preview(
            program_id: str,
            program_revision: int = 1,
            idempotency_key: Annotated[
                str | None, Field(min_length=1, max_length=128)
            ] = None,
            *,
            ctx: Context,
        ) -> dict[str, Any]:
            del ctx
            correlation_id = current_correlation_id(make_correlation_id)
            result = await _run(
                lambda: services.preview_program(
                    CadPreviewInput(
                        program_id=program_id,
                        program_revision=program_revision,
                        idempotency_key=idempotency_key,
                    ),
                    _principal(auth, services, correlation_id),
                    correlation_id,
                ),
                correlation_id,
            )
            return result.model_dump(mode="json")

        @mcp.tool(
            name="cad_commit",
            title="Commit a CAD Program",
            description=(
                "Commit one unexpired exact preview on an explicitly allowlisted "
                "Managed .NET R25 device."
            ),
            output_schema=CadCommitOutput.model_json_schema(),
            annotations=_tool_annotations(idempotent=False, read_only=False),
            auth=write_auth_check,
        )
        async def cad_commit(
            preview_id: str,
            idempotency_key: Annotated[
                str | None, Field(min_length=1, max_length=128)
            ] = None,
            *,
            ctx: Context,
        ) -> dict[str, Any]:
            del ctx
            correlation_id = current_correlation_id(make_correlation_id)
            result = await _run(
                lambda: services.commit_program(
                    CadCommitInput(
                        preview_id=preview_id,
                        idempotency_key=idempotency_key,
                    ),
                    _principal(auth, services, correlation_id),
                    correlation_id,
                ),
                correlation_id,
            )
            return result.model_dump(mode="json")

        if phase7:
            phase7_admission = services.phase7_admission
            if phase7_admission is None:
                raise RuntimeError("Phase 7 profile requires admission service")

            @mcp.tool(
                name="cad_preview_rollback",
                title="Preview rollback from a Phase 7 checkpoint",
                description=(
                    "Create a bounded rollback plan from one owner-scoped Phase 7 "
                    "receipt or checkpoint. Raw entity handles are never accepted."
                ),
                output_schema=CadPreviewRollbackOutput.model_json_schema(),
                annotations=_tool_annotations(
                    idempotent=False, read_only=False, destructive=False
                ),
                auth=write_auth_check,
            )
            async def cad_preview_rollback(
                idempotency_key: Annotated[
                    str, Field(min_length=1, max_length=128)
                ],
                receipt_id: str | None = None,
                checkpoint_id: str | None = None,
                *,
                ctx: Context,
            ) -> dict[str, Any]:
                del ctx
                correlation_id = current_correlation_id(make_correlation_id)
                result = await _run(
                    lambda: phase7_admission.preview_rollback(
                        CadPreviewRollbackInput(
                            receipt_id=receipt_id,
                            checkpoint_id=checkpoint_id,
                            idempotency_key=idempotency_key,
                        ),
                        _principal(auth, services, correlation_id),
                        correlation_id,
                    ),
                    correlation_id,
                )
                return result.model_dump(mode="json")

            @mcp.tool(
                name="cad_commit_rollback",
                title="Commit an approved rollback plan",
                description=(
                    "Request execution of one exact eligible rollback plan. "
                    "Trusted approval is always enforced by the Gateway."
                ),
                output_schema=CadCommitRollbackOutput.model_json_schema(),
                annotations=_tool_annotations(
                    idempotent=False, read_only=False, destructive=True
                ),
                auth=write_auth_check,
            )
            async def cad_commit_rollback(
                rollback_plan_id: str,
                idempotency_key: Annotated[
                    str, Field(min_length=1, max_length=128)
                ],
                *,
                ctx: Context,
            ) -> dict[str, Any]:
                del ctx
                correlation_id = current_correlation_id(make_correlation_id)
                result = await _run(
                    lambda: phase7_admission.commit_rollback(
                        CadCommitRollbackInput(
                            rollback_plan_id=rollback_plan_id,
                            idempotency_key=idempotency_key,
                        ),
                        _principal(auth, services, correlation_id),
                        correlation_id,
                    ),
                    correlation_id,
                )
                return result.model_dump(mode="json")

        @mcp.tool(
            name="cad_validate",
            title="Validate a CAD Program receipt",
            description=(
                "Run bounded read-only validation against an owner-scoped durable "
                "execution receipt."
            ),
            output_schema=CadValidateOutput.model_json_schema(),
            annotations=_tool_annotations(
                idempotent=False,
                read_only=False,
            ),
            auth=write_auth_check,
        )
        async def cad_validate(
            receipt_id: str,
            idempotency_key: Annotated[
                str | None, Field(min_length=1, max_length=128)
            ] = None,
            *,
            ctx: Context,
        ) -> dict[str, Any]:
            del ctx
            correlation_id = current_correlation_id(make_correlation_id)
            result = await _run(
                lambda: program_service.validate(
                    CadValidateInput(
                        receipt_id=receipt_id,
                        idempotency_key=idempotency_key,
                    ),
                    _principal(auth, services, correlation_id),
                    correlation_id,
                ),
                correlation_id,
            )
            return result.model_dump(mode="json")

    @mcp.resource(
        "cad://devices/{device_id}/capabilities",
        name="CAD device capabilities",
        description="Read bounded capabilities for a known CAD device.",
        mime_type="application/json",
        auth=auth_check,
    )
    async def device_capabilities(device_id: str) -> ResourceResult:
        correlation_id = current_correlation_id(make_correlation_id)
        value = await _run(
            lambda: services.read_device_capabilities(
                device_id, _principal(auth, services, correlation_id)
            ),
            correlation_id,
        )
        return ResourceResult([ResourceContent(content=value, mime_type="application/json")])

    @mcp.resource(
        "cad://snapshots/{snapshot_id}/summary",
        name="CAD snapshot summary",
        description="Read the bounded JSON summary for a known CAD snapshot.",
        mime_type="application/json",
        auth=auth_check,
    )
    async def snapshot_summary(snapshot_id: str) -> ResourceResult:
        correlation_id = current_correlation_id(make_correlation_id)
        value = await _run(
            lambda: services.read_snapshot_summary(
                snapshot_id, _principal(auth, services, correlation_id)
            ),
            correlation_id,
        )
        return ResourceResult([ResourceContent(content=value, mime_type="application/json")])

    @mcp.resource(
        "cad://snapshots/{snapshot_id}/entities{?cursor,limit,types,layers}",
        name="CAD snapshot entities",
        description="Read a bounded, filtered page of entities from a known CAD snapshot.",
        mime_type="application/json",
        auth=auth_check,
    )
    async def snapshot_entities(
        snapshot_id: str,
        cursor: str | None = None,
        limit: int = 50,
        types: str | None = None,
        layers: str | None = None,
    ) -> ResourceResult:
        correlation_id = current_correlation_id(make_correlation_id)
        value = await _run(
            lambda: services.read_snapshot_entities(
                snapshot_id,
                _principal(auth, services, correlation_id),
                types=_split_query_values(types),
                layers=_split_query_values(layers),
                cursor=cursor,
                limit=limit,
                correlation_id=correlation_id,
            ),
            correlation_id,
        )
        return ResourceResult([ResourceContent(content=value, mime_type="application/json")])

    @mcp.resource(
        "cad://artifacts/{artifact_id}",
        name="CAD artifact",
        description="Read a bounded PNG preview artifact referenced by a CAD snapshot.",
        mime_type="image/png",
        auth=auth_check,
    )
    async def artifact(artifact_id: str) -> ResourceResult:
        correlation_id = current_correlation_id(make_correlation_id)
        value = await _run(
            lambda: services.read_artifact(
                artifact_id, _principal(auth, services, correlation_id)
            ),
            correlation_id,
        )
        return ResourceResult([ResourceContent(content=value, mime_type="image/png")])

    if phase3:

        @mcp.resource(
            "cad://jobs/{job_id}",
            name="CAD job",
            description="Read the bounded durable state and ordered events for a CAD job.",
            mime_type="application/json",
            auth=auth_check,
        )
        async def job_resource(job_id: str) -> ResourceResult:
            correlation_id = current_correlation_id(make_correlation_id)
            value = await _run(
                lambda: services.read_job_resource(
                    job_id, _principal(auth, services, correlation_id)
                ),
                correlation_id,
            )
            return ResourceResult([ResourceContent(content=value, mime_type="application/json")])

    if phase6:
        @mcp.resource(
            "cad://programs/{program_id}/revisions/{revision}",
            name="CAD Program revision",
            description="Read one bounded immutable owner-scoped CAD Program revision.",
            mime_type="application/json",
            auth=write_auth_check,
        )
        async def program_revision_resource(
            program_id: str, revision: int
        ) -> ResourceResult:
            correlation_id = current_correlation_id(make_correlation_id)
            principal = _principal(auth, services, correlation_id)
            value = await _run(
                lambda: (
                    services.read_program_resource(
                        principal.subject, program_id, revision
                    )
                    if bool(getattr(services, "is_phase8", False))
                    else program_service.read_program(
                        principal.subject, program_id, revision
                    )
                ),
                correlation_id,
            )
            return ResourceResult(
                [ResourceContent(content=value, mime_type="application/json")]
            )

        @mcp.resource(
            "cad://previews/{preview_id}",
            name="CAD Program preview",
            description="Read one bounded exact preview and its execution binding.",
            mime_type="application/json",
            auth=write_auth_check,
        )
        async def preview_resource(preview_id: str) -> ResourceResult:
            correlation_id = current_correlation_id(make_correlation_id)
            principal = _principal(auth, services, correlation_id)
            value = await _run(
                lambda: program_service.read_preview(
                    principal.subject, preview_id
                ),
                correlation_id,
            )
            return ResourceResult(
                [ResourceContent(content=value, mime_type="application/json")]
            )

        @mcp.resource(
            "cad://validations/{validation_id}",
            name="CAD Program validation",
            description="Read one bounded owner-scoped validation report.",
            mime_type="application/json",
            auth=write_auth_check,
        )
        async def validation_resource(validation_id: str) -> ResourceResult:
            correlation_id = current_correlation_id(make_correlation_id)
            principal = _principal(auth, services, correlation_id)
            value = await _run(
                lambda: program_service.read_validation(
                    principal.subject, validation_id
                ),
                correlation_id,
            )
            return ResourceResult(
                [ResourceContent(content=value, mime_type="application/json")]
            )

        @mcp.resource(
            "cad://receipts/{receipt_id}",
            name="CAD Program execution receipt",
            description="Read one bounded owner-scoped durable execution receipt.",
            mime_type="application/json",
            auth=write_auth_check,
        )
        async def receipt_resource(receipt_id: str) -> ResourceResult:
            correlation_id = current_correlation_id(make_correlation_id)
            principal = _principal(auth, services, correlation_id)
            value = await _run(
                lambda: program_service.read_receipt(
                    principal.subject, receipt_id
                ),
                correlation_id,
            )
            return ResourceResult(
                [ResourceContent(content=value, mime_type="application/json")]
            )

    if phase7:
        phase7_admission = services.phase7_admission

        async def _phase7_resource(
            reader: Callable[[str, str], Any], record_id: str
        ) -> ResourceResult:
            correlation_id = current_correlation_id(make_correlation_id)
            principal = _principal(auth, services, correlation_id)
            value = await _run(
                lambda: reader(principal.subject, record_id), correlation_id
            )
            return ResourceResult(
                [ResourceContent(content=value, mime_type="application/json")]
            )

        @mcp.resource(
            "cad://intents/{intent_id}",
            name="Phase 7 execution intent",
            description="Read one bounded owner-scoped execution intent.",
            mime_type="application/json",
            auth=auth_check,
        )
        async def intent_resource(intent_id: str) -> ResourceResult:
            return await _phase7_resource(phase7_admission.read_intent, intent_id)

        @mcp.resource(
            "cad://consents/{consent_id}",
            name="Phase 7 consent",
            description="Read one bounded owner-scoped consent.",
            mime_type="application/json",
            auth=auth_check,
        )
        async def consent_resource(consent_id: str) -> ResourceResult:
            return await _phase7_resource(phase7_admission.read_consent, consent_id)

        @mcp.resource(
            "cad://evidence/{job_id}",
            name="Phase 7 execution evidence",
            description="Read bounded append-only evidence for one owner job.",
            mime_type="application/json",
            auth=auth_check,
        )
        async def evidence_resource(job_id: str) -> ResourceResult:
            return await _phase7_resource(phase7_admission.read_evidence, job_id)

        @mcp.resource(
            "cad://recovery/{case_id}",
            name="Phase 7 recovery case",
            description="Read one bounded owner-scoped recovery case.",
            mime_type="application/json",
            auth=auth_check,
        )
        async def recovery_resource(case_id: str) -> ResourceResult:
            return await _phase7_resource(phase7_admission.read_recovery, case_id)

        @mcp.resource(
            "cad://checkpoints/{checkpoint_id}",
            name="Phase 7 rollback checkpoint",
            description="Read one bounded owner-scoped rollback checkpoint.",
            mime_type="application/json",
            auth=auth_check,
        )
        async def checkpoint_resource(checkpoint_id: str) -> ResourceResult:
            return await _phase7_resource(
                phase7_admission.read_checkpoint, checkpoint_id
            )

        @mcp.resource(
            "cad://rollbacks/{rollback_id}",
            name="Phase 7 rollback plan",
            description="Read one bounded owner-scoped rollback plan.",
            mime_type="application/json",
            auth=auth_check,
        )
        async def rollback_resource(rollback_id: str) -> ResourceResult:
            return await _phase7_resource(
                phase7_admission.read_rollback, rollback_id
            )

        @mcp.resource(
            "cad://rollback-receipts/{receipt_id}",
            name="Phase 7 rollback receipt",
            description="Read one bounded owner-scoped rollback receipt.",
            mime_type="application/json",
            auth=auth_check,
        )
        async def rollback_receipt_resource(receipt_id: str) -> ResourceResult:
            return await _phase7_resource(
                phase7_admission.read_rollback_receipt, receipt_id
            )

    if phase9_catalog:
        async def _phase9_resource_call(operation: Any) -> Any:
            try:
                return await operation()
            except WorkflowServiceError as error:
                raise GatewayError(str(error)) from error

        @mcp.resource(
            "cad://skills", name="CAD skill catalog",
            description="Read bounded first-party skill summaries.",
            mime_type="application/json", auth=auth_check,
        )
        async def skills_resource() -> ResourceResult:
            correlation_id = current_correlation_id(make_correlation_id)
            principal = _principal(auth, services, correlation_id)
            value = await _run(
                lambda: _phase9_resource_call(
                    lambda: workflow_service.list_skills(
                        owner_subject=principal.subject, limit=50
                    )
                ),
                correlation_id,
            )
            return ResourceResult([ResourceContent(
                content=json.dumps(value, sort_keys=True),
                mime_type="application/json",
            )])

        @mcp.resource(
            "cad://workflows/{run_id}", name="CAD workflow run",
            description="Read a bounded owner-scoped workflow run and timeline.",
            mime_type="application/json", auth=auth_check,
        )
        async def workflow_resource(run_id: str) -> ResourceResult:
            correlation_id = current_correlation_id(make_correlation_id)
            principal = _principal(auth, services, correlation_id)
            value = await _run(lambda: _phase9_resource_call(lambda: workflow_service.get(principal.subject, run_id)), correlation_id)
            return ResourceResult([ResourceContent(content=json.dumps(value, sort_keys=True), mime_type="application/json")])

        @mcp.resource(
            "cad://skills/{skill_id}/versions/{version}/guide", name="CAD skill guide",
            description="Read non-executable first-party skill guidance.", mime_type="text/markdown", auth=auth_check,
        )
        async def skill_guide_resource(skill_id: str, version: str) -> ResourceResult:
            correlation_id = current_correlation_id(make_correlation_id)
            value = await _run(lambda: _phase9_resource_call(lambda: _async_value(workflow_service.read_guide(skill_id, version))), correlation_id)
            return ResourceResult([ResourceContent(content=value, mime_type="text/markdown")])

        @mcp.resource(
            "cad://skills/{skill_id}/versions/{version}/manifest",
            name="CAD skill manifest",
            description="Read one immutable first-party skill manifest.",
            mime_type="application/json", auth=auth_check,
        )
        async def skill_manifest_resource(
            skill_id: str, version: str
        ) -> ResourceResult:
            correlation_id = current_correlation_id(make_correlation_id)
            value = await _run(
                lambda: _phase9_resource_call(
                    lambda: _async_value(
                        workflow_service.read_manifest(skill_id, version)
                    )
                ),
                correlation_id,
            )
            return ResourceResult([ResourceContent(
                content=value, mime_type="application/json"
            )])

        @mcp.resource(
            "cad://workflows/{run_id}/events",
            name="CAD workflow events",
            description="Read bounded ordered workflow events.",
            mime_type="application/json", auth=auth_check,
        )
        async def workflow_events_resource(run_id: str) -> ResourceResult:
            correlation_id = current_correlation_id(make_correlation_id)
            principal = _principal(auth, services, correlation_id)
            value = await _run(
                lambda: _phase9_resource_call(
                    lambda: workflow_service.get(principal.subject, run_id)
                ),
                correlation_id,
            )
            return ResourceResult([ResourceContent(
                content=json.dumps({"events": value["events"]}, sort_keys=True),
                mime_type="application/json",
            )])

        @mcp.resource(
            "cad://workflows/{run_id}/report",
            name="CAD workflow report",
            description="Read the bounded workflow result/report when available.",
            mime_type="application/json", auth=auth_check,
        )
        async def workflow_report_resource(run_id: str) -> ResourceResult:
            correlation_id = current_correlation_id(make_correlation_id)
            principal = _principal(auth, services, correlation_id)
            value = await _run(
                lambda: _phase9_resource_call(
                    lambda: workflow_service.get(principal.subject, run_id)
                ),
                correlation_id,
            )
            report = value["run"].get("result")
            if report is None:
                raise GatewayError("not_found")
            return ResourceResult([ResourceContent(
                content=json.dumps(report, sort_keys=True),
                mime_type="application/json",
            )])

    @mcp.prompt(
        name="plan_cad_change",
        title="Plan a CAD change",
        description="Guide a read-only inspection and planning conversation before any drawing change.",
    )
    async def plan_cad_change() -> list[PromptMessage]:
        return [
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        "Observe the selected CAD device, query the relevant snapshot entities, "
                        "and describe a proposed change. Stop before modifying the drawing."
                    ),
                ),
            )
        ]

    @mcp.prompt(
        name="repair_after_validation",
        title="Repair after validation",
        description="Guide read-only validation follow-up without changing the drawing.",
    )
    async def repair_after_validation() -> list[PromptMessage]:
        return [
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        "Observe again, query the affected entity type or layer, compare the "
                        "snapshot revision, and report what remains to be repaired. Do not edit."
                    ),
                ),
            )
        ]

    return mcp


def _split_query_values(value: str | None) -> list[str]:
    if value is None or value == "":
        return []
    return value.split(",")


def create_app(
    services: Any,
    auth: RemoteAuthProvider | None = None,
    *,
    config: GatewayConfig | None = None,
    stateless_http: bool | None = None,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    correlation_id_factory: CorrelationIdFactory | None = None,
) -> Starlette:
    config = (config or GatewayConfig.from_env()).validate()
    if config.profile in {
        "phase4_c1",
        "phase5_identity",
        "phase6_program",
        "phase7_c2",
        "phase8_program",
    } and auth is None:
        raise ValueError(f"{config.profile} requires OAuth authentication")
    if stateless_http is not None:
        config = replace(config, stateless_http=stateless_http)
    configured_hosts = allowed_hosts if allowed_hosts is not None else list(config.allowed_hosts)
    configured_origins = (
        allowed_origins if allowed_origins is not None else list(config.allowed_origins)
    )
    if auth is None:
        try:
            ip_address(config.host).is_loopback
        except ValueError:
            if config.host.lower() != "localhost":
                raise ValueError("no-auth public v1 must bind to loopback")
    mcp = build_mcp_server(
        services, auth, correlation_id_factory=correlation_id_factory
    )
    mcp_app = mcp.http_app(
        path=config.path,
        stateless_http=config.stateless_http,
        host_origin_protection=True,
        allowed_hosts=configured_hosts,
        allowed_origins=configured_origins,
    )

    async def healthz(request: Request) -> PlainTextResponse:
        del request
        return PlainTextResponse("ok")

    async def readyz(request: Request) -> PlainTextResponse:
        del request
        database = getattr(services, "database", None)
        if database is not None and not database.is_open:
            return PlainTextResponse("not ready", status_code=503)
        readiness = getattr(services, "is_ready", None)
        if callable(readiness) and not readiness():
            return PlainTextResponse("not ready", status_code=503)
        return PlainTextResponse("ready")

    async def agent_ws(websocket: Any) -> None:
        transport = getattr(services, "job_service", None)
        authenticator = getattr(services, "agent_authenticator", None)
        registry = getattr(services, "registry", None)
        if transport is None or authenticator is None or registry is None:
            await websocket.close(code=4404, reason="Agent transport is disabled")
            return
        from .infrastructure.agent_transport.websocket_endpoint import serve_agent_websocket

        await serve_agent_websocket(
            websocket,
            authenticator=authenticator,
            registry=registry,
            on_message=getattr(services, "on_agent_message", transport.handle_message),
            validate_message=getattr(
                services, "validate_agent_message", transport.validate_message
            ),
            on_connected=getattr(services, "on_agent_connected", transport.handle_connected),
            on_heartbeat=getattr(services, "on_agent_heartbeat", None),
            on_disconnected=getattr(
                services,
                "on_agent_disconnected",
                lambda connection: transport.handle_disconnect(connection.device_id),
            ),
        )

    async def identity_payload(request: Request, model: Any) -> Any:
        if int(request.headers.get("content-length", "0") or 0) > 16_384:
            raise ValueError("request too large")
        body = await request.body()
        if len(body) > 16_384:
            raise ValueError("request too large")
        return model.model_validate_json(body)

    class IdentityHttpAuthError(ValueError):
        def __init__(self, code: str, status_code: int) -> None:
            self.code = code
            self.status_code = status_code
            super().__init__(code)

    async def identity_principal(
        request: Request,
        *,
        required_scope: str = "autocad.device.manage",
    ) -> tuple[str, str, str]:
        authorization = request.headers.get("authorization", "")
        if not authorization.lower().startswith("bearer ") or auth is None:
            raise IdentityHttpAuthError("invalid_token", 401)
        token = await auth.verify_token(authorization[7:].strip())
        if token is None:
            raise IdentityHttpAuthError("invalid_token", 401)
        issuer = token.claims.get("iss")
        subject = token.claims.get("sub")
        if not isinstance(issuer, str) or not issuer or not isinstance(subject, str) or not subject:
            raise IdentityHttpAuthError("invalid_token", 401)
        if required_scope not in token.scopes:
            raise IdentityHttpAuthError("insufficient_scope", 403)
        from .identity import owner_key

        return issuer, subject, owner_key(issuer, subject)

    def identity_auth_error(error: IdentityHttpAuthError) -> JSONResponse:
        return JSONResponse({"error": error.code}, status_code=error.status_code)

    async def identity_response(operation: Any) -> JSONResponse:
        try:
            value = await operation
        except (ValueError, ValidationError) as error:
            code = getattr(error, "code", "invalid_request")
            status = (
                404
                if code == "not_found"
                else 401
                if code == "invalid_token"
                else 409
                if code == "device_already_paired"
                else 410
                if code == "credential_revoked"
                else 429
                if code == "rate_limited"
                else 400
            )
            return JSONResponse({"error": code}, status_code=status)
        return JSONResponse(value)

    anonymous_requests: dict[tuple[str, str], list[float]] = {}

    def allow_anonymous(request: Request, operation: str, limit: int) -> bool:
        now = time.monotonic()
        peer = request.client.host if request.client is not None else "unknown"
        client = peer
        try:
            peer_is_loopback = ip_address(peer).is_loopback
        except ValueError:
            peer_is_loopback = peer.lower() == "localhost"
        if peer_is_loopback:
            forwarded = request.headers.get("cf-connecting-ip", "").strip()
            if forwarded:
                try:
                    client = str(ip_address(forwarded))
                except ValueError:
                    return False

        def recent_for(key: tuple[str, str]) -> list[float]:
            return [
                recorded
                for recorded in anonymous_requests.get(key, ())
                if now - recorded < 60
            ]

        key = (operation, client)
        global_key = (operation, "*")
        recent = recent_for(key)
        global_recent = recent_for(global_key)
        global_limit = limit * 20
        if len(recent) >= limit or len(global_recent) >= global_limit:
            anonymous_requests[key] = recent
            anonymous_requests[global_key] = global_recent
            return False
        recent.append(now)
        global_recent.append(now)
        anonymous_requests[key] = recent
        anonymous_requests[global_key] = global_recent
        if len(anonymous_requests) > 2048:
            stale = [
                item
                for item, values in anonymous_requests.items()
                if not values or now - values[-1] >= 60
            ]
            for item in stale:
                anonymous_requests.pop(item, None)
        return True

    async def pairing_start(request: Request) -> JSONResponse:
        from autocad_contracts import PairingStartRequest

        if not allow_anonymous(request, "pairing_start", 10):
            return JSONResponse(
                {"error": "rate_limited"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        try:
            value = await identity_payload(request, PairingStartRequest)
        except (ValueError, ValidationError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return await identity_response(
            services.identity.start_pairing(
                device_id=value.device_id,
                display_name=value.display_name,
                public_key=value.public_key,
            )
        )

    async def pairing_approve(request: Request) -> JSONResponse:
        from autocad_contracts import PairingApproveRequest

        try:
            issuer, subject, _ = await identity_principal(request)
        except IdentityHttpAuthError as error:
            return identity_auth_error(error)
        try:
            value = await identity_payload(request, PairingApproveRequest)
        except (ValueError, ValidationError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return await identity_response(
            services.identity.approve_pairing(
                issuer=issuer, subject=subject, user_code=value.user_code
            )
        )

    async def pairing_complete(request: Request) -> JSONResponse:
        from autocad_contracts import PairingCompleteRequest

        try:
            value = await identity_payload(request, PairingCompleteRequest)
        except (ValueError, ValidationError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        pairing_id = request.path_params.get("pairing_id") or value.pairing_id
        if not pairing_id or (value.pairing_id is not None and value.pairing_id != pairing_id):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return await identity_response(
            services.identity.complete_pairing(
                pairing_id=pairing_id,
                challenge=value.challenge,
                signature=value.signature,
            )
        )

    async def pairing_status(request: Request) -> JSONResponse:
        polling_secret = request.headers.get("x-polling-secret", "")
        if not polling_secret:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return await identity_response(
            services.identity.pairing_status(
                pairing_id=request.path_params["pairing_id"],
                polling_secret=polling_secret,
            )
        )

    async def device_challenge(request: Request) -> JSONResponse:
        from autocad_contracts import DeviceChallengeRequest

        if not allow_anonymous(request, "device_challenge", 30):
            return JSONResponse(
                {"error": "rate_limited"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        try:
            value = await identity_payload(request, DeviceChallengeRequest)
        except (ValueError, ValidationError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return await identity_response(services.identity.create_challenge(value.device_id))

    async def device_token(request: Request) -> JSONResponse:
        from autocad_contracts import DeviceTokenRequest

        try:
            value = await identity_payload(request, DeviceTokenRequest)
        except (ValueError, ValidationError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return await identity_response(
            services.identity.exchange_challenge(
                device_id=value.device_id,
                challenge_id=value.challenge_id,
                challenge=value.challenge,
                signature=value.signature,
            )
        )

    async def device_revoke(request: Request) -> JSONResponse:
        from autocad_contracts import DeviceRevokeRequest

        try:
            _, _, user_id = await identity_principal(request)
        except IdentityHttpAuthError as error:
            return identity_auth_error(error)
        try:
            value = await identity_payload(request, DeviceRevokeRequest)
        except (ValueError, ValidationError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return await identity_response(
            _revoke_response(services.identity, user_id, value.device_id)
        )

    async def portal_devices(request: Request) -> JSONResponse:
        try:
            _, _, user_id = await identity_principal(request)
        except IdentityHttpAuthError as error:
            return identity_auth_error(error)
        return await identity_response(_portal_devices_response(services.identity, user_id))

    async def _portal_devices_response(identity: Any, user_id: str) -> dict[str, Any]:
        return {"devices": await identity.portal_devices(user_id)}

    async def portal_device(request: Request) -> JSONResponse:
        try:
            _, _, user_id = await identity_principal(request)
        except IdentityHttpAuthError as error:
            return identity_auth_error(error)
        return await identity_response(
            services.identity.portal_device(
                owner_user_id=user_id, device_id=request.path_params["device_id"]
            )
        )

    async def portal_pairing(request: Request) -> JSONResponse:
        try:
            _, _, user_id = await identity_principal(request)
        except IdentityHttpAuthError as error:
            return identity_auth_error(error)
        return await identity_response(
            services.identity.portal_pairing(
                owner_user_id=user_id, reference=request.path_params["reference"]
            )
        )

    async def portal_pairing_confirm(request: Request) -> JSONResponse:
        try:
            issuer, subject, _ = await identity_principal(request)
        except IdentityHttpAuthError as error:
            return identity_auth_error(error)
        return await identity_response(
            services.identity.confirm_pairing(
                issuer=issuer,
                subject=subject,
                reference=request.path_params["reference"],
            )
        )

    async def portal_pairing_deny(request: Request) -> JSONResponse:
        try:
            issuer, subject, _ = await identity_principal(request)
        except IdentityHttpAuthError as error:
            return identity_auth_error(error)
        return await identity_response(
            services.identity.deny_pairing(
                issuer=issuer,
                subject=subject,
                reference=request.path_params["reference"],
            )
        )

    async def portal_device_revoke(request: Request) -> JSONResponse:
        try:
            _, _, user_id = await identity_principal(request)
        except IdentityHttpAuthError as error:
            return identity_auth_error(error)
        return await identity_response(
            _revoke_response(
                services.identity, user_id, request.path_params["device_id"]
            )
        )

    async def _revoke_response(identity: Any, user_id: str, device_id: str) -> dict[str, str]:
        await identity.revoke(owner_user_id=user_id, device_id=device_id)
        return {"status": "revoked"}

    async def phase6_portal_owner(request: Request) -> str | JSONResponse:
        try:
            _, _, owner_subject = await identity_principal(
                request, required_scope="autocad.read"
            )
        except IdentityHttpAuthError as error:
            return identity_auth_error(error)
        return owner_subject

    def phase6_portal_response(value: dict[str, Any] | None) -> JSONResponse:
        if value is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > 256_000:
            return JSONResponse({"error": "response_too_large"}, status_code=413)
        return JSONResponse(value)

    async def portal_phase6_program(request: Request) -> JSONResponse:
        owner = await phase6_portal_owner(request)
        if isinstance(owner, JSONResponse):
            return owner
        value = await services.program_repository.get_program_revision(
            owner,
            request.path_params["program_id"],
            int(request.path_params["revision"]),
        )
        if value is not None:
            value = {
                key: value[key]
                for key in (
                    "program_id",
                    "program_revision",
                    "device_id",
                    "document_id",
                    "source_snapshot_id",
                    "expected_document_revision",
                    "schema_version",
                    "program_digest",
                    "risk_class",
                    "missing_capabilities",
                    "pins",
                    "created_at",
                )

            }
        return phase6_portal_response(value)

    async def portal_phase6_release_status(request: Request) -> JSONResponse:
        owner = await phase6_portal_owner(request)
        if isinstance(owner, JSONResponse):
            return owner
        return phase6_portal_response(
            {
                "program_v0_enabled": config.program_v0_enabled,
                "managed_write_enabled": config.managed_write_enabled,
                "kill_switch_active": not config.managed_write_enabled,
            }
        )

    async def portal_phase6_preview(request: Request) -> JSONResponse:
        owner = await phase6_portal_owner(request)
        if isinstance(owner, JSONResponse):
            return owner
        return phase6_portal_response(
            await services.program_repository.get_preview(
                owner, request.path_params["preview_id"]
            )
        )

    async def portal_phase6_validation(request: Request) -> JSONResponse:
        owner = await phase6_portal_owner(request)
        if isinstance(owner, JSONResponse):
            return owner
        return phase6_portal_response(
            await services.program_repository.get_validation(
                owner, request.path_params["validation_id"]
            )
        )

    async def portal_phase6_receipt(request: Request) -> JSONResponse:
        owner = await phase6_portal_owner(request)
        if isinstance(owner, JSONResponse):
            return owner
        return phase6_portal_response(
            await services.program_repository.get_receipt(
                owner, request.path_params["receipt_id"]
            )
        )

    async def portal_phase6_job(request: Request) -> JSONResponse:
        owner = await phase6_portal_owner(request)
        if isinstance(owner, JSONResponse):
            return owner
        job = await services.repository.get_job(
            owner, request.path_params["job_id"]
        )
        if job is not None:
            job = {
                key: job[key]
                for key in (
                    "job_id",
                    "device_id",
                    "kind",
                    "effect_class",
                    "state",
                    "progress",
                    "result",
                    "error_code",
                    "created_at",
                    "updated_at",
                )
            }
        return phase6_portal_response(job)

    async def phase7_portal_context(
        request: Request, *, required_scope: str
    ) -> tuple[str, str, str, dict[str, Any]] | JSONResponse:
        authorization = request.headers.get("authorization", "")
        if not authorization.lower().startswith("bearer ") or auth is None:
            return JSONResponse({"error": "invalid_token"}, status_code=401)
        token = await auth.verify_token(authorization[7:].strip())
        if token is None:
            return JSONResponse({"error": "invalid_token"}, status_code=401)
        issuer = token.claims.get("iss")
        subject = token.claims.get("sub")
        if (
            not isinstance(issuer, str)
            or not issuer
            or not isinstance(subject, str)
            or not subject
        ):
            return JSONResponse({"error": "invalid_token"}, status_code=401)
        if required_scope not in token.scopes:
            return JSONResponse({"error": "insufficient_scope"}, status_code=403)
        from .identity import owner_key

        return owner_key(issuer, subject), issuer, subject, dict(token.claims)

    def phase7_http_error(error: GatewayError) -> JSONResponse:
        status = (
            404
            if error.code == "not_found"
            else 401
            if error.code == "recent_auth_required"
            else 409
            if error.code
            in {
                "approval_replay",
                "approval_binding_mismatch",
                "version_conflict",
                "consent_expired",
                "intent_expired",
            }
            else 403
            if error.code == "feature_disabled"
            else 400
        )
        return JSONResponse({"error": error.code}, status_code=status)

    async def portal_phase7_intent(request: Request) -> JSONResponse:
        context = await phase7_portal_context(
            request, required_scope="autocad.read"
        )
        if isinstance(context, JSONResponse):
            return context
        owner, _, _, _ = context
        try:
            value = await services.phase7_admission.portal_intent(
                owner, request.path_params["intent_id"]
            )
        except GatewayError as error:
            return phase7_http_error(error)
        return phase6_portal_response(value)

    async def portal_phase7_consent(request: Request) -> JSONResponse:
        context = await phase7_portal_context(
            request, required_scope="autocad.read"
        )
        if isinstance(context, JSONResponse):
            return context
        owner, _, _, _ = context
        try:
            value = await services.phase7_admission.portal_consent(
                owner, request.path_params["consent_id"]
            )
        except GatewayError as error:
            return phase7_http_error(error)
        return phase6_portal_response(value)

    async def portal_phase7_decide(
        request: Request, decision: Literal["approved", "denied"]
    ) -> JSONResponse:
        context = await phase7_portal_context(
            request, required_scope="autocad.write"
        )
        if isinstance(context, JSONResponse):
            return context
        owner, issuer, subject, claims = context
        origin = request.headers.get("origin")
        if (
            not origin
            or not config.public_origin
            or not _origin_matches(origin, config.public_origin)
        ):
            return JSONResponse({"error": "origin_forbidden"}, status_code=403)
        try:
            body = await identity_payload(request, Phase7ConsentDecisionInput)
        except (ValueError, ValidationError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        expected_decision = "approve" if decision == "approved" else "deny"
        if body.decision != expected_decision:
            return JSONResponse(
                {"error": "approval_binding_mismatch"}, status_code=409
            )
        if request.headers.get("x-csrf-token") != body.challenge_nonce:
            return JSONResponse({"error": "csrf_failed"}, status_code=403)
        try:
            value = await services.phase7_admission.portal_decide(
                owner_subject=owner,
                consent_id=request.path_params["consent_id"],
                decision=decision,
                intent_digest=body.intent_digest,
                consent_version=body.consent_version,
                nonce=body.challenge_nonce,
                actor_issuer=issuer,
                actor_subject=subject,
                auth_time=claims.get("auth_time"),
            )
        except GatewayError as error:
            return phase7_http_error(error)
        consent = value.get("consent")
        intent = value.get("intent")
        if not isinstance(consent, dict) or not isinstance(intent, dict):
            return JSONResponse({"error": "invalid_response"}, status_code=502)
        return phase6_portal_response(
            {
                "status": decision,
                "consent_id": consent["consent_id"],
                "consent_version": consent["consent_version"],
                "intent_id": intent["intent_id"],
            }
        )

    async def portal_phase7_approve(request: Request) -> JSONResponse:
        return await portal_phase7_decide(request, "approved")

    async def portal_phase7_deny(request: Request) -> JSONResponse:
        return await portal_phase7_decide(request, "denied")

    async def portal_phase9_workflows(request: Request) -> JSONResponse:
        context = await phase7_portal_context(request, required_scope="autocad.read")
        if isinstance(context, JSONResponse):
            return context
        owner, _, _, _ = context
        try:
            value = await services.workflow_service.list_runs(owner, limit=100)
        except WorkflowServiceError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return phase6_portal_response(value)

    async def portal_phase9_workflow(request: Request) -> JSONResponse:
        context = await phase7_portal_context(request, required_scope="autocad.read")
        if isinstance(context, JSONResponse):
            return context
        owner, _, _, _ = context
        try:
            value = await services.workflow_service.get(owner, request.path_params["run_id"])
        except WorkflowServiceError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return phase6_portal_response(value)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        await services.initialize()
        try:
            async with mcp_app.lifespan(app):
                yield
        finally:
            shutdown = getattr(services, "shutdown", None)
            if shutdown is not None:
                await shutdown()

    routes: list[Any] = [
            Route("/healthz", healthz, methods=["GET"]),
            Route("/readyz", readyz, methods=["GET"]),
            WebSocketRoute("/agent/ws", agent_ws),
    ]
    if config.profile in {
        "phase5_identity",
        "phase6_program",
        "phase7_c2",
        "phase8_program",
        "phase9_workflow",
    }:
        routes.extend(
            [
                Route("/api/agent/v1/enrollments", pairing_start, methods=["POST"]),
                Route(
                    "/api/agent/v1/enrollments/{pairing_id:str}",
                    pairing_status,
                    methods=["GET"],
                ),
                Route(
                    "/api/agent/v1/enrollments/{pairing_id:str}/complete",
                    pairing_complete,
                    methods=["POST"],
                ),
                Route(
                    "/api/agent/v1/session-challenges",
                    device_challenge,
                    methods=["POST"],
                ),
                Route("/api/agent/v1/session-tokens", device_token, methods=["POST"]),
                Route(
                    "/api/portal/v1/pairings/approve",
                    pairing_approve,
                    methods=["POST"],
                ),
                Route("/api/portal/v1/devices", portal_devices, methods=["GET"]),
                Route(
                    "/api/portal/v1/devices/{device_id:str}",
                    portal_device,
                    methods=["GET"],
                ),
                Route(
                    "/api/portal/v1/devices/{device_id:str}/revoke",
                    portal_device_revoke,
                    methods=["POST"],
                ),
                Route(
                    "/api/portal/v1/pairings/{reference:str}",
                    portal_pairing,
                    methods=["GET"],
                ),
                Route(
                    "/api/portal/v1/pairings/{reference:str}/confirm",
                    portal_pairing_confirm,
                    methods=["POST"],
                ),
                Route(
                    "/api/portal/v1/pairings/{reference:str}/deny",
                    portal_pairing_deny,
                    methods=["POST"],
                ),
                Route(
                    "/api/portal/v1/devices/revoke",
                    device_revoke,
                    methods=["POST"],
                ),
                Route("/identity/pairing/start", pairing_start, methods=["POST"]),
                Route("/identity/pairing/approve", pairing_approve, methods=["POST"]),
                Route("/identity/pairing/complete", pairing_complete, methods=["POST"]),
                Route("/identity/device/challenge", device_challenge, methods=["POST"]),
                Route("/identity/device/token", device_token, methods=["POST"]),
                Route("/identity/device/revoke", device_revoke, methods=["POST"]),
            ]
        )
    if config.profile in {"phase6_program", "phase7_c2", "phase8_program", "phase9_workflow"}:
        routes.extend(
            [
                Route(
                    "/api/portal/v1/phase6/status",
                    portal_phase6_release_status,
                    methods=["GET"],
                ),
                Route(
                    "/api/portal/v1/programs/{program_id:str}/revisions/{revision:int}",
                    portal_phase6_program,
                    methods=["GET"],
                ),
                Route(
                    "/api/portal/v1/previews/{preview_id:str}",
                    portal_phase6_preview,
                    methods=["GET"],
                ),
                Route(
                    "/api/portal/v1/validations/{validation_id:str}",
                    portal_phase6_validation,
                    methods=["GET"],
                ),
                Route(
                    "/api/portal/v1/receipts/{receipt_id:str}",
                    portal_phase6_receipt,
                    methods=["GET"],
                ),
                Route(
                    "/api/portal/v1/jobs/{job_id:str}",
                    portal_phase6_job,
                    methods=["GET"],
                ),
            ]
        )
    if config.profile in {"phase7_c2", "phase8_program", "phase9_workflow"}:
        routes.extend(
            [
                Route(
                    "/api/portal/v1/intents/{intent_id:str}",
                    portal_phase7_intent,
                    methods=["GET"],
                ),
                Route(
                    "/api/portal/v1/consents/{consent_id:str}",
                    portal_phase7_consent,
                    methods=["GET"],
                ),
                Route(
                    "/api/portal/v1/consents/{consent_id:str}/approve",
                    portal_phase7_approve,
                    methods=["POST"],
                ),
                Route(
                    "/api/portal/v1/consents/{consent_id:str}/deny",
                    portal_phase7_deny,
                    methods=["POST"],
                ),
            ]
        )
    if config.profile == "phase9_workflow" and config.phase9_skill_catalog_enabled:
        routes.extend([
            Route("/api/portal/v1/workflows", portal_phase9_workflows, methods=["GET"]),
            Route("/api/portal/v1/workflows/{run_id:str}", portal_phase9_workflow, methods=["GET"]),
        ])
    routes.append(Mount("/", app=mcp_app))
    outer_app: Any = Starlette(
        routes=routes,
        lifespan=lifespan,
    )
    outer_app = OuterHostOriginGuard(
        outer_app, configured_hosts, configured_origins, protected_path=config.path
    )
    return CorrelationMiddleware(outer_app, correlation_id_factory)
