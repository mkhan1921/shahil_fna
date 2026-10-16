/**
 * Distribution maths over the §3.3 types, ported from pipeline/advisor_intel/stats.py so the overlay
 * reads values exactly as the pipeline does: the same quantiles (mixture bisection included), the
 * same medians when a reviewer's value is materialised, and the same short phrase as
 * binding.describe_value ("60–65, most likely 62"). Rounding and number formatting follow Python's
 * round-half-even, so a phrase or a rounded amount matches the pipeline character for character.
 */
import type { Dist, Hypothesis, Quantity, ValueSummary } from './types';

/* ------------------------------------------------------------------ */
/* Python-compatible rounding and formatting                           */
/* ------------------------------------------------------------------ */

/** x rounded to `digits` decimals as text, half to even on the exact binary value (Python's format(x, '.nf')). */
export const pyFixed = (x: number, digits = 0): string => {
  if (!Number.isFinite(x)) return String(x);
  const neg = x < 0 || Object.is(x, -0);
  const exact = Math.abs(x).toFixed(100); // the exact decimal expansion of the double
  const dot = exact.indexOf('.');
  const tail = exact.slice(dot + 1 + digits);
  let out = Math.abs(x).toFixed(digits); // correct except on exact ties, where it rounds up
  if (tail[0] === '5' && /^5?0*$/.test(tail)) {
    const kept = exact.slice(0, dot + 1 + digits).replace(/\.$/, '');
    const last = Number(kept[kept.length - 1]);
    if (last % 2 === 0) out = kept; // a tie goes to the even neighbour, which is the truncation here
  }
  const zero = Number(out) === 0;
  return neg && !zero ? `-${out}` : out;
};

/** Python's round(x, digits) for floats. */
export const pyRound = (x: number, digits = 0): number => Number(pyFixed(x, digits));

const group = (int: string) => int.replace(/\B(?=(\d{3})+(?!\d))/g, ',');

/** Python's format(x, ',.nf'). */
export const pyGrouped = (x: number, digits = 0): string => {
  const s = pyFixed(x, digits);
  const neg = s.startsWith('-');
  const [int, dec] = (neg ? s.slice(1) : s).split('.');
  return `${neg ? '-' : ''}${group(int)}${dec !== undefined ? `.${dec}` : ''}`;
};

/** Python's format(x, 'g'): six significant digits, trailing zeros dropped, exponent outside 1e-4 … 1e6. */
export const pyG = (x: number): string => {
  if (x === 0) return Object.is(x, -0) ? '-0' : '0';
  if (!Number.isFinite(x)) return x > 0 ? 'inf' : x < 0 ? '-inf' : 'nan';
  const exp = Number(x.toExponential(5).split('e')[1]);
  if (exp < -4 || exp >= 6) {
    const [mant, e] = x.toExponential(5).split('e');
    const m = mant.includes('.') ? mant.replace(/0+$/, '').replace(/\.$/, '') : mant;
    const n = Number(e);
    return `${m}e${n < 0 ? '-' : '+'}${String(Math.abs(n)).padStart(2, '0')}`;
  }
  const s = pyFixed(x, Math.max(0, 5 - exp));
  return s.includes('.') ? s.replace(/0+$/, '').replace(/\.$/, '') : s;
};

/** Python's format(p, '.0%'). */
export const pyPercent = (p: number, digits = 0): string => `${pyFixed(p * 100, digits)}%`;

/* ------------------------------------------------------------------ */
/* Dates as decimal years                                              */
/* ------------------------------------------------------------------ */

const DAY = 86_400_000;
const yearStart = (y: number) => Date.UTC(y, 0, 1);
const daysIn = (y: number) => Math.round((yearStart(y + 1) - yearStart(y)) / DAY);

/** '1985-07-01' → 1985.496; a bare year '1985' → 1985.5. */
export const isoToYear = (s: string): number => {
  const t = s.trim();
  if (/^\d{4}$/.test(t)) return Number(t) + 0.5;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(t);
  if (!m) throw new RangeError(`not an ISO date: ${s}`);
  const y = Number(m[1]);
  const day = Date.UTC(y, Number(m[2]) - 1, Number(m[3]));
  const check = new Date(day);
  if (check.getUTCMonth() !== Number(m[2]) - 1 || check.getUTCDate() !== Number(m[3])) throw new RangeError(`not a calendar date: ${s}`);
  return y + Math.round((day - yearStart(y)) / DAY) / daysIn(y);
};

