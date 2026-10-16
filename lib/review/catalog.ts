/**
 * The profile slot catalog, as the overlay needs it: labels, units, categories, tiers and how each
 * slot's value is written into the FnaDocument. schema/slots.json is exported from
 * pipeline/advisor_intel/schema/profile_v1.yaml (scripts/export_review_schemas.py).
 *
 * toFnaValue and toQuantity port binding.to_fna_value (median mode) and binding.to_quantity, so a
 * value the reviewer types lands in the sheet exactly as the pipeline's materialise() writes it.
 */
import { EXPENSE_ITEMS } from '../fna/catalog';
import { RISK_QUESTIONS } from '../fna/riskProfile';
import { isBoolean, isCategorical, median, modeLabel, pTrue, pyRound, yearToIso } from './dist';
import { DICT_CONTAINERS, isListContainer, parsePath } from './paths';
import slotsJson from './schema/slots.json';
import type { Basis, Dist, Entity, Period, Quantity, Tier, Unit } from './types';
import { rowIdForNewEntity } from './uuid';

export type ValueType = 'amount' | 'age' | 'years' | 'months' | 'date' | 'year' | 'rate' | 'count' | 'category' | 'boolean' | 'text' | 'probability';
export type FnaUnit = 'rand' | 'rand_monthly' | 'rand_annual' | 'years' | 'months' | 'age' | 'year' | 'decimal' | 'date' | 'count' | 'category' | 'boolean' | 'text' | 'probability';

export interface SlotDef {
  slot_id: string;
  fna_path: string;
  group: string;
  cardinality: 'one' | 'per_person' | 'per_entity' | 'list';
  entity_type: string | null;
  policy: string;
  value_type: ValueType;
  fna_unit: FnaUnit;
  allowed_dists: string[];
  categories: string[] | null;
  bounds: [number, number] | null;
  /** The extraction question: also the annotator's guide to the slot (§15.2). */
  question: string;
  sensitivity_tier: Tier;
  /** False for forecast-only slots (events, income changes). */
  exported: boolean;
  /** Risk answers store the option index; counts store an integer. */
  store_as: 'value' | 'index' | 'int';
  label: string;
}

export interface SlotGroupDef {
  name: string;
  description: string;
  list_entity: string | null;
  family: string;
}

interface SlotCatalog {
  version: string;
  groups: Record<string, SlotGroupDef>;
  slots: SlotDef[];
}

export const CATALOG = slotsJson as unknown as SlotCatalog;
const INDEX = new Map(CATALOG.slots.map((s) => [s.slot_id, s]));

/** 'retirement.client.retirementAge' → 'retirement.{p}.retirementAge'; strips '#instance'. */
export const genericSlotId = (concrete: string): string => concrete.split('#', 1)[0].replace(/\b(client|spouse)\b/, '{p}');

export const personOf = (concrete: string): 'client' | 'spouse' | null => {
  const m = /\b(client|spouse)\b/.exec(concrete.split('#', 1)[0]);
  return m ? (m[1] as 'client' | 'spouse') : null;
};

/** The slot by its generic id, or by a concrete one ('retirement.client.retirementAge'). */
export const slotById = (slotId: string): SlotDef | undefined => INDEX.get(slotId) ?? INDEX.get(genericSlotId(slotId));

export const slotOrThrow = (slotId: string): SlotDef => {
  const s = slotById(slotId);
  if (!s) throw new RangeError(`unknown slot ${slotId}`);
  return s;
};

export const factIdFor = (slotId: string, instance: string | null): string => (instance ? `${slotId}#${instance}` : slotId);

export const splitFactId = (factId: string): { slotId: string; instance: string | null } => {
  const i = factId.indexOf('#');
  return i < 0 ? { slotId: factId, instance: null } : { slotId: factId.slice(0, i), instance: factId.slice(i + 1) };
};

export const isPerPerson = (slot: SlotDef) => slot.slot_id.includes('{p}');

/** The FnaDocument field at the end of the slot's path ('tertiaryStartAge'). */
export const fieldOf = (slot: SlotDef) => slot.fna_path.slice(slot.fna_path.lastIndexOf('.') + 1);

/** The FnaDocument list a per-entity slot lives in ('dependants'), or null. */
export const listOf = (slot: SlotDef): string | null => {
  const m = /^(.*?)\[\]/.exec(slot.fna_path);
  return m ? m[1] : null;
};

/* ------------------------------------------------------------------ */
/* Units and values                                                    */
/* ------------------------------------------------------------------ */

const UNIT: Partial<Record<FnaUnit, [Unit, Period]>> = {
  rand: ['ZAR', 'once'],
  rand_monthly: ['ZAR', 'monthly'],
  rand_annual: ['ZAR', 'annual'],
  years: ['years', 'none'],
  months: ['months', 'none'],
  age: ['age', 'none'],
  year: ['date', 'none'],
  decimal: ['pct', 'none'],
  date: ['date', 'none'],
  count: ['count', 'none'],
};

/** (Quantity.unit, Quantity.period) for a slot's FnaDocument unit (binding.quantity_unit). */
export const quantityUnit = (slot: SlotDef): [Unit, Period] => UNIT[slot.fna_unit] ?? ['none', 'none'];

export const quantityFor = (slot: SlotDef, dist: Dist, basis: Basis = 'na'): Quantity => {
  const [unit, period] = quantityUnit(slot);
  return { dist, unit, period, basis, real_or_nominal: 'na' };
};

