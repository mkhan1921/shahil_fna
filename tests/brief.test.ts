import { describe, expect, it } from 'vitest';
import { applies, peopleFor, QUESTIONS } from '@/components/present/answers';
import { addressFor, basisFor, bridgeFor, fundedRows, retirementSources, runningTotals, studyCosts, type PlanCtx } from '@/components/present/brief';
import { analyse } from '@/lib/fna/analysis';
import { applyPlan, closeEveryGap, emptyPlan } from '@/lib/fna/plan';
import { sampleDocument } from '@/lib/fna/sample';
import type { PlanLevers } from '@/lib/fna/types';

const now = new Date('2026-10-15T09:00:00');
const doc = sampleDocument();
const today = analyse(doc, now);
const ctx = (plan: PlanLevers): PlanCtx => ({ doc, today, planned: analyse(applyPlan(doc, plan, today), now), plan });
const plans = { 'no changes': ctx(emptyPlan()), 'every gap closed': ctx(closeEveryGap(doc, today, now)) };

/** Each question once for the household, or once per person it applies to. */
const cases = (c: PlanCtx) =>
  QUESTIONS.filter((q) => applies(q, c)).flatMap((q) => {
    const ks = peopleFor(q, c);
    return (ks.length ? ks : (['client'] as const)).map((k) => ({ q, k }));
  });

describe.each(Object.entries(plans))('the breakdowns, with %s', (_name, c) => {
  it('add up: every result restates the running total before it', () => {
    for (const { q, k } of cases(c)) {
      const b = bridgeFor(q, c, k);
      if (!b) continue;
      const runs = runningTotals(b.rows);
      b.rows.forEach((r, i) => {
        if (r.kind !== 'result' || i === 0 || q === 'retirement') return;
        expect(r.value, `${q} ${k} ${r.label}`).toBeCloseTo(runs[i - 1].to, 0);
      });
      // Totals restate what the rows before them added up to.
      b.rows.forEach((r, i) => {
        if (r.kind !== 'total' || i === 0) return;
        expect(r.value, `${q} ${k} ${r.label}`).toBeCloseTo(runs[i - 1].to, 0);
      });
    }
  });

  it('end where the analysis does', () => {
    for (const k of c.today.people) {
      const p = c.planned.persons[k]!;
      const last = (q: 'life' | 'income' | 'disability' | 'illness' | 'estate') => bridgeFor(q, c, k)!.rows.at(-1)!.value;
      expect(last('life')).toBeCloseTo(p.death.need - p.death.provision, 0);
      expect(last('income')).toBeCloseTo(p.incomeProtection.need - p.incomeProtection.provision, 0);
      expect(last('estate')).toBeCloseTo(p.estate.cashRequired - p.estate.cashAvailable, 0);
    }
    expect(bridgeFor('afford', c)!.rows.at(-1)!.value).toBeCloseTo(c.planned.cashflow.surplus, 0);
    expect(bridgeFor('emergency', c)!.rows.at(-1)!.value).toBeCloseTo(c.planned.emergency.need - c.planned.emergency.provision, 0);
  });

  it('splits projected retirement savings into their sources without losing any', () => {
    for (const k of c.today.people) {
      const s = retirementSources(c, k);
      expect(s.grown + s.contributions + s.extra).toBeCloseTo(s.total, 0);
      expect(s.needed).toBeCloseTo(c.planned.persons[k]!.retirement.requiredCapitalReal, 0);
    }
  });

  it('says what each question must address, in full sentences with real figures', () => {
    for (const { q, k } of cases(c)) {
      const groups = addressFor(q, c, k);
      expect(groups.length, q).toBeGreaterThan(1);
      for (const g of groups) {
        expect(g.items.length, `${q}: ${g.heading}`).toBeGreaterThan(0);
        for (const it of g.items) expect(`${it.title} ${it.text} ${it.figure ?? ''}`, q).not.toMatch(/undefined|NaN|null/);
      }
      for (const line of basisFor(q, c, k)) expect(line).not.toMatch(/undefined|NaN/);
    }
  });
});

describe('the overview and the study timeline', () => {
  it('measures every need, today and with the plan', () => {
    const rows = fundedRows(plans['every gap closed']);
    expect(rows.length).toBeGreaterThan(10);
    expect(rows.every((r) => r.plan >= 0.999)).toBe(true);
    expect(fundedRows(plans['no changes']).some((r) => r.today < 0.6)).toBe(true);
  });

  it('lays out each child’s study costs by year', () => {
    const series = studyCosts(plans['no changes']);
    expect(series.map((s) => s.name)).toEqual(['Neo', 'Kagiso']);
    for (const s of series) {
      expect(s.points.length).toBeGreaterThan(0);
      expect(s.points.every((p, i) => i === 0 || p.year === s.points[i - 1].year + 1)).toBe(true);
    }
  });
});
