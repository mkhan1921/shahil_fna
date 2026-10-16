/**
 * Concrete FnaDocument paths, in exactly the pipeline's syntax (binding.parse_path / get_path / set_path):
 *
 *   income.client.grossMonthly          a field of a nested object
 *   dependants[<row id>].tertiaryStartAge  a field of one list row
 *   expenses.items[groceries]           one entry of a dict
 *   expenses.custom[<row id>].amount    a field of a custom expense row
 *   riskProfile.answers[horizon]        one risk answer (stored as the option index)
 *
 * Forecast-only slots (event[...], income.{p}.expectedChange[...]) use the same syntax but have
 * no place in the document.
 */
import { newAsset, newDependant, newDocument, newGoal, newLiability, newPolicy, newRetirementFund } from '../fna/defaults';
import type { FnaDocument } from '../fna/types';

export const DICT_CONTAINERS = new Set(['expenses.items', 'riskProfile.answers']);
export const LIST_CONTAINERS = ['dependants', 'retirementFunds', 'assets', 'liabilities', 'policies', 'goals', 'expenses.custom'] as const;
export type ListContainer = (typeof LIST_CONTAINERS)[number];

export const isListContainer = (c: string): c is ListContainer => (LIST_CONTAINERS as readonly string[]).includes(c);

/** The field that identifies a row: a reviewer-removed identity fact removes the row (§3.6 "removed"). */
export const IDENTITY_FIELD: Record<ListContainer, string> = {
  dependants: 'relationship',
  retirementFunds: 'type',
  assets: 'type',
  liabilities: 'type',
  policies: 'type',
  goals: 'name',
  'expenses.custom': 'label',
};

/** The FnaDocument list of each catalog list entity (binding.ENTITY_LIST). */
export const ENTITY_LIST: Record<string, ListContainer> = {
  dependant: 'dependants',
  fund: 'retirementFunds',
  asset: 'assets',
  liability: 'liabilities',
  policy: 'policies',
  goal: 'goals',
  expense: 'expenses.custom',
};

export interface PathParts {
  /** "client", "income.client", "dependants", "expenses.items" … */
  container: string;
  /** The row id or dict key, when the container is a list or dict. */
  key: string | null;
  /** The field within the row (null for dict entries). */
  field: string | null;
}

export const parsePath = (path: string): PathParts => {
  const m = /^(.*?)\[([^\]]*)\](?:\.(\w+))?$/.exec(path);
  if (m) return { container: m[1], key: m[2], field: m[3] ?? null };
  const i = path.lastIndexOf('.');
  return { container: i < 0 ? '' : path.slice(0, i), key: null, field: path.slice(i + 1) };
};

export const formatPath = (p: PathParts): string => (p.key === null ? `${p.container}.${p.field}` : `${p.container}[${p.key}]${p.field ? `.${p.field}` : ''}`);

type Node = Record<string, unknown>;

let template: FnaDocument | null = null;
/** A fresh document with the FNA defaults, built once: only read, never handed out. */
const blank = (): FnaDocument => (template ??= newDocument());

const containerOf = (doc: unknown, dotted: string): unknown => {
  let node: unknown = doc;
  for (const part of dotted.split('.')) {
    if (node === null || typeof node !== 'object') return undefined;
    node = (node as Node)[part];
  }
  return node;
};

/** Whether the path names something the FnaDocument can hold (forecast-only slots cannot). */
export const isDocumentPath = (path: string): boolean => {
  const pp = parsePath(path);
  const node = containerOf(blank(), pp.container);
  if (pp.key === null) return !!node && typeof node === 'object' && !Array.isArray(node) && pp.field !== null && pp.field in (node as Node);
  if (DICT_CONTAINERS.has(pp.container)) return true;
  return isListContainer(pp.container) && pp.field !== null && pp.field in newRow(pp.container, 'x');
};

export const getPath = (doc: FnaDocument | Node, path: string): unknown => {
  const pp = parsePath(path);
  const node = containerOf(doc, pp.container);
  if (pp.key === null) return node && typeof node === 'object' ? (node as Node)[pp.field as string] : undefined;
  if (DICT_CONTAINERS.has(pp.container)) return node && typeof node === 'object' ? (node as Node)[pp.key] : undefined;
  if (!Array.isArray(node)) return undefined;
  const row = (node as Node[]).find((r) => r.id === pp.key);
  return row === undefined || pp.field === null ? row : row[pp.field];
};

/** A row with the container's defaults (as the pipeline's row models) and the given id. */
export const newRow = (container: ListContainer, id: string): Node => {
  const base: Node = (() => {
    switch (container) {
      case 'dependants':
        return { ...newDependant() };
      case 'retirementFunds':
        return { ...newRetirementFund() };
      case 'assets':
        return { ...newAsset() };
      case 'liabilities':
        return { ...newLiability() };
      case 'policies':
        return { ...newPolicy() };
      case 'goals':
        return { ...newGoal() };
      case 'expenses.custom':
        return { id, category: 'other', label: '', amount: 0, essential: false };
    }
  })();
  return { ...base, id };
};

