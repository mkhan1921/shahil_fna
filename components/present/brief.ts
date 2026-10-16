import type { Line, NeedResult } from '@/lib/fna/analysis';
import { fvEscalatingContributions, monthlyRate } from '@/lib/fna/finance';
import { personPlan } from '@/lib/fna/plan';
import type { PersonKey, PlanLevers } from '@/lib/fna/types';
import { money, moneyCompact, pct } from '@/lib/format';
import { firstName, measures, type Ctx, type QuestionKey } from './answers';

/*
 * The depth behind each question: what the scenario is made of (a bridge from
 * each part of the need, through what is in place, to what is still short),
 * the points the conversation has to settle, and the assumptions the figures
 * rest on. Everything is read from the analysis; nothing here recalculates a
 * need, except splitting projected retirement savings into its sources.
 */

export type PlanCtx = Ctx & { plan: PlanLevers };

/** One step of a bridge. `add` and `less` move the running total; `total` and `result` restate it from zero. */
export type BridgeKind = 'add' | 'less' | 'total' | 'plan' | 'result';

export interface BridgeRow {
  key: string;
  label: string;
  value: number;
  kind: BridgeKind;
  /** For results: whether the amount is good news or bad. */
  tone?: 'good' | 'bad';
  note?: string;
}

export interface Bridge {
  rows: BridgeRow[];
  monthly?: boolean;
  /** A reference the total is measured against, drawn as a line. */
  target?: { value: number; label: string };
}

export interface AddressItem {
  title: string;
  figure?: string;
  text: string;
}

export interface AddressGroup {
  heading: string;
  items: AddressItem[];
}

const pos = (n: number) => n > 0.5;
const sum = <T>(xs: T[], f: (x: T) => number) => xs.reduce((s, x) => s + f(x), 0);
const rate = (r: number) => `${(r * 100).toFixed(1).replace(/\.0$/, '')}%`;

const resultRow = (short: number, words: { short: string; over: string; met: string }): BridgeRow => ({
  key: 'result',
  kind: 'result',
  value: short,
  label: short > 0.5 ? words.short : short < -0.5 ? words.over : words.met,
  tone: short > 0.5 ? 'bad' : 'good',
});

/** The running total after each row, as the bridge draws it. */
export const runningTotals = (rows: BridgeRow[]) => {
  let run = 0;
  return rows.map((r) => {
    const from = r.kind === 'total' || r.kind === 'result' ? 0 : run;
    const to = r.kind === 'total' || r.kind === 'result' ? r.value : run + r.value;
    run = to;
    return { from, to };
  });
};

/* ------------------------------------------------------------------ */
/* Bridges                                                             */
/* ------------------------------------------------------------------ */

const needBridge = (t: NeedResult, p: NeedResult, planLabel: string): Bridge => {
  const rows: BridgeRow[] = p.needLines.filter((l) => pos(l.amount)).map((l, i) => ({ key: `need-${i}`, label: l.label, value: l.amount, kind: 'add', note: l.note }));
  rows.push({ key: 'need', label: 'Needed', value: p.need, kind: 'total' });
  t.provisionLines.filter((l) => pos(l.amount)).forEach((l, i) => rows.push({ key: `have-${i}`, label: l.label, value: -l.amount, kind: 'less' }));
  const added = p.provision - t.provision;
  if (Math.abs(added) > 0.5) rows.push({ key: 'plan', label: planLabel, value: -added, kind: 'plan' });
  rows.push(resultRow(p.need - p.provision, { short: 'Still short', over: 'More than needed', met: 'Fully provided for' }));
  return { rows, monthly: p.monthly };
};

const NEED_OF = { life: 'death', income: 'incomeProtection', disability: 'disability', illness: 'severeIllness' } as const;
const PLAN_LABEL: Record<keyof typeof NEED_OF, string> = {
  life: 'Added by the plan',
  income: 'Income protection in the plan',
  disability: 'Disability cover in the plan',
  illness: 'Severe illness cover in the plan',
};

const estateBridge = (c: Ctx, k: PersonKey): Bridge => {
  const t = c.today.persons[k]!.estate;
  const p = c.planned.persons[k]!.estate;
  const rows: BridgeRow[] = p.lines.filter((l) => pos(l.amount)).map((l, i) => ({ key: `cost-${i}`, label: l.label, value: l.amount, kind: 'add' }));
  rows.push({ key: 'need', label: 'Cash the executor needs', value: p.cashRequired, kind: 'total' });
  if (pos(t.cashAvailable)) rows.push({ key: 'have', label: 'Cash in the estate', value: -t.cashAvailable, kind: 'less' });
  const added = p.cashAvailable - t.cashAvailable;
  if (Math.abs(added) > 0.5) rows.push({ key: 'plan', label: 'Cover paid to the estate (plan)', value: -added, kind: 'plan' });
  rows.push(resultRow(p.cashRequired - p.cashAvailable, { short: 'Still short of cash', over: 'Cash to spare', met: 'Enough cash' }));
  return { rows };
};

