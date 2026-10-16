/**
 * How facts are named to the adviser — "Thabo · Retirement age", "Kagiso · Tertiary cost annual",
 * "Home loan · Balance" — and who a fact could be moved to on re-attribution or added for.
 */
import { personName } from '../fna/analysis';
import { ASSET_TYPE, EXPENSE_ITEMS, LIABILITY_TYPE, POLICY_TYPE, RELATIONSHIP, RETIREMENT_FUND_TYPE } from '../fna/catalog';
import type { FnaDocument } from '../fna/types';
import { isPerPerson, listOf, slotById, type SlotDef } from './catalog';
import { getPath, isListContainer, parsePath, type ListContainer } from './paths';
import { entityOfRow, type ReviewFact, type ReviewSession } from './session';

type Row = Record<string, unknown>;
const s = (v: unknown) => (typeof v === 'string' ? v.trim() : '');

/** A list row as the adviser knows it: the dependant's name, the policy's insurer and type … */
export const rowLabel = (container: string, row: Row | undefined): string => {
  if (!row) return '';
  switch (container) {
    case 'dependants':
      return s(row.name).split(' ')[0] || RELATIONSHIP[row.relationship as keyof typeof RELATIONSHIP] || 'Dependant';
    case 'retirementFunds':
      return s(row.name) || s(row.provider) || RETIREMENT_FUND_TYPE[row.type as keyof typeof RETIREMENT_FUND_TYPE] || 'Retirement fund';
    case 'assets':
      return s(row.description) || ASSET_TYPE[row.type as keyof typeof ASSET_TYPE] || 'Asset';
    case 'liabilities':
      return s(row.description) || s(row.lender) || LIABILITY_TYPE[row.type as keyof typeof LIABILITY_TYPE] || 'Liability';
    case 'policies':
      return [s(row.insurer), POLICY_TYPE[row.type as keyof typeof POLICY_TYPE]].filter(Boolean).join(' · ') || 'Policy';
    case 'goals':
      return s(row.name) || 'Goal';
    case 'expenses.custom':
      return s(row.label) || 'Other expense';
    default:
      return '';
  }
};

export const firstName = (doc: FnaDocument, key: 'client' | 'spouse') => doc[key].firstName || (key === 'client' ? 'Client' : 'Spouse');

/** "Thabo & Lerato Mokoena". */
export const householdName = (doc: FnaDocument): string => {
  const names = [doc.client.firstName, doc.household.includeSpouse ? doc.spouse.firstName : ''].filter(Boolean);
  return names.length ? `${names.join(' & ')} ${doc.client.surname}`.trim() : 'Unnamed client';
};

/** Who or what a fact is about: a person's first name, a row's label, an event's name; empty for household fields. */
export const factSubject = (session: ReviewSession, f: Pick<ReviewFact, 'slotId' | 'instanceKey' | 'path'>): string => {
  const slot = slotById(f.slotId);
  if (!slot) return '';
  if (isPerPerson(slot)) return firstName(session.working, f.instanceKey === 'spouse' ? 'spouse' : 'client');
  const pp = parsePath(f.path);
  if (pp.key !== null && isListContainer(pp.container)) {
    const row = getPath(session.working, `${pp.container}[${pp.key}]`) as Row | undefined;
    return rowLabel(pp.container, row ?? (getPath(session.bundle.median_document, `${pp.container}[${pp.key}]`) as Row | undefined)) || (f.instanceKey ? session.bundle.entities[f.instanceKey]?.canonical_name : '') || '';
  }
  if (!slot.exported && f.instanceKey) return session.bundle.entities[f.instanceKey]?.canonical_name ?? '';
  return '';
};

/** "Thabo · Gross monthly". */
export const factTitle = (session: ReviewSession, f: ReviewFact): string => [factSubject(session, f), f.label].filter(Boolean).join(' · ');

export interface InstanceChoice {
  /** null for a new row. */
  instance: string | null;
  label: string;
  newRow?: boolean;
}

const hasPartner = (doc: FnaDocument) => doc.household.includeSpouse && ['married', 'life-partner'].includes(doc.household.maritalStatus);

/** Who a fact of this slot could belong to: each person, each row of its list (and a new row), or each budget line. */
export const instanceChoices = (session: ReviewSession, slot: SlotDef): InstanceChoice[] => {
  const doc = session.working;
  if (isPerPerson(slot)) {
    const out: InstanceChoice[] = [{ instance: 'client', label: personName(doc.client, 'Client') }];
    if (hasPartner(doc)) out.push({ instance: 'spouse', label: personName(doc.spouse, 'Spouse') });
    return out;
  }
  if (slot.slot_id === 'expenses.items[]') return EXPENSE_ITEMS.map((e) => ({ instance: e.key, label: e.label }));
  const list = listOf(slot);
  if (!list || !isListContainer(list) || !slot.exported) return [];
  const rows = (list === 'expenses.custom' ? doc.expenses.custom : (doc as unknown as Record<ListContainer, Row[]>)[list]) as unknown as Row[];
  const out = rows.flatMap((row) => {
    const entity = entityOfRow(session, list, String(row.id));
    return entity ? [{ instance: entity, label: rowLabel(list, row) }] : [];
  });
  return [...out, { instance: null, label: 'A new row', newRow: true }];
};
