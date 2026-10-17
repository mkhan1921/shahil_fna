-- Advisor Call Intelligence: full Postgres schema (spec §14.2).
-- Rendered from storage/migrations by `python -m advisor_intel.storage.migrate render`; do not edit by hand.

-- ===== 0001_core =====

-- The tables of spec §14.2. Postgres holds the data, Temporal the run state, Braintrust the traces.
-- Facts, state ops and review events are append-only: a statement trigger refuses UPDATE, DELETE and TRUNCATE.

CREATE OR REPLACE FUNCTION advisor_intel_forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'restrict_violation';
END;
$$;

-- One row per recording.
CREATE TABLE calls (
  call_id     text PRIMARY KEY,
  workflow_id text,                              -- the latest run
  advisor_id  text NOT NULL DEFAULT '',
  client_ids  text[] NOT NULL DEFAULT '{}',
  started_at  timestamptz,
  audio_uri   text,
  meta        jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Pinned model, prompt, lexicon, calibrator and schema versions per run (the workflow id is the run id).
CREATE TABLE run_versions (
  workflow_id   text PRIMARY KEY,
  call_id       text NOT NULL REFERENCES calls (call_id),
  versions      jsonb NOT NULL,
  versions_key  text NOT NULL,
  start_stage   integer NOT NULL DEFAULT 1,
  source_run_id text,                            -- re-runs from a later stage reuse this run's outputs
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX run_versions_call_idx ON run_versions (call_id, created_at DESC);

-- Cached Stage 1 output, keyed by the ASR versions so re-runs never re-transcribe (§5).
CREATE TABLE transcripts (
  call_id           text NOT NULL REFERENCES calls (call_id),
  asr_model_version text NOT NULL,
  cache_key         text NOT NULL,
  transcript        jsonb NOT NULL,
  quality           jsonb,
  transcript_uri    text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (call_id, cache_key)
);

CREATE TABLE entities (
  workflow_id text NOT NULL,
  entity_id   text NOT NULL,
  ord         integer NOT NULL,                  -- registry order: list rows materialise in this order
  entity      jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workflow_id, entity_id)
);

-- Append-only: every change to a fact is a new version; a removed fact is a tombstone.
CREATE TABLE facts (
  workflow_id     text NOT NULL,
  fact_id         text NOT NULL,
  version         integer NOT NULL,
  profile_version integer NOT NULL,
  fact            jsonb,                         -- NULL for a tombstone
  deleted         boolean NOT NULL DEFAULT false,
  content_hash    text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workflow_id, fact_id, version),
  CHECK (deleted = (fact IS NULL))
);
CREATE INDEX facts_profile_version_idx ON facts (workflow_id, profile_version);
CREATE TRIGGER facts_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON facts
  FOR EACH STATEMENT EXECUTE FUNCTION advisor_intel_forbid_mutation();

-- The latest version of every fact that still exists.
CREATE VIEW facts_latest AS
SELECT workflow_id, fact_id, version, profile_version, fact, created_at
FROM (
  SELECT DISTINCT ON (workflow_id, fact_id) workflow_id, fact_id, version, profile_version, fact, deleted, created_at
  FROM facts
  ORDER BY workflow_id, fact_id, version DESC
) latest
WHERE NOT deleted;

-- The full replay log (§3.5).
CREATE TABLE state_ops (
  workflow_id text NOT NULL,
  seq         integer NOT NULL,
  op          jsonb NOT NULL,
  actor       text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workflow_id, seq)
);
CREATE TRIGGER state_ops_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON state_ops
  FOR EACH STATEMENT EXECUTE FUNCTION advisor_intel_forbid_mutation();

-- Lets run_agent resume after a retry (§13.2). One row per run and Temporal execution: a retry
-- resumes from it, a workflow reset (a new execution) starts clean.
CREATE TABLE agent_checkpoints (
  workflow_id   text NOT NULL,
  scope         text NOT NULL DEFAULT '',
  turn          integer NOT NULL,
  messages      jsonb NOT NULL,
  harness_state jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workflow_id, scope)
);

CREATE TABLE followups (
  workflow_id text NOT NULL,
  followup_id text NOT NULL,
  ord         integer NOT NULL,
  followup    jsonb NOT NULL,
  status      text NOT NULL DEFAULT 'open',
  answer      jsonb,
  answered_at timestamptz,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workflow_id, followup_id)
);

-- Source of calibration labels (§9.3) and of the nightly feedback datasets (§11.4).
CREATE TABLE review_events (
  id              bigserial PRIMARY KEY,
  workflow_id     text NOT NULL,
  fact_id         text,
  action          text NOT NULL,
  reason          text,
  reviewer_id     text NOT NULL,
  at              timestamptz NOT NULL,
  op_seq          integer,
  profile_version integer,
  detail          jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX review_events_at_idx ON review_events (at);
CREATE INDEX review_events_run_idx ON review_events (workflow_id, at);
CREATE TRIGGER review_events_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON review_events
  FOR EACH STATEMENT EXECUTE FUNCTION advisor_intel_forbid_mutation();

CREATE TABLE forecasts (
  workflow_id     text NOT NULL,
  profile_version integer NOT NULL,
  config_version  text NOT NULL,
  cma_version     text NOT NULL,
  mode            text NOT NULL,
  result          jsonb NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workflow_id, profile_version, config_version, cma_version, mode)
);

