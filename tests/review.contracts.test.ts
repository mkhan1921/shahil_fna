import { describe, expect, it } from 'vitest';
import { analyse } from '@/lib/fna/analysis';
import { normaliseDocument } from '@/lib/fna/defaults';
import { slotById, toFnaValue } from '@/lib/review/catalog';
import { checkDist, describe as describeValue, summarise } from '@/lib/review/dist';
import { describeErrors, validate } from '@/lib/review/jsonSchema';
import { getPath } from '@/lib/review/paths';
import type { Quantity } from '@/lib/review/types';
import { ANCHOR, loadPackage, readJson, schema } from './reviewFixture';

const pkg = loadPackage();
const bundle = pkg.bundle;
const fields = Object.entries(bundle.sidecar.fields);

describe('the review package contract', () => {
  it('validates against the JSON Schema exported from the pipeline’s ReviewPackage', () => {
    const errors = validate(schema('review_package'), pkg);
    expect(describeErrors(errors)).toBe('');
  });

  it('carries a sidecar that validates against the Sidecar model’s schema', () => {
    expect(describeErrors(validate(schema('sidecar'), bundle.sidecar))).toBe('');
    expect(bundle.sidecar.last_seq).toBeGreaterThan(0);
  });

  it('is the same package in every place it is used (vitest, pytest, the app’s demo)', () => {
    expect(readJson('pipeline', 'tests', 'fixtures', 'review_package.json')).toEqual(pkg);
    expect(readJson('public', 'demo', 'review_package.json')).toEqual(pkg);
  });

  it('holds only distributions the pipeline would accept', () => {
    for (const [path, f] of fields) {
      if (f.quantity) expect(checkDist(f.quantity.dist), path).toEqual([]);
      for (const h of f.hypotheses) if (h.quantity) expect(checkDist(h.quantity.dist), path).toEqual([]);
    }
  });

  it('flags and follow-ups point at facts in the sidecar, and quotes are verbatim transcript spans', () => {
    const ids = new Set(fields.map(([, f]) => f.fact_id));
    for (const flag of bundle.blocking_flags) if (flag.fact_id) expect(ids.has(flag.fact_id), flag.flag_id).toBe(true);
    for (const q of bundle.followups) for (const id of q.fact_ids) expect(ids.has(id), q.followup_id).toBe(true);
    const segments = new Map(bundle.transcript.segments.map((s) => [s.segment_id, s]));
    for (const [path, f] of fields)
      for (const e of f.evidence) {
        const s = segments.get(e.segment_id);
        expect(s, `${path} cites ${e.segment_id}`).toBeDefined();
        expect(s!.text.slice(e.char_start, e.char_end), path).toBe(e.quote);
        expect(e.t_start).toBeGreaterThanOrEqual(s!.t_start);
        expect(e.t_end).toBeLessThanOrEqual(s!.t_end);
      }
  });
});

describe('the overlay reads values exactly as the pipeline does', () => {
  it('phrases every value as binding.describe_value does', () => {
    for (const [path, f] of fields) expect(describeValue(f.quantity), path).toBe(f.value_text);
  });

  it('summarises every value as binding.summarise_value does', () => {
    for (const [path, f] of fields) {
      const mine = summarise(f.quantity);
      expect(mine === null, path).toBe(f.value === null);
      if (!mine || !f.value) continue;
      for (const k of ['p10', 'p50', 'p90', 'p_true'] as const) if (typeof f.value[k] === 'number') expect(mine[k], `${path} ${k}`).toBeCloseTo(f.value[k] as number, 6);
      expect(mine.point ?? null, path).toEqual(f.value.point ?? null);
      expect(mine.probs ?? null, path).toEqual(f.value.probs ?? null);
    }
  });

  it('writes every usable fact into the document exactly as binding.materialise does', () => {
    let checked = 0;
    for (const [path, f] of fields) {
      if (!f.exported || !f.quantity || f.review.status === 'rejected') continue;
      const slot = slotById(f.slot_id)!;
      const expected = getPath(bundle.median_document, path);
      const mine = toFnaValue(slot, (f.quantity as Quantity).dist);
      if (expected === undefined) continue;
      expect(mine, path).toEqual(expected);
      checked++;
    }
    expect(checked).toBeGreaterThan(300);
  });
});

describe('the preview forecast agrees with the app’s engine', () => {
  const a = analyse(normaliseDocument(bundle.median_document), ANCHOR);
  const det = bundle.preview_forecast!.deterministic;

  it('records the FNA’s deterministic retirement projection of the median document (§12.8)', () => {
    for (const k of a.people) {
      const r = a.persons[k]!.retirement;
      expect(det[`${k}.projected_capital`]! / r.projectedCapital).toBeCloseTo(1, 2);
      expect(det[`${k}.required_capital`]! / r.requiredCapital).toBeCloseTo(1, 2);
      expect(det[`${k}.depletion_age`]).toBeCloseTo(r.depletionAge ?? 0, 1);
      expect(Math.abs(det[`${k}.mc_zero_vol_capital`]! / r.projectedCapital - 1)).toBeLessThan(0.005);
    }
  });

  it('puts the median of each need’s shortfall band at the engine’s shortfall', () => {
    const bands = bundle.preview_forecast!.need_bands;
    const c = a.persons.client!;
    expect(bands['life.client'].p50 / c.death.shortfall).toBeCloseTo(1, 2);
    expect(bands['retirement.client'].p50 / c.retirement.shortfall).toBeCloseTo(1, 2);
    expect(bands['education.household'].p50 / a.educationTotal.shortfall).toBeCloseTo(1, 2);
  });
});

describe('the JSON Schema validator', () => {
  const result = schema('review_result');

  it('reports wrong types, missing fields and unknown constants with their paths', () => {
    const errors = validate(result, { format: 'something-else', version: 2, call_id: 7, ops: [{ op: 'ADD' }] });
    const text = describeErrors(errors, 50);
    expect(text).toContain('$.format: must be "advisor-intel-review-result"');
    expect(text).toContain('$.version: must be 1');
    expect(text).toContain('$.call_id: expected string');
    expect(text).toContain('$.run_id: is required');
    expect(text).toContain('$.ops[0].fact_id: is required');
  });

  it('follows discriminated unions to the right shape', () => {
    const payloads = schema('op_payloads');
    const good = { slot_id: 's', fna_path: 'p', provenance: { run_id: 'r' }, hypotheses: [{ value: { dist: { kind: 'triangular', low: 1, mode: 2, high: 3 }, unit: 'age' }, weight: 1, rationale: '' }] };
    expect(validate(payloads, good, '#/$defs/AddPayload')).toEqual([]);
    const bad = { ...good, hypotheses: [{ value: { dist: { kind: 'triangular', low: 1 }, unit: 'age' }, weight: 1, rationale: '' }] };
    expect(describeErrors(validate(payloads, bad, '#/$defs/AddPayload'))).toContain('mode: is required');
    const unknown = { ...good, hypotheses: [{ value: { dist: { kind: 'beta' }, unit: 'age' }, weight: 1, rationale: '' }] };
    expect(describeErrors(validate(payloads, unknown, '#/$defs/AddPayload'))).toContain('kind: must be one of');
  });
});
