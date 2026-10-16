/**
 * Planning levers: the trade-offs explored with the client in presentation
 * mode. A plan never edits the client's data. `applyPlan` returns an adjusted
 * copy of the file, so the ordinary analysis runs on it unchanged and every
 * figure in the planner comes from the same tested engine as the report.
 */
import { money, moneyCompact } from '../format';
import { analyse, type Analysis } from './analysis';
import { newRecommendation } from './defaults';
import type { CoverLever, FnaDocument, NeedArea, PersonKey, PersonPlan, PlanLevers, Policy, PolicyType, Recommendation, RecommendationAction } from './types';

export type { CoverLever, PersonPlan, PlanLevers };

export type CoverKey = 'lifeCover' | 'estateCover' | 'incomeProtection' | 'disability' | 'severeIllness';
export const COVER_KEYS: CoverKey[] = ['lifeCover', 'estateCover', 'incomeProtection', 'disability', 'severeIllness'];

export const COVER: Record<CoverKey, { label: string; type: PolicyType; area: NeedArea; productType: string; monthly?: boolean }> = {
  lifeCover: { label: 'Life cover', type: 'life', area: 'life', productType: 'Life cover' },
  estateCover: { label: 'Life cover paid to the estate', type: 'life', area: 'estate', productType: 'Life cover payable to the estate' },
  incomeProtection: { label: 'Income protection', type: 'income-protection', area: 'income-protection', productType: 'Income protection (to retirement, CPI-linked)', monthly: true },
  disability: { label: 'Disability lump sum', type: 'disability-lump', area: 'disability', productType: 'Lump-sum disability (own/suited occupation)' },
  severeIllness: { label: 'Severe illness cover', type: 'severe-illness', area: 'severe-illness', productType: 'Severe illness cover (comprehensive, severity-based)' },
};

const cover = (): CoverLever => ({ amount: 0, premium: 0 });

export const emptyPersonPlan = (): PersonPlan => ({
  retirementAge: null,
  incomeWanted: null,
  extraRetirement: 0,
  lifeCover: cover(),
  estateCover: cover(),
  incomeProtection: cover(),
  disability: cover(),
  severeIllness: cover(),
});

export const emptyPlan = (): PlanLevers => ({ version: 1, people: {}, spendingCut: 0, settleDebts: [], education: {}, goalDelay: {}, goalSaving: {}, emergencyTopUp: 0, returnShift: 0 });

const num = (v: unknown, lo: number, hi: number, fallback = 0) => (typeof v === 'number' && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : fallback);
const numOrNull = (v: unknown, lo: number, hi: number) => (typeof v === 'number' && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : null);
const record = (v: unknown, lo: number, hi: number): Record<string, number> => {
  const out: Record<string, number> = {};
  if (v && typeof v === 'object') for (const [k, x] of Object.entries(v)) if (typeof x === 'number' && Number.isFinite(x) && x !== 0) out[k] = Math.min(hi, Math.max(lo, x));
  return out;
};
const coverOf = (v: unknown): CoverLever => {
  const o = (v && typeof v === 'object' ? v : {}) as Partial<CoverLever>;
  return { amount: num(o.amount, 0, 1e9), premium: num(o.premium, 0, 1e7) };
};

/** A plan from stored or imported data, with every value checked and bounded. */
export const normalisePlan = (raw: unknown): PlanLevers => {
  if (!raw || typeof raw !== 'object') return emptyPlan();
  const r = raw as Partial<PlanLevers>;
  const people: PlanLevers['people'] = {};
  for (const k of ['client', 'spouse'] as PersonKey[]) {
    const p = r.people?.[k] as Partial<PersonPlan> | undefined;
    if (!p || typeof p !== 'object') continue;
    people[k] = {
      retirementAge: numOrNull(p.retirementAge, 40, 80),
      incomeWanted: numOrNull(p.incomeWanted, 0, 1e7),
      extraRetirement: num(p.extraRetirement, 0, 1e7),
      lifeCover: coverOf(p.lifeCover),
      estateCover: coverOf(p.estateCover),
      incomeProtection: coverOf(p.incomeProtection),
      disability: coverOf(p.disability),
      severeIllness: coverOf(p.severeIllness),
    };
  }
  return {
    version: 1,
    people,
    spendingCut: num(r.spendingCut, 0, 1e7),
    settleDebts: Array.isArray(r.settleDebts) ? r.settleDebts.filter((x): x is string => typeof x === 'string') : [],
    education: record(r.education, 0, 1e6),
    goalDelay: record(r.goalDelay, -10, 20),
    goalSaving: record(r.goalSaving, 0, 1e6),
    emergencyTopUp: num(r.emergencyTopUp, 0, 1e8),
    returnShift: num(r.returnShift, -0.03, 0.03),
  };
};