export const yearToIso = (y: number): string => {
  const year = Math.floor(y);
  const days = daysIn(year);
  const offset = Math.min(days - 1, Math.max(0, Math.trunc(pyRound((y - year) * days))));
  return new Date(yearStart(year) + offset * DAY).toISOString().slice(0, 10);
};

export const looksLikeDate = (s: string): boolean => {
  try {
    isoToYear(s);
    return true;
  } catch {
    return false;
  }
};

/** A point value as a number: booleans as 0/1, ISO dates as decimal years. */
export const asNumber = (v: number | string | boolean): number => {
  if (typeof v === 'boolean') return v ? 1 : 0;
  if (typeof v === 'number') return v;
  const n = Number(v);
  if (v.trim() !== '' && Number.isFinite(n)) return n;
  return isoToYear(v);
};

/* ------------------------------------------------------------------ */
/* Type tests                                                          */
/* ------------------------------------------------------------------ */

export const isCategorical = (d: Dist): boolean => {
  if (d.kind === 'categorical') return true;
  if (d.kind === 'point') return typeof d.value === 'string' && !looksLikeDate(d.value);
  if (d.kind === 'mixture') return d.components.every(([, c]) => isCategorical(c));
  return false;
};

export const isBoolean = (d: Dist): boolean => {
  if (d.kind === 'bernoulli') return true;
  if (d.kind === 'point') return typeof d.value === 'boolean';
  if (d.kind === 'mixture') return d.components.every(([, c]) => isBoolean(c));
  return false;
};

export const isNumeric = (d: Dist): boolean => !isCategorical(d) && !isBoolean(d);

/* ------------------------------------------------------------------ */
/* Normal distribution helpers                                         */
/* ------------------------------------------------------------------ */

const ERFC_COF = [
  -1.3026537197817094, 6.4196979235649026e-1, 1.9476473204185836e-2, -9.561514786808631e-3, -9.46595344482036e-4, 3.66839497852761e-4,
  4.2523324806907e-5, -2.0278578112534e-5, -1.624290004647e-6, 1.30365583558e-6, 1.5626441722e-8, -8.5238095915e-8, 6.529054439e-9,
  5.059343495e-9, -9.91364156e-10, -2.27365122e-10, 9.6467911e-11, 2.394038e-12, -6.886027e-12, 8.94487e-13, 3.13092e-13, -1.12708e-13,
  3.81e-16, 7.106e-15, -1.523e-15, -9.4e-17, 1.21e-16, -2.8e-17,
];

const erfcPositive = (z: number): number => {
  const t = 2 / (2 + z);
  const ty = 4 * t - 2;
  let d = 0;
  let dd = 0;
  for (let j = ERFC_COF.length - 1; j > 0; j--) {
    const tmp = d;
    d = ty * d - dd + ERFC_COF[j];
    dd = tmp;
  }
  return t * Math.exp(-z * z + 0.5 * (ERFC_COF[0] + ty * d) - dd);
};

/** The complementary error function, to near machine precision (Chebyshev fit, Numerical Recipes 3rd ed. §6.2). */
export const erfc = (x: number): number => (x >= 0 ? erfcPositive(x) : 2 - erfcPositive(-x));

const phi = (x: number) => 0.5 * erfc(-x / Math.SQRT2);

