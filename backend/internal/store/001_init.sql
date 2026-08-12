-- Multi-Agent Automated SRE — Go/SQL schema (replaces Prisma + pgvector).
-- Migrations are applied idempotently by the gateway on startup.

CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Latest live agent state per incident (absorbe'd from `memory.snapshot`).
CREATE TABLE IF NOT EXISTS incident_states (
    incident_id TEXT PRIMARY KEY,
    state       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Durable ROI ledger. Mirrors legacy PostgresROILedger; history survives
-- restarts so summary + token spend are never lost.
CREATE TABLE IF NOT EXISTS roi_ledger (
    incident_id        TEXT PRIMARY KEY,
    status             TEXT NOT NULL,
    duration_seconds   DOUBLE PRECISION NOT NULL,
    human_mttr_seconds INTEGER NOT NULL,
    input_tokens       INTEGER NOT NULL,
    output_tokens      INTEGER NOT NULL,
    token_cost         DOUBLE PRECISION NOT NULL,
    saved_seconds      DOUBLE PRECISION NOT NULL,
    saved_amount       DOUBLE PRECISION NOT NULL,
    recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Time-travel / snapshot engine: every graph step persisted as a full state
-- blob, addressable by ref for replay + fork.
CREATE TABLE IF NOT EXISTS snapshots (
    ref         TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    step_index  INTEGER NOT NULL,
    node        TEXT NOT NULL,
    summary     TEXT NOT NULL,
    status      TEXT NOT NULL,
    blob        JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_snapshots_incident ON snapshots (incident_id, step_index);