/** A point Quantity from an FnaDocument value; risk answers store the option index (binding.to_quantity). */
export const toQuantity = (slot: SlotDef, raw: unknown): Quantity => {
  let v = raw as number | string | boolean;
  if (slot.store_as === 'index' && slot.categories) {
    const i = Math.trunc(Number(raw));
    v = i >= 0 && i < slot.categories.length ? slot.categories[i] : String(raw);
  } else if (slot.store_as === 'int' || slot.value_type === 'count') {
    v = slot.categories ? String(Math.trunc(Number(raw))) : Math.trunc(Number(raw));
  }
  return quantityFor(slot, { kind: 'point', value: v });
};

const roundFor = (slot: SlotDef, x: number): number => {
  if (['age', 'years', 'months', 'count', 'year'].includes(slot.value_type)) return Math.trunc(pyRound(x));
  if (slot.value_type === 'amount') return pyRound(x, 2);
  if (slot.value_type === 'rate') return pyRound(x, 6);
  return x;
};

/** The FnaDocument value of a distribution at its median: median, mode or p ≥ 0.5 (binding.to_fna_value). */
export const toFnaValue = (slot: SlotDef, dist: Dist): unknown => {
  if (slot.value_type === 'text') return dist.kind === 'point' ? String(dist.value) : '';
  if (slot.value_type === 'boolean' || slot.value_type === 'probability' || isBoolean(dist)) return pTrue(dist) >= 0.5;
  if (slot.value_type === 'category' || slot.value_type === 'count' || isCategorical(dist)) {
    const label = modeLabel(dist);
    if (slot.store_as === 'index' && slot.categories) return slot.categories.includes(label) ? slot.categories.indexOf(label) : null;
    if (slot.store_as === 'int') return Math.trunc(Number(label));
    return label;
  }
  if (dist.kind === 'point' && typeof dist.value === 'string') return dist.value; // an exact ISO date
  const x = median(dist);
  if (slot.value_type === 'date') return yearToIso(x);
  return roundFor(slot, x);
};

/* ------------------------------------------------------------------ */
/* Paths ↔ slots and instances                                         */
/* ------------------------------------------------------------------ */

/** The FnaDocument row id of an entity: its own, or the id the pipeline gives a row it creates. */
export const rowIdFor = (callId: string, entities: Record<string, Entity>, entityId: string): string => entities[entityId]?.fna_id || rowIdForNewEntity(callId, entityId);

/** The concrete path of a slot for one instance (a person, a row's entity, an expense key). */
export const pathFor = (slot: SlotDef, instance: string | null, rowId: (entityId: string) => string): string => {
  if (isPerPerson(slot)) return slot.fna_path.replace('{p}', instance ?? 'client');
  if (slot.slot_id === 'expenses.items[]') return `expenses.items[${instance}]`;
  if (!slot.exported) return instance ? slot.fna_path.replace('[]', `[${instance}]`) : slot.fna_path;
  if (instance && slot.fna_path.includes('[]')) return slot.fna_path.replace('[]', `[${rowId(instance)}]`);
  return slot.fna_path;
};

export interface SlotAt {
  slot: SlotDef;
  /** The person, expense key or row's entity id; null for single-instance slots. */
  instance: string | null;
}

/**
 * The slot and instance a concrete document path belongs to, or null for fields the pipeline
 * does not extract (contact details, consents, the advice record …). `entityOfRow` names the
 * entity behind a list row.
 */
export const slotAtPath = (path: string, entityOfRow: (container: string, rowId: string) => string | null): SlotAt | null => {
  const pp = parsePath(path);
  if (pp.key !== null) {
    if (DICT_CONTAINERS.has(pp.container)) {
      const slot = pp.container === 'expenses.items' ? slotById('expenses.items[]') : slotById(`${pp.container}[${pp.key}]`);
      return slot ? { slot, instance: pp.container === 'expenses.items' ? pp.key : null } : null;
    }
    if (!isListContainer(pp.container) || !pp.field) return null;
    const slot = slotById(`${pp.container}[].${pp.field}`);
    if (!slot) return null;
    return { slot, instance: entityOfRow(pp.container, pp.key) };
  }
  const slot = slotById(path);
  if (!slot) return null;
  return { slot, instance: isPerPerson(slot) ? personOf(path) : null };
};

/* ------------------------------------------------------------------ */
/* Labels                                                              */
/* ------------------------------------------------------------------ */

const EXPENSE_LABEL = new Map(EXPENSE_ITEMS.map((e) => [e.key, e.label]));
const RISK_QUESTION = new Map(RISK_QUESTIONS.map((q, i) => [q.id, `Risk question ${i + 1}: ${q.question}`]));

/** A human label for one instance of a slot: "Groceries & toiletries", "Risk question 1: …", else the slot label. */
export const fieldLabel = (slot: SlotDef, instance: string | null): string => {
  if (slot.slot_id === 'expenses.items[]' && instance) return EXPENSE_LABEL.get(instance) ?? instance;
  const risk = /^riskProfile\.answers\[(.+)\]$/.exec(slot.slot_id);
  if (risk) return RISK_QUESTION.get(risk[1]) ?? slot.label;
  return slot.label;
};

/** The words a category is shown with (risk answers are the option labels already). */
export const categoryLabel = (slot: SlotDef, value: string): string => {
  if (slot.store_as === 'index') return value;
  return value.replace(/-/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
};

export const groupLabel = (group: string): string => group.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
