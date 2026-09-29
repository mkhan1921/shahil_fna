/**
 * South African tax tables (SARS) and tax calculators.
 *
 * Sources are listed per table in `TAX_TABLES[year].sources` and surfaced in
 * the report appendix so the adviser can evidence the basis of calculation.
 */

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

const RETIREMENT_LUMP_SUM: Bracket[] = [
  { from: 0, base: 0, rate: 0 },
  { from: 550_000, base: 0, rate: 0.18 },
  { from: 770_000, base: 39_600, rate: 0.27 },
  { from: 1_155_000, base: 143_550, rate: 0.36 },
];

const WITHDRAWAL_LUMP_SUM: Bracket[] = [
  { from: 0, base: 0, rate: 0 },
  { from: 27_500, base: 0, rate: 0.18 },
  { from: 726_000, base: 125_730, rate: 0.27 },
  { from: 1_089_000, base: 223_740, rate: 0.36 },
];

const COMMON = {
  uif: { rate: 0.01, ceilingMonthly: 17_712 },
  interestExemption: { under65: 23_800, over65: 34_500 },
  estateDuty: { abatement: 3_500_000, rateLow: 0.2, threshold: 30_000_000, rateHigh: 0.25 },
  retirementLumpSum: RETIREMENT_LUMP_SUM,
  withdrawalLumpSum: WITHDRAWAL_LUMP_SUM,
};

export const TAX_TABLES: Record<string, TaxTable> = {
  '2026/27': {
    label: '2026/27',
    period: '1 March 2026 – 28 February 2027',
    status: 'confirmed',
    brackets: [
      { from: 0, base: 0, rate: 0.18 },
      { from: 245_100, base: 44_118, rate: 0.26 },
      { from: 383_100, base: 79_998, rate: 0.31 },
      { from: 530_200, base: 125_599, rate: 0.36 },
      { from: 695_800, base: 185_215, rate: 0.39 },
      { from: 887_000, base: 259_783, rate: 0.41 },
      { from: 1_878_600, base: 666_339, rate: 0.45 },
    ],
    rebates: { primary: 17_820, secondary: 9_765, tertiary: 3_249 },
    thresholds: { under65: 99_000, age65to74: 153_250, age75plus: 171_300 },
    medicalCredits: { main: 376, firstDependant: 376, additional: 254 },
    retirementDeduction: { rate: 0.275, cap: 430_000 },
    cgt: { inclusionRate: 0.4, annualExclusion: 50_000, deathExclusion: 440_000, primaryResidence: 3_000_000 },
    tfsa: { annual: 46_000, lifetime: 500_000 },
    ...COMMON,
    sources: [
      'SARS — Rates of Tax for Individuals: https://www.sars.gov.za/tax-rates/income-tax/rates-of-tax-for-individuals/',
      'SARS — Budget 2026 Tax Guide: https://www.sars.gov.za/wp-content/uploads/Docs/Budget/Budget2026/Budget-tax-guide-2026-online-version-for-printing.pdf',
      'National Treasury — 2026 Budget Review, Chapter 4: https://www.treasury.gov.za/documents/National%20Budget/2026/review/Chapter%204.pdf',
      'SARS — Retirement lump sum benefits: https://www.sars.gov.za/tax-rates/income-tax/retirement-lump-sum-benefits/',
      'SARS — Estate duty: https://www.sars.gov.za/types-of-tax/estate-duty/',
    ],
  },
  '2025/26': {
    label: '2025/26',
    period: '1 March 2025 – 28 February 2026',
    status: 'confirmed',
    brackets: [
      { from: 0, base: 0, rate: 0.18 },
      { from: 237_100, base: 42_678, rate: 0.26 },
      { from: 370_500, base: 77_362, rate: 0.31 },
      { from: 512_800, base: 121_475, rate: 0.36 },
      { from: 673_000, base: 179_147, rate: 0.39 },
      { from: 857_900, base: 251_258, rate: 0.41 },
      { from: 1_817_000, base: 644_489, rate: 0.45 },
    ],
    rebates: { primary: 17_235, secondary: 9_444, tertiary: 3_145 },
    thresholds: { under65: 95_750, age65to74: 148_217, age75plus: 165_689 },
    medicalCredits: { main: 364, firstDependant: 364, additional: 246 },
    retirementDeduction: { rate: 0.275, cap: 350_000 },
    cgt: { inclusionRate: 0.4, annualExclusion: 40_000, deathExclusion: 300_000, primaryResidence: 2_000_000 },
    tfsa: { annual: 36_000, lifetime: 500_000 },
    ...COMMON,
    sources: [
      'SARS — Rates of Tax for Individuals (2026 tax year): https://www.sars.gov.za/tax-rates/income-tax/rates-of-tax-for-individuals/',
      'SARS — Medical Tax Credit Rates: https://www.sars.gov.za/tax-rates/medical-tax-credit-rates/',
      'SARS — Retirement lump sum benefits: https://www.sars.gov.za/tax-rates/income-tax/retirement-lump-sum-benefits/',
    ],
  },
};

export const DEFAULT_TAX_YEAR = '2026/27';

export const getTaxTable = (year: string): TaxTable => TAX_TABLES[year] ?? TAX_TABLES[DEFAULT_TAX_YEAR];

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
