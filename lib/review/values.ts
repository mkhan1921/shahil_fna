/**
 * The typed editor's values and the distributions they become (spec §11.2 "Edit value"):
 *
 *   point                     → Point
 *   range (low, likely, high) → Triangular(low, mode = likely, high)
 *   options with weights      → Discrete for numbers (a mixture of points where a slot does not
 *                               allow discrete), Categorical for labels, Bernoulli for yes / no
 *
 * Values are entered in the units people use — rand, percent, ages, ISO dates — and converted to
 * the slot's stored unit here (rates as decimals, dates as decimal years).
 */
import type { SlotDef } from './catalog';
import { categoricalProbs, isBoolean, isCategorical, isoToYear, looksLikeDate, pTrue, pyRound, summary, yearToIso } from './dist';
import type { Dist, PointDist, Quantity, ValueSummary } from './types';

export type Scalar = number | string | boolean;

export type EditorValue =
  | { mode: 'point'; value: Scalar }
  | { mode: 'range'; low: number | string; likely: number | string; high: number | string }
  | { mode: 'options'; options: { value: Scalar; weight: number }[] };

export type EditorMode = EditorValue['mode'];

/** What kind of input a slot takes. */
export type InputKind = 'amount' | 'number' | 'percent' | 'date' | 'year' | 'category' | 'boolean' | 'text';

export const inputKind = (slot: SlotDef): InputKind => {
  switch (slot.value_type) {
    case 'amount':
      return 'amount';
    case 'rate':
      return 'percent';
    case 'date':
      return 'date';
    case 'year':
      return 'year';
    case 'category':
    case 'count':
      return 'category';
    case 'boolean':
    case 'probability':
      return 'boolean';
    case 'text':
      return 'text';
    default:
      return 'number';
  }
};

/** The editor modes a slot offers: text is a point; labels and yes / no have no range. */
export const editorModes = (slot: SlotDef): EditorMode[] => {
  const kind = inputKind(slot);
  if (kind === 'text') return ['point'];
  if (kind === 'category' || kind === 'boolean') return ['point', 'options'];
  return ['point', 'range', 'options'];
};

/** The slot's options for labels: its categories, or yes / no. */
export const optionValues = (slot: SlotDef): Scalar[] => (inputKind(slot) === 'boolean' ? [true, false] : (slot.categories ?? []));

/* ------------------------------------------------------------------ */
/* Units as typed ↔ units as stored                                     */
/* ------------------------------------------------------------------ */

/** A typed number in the slot's stored unit (percent → decimal, ISO date → decimal year). */
export const fromInput = (slot: SlotDef, v: number | string): number => {
  const kind = inputKind(slot);
  if (kind === 'date') return typeof v === 'number' ? v : isoToYear(v);
  const n = typeof v === 'number' ? v : Number(String(v).replace(/[\s,R%]/g, ''));
  return kind === 'percent' ? n / 100 : n;
};

/** A stored number as the editor shows it. */
export const toInput = (slot: SlotDef, x: number): number | string => {
  const kind = inputKind(slot);
  if (kind === 'date') return yearToIso(x);
  if (kind === 'percent') return pyRound(x * 100, 4);
  return pyRound(x, 2);
};

/* ------------------------------------------------------------------ */
/* Editor value → distribution                                         */
/* ------------------------------------------------------------------ */

export class EditorError extends Error {}

const normalised = (ws: number[]): number[] => {
  const total = ws.reduce((a, b) => a + b, 0);
  if (!(total > 0) || ws.some((w) => !(w >= 0))) throw new EditorError('Weights must be zero or more and add up to more than zero.');
  return ws.map((w) => w / total);
};

