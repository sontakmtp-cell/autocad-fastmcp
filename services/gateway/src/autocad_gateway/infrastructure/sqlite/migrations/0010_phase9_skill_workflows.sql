-- Phase 9 is additive.  Disabling its flags is the rollback mechanism; these
-- audit records must never be destructively downgraded.
CREATE TABLE skill_versions (
    skill_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'deprecated', 'withdrawn', 'security_revoked')),
    manifest_json TEXT NOT NULL CHECK (length(manifest_json) <= 65536),
    manifest_digest TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    workflow_digest TEXT NOT NULL,
    guide_digest TEXT NOT NULL,
    catalog_release_digest TEXT NOT NULL,
    published_at TEXT,
    deprecated_at TEXT,
    withdrawn_at TEXT,
    security_revoked_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (skill_id, version),
    UNIQUE (manifest_digest),
    CHECK ((status = 'published' AND published_at IS NOT NULL) OR status <> 'published')
);

CREATE TRIGGER skill_versions_immutable_binding
BEFORE UPDATE ON skill_versions
WHEN NEW.skill_id IS NOT OLD.skill_id OR NEW.version IS NOT OLD.version
 OR NEW.manifest_json IS NOT OLD.manifest_json OR NEW.manifest_digest IS NOT OLD.manifest_digest
 OR NEW.workflow_id IS NOT OLD.workflow_id OR NEW.workflow_version IS NOT OLD.workflow_version
 OR NEW.workflow_digest IS NOT OLD.workflow_digest OR NEW.guide_digest IS NOT OLD.guide_digest
 OR NEW.catalog_release_digest IS NOT OLD.catalog_release_digest OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'skill_version_immutable'); END;

CREATE TABLE skill_channels (
    skill_id TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('default', 'preview')),
    default_version TEXT NOT NULL,
    epoch INTEGER NOT NULL CHECK (epoch >= 0),
    status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (skill_id, channel),
    FOREIGN KEY (skill_id, default_version) REFERENCES skill_versions(skill_id, version)
);

CREATE TABLE skill_publication_events (
    event_id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    version TEXT NOT NULL,
    previous_status TEXT,
    status TEXT NOT NULL CHECK (status IN ('published','deprecated','withdrawn','security_revoked','promoted')),
    channel TEXT,
    channel_epoch INTEGER,
    operator_subject TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (skill_id, version) REFERENCES skill_versions(skill_id, version)
);
CREATE INDEX idx_skill_publication_events_skill ON skill_publication_events(skill_id, version, created_at);

CREATE TABLE workflow_definitions (
    workflow_id TEXT NOT NULL,
    version TEXT NOT NULL,
    definition_json TEXT NOT NULL CHECK (length(definition_json) <= 65536),
    definition_digest TEXT NOT NULL,
    step_count INTEGER NOT NULL CHECK (step_count BETWEEN 1 AND 64),
    planner_refs_json TEXT NOT NULL CHECK (length(planner_refs_json) <= 65536),
    template_refs_json TEXT NOT NULL CHECK (length(template_refs_json) <= 65536),
    created_at TEXT NOT NULL,
    PRIMARY KEY (workflow_id, version),
    UNIQUE (definition_digest)
);

CREATE TRIGGER workflow_definitions_immutable
BEFORE UPDATE ON workflow_definitions
BEGIN SELECT RAISE(ABORT, 'workflow_definition_immutable'); END;

CREATE TABLE workflow_runs (
    run_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    actor_issuer TEXT NOT NULL,
    actor_subject TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    skill_version TEXT NOT NULL,
    skill_digest TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    workflow_digest TEXT NOT NULL,
    catalog_epoch INTEGER NOT NULL CHECK (catalog_epoch >= 0),
    policy_epoch INTEGER NOT NULL CHECK (policy_epoch >= 0),
    planner_registry_version TEXT NOT NULL,
    planner_registry_hash TEXT NOT NULL,
    pins_json TEXT NOT NULL CHECK (length(pins_json) <= 65536),
    pins_digest TEXT NOT NULL,
    inputs_json TEXT NOT NULL CHECK (length(inputs_json) <= 65536),
    inputs_digest TEXT NOT NULL,
    device_id TEXT NOT NULL,
    device_identity_generation INTEGER NOT NULL CHECK (device_identity_generation >= 1),
    initial_snapshot_id TEXT,
    initial_document_id TEXT,
    initial_document_revision TEXT,
    state TEXT NOT NULL CHECK (state IN ('created','running','waiting_for_user','waiting_for_program_revision','waiting_for_trusted_approval','waiting_for_job','waiting_for_recovery','paused','succeeded','failed','cancelled','needs_attention')),
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    current_step_id TEXT,
    result_json TEXT CHECK (result_json IS NULL OR length(result_json) <= 65536),
    result_digest TEXT,
    error_json TEXT CHECK (error_json IS NULL OR length(error_json) <= 65536),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE (owner_subject, idempotency_key),
    FOREIGN KEY (skill_id, skill_version) REFERENCES skill_versions(skill_id, version),
    FOREIGN KEY (workflow_id, workflow_version) REFERENCES workflow_definitions(workflow_id, version)
);

