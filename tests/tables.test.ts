import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { analyse } from '@/lib/fna/analysis';
import { newDocument, newRetirementFund } from '@/lib/fna/defaults';
import { DEFAULT_TAX_YEAR, TAX_TABLE_FILES, TAX_TABLES, getTaxTable, getTaxTableFile, incomeTax } from '@/lib/fna/tax';

const DIR = join(import.meta.dirname, '..', 'fna_tables');
const files = readdirSync(DIR).filter((f) => /^za_\d{4}_\d{2}\.json$/.test(f));
const NOW = new Date('2026-09-29T12:00:00');

describe('shared SARS tables (fna_tables/)', () => {
  it('registers every table file, named after its tax year', () => {
    expect(files.length).toBeGreaterThanOrEqual(2);
    for (const f of files) {
      const raw = JSON.parse(readFileSync(join(DIR, f), 'utf8'));
      expect(f).toBe(`za_${raw.taxYear.replace('/', '_')}.json`);
      expect(TAX_TABLE_FILES[raw.taxYear]).toEqual(raw);
    }
    expect(Object.keys(TAX_TABLES).sort()).toEqual(Object.keys(TAX_TABLE_FILES).sort());
    expect(TAX_TABLES[DEFAULT_TAX_YEAR]).toBeDefined();
  });

  it('reports the table fields in their established order', () => {
    expect(Object.keys(getTaxTable('2026/27'))).toEqual([
      'label', 'period', 'endDate', 'status', 'brackets', 'rebates', 'thresholds', 'medicalCredits', 'retirementDeduction',
      'cgt', 'tfsa', 'uif', 'interestExemption', 'estateDuty', 'retirementLumpSum', 'withdrawalLumpSum', 'sources',
    ]);
  });

  it('falls back to the default year for an unknown tax year', () => {
    expect(getTaxTable('1999/00')).toBe(TAX_TABLES[DEFAULT_TAX_YEAR]);
    expect(getTaxTableFile('1999/00')).toBe(TAX_TABLE_FILES[DEFAULT_TAX_YEAR]);
  });

  it('keeps each year consistent: bracket bases reconcile and thresholds clear the rebates', () => {
    for (const f of Object.values(TAX_TABLE_FILES)) {
      const t = getTaxTable(f.taxYear);
      for (const brackets of [t.brackets, t.retirementLumpSum, t.withdrawalLumpSum]) {
        for (let i = 1; i < brackets.length; i++) {
          const [prev, cur] = [brackets[i - 1], brackets[i]];
          expect(cur.from).toBeGreaterThan(prev.from);
          expect(prev.base + (cur.from - prev.from) * prev.rate).toBeCloseTo(cur.base, 0);
        }
      }
      expect(incomeTax(t.thresholds.under65, 40, t)).toBeLessThan(1);
      expect(incomeTax(t.thresholds.age65to74, 70, t)).toBeLessThan(1);
      expect(incomeTax(t.thresholds.age75plus, 80, t)).toBeLessThan(1);
      expect(f.livingAnnuity.minDrawdown).toBeLessThan(f.livingAnnuity.maxDrawdown);
    }
  });

  it('tests a retiree drawdown against the living annuity maximum in the table', () => {
    const doc = newDocument();
    doc.client.dateOfBirth = '1956-01-01';
    doc.client.employmentType = 'retired';
    const la = newRetirementFund('client');
    Object.assign(la, { type: 'living-annuity', value: 1_000_000 });
    doc.retirementFunds = [la];
    doc.retirement.client = { ...doc.retirement.client, targetMode: 'amount', targetMonthly: 20_000 };
    const notes = analyse(doc, NOW).persons.client!.retirement.notes;
    expect(notes[0]).toBe('The required income exceeds the 17.5% maximum living annuity drawdown — the target is not achievable from this capital.');
  });
});
