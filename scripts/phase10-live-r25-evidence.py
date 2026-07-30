"""Capture one bounded read-only Phase 10 scene from the active R25 drawing."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autocad_contracts import CadBuildSceneInput, CadQuerySceneInput
from autocad_desktop_agent.runtime.managed_dotnet import ManagedDotNetCadReadPort
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.scenes.repository import SceneRepository
from autocad_gateway.scenes.service import SceneApplicationService
from cad_core.scene import SceneBuildContext, build_scene


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_MANIFEST = (
    Path(os.environ["APPDATA"])
    / "Autodesk"
    / "ApplicationPlugins"
    / "AutocadMcp.ManagedHost.R25.bundle"
    / "Contents"
    / "Shared"
    / "package-manifest.json"
)


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _counts(values: list[str]) -> dict[str, int]:
    return {value: values.count(value) for value in sorted(set(values))}


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class _LiveSnapshot:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value

    async def get_snapshot(self, owner_subject: str, snapshot_id: str):
        if owner_subject != "phase10-live-operator":
            return None
        return self.value if snapshot_id == self.value["snapshot_id"] else None


async def _capture(fixture_id: str, database_path: Path) -> dict[str, Any]:
    port = ManagedDotNetCadReadPort.from_default_bootstrap(
        agent_version="phase10-live-r25-evidence",
        expected_host_family="R25",
    )
    handshake = await port._ensure_handshake()
    probe = await port.probe()
    health = await port.health()
    before = await port.entity_snapshot(limit=5_000)
    if not health.ok or not before.ok:
        raise RuntimeError(health.error_code or before.error_code or "live_read_failed")
    snapshot = before.payload
    assert isinstance(snapshot, dict)
    revision = str(snapshot["revision"]["revision"])
    source_snapshot_id = f"live-{fixture_id}-{revision}"
    source_capabilities = tuple(
        sorted(str(value) for value in snapshot.get("source_capabilities", []))
    )
    durable_snapshot = {
        **snapshot,
        "snapshot_id": source_snapshot_id,
        "device_id": "local-r25-lab",
        "document_revision": revision,
        "drawing": {
            "document_id": str(snapshot["document_id"]),
            "name": str(snapshot.get("document_name", "")),
            "units": "mm",
        },
    }
    artifact = build_scene(
        snapshot["entities"],
        SceneBuildContext(
            source_snapshot_id=source_snapshot_id,
            device_id="local-r25-lab",
            document_id=str(snapshot["document_id"]),
            document_revision=revision,
            source_capabilities=source_capabilities,
            drawing_units="mm",
        ),
    )
    after = await port.entity_snapshot(
        limit=1,
        expected_revision=int(revision),
    )
    if not after.ok or not isinstance(after.payload, dict):
        raise RuntimeError(after.error_code or "post_scene_read_failed")
    revision_after = str(after.payload["revision"]["revision"])
    if revision_after != revision:
        raise RuntimeError("scene_build_changed_document_revision")

    if database_path.exists():
        raise RuntimeError("live evidence database already exists")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database = SqliteDatabase(database_path)
    await database.open()
    service = SceneApplicationService(
        SceneRepository(database),
        _LiveSnapshot(durable_snapshot),
        cursor_secret=b"phase10-live-cursor-secret-32-bytes-minimum",
        mechanical_features_enabled=True,
    )
    built = await service.build(
        "phase10-live-operator",
        CadBuildSceneInput(
            source_snapshot_id=source_snapshot_id,
            idempotency_key=f"phase10-live-{fixture_id}-{revision}",
        ),
        "phase10-live-build",
    )
    await database.close()
    await database.open()
    restarted = SceneApplicationService(
        SceneRepository(database),
        _LiveSnapshot(durable_snapshot),
        cursor_secret=b"phase10-live-cursor-secret-32-bytes-minimum",
        mechanical_features_enabled=True,
    )
    restored = await restarted.summary(
        "phase10-live-operator", built.scene.scene_id
    )
    restored_issues = await restarted.query(
        "phase10-live-operator",
        CadQuerySceneInput(
            scene_id=built.scene.scene_id,
            section="issues",
            limit=200,
        ),
        "phase10-live-restart-query",
    )
    await database.close()
    if (
        restored["scene_digest"] != built.scene.scene_digest
        or restored["source_digest"] != artifact.source_digest
    ):
        raise RuntimeError("gateway_restart_scene_binding_mismatch")

    manifest = json.loads(PACKAGE_MANIFEST.read_text(encoding="utf-8"))
    signing = manifest.get("signing") if isinstance(manifest.get("signing"), dict) else {}
    return {
        "schema_version": "cad.phase10-live-r25-evidence/1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "operator": "local-operator",
        "implementation_commit": _git_head(),
        "fixture": {
            "fixture_id": fixture_id,
            "document_name": str(snapshot.get("document_name", "")),
            "document_id": str(snapshot["document_id"]),
            "database_fingerprint": str(snapshot.get("database_fingerprint", "")),
        },
        "runtime": {
            "runtime_id": probe.runtime_id,
            "product": probe.product,
            "edition": probe.edition,
            "release_year": probe.release_year,
            "series": probe.series,
            "host_family": handshake["host_family"],
            "host_version": handshake["host_version"],
            "package_id": handshake["package_id"],
            "package_version": manifest.get("package_version"),
            "package_hash": handshake["package_hash"],
            "package_manifest_hash": _sha256(PACKAGE_MANIFEST),
            "signed": manifest.get("signed") is True,
            "lab_only": signing.get("lab_only") is True,
            "timestamped": signing.get("timestamped") is True,
            "capabilities": sorted(handshake["capabilities"]),
        },
        "source": {
            "snapshot_id": artifact.context.source_snapshot_id,
            "document_revision_before": revision,
            "document_revision_after": revision_after,
            "document_revision_unchanged": True,
            "entity_count": len(snapshot["entities"]),
            "entity_types": _counts(
                [str(entity["type"]) for entity in snapshot["entities"]]
            ),
            "source_capabilities": list(source_capabilities),
        },
        "scene": {
            "profile_id": artifact.context.profile_id,
            "projection_version": "cad.entity-projection/2",
            "engine_version": artifact.engine_version,
            "source_digest": artifact.source_digest,
            "scene_digest": artifact.scene_digest,
            "complete": artifact.complete,
            "geometry_statuses": _counts(
                [node.geometry_status for node in artifact.nodes]
            ),
            "relation_types": _counts(
                [relation.relation_type for relation in artifact.relations]
            ),
            "feature_types": _counts(
                [feature.feature_type for feature in artifact.features]
            ),
            "issue_codes": _counts([issue.code for issue in artifact.issues]),
            "counts": {
                "nodes": len(artifact.nodes),
                "relations": len(artifact.relations),
                "contours": len(artifact.contours),
                "components": len(artifact.components),
                "features": len(artifact.features),
                "issues": len(artifact.issues),
            },
            "stats": asdict(artifact.stats),
        },
        "effects": {
            "write_requested": False,
            "document_revision_unchanged": True,
        },
        "gateway_restart": {
            "database_hash": _sha256(database_path),
            "scene_id": built.scene.scene_id,
            "scene_digest": built.scene.scene_digest,
            "source_digest": built.scene.source_digest,
            "same_scene_retrieved": True,
            "issue_count_after_restart": restored_issues.total,
            "duplicate_cad_effect": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(_capture(args.fixture_id, args.database))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