/** The person's part of the plan, filled in with no-change values. */
export const personPlan = (plan: PlanLevers, k: PersonKey): PersonPlan => ({ ...emptyPersonPlan(), ...plan.people[k] });

/** Monthly living expenses the client could cut: everything not marked essential. */
export const discretionarySpending = (a: Analysis) => Math.max(0, a.cashflow.livingExpenses - a.cashflow.essentialExpenses);

/** The client's file with the plan applied. The original is not changed. */
export const applyPlan = (doc: FnaDocument, plan: PlanLevers, baseline?: Analysis): FnaDocument => {
  const d: FnaDocument = structuredClone(doc);
  const married = doc.household.includeSpouse && ['married', 'life-partner'].includes(doc.household.maritalStatus);

  for (const k of Object.keys(plan.people) as PersonKey[]) {
    const p = personPlan(plan, k);
    if (p.retirementAge !== null) d.retirement[k].retirementAge = p.retirementAge;
    if (p.incomeWanted !== null) {
      d.retirement[k].targetMode = 'amount';
      d.retirement[k].targetMonthly = p.incomeWanted;
    }
    if (p.extraRetirement > 0)
      d.retirementFunds.push({
        id: `plan-ra-${k}`,
        owner: k,
        type: 'retirement-annuity',
        provider: '',
        name: 'Retirement annuity (plan)',
        value: 0,
        savingsPot: 0,
        employeeMonthly: p.extraRetirement,
        employerMonthly: 0,
        viaPayroll: false,
        beneficiariesNominated: true,
        drawdownRate: 0.05,
        notes: '',
      });
    for (const key of COVER_KEYS) {
      const c = p[key];
      if (c.amount <= 0) continue;
      const spec = COVER[key];
      const policy: Policy = {
        id: `plan-${key}-${k}`,
        lifeAssured: k,
        type: spec.type,
        insurer: '',
        policyNumber: '',
        isGroup: false,
        cover: spec.monthly ? 0 : c.amount,
        monthlyBenefit: spec.monthly ? c.amount : 0,
        benefitTermYears: 0,
        waitingPeriodMonths: 3,
        benefitToAge: Math.max(65, d.retirement[k].retirementAge),
        beneficiary: key === 'estateCover' ? 'estate' : married ? 'spouse' : 'children',
        definition: 'own-occupation',
        accelerated: false,
        premiumMonthly: c.premium,
        premiumEscalation: 0.06,
        coverEscalation: 0,
        inceptionDate: '',
        notes: 'Planned cover (not yet in force)',
      };
      d.policies.push(policy);
    }
  }

  if (plan.spendingCut > 0) {
    const cap = baseline ? discretionarySpending(baseline) : plan.spendingCut;
    const cut = Math.min(plan.spendingCut, cap);
    if (cut > 0) d.expenses.custom.push({ id: 'plan-spending-cut', category: 'other', label: 'Spending cut (plan)', amount: -cut, essential: false });
  }
  if (plan.settleDebts.length) d.liabilities = d.liabilities.filter((l) => !plan.settleDebts.includes(l.id));
  for (const dep of d.dependants) dep.educationSavingsMonthly += plan.education[dep.id] ?? 0;
  for (const g of d.goals) {
    g.targetYear += Math.round(plan.goalDelay[g.id] ?? 0);
    const extra = plan.goalSaving[g.id] ?? 0;
    if (extra > 0) {
      // The goal's projection uses its own contribution; the budget sees the debit order as a savings line.
      g.monthlyContribution += extra;
      d.assets.push({
        id: `plan-goal-${g.id}`,
        owner: 'client',
        type: 'unit-trust',
        description: `Saving for ${g.name || 'a goal'} (plan)`,
        provider: '',
        value: 0,
        baseCost: 0,
        monthlyContribution: extra,
        earmark: 'general',
        monthlyIncome: 0,
        bequeathToSpouse: true,
        notes: '',
      });
    }
  }
  if (plan.emergencyTopUp > 0) {
    // Earmarked emergency savings, if the client has any, are the only ones counted; otherwise cash and money market are.
    const earmarked = d.assets.some((x) => x.earmark === 'emergency');
    d.assets.push({
      id: 'plan-emergency',
      owner: d.household.includeSpouse ? 'joint' : 'client',
      type: 'money-market',
      description: 'Emergency savings (plan)',
      provider: '',
      value: plan.emergencyTopUp,
      baseCost: plan.emergencyTopUp,
      monthlyContribution: 0,
      earmark: earmarked ? 'emergency' : 'general',
      monthlyIncome: 0,
      bequeathToSpouse: true,
      notes: '',
    });
  }
  if (plan.returnShift) {
    const s = plan.returnShift;
    d.assumptions.preRetirementReturn += s;
    d.assumptions.postRetirementReturn += s;
    d.assumptions.riskCapitalReturn += s;
  }
  return d;
};