-- Promoted calibrators and thresholds (§9.4, §9.5).
CREATE TABLE calibrators (
  version       text NOT NULL,
  slot_group    text NOT NULL,
  artifact_uri  text,
  metrics       jsonb NOT NULL DEFAULT '{}'::jsonb,
  tau           double precision,
  alpha         double precision,
  n_calib_calls integer,
  promoted      boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (version, slot_group)
);

CREATE TABLE lexicons (
  version     text PRIMARY KEY,
  yaml        text NOT NULL,
  approved_by text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Adviser-confirmed term meanings, loaded into every context pack (§8.5).
CREATE TABLE glossary (
  term           text PRIMARY KEY,               -- normalised: lower case, single spaces
  display_term   text NOT NULL,
  aliases        text[] NOT NULL DEFAULT '{}',   -- normalised like term
  canonical_name text NOT NULL,
  product_type   text NOT NULL DEFAULT '',
  provider       text NOT NULL DEFAULT '',
  source         text NOT NULL DEFAULT 'adviser',
  source_url     text NOT NULL DEFAULT '',
  confirmed_by   text,
  confirmed_at   timestamptz
);
CREATE INDEX glossary_aliases_idx ON glossary USING gin (aliases);

-- ===== 0002_runs_and_review =====

-- What the pipeline needs to resume runs and to run the review (§2.1, §11, §13.1): stage outputs
-- for idempotent activities, discourse outputs, profile versions, blocking flags, review results,
-- each client's prior FNA document and CRM record, notifications and meeting notes.

-- Idempotency and caches: one row per (call, stage, cache key). Activities skip work when it exists.
CREATE TABLE stage_outputs (
  call_id    text NOT NULL,
  stage      text NOT NULL,
  cache_key  text NOT NULL,
  run_id     text NOT NULL,
  versions   jsonb NOT NULL DEFAULT '{}'::jsonb,
  output     jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (call_id, stage, cache_key)
);
CREATE INDEX stage_outputs_run_idx ON stage_outputs (run_id);

-- Cached Stage 2 output per (call, discourse versions).
CREATE TABLE discourse_outputs (
  call_id    text NOT NULL REFERENCES calls (call_id),
  cache_key  text NOT NULL,
  versions   jsonb NOT NULL DEFAULT '{}'::jsonb,
  output     jsonb NOT NULL,
  output_uri text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (call_id, cache_key)
);

-- One header per saved profile version. Facts come from `facts` (profile_version <= version),
-- ops from `state_ops` (seq <= last_seq). A frozen version (review submitted) is never rewritten.
CREATE TABLE profiles (
  workflow_id    text NOT NULL,
  version        integer NOT NULL,
  call_id        text NOT NULL,
  schema_version text NOT NULL,
  anchor_date    date NOT NULL,
  last_seq       integer NOT NULL DEFAULT 0,
  frozen         boolean NOT NULL DEFAULT false,
  frozen_at      timestamptz,
  frozen_by      text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workflow_id, version)
);
CREATE INDEX profiles_call_idx ON profiles (call_id);

-- Blocking flags (§10.3): any open flag blocks review submission and export.
CREATE TABLE flags (
  workflow_id text NOT NULL,
  flag_id     text NOT NULL,
  ord         integer NOT NULL,
  flag        jsonb NOT NULL,
  cleared     boolean NOT NULL DEFAULT false,
  cleared_by  text,
  resolution  text,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workflow_id, flag_id)
);

-- Review results from the API or the file exchange (§11.5), kept whole for audit and prompt tuning.
CREATE TABLE review_results (
  id                   bigserial PRIMARY KEY,
  workflow_id          text NOT NULL,
  reviewer_id          text NOT NULL,
  base_profile_version integer NOT NULL,
  submitted            boolean NOT NULL DEFAULT false,
  result               jsonb NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX review_results_run_idx ON review_results (workflow_id, created_at);

-- Each client's FnaDocument before a call (POST /clients/{id}/fna) and after review (§3.6).
CREATE TABLE client_documents (
  id         bigserial PRIMARY KEY,
  client_id  text NOT NULL,
  document   jsonb NOT NULL,
  source     text NOT NULL,                      -- 'import' | 'reviewed'
  call_id    text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX client_documents_client_idx ON client_documents (client_id, id DESC);

CREATE TABLE client_crm (
  client_id  text PRIMARY KEY,
  record     jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- One row per notification; the key makes sends idempotent across activity retries.
CREATE TABLE notifications (
  idem_key    text PRIMARY KEY,
  workflow_id text NOT NULL,
  kind        text NOT NULL,
  channel     text,
  recipient   text,
  payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now(),
  sent_at     timestamptz,
  error       text
);

-- The meeting note rendered from reviewed facts (§11.6), per profile version.
CREATE TABLE meeting_notes (
  workflow_id     text NOT NULL,
  profile_version integer NOT NULL,
  note            jsonb NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workflow_id, profile_version)
);

-- ===== 0003_glossary_kind =====

-- The lexicon's glossary entries carry the catalogue kind of the resolved term (fund, policy,
-- provider …) beside its FNA product type (§8.5); keys are the lexicon's canonical form.
ALTER TABLE glossary ADD COLUMN kind text NOT NULL DEFAULT '';

-- ===== 0004_notifications_run_index =====

-- Notifications are read per run (the API's run view, the activity's duplicate check); the primary key
-- is the idempotency key, so the per-run lookup needs its own index.
CREATE INDEX IF NOT EXISTS notifications_run_idx ON notifications (workflow_id, created_at);
