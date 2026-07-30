from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from autocad_gateway.durable_services import DurableGatewayServices
from autocad_gateway.infrastructure.agent_transport.connection_registry import (
    ConnectionRegistry,
)
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.scenes.repository import SceneRepository, SceneRepositoryConflict


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _scene(*, scene_digest: str | None = None) -> tuple[dict, dict]:
    sections = {
        "nodes": [{"node_id": "node-a"}],
        "relations": [],
        "contours": [],
        "features": [],
        "issues": [],
        "evidence": [{"source_entity_id": "A"}],
    }
    root = {
        "scene_id": "",
        "device_id": "device-a",
        "source_snapshot_id": "snapshot-a",
        "document_id": "drawing-a",
        "document_revision": "revision-a",
        "space": "model",
        "projection_version": "cad.entity-projection/2",
        "engine_version": "scene-engine/1.0.0",
        "profile_id": "mechanical-2d/1",
        "source_digest": _digest({"snapshot": "snapshot-a"}),
        "scene_digest": scene_digest or _digest(sections),
        "complete": True,
        "counts": {name: len(items) for name, items in sections.items()},
        "warnings": [],
        "capabilities": ["scene.core/1"],
    }
    return root, sections


@pytest.fixture
async def repo(tmp_path):
    database = SqliteDatabase(tmp_path / "scene.sqlite")
    await database.open()
    yield SceneRepository(database)
    await database.close()


@pytest.mark.asyncio
async def test_scene_is_owner_scoped_immutable_and_restart_safe(repo):
    root, sections = _scene()
    base = datetime.now(timezone.utc)
    first_expiry = (base + timedelta(days=10)).isoformat()
    replay_expiry = (base + timedelta(days=11)).isoformat()
    reuse_expiry = (base + timedelta(days=12)).isoformat()
    first, replayed = await repo.create(
        owner_subject="alice",
        root=root,
        sections=sections,
        request_hash=_digest({"request": 1}),
        idempotency_key="build-a",
        tolerance_digest=_digest({"profile": "mechanical-2d/1"}),
        build_options_digest=_digest({"sections": sorted(sections)}),
        section_digests={name: _digest(items) for name, items in sections.items()},
        correlation_id="correlation-a",
        expires_at=first_expiry,
    )
    assert not replayed
    assert first["scene_id"].startswith("scn_")
    assert await repo.get("bob", first["scene_id"]) is None
    assert await repo.get_section("bob", first["scene_id"], "nodes") is None
    assert await repo.get_section("alice", first["scene_id"], "nodes") == sections["nodes"]

    duplicate, replayed = await repo.create(
        owner_subject="alice",
        root=root,
        sections=sections,
        request_hash=_digest({"request": 1}),
        idempotency_key="build-a",
        tolerance_digest=_digest({"profile": "mechanical-2d/1"}),
        build_options_digest=_digest({"sections": sorted(sections)}),
        section_digests={name: _digest(items) for name, items in sections.items()},
        correlation_id="correlation-b",
        expires_at=replay_expiry,
    )
    assert replayed and duplicate["scene_id"] == first["scene_id"]
    assert duplicate["expires_at"] == replay_expiry
    assert (await repo.list("alice"))[0]["scene_id"] == first["scene_id"]
    canonical_reuse, replayed = await repo.create(
        owner_subject="alice",
        root=root,
        sections=sections,
        request_hash=_digest({"request": 1}),
        idempotency_key="different-key",
        tolerance_digest=_digest({"profile": "mechanical-2d/1"}),
        build_options_digest=_digest({"sections": sorted(sections)}),
        section_digests={name: _digest(items) for name, items in sections.items()},
        correlation_id="correlation-c",
        expires_at=reuse_expiry,
    )
    assert replayed and canonical_reuse["scene_id"] == first["scene_id"]
    assert canonical_reuse["expires_at"] == reuse_expiry
    with repo.database.read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM scene_request_bindings"
        ).fetchone()[0] == 2
    with pytest.raises(SceneRepositoryConflict, match="idempotency_conflict"):
        await repo.create(
            owner_subject="alice",
            root=root,
            sections=sections,
            request_hash=_digest({"request": 1}),
            idempotency_key="build-a",
            tolerance_digest=_digest({"profile": "mechanical-2d/1"}),
            build_options_digest=_digest({"sections": ["changed"]}),
            section_digests={
                name: _digest(items) for name, items in sections.items()
            },
            correlation_id="correlation-d",
            expires_at=first_expiry,
        )

    with pytest.raises(Exception, match="scene_records_immutable"):
        with repo.database.transaction() as connection:
            connection.execute(
                "UPDATE scene_records SET complete=0 WHERE scene_id=?",
                (first["scene_id"],),
            )