const emergencyBridge = (c: Ctx): Bridge => {
  const months = c.doc.assumptions.emergencyMonths;
  const cf = c.planned.cashflow;
  const t = c.today.emergency;
  const p = c.planned.emergency;
  const rows: BridgeRow[] = [
    { key: 'living', label: `Essential living costs × ${months}`, value: cf.essentialExpenses * months, kind: 'add' },
    { key: 'debt', label: `Debt repayments × ${months}`, value: cf.debtRepayments * months, kind: 'add' },
    { key: 'premiums', label: `Insurance premiums × ${months}`, value: cf.riskPremiums * months, kind: 'add' },
  ].filter((r) => pos(r.value)) as BridgeRow[];
  rows.push({ key: 'need', label: `${months} months of essentials`, value: p.need, kind: 'total' });
  t.provisionLines.filter((l) => pos(l.amount)).forEach((l, i) => rows.push({ key: `have-${i}`, label: l.label, value: -l.amount, kind: 'less' }));
  const added = p.provision - t.provision;
  if (Math.abs(added) > 0.5) rows.push({ key: 'plan', label: 'Put aside from the bonus (plan)', value: -added, kind: 'plan' });
  rows.push(resultRow(p.need - p.provision, { short: 'Still short', over: 'More than advised', met: 'Fully provided for' }));
  return { rows };
};

/** Projected retirement savings split into where they come from, in today's money. */
export const retirementSources = (c: PlanCtx, k: PersonKey) => {
  const r = c.planned.persons[k]!.retirement;
  const a = c.doc.assumptions;
  const pre = a.preRetirementReturn + c.plan.returnShift;
  const months = Math.round(r.yearsToRetirement * 12);
  const inflator = Math.pow(1 + a.cpi, r.yearsToRetirement);
  const grown = r.currentCapital * Math.pow(1 + monthlyRate(pre), months);
  const extra = r.retired ? 0 : personPlan(c.plan, k).extraRetirement;
  const extraGrown = fvEscalatingContributions(extra, months / 12, pre, a.salaryEscalation);
  const contributions = Math.max(0, r.projectedCapital - grown - extraGrown);
  return { grown: grown / inflator, contributions: contributions / inflator, extra: extraGrown / inflator, total: r.projectedCapitalReal, needed: r.requiredCapitalReal };
};

const retirementBridge = (c: PlanCtx, k: PersonKey): Bridge => {
  const r = c.planned.persons[k]!.retirement;
  const s = retirementSources(c, k);
  const age = r.retirementAge;
  const rows: BridgeRow[] = [];
  if (pos(s.grown)) rows.push({ key: 'grown', label: `Savings today, grown to ${age}`, value: s.grown, kind: 'add' });
  if (pos(s.contributions)) rows.push({ key: 'contrib', label: `Contributions until ${age}`, value: s.contributions, kind: 'add' });
  if (pos(s.extra)) rows.push({ key: 'plan', label: 'Extra saving in the plan', value: s.extra, kind: 'plan' });
  rows.push({ key: 'total', label: `Expected at ${age}`, value: s.total, kind: 'total' });
  rows.push(resultRow(s.needed - s.total, { short: `Short of what is needed`, over: 'More than is needed', met: 'Exactly what is needed' }));
  return { rows, target: { value: s.needed, label: `Needed at ${age}` } };
};

