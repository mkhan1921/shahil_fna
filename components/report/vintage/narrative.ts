import type { Analysis, NeedResult, PersonAnalysis, Status } from '@/lib/fna/analysis';
import type { FnaDocument } from '@/lib/fna/types';
import { money, pct } from '@/lib/format';
import { inWords } from './ink';

/*
 * The analysis restated in plain words: one sentence per matter, ranked so the
 * reader meets the most serious first. Every figure comes from the analysis;
 * nothing here calculates anything new.
 */

export interface Matter {
  tone: 'critical' | 'warning' | 'good';
  text: string;
  weight: number;
}

/** R4.9 million · R892 000 · R16 050 — amounts rounded the way people say them. */
export const said = (n: number) => {
  const a = Math.abs(n);
  if (a >= 1e6) {
    const m = (a / 1e6).toFixed(1).replace(/\.0$/, '');
    return `R${m} million`;
  }
  if (a >= 1e5) return money(Math.round(a / 1e3) * 1e3);
  return money(a);
};

const first = (p: PersonAnalysis) => p.name.split(' ')[0] || 'You';

const SEVERITY: Record<Status, number> = { shortfall: 3, partial: 2, covered: 0, na: 0 };

const need = (n: NeedResult, importance: number) => SEVERITY[n.status] * importance * (1 - Math.min(1, n.funded) * 0.5);

