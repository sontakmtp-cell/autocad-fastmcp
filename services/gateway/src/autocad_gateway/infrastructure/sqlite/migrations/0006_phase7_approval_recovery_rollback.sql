CREATE TABLE execution_intents (
    intent_id TEXT PRIMARY KEY,
    intent_version INTEGER NOT NULL CHECK (intent_version >= 1),
    owner_subject TEXT NOT NULL,
    actor_issuer TEXT NOT NULL,
    actor_subject TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('program_commit', 'rollback_commit')),
    state TEXT NOT NULL CHECK (
        state IN (
            'awaiting_approval', 'ready', 'released', 'denied',
            'expired', 'invalidated', 'cancelled'
        )
    ),
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    device_identity_generation INTEGER NOT NULL CHECK (device_identity_generation >= 1),
    device_key_thumbprint TEXT NOT NULL,
    document_id TEXT NOT NULL,
    expected_document_revision TEXT NOT NULL,
    program_id TEXT NOT NULL,
    program_revision INTEGER NOT NULL,
    program_digest TEXT NOT NULL,
    preview_id TEXT NOT NULL REFERENCES cad_previews(preview_id),
    preview_digest TEXT NOT NULL,
    preview_execution_digest TEXT NOT NULL,
    preview_expires_at TEXT NOT NULL,
    deterministic_receipt_id TEXT NOT NULL,
    commit_execution_digest TEXT NOT NULL,
    runtime_pins_json TEXT NOT NULL,
    policy_pins_json TEXT NOT NULL,
    risk_class TEXT NOT NULL CHECK (
        risk_class IN ('low', 'medium', 'high', 'destructive')
    ),
    required_assurance TEXT NOT NULL CHECK (
        required_assurance IN (
            'none', 'device_local_confirmation', 'user_recent_auth',
            'user_recent_auth_plus_device_local'
        )
    ),
    trusted_effect_summary_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    intent_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consent_id TEXT REFERENCES consents(consent_id),
    released_job_id TEXT UNIQUE REFERENCES jobs(job_id),
    FOREIGN KEY (program_id, program_revision)
        REFERENCES cad_program_revisions(program_id, revision),
    UNIQUE (owner_subject, idempotency_key),
    UNIQUE (owner_subject, intent_digest),
    CHECK (
        (state = 'released' AND released_job_id IS NOT NULL)
        OR (state <> 'released' AND released_job_id IS NULL)
    )
);

CREATE TABLE consents (
    consent_id TEXT PRIMARY KEY,
    consent_version INTEGER NOT NULL CHECK (consent_version >= 1),
    owner_subject TEXT NOT NULL,
    intent_id TEXT NOT NULL REFERENCES execution_intents(intent_id),
    intent_version INTEGER NOT NULL CHECK (intent_version >= 1),
    intent_digest TEXT NOT NULL,
    required_assurance TEXT NOT NULL CHECK (
        required_assurance IN (
            'none', 'device_local_confirmation', 'user_recent_auth',
            'user_recent_auth_plus_device_local'
        )
    ),
    state TEXT NOT NULL CHECK (
        state IN ('requested', 'approved', 'denied', 'expired', 'invalidated', 'consumed')
    ),
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    challenge_nonce_hash TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_at TEXT,
    decision_source TEXT CHECK (
        decision_source IS NULL OR decision_source IN ('device_local', 'portal_recent_auth')
    ),
    decision_principal_json TEXT,
    decision_device_id TEXT REFERENCES devices(device_id),
    decision_device_identity_generation INTEGER CHECK (
        decision_device_identity_generation IS NULL
        OR decision_device_identity_generation >= 1
    ),
    consumed_at TEXT,
    UNIQUE (intent_id, intent_version, required_assurance),
    CHECK (
        (state = 'consumed' AND consumed_at IS NOT NULL)
        OR (state <> 'consumed' AND consumed_at IS NULL)
    )
);

