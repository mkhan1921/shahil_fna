/**
 * South African tax tables (SARS) and tax calculators.
 *
 * Every SARS value lives in `fna_tables/za_<year>.json` (validated against
 * `fna_tables/schema.json`), shared with the Python engine in
 * `pipeline/advisor_intel/fna_engine`; nothing here hard-codes a SARS number.
 * Sources are listed per table in `TAX_TABLES[year].sources` and surfaced in
 * the report appendix so the adviser can evidence the basis of calculation.
 */
import za_2025_26 from '../../fna_tables/za_2025_26.json';
import za_2026_27 from '../../fna_tables/za_2026_27.json';

export interface Bracket {
  /** Lower bound of the bracket (exclusive of previous upper bound). */
  from: number;
  /** Tax on income up to `from`. */
  base: number;
  rate: number;
}

export interface TaxTable {
  label: string;
  period: string;
  /** Last day of the year of assessment — age for rebates is measured on this date. */
  endDate: string;
  status: 'confirmed' | 'provisional';
  brackets: Bracket[];
  rebates: { primary: number; secondary: number; tertiary: number };
  thresholds: { under65: number; age65to74: number; age75plus: number };
  medicalCredits: { main: number; firstDependant: number; additional: number };
  retirementDeduction: { rate: number; cap: number };
  uif: { rate: number; ceilingMonthly: number };
  interestExemption: { under65: number; over65: number };
  cgt: { inclusionRate: number; annualExclusion: number; deathExclusion: number; primaryResidence: number };
  estateDuty: { abatement: number; rateLow: number; threshold: number; rateHigh: number };
  retirementLumpSum: Bracket[];
  withdrawalLumpSum: Bracket[];
  tfsa: { annual: number; lifetime: number };
  sources: string[];
}

/**
 * Everything in one table file (fna_tables/schema.json). `TaxTable` is the part
 * the analysis reports; the rest holds SARS rules it reads but does not report.
 * TypeScript checks the JSON files against this shape when they are imported.
 */
export interface TaxTableFile extends Omit<TaxTable, 'status'> {
  schemaVersion: number;
  taxYear: string;
  /** First day of the year of assessment. */
  startDate: string;
  status: string;
  donations: { annualExemption: number; rateLow: number; threshold: number; rateHigh: number; source: string };
  /** Two-pot savings withdrawal benefit: taxed at the member's marginal rate. */
  savingsWithdrawal: { perTaxYear: number; minimum: number; source: string };
  /** Allowed annual living annuity drawdown, as a share of capital. */
  livingAnnuity: { minDrawdown: number; maxDrawdown: number; source: string };
}

/** Newest year first; each Budget adds a file to fna_tables/ and an import here. */
const TABLE_FILES: TaxTableFile[] = [za_2026_27, za_2025_26];

const isStatus = (s: string): s is TaxTable['status'] => s === 'confirmed' || s === 'provisional';

/** The reported part of a table file, with the fields in their established order. */
const toTaxTable = (f: TaxTableFile): TaxTable => {
  if (!isStatus(f.status)) throw new Error(`fna_tables ${f.taxYear}: unknown status "${f.status}"`);
  return {
    label: f.label,
    period: f.period,
    endDate: f.endDate,
    status: f.status,
    brackets: f.brackets,
    rebates: f.rebates,
    thresholds: f.thresholds,
    medicalCredits: f.medicalCredits,
    retirementDeduction: f.retirementDeduction,
    cgt: f.cgt,
    tfsa: f.tfsa,
    uif: f.uif,
    interestExemption: f.interestExemption,
    estateDuty: f.estateDuty,
    retirementLumpSum: f.retirementLumpSum,
    withdrawalLumpSum: f.withdrawalLumpSum,
    sources: f.sources,
  };
};

export const TAX_TABLE_FILES: Record<string, TaxTableFile> = Object.fromEntries(TABLE_FILES.map((f) => [f.taxYear, f]));

export const TAX_TABLES: Record<string, TaxTable> = Object.fromEntries(TABLE_FILES.map((f) => [f.taxYear, toTaxTable(f)]));

export const DEFAULT_TAX_YEAR = '2026/27';

export const getTaxTable = (year: string): TaxTable => TAX_TABLES[year] ?? TAX_TABLES[DEFAULT_TAX_YEAR];

/** The whole table file for a year (falls back to the default year, like `getTaxTable`). */
export const getTaxTableFile = (year: string): TaxTableFile => TAX_TABLE_FILES[year] ?? TAX_TABLE_FILES[DEFAULT_TAX_YEAR];

export const TAX_YEARS = (): string[] => Object.keys(TAX_TABLES);

/** Apply a progressive bracket table (brackets carry cumulative base amounts). */
export const applyBrackets = (amount: number, brackets: Bracket[]): number => {
  if (amount <= 0) return 0;
  let bracket = brackets[0];
  for (const b of brackets) {
    if (amount > b.from) bracket = b;
  }
  return bracket.base + (amount - bracket.from) * bracket.rate;
};