/** The inverse standard normal CDF (Acklam's approximation, refined by one Halley step). */
export const ndtri = (p: number): number => {
  if (p <= 0) return -Infinity;
  if (p >= 1) return Infinity;
  const a = [-3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2, 1.38357751867269e2, -3.066479806614716e1, 2.506628277459239];
  const b = [-5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2, 6.680131188771972e1, -1.328068155288572e1];
  const c = [-7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783];
  const d = [7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996, 3.754408661907416];
  const lo = 0.02425;
  let x: number;
  if (p < lo) {
    const q = Math.sqrt(-2 * Math.log(p));
    x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  } else if (p <= 1 - lo) {
    const q = p - 0.5;
    const r = q * q;
    x = ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
  } else {
    const q = Math.sqrt(-2 * Math.log(1 - p));
    x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  const e = 0.5 * erfc(-x / Math.SQRT2) - p;
  const u = e * Math.sqrt(2 * Math.PI) * Math.exp((x * x) / 2);
  return x - u / (1 + (x * u) / 2);
};

const Z90 = 1.2815515655446004;
const lognormalMu = (d: { median: number }) => Math.log(d.median);
/** A lognormal is symmetric in log space; the wider half is used so neither quantile is understated. */
const lognormalSigma = (d: { median: number; p10: number; p90: number }) => Math.max(Math.log(d.median / d.p10), Math.log(d.p90 / d.median)) / Z90;

/* ------------------------------------------------------------------ */
/* CDF, quantiles and moments (numeric)                                */
/* ------------------------------------------------------------------ */

export const cdf = (d: Dist, x: number): number => {
  switch (d.kind) {
    case 'point':
      return x >= asNumber(d.value) ? 1 : 0;
    case 'discrete':
      return d.support.reduce((s, v, i) => (v <= x ? s + d.weights[i] : s), 0);
    case 'triangular': {
      const { low: a, mode: c, high: b } = d;
      if (x <= a) return 0;
      if (x >= b) return 1;
      if (b === a) return 1;
      if (x <= c) return c > a ? (x - a) ** 2 / ((b - a) * (c - a)) : 0;
      return b > c ? 1 - (b - x) ** 2 / ((b - a) * (b - c)) : 1;
    }
    case 'lognormal': {
      if (x <= 0) return 0;
      const s = lognormalSigma(d);
      return s > 0 ? phi((Math.log(x) - lognormalMu(d)) / s) : x >= d.median ? 1 : 0;
    }
    case 'normal':
      return d.sd > 0 ? phi((x - d.mean) / d.sd) : x >= d.mean ? 1 : 0;
    case 'bernoulli':
      return x < 0 ? 0 : x < 1 ? 1 - d.p : 1;
    case 'mixture':
      return d.components.reduce((s, [w, c]) => s + w * cdf(c, x), 0);
    case 'categorical':
      throw new TypeError('no numeric CDF for a categorical distribution');
  }
};

export const quantile = (d: Dist, q: number): number => {
  const p = Math.min(1, Math.max(0, q));
  switch (d.kind) {
    case 'point':
      return asNumber(d.value);
    case 'discrete': {
      const order = d.support.map((_, i) => i).sort((i, j) => d.support[i] - d.support[j]);
      let acc = 0;
      for (const i of order) {
        acc += d.weights[i];
        if (acc >= p - 1e-12) return d.support[i];
      }
      return d.support[order[order.length - 1]];
    }
    case 'triangular': {
      const { low: a, mode: c, high: b } = d;
      if (b === a) return a;
      const fc = (c - a) / (b - a);
      return p <= fc ? a + Math.sqrt(p * (b - a) * (c - a)) : b - Math.sqrt((1 - p) * (b - a) * (b - c));
    }
    case 'lognormal':
      if (p <= 0) return 0;
      if (p >= 1) return Infinity;
      return Math.exp(lognormalMu(d) + lognormalSigma(d) * ndtri(p));
    case 'normal':
      if (p <= 0) return -Infinity;
      if (p >= 1) return Infinity;
      return d.mean + d.sd * ndtri(p);
    case 'bernoulli':
      return p > 1 - d.p ? 1 : 0;
    case 'mixture':
      return mixtureQuantile(d, p);
    case 'categorical':
      throw new TypeError('no numeric quantile for a categorical distribution');
  }
};

/** Bisection on the mixture CDF, step for step as stats._mixture_quantile so medians agree with the pipeline. */
const mixtureQuantile = (d: Extract<Dist, { kind: 'mixture' }>, q: number): number => {
  let lo = Math.min(...d.components.map(([, c]) => quantile(c, 1e-6)));
  let hi = Math.max(...d.components.map(([, c]) => quantile(c, 1 - 1e-6)));
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
    // Unbounded components cannot occur at the 1e-6 margins of the §3.3 types; keep a finite answer regardless.
    lo = Number.isFinite(lo) ? lo : -1e12;
    hi = Number.isFinite(hi) ? hi : 1e12;
  }
  if (hi <= lo) return lo;
  for (let i = 0; i < 80; i++) {
    const mid = (lo + hi) / 2;
    if (cdf(d, mid) >= q - 1e-12) hi = mid;
    else lo = mid;
  }
  return hi;
};

export const median = (d: Dist): number => quantile(d, 0.5);

