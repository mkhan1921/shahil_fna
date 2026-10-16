import { describe, expect, it } from 'vitest';
import { analyse } from '@/lib/fna/analysis';
import {
  applyPlan,
  closeEveryGap,
  discretionarySpending,
  emptyPersonPlan,
  emptyPlan,
  normalisePlan,
  planAfterAdvice,
  planFromAdvice,
  planToAdvice,
  summarisePlan,
  withoutChange,
} from '@/lib/fna/plan';
import { draftRecommendations } from '@/lib/fna/recommend';
import { sampleDocument } from '@/lib/fna/sample';
import type { PlanLevers } from '@/lib/fna/types';

const now = new Date('2026-10-15T09:00:00');
const doc = sampleDocument();
doc.advice.recommendations = draftRecommendations(doc, analyse(doc, now));
const today = analyse(doc, now);
const run = (plan: PlanLevers) => {
  const planned = analyse(applyPlan(doc, plan, today), now);
  return { planned, summary: summarisePlan(doc, plan, today, planned) };
};
const withPerson = (patch: Partial<ReturnType<typeof emptyPersonPlan>>, k: 'client' | 'spouse' = 'client'): PlanLevers => ({
  ...emptyPlan(),
  people: { [k]: { ...emptyPersonPlan(), ...patch } },
});

