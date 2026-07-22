ALTER TABLE devices ADD COLUMN agent_version TEXT;
ALTER TABLE devices ADD COLUMN runtime_state TEXT;
ALTER TABLE devices ADD COLUMN document_name TEXT;
ALTER TABLE devices ADD COLUMN paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1));
ALTER TABLE devices ADD COLUMN package_manifest_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE devices ADD COLUMN package_manifest_hash TEXT;
ALTER TABLE devices ADD COLUMN runtime_updated_at TEXT;

ALTER TABLE agent_sessions ADD COLUMN agent_version TEXT;
ALTER TABLE agent_sessions ADD COLUMN package_manifest_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE agent_sessions ADD COLUMN package_manifest_hash TEXT;

ALTER TABLE snapshots ADD COLUMN revision_strength TEXT;
ALTER TABLE snapshots ADD COLUMN commit_safe INTEGER NOT NULL DEFAULT 0 CHECK (commit_safe IN (0, 1));
