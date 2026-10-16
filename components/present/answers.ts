import type { Analysis, Line, RetirementResult, Status } from '@/lib/fna/analysis';
import type { FnaDocument, PersonKey } from '@/lib/fna/types';
import { money, moneyCompact } from '@/lib/format';

/*
 * The questions a planning session answers, one per screen. Everything here is
 * pure: given today's analysis and the analysis with the plan applied, it says
 * what each answer is — in a few words for the agenda, and in a sentence for
 * the question's own screen.
 */

export type QuestionKey = 'life' | 'income' | 'disability' | 'illness' | 'estate' | 'retirement' | 'education' | 'goals' | 'emergency' | 'afford';

/** The order a planning conversation usually takes. */
export const QUESTIONS: QuestionKey[] = ['life', 'income', 'disability', 'illness', 'estate', 'retirement', 'education', 'goals', 'emergency', 'afford'];

const PER_PERSON: ReadonlySet<QuestionKey> = new Set<QuestionKey>(['life', 'income', 'disability', 'illness', 'estate', 'retirement']);
export const isPerPerson = (q: QuestionKey) => PER_PERSON.has(q);

/** Short names, for navigation. */
export const TOPIC: Record<QuestionKey, string> = {
  life: 'If someone dies',
  income: 'If someone can’t work',
  disability: 'Disability',
  illness: 'Serious illness',
  estate: 'The estate',
  retirement: 'Retirement',
  education: 'Studies',
  goals: 'Goals',
  emergency: 'Emergencies',
  afford: 'Affordability',
};

export interface Ctx {
  doc: FnaDocument;
  today: Analysis;
  planned: Analysis;
}

export const firstName = (a: Analysis, k: PersonKey) => a.persons[k]?.name.split(' ')[0] || (k === 'client' ? 'Client' : 'Spouse');

type CoverNeed = 'death' | 'incomeProtection' | 'disability' | 'severeIllness';
const NEED: Partial<Record<QuestionKey, CoverNeed>> = { life: 'death', income: 'incomeProtection', disability: 'disability', illness: 'severeIllness' };

const sum = <T>(xs: T[], f: (x: T) => number) => xs.reduce((s, x) => s + f(x), 0);
const RANK: Record<Status, number> = { na: 0, covered: 1, partial: 2, shortfall: 3 };
const worst = (ss: Status[]): Status => ss.reduce<Status>((w, s) => (RANK[s] > RANK[w] ? s : w), 'na');

/* ------------------------------------------------------------------ */
/* Who and what a question is about                                    */
/* ------------------------------------------------------------------ */

/** The people a per-person question applies to. */
export const peopleFor = (q: QuestionKey, c: Ctx): PersonKey[] => {
  if (!PER_PERSON.has(q)) return [];
  return c.today.people.filter((k) => {
    const p = c.today.persons[k]!;
    const need = NEED[q];
    if (need) return p[need].need > 0.5;
    if (q === 'estate') return p.estate.cashRequired > 0.5;
    return p.retirement.targetMonthlyToday > 0.5;
  });
};

/** Whether the question has anything to answer for this family. */
export const applies = (q: QuestionKey, c: Ctx): boolean => {
  if (PER_PERSON.has(q)) return peopleFor(q, c).length > 0;
  if (q === 'education') return c.today.education.some((e) => e.capitalAtStart > 0.5);
  if (q === 'goals') return c.today.goals.some((g) => g.futureCost > 0.5);
  if (q === 'emergency') return c.today.emergency.need > 0.5;
  return c.today.cashflow.takeHome > 0;
};

/** The person a per-person screen shows: the one chosen, if the question applies to them. */
export const personFor = (q: QuestionKey, c: Ctx, chosen: PersonKey): PersonKey => {
  const ks = peopleFor(q, c);
  return ks.includes(chosen) ? chosen : (ks[0] ?? 'client');
};

