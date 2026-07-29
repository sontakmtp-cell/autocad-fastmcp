CREATE TABLE workflow_control_commands (
    owner_subject TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
    idempotency_key TEXT NOT NULL,
    action TEXT NOT NULL,
    expected_state_version INTEGER NOT NULL CHECK (expected_state_version >= 0),
    payload_json TEXT NOT NULL CHECK (length(payload_json) <= 65536),
    payload_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('started','completed')),
    result_json TEXT CHECK (result_json IS NULL OR length(result_json) <= 65536),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (owner_subject, idempotency_key)
);

CREATE INDEX idx_workflow_control_commands_run
ON workflow_control_commands(run_id, created_at);
