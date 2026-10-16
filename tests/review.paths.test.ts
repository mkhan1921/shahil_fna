/** Concrete FnaDocument paths and row ids, exactly as the pipeline writes them (binding.py). */
import { describe, expect, it } from 'vitest';
import { newDocument } from '@/lib/fna/defaults';
import { factIdFor, genericSlotId, pathFor, personOf, slotAtPath, slotById, splitFactId } from '@/lib/review/catalog';
import { defaultAt, diffDocuments, formatPath, getPath, hasRow, isDocumentPath, parsePath, removeRow, setPath } from '@/lib/review/paths';
import { NAMESPACE_URL, rowIdForNewEntity, sha1, uuid5 } from '@/lib/review/uuid';

describe('path syntax', () => {
  it('parses as binding.parse_path does: container, row id or dict key, field', () => {
    expect(parsePath('dependants[abc].tertiaryStartAge')).toEqual({ container: 'dependants', key: 'abc', field: 'tertiaryStartAge' });
    expect(parsePath('income.client.grossMonthly')).toEqual({ container: 'income.client', key: null, field: 'grossMonthly' });
    expect(parsePath('expenses.items[groceries]')).toEqual({ container: 'expenses.items', key: 'groceries', field: null });
    expect(parsePath('expenses.custom[c-1].amount')).toEqual({ container: 'expenses.custom', key: 'c-1', field: 'amount' });
    expect(parsePath('riskProfile.answers[horizon]')).toEqual({ container: 'riskProfile.answers', key: 'horizon', field: null });
    expect(parsePath('client.smoker')).toEqual({ container: 'client', key: null, field: 'smoker' });
    expect(parsePath('event[goal.kagiso_overseas_study].occurs')).toEqual({ container: 'event', key: 'goal.kagiso_overseas_study', field: 'occurs' });
    for (const p of ['dependants[abc].tertiaryStartAge', 'income.client.grossMonthly', 'expenses.items[groceries]', 'riskProfile.answers[horizon]']) expect(formatPath(parsePath(p))).toBe(p);
  });

  it('gets and sets like binding.get_path and set_path: new rows take the FNA defaults', () => {
    const d = newDocument();
    setPath(d, 'expenses.items[groceries]', 7000);
    setPath(d, 'dependants[abc].tertiaryStartAge', 18);
    setPath(d, 'riskProfile.answers[horizon]', 4);
    setPath(d, 'income.client.grossMonthly', 45000);
    expect(getPath(d, 'expenses.items[groceries]')).toBe(7000);
    expect(d.dependants[0].id).toBe('abc');
    expect(d.dependants[0].tertiaryCostAnnual).toBe(150000);
    expect(getPath(d, 'dependants[abc].tertiaryStartAge')).toBe(18);
    expect(getPath(d, 'riskProfile.answers[horizon]')).toBe(4);
    expect(d.income.client.grossMonthly).toBe(45000);
    expect(getPath(d, 'dependants[nope].name')).toBeUndefined();
    expect(hasRow(d, 'dependants', 'abc')).toBe(true);
    removeRow(d, 'dependants', 'abc');
    expect(hasRow(d, 'dependants', 'abc')).toBe(false);
    setPath(d, 'expenses.items[groceries]', undefined);
    expect('groceries' in d.expenses.items).toBe(false);
    expect(() => setPath(d, 'nowhere.at.all', 1)).toThrow(RangeError);
  });

  it('knows which paths the document can hold and their defaults', () => {
    expect(isDocumentPath('income.client.grossMonthly')).toBe(true);
    expect(isDocumentPath('dependants[x].schoolFeesAnnual')).toBe(true);
    expect(isDocumentPath('expenses.items[groceries]')).toBe(true);
    expect(isDocumentPath('event[goal.x].occurs')).toBe(false);
    expect(isDocumentPath('income.client.expectedChange[x].year')).toBe(false);
    expect(defaultAt('dependants[x].tertiaryCostAnnual')).toBe(150000);
    expect(defaultAt('expenses.items[groceries]')).toBeUndefined();
  });

  it('diffs two documents into field, row and other changes', () => {
    const a = newDocument();
    a.dependants.push({ ...structuredClone(newDocument().dependants[0] ?? {}), id: 'kid', name: 'Neo' } as (typeof a.dependants)[number]);
    const b = structuredClone(a);
    b.income.client.grossMonthly = 1;
    b.expenses.items.groceries = 9000;
    b.dependants[0].name = 'Neo M';
    b.goals.push({ ...(structuredClone(a.goals[0] ?? {}) as (typeof a.goals)[number]), id: 'g1', name: 'Car' });
    b.notes = 'changed';
    const d = diffDocuments(a, b);
    expect(d.fields).toEqual(expect.arrayContaining(['income.client.grossMonthly', 'expenses.items[groceries]', 'dependants[kid].name']));
    expect(d.addedRows).toEqual(['goals[g1]']);
    expect(d.removedRows).toEqual([]);
    expect(d.other).toBe(true);
    expect(diffDocuments(b, a).removedRows).toEqual(['goals[g1]']);
  });
});