describe('applying a plan', () => {
  it('leaves the client file untouched and an empty plan changes nothing', () => {
    const before = JSON.stringify(doc);
    const { planned, summary } = run(emptyPlan());
    expect(JSON.stringify(doc)).toBe(before);
    expect(planned.cashflow.surplus).toBeCloseTo(today.cashflow.surplus, 6);
    expect(planned.persons.client!.death.shortfall).toBeCloseTo(today.persons.client!.death.shortfall, 6);
    expect(summary.changes).toEqual([]);
    expect(summary.monthly).toBeCloseTo(0, 6);
  });

  it('adds cover to the need it serves, and its premium to the budget', () => {
    const { planned, summary } = run(withPerson({ lifeCover: { amount: 2_000_000, premium: 400 }, incomeProtection: { amount: 20_000, premium: 600 } }));
    const p = planned.persons.client!;
    const t = today.persons.client!;
    expect(p.death.provision - t.death.provision).toBeCloseTo(2_000_000, 0);
    expect(p.incomeProtection.provision - t.incomeProtection.provision).toBeCloseTo(20_000, 0);
    expect(today.cashflow.surplus - planned.cashflow.surplus).toBeCloseTo(1_000, 6);
    expect(summary.premiums).toBe(1_000);
    expect(summary.monthly).toBeCloseTo(1_000, 6);
  });

  it('counts cover paid to the estate as cash the executor can use, less the executor’s fee on it', () => {
    const before = today.persons.client!.estate.liquidityShortfall;
    const { planned } = run(withPerson({ estateCover: { amount: 900_000, premium: 0 } }));
    const after = planned.persons.client!.estate.liquidityShortfall;
    expect(before).toBeGreaterThan(800_000);
    expect(after).toBeGreaterThan(0);
    expect(after).toBeLessThan(before * 0.05);
    expect(run(withPerson({ estateCover: { amount: 1_000_000, premium: 0 } })).planned.persons.client!.estate.liquidityShortfall).toBe(0);
  });

  it('trades retirement saving against the budget, with the tax relief shown', () => {
    const { planned, summary } = run(withPerson({ extraRetirement: 3_000 }));
    const r = planned.persons.client!.retirement;
    expect(r.projectedCapitalReal).toBeGreaterThan(today.persons.client!.retirement.projectedCapitalReal);
    expect(today.cashflow.surplus - planned.cashflow.surplus).toBeCloseTo(3_000, 6);
    expect(summary.taxRelief).toBeGreaterThan(0);
    expect(summary.taxRelief).toBeLessThan(3_000 * 12);
  });

  it('moves retirement and income targets', () => {
    const later = run(withPerson({ retirementAge: 68 })).planned.persons.client!.retirement;
    expect(later.retirementAge).toBe(68);
    expect(later.funded).toBeGreaterThan(today.persons.client!.retirement.funded);
    const modest = run(withPerson({ incomeWanted: 60_000 })).planned.persons.client!.retirement;
    expect(modest.targetMonthlyToday).toBe(60_000);
    expect(modest.funded).toBeGreaterThan(today.persons.client!.retirement.funded);
  });

  it('caps a spending cut at discretionary spending', () => {
    const cap = discretionarySpending(today);
    expect(cap).toBeGreaterThan(0);
    const { planned, summary } = run({ ...emptyPlan(), spendingCut: cap + 50_000 });
    expect(planned.cashflow.surplus - today.cashflow.surplus).toBeCloseTo(cap, 6);
    expect(summary.monthly).toBeCloseTo(-cap, 6);
  });

  it('settles debts from the bonus, freeing their repayments', () => {
    const cards = doc.liabilities.filter((l) => l.type === 'credit-card' || l.type === 'personal-loan');
    const { planned, summary } = run({ ...emptyPlan(), settleDebts: cards.map((l) => l.id) });
    const freed = cards.reduce((s, l) => s + l.monthlyRepayment, 0);
    expect(planned.cashflow.surplus - today.cashflow.surplus).toBeCloseTo(freed, 6);
    expect(summary.bonusUsed).toBe(cards.reduce((s, l) => s + l.balance, 0));
  });

  it('funds education and moves goals', () => {
    const child = doc.dependants.find((d) => d.relationship === 'child')!;
    const goal = doc.goals[0];
    const { planned } = run({ ...emptyPlan(), education: { [child.id]: 2_000 }, goalDelay: { [goal.id]: 2 } });
    const e = (a: typeof today) => a.education.find((x) => x.dependantId === child.id)!;
    expect(e(planned).funded).toBeGreaterThan(e(today).funded);
    const g = (a: typeof today) => a.goals.find((x) => x.id === goal.id)!;
    expect(g(planned).years).toBe(g(today).years + 2);
    expect(g(planned).funded).toBeGreaterThan(g(today).funded);
  });

  it('stress-tests returns', () => {
    const { planned, summary } = run({ ...emptyPlan(), returnShift: -0.01 });
    expect(planned.persons.client!.retirement.projectedCapitalReal).toBeLessThan(today.persons.client!.retirement.projectedCapitalReal);
    expect(summary.changes.map((c) => c.kind)).toEqual(['test']);
  });

  it('bounds stored plans', () => {
    const p = normalisePlan({
      people: { client: { retirementAge: 200, lifeCover: { amount: -5, premium: 'x' } }, nobody: {} },
      spendingCut: Infinity,
      settleDebts: [1, 'a'],
      goalDelay: { g: 99 },
      returnShift: 1,
    });
    expect(p.people.client!.retirementAge).toBe(80);
    expect(p.people.client!.lifeCover).toEqual({ amount: 0, premium: 0 });
    expect(p.spendingCut).toBe(0);
    expect(p.settleDebts).toEqual(['a']);
    expect(p.goalDelay.g).toBe(20);
    expect(p.returnShift).toBe(0.03);
    expect(normalisePlan('nonsense')).toEqual(emptyPlan());
  });
});