/* ------------------------------------------------------------------ */
/* Measures — what is needed, what today provides, what the plan adds  */
/* ------------------------------------------------------------------ */

export interface Measure {
  key: string;
  label: string;
  /** When the money is needed, for studies and goals. */
  when?: string;
  need: number;
  today: number;
  plan: number;
  monthly?: boolean;
}

export const shortOf = (m: Pick<Measure, 'need' | 'plan'>) => Math.max(0, m.need - m.plan);

/** Met in full, at least 60% met, or less. Only a need met in full counts as met here. */
export const measureStatus = (m: Pick<Measure, 'need' | 'plan'>): Status => {
  if (m.need <= 0.5) return 'na';
  if (shortOf(m) <= 0.5) return 'covered';
  return m.plan / m.need >= 0.6 ? 'partial' : 'shortfall';
};

export const measures = (q: QuestionKey, c: Ctx, k: PersonKey = 'client'): Measure[] => {
  const { today: t, planned: p } = c;
  const year = new Date().getFullYear();
  const need = NEED[q];
  if (need) {
    const tn = t.persons[k]![need];
    const pn = p.persons[k]![need];
    return [{ key: q, label: pn.title, need: pn.need, today: tn.provision, plan: pn.provision, monthly: pn.monthly }];
  }
  switch (q) {
    case 'estate': {
      const te = t.persons[k]!.estate;
      const pe = p.persons[k]!.estate;
      return [{ key: q, label: 'Cash to settle the estate', need: pe.cashRequired, today: te.cashAvailable, plan: pe.cashAvailable }];
    }
    case 'emergency':
      return [{ key: q, label: 'Emergency savings', need: p.emergency.need, today: t.emergency.provision, plan: p.emergency.provision }];
    case 'education':
      return p.education
        .filter((e) => e.capitalAtStart > 0.5)
        .map((e) => ({
          key: e.dependantId,
          label: `${e.name.split(' ')[0] || 'Child'}’s studies`,
          when: e.yearsToTertiary > 0 ? `from ${year + Math.round(e.yearsToTertiary)}` : 'under way',
          need: e.capitalAtStart,
          today: t.education.find((x) => x.dependantId === e.dependantId)?.projectedSavings ?? 0,
          plan: e.projectedSavings,
        }));
    case 'goals':
      return p.goals
        .filter((g) => g.futureCost > 0.5)
        .map((g) => ({ key: g.id, label: g.name || 'Goal', when: `in ${year + g.years}`, need: g.futureCost, today: t.goals.find((x) => x.id === g.id)?.projected ?? 0, plan: g.projected }));
    default:
      return [];
  }
};

/** What the need is made of, and what is already there — for “why this much?”. */
export const whyLines = (q: QuestionKey, c: Ctx, k: PersonKey = 'client'): { need: Line[]; have: Line[] } | null => {
  const need = NEED[q];
  if (need) {
    const n = c.planned.persons[k]![need];
    return { need: n.needLines, have: n.provisionLines.filter((l) => Math.abs(l.amount) >= 0.5) };
  }
  if (q === 'estate') {
    const e = c.planned.persons[k]!.estate;
    return { need: e.lines.filter((l) => Math.abs(l.amount) >= 0.5), have: [{ label: 'Cash, and policies paying the estate', amount: e.cashAvailable }] };
  }
  if (q === 'emergency') return { need: c.planned.emergency.needLines, have: c.planned.emergency.provisionLines };
  return null;
};

/* ------------------------------------------------------------------ */
/* Verdicts — the answer in a few words                                */
/* ------------------------------------------------------------------ */

export interface Verdict {
  status: Status;
  text: string;
}

const runsOutAt = (r: RetirementResult) => (r.depletionAge !== null && r.depletionAge < r.planningAge ? Math.floor(r.depletionAge) : null);