const budgetBridge = (c: Ctx): Bridge => {
  const t = c.today.cashflow;
  const p = c.planned.cashflow;
  const rows: BridgeRow[] = [{ key: 'pay', label: 'Take-home pay', value: t.takeHome, kind: 'total' }];
  const cats = t.byCategory.filter((x) => pos(x.amount)).sort((a, b) => b.amount - a.amount);
  const top = cats.slice(0, 4);
  top.forEach((x) => rows.push({ key: `cat-${x.key}`, label: x.label, value: -x.amount, kind: 'less' }));
  const rest = t.livingExpenses - sum(top, (x) => x.amount);
  if (pos(rest)) rows.push({ key: 'cat-rest', label: 'Other living costs', value: -rest, kind: 'less' });
  if (pos(t.debtRepayments)) rows.push({ key: 'debt', label: 'Debt repayments', value: -t.debtRepayments, kind: 'less' });
  if (pos(t.riskPremiums)) rows.push({ key: 'premiums', label: 'Insurance premiums', value: -t.riskPremiums, kind: 'less' });
  if (pos(t.savingsContributions)) rows.push({ key: 'savings', label: 'Savings & investments', value: -t.savingsContributions, kind: 'less' });
  rows.push({ key: 'today', label: t.surplus >= -0.5 ? 'Left over today' : 'Overspent today', value: t.surplus, kind: 'result', tone: t.surplus >= -0.5 ? 'good' : 'bad' });
  const step = (key: string, label: string, delta: number) => Math.abs(delta) > 0.5 && rows.push({ key, label, value: delta, kind: 'plan' });
  step('plan-pay', 'Change in take-home pay', p.takeHome - t.takeHome);
  step('plan-premiums', 'New cover premiums', -(p.riskPremiums - t.riskPremiums));
  step('plan-savings', 'New saving', -(p.savingsContributions - t.savingsContributions));
  step('plan-living', 'Spending cut', -(p.livingExpenses - t.livingExpenses));
  step('plan-debt', 'Debt repayments ended', -(p.debtRepayments - t.debtRepayments));
  if (rows[rows.length - 1].kind === 'plan')
    rows.push({ key: 'plan-left', label: p.surplus >= -0.5 ? 'Left over with the plan' : 'Overspent with the plan', value: p.surplus, kind: 'result', tone: p.surplus >= -0.5 ? 'good' : 'bad' });
  return { rows, monthly: true };
};

export const bridgeFor = (q: QuestionKey, c: PlanCtx, k: PersonKey = 'client'): Bridge | null => {
  if (q in NEED_OF) {
    const key = NEED_OF[q as keyof typeof NEED_OF];
    return needBridge(c.today.persons[k]![key], c.planned.persons[k]![key], PLAN_LABEL[q as keyof typeof NEED_OF]);
  }
  if (q === 'estate') return estateBridge(c, k);
  if (q === 'emergency') return emergencyBridge(c);
  if (q === 'retirement') return retirementBridge(c, k);
  if (q === 'afford') return budgetBridge(c);
  return null;
};

/* ------------------------------------------------------------------ */
/* What each question must address                                     */
/* ------------------------------------------------------------------ */

const item = (title: string, text: string, amount?: number, monthly = false): AddressItem => ({
  title,
  text,
  figure: amount === undefined ? undefined : monthly ? `${money(amount)} a month` : money(amount),
});

const lifeNeedText = (c: Ctx, k: PersonKey, l: Line): string => {
  const a = c.doc.assumptions;
  const p = c.planned.persons[k]!;
  const n = firstName(c.today, k);
  if (l.label === 'Settle debts') return 'The home loan, vehicle finance and other debts are paid from the estate before anyone inherits.';
  if (l.label.startsWith('Estate costs')) return `Executor’s fees of ${rate(a.executorFeeRate)} plus VAT, the Master’s fee, transfer costs, capital gains tax on death and estate duty.`;
  if (l.label === 'Funeral') return 'The funeral, which has to be paid quickly: bank accounts are frozen on death.';
  if (l.label === 'Cash bequests') return 'Cash left to people in the will.';
  if (l.label.startsWith('Adjustment fund')) return `${a.emergencyMonths} months of ${n}’s after-tax income, to carry the family while the estate is wound up.`;
  if (l.label.startsWith('Tertiary')) return 'University costs for the children, in today’s money, less what is already saved for them.';
  if (l.label.startsWith('Income for dependants')) {
    const years = p.deathIncomeYears;
    const term =
      a.deathIncomeTerm === 'fixed'
        ? `for a fixed ${years} years`
        : a.deathIncomeTerm === 'spouse-retirement'
          ? `for ${years} years, until the spouse retires`
          : `for ${years} years, until the youngest child is independent`;
    return `${pct(a.deathIncomeReplacement)} of ${n}’s after-tax income, ${money(p.deathIncomeMonthly)} a month, ${term}. It rises with inflation (${rate(a.cpi)}) and the capital earns ${rate(a.riskCapitalReturn)} a year.`;
  }
  if (l.label.startsWith('Support for parents')) return 'Support for parents or others who depend on this income, for as long as was stated.';
  return 'Other amounts the family asked to provide for.';
};

const lifeHaveText = (l: Line): string => {
  if (l.label === 'Existing life cover') return 'Life policies on this life, paid to the family.';
  if (l.label === 'Funeral cover') return 'Funeral policies, which pay out within days.';
  if (l.label.startsWith('Family income')) return 'Policies that pay a monthly income, valued as a lump sum.';
  if (l.label.startsWith('Retirement fund')) return 'Paid by the fund’s trustees under section 37C of the Pension Funds Act. The trustees decide who receives it, and it can take up to a year.';
  if (l.label.startsWith('Liquid')) return 'Cash and investments that could be drawn on.';
  return 'Already in place.';
};

