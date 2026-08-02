"""SQLite authority for the operator-only Phase 9 catalog lifecycle."""
from __future__ import annotations

import json
from typing import Any

from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase, new_id, utc_now
from .catalog import SkillCatalog


class CatalogLifecycleError(ValueError):
    pass


_TRANSITIONS = {
    "published": {"deprecated", "withdrawn", "security_revoked"},
    "deprecated": {"withdrawn", "security_revoked"},
    "withdrawn": set(),
    "security_revoked": set(),
}


class SkillCatalogRepository:
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    def import_catalog(self, catalog: SkillCatalog) -> None:
        """Import the fixed release after migrations are open; never from request paths."""
        for manifest_model in catalog.list():
            workflow_model = catalog.workflow_for(manifest_model)
            self.import_version(
                manifest_model.model_dump(mode="json"),
                workflow_model.model_dump(mode="json"),
                release_digest=catalog.release_digest,
            )

    def import_version(
        self, manifest: dict[str, Any], workflow: dict[str, Any], *, release_digest: str, channel: str = "default"
    ) -> None:
        """Idempotently import exact reviewed assets; conflict never overwrites a version."""
        if channel not in {"default", "preview"}:
            raise CatalogLifecycleError("invalid_channel")
        required = ("skill_id", "version", "manifest_digest", "workflow_definition", "guide_digest")
        if not all(isinstance(manifest.get(key), (str, dict)) for key in required):
            raise CatalogLifecycleError("invalid_manifest")
        reference = manifest["workflow_definition"]
        if reference.get("digest") != workflow.get("definition_digest"):
            raise CatalogLifecycleError("workflow_digest_mismatch")
        now = utc_now()
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        with self.database.transaction() as conn:
            old = conn.execute("SELECT manifest_digest,workflow_digest FROM skill_versions WHERE skill_id=? AND version=?", (manifest["skill_id"], manifest["version"])).fetchone()
            if old is not None:
                if tuple(old) != (manifest["manifest_digest"], reference["digest"]):
                    raise CatalogLifecycleError("immutable_version_conflict")
                return
            conn.execute("INSERT INTO workflow_definitions(workflow_id,version,definition_json,definition_digest,step_count,planner_refs_json,template_refs_json,created_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(workflow_id,version) DO NOTHING", (workflow["workflow_id"], workflow["version"], json.dumps(workflow, sort_keys=True, separators=(",", ":")), workflow["definition_digest"], len(workflow["steps"]), "[]", "[]", now))
            conn.execute("INSERT INTO skill_versions(skill_id,version,status,manifest_json,manifest_digest,workflow_id,workflow_version,workflow_digest,guide_digest,catalog_release_digest,published_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (manifest["skill_id"], manifest["version"], "published", canonical, manifest["manifest_digest"], reference["workflow_id"], reference["version"], reference["digest"], manifest["guide_digest"], release_digest, now, now))
            conn.execute("INSERT INTO skill_channels(skill_id,channel,default_version,epoch,status,updated_by,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(skill_id,channel) DO NOTHING", (manifest["skill_id"], channel, manifest["version"], 1, "active", "release-import", now))

    def transition(self, skill_id: str, version: str, expected: str, target: str, operator: str) -> None:
        if not operator.strip(): raise CatalogLifecycleError("invalid_operator")
        if target not in _TRANSITIONS.get(expected, set()): raise CatalogLifecycleError("illegal_publication_transition")
        stamp = {"deprecated": "deprecated_at", "withdrawn": "withdrawn_at", "security_revoked": "security_revoked_at"}[target]
        with self.database.transaction() as conn:
            changed = conn.execute(f"UPDATE skill_versions SET status=?, {stamp}=? WHERE skill_id=? AND version=? AND status=?", (target, utc_now(), skill_id, version, expected)).rowcount
            if changed != 1: raise CatalogLifecycleError("stale_publication_state")
            conn.execute("INSERT INTO skill_publication_events VALUES(?,?,?,?,?,?,?,?,?)", (new_id("skillpub"), skill_id, version, expected, target, None, None, operator, utc_now()))

    def promote(self, skill_id: str, version: str, channel: str, operator: str) -> int:
        if channel not in {"default", "preview"} or not operator.strip(): raise CatalogLifecycleError("invalid_promotion")
        with self.database.transaction() as conn:
            status = conn.execute("SELECT status FROM skill_versions WHERE skill_id=? AND version=?", (skill_id, version)).fetchone()
            if status is None or status["status"] in {"withdrawn", "security_revoked"}: raise CatalogLifecycleError("channel_target_unavailable")
            old = conn.execute("SELECT default_version,epoch FROM skill_channels WHERE skill_id=? AND channel=?", (skill_id, channel)).fetchone()
            epoch = int(old["epoch"]) + 1 if old else 1
            conn.execute("INSERT INTO skill_channels(skill_id,channel,default_version,epoch,status,updated_by,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(skill_id,channel) DO UPDATE SET default_version=excluded.default_version,epoch=excluded.epoch,status='active',updated_by=excluded.updated_by,updated_at=excluded.updated_at", (skill_id, channel, version, epoch, "active", operator, utc_now()))
            conn.execute("INSERT INTO skill_publication_events VALUES(?,?,?,?,?,?,?,?,?)", (new_id("skillpub"), skill_id, version, None if old is None else old["default_version"], "promoted", channel, epoch, operator, utc_now()))
            return epoch

    def get_status(self, skill_id: str, version: str) -> str:
        with self.database.read_connection() as conn:
            row = conn.execute("SELECT status FROM skill_versions WHERE skill_id=? AND version=?", (skill_id, version)).fetchone()
        if row is None: raise CatalogLifecycleError("skill_not_found")
        return str(row["status"])

    def get_channel(self, skill_id: str, channel: str = "default") -> tuple[str, int]:
        with self.database.read_connection() as conn:
            row = conn.execute("SELECT default_version,epoch FROM skill_channels WHERE skill_id=? AND channel=? AND status='active'", (skill_id, channel)).fetchone()
        if row is None: raise CatalogLifecycleError("skill_not_found")
        return str(row["default_version"]), int(row["epoch"])