CREATE TRIGGER workflow_runs_immutable_pins
BEFORE UPDATE ON workflow_runs
WHEN NEW.owner_subject IS NOT OLD.owner_subject OR NEW.actor_issuer IS NOT OLD.actor_issuer
 OR NEW.actor_subject IS NOT OLD.actor_subject OR NEW.skill_id IS NOT OLD.skill_id
 OR NEW.skill_version IS NOT OLD.skill_version OR NEW.skill_digest IS NOT OLD.skill_digest
 OR NEW.workflow_id IS NOT OLD.workflow_id OR NEW.workflow_version IS NOT OLD.workflow_version
 OR NEW.workflow_digest IS NOT OLD.workflow_digest OR NEW.catalog_epoch IS NOT OLD.catalog_epoch
 OR NEW.policy_epoch IS NOT OLD.policy_epoch OR NEW.planner_registry_version IS NOT OLD.planner_registry_version
 OR NEW.planner_registry_hash IS NOT OLD.planner_registry_hash OR NEW.pins_json IS NOT OLD.pins_json
 OR NEW.pins_digest IS NOT OLD.pins_digest OR NEW.inputs_json IS NOT OLD.inputs_json OR NEW.inputs_digest IS NOT OLD.inputs_digest
 OR NEW.device_id IS NOT OLD.device_id OR NEW.device_identity_generation IS NOT OLD.device_identity_generation
 OR NEW.initial_snapshot_id IS NOT OLD.initial_snapshot_id OR NEW.initial_document_id IS NOT OLD.initial_document_id
 OR NEW.initial_document_revision IS NOT OLD.initial_document_revision OR NEW.idempotency_key IS NOT OLD.idempotency_key
 OR NEW.request_hash IS NOT OLD.request_hash OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'workflow_run_pins_immutable'); END;

CREATE TABLE workflow_steps (
    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
    step_id TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    kind TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending','ready','dispatch_pending','running','waiting','succeeded','failed','skipped','cancelled','needs_attention')),
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    input_ref_json TEXT CHECK (input_ref_json IS NULL OR length(input_ref_json) <= 65536),
    output_ref_json TEXT CHECK (output_ref_json IS NULL OR length(output_ref_json) <= 65536),
    child_program_id TEXT,
    child_program_revision INTEGER,
    child_preview_id TEXT,
    child_intent_id TEXT,
    child_job_id TEXT,
    child_receipt_id TEXT,
    child_recovery_id TEXT,
    error_code TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, step_id, attempt)
);

CREATE TABLE workflow_actions (
    action_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    action_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (length(payload_json) <= 65536),
    payload_digest TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    retry_class TEXT NOT NULL,
    effect_class TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending','claimed','started','outcome_unknown','completed','failed','needs_attention','cancelled')),
    lease_owner TEXT,
    lease_expires_at TEXT,
    dispatch_started_at TEXT,
    child_state TEXT,
    child_ref_json TEXT CHECK (child_ref_json IS NULL OR length(child_ref_json) <= 65536),
    result_json TEXT CHECK (result_json IS NULL OR length(result_json) <= 65536),
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (run_id, step_id, attempt, action_kind),
    UNIQUE (run_id, idempotency_key),
    FOREIGN KEY (run_id, step_id, attempt) REFERENCES workflow_steps(run_id, step_id, attempt)
);

CREATE TABLE workflow_waits (
    wait_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
    step_id TEXT NOT NULL,
    wait_kind TEXT NOT NULL CHECK (wait_kind IN ('user_input','program_revision','trusted_approval','job','recovery')),
    expected_state_version INTEGER NOT NULL CHECK (expected_state_version >= 0),
    response_schema_json TEXT NOT NULL CHECK (length(response_schema_json) <= 65536),
    response_schema_digest TEXT NOT NULL,
    expires_at TEXT,
    resolved_at TEXT,
    resolution_json TEXT CHECK (resolution_json IS NULL OR length(resolution_json) <= 65536),
    created_at TEXT NOT NULL
);

CREATE TABLE workflow_events (
    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (length(payload_json) <= 65536),
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
);

CREATE INDEX idx_skill_versions_status ON skill_versions(status, skill_id, version);
CREATE INDEX idx_workflow_runs_owner_state ON workflow_runs(owner_subject, state, updated_at);
CREATE INDEX idx_workflow_runs_nonterminal ON workflow_runs(state, updated_at) WHERE state NOT IN ('succeeded','failed','cancelled');
CREATE INDEX idx_workflow_actions_pending ON workflow_actions(state, lease_expires_at, created_at) WHERE state IN ('pending','claimed');
CREATE INDEX idx_workflow_waits_expiry ON workflow_waits(resolved_at, expires_at);
CREATE INDEX idx_workflow_events_run_sequence ON workflow_events(run_id, sequence);

CREATE TRIGGER workflow_events_append_only_update
BEFORE UPDATE ON workflow_events
BEGIN SELECT RAISE(ABORT, 'workflow_event_append_only'); END;

CREATE TRIGGER workflow_events_append_only_delete
BEFORE DELETE ON workflow_events
BEGIN SELECT RAISE(ABORT, 'workflow_event_append_only'); END;