/** Problems with an editor value for this slot (empty when it can be saved). */
export const editorProblems = (slot: SlotDef, v: EditorValue): string[] => {
  try {
    toDist(slot, v);
  } catch (e) {
    return [e instanceof Error ? e.message : String(e)];
  }
  const out: string[] = [];
  const b = slot.bounds;
  if (b && inputKind(slot) !== 'category' && inputKind(slot) !== 'boolean' && inputKind(slot) !== 'text') {
    const nums = v.mode === 'point' ? [v.value] : v.mode === 'range' ? [v.low, v.high] : v.options.map((o) => o.value);
    const xs = nums.map((n) => fromInput(slot, n as number | string));
    if (xs.some((x) => x < b[0] || x > b[1])) out.push(`Outside the usual range for this field (${toInput(slot, b[0])} to ${toInput(slot, b[1])}). Check it before saving.`);
  }
  return out;
};

/** Whether the problems only warn (the value can still be saved). */
export const onlyWarnings = (problems: string[]) => problems.every((p) => p.startsWith('Outside the usual range'));

export const toDist = (slot: SlotDef, v: EditorValue): Dist => {
  const kind = inputKind(slot);
  if (v.mode === 'point') {
    if (kind === 'text') {
      const s = String(v.value).trim();
      if (!s) throw new EditorError('Enter the text.');
      return { kind: 'point', value: s };
    }
    if (kind === 'boolean') {
      if (typeof v.value !== 'boolean') throw new EditorError('Choose yes or no.');
      return { kind: 'point', value: v.value };
    }
    if (kind === 'category') {
      const s = String(v.value);
      if (!(slot.categories ?? []).includes(s)) throw new EditorError('Choose one of the options.');
      return { kind: 'point', value: s };
    }
    if (kind === 'date') {
      const s = String(v.value);
      if (!looksLikeDate(s)) throw new EditorError('Enter a date as YYYY-MM-DD.');
      return { kind: 'point', value: s.slice(0, 10) };
    }
    const x = fromInput(slot, v.value as number | string);
    if (!Number.isFinite(x)) throw new EditorError('Enter a number.');
    return { kind: 'point', value: x };
  }
  if (v.mode === 'range') {
    if (kind === 'category' || kind === 'boolean' || kind === 'text') throw new EditorError('A range needs a number.');
    const [low, mode, high] = [v.low, v.likely, v.high].map((x) => fromInput(slot, x));
    if (![low, mode, high].every(Number.isFinite)) throw new EditorError('Enter the lowest, most likely and highest values.');
    if (!(low <= mode && mode <= high)) throw new EditorError('The most likely value must lie between the lowest and the highest.');
    if (low === high) return { kind: 'point', value: low };
    return { kind: 'triangular', low, mode, high };
  }
  const opts = v.options.filter((o) => o.value !== '' && o.weight > 0);
  if (!opts.length) throw new EditorError('Give at least one option a weight.');
  const weights = normalised(opts.map((o) => o.weight));
  if (kind === 'boolean') {
    const yes = opts.reduce((s, o, i) => (o.value === true ? s + weights[i] : s), 0);
    return { kind: 'bernoulli', p: pyRound(yes, 6) };
  }
  if (kind === 'category') {
    const probs: Record<string, number> = {};
    opts.forEach((o, i) => {
      const k = String(o.value);
      if (!(slot.categories ?? []).includes(k)) throw new EditorError(`"${k}" is not one of the options.`);
      probs[k] = (probs[k] ?? 0) + weights[i];
    });
    const keys = Object.keys(probs);
    return keys.length === 1 ? { kind: 'point', value: keys[0] } : { kind: 'categorical', probs };
  }
  if (kind === 'text') throw new EditorError('Text has a single value.');
  const merged = new Map<number, number>();
  opts.forEach((o, i) => {
    const x = fromInput(slot, o.value as number | string);
    if (!Number.isFinite(x)) throw new EditorError('Every option needs a number.');
    merged.set(x, (merged.get(x) ?? 0) + weights[i]);
  });
  const support = [...merged.keys()];
  const ws = [...merged.values()];
  if (support.length === 1) return { kind: 'point', value: support[0] };
  if (slot.allowed_dists.includes('discrete')) return { kind: 'discrete', support, weights: ws };
  return { kind: 'mixture', components: support.map((x, i) => [ws[i], { kind: 'point', value: x }] as [number, Dist]) };
};

/* ------------------------------------------------------------------ */
/* Distribution → editor value (to start an edit from what is there)    */
/* ------------------------------------------------------------------ */

