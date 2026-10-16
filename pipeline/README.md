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
```

`process_cached_call(call, pins)` is the eval harness's task (stages 3–6 on cached stage 1–2 output).
`tests/test_extract_e2e.py` and `tests/test_pipeline.py` run the sample call end to end with the
gold-call oracle standing in for the model.

## FNA engine (spec §12.7)

`advisor_intel/fna_engine` is the app's needs analysis (`lib/fna`) ported to Python, with the TypeScript as
the reference: `analyse(doc, today=...)` returns the same `Analysis` the app computes, field for field, and
`draft_recommendations(doc, analysis, today=...)` the same drafts. `tax` is the one tax implementation in
the pipeline (the Monte Carlo layer uses `income_tax_vec` / `person_tax_vec`); `analyse_batch` and
`need_bands` turn materialised draws into shortfall bands. SARS values come from `fna_tables/` at the repo
root, which the app reads too.

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
