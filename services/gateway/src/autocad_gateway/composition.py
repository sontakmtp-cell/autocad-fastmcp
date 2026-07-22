"""Profile-specific composition roots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .app import GatewayConfig
from .backend import build_backend
from .durable_services import DurableGatewayServices
from .infrastructure.agent_transport.authenticator import FixtureDeviceAuthenticator
from .infrastructure.agent_transport.connection_registry import ConnectionRegistry
from .infrastructure.sqlite.database import SqliteDatabase
from .services import GatewayServices


def fixture_token_map(config: GatewayConfig) -> dict[str, str]:
    return dict(config.fixture_tokens)


def build_services(config: GatewayConfig) -> Any:
    if config.profile == "phase3_poc":
        tokens = fixture_token_map(config)
        return DurableGatewayServices(
            SqliteDatabase(Path(config.db_path or "")),
            ConnectionRegistry(stale_after_seconds=config.stale_after_seconds),
            device_tokens=tokens,
            owner_subject=config.fixture_owner_subject,
            request_wait_timeout_seconds=config.effective_request_wait_timeout_seconds,
            job_deadline_seconds=config.job_deadline_seconds,
        )
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
    if config.profile != "phase3_poc":
        return None
    return FixtureDeviceAuthenticator(fixture_token_map(config))