export const mean = (d: Dist): number => {
  switch (d.kind) {
    case 'point':
      return asNumber(d.value);
    case 'discrete':
      return d.support.reduce((s, v, i) => s + v * d.weights[i], 0);
    case 'triangular':
      return (d.low + d.mode + d.high) / 3;
    case 'lognormal': {
      const s = lognormalSigma(d);
      return Math.exp(lognormalMu(d) + (s * s) / 2);
    }
    case 'normal':
      return d.mean;
    case 'bernoulli':
      return d.p;
    case 'mixture':
      return d.components.reduce((s, [w, c]) => s + w * mean(c), 0);
    case 'categorical':
      throw new TypeError('no numeric mean for a categorical distribution');
  }
};

export interface Quantiles {
  p10: number;
  p50: number;
  p90: number;
}

export const summary = (d: Dist): Quantiles => ({ p10: quantile(d, 0.1), p50: quantile(d, 0.5), p90: quantile(d, 0.9) });

/* ------------------------------------------------------------------ */
/* Categorical and boolean                                             */
/* ------------------------------------------------------------------ */

export const categoricalProbs = (d: Dist): Record<string, number> => {
  if (d.kind === 'categorical') return { ...d.probs };
  if (d.kind === 'point') return { [String(d.value)]: 1 };
  if (d.kind === 'mixture') {
    const out: Record<string, number> = {};
    for (const [w, c] of d.components) for (const [k, p] of Object.entries(categoricalProbs(c))) out[k] = (out[k] ?? 0) + w * p;
    return out;
  }
  throw new TypeError(`${d.kind} is not categorical`);
};

/** The most likely label; ties go to the first, as Python's max over a dict. */
export const modeLabel = (d: Dist): string => {
  let best = '';
  let bestP = -Infinity;
  for (const [k, p] of Object.entries(categoricalProbs(d))) {
    if (p > bestP) {
      best = k;
      bestP = p;
    }
  }
  return best;
};

export const pTrue = (d: Dist): number => {
  if (d.kind === 'bernoulli') return d.p;
  if (d.kind === 'point') return d.value ? 1 : 0;
  if (d.kind === 'mixture') return d.components.reduce((s, [w, c]) => s + w * pTrue(c), 0);
  throw new TypeError(`${d.kind} is not boolean`);
};

/* ------------------------------------------------------------------ */
/* Building distributions                                              */
/* ------------------------------------------------------------------ */

const sameDist = (a: Dist, b: Dist) => JSON.stringify(a) === JSON.stringify(b);

/** Weighted mixture with weights renormalised; one component is returned as itself (stats.mixture). */
export const mixture = (components: [number, Dist][]): Dist => {
  const comps = components.filter(([w]) => w > 0);
  if (!comps.length) throw new RangeError('a mixture needs a positive weight');
  const total = comps.reduce((s, [w]) => s + w, 0);
  const normed = comps.map(([w, d]) => [w / total, d] as [number, Dist]);
  if (normed.length === 1) return normed[0][1];
  const merged: [number, Dist][] = [];
  for (const [w, d] of normed) {
    const i = merged.findIndex(([, e]) => sameDist(e, d));
    if (i >= 0) merged[i] = [merged[i][0] + w, merged[i][1]];
    else merged.push([w, d]);
  }
  return merged.length === 1 ? merged[0][1] : { kind: 'mixture', components: merged };
};

/* ------------------------------------------------------------------ */
/* Validation (the Pydantic validators of models/dists.py)             */
/* ------------------------------------------------------------------ */

const TOL = 1e-6;

