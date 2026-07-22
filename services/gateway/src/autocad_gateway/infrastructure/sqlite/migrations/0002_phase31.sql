ALTER TABLE devices ADD COLUMN capability_hash TEXT;

ALTER TABLE agent_sessions ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE agent_sessions ADD COLUMN capability_hash TEXT;

ALTER TABLE jobs ADD COLUMN request_fingerprint TEXT;
ALTER TABLE jobs ADD COLUMN last_agent_sequence INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN cancel_requested_at TEXT;

-- Phase 3 allowed more than one durable active row during connection replacement.
-- Keep the newest row active before enforcing the Phase 3.1 invariant. Gateway
-- startup disconnects that remaining pre-restart row immediately after migration.
UPDATE agent_sessions
SET disconnected_at = CURRENT_TIMESTAMP
WHERE disconnected_at IS NULL
  AND EXISTS (
      SELECT 1
      FROM agent_sessions AS newer
      WHERE newer.device_id = agent_sessions.device_id
        AND newer.disconnected_at IS NULL
        AND (
            newer.connected_at > agent_sessions.connected_at
            OR (
                newer.connected_at = agent_sessions.connected_at
                AND newer.session_id > agent_sessions.session_id
            )
        )
  );

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_one_active_device
ON agent_sessions(device_id)
WHERE disconnected_at IS NULL;
