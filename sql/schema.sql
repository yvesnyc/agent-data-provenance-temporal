-- Source tables (written by humans / upstream systems) ----------------------

CREATE TABLE IF NOT EXISTS patients (
    id          integer PRIMARY KEY,
    name        text    NOT NULL,
    dob         date    NOT NULL,
    plan        text    NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id          integer PRIMARY KEY,
    patient_id  integer NOT NULL REFERENCES patients(id),
    code        text    NOT NULL,          -- CPT-like procedure code
    description text    NOT NULL,
    amount      numeric(10,2) NOT NULL,
    ordered_at  date    NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id           integer PRIMARY KEY,
    patient_id   integer   NOT NULL REFERENCES patients(id),
    order_ids    integer[] NOT NULL,
    status       text      NOT NULL,
    submitted_at date      NOT NULL
);

-- Agent-written tables ------------------------------------------------------
-- Every row carries _provenance: compact references (IDs, hashes), never copies.
-- prov_run is derived from _provenance so retries of the same Temporal run
-- upsert instead of duplicating rows.

-- Path A: written by the summarizer agent *during* its Temporal run.
CREATE TABLE IF NOT EXISTS claim_summaries (
    id           bigserial PRIMARY KEY,
    claim_id     integer     NOT NULL REFERENCES claims(id),
    summary      text        NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    _provenance  jsonb       NOT NULL,
    prov_run     text GENERATED ALWAYS AS (_provenance->>'run') STORED,
    UNIQUE (claim_id, prov_run)
);
CREATE INDEX IF NOT EXISTS claim_summaries_prov_gin ON claim_summaries USING gin (_provenance);

-- Path B: written by agent-*authored code*, long after the agent finished.
CREATE TABLE IF NOT EXISTS claim_risk_scores (
    id           bigserial PRIMARY KEY,
    claim_id     integer      NOT NULL REFERENCES claims(id),
    score        numeric(6,3) NOT NULL,
    reasons      jsonb        NOT NULL,
    created_at   timestamptz  NOT NULL DEFAULT now(),
    _provenance  jsonb        NOT NULL,
    prov_run     text GENERATED ALWAYS AS (_provenance->>'run') STORED,
    UNIQUE (claim_id, prov_run)
);
CREATE INDEX IF NOT EXISTS claim_risk_scores_prov_gin ON claim_risk_scores USING gin (_provenance);