describe('slots and their paths', () => {
  it('names facts as the pipeline does: slot#instance', () => {
    expect(factIdFor('retirement.{p}.retirementAge', 'client')).toBe('retirement.{p}.retirementAge#client');
    expect(splitFactId('expenses.items[]#groceries')).toEqual({ slotId: 'expenses.items[]', instance: 'groceries' });
    expect(splitFactId('riskProfile.answers[horizon]')).toEqual({ slotId: 'riskProfile.answers[horizon]', instance: null });
    expect(genericSlotId('income.spouse.grossMonthly')).toBe('income.{p}.grossMonthly');
    expect(personOf('retirement.spouse.retirementAge')).toBe('spouse');
  });

  it('maps a slot instance to its path and back', () => {
    const rowOf = (e: string) => (e === 'person.neo' ? 'row-neo' : rowIdForNewEntity('call-1', e));
    const entityOf = (c: string, k: string) => (c === 'dependants' && k === 'row-neo' ? 'person.neo' : null);
    const cases: [string, string | null, string][] = [
      ['income.{p}.grossMonthly', 'spouse', 'income.spouse.grossMonthly'],
      ['dependants[].schoolFeesAnnual', 'person.neo', 'dependants[row-neo].schoolFeesAnnual'],
      ['expenses.items[]', 'groceries', 'expenses.items[groceries]'],
      ['riskProfile.answers[horizon]', null, 'riskProfile.answers[horizon]'],
      ['event[].occurs', 'goal.study', 'event[goal.study].occurs'],
    ];
    for (const [slotId, instance, path] of cases) {
      const slot = slotById(slotId)!;
      expect(slot, slotId).toBeDefined();
      expect(pathFor(slot, instance, rowOf)).toBe(path);
      if (slot.exported) expect(slotAtPath(path, entityOf)).toEqual({ slot, instance });
    }
    expect(slotAtPath('client.email', entityOf)).toBeNull();
    expect(slotAtPath('engagement.meetingDate', entityOf)).toBeNull();
  });
});

describe('row ids for rows the call creates', () => {
  it('are uuid5(NAMESPACE_URL, "advisor-intel/{call}/{entity}"), as binding.row_id_for', () => {
    expect(rowIdForNewEntity('call-2026-10-15-mokoena', 'property.krugerrands_new')).toBe('a61de0ae-2fb9-5c1e-ad5d-e07332b4b4a2');
    expect(rowIdForNewEntity('call-2026-10-15-mokoena', 'policy.group_disability_new')).toBe('d58b14a2-1075-5f87-9784-cb52334a54fa');
    expect(rowIdForNewEntity('call-x', 'person.thandi_1')).toBe('40200422-f4ab-5de2-8c1e-ade9a8f617a4');
    expect(uuid5(NAMESPACE_URL, 'https://example.com/')).toBe('dd2c1780-811a-5296-81c5-178a0ef488bc');
    expect(uuid5(NAMESPACE_URL, '')).toBe('1b4db7eb-4057-5ddf-91e0-36dec72071f5');
  });

  it('rest on a correct SHA-1', () => {
    const hex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
    expect(hex(sha1(new TextEncoder().encode('abc')))).toBe('a9993e364706816aba3e25717850c26c9cd0d89d');
    expect(hex(sha1(new Uint8Array()))).toBe('da39a3ee5e6b4b0d3255bfef95601890afd80709');
    expect(hex(sha1(new TextEncoder().encode('a'.repeat(1000))))).toBe('291e9a6c66994949b57ba5e650361e98fc36b1ba');
  });
});
