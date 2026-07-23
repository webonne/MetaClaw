BEGIN IMMEDIATE;

ALTER TABLE knowledge_outbox ADD COLUMN claimed_by TEXT;
ALTER TABLE knowledge_outbox ADD COLUMN lease_expires_at TEXT;

CREATE INDEX IF NOT EXISTS idx_knowledge_outbox_claim
    ON knowledge_outbox (status, lease_expires_at, created_at);

INSERT INTO schema_migrations (version, applied_at)
VALUES (2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

COMMIT;
