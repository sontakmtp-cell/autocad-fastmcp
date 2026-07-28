CREATE TABLE phase8_program_revisions (
    program_id TEXT NOT NULL REFERENCES cad_programs(program_id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    owner_subject TEXT NOT NULL,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    document_id TEXT NOT NULL,
    source_snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
    expected_document_revision TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK (schema_version = 'cad.program/1.0'),
    source_digest TEXT NOT NULL,
    semantic_digest TEXT NOT NULL,
    source_json TEXT NOT NULL,
    lineage_kind TEXT NOT NULL CHECK (
        lineage_kind IN ('root', 'patch', 'rebase', 'conflict_resolution')
    ),
    parent_revision INTEGER,
    base_revision INTEGER,
    lineage_request_digest TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (program_id, revision),
    UNIQUE (owner_subject, source_digest),
    FOREIGN KEY (program_id, parent_revision)
        REFERENCES phase8_program_revisions(program_id, revision),
    FOREIGN KEY (program_id, base_revision)
        REFERENCES phase8_program_revisions(program_id, revision),
    CHECK (
        (lineage_kind = 'root' AND revision = 1
            AND parent_revision IS NULL AND base_revision IS NULL)
        OR
        (lineage_kind <> 'root' AND revision > 1
            AND parent_revision IS NOT NULL)
    )
);

CREATE TABLE phase8_execution_plans (
    plan_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    program_id TEXT NOT NULL,
    program_revision INTEGER NOT NULL,
    schema_version TEXT NOT NULL CHECK (schema_version = 'cad.execution-plan/1'),
    source_digest TEXT NOT NULL,
    semantic_digest TEXT NOT NULL,
    compiler_id TEXT NOT NULL,
    compiler_version TEXT NOT NULL,
    compiler_hash TEXT NOT NULL,
    plan_digest TEXT NOT NULL UNIQUE,
    expansion_digest TEXT NOT NULL,
    effect_digest TEXT NOT NULL,
    target_set_digest TEXT NOT NULL,
    reference_digest TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    effect_manifest_json TEXT NOT NULL,
    trusted_effect_summary_json TEXT NOT NULL,
    risk_class TEXT NOT NULL CHECK (
        risk_class IN ('low', 'medium', 'high', 'destructive')
    ),
    hard_budgets_json TEXT NOT NULL,
    required_capabilities_json TEXT NOT NULL,
    operation_packs_json TEXT NOT NULL,
    validation_profiles_json TEXT NOT NULL,
    runtime_pins_json TEXT NOT NULL,
    checkpoint_strategy TEXT NOT NULL CHECK (
        checkpoint_strategy IN (
            'none', 'cad.rollback.checkpoint/1', 'cad.rollback.checkpoint/2'
        )
    ),
    create_count INTEGER NOT NULL CHECK (create_count >= 0),
    modify_count INTEGER NOT NULL CHECK (modify_count >= 0),
    erase_count INTEGER NOT NULL CHECK (erase_count >= 0),
    rollout_policy_digest TEXT NOT NULL,
    rollout_policy_epoch INTEGER NOT NULL CHECK (rollout_policy_epoch >= 1),
    sealed_at TEXT NOT NULL,
    UNIQUE (program_id, program_revision),
    FOREIGN KEY (program_id, program_revision)
        REFERENCES phase8_program_revisions(program_id, revision)
);

CREATE TABLE phase8_plan_invalidations (
    invalidation_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    plan_id TEXT NOT NULL REFERENCES phase8_execution_plans(plan_id),
    reason TEXT NOT NULL CHECK (
        reason IN (
            'compiler_changed', 'registry_changed', 'runtime_changed',
            'policy_changed', 'capability_changed', 'feature_disabled'
        )
    ),
    observed_binding_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (plan_id, reason, observed_binding_digest)
);

CREATE TABLE phase8_materialized_refs (
    materialized_ref_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    plan_id TEXT NOT NULL REFERENCES phase8_execution_plans(plan_id),
    snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    document_id TEXT NOT NULL,
    document_revision TEXT NOT NULL,
    ref_kind TEXT NOT NULL CHECK (
        ref_kind IN ('query_result', 'snapshot_entity', 'prior_output', 'component')
    ),
    query_digest TEXT,
    result_digest TEXT NOT NULL,
    fingerprint_digest TEXT NOT NULL,
    target_set_digest TEXT NOT NULL,
    reference_digest TEXT NOT NULL,
    materialized_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (owner_subject, plan_id, result_digest)
);

CREATE TABLE phase8_conflict_reports (
    conflict_report_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    program_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    candidate_revision INTEGER NOT NULL,
    request_kind TEXT NOT NULL CHECK (request_kind IN ('patch', 'rebase')),
    old_snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
    new_snapshot_id TEXT REFERENCES snapshots(snapshot_id),
    request_digest TEXT NOT NULL,
    conflicts_digest TEXT NOT NULL,
    conflicts_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (owner_subject, program_id, candidate_revision, request_digest),
    FOREIGN KEY (program_id, source_revision)
        REFERENCES phase8_program_revisions(program_id, revision),
    FOREIGN KEY (program_id, candidate_revision)
        REFERENCES phase8_program_revisions(program_id, revision)
);

CREATE TABLE phase8_conflict_events (
    conflict_report_id TEXT NOT NULL
        REFERENCES phase8_conflict_reports(conflict_report_id),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    owner_subject TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('open', 'resolved', 'abandoned')),
    resolution_revision INTEGER,
    event_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (conflict_report_id, sequence),
    CHECK (
        (state = 'resolved' AND resolution_revision IS NOT NULL)
        OR (state <> 'resolved' AND resolution_revision IS NULL)
    )
);