/** Problems Python would refuse when it validates this distribution; empty when it is sound. */
export const checkDist = (d: Dist, at = 'dist'): string[] => {
  const bad = (msg: string) => [`${at}: ${msg}`];
  const finite = (...xs: number[]) => xs.every((x) => typeof x === 'number' && Number.isFinite(x));
  switch (d.kind) {
    case 'point':
      return ['number', 'string', 'boolean'].includes(typeof d.value) && (typeof d.value !== 'number' || Number.isFinite(d.value)) ? [] : bad('a point needs a finite number, a string or a boolean');
    case 'categorical': {
      const ps = Object.values(d.probs);
      if (!ps.length) return bad('a categorical needs at least one category');
      if (ps.some((p) => !finite(p) || p < -TOL)) return bad('categorical probabilities must be non-negative');
      const s = ps.reduce((a, b) => a + b, 0);
      return Math.abs(s - 1) > 1e-4 ? bad(`categorical probabilities sum to ${s}, not 1`) : [];
    }
    case 'discrete': {
      if (!d.support.length || d.support.length !== d.weights.length) return bad('discrete support and weights must be non-empty and the same length');
      if (!finite(...d.support, ...d.weights)) return bad('discrete values must be finite numbers');
      const s = d.weights.reduce((a, b) => a + b, 0);
      return d.weights.some((w) => w < -TOL) || Math.abs(s - 1) > 1e-4 ? bad('discrete weights must be non-negative and sum to 1') : [];
    }
    case 'triangular':
      if (!finite(d.low, d.mode, d.high)) return bad('triangular bounds must be finite numbers');
      return d.low <= d.mode && d.mode <= d.high ? [] : bad(`triangular needs low <= mode <= high, got ${d.low}, ${d.mode}, ${d.high}`);
    case 'lognormal':
      return finite(d.p10, d.median, d.p90) && 0 < d.p10 && d.p10 <= d.median && d.median <= d.p90 ? [] : bad('lognormal needs 0 < p10 <= median <= p90');
    case 'normal':
      return finite(d.mean, d.sd) && d.sd >= 0 ? [] : bad('normal needs a finite mean and sd >= 0');
    case 'bernoulli':
      return finite(d.p) && d.p >= -TOL && d.p <= 1 + TOL ? [] : bad('p must be in [0, 1]');
    case 'mixture': {
      if (!d.components.length) return bad('a mixture needs at least one component');
      const s = d.components.reduce((a, [w]) => a + w, 0);
      const own = d.components.some(([w]) => !finite(w) || w < -TOL) || Math.abs(s - 1) > 1e-4 ? bad(`mixture weights must be non-negative and sum to 1 (got ${s})`) : [];
      return [...own, ...d.components.flatMap(([, c], i) => checkDist(c, `${at}.components[${i}]`))];
    }
  }
};

/* ------------------------------------------------------------------ */
/* Summaries and phrases (binding.summarise_value, describe_value)     */
/* ------------------------------------------------------------------ */

const MONEY_UNITS = new Set(['ZAR', 'USD', 'GBP', 'EUR']);
const SYMBOL: Record<string, string> = { ZAR: 'R', USD: '$', GBP: '£', EUR: '€' };
const finiteOrNull = (x: number) => (Number.isFinite(x) ? pyRound(x, 6) : null);

/** A JSON summary of a value, as the sidecar carries it. */
export const summarise = (q: Quantity | null): ValueSummary | null => {
  if (!q) return null;
  const d = q.dist;
  const out: ValueSummary = { unit: q.unit, period: q.period, basis: q.basis, kind: d.kind };
  if (d.kind === 'point') return { ...out, point: d.value };
  if (isBoolean(d)) return { ...out, p_true: pyRound(pTrue(d), 4) };
  if (isCategorical(d)) {
    const probs = Object.entries(categoricalProbs(d)).sort((a, b) => b[1] - a[1]);
    return { ...out, probs: Object.fromEntries(probs.map(([k, p]) => [k, pyRound(p, 4)])) };
  }
  const s = summary(d);
  return { ...out, p10: finiteOrNull(s.p10), p50: finiteOrNull(s.p50), p90: finiteOrNull(s.p90) };
};

/** A number in the value's unit: "R42,000", "11.5%", "2026-06", "62". */
export const formatIn = (q: Pick<Quantity, 'unit'>, x: number, money = MONEY_UNITS.has(q.unit)): string => {
  if (q.unit === 'date') return Math.abs(x - pyRound(x)) > 1e-9 ? yearToIso(x).slice(0, 7) : String(Math.trunc(pyRound(x)));
  if (q.unit === 'pct') return `${pyFixed(x * 100, 1)}%`;
  if (money) return `${SYMBOL[q.unit] ?? ''}${pyGrouped(x, 0)}`;
  return Math.abs(x) < 1000 ? pyG(x) : pyGrouped(x, 0);
};

const PER: Record<string, string> = { monthly: ' a month', annual: ' a year' };