export const marginalRate = (taxable: number, table: TaxTable): number => {
  let rate = table.brackets[0].rate;
  for (const b of table.brackets) if (taxable > b.from) rate = b.rate;
  return rate;
};

export const rebatesFor = (age: number, table: TaxTable): number => {
  let rebate = table.rebates.primary;
  if (age >= 65) rebate += table.rebates.secondary;
  if (age >= 75) rebate += table.rebates.tertiary;
  return rebate;
};

/** Annual s6A medical scheme fees tax credit. */
export const medicalTaxCredit = (members: number, table: TaxTable): number => {
  if (members <= 0) return 0;
  const { main, firstDependant, additional } = table.medicalCredits;
  const monthly = main + (members >= 2 ? firstDependant : 0) + Math.max(0, members - 2) * additional;
  return monthly * 12;
};

/** Normal tax after rebates, before medical credits. */
export const incomeTax = (taxable: number, age: number, table: TaxTable): number =>
  Math.max(0, applyBrackets(taxable, table.brackets) - rebatesFor(age, table));

/**
 * s11F deduction for contributions to pension, provident and retirement
 * annuity funds: lesser of contributions, 27.5% of the greater of
 * remuneration or taxable income, and the annual cap.
 */
export const retirementDeduction = (contributions: number, remuneration: number, table: TaxTable): number => {
  const { rate, cap } = table.retirementDeduction;
  return Math.max(0, Math.min(contributions, rate * remuneration, cap));
};

export interface PersonTaxInput {
  age: number;
  grossMonthly: number;
  annualBonus: number;
  otherTaxableMonthly: number;
  /** Annual retirement fund contributions (employee + employer fringe + RA). */
  retirementContributionsAnnual: number;
  /** Employer retirement contributions (a taxable fringe benefit, s7(8)). */
  employerRetirementAnnual: number;
  medicalSchemeMembers: number;
  uifApplies: boolean;
}

export interface PersonTaxResult {
  grossAnnual: number;
  remuneration: number;
  retirementDeduction: number;
  taxable: number;
  taxBeforeRebates: number;
  rebates: number;
  medicalCredit: number;
  annualTax: number;
  monthlyPaye: number;
  monthlyUif: number;
  marginalRate: number;
  effectiveRate: number;
}

export const personTax = (input: PersonTaxInput, table: TaxTable): PersonTaxResult => {
  const cashIncome = input.grossMonthly * 12 + input.annualBonus + input.otherTaxableMonthly * 12;
  // Employer contributions are a fringe benefit included in remuneration and then
  // deductible, so they count on both sides of the s11F calculation.
  const remuneration = cashIncome + input.employerRetirementAnnual;
  const deduction = retirementDeduction(input.retirementContributionsAnnual, remuneration, table);
  const taxable = Math.max(0, remuneration - deduction);
  const before = applyBrackets(taxable, table.brackets);
  const rebates = Math.min(before, rebatesFor(input.age, table));
  const afterRebates = Math.max(0, before - rebates);
  const medicalCredit = Math.min(afterRebates, medicalTaxCredit(input.medicalSchemeMembers, table));
  const annualTax = Math.max(0, afterRebates - medicalCredit);
  const uifBase = Math.min(input.grossMonthly, table.uif.ceilingMonthly);
  return {
    grossAnnual: cashIncome,
    remuneration,
    retirementDeduction: deduction,
    taxable,
    taxBeforeRebates: before,
    rebates,
    medicalCredit,
    annualTax,
    monthlyPaye: annualTax / 12,
    monthlyUif: input.uifApplies ? uifBase * table.uif.rate : 0,
    marginalRate: taxable > 0 ? marginalRate(taxable, table) : 0,
    effectiveRate: cashIncome > 0 ? annualTax / cashIncome : 0,
  };
};

/** Tax on a retirement or death lump sum (retirement table), ignoring prior lump sums. */
export const retirementLumpSumTax = (amount: number, table: TaxTable, priorLumpSums = 0): number =>
  Math.max(0, applyBrackets(amount + priorLumpSums, table.retirementLumpSum) - applyBrackets(priorLumpSums, table.retirementLumpSum));

/** Tax on a pre-retirement withdrawal lump sum (withdrawal table). */
export const withdrawalLumpSumTax = (amount: number, table: TaxTable, priorLumpSums = 0): number =>
  Math.max(0, applyBrackets(amount + priorLumpSums, table.withdrawalLumpSum) - applyBrackets(priorLumpSums, table.withdrawalLumpSum));

/** Estate duty on the dutiable amount (after abatement). */
export const estateDuty = (dutiable: number, table: TaxTable): number => {
  if (dutiable <= 0) return 0;
  const { rateLow, threshold, rateHigh } = table.estateDuty;
  return Math.min(dutiable, threshold) * rateLow + Math.max(0, dutiable - threshold) * rateHigh;
};

/** Incremental income tax caused by adding `extraTaxable` to a base taxable income. */
export const incrementalTax = (baseTaxable: number, extraTaxable: number, age: number, table: TaxTable): number =>
  Math.max(0, incomeTax(baseTaxable + extraTaxable, age, table) - incomeTax(baseTaxable, age, table));
