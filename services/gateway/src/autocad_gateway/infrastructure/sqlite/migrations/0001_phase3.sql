CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('online', 'offline', 'incompatible')),
    capabilities_json TEXT NOT NULL,
    fixture_auth_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    protocol_version TEXT NOT NULL,
    connected_at TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    disconnected_at TEXT,
    last_sequence INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    kind TEXT NOT NULL,
    effect_class TEXT NOT NULL CHECK (effect_class IN ('read', 'write')),
    state TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 0,
    deadline_at TEXT,
    command_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    progress_json TEXT,
    result_json TEXT,
    error_code TEXT,
    error_summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (owner_subject, device_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS job_events (
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    state TEXT,
    progress_json TEXT,
    error_code TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, sequence)
);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    revision INTEGER NOT NULL,
    document_revision TEXT NOT NULL,
    observation_level TEXT NOT NULL,
    drawing_json TEXT NOT NULL,
    entity_summary_json TEXT NOT NULL,
    entities_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (job_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_jobs_owner_state ON jobs(owner_subject, state);
CREATE INDEX IF NOT EXISTS idx_jobs_device_state ON jobs(device_id, state);
CREATE INDEX IF NOT EXISTS idx_events_job_sequence ON job_events(job_id, sequence);
CREATE INDEX IF NOT EXISTS idx_sessions_device ON agent_sessions(device_id, connected_at);