@pytest.mark.asyncio
async def test_conflict_expiry_and_no_orphan_sections(repo):
    root, sections = _scene()
    base = datetime.now(timezone.utc)
    active_expiry = (base + timedelta(days=10)).isoformat()
    expired_cutoff = (base + timedelta(days=11)).isoformat()
    created, _ = await repo.create(
        owner_subject="alice",
        root=root,
        sections=sections,
        request_hash=_digest({"request": 1}),
        idempotency_key="build-a",
        tolerance_digest=_digest({"profile": "mechanical-2d/1"}),
        build_options_digest=_digest({"sections": sorted(sections)}),
        section_digests={name: _digest(items) for name, items in sections.items()},
        correlation_id="correlation-a",
        expires_at=active_expiry,
    )
    conflicting, conflicting_sections = _scene(scene_digest=_digest({"other": True}))
    with pytest.raises(SceneRepositoryConflict, match="idempotency_conflict"):
        await repo.create(
            owner_subject="alice",
            root=conflicting,
            sections=conflicting_sections,
            request_hash=_digest({"request": 2}),
            idempotency_key="build-a",
            tolerance_digest=_digest({"profile": "mechanical-2d/1"}),
            build_options_digest=_digest({"sections": sorted(sections)}),
            section_digests={
                name: _digest(items) for name, items in conflicting_sections.items()
            },
            correlation_id="correlation-b",
            expires_at=active_expiry,
        )

    assert await repo.delete_expired(now=expired_cutoff) == 1
    assert await repo.get("alice", created["scene_id"]) is None
    with repo.database.read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM scene_sections"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM scene_request_bindings"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_expired_canonical_scene_is_rebuilt_with_fresh_retention(repo):
    root, sections = _scene()
    base = datetime.now(timezone.utc)
    expired_at = (base - timedelta(days=1)).isoformat()
    fresh_expiry = (base + timedelta(days=10)).isoformat()
    retained_cutoff = (base + timedelta(days=1)).isoformat()
    expired_cutoff = (base + timedelta(days=11)).isoformat()
    created, _ = await repo.create(
        owner_subject="alice",
        root=root,
        sections=sections,
        request_hash=_digest({"request": 1}),
        idempotency_key="old-key",
        tolerance_digest=_digest({"profile": "mechanical-2d/1"}),
        build_options_digest=_digest({"sections": sorted(sections)}),
        section_digests={name: _digest(items) for name, items in sections.items()},
        correlation_id="correlation-old",
        expires_at=expired_at,
    )
    reused, replayed = await repo.create(
        owner_subject="alice",
        root=root,
        sections=sections,
        request_hash=_digest({"request": 1}),
        idempotency_key="fresh-key",
        tolerance_digest=_digest({"profile": "mechanical-2d/1"}),
        build_options_digest=_digest({"sections": sorted(sections)}),
        section_digests={name: _digest(items) for name, items in sections.items()},
        correlation_id="correlation-fresh",
        expires_at=fresh_expiry,
    )

    assert not replayed
    assert reused["scene_id"] != created["scene_id"]
    assert reused["expires_at"] == fresh_expiry
    assert await repo.delete_expired(now=retained_cutoff) == 0
    assert await repo.get("alice", created["scene_id"]) is None
    assert await repo.get("alice", reused["scene_id"]) is not None
    assert await repo.delete_expired(now=expired_cutoff) == 1


@pytest.mark.asyncio
async def test_expired_scenes_are_hidden_and_swept_by_maintenance(tmp_path):
    services = DurableGatewayServices(
        SqliteDatabase(tmp_path / "scene-maintenance.sqlite"),
        ConnectionRegistry(),
        device_tokens={"device-a": "token-a"},
        phase10_scene_engine_enabled=True,
        maintenance_interval_seconds=3600,
    )
    await services.initialize()
    assert services.scene_repository is not None
    root, sections = _scene()
    created, _ = await services.scene_repository.create(
        owner_subject="alice",
        root=root,
        sections=sections,
        request_hash=_digest({"request": 1}),
        idempotency_key="expired-key",
        tolerance_digest=_digest({"profile": "mechanical-2d/1"}),
        build_options_digest=_digest({"sections": sorted(sections)}),
        section_digests={name: _digest(items) for name, items in sections.items()},
        correlation_id="correlation-expired",
        expires_at="2000-01-01T00:00:00+00:00",
    )
    try:
        assert await services.scene_repository.get("alice", created["scene_id"]) is None
        assert (
            await services.scene_repository.get_section(
                "alice", created["scene_id"], "nodes"
            )
            is None
        )
        assert await services.scene_repository.list("alice") == []
        with services.database.read_connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM scene_records"
            ).fetchone()[0] == 1

        await services._run_maintenance_once()

        with services.database.read_connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM scene_records"
            ).fetchone()[0] == 0
    finally:
        await services.shutdown()