CREATE TABLE execution_evidence_events (
    event_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('gateway', 'agent', 'host')),
    source_sequence INTEGER NOT NULL CHECK (source_sequence >= 0),
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    command_id TEXT,
    intent_id TEXT REFERENCES execution_intents(intent_id),
    payload_digest TEXT,
    execution_digest TEXT,
    receipt_digest TEXT,
    payload_json TEXT NOT NULL,
    source_timestamp TEXT NOT NULL,
    gateway_received_at TEXT NOT NULL,
    event_digest TEXT NOT NULL,
    UNIQUE (job_id, source, source_sequence)
);

CREATE TABLE recovery_cases (
    case_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('open', 'investigating', 'resolved', 'needs_support')
    ),
    resolution_version INTEGER NOT NULL DEFAULT 0 CHECK (resolution_version >= 0),
    execution_binding_digest TEXT NOT NULL,
    intent_id TEXT NOT NULL REFERENCES execution_intents(intent_id),
    consent_id TEXT REFERENCES consents(consent_id),
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    receipt_id TEXT REFERENCES cad_execution_receipts(receipt_id),
    evidence_event_ids_json TEXT NOT NULL,
    missing_evidence_json TEXT NOT NULL,
    latest_query_result_json TEXT,
    current_state_json TEXT NOT NULL,
    safe_actions_json TEXT NOT NULL,
    resolution TEXT CHECK (
        resolution IS NULL OR resolution IN (
            'exact_receipt_materialized', 'proven_no_effect', 'rolled_back',
            'unresolved', 'support_required'
        )
    ),
    operator_notes_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    CHECK (
        (state = 'resolved' AND resolution IS NOT NULL AND resolved_at IS NOT NULL)
        OR (state <> 'resolved' AND resolution IS NULL AND resolved_at IS NULL)
    )
);

CREATE TABLE rollback_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    original_receipt_id TEXT NOT NULL UNIQUE
        REFERENCES cad_execution_receipts(receipt_id),
    original_receipt_digest TEXT NOT NULL,
    program_id TEXT NOT NULL,
    program_revision INTEGER NOT NULL,
    program_digest TEXT NOT NULL,
    preview_id TEXT NOT NULL REFERENCES cad_previews(preview_id),
    preview_digest TEXT NOT NULL,
    execution_digest TEXT NOT NULL,
    document_id TEXT NOT NULL,
    document_revision_before TEXT NOT NULL,
    document_revision_after TEXT NOT NULL,
    created_entities_json TEXT NOT NULL,
    non_entity_object_created INTEGER NOT NULL CHECK (non_entity_object_created IN (0, 1)),
    runtime_pins_json TEXT NOT NULL,
    policy_pins_json TEXT NOT NULL,
    checkpoint_digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY (program_id, program_revision)
        REFERENCES cad_program_revisions(program_id, revision)
);

CREATE TABLE rollback_plans (
    plan_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL REFERENCES rollback_checkpoints(checkpoint_id),
    checkpoint_digest TEXT NOT NULL,
    original_receipt_id TEXT NOT NULL REFERENCES cad_execution_receipts(receipt_id),
    document_id TEXT NOT NULL,
    current_document_revision TEXT NOT NULL,
    rollback_execution_digest TEXT NOT NULL UNIQUE,
    entity_handles_json TEXT NOT NULL,
    conflicts_json TEXT NOT NULL,
    eligible INTEGER NOT NULL CHECK (eligible IN (0, 1)),
    runtime_pins_json TEXT NOT NULL,
    policy_pins_json TEXT NOT NULL,
    plan_digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE (checkpoint_id, current_document_revision)
);