/* ------------------------------------------------------------------ */
/* What the plan changes, and what it costs                            */
/* ------------------------------------------------------------------ */

export interface PlanChange {
  key: string;
  label: string;
  detail?: string;
  /** Change to monthly outgoings: positive costs money, negative frees it. */
  monthly: number;
  /** A product the adviser recommends (as opposed to a decision or a stress test). */
  kind: 'product' | 'decision' | 'test';
}

export interface PlanSummary {
  changes: PlanChange[];
  /** Net change in monthly outgoings. */
  monthly: number;
  premiums: number;
  savings: number;
  /** Cover added without a quoted premium. */
  unquoted: number;
  bonusUsed: number;
  bonusAvailable: number;
  /** Extra tax relief a year on new retirement-annuity contributions (refunded on assessment). */
  taxRelief: number;
  surplusToday: number;
  surplusPlan: number;
}

const first = (a: Analysis, k: PersonKey) => a.persons[k]?.name.split(' ')[0] ?? (k === 'client' ? 'Client' : 'Spouse');

export const summarisePlan = (doc: FnaDocument, plan: PlanLevers, today: Analysis, planned: Analysis): PlanSummary => {
  const changes: PlanChange[] = [];
  let premiums = 0;
  let savings = 0;
  let unquoted = 0;
  for (const k of today.people) {
    const p = personPlan(plan, k);
    const n = first(today, k);
    const r = today.persons[k]!.retirement;
    if (p.retirementAge !== null && p.retirementAge !== doc.retirement[k].retirementAge)
      changes.push({ key: `${k}-age`, label: `${n} retires at ${p.retirementAge}`, detail: `instead of ${doc.retirement[k].retirementAge}`, monthly: 0, kind: 'decision' });
    if (p.incomeWanted !== null && Math.round(p.incomeWanted) !== Math.round(r.targetMonthlyToday))
      changes.push({ key: `${k}-income`, label: `${n} plans on ${money(p.incomeWanted)} a month in retirement`, detail: `instead of ${money(r.targetMonthlyToday)}`, monthly: 0, kind: 'decision' });
    if (p.extraRetirement > 0) {
      savings += p.extraRetirement;
      changes.push({ key: `${k}-ra`, label: `${n} saves ${money(p.extraRetirement)} more a month for retirement`, monthly: p.extraRetirement, kind: 'product' });
    }
    for (const key of COVER_KEYS) {
      const c = p[key];
      if (c.amount <= 0) continue;
      premiums += c.premium;
      if (!c.premium) unquoted++;
      const amt = COVER[key].monthly ? `${money(c.amount)} a month` : moneyCompact(c.amount);
      changes.push({
        key: `${k}-${key}`,
        label: `${COVER[key].label} for ${n}: +${amt}`,
        detail: c.premium ? `${money(c.premium)} a month` : 'premium to be quoted',
        monthly: c.premium,
        kind: 'product',
      });
    }
  }
  const cut = Math.min(plan.spendingCut, discretionarySpending(today));
  if (cut > 0) changes.push({ key: 'cut', label: `Spend ${money(cut)} a month less`, monthly: -cut, kind: 'decision' });
  const settled = doc.liabilities.filter((l) => plan.settleDebts.includes(l.id));
  const bonusUsed = settled.reduce((s, l) => s + l.balance, 0) + plan.emergencyTopUp;
  if (plan.emergencyTopUp > 0) changes.push({ key: 'emergency', label: `Put ${money(plan.emergencyTopUp)} of the bonus into emergency savings`, detail: 'once-off', monthly: 0, kind: 'product' });
  for (const l of settled)
    changes.push({
      key: `debt-${l.id}`,
      label: `Settle the ${(l.description || l.type).toLowerCase()} from the bonus`,
      detail: `${money(l.balance)} once-off`,
      monthly: -l.monthlyRepayment,
      kind: 'product',
    });
  for (const dep of doc.dependants) {
    const x = plan.education[dep.id] ?? 0;
    if (x > 0) {
      savings += x;
      changes.push({ key: `edu-${dep.id}`, label: `Save ${money(x)} more a month for ${dep.name.split(' ')[0] || 'education'}’s studies`, monthly: x, kind: 'product' });
    }
  }
  for (const g of doc.goals) {
    const x = plan.goalSaving[g.id] ?? 0;
    if (x > 0) {
      savings += x;
      changes.push({ key: `goalsave-${g.id}`, label: `Save ${money(x)} more a month for ${g.name || 'a goal'}`, monthly: x, kind: 'product' });
    }
    const y = Math.round(plan.goalDelay[g.id] ?? 0);
    if (y)
      changes.push({
        key: `goal-${g.id}`,
        label: `${g.name || 'Goal'} in ${g.targetYear + y}`,
        detail: `${y > 0 ? 'moved out' : 'brought forward'} ${Math.abs(y)} year${Math.abs(y) === 1 ? '' : 's'}`,
        monthly: 0,
        kind: 'decision',
      });
  }
  if (plan.returnShift)
    changes.push({
      key: 'returns',
      label: `Investment returns ${plan.returnShift > 0 ? '+' : '−'}${(Math.abs(plan.returnShift) * 100).toFixed(1)}% a year`,
      detail: 'stress test',
      monthly: 0,
      kind: 'test',
    });

  const taxRelief = Math.max(0, today.incomes.reduce((s, i) => s + i.tax.annualTax, 0) - planned.incomes.reduce((s, i) => s + i.tax.annualTax, 0));
  return {
    changes,
    monthly: planned.cashflow.totalOutgoings - today.cashflow.totalOutgoings,
    premiums,
    savings,
    unquoted,
    bonusUsed,
    bonusAvailable: today.cashflow.bonusNet,
    taxRelief,
    surplusToday: today.cashflow.surplus,
    surplusPlan: planned.cashflow.surplus,
  };
};

