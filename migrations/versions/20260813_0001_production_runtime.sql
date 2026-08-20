BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('QUEUED','RUNNING','PAUSED','COMPLETED','FAILED','CANCELLED')),
    run_version INTEGER NOT NULL,
    command_json JSONB NOT NULL,
    result_json JSONB,
    proposal_json JSONB,
    create_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    response_json JSONB NOT NULL,
    PRIMARY KEY(run_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS run_events (
    event_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    run_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_run_events_replay ON run_events(run_id,event_id);

CREATE TABLE IF NOT EXISTS outbox_events (
    outbox_id BIGSERIAL PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_events(outbox_id) WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS run_jobs (
    job_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'READY' CHECK (status IN ('READY','LEASED','DONE','FAILED','CANCELLED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_run_jobs_claim ON run_jobs(status,available_at,job_id);

CREATE TABLE IF NOT EXISTS preference_memory (
    user_id TEXT PRIMARY KEY,
    items_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS history_documents (
    user_id TEXT PRIMARY KEY,
    collection_version INTEGER NOT NULL DEFAULT 0,
    items_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    feedback_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    deleted_run_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS operation_idempotency (
    user_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    response_json JSONB NOT NULL,
    PRIMARY KEY(user_id,operation,idempotency_key)
);
CREATE TABLE IF NOT EXISTS conversation_documents (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    conversation_version INTEGER NOT NULL DEFAULT 0,
    messages_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversation_documents_user ON conversation_documents(user_id,updated_at DESC,conversation_id DESC);
CREATE TABLE IF NOT EXISTS conversation_operation_idempotency (
    scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    response_json JSONB NOT NULL,
    PRIMARY KEY(scope,idempotency_key)
);

INSERT INTO schema_migrations(version) VALUES('20260813_0001') ON CONFLICT(version) DO NOTHING;
COMMIT;
