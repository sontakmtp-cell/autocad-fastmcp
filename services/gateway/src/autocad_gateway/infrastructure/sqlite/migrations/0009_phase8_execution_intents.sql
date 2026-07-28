PRAGMA foreign_keys = OFF;

PRAGMA legacy_alter_table = ON;

ALTER TABLE execution_intents RENAME TO execution_intents_legacy;

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
    preview_id TEXT NOT NULL,
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
    UNIQUE (owner_subject, idempotency_key),
    UNIQUE (owner_subject, intent_digest),
    CHECK (
        (state = 'released' AND released_job_id IS NOT NULL)
        OR (state <> 'released' AND released_job_id IS NULL)
    )
);

INSERT INTO execution_intents
SELECT * FROM execution_intents_legacy;

DROP TABLE execution_intents_legacy;

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

CREATE INDEX idx_intents_owner_state
ON execution_intents(owner_subject, state, created_at);

PRAGMA legacy_alter_table = OFF;