/* ------------------------------------------------------------------ */
/* Carrying an agreed plan into the record of advice                   */
/* ------------------------------------------------------------------ */

const AGREED = /\s*Agreed in the planning session[^.]*\.[^]*$/;
const SESSION = /\n*Planning session, [^\n]*:[^]*?(?=\n\n|$)/g;

export interface AdviceUpdate {
  recommendations: Recommendation[];
  summary: string;
  retirement: FnaDocument['retirement'];
  goals: FnaDocument['goals'];
  /** One line per change, for the adviser to confirm. */
  lines: string[];
}

/**
 * The agreed plan as changes to the record of advice: products become (or
 * update) recommendations awaiting the client's decision; retirement and goal
 * decisions update the file's goals; everything else is noted in the summary.
 */
export const planToAdvice = (doc: FnaDocument, plan: PlanLevers, today: Analysis, date: string): AdviceUpdate => {
  const recs = doc.advice.recommendations.map((r) => ({ ...r }));
  const lines: string[] = [];
  const when = date;
  const agreed = (detail: string) => ` Agreed in the planning session on ${when}: ${detail}.`;
  const upsert = (match: (r: Recommendation) => boolean, make: Partial<Recommendation> & { area: NeedArea; lifeAssured: PersonKey; action: RecommendationAction }, detail: string) => {
    const i = recs.findIndex((r) => r.clientDecision !== 'declined' && match(r));
    if (i >= 0) {
      const r = recs[i];
      recs[i] = {
        ...r,
        amount: make.amount ?? r.amount,
        premiumMonthly: make.premiumMonthly ?? r.premiumMonthly,
        rationale: `${r.rationale.replace(AGREED, '')}${agreed(detail)}`.trim(),
      };
      lines.push(`Update recommendation: ${r.productType || make.productType} — ${detail}`);
    } else {
      const given = Object.fromEntries(Object.entries(make).filter(([, v]) => v !== undefined)) as Partial<Recommendation>;
      recs.push(newRecommendation({ ...given, rationale: agreed(detail).trim() }));
      lines.push(`Add recommendation: ${make.productType} — ${detail}`);
    }
  };

  for (const k of today.people) {
    const p = personPlan(plan, k);
    const n = first(today, k);
    const has = (t: PolicyType) => doc.policies.some((x) => x.lifeAssured === k && !x.isGroup && x.type === t);
    for (const key of COVER_KEYS) {
      const c = p[key];
      if (c.amount <= 0) continue;
      const spec = COVER[key];
      const amount = spec.monthly ? `${money(c.amount)} a month` : money(c.amount);
      const detail = `${spec.label.toLowerCase()} of ${amount} for ${n}${c.premium ? ` at ${money(c.premium)} a month` : ', premium to be quoted'}`;
      upsert(
        (r) => r.lifeAssured === k && r.area === spec.area && (key === 'estateCover' ? r.productType === spec.productType : ['new', 'increase'].includes(r.action)),
        {
          area: spec.area,
          lifeAssured: k,
          action: has(spec.type) && key !== 'estateCover' ? 'increase' : 'new',
          productType: spec.productType,
          amount: c.amount,
          premiumMonthly: c.premium || undefined,
        },
        detail,
      );
    }
    if (p.extraRetirement > 0)
      upsert(
        (r) => r.lifeAssured === k && r.area === 'retirement' && r.action === 'new',
        {
          area: 'retirement',
          lifeAssured: k,
          action: 'new',
          productType: 'Retirement annuity (Regulation 28 compliant)',
          amount: p.extraRetirement,
          premiumMonthly: p.extraRetirement,
          premiumEscalation: doc.assumptions.salaryEscalation,
        },
        `an extra ${money(p.extraRetirement)} a month for ${n}’s retirement`,
      );
  }
  for (const dep of doc.dependants) {
    const x = plan.education[dep.id] ?? 0;
    if (x <= 0) continue;
    const who = dep.name || 'the child';
    upsert(
      (r) => r.area === 'education' && r.premiumMonthly > 0 && !!dep.name && r.rationale.includes(dep.name),
      { area: 'education', lifeAssured: 'client', action: 'new', productType: 'Education investment (TFSA or unit trust)', amount: x, premiumMonthly: x, premiumEscalation: doc.assumptions.cpi },
      `${money(x)} a month towards ${who}’s education`,
    );
  }
  if (plan.emergencyTopUp > 0)
    upsert(
      (r) => r.area === 'emergency',
      { area: 'emergency', lifeAssured: 'client', action: 'new', productType: 'Money market / notice account', amount: plan.emergencyTopUp, premiumMonthly: 0, premiumEscalation: 0 },
      `${money(plan.emergencyTopUp)} from the annual bonus into an emergency fund`,
    );
  for (const g of doc.goals) {
    const x = plan.goalSaving[g.id] ?? 0;
    if (x <= 0) continue;
    const productType = `Savings for ${g.name || 'a goal'}`;
    upsert(
      (r) => r.area === 'investment' && r.productType === productType,
      { area: 'investment', lifeAssured: 'client', action: 'new', productType, amount: x, premiumMonthly: x, premiumEscalation: 0 },
      `${money(x)} a month towards ${g.name || 'the goal'}`,
    );
  }
  const settled = doc.liabilities.filter((l) => plan.settleDebts.includes(l.id));
  if (settled.length) {
    const total = settled.reduce((s, l) => s + l.balance, 0);
    upsert(
      (r) => r.area === 'debt',
      { area: 'debt', lifeAssured: 'client', action: 'restructure', productType: 'Debt reduction plan', amount: total, premiumMonthly: 0 },
      `settle ${settled.map((l) => (l.description || l.type).toLowerCase()).join(' and ')} (${money(total)}) from the annual bonus`,
    );
  }

  /* Goals the client has changed become the file's goals. */
  const retirement = structuredClone(doc.retirement);
  for (const k of today.people) {
    const p = personPlan(plan, k);
    const n = first(today, k);
    if (p.retirementAge !== null && p.retirementAge !== retirement[k].retirementAge) {
      lines.push(`Change ${n}’s retirement age from ${retirement[k].retirementAge} to ${p.retirementAge}`);
      retirement[k].retirementAge = p.retirementAge;
    }
    if (p.incomeWanted !== null && Math.round(p.incomeWanted) !== Math.round(today.persons[k]!.retirement.targetMonthlyToday)) {
      lines.push(`Change ${n}’s retirement income target to ${money(p.incomeWanted)} a month (today’s money)`);
      retirement[k].targetMode = 'amount';
      retirement[k].targetMonthly = p.incomeWanted;
    }
  }
  const goals = doc.goals.map((g) => {
    const y = Math.round(plan.goalDelay[g.id] ?? 0);
    if (!y) return g;
    lines.push(`Move “${g.name || 'Goal'}” from ${g.targetYear} to ${g.targetYear + y}`);
    return { ...g, targetYear: g.targetYear + y };
  });

  /* Decisions that are not products are recorded in the advice summary. */
  const notes: string[] = [];
  const cut = Math.min(plan.spendingCut, discretionarySpending(today));
  if (cut > 0) notes.push(`reduce discretionary spending by ${money(cut)} a month`);
  if (plan.returnShift) notes.push(`the plan was tested with investment returns ${(plan.returnShift * 100).toFixed(1)}% a year ${plan.returnShift < 0 ? 'lower' : 'higher'} than assumed`);
  let summary = doc.advice.summary.replace(SESSION, '').trim();
  if (notes.length) {
    const para = `Planning session, ${when}: ${notes.join('; ')}.`;
    summary = summary ? `${summary}\n\n${para}` : para;
    lines.push(`Note in the advice summary: ${notes.join('; ')}`);
  }
  return { recommendations: recs, summary, retirement, goals, lines };
};