CREATE TABLE phase8_revision_usage_events (
    usage_event_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    plan_id TEXT NOT NULL REFERENCES phase8_execution_plans(plan_id),
    state TEXT NOT NULL CHECK (
        state IN (
            'previewed', 'intent_created', 'consent_created', 'released',
            'dispatched', 'running', 'outcome_unknown', 'terminal'
        )
    ),
    external_id TEXT NOT NULL,
    binding_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (plan_id, state, external_id)
);

CREATE TABLE phase8_capability_evidence (
    evidence_id TEXT PRIMARY KEY,
    evidence_authority TEXT NOT NULL CHECK (
        evidence_authority = 'gateway_server'
    ),
    owner_subject TEXT NOT NULL,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    capability_key TEXT NOT NULL,
    operation_pack TEXT NOT NULL,
    runtime_id TEXT NOT NULL,
    host_family TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    support_state TEXT NOT NULL CHECK (
        support_state IN (
            'unsupported', 'contract_only', 'preview_only',
            'lab_commit', 'certified'
        )
    ),
    package_hash TEXT NOT NULL,
    capability_manifest_hash TEXT NOT NULL,
    operation_registry_hash TEXT NOT NULL,
    package_signature_verified INTEGER NOT NULL
        CHECK (package_signature_verified IN (0, 1)),
    agent_evidence_digest TEXT NOT NULL,
    host_evidence_digest TEXT NOT NULL,
    cohort TEXT NOT NULL,
    evidence_version TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    evidence_digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE (
        owner_subject, device_id, capability_key, operation_pack,
        runtime_id, host_family, entity_type, cohort, evidence_version
    )
);