/** The value a field takes in a fresh row or a fresh document: the FNA default. */
export const defaultAt = (path: string): unknown => {
  const pp = parsePath(path);
  if (pp.key === null) return structuredClone(getPath(blank(), path));
  if (DICT_CONTAINERS.has(pp.container)) return undefined;
  if (isListContainer(pp.container) && pp.field) return newRow(pp.container, pp.key)[pp.field];
  return undefined;
};

/** Set a value, creating a list row (with the container's defaults) when the id is new. `undefined` deletes a dict entry. */
export const setPath = (doc: FnaDocument | Node, path: string, value: unknown): void => {
  const pp = parsePath(path);
  const node = containerOf(doc, pp.container);
  if (node === undefined || node === null || typeof node !== 'object') throw new RangeError(`no container for ${path}`);
  if (pp.key === null) {
    (node as Node)[pp.field as string] = value;
    return;
  }
  if (DICT_CONTAINERS.has(pp.container)) {
    if (value === undefined) delete (node as Node)[pp.key];
    else (node as Node)[pp.key] = value;
    return;
  }
  if (!isListContainer(pp.container) || !Array.isArray(node)) throw new RangeError(`not a list: ${pp.container}`);
  let row = (node as Node[]).find((r) => r.id === pp.key);
  if (!row) {
    row = newRow(pp.container, pp.key);
    (node as Node[]).push(row);
  }
  if (pp.field) row[pp.field] = value;
};

export const hasRow = (doc: FnaDocument | Node, container: string, key: string): boolean => {
  const node = containerOf(doc, container);
  return Array.isArray(node) && (node as Node[]).some((r) => r.id === key);
};

export const removeRow = (doc: FnaDocument | Node, container: string, key: string): void => {
  const node = containerOf(doc, container);
  if (!Array.isArray(node)) return;
  const i = (node as Node[]).findIndex((r) => r.id === key);
  if (i >= 0) (node as Node[]).splice(i, 1);
};

/* ------------------------------------------------------------------ */
/* Which paths changed between two documents                           */
/* ------------------------------------------------------------------ */

const OBJECT_CONTAINERS = ['client', 'spouse', 'household', 'income.client', 'income.spouse', 'retirement.client', 'retirement.spouse', 'estate.client', 'estate.spouse'] as const;

const same = (a: unknown, b: unknown) => a === b || JSON.stringify(a) === JSON.stringify(b);

export interface DocChanges {
  /** Changed fields, in path syntax (rows present in both documents only). */
  fields: string[];
  /** Rows only in the second document, as "container[id]". */
  addedRows: string[];
  /** Rows only in the first document, as "container[id]". */
  removedRows: string[];
  /** Whether anything outside the fact-bearing containers changed (engagement, advice, assumptions, notes …). */
  other: boolean;
}

/** The fact-bearing changes from `a` to `b`, in path syntax. */
export const diffDocuments = (a: FnaDocument, b: FnaDocument): DocChanges => {
  const fields: string[] = [];
  const addedRows: string[] = [];
  const removedRows: string[] = [];
  for (const c of OBJECT_CONTAINERS) {
    const x = (containerOf(a, c) ?? {}) as Node;
    const y = (containerOf(b, c) ?? {}) as Node;
    for (const k of new Set([...Object.keys(x), ...Object.keys(y)])) if (!same(x[k], y[k])) fields.push(`${c}.${k}`);
  }
  for (const c of DICT_CONTAINERS) {
    const x = (containerOf(a, c) ?? {}) as Node;
    const y = (containerOf(b, c) ?? {}) as Node;
    for (const k of new Set([...Object.keys(x), ...Object.keys(y)])) if (!same(x[k], y[k])) fields.push(`${c}[${k}]`);
  }
  for (const c of LIST_CONTAINERS) {
    const xs = (containerOf(a, c) ?? []) as Node[];
    const ys = (containerOf(b, c) ?? []) as Node[];
    const byId = new Map(xs.map((r) => [String(r.id), r]));
    const seen = new Set<string>();
    for (const r of ys) {
      const id = String(r.id);
      seen.add(id);
      const old = byId.get(id);
      if (!old) {
        addedRows.push(`${c}[${id}]`);
        continue;
      }
      for (const k of new Set([...Object.keys(old), ...Object.keys(r)])) if (k !== 'id' && !same(old[k], r[k])) fields.push(`${c}[${id}].${k}`);
    }
    for (const r of xs) if (!seen.has(String(r.id))) removedRows.push(`${c}[${String(r.id)}]`);
  }
  const skip = new Set(['client', 'spouse', 'household', 'income', 'retirement', 'estate', 'expenses', 'riskProfile', 'dependants', 'retirementFunds', 'assets', 'liabilities', 'policies', 'goals', 'updatedAt', 'status']);
  const outside = Object.keys({ ...a, ...b }).filter((k) => !skip.has(k)) as (keyof FnaDocument)[];
  const other =
    outside.some((k) => !same(a[k], b[k])) ||
    !same(a.riskProfile.notes, b.riskProfile.notes) ||
    (['income', 'retirement', 'estate'] as const).some((k) => !same(Object.keys(a[k]), Object.keys(b[k])));
  return { fields, addedRows, removedRows, other };
};