/** After the plan is written into the file, the decisions it made are part of the file. */
export const planAfterAdvice = (plan: PlanLevers): PlanLevers => ({
  ...plan,
  people: Object.fromEntries(Object.entries(plan.people).map(([k, p]) => [k, { ...p, retirementAge: null, incomeWanted: null }])),
  goalDelay: {},
});

/* ------------------------------------------------------------------ */
/* Starting points and undo                                            */
/* ------------------------------------------------------------------ */

const roundUp = (n: number, step: number) => (n > 0 ? Math.ceil(n / step) * step : 0);

/** Cover paid to the estate is reduced by the executor's fee on it, so a little more is needed. */
export const estateCoverFor = (shortfall: number, doc: FnaDocument) => roundUp(shortfall / (1 - doc.assumptions.executorFeeRate * (1 + doc.assumptions.vatRate)), 25_000);

/**
 * A plan that closes every measured gap. Premiums are left for the adviser to
 * quote. Needs move with the plan (cover paid to the estate attracts executor's
 * fees; retirement-annuity tax relief raises the after-tax income to protect),
 * so the plan is topped up until nothing is left open.
 */
export const closeEveryGap = (doc: FnaDocument, today: Analysis, now: Date = new Date()): PlanLevers => {
  const plan = emptyPlan();
  for (const k of today.people) plan.people[k] = emptyPersonPlan();
  for (let round = 0; round < 6; round++) {
    const a = round === 0 ? today : analyse(applyPlan(doc, plan, today), now);
    let changed = false;
    const add = (c: CoverLever, gap: number, step: number) => {
      if (gap > 0.5) {
        c.amount += roundUp(gap, step);
        changed = true;
      }
    };
    for (const k of today.people) {
      const p = a.persons[k]!;
      const lv = plan.people[k]!;
      add(lv.lifeCover, p.death.shortfall, 50_000);
      add(lv.incomeProtection, p.incomeProtection.shortfall, 500);
      add(lv.disability, p.disability.shortfall, 50_000);
      add(lv.severeIllness, p.severeIllness.shortfall, 50_000);
      if (p.estate.liquidityShortfall > 0.5) {
        lv.estateCover.amount += estateCoverFor(p.estate.liquidityShortfall, doc);
        changed = true;
      }
      if (!p.retirement.retired && p.retirement.additionalMonthly > 0.5) {
        lv.extraRetirement += roundUp(p.retirement.additionalMonthly, 100);
        changed = true;
      }
    }
    for (const e of a.education)
      if (e.monthlyRequired > 0.5) {
        plan.education[e.dependantId] = (plan.education[e.dependantId] ?? 0) + roundUp(e.monthlyRequired, 100);
        changed = true;
      }
    for (const g of a.goals)
      if (g.monthlyRequired > 0.5) {
        plan.goalSaving[g.id] = (plan.goalSaving[g.id] ?? 0) + roundUp(g.monthlyRequired, 100);
        changed = true;
      }
    if (a.emergency.shortfall > 0.5) {
      plan.emergencyTopUp += roundUp(a.emergency.shortfall, 1_000);
      changed = true;
    }
    if (!changed) break;
  }
  return plan;
};