CREATE TABLE phase8_intent_bindings (
    intent_id TEXT PRIMARY KEY REFERENCES execution_intents(intent_id),
    owner_subject TEXT NOT NULL,
    plan_id TEXT NOT NULL REFERENCES phase8_execution_plans(plan_id),
    source_digest TEXT NOT NULL,
    semantic_digest TEXT NOT NULL,
    plan_digest TEXT NOT NULL,
    expansion_digest TEXT NOT NULL,
    effect_digest TEXT NOT NULL,
    target_set_digest TEXT NOT NULL,
    reference_digest TEXT NOT NULL,
    compiler_hash TEXT NOT NULL,
    risk_class TEXT NOT NULL CHECK (
        risk_class IN ('low', 'medium', 'high', 'destructive')
    ),
    trusted_effect_summary_json TEXT NOT NULL,
    rollout_policy_digest TEXT NOT NULL,
    rollout_policy_epoch INTEGER NOT NULL CHECK (rollout_policy_epoch >= 1),
    binding_digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE phase8_consent_bindings (
    consent_id TEXT PRIMARY KEY REFERENCES consents(consent_id),
    owner_subject TEXT NOT NULL,
    intent_id TEXT NOT NULL REFERENCES phase8_intent_bindings(intent_id),
    binding_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (intent_id, consent_id)
);

CREATE TRIGGER phase8_program_revisions_no_update
BEFORE UPDATE ON phase8_program_revisions
BEGIN
    SELECT RAISE(ABORT, 'phase8_program_revision_immutable');
END;

CREATE TRIGGER phase8_program_revisions_no_delete
BEFORE DELETE ON phase8_program_revisions
BEGIN
    SELECT RAISE(ABORT, 'phase8_program_revision_immutable');
END;

CREATE TRIGGER phase8_execution_plans_no_update
BEFORE UPDATE ON phase8_execution_plans
BEGIN
    SELECT RAISE(ABORT, 'phase8_execution_plan_immutable');
END;

CREATE TRIGGER phase8_execution_plans_no_delete
BEFORE DELETE ON phase8_execution_plans
BEGIN
    SELECT RAISE(ABORT, 'phase8_execution_plan_immutable');
END;

CREATE TRIGGER phase8_plan_invalidations_no_update
BEFORE UPDATE ON phase8_plan_invalidations
BEGIN
    SELECT RAISE(ABORT, 'phase8_plan_invalidation_append_only');
END;

CREATE TRIGGER phase8_plan_invalidations_no_delete
BEFORE DELETE ON phase8_plan_invalidations
BEGIN
    SELECT RAISE(ABORT, 'phase8_plan_invalidation_append_only');
END;

CREATE TRIGGER phase8_materialized_refs_no_update
BEFORE UPDATE ON phase8_materialized_refs
BEGIN
    SELECT RAISE(ABORT, 'phase8_materialized_ref_immutable');
END;

CREATE TRIGGER phase8_materialized_refs_no_delete
BEFORE DELETE ON phase8_materialized_refs
BEGIN
    SELECT RAISE(ABORT, 'phase8_materialized_ref_immutable');
END;

CREATE TRIGGER phase8_conflict_reports_no_update
BEFORE UPDATE ON phase8_conflict_reports
BEGIN
    SELECT RAISE(ABORT, 'phase8_conflict_report_immutable');
END;

CREATE TRIGGER phase8_conflict_reports_no_delete
BEFORE DELETE ON phase8_conflict_reports
BEGIN
    SELECT RAISE(ABORT, 'phase8_conflict_report_immutable');
END;

CREATE TRIGGER phase8_conflict_events_no_update
BEFORE UPDATE ON phase8_conflict_events
BEGIN
    SELECT RAISE(ABORT, 'phase8_conflict_event_append_only');
END;

CREATE TRIGGER phase8_conflict_events_no_delete
BEFORE DELETE ON phase8_conflict_events
BEGIN
    SELECT RAISE(ABORT, 'phase8_conflict_event_append_only');
END;

CREATE TRIGGER phase8_revision_usage_events_no_update
BEFORE UPDATE ON phase8_revision_usage_events
BEGIN
    SELECT RAISE(ABORT, 'phase8_revision_usage_append_only');
END;

CREATE TRIGGER phase8_revision_usage_events_no_delete
BEFORE DELETE ON phase8_revision_usage_events
BEGIN
    SELECT RAISE(ABORT, 'phase8_revision_usage_append_only');
END;

CREATE TRIGGER phase8_capability_evidence_no_update
BEFORE UPDATE ON phase8_capability_evidence
BEGIN
    SELECT RAISE(ABORT, 'phase8_capability_evidence_append_only');
END;

CREATE TRIGGER phase8_capability_evidence_no_delete
BEFORE DELETE ON phase8_capability_evidence
BEGIN
    SELECT RAISE(ABORT, 'phase8_capability_evidence_append_only');
END;

CREATE TRIGGER phase8_intent_bindings_no_update
BEFORE UPDATE ON phase8_intent_bindings
BEGIN
    SELECT RAISE(ABORT, 'phase8_intent_binding_immutable');
END;

CREATE TRIGGER phase8_intent_bindings_no_delete
BEFORE DELETE ON phase8_intent_bindings
BEGIN
    SELECT RAISE(ABORT, 'phase8_intent_binding_immutable');
END;

CREATE TRIGGER phase8_consent_bindings_no_update
BEFORE UPDATE ON phase8_consent_bindings
BEGIN
    SELECT RAISE(ABORT, 'phase8_consent_binding_immutable');
END;

CREATE TRIGGER phase8_consent_bindings_no_delete
BEFORE DELETE ON phase8_consent_bindings
BEGIN
    SELECT RAISE(ABORT, 'phase8_consent_binding_immutable');
END;

CREATE INDEX idx_phase8_revisions_owner_program
ON phase8_program_revisions(owner_subject, program_id, revision);

CREATE INDEX idx_phase8_plans_owner_revision
ON phase8_execution_plans(owner_subject, program_id, program_revision);

CREATE INDEX idx_phase8_refs_owner_snapshot
ON phase8_materialized_refs(owner_subject, snapshot_id);

CREATE INDEX idx_phase8_conflicts_owner_program
ON phase8_conflict_reports(owner_subject, program_id, candidate_revision);

CREATE INDEX idx_phase8_capabilities_admission
ON phase8_capability_evidence(
    owner_subject, device_id, capability_key, operation_pack,
    runtime_id, host_family, cohort, support_state
);
