# Advisor Call Intelligence

Python pipeline that turns one recorded adviser–client call into a reviewed, probabilistic update
to the client's Shahil FNA document (`FnaDocument` v2) and a Monte Carlo forecast.

Work in progress: see the implementation spec for the stage map. Run the tests with

```bash
cd pipeline
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest
```

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
