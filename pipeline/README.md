# Advisor Call Intelligence

Python pipeline that turns one recorded adviser–client call into a reviewed, probabilistic update to
the client's Shahil FNA document (`FnaDocument` v2) and a Monte Carlo forecast. Every fact carries a
value distribution, a calibrated probability that it was extracted correctly, and timestamped
transcript evidence. The FNA app (repo root) stays the system of record: the pipeline reads the
client's existing `shahil-fna` export and returns a reviewed one plus a provenance sidecar.

## Stages

| Stage | Package | What it does |
| --- | --- | --- |
| 1 Ingestion | `advisor_intel/ingest` | Audio preprocessing, VAD, ASR adapters, diarization, segmentation, role assignment, number/age/product grammars, risky-span second pass, quality report |
| 2 Discourse | `advisor_intel/discourse` | Entity registry and references, self-contained rewrites (checked), revision detection, two-pass topic index with segment vectors |
| 3–4 Plan and extract | `advisor_intel/extract` | Planner (model proposes, code enforces), three strict-schema extraction stages with field-level retries, span alignment, scale resolution, normalisation, the reducer |
| Lexicon | `advisor_intel/lexicon` | Versioned vagueness lexicon, `normalize_value`, hedges, weighting, bounds, lexicon re-fit, unknown terms and the practice glossary |
| 5 Verify | `advisor_intel/verify`, `schema/constraints.py` | Entailment, consistency probes, constraints, omission check, features, calibrator, conformal auto-accept |
| 6 VOI | `advisor_intel/voi` | Sensitivity runs, priorities, blocking flags, clarify decisions, follow-up questions |
| 7 Review | `advisor_intel/api`, `lib/review` (app) | Review bundle, reviewer ops through the same reducer, file exchange, export with sidecar |
| 8 Forecast | `advisor_intel/forecast` | Vectorised Monte Carlo cash-flow model, variance attribution, sensitivity runs |
| FNA engine | `advisor_intel/fna_engine`, `fna_tables/` | Python port of `lib/fna` with shared SARS tables and a TypeScript parity harness |
| Harness | `advisor_intel/harness` | Orchestrator agent, toolsets, budgets, loop detection, compaction, per-turn checkpoints |
| Durable runs | `advisor_intel/workflows`, `activities`, `storage`, `api` | Temporal workflows and activities, Postgres + object storage, FastAPI |

The schema is the FNA app's own: `schema/profile_v1.yaml` is generated from `lib/fna/types.ts`,
`catalog.ts` and `riskProfile.ts` plus `schema/annotations.yaml`
(`python -m advisor_intel.schema.generate`; `--check` in CI). Values enter a profile only through
`extract/reducer.py`, whose ops log replays to the same facts.

## Running

```bash
cd pipeline
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest
```

Runs pinned to the `"test"` model use schema-driven fakes, so nothing reaches a provider;
`"local:<name>"` pins use registered in-process models (scripted tests and the gold-call oracle).

### One call, in process

`advisor_intel.pipeline.process_call` runs a call through every installed stage without Temporal or a
database: stage 1 (a ready transcript, a scripted call, or audio through the pinned ASR and diarizer),
discourse, the client's prior file, the orchestrated extraction and verification, VOI and follow-ups,
the median document with its sidecar, the preview forecast and the meeting note.

```python
from advisor_intel.pipeline import CallInput, process_call

result = await process_call(CallInput(call_id="c1", script=open("call.txt").read(), prior_document=doc), pins)
result.document      # the reviewed-median FnaDocument
result.sidecar       # probabilities and evidence per FnaDocument path
result.followups, result.flags, result.forecast, result.note
result.warnings      # checks that failed or did not run inside stages 5–6 (a stub verifier, a failed PoS run): never a silent zero
```

From the command line, a scripted call or a recording becomes the files the FNA app imports:

```bash
.venv/bin/python -m advisor_intel.pipeline --script call.txt --roles sipho=advisor,thabo=client_primary,lerato=client_secondary \
    --prior thabo-before.json --date 2026-10-15 --out out/
# out/export.json          the reviewed-median client file (shahil-fna format)
# out/review_package.json  the review for the app's overlay (file exchange)
# out/sidecar.json         probabilities and evidence per field
```

`--pins deployment` (the default) uses the configured models and needs their API keys; `--pins test`
runs offline on fakes whose values are arbitrary (plumbing only). `--audio` takes a recording and
uses the pinned ASR provider and diarizer.

`process_cached_call(call, pins)` is the eval harness's task (stages 3–6 on cached stage 1–2 output).
`tests/test_extract_e2e.py` and `tests/test_pipeline.py` run the sample call end to end with the
gold-call oracle standing in for the model.

## Durable runs: Temporal, Postgres and the API (spec §13–14)

Production runs go through Temporal: `CallPipelineWorkflow` runs ingestion, discourse, the agent (one
heartbeating activity with per-turn checkpoints in Postgres), stage 5's post-agent checks, VOI, the preview
forecast and the meeting note, then waits for the adviser's review and the follow-up answers. Nightly
workflows retrain the calibrator, refit conformal thresholds and propose lexicon changes.

```bash
export ADVISOR_INTEL_DATABASE_URL=postgresql://localhost:5432/advisor_intel
export ADVISOR_INTEL_TEMPORAL_ADDRESS=localhost:7233        # Temporal Cloud: also ADVISOR_INTEL_TEMPORAL_API_KEY (turns TLS on)
export ADVISOR_INTEL_OBJECT_STORE_URI=file:///srv/advisor-intel/objects   # or s3://bucket/prefix with AWS credentials
export ADVISOR_INTEL_API_TOKEN=change-me                    # the bearer token the FNA app's review client sends
.venv/bin/python -m advisor_intel.storage.migrate up
.venv/bin/python -m advisor_intel.worker                    # workflows, activities and the nightly schedules
.venv/bin/python -m advisor_intel.api.app                   # calls, review bundle, reviewer ops, submit, /fna
```

