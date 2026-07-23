BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnoses (
    diagnosis_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    rehearsal INTEGER NOT NULL CHECK (rehearsal IN (0, 1)),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0)
);

CREATE INDEX IF NOT EXISTS idx_diagnoses_rehearsal
    ON diagnoses (rehearsal, created_at);

CREATE TABLE IF NOT EXISTS knowledge_outbox (
    publication_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE,
    diagnosis_id TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'published', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (diagnosis_id) REFERENCES diagnoses (diagnosis_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_knowledge_outbox_delivery
    ON knowledge_outbox (status, created_at);

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

COMMIT;
