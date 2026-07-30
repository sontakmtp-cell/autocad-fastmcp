from __future__ import annotations

import hashlib
import json

import pytest

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
        "tolerance_digest": _digest({"profile": "mechanical-2d/1"}),
        "source_digest": _digest({"snapshot": "snapshot-a"}),
        "scene_digest": scene_digest or _digest(sections),
        "complete": True,
        "counts": {name: len(items) for name, items in sections.items()},
        "warnings": [],
        "capabilities": ["scene.core/1"],
        "section_digests": {
            name: _digest(items) for name, items in sections.items()
        },
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
    first, replayed = await repo.create(
        owner_subject="alice",
        root=root,
        sections=sections,
        request_hash=_digest({"request": 1}),
        idempotency_key="build-a",
        build_options_digest=_digest({"sections": sorted(sections)}),
        correlation_id="correlation-a",
        expires_at="2026-08-01T00:00:00+00:00",
    )
    assert not replayed
    assert first["scene_id"].startswith("scn-")
    assert await repo.get("bob", first["scene_id"]) is None
    assert await repo.get_section("bob", first["scene_id"], "nodes") is None
    assert await repo.get_section("alice", first["scene_id"], "nodes") == sections["nodes"]

    duplicate, replayed = await repo.create(
        owner_subject="alice",
        root=root,
        sections=sections,
        request_hash=_digest({"request": 1}),
        idempotency_key="build-a",
        build_options_digest=_digest({"sections": sorted(sections)}),
        correlation_id="correlation-b",
        expires_at="2026-08-01T00:00:00+00:00",
    )
    assert replayed and duplicate["scene_id"] == first["scene_id"]
    assert (await repo.list("alice"))[0]["scene_id"] == first["scene_id"]

    with pytest.raises(Exception, match="scene_records_immutable"):
        with repo.database.transaction() as connection:
            connection.execute(
                "UPDATE scene_records SET complete=0 WHERE scene_id=?",
                (first["scene_id"],),
            )


@pytest.mark.asyncio
async def test_conflict_expiry_and_no_orphan_sections(repo):
    root, sections = _scene()
    created, _ = await repo.create(
        owner_subject="alice",
        root=root,
        sections=sections,
        request_hash=_digest({"request": 1}),
        idempotency_key="build-a",
        build_options_digest=_digest({"sections": sorted(sections)}),
        correlation_id="correlation-a",
        expires_at="2026-07-01T00:00:00+00:00",
    )
    conflicting, conflicting_sections = _scene(scene_digest=_digest({"other": True}))
    with pytest.raises(SceneRepositoryConflict, match="idempotency_conflict"):
        await repo.create(
            owner_subject="alice",
            root=conflicting,
            sections=conflicting_sections,
            request_hash=_digest({"request": 2}),
            idempotency_key="build-a",
            build_options_digest=_digest({"sections": sorted(sections)}),
            correlation_id="correlation-b",
            expires_at="2026-08-01T00:00:00+00:00",
        )

    assert await repo.delete_expired(now="2026-07-02T00:00:00+00:00") == 1
    assert await repo.get("alice", created["scene_id"]) is None
    with repo.database.read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM scene_sections"
        ).fetchone()[0] == 0