const willText = (c: Ctx, k: PersonKey) => {
  const w = c.doc.estate[k];
  const n = firstName(c.today, k);
  if (w.hasWill === 'yes')
    return w.willDate ? `${n}’s will is dated ${w.willDate}. Check it names guardians for the children and an executor.` : `${n} has a will. Check it is current and names guardians and an executor.`;
  if (w.hasWill === 'no') return `${n} has no will, so the law would decide who inherits and a court who cares for the children.`;
  return `We could not confirm that ${n} has a valid will.`;
};

const coverGroups = (q: keyof typeof NEED_OF, c: PlanCtx, k: PersonKey): AddressGroup[] => {
  const key = NEED_OF[q];
  const t = c.today.persons[k]![key];
  const p = c.planned.persons[k]![key];
  const per = c.planned.persons[k]!;
  const n = firstName(c.today, k);
  const a = c.doc.assumptions;
  const monthly = !!p.monthly;
  const groups: AddressGroup[] = [];
  const needs = p.needLines.filter((l) => pos(l.amount));
  const have = t.provisionLines.filter((l) => pos(l.amount));
  const added = p.provision - t.provision;

  if (q === 'life') {
    groups.push({ heading: 'What the family would need', items: needs.map((l) => item(l.label, lifeNeedText(c, k, l), l.amount)) });
    groups.push({
      heading: 'What is already there',
      items: have.length ? have.map((l) => item(l.label, lifeHaveText(l), l.amount)) : [item('Nothing yet', `No cover or savings would pay out on ${n}’s death.`)],
    });
    groups.push({
      heading: 'Also to settle',
      items: [
        item('Who receives the money', 'Cover with a named beneficiary pays within days and is not part of the estate. Cover paid to the estate attracts executor’s fees and counts for estate duty.'),
        item('The will and guardians', willText(c, k)),
      ],
    });
  }
  if (q === 'income') {
    const earned = per.income.earnedAfterTaxMonthly;
    groups.push({
      heading: 'What needs protecting',
      items: needs.map((l) =>
        item(
          l.label,
          `${pct(a.incomeProtectionTarget)} of ${n}’s after-tax earned income of ${money(earned)} a month. Insurers cap total cover at about the full after-tax income, and rental or investment income carries on regardless.`,
          l.amount,
          true,
        ),
      ),
    });
    groups.push({
      heading: 'What is already there',
      items: have.length
        ? have.map((l) => item(l.label, 'Only benefits that pay until retirement age count; temporary benefits stop after a set period.', l.amount, true))
        : [item('Nothing yet', `No income protection that pays to retirement is in place for ${n}.`)],
    });
    groups.push({
      heading: 'Also to settle',
      items: [
        item('The waiting period', 'How long sick leave and savings must carry the household before the benefit starts, usually one to three months.'),
        item('How long it pays, and how it grows', 'It should pay to retirement age and rise with inflation, so the income keeps its value over a long claim.'),
        item('The definition of disability', 'Own-occupation cover pays when someone can no longer do their own job, not only when they can do no job at all.'),
        item('Tax', 'Benefits from policies you own are tax-free. Group benefits through an employer may be taxed.'),
      ],
    });
  }
  if (q === 'disability') {
    const text = (l: Line) =>
      l.label === 'Settle debts'
        ? 'Debts that would be paid off on disability, leaving out those already covered by credit life.'
        : l.label.startsWith('Home, vehicle')
          ? `An allowance of ${money(a.disabilityAdjustments)} to adapt the home and car and pay medical costs medical aid will not.`
          : 'The employer’s retirement contributions stop on disability. This is what they would have added by retirement, in today’s money.';
    groups.push({ heading: 'What the big costs would be', items: needs.map((l) => item(l.label, text(l), l.amount)) });
    groups.push({
      heading: 'What is already there',
      items: have.length ? have.map((l) => item(l.label, 'Lump-sum disability benefits on existing policies.', l.amount)) : [item('Nothing yet', `No lump-sum disability cover is in place for ${n}.`)],
    });
    groups.push({
      heading: 'Also to settle',
      items: [
        item('Income is a separate question', 'Replacing the monthly income is the job of income protection. This lump sum is for once-off costs.'),
        item('The definition of disability', 'Own- or suited-occupation cover is far easier to claim on than any-occupation cover.'),
      ],
    });
  }
  if (q === 'illness') {
    const gross = per.income.earnedGrossMonthly;
    groups.push({
      heading: 'What recovery would cost',
      items: needs.map((l) =>
        item(l.label, `${a.severeIllnessMonths} months of ${n}’s gross income of ${money(gross)} a month: time off to recover, a partner taking leave, and costs medical aid won’t cover.`, l.amount),
      ),
    });
    groups.push({
      heading: 'What is already there',
      items: have.length ? have.map((l) => item(l.label, 'Severe illness benefits on existing policies.', l.amount)) : [item('Nothing yet', `No severe illness cover is in place for ${n}.`)],
    });
    groups.push({
      heading: 'Also to settle',
      items: [
        item('How the cover pays', 'Severity-based cover pays part of the benefit for less serious conditions, so it pays more often.'),
        item('Accelerated or stand-alone', 'Accelerated cover reduces the life cover when it pays; stand-alone cover leaves it intact.'),
      ],
    });
  }
  if (Math.abs(added) > 0.5) {
    const lv = personPlan(c.plan, k);
    const cover = { life: lv.lifeCover, income: lv.incomeProtection, disability: lv.disability, illness: lv.severeIllness }[q];
    const premium = cover.premium ? `at ${money(cover.premium)} a month` : 'with the premium still to be quoted';
    groups[1].items.push(
      item(
        'Added by the plan',
        cover.amount > 0.5 ? `New cover of ${monthly ? `${money(cover.amount)} a month` : moneyCompact(cover.amount)}, ${premium}.` : 'From other changes in the plan.',
        added,
        monthly,
      ),
    );
  }
  return groups;
};

