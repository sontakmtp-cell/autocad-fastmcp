CREATE TABLE scene_records (
    scene_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    device_id TEXT NOT NULL,
    source_snapshot_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    document_revision TEXT NOT NULL,
    space TEXT NOT NULL CHECK (space IN ('model', 'paper')),
    projection_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    tolerance_digest TEXT NOT NULL,
    build_options_digest TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    scene_digest TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    complete INTEGER NOT NULL CHECK (complete IN (0, 1)),
    root_json TEXT NOT NULL,
    counts_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE (owner_subject, idempotency_key),
    UNIQUE (
        owner_subject, source_digest, projection_version, engine_version,
        profile_id, tolerance_digest, build_options_digest
    )
);

CREATE TABLE scene_sections (
    scene_id TEXT NOT NULL REFERENCES scene_records(scene_id) ON DELETE CASCADE,
    section TEXT NOT NULL CHECK (
        section IN (
            'nodes', 'relations', 'contours', 'features', 'issues', 'evidence'
        )
    ),
    payload_json TEXT NOT NULL,
    item_count INTEGER NOT NULL CHECK (item_count >= 0),
    section_digest TEXT NOT NULL,
    PRIMARY KEY (scene_id, section)
);

CREATE INDEX idx_scene_records_owner_created
    ON scene_records(owner_subject, created_at DESC);
CREATE INDEX idx_scene_records_expires
    ON scene_records(expires_at);

CREATE TRIGGER scene_records_immutable
BEFORE UPDATE ON scene_records
BEGIN
    SELECT RAISE(ABORT, 'scene_records_immutable');
END;

CREATE TRIGGER scene_sections_immutable
BEFORE UPDATE ON scene_sections
BEGIN
    SELECT RAISE(ABORT, 'scene_sections_immutable');
END;
