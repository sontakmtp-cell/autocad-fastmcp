CREATE TABLE scene_request_bindings (
    owner_subject TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    projection_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    tolerance_digest TEXT NOT NULL,
    build_options_digest TEXT NOT NULL,
    scene_digest TEXT NOT NULL,
    scene_id TEXT NOT NULL REFERENCES scene_records(scene_id) ON DELETE CASCADE,
    correlation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_subject, idempotency_key)
);

INSERT INTO scene_request_bindings(
    owner_subject, idempotency_key, request_hash, source_digest,
    projection_version, engine_version, profile_id, tolerance_digest,
    build_options_digest, scene_digest, scene_id, correlation_id, created_at
)
SELECT
    owner_subject, idempotency_key, request_hash, source_digest,
    projection_version, engine_version, profile_id, tolerance_digest,
    build_options_digest, scene_digest, scene_id, correlation_id, created_at
FROM scene_records;

CREATE INDEX idx_scene_request_bindings_scene
ON scene_request_bindings(scene_id);

CREATE TRIGGER scene_request_bindings_immutable
BEFORE UPDATE ON scene_request_bindings
BEGIN
    SELECT RAISE(ABORT, 'scene_request_bindings_immutable');
END;
