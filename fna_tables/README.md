# SARS tables

One JSON file per year of assessment, `za_<yyyy>_<yy>.json`, holding every SARS value the FNA engines use:
income tax brackets, rebates and thresholds, s6A medical credits, the s11F retirement deduction, UIF,
the interest exemption, CGT, tax-free savings limits, estate duty, the retirement and withdrawal lump-sum
tables, donations tax, the two-pot savings withdrawal and the living annuity drawdown band.
`schema.json` (JSON Schema 2020-12) describes the format. Amounts are in rand and rates are decimals.

Both engines read these files and neither hard-codes a SARS number (spec §12.7):

- the app: `lib/fna/tax.ts` imports each file (TypeScript checks it against `TaxTableFile` at compile time);
- the pipeline: `pipeline/advisor_intel/fna_engine/tables.py` validates each file against `schema.json`,
  then checks that bracket bases reconcile and that the thresholds clear the rebates.

The Master's fee tariff, the LSSA conveyancing guideline and the estate cost estimates are not SARS tables;
they stay in `lib/fna/estate.ts` and `fna_engine/estate.py`.

## Adding a tax year

1. Copy the latest file to `za_<yyyy>_<yy>.json` and update every value from the Budget tax guide;
   set `status` to `provisional` until the rates are enacted.
2. Import it in `lib/fna/tax.ts` (`TABLE_FILES`, newest first). `tests/tables.test.ts` fails if a file is
   not registered; the Python loader finds files by name.
3. Add the year's SARS worked examples to `tests/engine.test.ts` and `pipeline/tests/test_fna_engine.py`,
   then run both test suites and the parity harness:

```bash
npx vitest run && npx tsc --noEmit
cd pipeline && .venv/bin/python -m pytest -q
.venv/bin/python -m advisor_intel.fna_parity regenerate
```

Years after the latest file are projected by `fna_engine.tables.indexed_table`, which indexes the
brackets, rebates, thresholds and medical credits by CPI (§12.3).