/** A short phrase for the review popover: "60–65, most likely 62" (binding.describe_value). */
export const describe = (q: Quantity | null): string => {
  if (!q) return 'not known';
  const per = PER[q.period] ?? '';
  const d = q.dist;
  if (d.kind === 'point') {
    if (typeof d.value === 'boolean') return d.value ? 'yes' : 'no';
    if (typeof d.value === 'string') return d.value;
    return formatIn(q, d.value) + per;
  }
  if (isBoolean(d)) return `${pyPercent(pTrue(d))} likely`;
  if (isCategorical(d)) {
    const probs = Object.entries(categoricalProbs(d)).sort((a, b) => b[1] - a[1]);
    return probs
      .slice(0, 3)
      .map(([k, p]) => `${k} (${pyPercent(p)})`)
      .join(', ');
  }
  const s = summary(d);
  if (Math.abs(s.p90 - s.p10) <= 1e-9) return formatIn(q, s.p50) + per;
  return `${formatIn(q, s.p10)}–${formatIn(q, s.p90)}, most likely ${formatIn(q, s.p50)}${per}`;
};

/* ------------------------------------------------------------------ */
/* The phrase the adviser reads                                        */
/* ------------------------------------------------------------------ */

const WHOLE_UNITS = new Set(['age', 'years', 'months', 'count']);

/** A number as the adviser reads it: whole years and counts, rand, percent, dates. */
const readable = (q: Pick<Quantity, 'unit'>, x: number): string => (WHOLE_UNITS.has(q.unit) ? formatIn(q, pyRound(x)) : formatIn(q, x));

/**
 * The value as a short phrase for the overlay (§11.1): a range reads as it was stated —
 * "60–65, most likely 62" — weighted options list their weights, and anything else reads
 * as describe() does, with whole years and counts.
 */
export const phrase = (q: Quantity | null): string => {
  if (!q) return 'not known';
  const d = q.dist;
  if (d.kind === 'point' || isBoolean(d) || isCategorical(d)) return describe(q);
  const per = PER[q.period] ?? '';
  if (d.kind === 'triangular') {
    if (d.high - d.low <= 1e-9) return readable(q, d.mode) + per;
    return `${readable(q, d.low)}–${readable(q, d.high)}, most likely ${readable(q, d.mode)}${per}`;
  }
  const points = d.kind === 'discrete' ? d.support.map((x, i) => [x, d.weights[i]] as const) : d.kind === 'mixture' && d.components.every(([, c]) => c.kind === 'point') ? d.components.map(([w, c]) => [asNumber((c as { value: number }).value), w] as const) : null;
  if (points && points.length <= 4) {
    return [...points]
      .sort((a, b) => b[1] - a[1])
      .map(([x, w]) => `${readable(q, x)} (${pyPercent(w)})`)
      .join(', ') + per;
  }
  const s = summary(d);
  const [lo, mid, hi] = [readable(q, s.p10), readable(q, s.p50), readable(q, s.p90)];
  if (lo === hi) return mid + per;
  return `${lo}–${hi}, most likely ${mid}${per}`;
};

/** The most representative number of a numeric value: a range's most likely value, a point, else the median. */
const centre = (q: Quantity): number => (q.dist.kind === 'triangular' ? q.dist.mode : q.dist.kind === 'point' ? asNumber(q.dist.value) : median(q.dist));

/**
 * A fact's value as the adviser reads it: several numeric hypotheses read as alternatives —
 * "R280,000 (60%) or R350,000 (40%)" — so a bimodal or contradicted value is never summarised by a
 * median nobody said.
 */
export const phraseHypotheses = (hyps: Hypothesis[], merged: Quantity | null): string => {
  const valued = hyps.filter((h): h is Hypothesis & { value: Quantity } => !!h.value && h.weight > 0);
  if (valued.length > 1 && merged && isNumeric(merged.dist)) {
    const per = PER[merged.period] ?? '';
    return (
      [...valued]
        .sort((a, b) => b.weight - a.weight)
        .slice(0, 3)
        .map((h) => `${readable(h.value, centre(h.value))} (${pyPercent(h.weight)})`)
        .join(' or ') + per
    );
  }
  return phrase(merged);
};

const BASIS: Record<string, string> = { gross: 'gross', net: 'after tax', unknown: 'gross or after tax' };

