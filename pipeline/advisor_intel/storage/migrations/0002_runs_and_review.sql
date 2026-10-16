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