`ADVISOR_INTEL_PINS=test` runs every model role on offline stubs. `POST /calls` takes audio, or a scripted call
as JSON (`{script, script_roles}`). The tests under `tests/infra` start a Temporal dev server
(`pipeline/.tools/temporal` or `TEMPORAL_CLI_PATH`) and a throwaway PostgreSQL (`initdb` on the path,
`ADVISOR_INTEL_PG_BIN`, or `ADVISOR_INTEL_TEST_DATABASE_URL`); they skip when those are missing.

## Models, credentials and data

Every setting reads `ADVISOR_INTEL_<NAME>` or `<NAME>` from the environment. Model roles are Pydantic AI
model strings: `ADVISOR_INTEL_MODEL_FRONTIER`, `_REASONING`, `_VERIFIER` (a different family from the
extractor), `_SMALL` and `_SECONDARY` (the fallback provider), with the providers' own keys
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`). The agent's per-run budgets (spec §13.6) are
`ADVISOR_INTEL_BUDGET_<FIELD>` for any field of `config.Budgets` (`BUDGET_USD_CAP`, `BUDGET_TOOL_CALLS`,
`BUDGET_TOTAL_TOKENS`, `BUDGET_WALL_CLOCK_SOFT_S`, …; a call's `budget_usd` in `POST /calls` overrides the cap for
that run); the review thresholds are `CONFORMAL_ALPHA`, `MIN_CONFORMAL_CALLS` and `SPOT_CHECK_RATE`, and the model
options `EXTRACTION_EFFORT` (low / medium / high) and `REFUSAL_FALLBACKS`.

| Needs | For |
| --- | --- |
| Model API keys | real extraction, verification, follow-up drafting and the meeting note (tests use fakes and the gold-call oracle) |
| `ASSEMBLYAI_API_KEY` / `DEEPGRAM_API_KEY` / `SPEECHMATICS_API_KEY`, `PYANNOTEAI_API_KEY` | recorded calls (ASR and diarization); `VLLM_BASE_URL` / `PARAKEET_BASE_URL` for self-hosted ASR, the second pass and MiniCheck |
| Temporal Cloud, Postgres, S3 | durable production runs |
| `BRAINTRUST_API_KEY` | tracing, datasets and the experiment CI gate |
| `ADVISOR_INTEL_SMTP_*`, `ADVISOR_INTEL_NOTIFY_WEBHOOK_URL` | adviser notifications |
| Reviewed gold calls | the calibrator (50 labelled calls warm, 300 to promote) and conformal auto-accept (50 calibration calls; until then nothing is auto-accepted) |
| The investment team's CMA and return history | the forecast's market model (`forecast/cma/2026Q4.yaml` is a placeholder) |
| GPUs or a Tinker-compatible API | the fine-tuning (M6) and RL (M7) tracks |

## The review overlay's contract (spec §11)

The FNA app reviews a call against JSON Schemas exported from these models (`lib/review/schema/`):
`ReviewBundle`, `ReviewPackage`, `ReviewResult`, the reducer's op payloads, `models/sidecar.py` (what
`binding.sidecar()` emits) and `evals/annotation.py` (annotate mode's gold file), plus the slot
catalog. After changing any of them, or the extraction a fixture is built from, regenerate:

```bash
.venv/bin/python scripts/export_review_schemas.py          # lib/review/schema/*.json
.venv/bin/python scripts/make_review_fixture.py            # the hand-built demo package (also public/demo/)
.venv/bin/python scripts/make_pipeline_review_package.py   # the scripted call through process_call
```

Each takes `--check`. `tests/test_review_contracts.py` checks they are current, and replays the review
result the app's tests write (`tests/fixtures/review_result.json` at the repo root) through the reducer:
it must materialise to exactly the reviewed file the app saved.

## FNA engine (spec §12.7)

`advisor_intel/fna_engine` is the app's needs analysis (`lib/fna`) ported to Python, with the TypeScript as
the reference: `analyse(doc, today=...)` returns the same `Analysis` the app computes, field for field, and
`draft_recommendations(doc, analysis, today=...)` the same drafts. `tax` is the one tax implementation in
the pipeline (the Monte Carlo layer uses `income_tax_vec` / `person_tax_vec`); `analyse_batch` and
`need_bands` turn materialised draws into shortfall bands. SARS values come from `fna_tables/` at the repo
root, which the app reads too. `forecast.run_forecast` reports the engine's own retirement projection
(`deterministic`) and the need bands beside the Monte Carlo result by default (§12.8), and takes the living
annuity drawdown band from the table through the engine's tax model.

The parity harness (`advisor_intel/fna_parity`) runs the TypeScript engine on 2,000 seeded documents
through Node and checks the Python results: money within R1, ratios within 1e-6, text exactly. Without
Node, pytest checks a committed subset (`tests/fixtures/fna_parity_subset.jsonl.gz`). After changing
either engine or a table file, regenerate the TypeScript results and the subset with one command (needs
Node and the app's `node_modules`):

```bash
.venv/bin/python -m advisor_intel.fna_parity regenerate   # generate (~7 s) + check 2,000 documents (~10 s) + rewrite the subset
```

`generate`, `check` and `fixture` run the steps on their own; output goes to `<repo>/fna_parity_out/`
(gitignored). `pytest -m slow` runs the full harness and the timing targets (`FNA_PERF_SLACK` scales them).
