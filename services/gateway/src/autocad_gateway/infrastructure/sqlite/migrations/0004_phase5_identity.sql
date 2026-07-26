CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE (issuer, subject)
);

CREATE TABLE device_credentials (
    device_id TEXT PRIMARY KEY REFERENCES devices(device_id) ON DELETE CASCADE,
    public_key TEXT NOT NULL,
    key_fingerprint TEXT NOT NULL UNIQUE,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE pairing_sessions (
    pairing_id TEXT PRIMARY KEY,
    user_code_hash TEXT NOT NULL UNIQUE,
    device_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    public_key TEXT NOT NULL,
    challenge_hash TEXT NOT NULL,
    polling_secret_hash TEXT NOT NULL,
    owner_user_id TEXT REFERENCES users(user_id),
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'approved', 'completed', 'denied', 'expired')
    ),
    attempts INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE device_challenges (
    challenge_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    challenge_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE agent_access_tokens (
    token_hash TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE security_audit (
    audit_id TEXT PRIMARY KEY,
    owner_user_id TEXT,
    device_id TEXT,
    event_type TEXT NOT NULL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_pairing_code ON pairing_sessions(user_code_hash, state);
CREATE INDEX idx_challenges_device ON device_challenges(device_id, used_at);
CREATE INDEX idx_audit_owner_created ON security_audit(owner_user_id, created_at);
