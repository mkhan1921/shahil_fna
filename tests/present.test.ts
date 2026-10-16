import { describe, expect, it } from 'vitest';
import { agendaText, answer, answerText, applies, measures, measureStatus, peopleFor, QUESTIONS, questionOfChange, questionText, verdicts, type Ctx } from '@/components/present/answers';
import { analyse } from '@/lib/fna/analysis';
import { applyPlan, closeEveryGap, emptyPersonPlan, emptyPlan, summarisePlan } from '@/lib/fna/plan';
import { sampleDocument } from '@/lib/fna/sample';
import type { PlanLevers } from '@/lib/fna/types';

const now = new Date('2026-10-15T09:00:00');
const doc = sampleDocument();
const today = analyse(doc, now);
const ctx = (plan: PlanLevers): Ctx & { changes: number } => {
  const planned = analyse(applyPlan(doc, plan, today), now);
  return { doc, today, planned, changes: summarisePlan(doc, plan, today, planned).changes.length };
};
const asIs = ctx(emptyPlan());
const closed = ctx(closeEveryGap(doc, today, now));
const asked = (c: Ctx & { changes: number }) => QUESTIONS.filter((q) => applies(q, c));
// Rand amounts, however the runtime groups thousands (a comma, or a space as en-ZA does in Node).
const R = String.raw`R[\d\s,.\u00a0\u202f]+`;
const re = (source: string) => new RegExp(source.replaceAll('{R}', R));

describe('the questions', () => {
  it('asks every question the sample family has something to answer for, about each person', () => {
    expect(asked(asIs)).toEqual(QUESTIONS);
    expect(peopleFor('life', asIs)).toEqual(['client', 'spouse']);
    expect(peopleFor('education', asIs)).toEqual([]);
    expect(questionText('life', asIs, 'spouse')).toBe('If Lerato died, would the family be OK?');
    expect(agendaText('life', asIs)).toBe('If one of you died, would the family be OK?');
    expect(questionText('retirement', asIs, 'client')).toMatch(re('^Can Thabo retire at 65 on {R} a month\\?$'));
  });

  it('leaves out questions that have nothing to answer', () => {
    const single = sampleDocument();
    single.household.includeSpouse = false;
    single.dependants = [];
    single.goals = [];
    const a = analyse(single, now);
    const c = { doc: single, today: a, planned: a };
    expect(applies('education', c)).toBe(false);
    expect(applies('goals', c)).toBe(false);
    expect(peopleFor('life', c)).toEqual(['client']);
    expect(agendaText('life', c)).toBe('If Thabo died, would the family be OK?');
  });
});

describe('the answers', () => {
  it('reads every gap as open today, in plain words', () => {
    for (const q of QUESTIONS.filter((x) => x !== 'afford')) {
      const ks = peopleFor(q, asIs);
      for (const k of ks.length ? ks : (['client'] as const)) {
        const a = answer(q, asIs, k, asIs.changes);
        expect(a.status, `${q} ${k}`).not.toBe('covered');
        expect(['Not yet.', 'Almost.']).toContain(a.lead);
        expect(answerText(a)).not.toMatch(/undefined|NaN/);
      }
    }
    expect(answerText(answer('life', asIs, 'client'))).toMatch(re('^(Not yet|Almost)\\. The family would be {R}m short\\.$'));
    expect(answerText(answer('afford', asIs, 'client', 0))).toMatch(re('^Today, yes\\. {R} a month is free before any changes\\.$'));
  });

  it('reads every gap as closed once the plan closes them, and the cost as over budget', () => {
    for (const q of QUESTIONS.filter((x) => x !== 'afford')) {
      const ks = peopleFor(q, closed);
      for (const k of ks.length ? ks : (['client'] as const)) {
        const a = answer(q, closed, k, closed.changes);
        expect(a.status, `${q} ${k}: ${answerText(a)}`).toBe('covered');
        expect(a.lead).toBe('Yes, with the plan.');
        expect(verdicts(q, closed, k).plan.status).toBe('covered');
      }
    }
    const afford = answer('afford', closed, 'client', closed.changes);
    expect(afford.status).toBe('shortfall');
    expect(answerText(afford)).toMatch(re('^Not yet\\. The plan costs {R} a month — {R} more than is free\\.$'));
  });

  it('says how far a partial plan has come', () => {
    const half = ctx({ ...emptyPlan(), people: { client: { ...emptyPersonPlan(), lifeCover: { amount: 2_000_000, premium: 0 } } } });
    const a = answer('life', half, 'client', half.changes);
    expect(a.lead).toBe('Closer.');
    expect(answerText(a)).toMatch(re('short, down from {R}m\\.$'));
    const [m] = measures('life', half, 'client');
    expect(m.plan - m.today).toBeCloseTo(2_000_000, 0);
  });

  it('gives the agenda a short verdict, today and with the plan', () => {
    const v = verdicts('life', closed, 'client');
    expect(v.today.text).toMatch(re('^{R}m short$'));
    expect(v.plan).toEqual({ status: 'covered', text: 'Covered' });
    expect(verdicts('retirement', asIs, 'client').today.text).toMatch(/^Runs out at \d+$/);
    expect(verdicts('retirement', closed, 'client').plan.text).toBe('On track');
    expect(verdicts('afford', closed).plan.text).toMatch(re('^{R} a month over$'));
  });

  it('only counts a need as met when it is met in full', () => {
    expect(measureStatus({ need: 100, plan: 100 })).toBe('covered');
    expect(measureStatus({ need: 100, plan: 97 })).toBe('partial');
    expect(measureStatus({ need: 100, plan: 40 })).toBe('shortfall');
    expect(measureStatus({ need: 0, plan: 0 })).toBe('na');
  });
});

describe('the plan, by question', () => {
  it('files every change under the question it answers', () => {
    const plan = closeEveryGap(doc, today, now);
    const planned = analyse(applyPlan(doc, plan, today), now);
    const keys = summarisePlan(doc, plan, today, planned).changes.map((ch) => ch.key);
    const filed = Object.fromEntries(keys.map((k) => [k, questionOfChange(k)]));
    expect(filed['client-lifeCover']).toBe('life');
    expect(filed['spouse-incomeProtection']).toBe('income');
    expect(filed['client-estateCover']).toBe('estate');
    expect(filed['client-ra']).toBe('retirement');
    expect(filed.emergency).toBe('emergency');
    expect(keys.filter((k) => k.startsWith('edu-')).every((k) => filed[k] === 'education')).toBe(true);
    expect(keys.filter((k) => k.startsWith('goalsave-')).every((k) => filed[k] === 'goals')).toBe(true);
    expect(questionOfChange('client-age')).toBe('retirement');
    expect(questionOfChange('client-income')).toBe('retirement');
    expect(questionOfChange('cut')).toBe('afford');
    expect(questionOfChange('debt-x')).toBe('afford');
    expect(questionOfChange('returns')).toBe('retirement');
  });
});
