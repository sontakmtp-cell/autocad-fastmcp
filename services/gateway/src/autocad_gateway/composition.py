"""Profile-specific composition roots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .app import GatewayConfig
from .backend import build_backend
from .durable_services import DurableGatewayServices
from .auth import build_phase4_auth
from .infrastructure.agent_transport.authenticator import (
    FixtureDeviceAuthenticator,
    LabDeviceAuthenticator,
    PairedDeviceAuthenticator,
)
from .infrastructure.agent_transport.connection_registry import ConnectionRegistry
from .infrastructure.sqlite.database import SqliteDatabase
from .services import GatewayServices
from .identity import Phase5IdentityService


def fixture_token_map(config: GatewayConfig) -> dict[str, str]:
    return dict(config.fixture_tokens)


def build_services(config: GatewayConfig) -> Any:
    if config.profile in {
        "phase3_poc",
        "phase4_c1",
        "phase5_identity",
        "phase6_program",
        "phase7_c2",
    }:
        tokens = fixture_token_map(config)
        phase4 = config.profile == "phase4_c1"
        managed_profile = config.profile in {
            "phase4_c1",
            "phase5_identity",
            "phase6_program",
            "phase7_c2",
        }
        services = DurableGatewayServices(
            SqliteDatabase(Path(config.db_path or "")),
            ConnectionRegistry(stale_after_seconds=config.stale_after_seconds),
            device_tokens=tokens,
            owner_subject=config.fixture_owner_subject,
            request_wait_timeout_seconds=config.effective_request_wait_timeout_seconds,
            job_deadline_seconds=config.job_deadline_seconds,
            profile=config.profile,
            agent_authenticator=(LabDeviceAuthenticator(tokens) if phase4 else None),
            required_package=config.required_package if managed_profile else None,
            display_name=config.device_display_name if managed_profile else None,
            program_enabled=config.program_v0_enabled,
            managed_write_enabled=config.managed_write_enabled,
            allowed_write_device_ids=config.phase6_allowed_device_ids,
            program_policy_version=config.phase6_policy_version,
            phase7_c2_enabled=config.phase7_c2_enabled,
            trusted_approval_enabled=config.trusted_approval_enabled,
            device_local_approval_enabled=config.device_local_approval_enabled,
            portal_recent_auth_approval_enabled=(
                config.portal_recent_auth_approval_enabled
            ),
            public_rollback_enabled=config.public_rollback_enabled,
            recovery_cases_enabled=config.recovery_cases_enabled,
            phase6_direct_commit_lab_enabled=(
                config.phase6_direct_commit_lab_enabled
            ),
        )
        if config.profile in {"phase5_identity", "phase6_program", "phase7_c2"}:
            services.identity = Phase5IdentityService(services.database, services.registry)
            services.agent_authenticator = PairedDeviceAuthenticator(services.identity)
        return services
    return GatewayServices(
        build_backend(),
        max_image_bytes=config.max_image_bytes,
        max_entities=config.max_entities,
        max_entity_detail_calls=config.max_entity_detail_calls,
        observation_timeout_seconds=config.observation_timeout_seconds,
        max_snapshot_bytes=config.max_snapshot_bytes,
        snapshot_ttl_seconds=config.snapshot_ttl_seconds,
        max_snapshot_count=config.max_snapshot_count,
        max_snapshot_store_bytes=config.max_snapshot_store_bytes,
    )


def build_agent_authenticator(config: GatewayConfig) -> FixtureDeviceAuthenticator | None:
    if config.profile == "phase4_c1":
        return LabDeviceAuthenticator(fixture_token_map(config))
    if config.profile != "phase3_poc":
        return None
    return FixtureDeviceAuthenticator(fixture_token_map(config))


def build_human_auth(config: GatewayConfig) -> Any | None:
    if config.profile not in {
        "phase4_c1",
        "phase5_identity",
        "phase6_program",
        "phase7_c2",
    }:
        return None
    return build_phase4_auth(
        issuer=config.oauth_issuer or "",
        audience=config.oauth_audience or "",
        jwks_uri=config.oauth_jwks_uri or "",
        public_origin=config.public_origin or "",
        include_device_manage=config.profile in {
            "phase5_identity",
            "phase6_program",
            "phase7_c2",
        },
        include_write=config.profile in {"phase6_program", "phase7_c2"},
    )
