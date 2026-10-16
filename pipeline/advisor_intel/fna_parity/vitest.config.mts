// Runs the TypeScript side of the FNA parity harness (spec §12.7): `generate.parity.ts` builds seeded
// documents, runs analyse() and draftRecommendations() on each and writes JSONL for pytest.
// Start it with `python -m advisor_intel.fna_parity generate` (see pipeline/README.md).
import { resolve } from 'node:path';
import { defineConfig } from 'vitest/config';

// The engine reads calendar dates in local time; the Python port reads them in UTC.
process.env.TZ = 'UTC';

const repo = resolve(import.meta.dirname, '../../..');

export default defineConfig({
  root: repo,
  resolve: { alias: { '@': repo } },
  test: {
    include: ['pipeline/advisor_intel/fna_parity/*.parity.ts'],
    testTimeout: 600_000,
  },
});