CREATE TABLE rollback_receipts (
    rollback_receipt_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    original_receipt_id TEXT NOT NULL REFERENCES cad_execution_receipts(receipt_id),
    original_receipt_digest TEXT NOT NULL,
    program_digest TEXT NOT NULL,
    original_execution_digest TEXT NOT NULL,
    original_document_revision TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL REFERENCES rollback_checkpoints(checkpoint_id),
    checkpoint_digest TEXT NOT NULL,
    rollback_plan_id TEXT NOT NULL UNIQUE REFERENCES rollback_plans(plan_id),
    rollback_plan_digest TEXT NOT NULL,
    rollback_job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
    rollback_execution_digest TEXT NOT NULL UNIQUE,
    document_id TEXT NOT NULL,
    document_revision_before TEXT NOT NULL,
    document_revision_after TEXT NOT NULL,
    removed_entities_json TEXT NOT NULL,
    runtime_pins_json TEXT NOT NULL,
    policy_pins_json TEXT NOT NULL,
    receipt_digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TRIGGER execution_intents_immutable_binding
BEFORE UPDATE ON execution_intents
WHEN
    NEW.intent_version IS NOT OLD.intent_version
    OR NEW.owner_subject IS NOT OLD.owner_subject
    OR NEW.actor_issuer IS NOT OLD.actor_issuer
    OR NEW.actor_subject IS NOT OLD.actor_subject
    OR NEW.action IS NOT OLD.action
    OR NEW.device_id IS NOT OLD.device_id
    OR NEW.device_identity_generation IS NOT OLD.device_identity_generation
    OR NEW.device_key_thumbprint IS NOT OLD.device_key_thumbprint
    OR NEW.document_id IS NOT OLD.document_id
    OR NEW.expected_document_revision IS NOT OLD.expected_document_revision
    OR NEW.program_id IS NOT OLD.program_id
    OR NEW.program_revision IS NOT OLD.program_revision
    OR NEW.program_digest IS NOT OLD.program_digest
    OR NEW.preview_id IS NOT OLD.preview_id
    OR NEW.preview_digest IS NOT OLD.preview_digest
    OR NEW.preview_execution_digest IS NOT OLD.preview_execution_digest
    OR NEW.preview_expires_at IS NOT OLD.preview_expires_at
    OR NEW.deterministic_receipt_id IS NOT OLD.deterministic_receipt_id
    OR NEW.commit_execution_digest IS NOT OLD.commit_execution_digest
    OR NEW.runtime_pins_json IS NOT OLD.runtime_pins_json
    OR NEW.policy_pins_json IS NOT OLD.policy_pins_json
    OR NEW.risk_class IS NOT OLD.risk_class
    OR NEW.required_assurance IS NOT OLD.required_assurance
    OR NEW.trusted_effect_summary_json IS NOT OLD.trusted_effect_summary_json
    OR NEW.idempotency_key IS NOT OLD.idempotency_key
    OR NEW.request_hash IS NOT OLD.request_hash
    OR NEW.intent_digest IS NOT OLD.intent_digest
    OR NEW.created_at IS NOT OLD.created_at
    OR NEW.expires_at IS NOT OLD.expires_at
BEGIN
    SELECT RAISE(ABORT, 'execution_intent_immutable');
END;

CREATE TRIGGER execution_evidence_events_no_update
BEFORE UPDATE ON execution_evidence_events
BEGIN
    SELECT RAISE(ABORT, 'execution_evidence_append_only');
END;

CREATE TRIGGER execution_evidence_events_no_delete
BEFORE DELETE ON execution_evidence_events
BEGIN
    SELECT RAISE(ABORT, 'execution_evidence_append_only');
END;

CREATE INDEX idx_intents_owner_state
ON execution_intents(owner_subject, state, created_at);

CREATE INDEX idx_consents_owner_state
ON consents(owner_subject, state, requested_at);

CREATE INDEX idx_evidence_owner_job
ON execution_evidence_events(owner_subject, job_id, source, source_sequence);

CREATE INDEX idx_recovery_owner_state
ON recovery_cases(owner_subject, state, updated_at);

CREATE INDEX idx_checkpoints_owner_receipt
ON rollback_checkpoints(owner_subject, original_receipt_id);

CREATE INDEX idx_plans_owner_checkpoint
ON rollback_plans(owner_subject, checkpoint_id, created_at);

CREATE INDEX idx_rollback_receipts_owner_original
ON rollback_receipts(owner_subject, original_receipt_id);