/** A plan built from the recommendations already in the record of advice (declined ones left out). */
export const planFromAdvice = (doc: FnaDocument, today: Analysis): PlanLevers => {
  const plan = emptyPlan();
  const live = doc.advice.recommendations.filter((r) => r.clientDecision !== 'declined');
  for (const k of today.people) {
    const p = emptyPersonPlan();
    for (const r of live.filter((x) => x.lifeAssured === k)) {
      const c = { amount: r.amount, premium: r.premiumMonthly };
      if (r.productType === COVER.estateCover.productType) p.estateCover = c;
      else if (r.area === 'life' && ['new', 'increase'].includes(r.action)) p.lifeCover = c;
      else if (r.area === 'income-protection') p.incomeProtection = c;
      else if (r.area === 'disability') p.disability = c;
      else if (r.area === 'severe-illness') p.severeIllness = c;
      else if (r.area === 'retirement' && r.premiumMonthly > 0) p.extraRetirement += r.premiumMonthly;
    }
    plan.people[k] = p;
  }
  for (const d of doc.dependants) {
    const r = live.find((x) => x.area === 'education' && x.premiumMonthly > 0 && !!d.name && x.rationale.includes(d.name));
    if (r) plan.education[d.id] = r.premiumMonthly;
  }
  const em = live.find((r) => r.area === 'emergency' && r.amount > 0);
  if (em) plan.emergencyTopUp = em.amount;
  for (const g of doc.goals) {
    const r = live.find((x) => x.area === 'investment' && x.productType === `Savings for ${g.name || 'a goal'}`);
    if (r) plan.goalSaving[g.id] = r.premiumMonthly;
  }
  if (live.some((r) => r.area === 'debt'))
    plan.settleDebts = doc.liabilities.filter((l) => ['personal-loan', 'credit-card', 'store-card', 'overdraft'].includes(l.type) && l.interestRate >= 0.18 && l.balance > 0).map((l) => l.id);
  return plan;
};

/** The plan without one of the changes listed by `summarisePlan`. */
export const withoutChange = (plan: PlanLevers, key: string): PlanLevers => {
  const next = structuredClone(plan);
  const [head, ...rest] = key.split('-');
  const tail = rest.join('-');
  if (key === 'cut') next.spendingCut = 0;
  else if (key === 'returns') next.returnShift = 0;
  else if (head === 'debt') next.settleDebts = next.settleDebts.filter((id) => id !== tail);
  else if (head === 'edu') delete next.education[tail];
  else if (head === 'goal') delete next.goalDelay[tail];
  else if (head === 'goalsave') delete next.goalSaving[tail];
  else if (key === 'emergency') next.emergencyTopUp = 0;
  else if (head === 'client' || head === 'spouse') {
    const p = next.people[head];
    if (!p) return next;
    if (tail === 'age') p.retirementAge = null;
    else if (tail === 'income') p.incomeWanted = null;
    else if (tail === 'ra') p.extraRetirement = 0;
    else if ((COVER_KEYS as string[]).includes(tail)) p[tail as CoverKey] = { amount: 0, premium: 0 };
  }
  return next;
};