const needVerdict = (need: number, have: number, monthly?: boolean, met = 'Covered'): Verdict => {
  const status = measureStatus({ need, plan: have });
  if (status === 'na') return { status, text: 'Not needed' };
  if (status === 'covered') return { status, text: met };
  const short = need - have;
  return { status, text: monthly ? `${money(short)} a month short` : `${moneyCompact(short)} short` };
};

const retirementVerdict = (r: RetirementResult): Verdict => {
  if (r.targetMonthlyToday <= 0.5) return { status: 'na', text: 'No income set' };
  const out = runsOutAt(r);
  if (out === null) return { status: 'covered', text: 'On track' };
  return { status: r.sustainableMonthlyToday / r.targetMonthlyToday >= 0.6 ? 'partial' : 'shortfall', text: `Runs out at ${out}` };
};

const savingVerdict = (rows: Measure[], monthly: number, lump: number, met: string): Verdict => {
  const status = worst(rows.map(measureStatus));
  if (status === 'na' || status === 'covered') return { status, text: status === 'na' ? 'Not needed' : met };
  const parts = [monthly > 0.5 ? `${money(monthly)} a month` : '', lump > 0.5 ? `${moneyCompact(lump)} now` : ''].filter(Boolean);
  return { status, text: parts.length ? `Needs ${parts.join(' + ')}` : 'Short' };
};

/** One analysis's answer, in a few words. */
const verdictIn = (q: QuestionKey, c: Ctx, a: Analysis, rows: Measure[], k: PersonKey): Verdict => {
  const need = NEED[q];
  if (need) {
    const n = a.persons[k]![need];
    return needVerdict(n.need, n.provision, n.monthly);
  }
  switch (q) {
    case 'estate': {
      const e = a.persons[k]!.estate;
      return needVerdict(e.cashRequired, e.cashAvailable, false, 'Enough cash');
    }
    case 'retirement':
      return retirementVerdict(a.persons[k]!.retirement);
    case 'education':
      return savingVerdict(
        rows,
        sum(a.education, (e) => e.monthlyRequired),
        sum(a.education, (e) => e.lumpSumRequired),
        'Paid for',
      );
    case 'goals':
      return savingVerdict(
        rows,
        sum(a.goals, (g) => g.monthlyRequired),
        0,
        'On track',
      );
    case 'emergency': {
      const v = needVerdict(a.emergency.need, a.emergency.provision);
      return v.status === 'covered' || v.status === 'na' ? v : { status: v.status, text: `${a.ratios.emergencyMonths.toFixed(1)} of ${c.doc.assumptions.emergencyMonths} months` };
    }
    case 'afford': {
      const s = a.cashflow.surplus;
      return s >= -0.5 ? { status: 'covered', text: `${money(Math.max(0, s))} a month free` } : { status: 'shortfall', text: `${money(-s)} a month over` };
    }
    default:
      return { status: 'na', text: '' };
  }
};

/** The answer in a few words, today and with the plan. */
export const verdicts = (q: QuestionKey, c: Ctx, k: PersonKey = 'client'): { today: Verdict; plan: Verdict } => {
  const rows = measures(q, c, k);
  const todayRows = rows.map((r) => ({ ...r, plan: r.today }));
  return { today: verdictIn(q, c, c.today, todayRows, k), plan: verdictIn(q, c, c.planned, rows, k) };
};

/* ------------------------------------------------------------------ */
/* Questions, in words                                                 */
/* ------------------------------------------------------------------ */

