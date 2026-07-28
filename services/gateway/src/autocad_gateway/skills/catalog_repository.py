"""SQLite authority for operator-only Phase 9 catalog lifecycle."""
from __future__ import annotations
import json
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase, new_id, utc_now

class SkillCatalogRepository:
    def __init__(self, database: SqliteDatabase) -> None: self.database = database
    def import_version(self, manifest: dict, *, release_digest: str) -> None:
        with self.database.transaction() as c:
            c.execute("""INSERT INTO skill_versions(skill_id,version,status,manifest_json,manifest_digest,workflow_id,workflow_version,workflow_digest,guide_digest,catalog_release_digest,published_at,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(skill_id,version) DO NOTHING""", (manifest['skill_id'],manifest['version'],'published',json.dumps(manifest,sort_keys=True),manifest['manifest_digest'],manifest['workflow_definition']['workflow_id'],manifest['workflow_definition']['version'],manifest['workflow_definition']['digest'],manifest['guide_digest'],release_digest,utc_now(),utc_now()))
    def transition(self, skill_id: str, version: str, expected: str, target: str, operator: str) -> None:
        with self.database.transaction() as c:
            row=c.execute("SELECT status FROM skill_versions WHERE skill_id=? AND version=?",(skill_id,version)).fetchone()
            if row is None: raise ValueError('skill_not_found')
            if row['status'] != expected or target not in {'published','deprecated','withdrawn','security_revoked'}: raise ValueError('stale_publication_state')
            c.execute("UPDATE skill_versions SET status=? WHERE skill_id=? AND version=? AND status=?",(target,skill_id,version,expected))
            c.execute("INSERT INTO skill_publication_events VALUES(?,?,?,?,?,?,?,?,?)",(new_id('skillpub'),skill_id,version,expected,target,None,None,operator,utc_now()))
    def promote(self, skill_id: str, version: str, channel: str, operator: str) -> None:
        with self.database.transaction() as c:
            row=c.execute("SELECT status FROM skill_versions WHERE skill_id=? AND version=?",(skill_id,version)).fetchone()
            if row is None or row['status'] in {'withdrawn','security_revoked'}: raise ValueError('channel_target_unavailable')
            old=c.execute("SELECT epoch FROM skill_channels WHERE skill_id=? AND channel=?",(skill_id,channel)).fetchone(); epoch=(int(old['epoch'])+1) if old else 0
            c.execute("INSERT INTO skill_channels(skill_id,channel,default_version,epoch,status,updated_by,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(skill_id,channel) DO UPDATE SET default_version=excluded.default_version,epoch=excluded.epoch,status='active',updated_by=excluded.updated_by,updated_at=excluded.updated_at",(skill_id,channel,version,epoch,'active',operator,utc_now()))
            c.execute("INSERT INTO skill_publication_events VALUES(?,?,?,?,?,?,?,?,?)",(new_id('skillpub'),skill_id,version,None,'promoted',channel,epoch,operator,utc_now()))