export function matters(doc: FnaDocument, a: Analysis): { attention: Matter[]; good: Matter[] } {
  const out: Matter[] = [];
  const P = a.people.map((k) => a.persons[k]!);
  const family = doc.dependants.length > 0 || a.people.length > 1 ? 'your family' : 'your dependants';
  const kids = doc.dependants.some((d) => d.relationship === 'child');
  const push = (tone: Matter['tone'], text: string, weight: number) => out.push({ tone, text, weight });
  const toneOf = (s: Status): Matter['tone'] => (s === 'shortfall' ? 'critical' : 'warning');

  for (const p of P) {
    const n = first(p);
    const d = p.death;
    if (d.need > 0 && d.shortfall > 0.5)
      push(toneOf(d.status), `If ${n} died, ${family} would be ${said(d.shortfall)} short of the ${said(d.need)} it would need; ${pct(d.funded)} is in place.`, need(d, 1));
    const ip = p.incomeProtection;
    if (ip.need > 0 && ip.shortfall > 0.5)
      push(
        toneOf(ip.status),
        `If ${n} could not work because of illness or injury, ${money(ip.shortfall)} a month of income would not be replaced${ip.provision <= 0 ? ' — none of it is insured today' : ''}.`,
        need(ip, 0.97),
      );
    const dis = p.disability;
    if (dis.need > 0 && dis.shortfall > 0.5)
      push(
        toneOf(dis.status),
        `Permanent disability would leave ${n} ${said(dis.shortfall)} short of what is needed to settle debts, adapt the home and replace lost retirement saving.`,
        need(dis, 0.8),
      );
    const si = p.severeIllness;
    if (si.need > 0 && si.shortfall > 0.5) push(toneOf(si.status), `A severe illness, such as cancer, a heart attack or a stroke, would leave ${n} ${said(si.shortfall)} short.`, need(si, 0.6));
    if (p.funeral.shortfall > 0.5) push('warning', `${n}’s funeral cover falls ${money(p.funeral.shortfall)} short of the expected cost.`, 0.3);

    const e = p.estate;
    if (e.liquidityShortfall > 0.5) {
      const ratio = e.cashRequired > 0 ? e.cashAvailable / e.cashRequired : 1;
      push(
        ratio < 0.6 ? 'critical' : 'warning',
        `On ${n}’s death the executor would need ${said(e.liquidityShortfall)} more in cash than the estate holds, which could force the sale of assets.`,
        (ratio < 0.6 ? 3 : 2) * 0.7 * (1 - ratio * 0.5),
      );
    }
    const will = doc.estate[p.key];
    if (will.hasWill === 'no') push('critical', `${n} has no will, so the law — not ${n} — would decide who inherits${kids ? ', and a court who cares for the children' : ''}.`, 2.6);
    else if (will.hasWill !== 'yes') push('warning', `We could not confirm that ${n} has a valid will.`, 1.2);
    else if (will.willDate) {
      const age = (Date.now() - new Date(will.willDate).getTime()) / (365.25 * 864e5);
      if (age > 5) push('warning', `${n}’s will dates from ${will.willDate.slice(0, 4)} and should be brought up to date.`, 0.5);
    }

    const r = p.retirement;
    if (!r.retired && r.status !== 'covered' && r.status !== 'na') {
      const runsOut = r.depletionAge !== null && r.depletionAge < r.planningAge;
      push(
        toneOf(r.status),
        `${n}’s savings could pay about ${pct(r.replacementRatio)} of today’s gross income until age ${r.planningAge}, against the ${pct(r.targetReplacementRatio)} wanted${runsOut ? `; drawing the full amount, they would run out at about age ${Math.floor(r.depletionAge!)}` : ''}.`,
        SEVERITY[r.status] * 0.75 * (1 - r.funded * 0.5),
      );
    }
  }

  const em = a.emergency;
  if (em.need > 0 && em.shortfall > 0.5)
    push(
      toneOf(em.status),
      `Your emergency savings would cover ${a.ratios.emergencyMonths.toFixed(1)} months of essential spending; ${doc.assumptions.emergencyMonths} months is advised.`,
      need(em, 0.55),
    );

  for (const x of a.education)
    if (x.status !== 'covered' && x.status !== 'na')
      push(
        toneOf(x.status),
        `${x.name.split(' ')[0]}’s education is ${pct(x.funded)} provided for${x.monthlyRequired > 0 ? `; saving ${money(x.monthlyRequired)} a month would close the gap` : ''}.`,
        SEVERITY[x.status] * 0.5 * (1 - x.funded * 0.5),
      );

  const cf = a.cashflow;
  if (cf.surplus < -0.5) push('critical', `You spend ${money(-cf.surplus)} a month more than you take home.`, 2.9);

  /* Matters in good order */
  const good: Matter[] = [];
  const praise = (text: string, weight: number) => good.push({ tone: 'good', text, weight });
  for (const p of P)
    for (const n of [p.death, p.incomeProtection, p.disability, p.severeIllness, p.funeral])
      if (n.status === 'covered' && n.need > 0) praise(`${n.title.replace(' — lump sum', '')} for ${first(p)} is fully provided for.`, n.need > 100_000 ? 1 : 0.4);
  if (cf.surplus > 0.5) praise(`After every commitment you keep ${money(cf.surplus)} a month.`, 0.9);
  if (a.ratios.savingsRate >= 0.15) praise(`You save ${pct(a.ratios.savingsRate)} of your income — above the 15% guide.`, 0.85);
  if (a.ratios.debtToIncome > 0 && a.ratios.debtToIncome <= 0.36) praise(`Debt repayments take ${pct(a.ratios.debtToIncome)} of income, well within the 36% guide.`, 0.8);
  if (a.ratios.housingToIncome > 0 && a.ratios.housingToIncome <= 0.3) praise(`Housing costs take ${pct(a.ratios.housingToIncome)} of income, within the 30% guide.`, 0.6);
  for (const p of P) if (p.estate.cashRequired > 0 && p.estate.liquidityShortfall <= 0.5) praise(`${first(p)}’s estate holds enough cash to settle debts and costs.`, 0.7);

  return {
    attention: out.filter((m) => m.weight > 0).sort((x, y) => y.weight - x.weight),
    good: good.sort((x, y) => y.weight - x.weight),
  };
}

/** The opening verdict: how many needs are met, in words. */
export function verdict(statuses: Status[], withGoals = false): string {
  const c = { covered: 0, partial: 0, shortfall: 0 };
  for (const s of statuses) if (s !== 'na') c[s]++;
  const total = c.covered + c.partial + c.shortfall;
  if (!total) return 'We could not yet measure any of your needs, because the information needed has not been provided.';
  const parts: string[] = [];
  parts.push(c.covered === 0 ? 'none is yet fully provided for' : `${inWords(c.covered)} ${c.covered === 1 ? 'is' : 'are'} fully provided for`);
  if (c.partial) parts.push(`${inWords(c.partial)} ${c.partial === 1 ? 'is' : 'are'} partly provided for`);
  if (c.shortfall) parts.push(`${inWords(c.shortfall)} fall${c.shortfall === 1 ? 's' : ''} well short`);
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0];
  const noun = total === 1 ? (withGoals ? 'need or goal' : 'need') : withGoals ? 'needs and goals' : 'needs';
  return `Of the ${inWords(total)} ${noun} we measured, ${list}.`;
}
