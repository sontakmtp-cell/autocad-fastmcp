ALTER TABLE phase8_capability_evidence ADD COLUMN issued_at TEXT;

UPDATE phase8_capability_evidence
SET issued_at = created_at
WHERE issued_at IS NULL;

CREATE TABLE phase8_previews (
    preview_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    plan_id TEXT NOT NULL REFERENCES phase8_execution_plans(plan_id),
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    execution_binding_json TEXT NOT NULL,
    execution_binding_digest TEXT NOT NULL UNIQUE,
    capability_evidence_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (owner_subject, plan_id, idempotency_key)
);

CREATE TRIGGER phase8_previews_no_update
BEFORE UPDATE ON phase8_previews
BEGIN
    SELECT RAISE(ABORT, 'phase8_preview_immutable');
END;

CREATE TRIGGER phase8_previews_no_delete
BEFORE DELETE ON phase8_previews
BEGIN
    SELECT RAISE(ABORT, 'phase8_preview_immutable');
END;

CREATE INDEX idx_phase8_previews_owner_plan
ON phase8_previews(owner_subject, plan_id, created_at);