const ESTATE_TEXT: [RegExp, string][] = [
  [/^Debts payable/, 'Debts are settled before anything passes to the heirs.'],
  [/^Executor/, 'The executor’s fee, charged on the gross value of the estate.'],
  [/^Master/, 'The Master of the High Court’s fee, set by tariff.'],
  [/^Conveyancing/, 'Transferring property into the heirs’ names.'],
  [/^Bond cancellation/, 'Cancelling the bond over the home.'],
  [/^Advertising/, 'Notices to creditors, final tax returns and other costs.'],
  [/^Funeral/, 'The funeral.'],
  [/^Capital gains/, 'Assets are treated as sold on death; gains above the R440,000 exclusion are taxed.'],
  [/^Estate duty/, '20% of the dutiable estate above R3.5 million, 25% above R30 million. What passes to a spouse is deducted.'],
  [/^Accrual/, 'Owed to the spouse under the accrual system.'],
  [/^Cash bequests/, 'Cash left to people in the will.'],
];

const estateGroups = (c: PlanCtx, k: PersonKey): AddressGroup[] => {
  const t = c.today.persons[k]!.estate;
  const p = c.planned.persons[k]!.estate;
  const n = firstName(c.today, k);
  const lines = p.lines.filter((l) => pos(l.amount));
  const added = p.cashAvailable - t.cashAvailable;
  const have = [item('Cash in the estate', 'Cash, investments and policies that pay into the estate.', t.cashAvailable)];
  if (Math.abs(added) > 0.5) have.push(item('Added by the plan', 'Life cover paid to the estate, after the executor’s fee it attracts.', added));
  return [
    { heading: 'What the executor must pay', items: lines.map((l) => item(l.label, ESTATE_TEXT.find(([re]) => re.test(l.label))?.[1] ?? '', l.amount)) },
    { heading: 'What is already there', items: have },
    {
      heading: 'Also to settle',
      items: [
        item('Assets that are hard to sell', `Property or a business may have to be sold to raise cash if the estate is short, often at a poor price.`),
        item('The will and executor', willText(c, k)),
        item('Gross estate', `${n}’s estate is valued at ${money(p.grossEstate)}, with ${money(p.dutiable)} subject to estate duty.`),
      ],
    },
  ];
};

const retirementGroups = (c: PlanCtx, k: PersonKey): AddressGroup[] => {
  const r = c.planned.persons[k]!.retirement;
  const per = c.planned.persons[k]!;
  const a = c.doc.assumptions;
  const shift = c.plan.returnShift;
  const s = retirementSources(c, k);
  const n = firstName(c.today, k);
  const gross = per.income.grossMonthlyEquivalent;
  const wanted: AddressItem[] = [item('Income wanted', `In today’s money${gross > 0 ? `: ${pct(r.targetMonthlyToday / gross)} of ${n}’s gross income today` : ''}.`, r.targetMonthlyToday, true)];
  if (pos(r.otherIncomeToday)) wanted.push(item('Other income in retirement', 'Income from elsewhere, such as rent, which the savings do not need to provide.', r.otherIncomeToday, true));
  wanted.push(
    item(
      'Years to provide for',
      `${Math.round(r.yearsInRetirement)} years, from ${r.retirementAge} to ${r.planningAge}, with the income rising with inflation (${rate(a.cpi)}) and the capital earning ${rate(a.postRetirementReturn + shift)} a year.`,
    ),
    item(`Capital needed at ${r.retirementAge}`, 'Enough to pay that income for the whole period, in today’s money.', s.needed),
  );
  const building: AddressItem[] = [];
  if (!r.retired) {
    building.push(
      item('Saved today', 'In retirement funds and investments set aside for retirement.', r.currentCapital),
      item(
        'Contributions',
        `From ${n} and the employer${pos(personPlan(c.plan, k).extraRetirement) ? ', with the plan’s extra saving' : ''}, rising ${rate(a.salaryEscalation)} a year with salary.`,
        r.monthlyContributions,
        true,
      ),
      item(`Expected at ${r.retirementAge}`, `With growth of ${rate(a.preRetirementReturn + shift)} a year before retirement, in today’s money.`, s.total),
    );
  } else building.push(item('Saved today', 'Retirement savings the income is drawn from.', r.currentCapital));
  const watch: AddressItem[] = [
    item('How much is drawn each year', `The income is ${pct(r.initialDrawdown, 1)} of the capital at retirement. About 4–5% a year is sustainable; a living annuity allows 2.5% to 17.5%.`),
    item('How long the money must last', `The plan runs to age ${r.planningAge}. Many people live longer, so a later planning age is safer.`),
    item('Tax relief', 'Contributions to retirement funds are deductible up to 27.5% of income, to R430,000 a year.'),
  ];
  return [
    { heading: 'What retirement needs', items: wanted },
    { heading: r.retired ? 'What there is' : 'What is building up', items: building },
    { heading: 'Also to settle', items: watch },
  ];
};