/** The question as its own screen asks it. */
export const questionText = (q: QuestionKey, c: Ctx, k: PersonKey = 'client'): string => {
  const n = firstName(c.today, k);
  switch (q) {
    case 'life':
      return `If ${n} died, would the family be OK?`;
    case 'income':
      return `If ${n} couldn’t work, would the income carry on?`;
    case 'disability':
      return `If ${n} were disabled for good, would there be money for the big costs?`;
    case 'illness':
      return `If ${n} fell seriously ill, would there be money to recover?`;
    case 'estate':
      return `When ${n} dies, will there be cash to settle the estate?`;
    case 'retirement': {
      const r = c.planned.persons[k]!.retirement;
      return r.retired ? `Will ${n}’s savings last?` : `Can ${n} retire at ${r.retirementAge} on ${money(r.targetMonthlyToday)} a month?`;
    }
    case 'education': {
      const rows = measures(q, c);
      return rows.length === 1 ? `Will ${rows[0].label} be paid for?` : 'Will the children’s studies be paid for?';
    }
    case 'goals': {
      const rows = measures(q, c);
      return rows.length === 1 ? `Will “${rows[0].label}” happen on time?` : 'Will your goals happen on time?';
    }
    case 'emergency':
      return 'Is there enough put aside for emergencies?';
    case 'afford':
      return 'Can you afford the plan?';
  }
};

/** The question as the agenda asks it: about the household when it applies to both people. */
export const agendaText = (q: QuestionKey, c: Ctx): string => {
  const ks = peopleFor(q, c);
  if (!PER_PERSON.has(q) || ks.length < 2) return questionText(q, c, ks[0]);
  switch (q) {
    case 'life':
      return 'If one of you died, would the family be OK?';
    case 'income':
      return 'If one of you couldn’t work, would the income carry on?';
    case 'disability':
      return 'If one of you were disabled for good, would there be money for the big costs?';
    case 'illness':
      return 'If one of you fell seriously ill, would there be money to recover?';
    case 'estate':
      return 'When one of you dies, will there be cash to settle the estate?';
    default:
      return 'Can you each retire when you want, on the income you want?';
  }
};

/* ------------------------------------------------------------------ */
/* Answers — the answer in a sentence                                  */
/* ------------------------------------------------------------------ */

/** Plain text, with `{ b }` marking the figure to emphasise. */
export type Part = string | { b: string };

export interface Answer {
  status: Status;
  /** “Yes.”, “Not yet.”, “Closer.” … */
  lead: string;
  parts: Part[];
}

export const answerText = (a: Answer) => [a.lead, a.parts.map((p) => (typeof p === 'string' ? p : p.b)).join('')].filter(Boolean).join(' ');

interface NeedCopy {
  met: (n: string) => string;
  short: (n: string, x: string) => Part[];
  none: (n: string) => string;
}

const COPY: Partial<Record<QuestionKey, NeedCopy>> = {
  life: { met: () => 'The family would be provided for.', short: (_n, x) => ['The family would be ', { b: x }, ' short'], none: (n) => `No life cover is needed for ${n}.` },
  income: { met: (n) => `${n}’s income would carry on.`, short: (n, x) => [`${n}’s income would be `, { b: x }, ' short'], none: (n) => `${n} has no earned income to protect.` },
  disability: { met: () => 'There would be enough for the big costs.', short: (_n, x) => ['There would be ', { b: x }, ' too little'], none: (n) => `No disability cover is needed for ${n}.` },
  illness: { met: () => 'There would be enough to recover.', short: (_n, x) => ['There would be ', { b: x }, ' too little'], none: (n) => `No severe illness cover is needed for ${n}.` },
  estate: { met: () => 'There would be enough cash to settle the estate.', short: (_n, x) => ['The estate would be ', { b: x }, ' short of cash'], none: (n) => `${n}’s estate needs no extra cash.` },
};

const leadFor = (status: Status, todayStatus: Status, change: number, nearly: boolean) => {
  if (status === 'covered') return todayStatus === 'covered' ? 'Yes.' : 'Yes, with the plan.';
  if (nearly) return 'Almost.';
  if (change < -0.5) return 'Closer.';
  if (change > 0.5) return 'Further away.';
  return 'Not yet.';
};