/** The normalised value exactly: the distribution and its parameters, in the value's unit. */
export const explain = (q: Quantity): string => {
  const f = (x: number) => formatIn(q, x);
  const d = q.dist;
  const per = PER[q.period] ?? '';
  const basis = BASIS[q.basis] ? `, ${BASIS[q.basis]}` : '';
  const body = (() => {
    switch (d.kind) {
      case 'point':
        return typeof d.value === 'number' ? `exactly ${f(d.value)}` : typeof d.value === 'boolean' ? (d.value ? 'yes' : 'no') : `“${d.value}”`;
      case 'triangular':
        return `a range: lowest ${f(d.low)}, most likely ${f(d.mode)}, highest ${f(d.high)}`;
      case 'discrete':
        return `options: ${d.support.map((x, i) => `${f(x)} at ${pyPercent(d.weights[i])}`).join(', ')}`;
      case 'categorical':
        return `options: ${Object.entries(d.probs)
          .sort((a, b) => b[1] - a[1])
          .map(([k, p]) => `${k} at ${pyPercent(p)}`)
          .join(', ')}`;
      case 'bernoulli':
        return `a ${pyPercent(d.p)} chance`;
      case 'lognormal':
        return `skewed: 80% between ${f(d.p10)} and ${f(d.p90)}, median ${f(d.median)}`;
      case 'normal':
        return `around ${f(d.mean)}, give or take ${q.unit === 'pct' ? `${pyFixed(d.sd * 100, 1)} points` : f(d.sd)}`;
      case 'mixture':
        return `${d.components.length} scenarios: ${d.components.map(([w, c]) => `${explain({ ...q, dist: c, period: 'none', basis: 'na' })} (${pyPercent(w)})`).join('; ')}`;
    }
  })();
  return `${body}${per}${basis}`;
};

/* ------------------------------------------------------------------ */
/* Density, for the popover's sparkline                                */
/* ------------------------------------------------------------------ */

export interface Spike {
  x: number;
  weight: number;
}
export interface Shape {
  /** Continuous part, sampled on an even grid over [lo, hi]; ys integrate to the continuous weight. */
  xs: number[];
  ys: number[];
  /** Point masses (points, discrete support). */
  spikes: Spike[];
  lo: number;
  hi: number;
}

const pdfContinuous = (d: Dist, x: number): number => {
  switch (d.kind) {
    case 'triangular': {
      const { low: a, mode: c, high: b } = d;
      if (x < a || x > b || b === a) return 0;
      if (x < c) return (2 * (x - a)) / ((b - a) * (c - a));
      if (x === c) return 2 / (b - a);
      return (2 * (b - x)) / ((b - a) * (b - c));
    }
    case 'normal':
      return d.sd > 0 ? Math.exp(-0.5 * ((x - d.mean) / d.sd) ** 2) / (d.sd * Math.sqrt(2 * Math.PI)) : 0;
    case 'lognormal': {
      if (x <= 0) return 0;
      const s = lognormalSigma(d);
      return s > 0 ? Math.exp(-0.5 * ((Math.log(x) - lognormalMu(d)) / s) ** 2) / (x * s * Math.sqrt(2 * Math.PI)) : 0;
    }
    case 'mixture':
      return d.components.reduce((acc, [w, c]) => acc + w * pdfContinuous(c, x), 0);
    default:
      return 0;
  }
};

const collectSpikes = (d: Dist, w: number, out: Spike[]) => {
  if (d.kind === 'point') out.push({ x: asNumber(d.value), weight: w });
  else if (d.kind === 'discrete') d.support.forEach((s, i) => out.push({ x: s, weight: w * d.weights[i] }));
  else if (d.kind === 'mixture') d.components.forEach(([cw, c]) => collectSpikes(c, w * cw, out));
};

/** The shape of a numeric distribution over its central 98% (or its whole support when bounded). */
export const shape = (d: Dist, points = 64): Shape => {
  const spikes: Spike[] = [];
  collectSpikes(d, 1, spikes);
  let lo = quantile(d, 0.01);
  let hi = quantile(d, 0.99);
  for (const s of spikes) {
    lo = Math.min(lo, s.x);
    hi = Math.max(hi, s.x);
  }
  if (hi - lo < 1e-9) {
    const pad = Math.max(1, Math.abs(lo) * 0.1);
    lo -= pad;
    hi += pad;
  }
  const xs: number[] = [];
  const ys: number[] = [];
  for (let i = 0; i < points; i++) {
    const x = lo + ((hi - lo) * i) / (points - 1);
    xs.push(x);
    ys.push(pdfContinuous(d, x));
  }
  const merged = new Map<number, number>();
  for (const s of spikes) merged.set(s.x, (merged.get(s.x) ?? 0) + s.weight);
  return { xs, ys, spikes: [...merged].map(([x, weight]) => ({ x, weight })), lo, hi };
};