const isPointMixture = (d: Dist): d is Extract<Dist, { kind: 'mixture' }> => d.kind === 'mixture' && d.components.every(([, c]) => c.kind === 'point');

export const toEditor = (slot: SlotDef, q: Quantity | null): EditorValue => {
  const kind = inputKind(slot);
  if (!q) {
    if (kind === 'boolean') return { mode: 'point', value: true };
    if (kind === 'category') return { mode: 'point', value: slot.categories?.[0] ?? '' };
    return { mode: 'point', value: '' };
  }
  const d = q.dist;
  if (d.kind === 'point') {
    if (kind === 'date' && typeof d.value === 'number') return { mode: 'point', value: yearToIso(d.value) };
    if (typeof d.value === 'number' && kind !== 'category') return { mode: 'point', value: toInput(slot, d.value) };
    return { mode: 'point', value: d.value };
  }
  if (kind === 'boolean' && isBoolean(d)) {
    const p = pyRound(pTrue(d) * 100, 1);
    return { mode: 'options', options: [{ value: true, weight: p }, { value: false, weight: pyRound(100 - p, 1) }] };
  }
  if (isCategorical(d)) {
    return { mode: 'options', options: Object.entries(categoricalProbs(d)).map(([value, p]) => ({ value, weight: pyRound(p * 100, 1) })) };
  }
  if (d.kind === 'triangular') return { mode: 'range', low: toInput(slot, d.low), likely: toInput(slot, d.mode), high: toInput(slot, d.high) };
  if (d.kind === 'discrete') return { mode: 'options', options: d.support.map((x, i) => ({ value: toInput(slot, x), weight: pyRound(d.weights[i] * 100, 1) })) };
  if (isPointMixture(d)) return { mode: 'options', options: d.components.map(([w, c]) => ({ value: toInput(slot, Number((c as PointDist).value)), weight: pyRound(w * 100, 1) })) };
  const s = summary(d);
  return { mode: 'range', low: toInput(slot, s.p10), likely: toInput(slot, s.p50), high: toInput(slot, s.p90) };
};

/* ------------------------------------------------------------------ */
/* Change against the prior value (reducer.compute_change)              */
/* ------------------------------------------------------------------ */

const CONFIRM_REL_TOL = 0.02;

const summaryNumber = (s: ValueSummary): number | null => {
  if (typeof s.point === 'number') return s.point;
  if (typeof s.point === 'string' && looksLikeDate(s.point)) return isoToYear(s.point);
  return typeof s.p50 === 'number' ? s.p50 : null;
};

const summaryLabel = (s: ValueSummary): string | null => {
  if (typeof s.point === 'string' && !looksLikeDate(s.point)) return s.point;
  if (s.probs) return Object.entries(s.probs).reduce((a, b) => (b[1] > a[1] ? b : a))[0];
  return null;
};

const summaryBool = (s: ValueSummary): boolean | null => {
  if (typeof s.point === 'boolean') return s.point;
  return typeof s.p_true === 'number' ? s.p_true >= 0.5 : null;
};

/** reducer._same_value against a summary of the prior: equal within 2% (numeric), same label or same yes / no. */
export const sameValue = (a: ValueSummary, prior: ValueSummary): boolean => {
  if (a.unit !== prior.unit) return false;
  const la = summaryLabel(a);
  const lb = summaryLabel(prior);
  if (la !== null || lb !== null) return la !== null && la === lb; // labels and text compare exactly, as the pipeline's mode labels do
  const ba = summaryBool(a);
  const bb = summaryBool(prior);
  if (ba !== null || bb !== null) return ba === bb;
  const xa = summaryNumber(a);
  const xb = summaryNumber(prior);
  if (xa === null || xb === null) return JSON.stringify(a) === JSON.stringify(prior);
  if (a.unit === 'date') return Math.abs(xa - xb) < 1 / 365 + 1e-9;
  return Math.abs(xa - xb) <= CONFIRM_REL_TOL * Math.max(Math.abs(xa), Math.abs(xb)) + 1e-9;
};
