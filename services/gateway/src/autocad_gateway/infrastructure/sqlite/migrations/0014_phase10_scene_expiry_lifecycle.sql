DROP TRIGGER scene_records_immutable;

CREATE TRIGGER scene_records_immutable
BEFORE UPDATE OF
    scene_id, owner_subject, device_id, source_snapshot_id, document_id,
    document_revision, space, projection_version, engine_version, profile_id,
    tolerance_digest, build_options_digest, source_digest, scene_digest,
    request_hash, idempotency_key, complete, root_json, counts_json,
    warnings_json, capabilities_json, correlation_id, created_at
ON scene_records
BEGIN
    SELECT RAISE(ABORT, 'scene_records_immutable');
END;
