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
import { createSession, isReviewItem, isUsable, applyAction, submit, toResult, type ReviewFact } from '@/lib/review/session';
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
    // No emergency-fund answer: the call never mentions savings, and silence is not "no savings".
    expect(derived.map((f) => f.derivation).sort()).toEqual([
      'gross_from_net', 'gross_from_net', 'horizon_from_retirement', 'income_stability_from_employment',
      'partner_on_call', 'partner_on_call', // the spouse spoke on the call: included, married or life partner
    ]);
  });

  it('reviews end to end: every fact queued, accepted, submitted as a valid review result', () => {
    let s = createSession(bundle, { docId: bundle.median_document.id, reviewerId: STAMP.reviewerId, now: STAMP.at });
    const q = buildQueue(s);
    expect(q.flags.length).toBeGreaterThan(0); // VOI's flags: missing tier A fields and a status flip
    const flagged = [...new Map(q.flags.flatMap((f) => (f.fact ? [[f.fact.factId, f.fact] as const] : []))).values()];
    expect(flagged.length).toBe(new Set(q.flags.map((f) => f.flag.fact_id)).size); // every flag sits on a fact in the package
    const grouped = [...q.needs, ...q.quick, ...q.collapsed];
    const queued: ReviewFact[] = [...flagged, ...grouped];
    const ids = new Set(queued.map((f) => f.factId));
    expect(ids.size).toBe(queued.length);
    for (const f of Object.values(s.facts).filter(isReviewItem)) expect(ids.has(f.factId), f.factId).toBe(true);
    // Unverified facts (p_correct 0.5) all need the adviser.
    for (const f of grouped) if (f.pCorrect < 0.6 && !f.autoAccepted) expect(groupOf(f, false), f.factId).toBe('needs');
    const decidable = queued.filter(isUsable); // a missing field is filled in or marked unknown, not accepted
    s = applyAction(s, { type: 'acceptMany', factIds: decidable.map((f) => f.factId) }, STAMP);
    expect(s.ops.length).toBe(decidable.length);
    expect(s.ops[0].seq).toBe(bundle.sidecar.last_seq + 1);
    // Every blocking flag is cleared before submitting: accepted where the call gave a value, marked unknown where it did not.
    for (const { flag, fact } of buildQueue(s).flags) {
      s = applyAction(s, { type: 'clearFlag', flagId: flag.flag_id, resolution: fact && isUsable(fact) ? 'accepted' : 'marked_unknown' }, STAMP);
    }
    expect(s.ops.some((op) => op.op === 'MARK_UNKNOWN')).toBe(true);
    const done = submit(s, STAMP.at);
    expect(describeErrors(validate(schema('review_result'), toResult(done, { submitted: true, at: STAMP.at })))).toBe('');
  });
});