const educationGroups = (c: PlanCtx): AddressGroup[] => {
  const a = c.doc.assumptions;
  const year = new Date().getFullYear();
  const kids = c.planned.education.filter((e) => e.capitalAtStart > 0.5);
  const items = kids.map((e) => {
    const d = c.doc.dependants.find((x) => x.id === e.dependantId);
    const name = e.name.split(' ')[0] || 'Child';
    const start = e.yearsToTertiary > 0 ? `from ${year + Math.round(e.yearsToTertiary)}` : 'already under way';
    const extra = c.plan.education[e.dependantId] ?? 0;
    const save =
      e.monthlyRequired > 0.5
        ? ` Saving ${money(e.monthlyRequired)} a month more would close the gap.`
        : e.lumpSumRequired > 0.5
          ? ` ${money(e.lumpSumRequired)} is needed now.`
          : ' It is fully provided for.';
    const study = d ? `${d.tertiaryYears} years of study ${start}, at ${money(d.tertiaryCostAnnual)} a year in today’s money. ` : '';
    return item(
      name,
      `${study}${moneyCompact(e.capitalAtStart)} is needed when studies start; ${moneyCompact(e.projectedSavings)} is expected to be saved by then${extra ? `, including the plan’s ${money(extra)} a month` : ''}.${save}`,
      e.capitalAtStart,
    );
  });
  return [
    { heading: 'What each child’s studies need', items },
    {
      heading: 'Also to settle',
      items: [
        item('Education inflation', `Fees rise about ${rate(a.educationInflation)} a year, faster than general inflation (${rate(a.cpi)}).`),
        item('School fees', 'School fees are paid from the monthly budget, so they are not counted here.'),
        item('Where to save', 'A tax-free savings account or unit trusts, chosen to match the years until studies start.'),
      ],
    },
  ];
};

const goalGroups = (c: PlanCtx): AddressGroup[] => {
  const a = c.doc.assumptions;
  const year = new Date().getFullYear();
  const items = c.planned.goals
    .filter((g) => g.futureCost > 0.5)
    .map((g) => {
      const d = c.doc.goals.find((x) => x.id === g.id);
      const save = g.monthlyRequired > 0.5 ? ` Saving ${money(g.monthlyRequired)} a month more would reach it on time.` : ' It is on track.';
      return item(
        g.name,
        `${money(d?.cost ?? 0)} today becomes ${moneyCompact(g.futureCost)} by ${year + g.years} with inflation of ${rate(a.cpi)}. ${moneyCompact(g.projected)} is expected to be saved by then.${save}`,
        g.futureCost,
      );
    });
  return [
    { heading: 'What each goal needs', items },
    {
      heading: 'Also to settle',
      items: [
        item('Which goal comes first', 'When the budget is tight, rank the goals and fund them in that order rather than all a little.'),
        item('Moving the date', 'A later date gives savings longer to grow, but the cost rises with inflation too.'),
      ],
    },
  ];
};