describe('adding an agreed plan to the record of advice', () => {
  const card = doc.liabilities.find((l) => l.type === 'credit-card')!;
  const neo = doc.dependants.find((d) => d.name.startsWith('Neo'))!;
  const plan: PlanLevers = {
    ...emptyPlan(),
    people: {
      client: { ...emptyPersonPlan(), retirementAge: 67, lifeCover: { amount: 2_500_000, premium: 450 }, estateCover: { amount: 900_000, premium: 120 }, extraRetirement: 3_000 },
    },
    spendingCut: 2_000,
    settleDebts: [card.id],
    education: { [neo.id]: 1_500 },
  };

  it('updates matching recommendations rather than duplicating them', () => {
    const u = planToAdvice(doc, plan, today, '15 October 2026');
    const life = u.recommendations.filter((r) => r.area === 'life' && r.lifeAssured === 'client');
    expect(life).toHaveLength(1);
    expect(life[0].amount).toBe(2_500_000);
    expect(life[0].premiumMonthly).toBe(450);
    expect(life[0].rationale).toContain('Agreed in the planning session on 15 October 2026');
    const estate = u.recommendations.filter((r) => r.productType === 'Life cover payable to the estate');
    expect(estate).toHaveLength(1);
    const ra = u.recommendations.filter((r) => r.area === 'retirement' && r.lifeAssured === 'client');
    expect(ra).toHaveLength(1);
    expect(ra[0].premiumMonthly).toBe(3_000);
    const edu = u.recommendations.filter((r) => r.area === 'education' && r.rationale.includes(neo.name));
    expect(edu).toHaveLength(1);
    expect(edu[0].premiumMonthly).toBe(1_500);
    expect(u.recommendations.length).toBe(doc.advice.recommendations.length + 1);
    expect(u.retirement.client.retirementAge).toBe(67);
    expect(u.summary).toContain('Planning session, 15 October 2026: reduce discretionary spending by');
  });

  it('can be applied twice without repeating itself', () => {
    const once = planToAdvice(doc, plan, today, '15 October 2026');
    const doc2 = structuredClone(doc);
    doc2.advice.recommendations = once.recommendations;
    doc2.advice.summary = once.summary;
    doc2.retirement = once.retirement;
    const twice = planToAdvice(doc2, planAfterAdvice(plan), analyse(doc2, now), '15 October 2026');
    expect(twice.recommendations.length).toBe(once.recommendations.length);
    const life = twice.recommendations.find((r) => r.area === 'life' && r.lifeAssured === 'client')!;
    expect(life.rationale.match(/Agreed in the planning session/g)).toHaveLength(1);
    expect(twice.summary.match(/Planning session,/g)).toHaveLength(1);
    expect(twice.retirement.client.retirementAge).toBe(67);
    expect(twice.lines.some((l) => l.startsWith('Change Thabo’s retirement age'))).toBe(false);
  });
});

describe('starting points', () => {
  it('can close every measured protection, retirement and education gap', () => {
    const { planned } = run(closeEveryGap(doc, today, now));
    for (const k of today.people) {
      const p = planned.persons[k]!;
      expect(p.death.shortfall).toBeLessThan(1);
      expect(p.incomeProtection.shortfall).toBeLessThan(1);
      expect(p.disability.shortfall).toBeLessThan(1);
      expect(p.severeIllness.shortfall).toBeLessThan(1);
      expect(p.estate.liquidityShortfall).toBeLessThan(1);
      expect(p.retirement.funded).toBeGreaterThan(0.99);
    }
    for (const e of planned.education) expect(e.funded).toBeGreaterThan(0.99);
    for (const g of planned.goals) expect(g.funded).toBeGreaterThan(0.99);
    expect(planned.emergency.shortfall).toBeLessThan(1);
  });

  it('counts goal saving in the budget and emergency savings against the bonus', () => {
    const goal = doc.goals[0];
    const { planned, summary } = run({ ...emptyPlan(), goalSaving: { [goal.id]: 1_000 }, emergencyTopUp: 50_000 });
    expect(today.cashflow.surplus - planned.cashflow.surplus).toBeCloseTo(1_000, 6);
    expect(planned.goals.find((g) => g.id === goal.id)!.funded).toBeGreaterThan(today.goals.find((g) => g.id === goal.id)!.funded);
    expect(planned.emergency.provision - today.emergency.provision).toBeCloseTo(50_000, 6);
    expect(summary.bonusUsed).toBe(50_000);
  });

  it('can start from the recorded recommendations', () => {
    const plan = planFromAdvice(doc, today);
    expect(plan.people.client!.lifeCover.amount).toBe(doc.advice.recommendations.find((r) => r.area === 'life' && r.lifeAssured === 'client')!.amount);
    expect(plan.people.spouse!.extraRetirement).toBeGreaterThan(0);
    expect(Object.keys(plan.education)).toHaveLength(2);
    expect(plan.settleDebts.length).toBeGreaterThan(0);
  });

  it('undoes one change at a time', () => {
    const plan = closeEveryGap(doc, today, now);
    const { summary } = run(plan);
    for (const c of summary.changes) {
      const after = summarisePlan(doc, withoutChange(plan, c.key), today, analyse(applyPlan(doc, withoutChange(plan, c.key), today), now));
      expect(after.changes.map((x) => x.key)).not.toContain(c.key);
      expect(after.changes).toHaveLength(summary.changes.length - 1);
    }
  });
});