const needAnswer = (q: QuestionKey, n: string, m: Measure): Answer => {
  const copy = COPY[q]!;
  const status = measureStatus(m);
  const todayStatus = measureStatus({ need: m.need, plan: m.today });
  if (status === 'na') return { status, lead: '', parts: [copy.none(n)] };
  if (status === 'covered') return { status, lead: leadFor(status, todayStatus, 0, false), parts: [copy.met(n)] };
  const fmt = (x: number) => (m.monthly ? `${money(x)} a month` : moneyCompact(x));
  const short = shortOf(m);
  const before = shortOf({ need: m.need, plan: m.today });
  const tail = short < before - 0.5 ? `, down from ${fmt(before)}.` : short > before + 0.5 ? `, up from ${fmt(before)}.` : '.';
  return { status, lead: leadFor(status, todayStatus, short - before, m.plan / m.need >= 0.95), parts: [...copy.short(n, fmt(short)), tail] };
};

const savingAnswer = (rows: Measure[], monthly: number, monthlyBefore: number, lump: number, what: { met: string; them: string }): Answer => {
  const status = worst(rows.map(measureStatus));
  const todayStatus = worst(rows.map((r) => measureStatus({ need: r.need, plan: r.today })));
  if (status === 'na') return { status, lead: '', parts: ['Nothing needs saving for.'] };
  if (status === 'covered') return { status, lead: leadFor(status, todayStatus, 0, false), parts: [what.met] };
  const parts: Part[] = ['It would take '];
  if (monthly > 0.5) parts.push({ b: `${money(monthly)} a month` }, ' more');
  if (monthly > 0.5 && lump > 0.5) parts.push(', and ');
  if (lump > 0.5) parts.push({ b: moneyCompact(lump) }, ' now');
  parts.push(` to ${what.them}`);
  const change = monthly - monthlyBefore;
  parts.push(change < -0.5 ? ` — down from ${money(monthlyBefore)} a month.` : change > 0.5 ? ` — up from ${money(monthlyBefore)} a month.` : '.');
  const nearly = rows.every((r) => r.need <= 0.5 || r.plan / r.need >= 0.95);
  return { status, lead: leadFor(status, todayStatus, change, nearly), parts };
};