const emergencyGroups = (c: PlanCtx): AddressGroup[] => {
  const a = c.doc.assumptions;
  const cf = c.planned.cashflow;
  const t = c.today.emergency;
  const essential = cf.essentialExpenses + cf.debtRepayments + cf.riskPremiums;
  const have = t.provisionLines.filter((l) => pos(l.amount)).map((l) => item(l.label, 'Savings that can be reached within days.', l.amount));
  if (pos(c.plan.emergencyTopUp)) have.push(item('Put aside from the bonus (plan)', 'A once-off top-up into a money-market account.', c.plan.emergencyTopUp));
  return [
    {
      heading: 'What an emergency would cost',
      items: [
        item(
          'Essential spending',
          `Essential living costs of ${money(cf.essentialExpenses)}, debt repayments of ${money(cf.debtRepayments)} and insurance premiums of ${money(cf.riskPremiums)}.`,
          essential,
          true,
        ),
        item('Months advised', `${a.emergencyMonths} months. Six is safer when income is variable or self-employed.`),
      ],
    },
    { heading: 'What is already there', items: have.length ? have : [item('Nothing yet', 'No cash or money-market savings are recorded.')] },
    {
      heading: 'Also to settle',
      items: [item('Where to keep it', 'In an instant-access account, separate from day-to-day money, so it is there when needed and not spent before.')],
    },
  ];
};

const affordGroups = (c: PlanCtx): AddressGroup[] => {
  const t = c.today.cashflow;
  const r = c.today.ratios;
  return [
    {
      heading: 'Where the money goes',
      items: [
        item('Take-home pay', 'After tax, UIF and deductions through the payroll.', t.takeHome, true),
        item('Living costs', `Of which ${money(t.essentialExpenses)} is essential.`, t.livingExpenses, true),
        item('Debt repayments', `${pct(r.debtToIncome)} of income; the guide is under 36%.`, t.debtRepayments, true),
        item('Insurance premiums', `${pct(r.premiumsToIncome)} of income.`, t.riskPremiums, true),
        item('Savings & investments', `${pct(r.savingsRate)} of income; the guide is 15% or more.`, t.savingsContributions, true),
      ],
    },
    {
      heading: 'Also to settle',
      items: [
        item('The annual bonus', `About ${money(t.bonusNet)} after tax. It suits once-off uses: emergency savings or settling expensive debt.`),
        item('Premiums still to be quoted', 'New cover adds to the monthly cost once the insurer quotes, so the plan should leave room for it.'),
        item('What gives first', 'If the plan does not fit, decide together what to scale back: cover amounts, saving, or the timing of goals.'),
      ],
    },
  ];
};

export const addressFor = (q: QuestionKey, c: PlanCtx, k: PersonKey = 'client'): AddressGroup[] => {
  if (q in NEED_OF) return coverGroups(q as keyof typeof NEED_OF, c, k);
  switch (q) {
    case 'estate':
      return estateGroups(c, k);
    case 'retirement':
      return retirementGroups(c, k);
    case 'education':
      return educationGroups(c);
    case 'goals':
      return goalGroups(c);
    case 'emergency':
      return emergencyGroups(c);
    default:
      return affordGroups(c);
  }
};

/* ------------------------------------------------------------------ */
/* The account — a need set out as a balanced ledger                   */
/* ------------------------------------------------------------------ */

export interface AccountRow {
  key: string;
  label: string;
  amount: number;
  gloss?: string;
  tone?: 'plan' | 'short' | 'over';
}

export interface Account {
  monthly: boolean;
  /** The heading of the side that lists what is needed. */
  needHeading: string;
  need: AccountRow[];
  meet: AccountRow[];
  /** Both sides total this: the need, or what is in place when that is more. */
  total: number;
  /** Points from the brief that are not lines of the account, for the matters to settle. */
  notes: AddressItem[];
}

const ACCOUNT_QUESTIONS: QuestionKey[] = ['life', 'income', 'disability', 'illness', 'estate', 'emergency'];

/**
 * A need set out as a ledger: what would be needed on one side; what is in
 * place, what the plan adds and what is still to be found on the other. The
 * figures are the bridge's, so the two always agree; each line carries the
 * plain-words explanation from the brief where there is one.
 */
