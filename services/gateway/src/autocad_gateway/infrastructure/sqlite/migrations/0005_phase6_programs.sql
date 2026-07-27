ALTER TABLE devices ADD COLUMN capability_manifest_json TEXT;
ALTER TABLE devices ADD COLUMN capability_manifest_hash TEXT;
ALTER TABLE devices ADD COLUMN operation_registry_hash TEXT;
ALTER TABLE devices ADD COLUMN registry_version TEXT;
ALTER TABLE devices ADD COLUMN write_lock_enabled INTEGER NOT NULL DEFAULT 0
    CHECK (write_lock_enabled IN (0, 1));
ALTER TABLE devices ADD COLUMN hard_pause INTEGER NOT NULL DEFAULT 0
    CHECK (hard_pause IN (0, 1));
ALTER TABLE devices ADD COLUMN active_document_id TEXT;
ALTER TABLE devices ADD COLUMN active_document_revision TEXT;

ALTER TABLE agent_sessions ADD COLUMN capability_manifest_json TEXT;
ALTER TABLE agent_sessions ADD COLUMN capability_manifest_hash TEXT;
ALTER TABLE agent_sessions ADD COLUMN operation_registry_hash TEXT;
ALTER TABLE agent_sessions ADD COLUMN registry_version TEXT;
ALTER TABLE agent_sessions ADD COLUMN write_lock_enabled INTEGER NOT NULL DEFAULT 0
    CHECK (write_lock_enabled IN (0, 1));
ALTER TABLE agent_sessions ADD COLUMN hard_pause INTEGER NOT NULL DEFAULT 0
    CHECK (hard_pause IN (0, 1));
ALTER TABLE agent_sessions ADD COLUMN active_document_id TEXT;
ALTER TABLE agent_sessions ADD COLUMN active_document_revision TEXT;

CREATE TABLE cad_programs (
    program_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    document_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE cad_program_revisions (
    program_id TEXT NOT NULL REFERENCES cad_programs(program_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    owner_subject TEXT NOT NULL,
    source_snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
    expected_document_revision TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK (schema_version = 'cad.program/0.2'),
    registry_version TEXT NOT NULL,
    program_digest TEXT NOT NULL,
    semantic_json TEXT NOT NULL,
    operations_json TEXT NOT NULL,
    preconditions_json TEXT NOT NULL,
    postconditions_json TEXT NOT NULL,
    budgets_json TEXT NOT NULL,
    risk_class TEXT NOT NULL CHECK (risk_class = 'low'),
    missing_capabilities_json TEXT NOT NULL,
    runtime_id TEXT NOT NULL,
    runtime_role TEXT NOT NULL,
    host_family TEXT NOT NULL,
    host_version TEXT NOT NULL,
    package_id TEXT NOT NULL,
    package_version TEXT NOT NULL,
    package_hash TEXT NOT NULL,
    capability_manifest_hash TEXT NOT NULL,
    operation_registry_hash TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (program_id, revision),
    UNIQUE (owner_subject, program_digest)
);

CREATE TABLE cad_previews (
    preview_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    program_id TEXT NOT NULL,
    program_revision INTEGER NOT NULL,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
    program_digest TEXT NOT NULL,
    execution_digest TEXT NOT NULL UNIQUE,
    preview_digest TEXT NOT NULL,
    binding_digest TEXT NOT NULL,
    document_id TEXT NOT NULL,
    expected_document_revision TEXT NOT NULL,
    runtime_id TEXT NOT NULL,
    runtime_role TEXT NOT NULL,
    host_family TEXT NOT NULL,
    host_version TEXT NOT NULL,
    package_id TEXT NOT NULL,
    package_version TEXT NOT NULL,
    package_hash TEXT NOT NULL,
    capability_manifest_hash TEXT NOT NULL,
    operation_registry_hash TEXT NOT NULL,
    registry_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    planned_operation_count INTEGER NOT NULL CHECK (planned_operation_count >= 0),
    planned_entity_count INTEGER NOT NULL CHECK (planned_entity_count >= 0),
    planned_layer_count INTEGER NOT NULL CHECK (planned_layer_count >= 0),
    validation_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    invalidated_reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (program_id, program_revision)
        REFERENCES cad_program_revisions(program_id, revision)
);

CREATE TABLE cad_validations (
    validation_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    program_id TEXT NOT NULL,
    program_revision INTEGER NOT NULL,
    receipt_id TEXT NOT NULL,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
    execution_digest TEXT NOT NULL UNIQUE,
    binding_digest TEXT NOT NULL,
    document_revision TEXT NOT NULL,
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (program_id, program_revision)
        REFERENCES cad_program_revisions(program_id, revision)
);

CREATE TABLE cad_execution_receipts (
    receipt_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    program_id TEXT NOT NULL,
    program_revision INTEGER NOT NULL,
    preview_id TEXT NOT NULL UNIQUE REFERENCES cad_previews(preview_id),
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
    program_digest TEXT NOT NULL,
    execution_digest TEXT NOT NULL UNIQUE,
    receipt_digest TEXT NOT NULL,
    preview_execution_digest TEXT NOT NULL,
    binding_digest TEXT NOT NULL,
    document_id TEXT NOT NULL,
    document_revision_before TEXT NOT NULL,
    document_revision_after TEXT NOT NULL,
    runtime_id TEXT NOT NULL,
    package_hash TEXT NOT NULL,
    capability_manifest_hash TEXT NOT NULL,
    operation_registry_hash TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    effect_summary_json TEXT NOT NULL,
    durable_receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (program_id, program_revision)
        REFERENCES cad_program_revisions(program_id, revision)
);

CREATE TABLE program_idempotency (
    owner_subject TEXT NOT NULL,
    action TEXT NOT NULL CHECK (
        action IN ('prepare', 'preview', 'commit', 'validate')
    ),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_kind TEXT NOT NULL CHECK (
        response_kind IN ('program', 'job', 'receipt')
    ),
    response_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_subject, action, idempotency_key)
);

CREATE TABLE cad_program_write_locks (
    device_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (device_id, document_id)
);

CREATE INDEX idx_programs_owner_created
ON cad_programs(owner_subject, created_at);

CREATE INDEX idx_program_revisions_owner_snapshot
ON cad_program_revisions(owner_subject, source_snapshot_id);

CREATE INDEX idx_previews_owner_program
ON cad_previews(owner_subject, program_id, program_revision);

CREATE INDEX idx_validations_owner_program
ON cad_validations(owner_subject, program_id, program_revision);

CREATE INDEX idx_receipts_owner_program
ON cad_execution_receipts(owner_subject, program_id, program_revision);
