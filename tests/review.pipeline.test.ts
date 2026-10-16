/**
 * The overlay against a review package the real pipeline produced: the scripted sample call through
 * advisor_intel.pipeline.process_call (pipeline/scripts/make_pipeline_review_package.py). The demo
 * package is hand-built to cover every case; this one keeps the overlay honest about what the
 * pipeline actually emits — a new client, derived values, its own phrasing of each value.
 */
import { describe, expect, it } from 'vitest';
import { slotById, toFnaValue } from '@/lib/review/catalog';
import { checkDist, describe as describeValue, summarise } from '@/lib/review/dist';
import { checkPackage } from '@/lib/review/exchange';
import { describeErrors, validate } from '@/lib/review/jsonSchema';
import { getPath } from '@/lib/review/paths';
import { badgeAt, buildQueue, groupOf } from '@/lib/review/queue';
import { createSession, isReviewItem, applyAction, submit, toResult } from '@/lib/review/session';
import type { Quantity, ReviewPackage } from '@/lib/review/types';
import { readJson, schema, STAMP } from './reviewFixture';

const pkg = readJson<ReviewPackage>('tests', 'fixtures', 'pipeline_review_package.json');
const bundle = pkg.bundle;
const fields = Object.entries(bundle.sidecar.fields);

describe('a review package from process_call', () => {
  it('passes the import checks: the exported schemas and the package’s own coherence', () => {
    expect(checkPackage(pkg, { pkg: schema('review_package'), sidecar: schema('sidecar') })).toEqual([]);
    for (const [path, f] of fields) if (f.quantity) expect(checkDist(f.quantity.dist), path).toEqual([]);
  });

  it('is phrased, summarised and written into the document exactly as the overlay does it', () => {
    let written = 0;
    for (const [path, f] of fields) {
      expect(describeValue(f.quantity), path).toBe(f.value_text);
      const mine = summarise(f.quantity);
      for (const k of ['p10', 'p50', 'p90', 'p_true'] as const) if (typeof f.value?.[k] === 'number') expect(mine?.[k], `${path} ${k}`).toBeCloseTo(f.value[k] as number, 6);
      if (!f.exported || !f.quantity) continue;
      const expected = getPath(bundle.median_document, path);
      if (expected === undefined) continue;
      expect(toFnaValue(slotById(f.slot_id)!, (f.quantity as Quantity).dist), path).toEqual(expected);
      written++;
    }
    expect(written).toBeGreaterThan(40);
  });

  it('shows what a derivation did: take-home pay grossed up, risk answers worked out', () => {
    const s = createSession(bundle, { docId: bundle.median_document.id, reviewerId: STAMP.reviewerId, now: STAMP.at });
    const gross = s.facts['income.{p}.grossMonthly#client'];
    expect(gross.derivation).toBe('gross_from_net');
    expect(gross.status).toBe('implied');
    expect(badgeAt(s, 'income.client.grossMonthly')?.title).toContain('grossed up from take-home pay');
    const derived = Object.values(s.facts).filter((f) => f.derivation);
    expect(derived.map((f) => f.derivation).sort()).toEqual(['emergency_from_cash_flow', 'gross_from_net', 'gross_from_net', 'horizon_from_retirement', 'income_stability_from_employment']);
  });

  it('reviews end to end: every fact queued, accepted, submitted as a valid review result', () => {
    let s = createSession(bundle, { docId: bundle.median_document.id, reviewerId: STAMP.reviewerId, now: STAMP.at });
    const q = buildQueue(s);
    const queued = [...q.needs, ...q.quick, ...q.collapsed];
    expect(queued.length).toBe(Object.values(s.facts).filter(isReviewItem).length);
    // Unverified facts (p_correct 0.5) all need the adviser.
    for (const f of queued) if (f.pCorrect < 0.6 && !f.autoAccepted) expect(groupOf(f, false), f.factId).toBe('needs');
    s = applyAction(s, { type: 'acceptMany', factIds: queued.map((f) => f.factId) }, STAMP);
    expect(s.ops.length).toBe(queued.length);
    expect(s.ops[0].seq).toBe(bundle.sidecar.last_seq + 1);
    const done = submit(s, STAMP.at);
    expect(describeErrors(validate(schema('review_result'), toResult(done, { submitted: true, at: STAMP.at })))).toBe('');
  });
});