export const accountFor = (q: QuestionKey, c: PlanCtx, k: PersonKey = 'client'): Account | null => {
  if (!ACCOUNT_QUESTIONS.includes(q)) return null;
  const bridge = bridgeFor(q, c, k);
  if (!bridge) return null;
  const groups = addressFor(q, c, k);
  const lines = groups.filter((g) => g.heading !== 'Also to settle').flatMap((g) => g.items);
  const settle = groups.filter((g) => g.heading === 'Also to settle').flatMap((g) => g.items);
  const used = new Set<string>();
  const gloss = (...titles: string[]) => {
    for (const t of titles) {
      const it = lines.find((x) => x.title === t);
      if (it) {
        used.add(it.title);
        return it.text;
      }
    }
    return undefined;
  };
  const need: AccountRow[] = [];
  const meet: AccountRow[] = [];
  for (const r of bridge.rows) {
    if (r.kind === 'add') need.push({ key: r.key, label: r.label, amount: r.value, gloss: gloss(r.label) });
    else if (r.kind === 'less') meet.push({ key: r.key, label: r.label, amount: -r.value, gloss: gloss(r.label) });
    else if (r.kind === 'plan') meet.push({ key: r.key, label: r.label, amount: -r.value, gloss: gloss(r.label, 'Added by the plan'), tone: 'plan' });
  }
  const nothing = gloss('Nothing yet');
  if (nothing && !meet.some((r) => r.tone !== 'plan')) meet.unshift({ key: 'nothing', label: 'Nothing yet', amount: 0, gloss: nothing });
  const needed = sum(need, (r) => r.amount);
  const have = sum(meet, (r) => r.amount);
  if (needed - have > 0.5) meet.push({ key: 'short', label: 'Still to be found', amount: needed - have, tone: 'short' });
  else if (have - needed > 0.5) need.push({ key: 'over', label: 'More than is needed', amount: have - needed, tone: 'over' });
  return {
    monthly: !!bridge.monthly,
    needHeading: groups[0]?.heading ?? 'What would be needed',
    need,
    meet,
    total: Math.max(needed, have),
    notes: [...lines.filter((x) => !used.has(x.title)), ...settle],
  };
};

/** The assumptions and caveats behind a question's figures, as the analysis states them. */
export const basisFor = (q: QuestionKey, c: PlanCtx, k: PersonKey = 'client'): string[] => {
  const p = c.planned.persons[k];
  const a = c.doc.assumptions;
  switch (q) {
    case 'life':
      return p?.death.notes ?? [];
    case 'income':
      return p?.incomeProtection.notes ?? [];
    case 'disability':
      return p?.disability.notes ?? [];
    case 'illness':
      return p?.severeIllness.notes ?? [];
    case 'estate':
      return p?.estate.notes ?? [];
    case 'retirement':
      return p?.retirement.notes ?? [];
    case 'emergency':
      return c.planned.emergency.notes;
    case 'education':
      return [
        `Costs rise ${rate(a.educationInflation)} a year and savings earn ${rate(a.riskCapitalReturn + c.plan.returnShift)} a year. Amounts needed are what the studies will cost when they start.`,
      ];
    case 'goals':
      return [`Costs rise with inflation of ${rate(a.cpi)} and savings earn ${rate(a.riskCapitalReturn + c.plan.returnShift)} a year.`];
    default:
      return ['Monthly figures from the budget captured in the client file. The bonus is not counted in the monthly budget.'];
  }
};

/* ------------------------------------------------------------------ */
/* Studies by year, for the cost timeline                              */
/* ------------------------------------------------------------------ */

export interface CostSeries {
  key: string;
  name: string;
  points: { year: number; cost: number }[];
}

export const studyCosts = (c: Ctx): CostSeries[] =>
  c.planned.education.filter((e) => e.schedule.length).map((e) => ({ key: e.dependantId, name: e.name.split(' ')[0] || 'Child', points: e.schedule.map((s) => ({ year: s.year, cost: s.cost })) }));

/** The needs and goals measured for the overview, per person and for the household. */
const COVER_NAME: [QuestionKey, string][] = [
  ['life', 'Life cover'],
  ['income', 'Income if unable to work'],
  ['disability', 'Disability lump sum'],
  ['illness', 'Severe illness'],
  ['estate', 'Cash for the estate'],
];

export interface FundedRow {
  key: string;
  group: string;
  label: string;
  /** Share of the need provided for, 0–1. */
  today: number;
  plan: number;
  q: QuestionKey;
}

export const fundedRows = (c: Ctx): FundedRow[] => {
  const rows: FundedRow[] = [];
  const share = (have: number, need: number) => (need > 0.5 ? Math.min(1, Math.max(0, have / need)) : 1);
  for (const k of c.today.people) {
    const g = firstName(c.today, k);
    for (const [q, label] of COVER_NAME) {
      const [m] = measures(q, c, k);
      if (m && m.need > 0.5) rows.push({ key: `${q}-${k}`, group: g, label, today: share(m.today, m.need), plan: share(m.plan, m.need), q });
    }
    const t = c.today.persons[k]!.retirement;
    const p = c.planned.persons[k]!.retirement;
    if (p.requiredCapitalReal > 0.5) rows.push({ key: `retirement-${k}`, group: g, label: 'Retirement savings', today: t.funded, plan: p.funded, q: 'retirement' });
  }
  for (const q of ['education', 'goals', 'emergency'] as QuestionKey[])
    for (const m of measures(q, c))
      if (m.need > 0.5) rows.push({ key: `${q}-${m.key}`, group: 'Household', label: q === 'emergency' ? 'Emergency fund' : m.label, today: share(m.today, m.need), plan: share(m.plan, m.need), q });
  return rows;
};
