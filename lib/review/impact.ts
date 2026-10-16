/**
 * The impact strip (spec §11.1, §12.7, §12.8): the app's own analyse() on the document before and
 * after a change, each need's shortfall and status side by side, the Monte Carlo success
 * probability from the last forecast run with its variance attribution, and the FNA's
 * deterministic retirement result labelled next to it.
 */
import type { Analysis, NeedResult, Status } from '../fna/analysis';
import type { PersonKey } from '../fna/types';
import { pyPercent } from './dist';
import type { ForecastResult } from './types';

export type NeedArea = 'life' | 'disability' | 'income-protection' | 'severe-illness' | 'funeral' | 'estate' | 'retirement' | 'education' | 'emergency';

export interface NeedLine {
  /** "{area}.{person}" or "{area}.household", as the forecast's need_bands are keyed. */
  key: string;
  area: NeedArea;
  person: PersonKey | null;
  title: string;
  need: number;
  provision: number;
  shortfall: number;
  status: Status;
  monthly: boolean;
}

const fromNeed = (area: NeedArea, person: PersonKey | null, n: NeedResult, title = n.title): NeedLine => ({
  key: `${area}.${person ?? 'household'}`,
  area,
  person,
  title,
  need: n.need,
  provision: n.provision,
  shortfall: n.shortfall,
  status: n.status,
  monthly: !!n.monthly,
});

/** The headline needs of an analysis (§1.1 goal 6): per person, then the household's. */
export const needLines = (a: Analysis): NeedLine[] => {
  const out: NeedLine[] = [];
  for (const k of a.people) {
    const p = a.persons[k];
    if (!p) continue;
    out.push(fromNeed('life', k, p.death, 'Life cover'));
    out.push(fromNeed('disability', k, p.disability, 'Disability'));
    out.push(fromNeed('income-protection', k, p.incomeProtection, 'Income protection'));
    out.push(fromNeed('severe-illness', k, p.severeIllness, 'Severe illness'));
    out.push(fromNeed('funeral', k, p.funeral, 'Funeral'));
    const e = p.estate;
    const estateStatus: Status = e.cashRequired <= 0 ? 'na' : e.liquidityShortfall <= 0 ? 'covered' : e.cashAvailable / e.cashRequired >= 0.6 ? 'partial' : 'shortfall';
    out.push({ key: `estate.${k}`, area: 'estate', person: k, title: 'Estate liquidity', need: e.cashRequired, provision: Math.min(e.cashAvailable, e.cashRequired), shortfall: e.liquidityShortfall, status: estateStatus, monthly: false });
    const r = p.retirement;
    out.push({ key: `retirement.${k}`, area: 'retirement', person: k, title: 'Retirement', need: r.requiredCapital, provision: r.projectedCapital, shortfall: r.shortfall, status: r.status, monthly: false });
  }
  out.push(fromNeed('education', null, a.educationTotal, 'Education'));
  out.push(fromNeed('emergency', null, a.emergency, 'Emergency fund'));
  return out;
};

export interface NeedDelta {
  key: string;
  before: NeedLine;
  after: NeedLine;
  /** after − before; positive means a bigger shortfall. */
  shortfallChange: number;
  statusChanged: boolean;
}

/** Each need before and after, with what changed; `minChange` hides rand-level noise. */
export const compareNeeds = (before: Analysis, after: Analysis, minChange = 1): NeedDelta[] => {
  const prior = new Map(needLines(before).map((n) => [n.key, n]));
  return needLines(after).flatMap((n) => {
    const b = prior.get(n.key);
    if (!b) return [];
    const change = n.shortfall - b.shortfall;
    return [{ key: n.key, before: b, after: n, shortfallChange: Math.abs(change) < minChange ? 0 : change, statusChanged: b.status !== n.status }];
  });
};

/** The needs a change actually moved: a status flip or a shortfall change. */
export const movedNeeds = (deltas: NeedDelta[]): NeedDelta[] => deltas.filter((d) => d.statusChanged || d.shortfallChange !== 0);

export interface RetirementView {
  person: PersonKey;
  name: string;
  status: Status;
  projectedCapital: number;
  requiredCapital: number;
  depletionAge: number | null;
  replacementRatio: number;
  targetReplacementRatio: number;
}

/** The FNA's deterministic retirement projection per person (computeRetirement). */
export const retirementViews = (a: Analysis): RetirementView[] =>
  a.people.flatMap((k) => {
    const p = a.persons[k];
    if (!p) return [];
    const r = p.retirement;
    return [
      {
        person: k,
        name: p.name,
        status: r.status,
        projectedCapital: r.projectedCapital,
        requiredCapital: r.requiredCapital,
        depletionAge: r.depletionAge,
        replacementRatio: r.replacementRatio,
        targetReplacementRatio: r.targetReplacementRatio,
      },
    ];
  });

/** "Of the uncertainty in this plan, about X% is markets, Y% is what the client hasn't decided, Z% is facts we may have misheard." (§12.5) */
export const attributionSentence = (f: ForecastResult | null, given?: string | null): string | null => {
  if (given) return given;
  const va = f?.variance_attribution;
  if (!va || va.market === undefined || va.client_content === undefined || va.extraction === undefined) return null;
  return `Of the uncertainty in this plan, about ${pyPercent(va.market)} is markets, ${pyPercent(va.client_content)} is what the client hasn't decided, ${pyPercent(va.extraction)} is facts we may have misheard.`;
};

/** "71% (70–72%)": the probability of success with its Monte Carlo interval. */
export const posText = (f: ForecastResult): string => `${pyPercent(f.pos)} (${pyPercent(f.pos_ci[0])}–${pyPercent(f.pos_ci[1])})`;

/** The forecast's shortfall band for a need, when the pipeline ran the draws (§12.7 use 2). */
export const needBand = (f: ForecastResult | null, key: string): { p10: number; p50: number; p90: number } | null => {
  const b = f?.need_bands?.[key];
  return b && typeof b.p10 === 'number' && typeof b.p50 === 'number' && typeof b.p90 === 'number' ? { p10: b.p10, p50: b.p50, p90: b.p90 } : null;
};

/** The deterministic figure the forecast run recorded for a person, for the side-by-side check (§12.8). */
export const forecastDeterministic = (f: ForecastResult | null, person: PersonKey, key: string): number | null => {
  const v = f?.deterministic?.[`${person}.${key}`];
  return typeof v === 'number' ? v : null;
};