export const answer = (q: QuestionKey, c: Ctx, k: PersonKey = 'client', changes = 0): Answer => {
  const n = firstName(c.today, k);
  if (COPY[q]) return needAnswer(q, n, measures(q, c, k)[0]);
  switch (q) {
    case 'retirement': {
      const t = c.today.persons[k]!.retirement;
      const p = c.planned.persons[k]!.retirement;
      const v = retirementVerdict(p);
      const tv = retirementVerdict(t);
      if (v.status === 'na') return { status: 'na', lead: '', parts: [`No retirement income has been set for ${n}.`] };
      const out = runsOutAt(p);
      const before = runsOutAt(t);
      if (out === null) return { status: 'covered', lead: tv.status === 'covered' ? 'Yes.' : 'Yes, with the plan.', parts: ['The money would last beyond ', { b: `age ${p.planningAge}` }, '.'] };
      const change = before === null ? 1 : before - out;
      const tail = before !== null && out > before ? `, up from ${before}.` : before !== null && out < before ? `, down from ${before}.` : '.';
      return {
        status: v.status,
        lead: leadFor(v.status, tv.status, change, p.sustainableMonthlyToday / p.targetMonthlyToday >= 0.95),
        parts: ['The money would run out at ', { b: `age ${out}` }, tail],
      };
    }
    case 'education': {
      const rows = measures(q, c);
      const met = rows.length === 1 ? `${rows[0].label} would be paid for.` : rows.length === 2 ? 'Both children’s studies would be paid for.' : 'Every child’s studies would be paid for.';
      return savingAnswer(
        rows,
        sum(c.planned.education, (e) => e.monthlyRequired),
        sum(c.today.education, (e) => e.monthlyRequired),
        sum(c.planned.education, (e) => e.lumpSumRequired),
        { met, them: rows.length === 1 ? 'pay for them' : 'pay for them all' },
      );
    }
    case 'goals': {
      const rows = measures(q, c);
      return savingAnswer(
        rows,
        sum(c.planned.goals, (g) => g.monthlyRequired),
        sum(c.today.goals, (g) => g.monthlyRequired),
        0,
        {
          met: rows.length === 1 ? 'It would be paid for on time.' : 'Every goal would be paid for on time.',
          them: rows.length === 1 ? 'reach it on time' : 'reach them all on time',
        },
      );
    }
    case 'emergency': {
      const m = measures(q, c)[0];
      const status = measureStatus(m);
      const target = c.doc.assumptions.emergencyMonths;
      const months = c.planned.ratios.emergencyMonths;
      const before = c.today.ratios.emergencyMonths;
      if (status === 'na') return { status, lead: '', parts: ['No emergency fund is needed.'] };
      const todayStatus = measureStatus({ need: m.need, plan: m.today });
      if (status === 'covered') return { status, lead: leadFor(status, todayStatus, 0, false), parts: [`There would be ${months.toFixed(1)} months of essentials put aside; ${target} are advised.`] };
      const tail = months > before + 0.05 ? `, up from ${before.toFixed(1)}` : '';
      return {
        status,
        lead: leadFor(status, todayStatus, before - months, m.plan / m.need >= 0.95),
        parts: ['Savings would cover ', { b: `${months.toFixed(1)} months` }, ` of essentials${tail}; ${target} are advised.`],
      };
    }
    case 'afford': {
      const t = c.today.cashflow;
      const p = c.planned.cashflow;
      const cost = p.totalOutgoings - t.totalOutgoings;
      if (changes === 0)
        return t.surplus >= -0.5
          ? { status: 'covered', lead: 'Today, yes.', parts: [{ b: `${money(t.surplus)} a month` }, ' is free before any changes.'] }
          : { status: 'shortfall', lead: 'Not today.', parts: ['Spending is already ', { b: `${money(-t.surplus)} a month` }, ' more than comes in.'] };
      if (p.surplus >= -0.5)
        return {
          status: 'covered',
          lead: 'Yes.',
          parts: cost > 0.5 ? ['After the plan, ', { b: `${money(p.surplus)} a month` }, ' is still free.'] : ['With the plan, ', { b: `${money(p.surplus)} a month` }, ' would be free.'],
        };
      if (cost > 0.5) return { status: 'shortfall', lead: 'Not yet.', parts: ['The plan costs ', { b: `${money(cost)} a month` }, ` — ${money(-p.surplus)} more than is free.`] };
      return {
        status: 'shortfall',
        lead: p.surplus > t.surplus + 0.5 ? 'Closer.' : 'Not yet.',
        parts: ['Even with the plan, spending is ', { b: `${money(-p.surplus)} a month` }, ' more than comes in.'],
      };
    }
    default:
      return { status: 'na', lead: '', parts: [] };
  }
};

/* ------------------------------------------------------------------ */
/* The plan's changes, by question                                     */
/* ------------------------------------------------------------------ */

const CHANGE_QUESTION: Record<string, QuestionKey> = {
  age: 'retirement',
  income: 'retirement',
  ra: 'retirement',
  lifeCover: 'life',
  estateCover: 'estate',
  incomeProtection: 'income',
  disability: 'disability',
  severeIllness: 'illness',
};

/** Which question a change in the plan summary belongs to. */
export const questionOfChange = (key: string): QuestionKey => {
  if (key === 'cut' || key.startsWith('debt-')) return 'afford';
  if (key === 'emergency') return 'emergency';
  if (key === 'returns') return 'retirement';
  if (key.startsWith('edu-')) return 'education';
  if (key.startsWith('goal')) return 'goals';
  return CHANGE_QUESTION[key.slice(key.indexOf('-') + 1)] ?? 'afford';
};
