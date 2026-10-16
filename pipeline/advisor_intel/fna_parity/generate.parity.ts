/**
 * TypeScript side of the FNA parity harness (spec §12.7).
 *
 * Builds FNA_PARITY_N documents (default 2,000) from a seeded PRNG: the sample household, random
 * perturbations of every field, fresh random households (single and married, ICOP and accrual, parents
 * and siblings as dependants, every asset, liability, policy and fund type), retirees, sparse and empty
 * documents and deliberate edge cases, in both tax years. Runs analyse() and draftRecommendations() on
 * each with a fixed date and writes FNA_PARITY_OUT/parity.jsonl plus meta.json (the catalogs and tables)
 * for pipeline/tests/test_fna_parity.py, which checks the Python port against them.
 */
import { createWriteStream, mkdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { afterAll, beforeAll, expect, it, vi } from 'vitest';
import { analyse } from '@/lib/fna/analysis';
import * as catalog from '@/lib/fna/catalog';
import { DEFAULT_ASSUMPTIONS, newAsset, newDependant, newDocument, newGoal, newLiability, newPolicy, newRecommendation, newRetirementFund } from '@/lib/fna/defaults';
import { draftRecommendations } from '@/lib/fna/recommend';
import { RISK_CATEGORIES, RISK_QUESTIONS } from '@/lib/fna/riskProfile';
import { sampleDocument } from '@/lib/fna/sample';
import { TAX_TABLE_FILES, TAX_TABLES } from '@/lib/fna/tax';
import type {
  Asset,
  AssetType,
  Beneficiary,
  Dependant,
  DisabilityDefinition,
  Earmark,
  EmploymentType,
  ExpenseCategory,
  FnaDocument,
  Gender,
  LiabilityType,
  MaritalRegime,
  MaritalStatus,
  OtherGoal,
  Owner,
  Person,
  PersonKey,
  Policy,
  PolicyType,
  RetirementFundType,
} from '@/lib/fna/types';
import sampleFixture from '../../tests/fixtures/sample_fna.json';
import sampleAnalysisFixture from '../../tests/fixtures/sample_analysis.json';

const N = Number(process.env.FNA_PARITY_N ?? 2000);
const SEED = Number(process.env.FNA_PARITY_SEED ?? 20261016);
const OUT = resolve(process.env.FNA_PARITY_OUT ?? join(import.meta.dirname, '../../../fna_parity_out'));
/** The fixed "today" for most documents; the rest use seeded instants around it. */
const TODAY = Date.UTC(2026, 9, 16);
/** pipeline/tests/fixtures/sample_analysis.json was exported at this instant (recovered from its exactAge). */
const SAMPLE_EXPORTED_AT = Date.parse('2026-10-16T18:49:32.222Z');

// ------------------------------------------------------------------ seeded randomness

/** mulberry32: small, fast and good enough for test data. */
const mulberry32 = (seed: number) => () => {
  seed = (seed + 0x6d2b79f5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};
const rand = mulberry32(SEED);
const chance = (p: number) => rand() < p;
const int = (lo: number, hi: number) => lo + Math.floor(rand() * (hi - lo + 1));
const pick = <T>(xs: readonly T[]): T => xs[Math.floor(rand() * xs.length)];
/** A rand amount rounded to `step`, zero with probability `zero`. */
const money = (lo: number, hi: number, step = 50, zero = 0) => (chance(zero) ? 0 : Math.round((lo + rand() * (hi - lo)) / step) * step);
const rate = (lo: number, hi: number, dp = 4) => Number((lo + rand() * (hi - lo)).toFixed(dp));
/** Occasionally a value with cents, as typed by hand or derived. */
const cents = (x: number) => (chance(0.15) ? Math.round(x * 100 + rand() * 100) / 100 : x);
const hex = (n: number) => Array.from({ length: n }, () => Math.floor(rand() * 16).toString(16)).join('');
const uuid = () => `${hex(8)}-${hex(4)}-4${hex(3)}-${pick(['8', '9', 'a', 'b'])}${hex(3)}-${hex(12)}`;

const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const DAY = 86_400_000;
const YEAR = 365.2425 * DAY;

/** A date of birth for someone aged roughly `lo`–`hi` on `now`, including birthdays today and 29 February. */
const birthDate = (now: number, lo: number, hi: number): string => {
  const today = new Date(now);
  if (chance(0.04)) return `${today.getUTCFullYear() - int(lo, hi)}-${String(today.getUTCMonth() + 1).padStart(2, '0')}-${String(today.getUTCDate()).padStart(2, '0')}`;
  if (chance(0.02)) return `${pick([1956, 1964, 1972, 1980, 1988, 1996, 2000, 2004, 2008, 2012, 2016, 2020, 2024])}-02-29`;
  return iso(now - (lo + rand() * (hi - lo + 1)) * YEAR);
};

const mix = <T extends object>(base: T, alt: T, p: number): T => {
  const out = { ...base } as Record<string, unknown>;
  for (const [k, v] of Object.entries(alt)) if (k !== 'id' && chance(p)) out[k] = v;
  return out as T;
};

// ------------------------------------------------------------------ enumerations

const EMPLOYMENT: EmploymentType[] = ['salaried', 'self-employed', 'commission', 'contract', 'retired', 'unemployed', 'student'];
const MARITAL: MaritalStatus[] = ['single', 'married', 'life-partner', 'divorced', 'widowed'];
const REGIMES: MaritalRegime[] = ['icop', 'anc-accrual', 'anc-no-accrual', 'customary', 'foreign', 'na'];
const FUND_TYPES: RetirementFundType[] = ['pension', 'provident', 'retirement-annuity', 'preservation-pension', 'preservation-provident', 'living-annuity', 'life-annuity'];
const ASSET_TYPES = Object.keys(catalog.ASSET_TYPE) as AssetType[];
const LIABILITY_TYPES = Object.keys(catalog.LIABILITY_TYPE) as LiabilityType[];
const POLICY_TYPES = Object.keys(catalog.POLICY_TYPE) as PolicyType[];
const BENEFICIARIES = Object.keys(catalog.BENEFICIARY) as Beneficiary[];
const DEFINITIONS = Object.keys(catalog.DISABILITY_DEFINITION) as DisabilityDefinition[];
const EARMARKS: Earmark[] = ['general', 'emergency', 'retirement', 'education', 'goal'];
const CATEGORIES = catalog.EXPENSE_CATEGORIES.map((c) => c.key) as ExpenseCategory[];
const OWNERS: Owner[] = ['client', 'spouse', 'joint'];
const PEOPLE: PersonKey[] = ['client', 'spouse'];
const GENDERS: Gender[] = ['male', 'female', ''];
const TAX_YEARS = ['2026/27', '2026/27', '2026/27', '2025/26', '2025/26', '2030/31'];

// ------------------------------------------------------------------ random parts of a document

const randomPerson = (now: number, lo: number, hi: number, base: Person): Person => ({
  ...base,
  title: pick(['', 'Mr', 'Mrs', 'Ms', 'Dr']),
  firstName: pick(['', 'Thabo', 'Lerato', 'Johan', 'Aisha', ' Sipho', 'Anna-Marie', 'Pieter ']),
  surname: pick(['', 'Mokoena', 'van der Merwe', 'Naidoo', 'Dlamini ']),
  dateOfBirth: chance(0.05) ? '' : birthDate(now, lo, hi),
  gender: pick(GENDERS),
  smoker: chance(0.2),
  employmentType: pick(EMPLOYMENT),
});

const randomIncome = (): FnaDocument['income']['client'] => {
  const gross = money(4_000, 260_000, 100, 0.1);
  return {
    grossMonthly: cents(gross),
    annualBonus: chance(0.5) ? money(0, gross * 3, 500) : 0,
    otherTaxableMonthly: money(0, 40_000, 100, 0.75),
    nonTaxableMonthly: money(0, 15_000, 100, 0.85),
    medicalAidPayrollMonthly: money(0, 12_000, 10, 0.4),
    medicalSchemeMembers: chance(0.3) ? 0 : int(1, 6),
    otherPayrollDeductionsMonthly: money(0, 3_000, 10, 0.7),
    payeOverrideMonthly: chance(0.12) ? money(0, gross * 0.4, 1) : null,
    uifApplies: chance(0.9),
  };
};

const randomFund = (owner: PersonKey): FnaDocument['retirementFunds'][number] => {
  const type = pick(FUND_TYPES);
  const value = money(0, 6_000_000, 1_000, 0.05);
  return {
    ...newRetirementFund(owner),
    id: uuid(),
    type,
    provider: pick(['', 'Fund administrator']),
    name: pick(['', 'Pension fund', 'RA']),
    value,
    savingsPot: money(0, Math.min(value, 60_000), 100),
    employeeMonthly: type === 'living-annuity' || type === 'life-annuity' ? 0 : money(0, 15_000, 50, 0.3),
    employerMonthly: ['pension', 'provident'].includes(type) ? money(0, 20_000, 50, 0.3) : 0,
    viaPayroll: chance(0.6),
    beneficiariesNominated: chance(0.6),
    drawdownRate: rate(0.025, 0.175, 3),
  };
};

const randomAsset = (): Asset => {
  const type = pick(ASSET_TYPES);
  const value = money(0, type.includes('property') || type === 'primary-residence' ? 9_000_000 : 1_500_000, 1_000, 0.04);
  return {
    ...newAsset(pick(OWNERS)),
    id: uuid(),
    type,
    description: pick(['', 'Asset']),
    value: cents(value),
    baseCost: chance(0.35) ? 0 : money(0, value * 1.3, 1_000),
    monthlyContribution: money(0, 6_000, 50, 0.6),
    earmark: chance(0.6) ? 'general' : pick(EARMARKS),
    monthlyIncome: type.includes('property') || type === 'business-interest' ? money(0, 30_000, 100, 0.5) : money(0, 2_000, 50, 0.92),
    bequeathToSpouse: chance(0.7),
  };
};

const randomLiability = (assets: Asset[]): FnaDocument['liabilities'][number] => ({
  ...newLiability(pick(OWNERS)),
  id: uuid(),
  type: pick(LIABILITY_TYPES),
  lender: pick(['', 'Bank']),
  description: pick(['', 'Card', 'Loan']),
  balance: cents(money(0, 3_000_000, 500, 0.05)),
  interestRate: chance(0.05) ? 0 : rate(0.06, 0.29, 4),
  monthlyRepayment: money(0, 35_000, 50, 0.05),
  linkedAssetId: assets.length && chance(0.3) ? pick(assets).id : '',
  creditLifeCover: chance(0.25),
  settleOnDeath: chance(0.85),
  settleOnDisability: chance(0.8),
});

const randomPolicy = (lifeAssured: PersonKey): Policy => {
  const type = pick(POLICY_TYPES);
  return {
    ...newPolicy(lifeAssured),
    id: uuid(),
    type,
    insurer: pick(['', 'Insurer']),
    isGroup: chance(0.3),
    cover: type === 'income-protection' || type === 'family-income' ? money(0, 100_000, 1_000, 0.8) : money(0, 6_000_000, 10_000, 0.05),
    monthlyBenefit: type === 'income-protection' || type === 'family-income' ? money(0, 90_000, 500, 0.05) : money(0, 10_000, 500, 0.9),
    benefitTermYears: chance(0.5) ? 0 : int(5, 25),
    waitingPeriodMonths: pick([0, 1, 3, 6, 12]),
    benefitToAge: pick([0, 55, 60, 63, 65, 65, 67, 70]),
    beneficiary: pick(BENEFICIARIES),
    definition: pick(DEFINITIONS),
    accelerated: chance(0.4),
    premiumMonthly: money(0, 3_000, 5, 0.25),
    premiumEscalation: pick([0, 0.05, 0.06, 0.08]),
    coverEscalation: pick([0, 0, 0.05, 0.06, 0.1]),
  };
};

const randomDependant = (now: number): Dependant => {
  const relationship = pick(['child', 'child', 'child', 'child', 'child', 'child', 'parent', 'parent', 'sibling', 'other'] as const);
  const child = relationship === 'child';
  return {
    ...newDependant(),
    id: uuid(),
    name: pick(['', 'Neo', 'Kagiso', 'Mpho', 'Ayesha']),
    relationship,
    dateOfBirth: chance(0.05) ? '' : child ? birthDate(now, 0, 26) : birthDate(now, 10, 92),
    gender: pick(GENDERS),
    dependencyEndAge: child ? pick([18, 21, 22, 23, 23, 25, 26]) : pick([90, 95, 23]),
    monthlySupport: child ? money(0, 3_000, 100, 0.85) : money(0, 12_000, 100, 0.2),
    supportYears: child ? 0 : chance(0.15) ? rate(1, 20, 1) : int(0, 25),
    schoolType: pick(['none', 'public', 'private'] as const),
    schoolFeesAnnual: child ? money(0, 250_000, 500, 0.3) : 0,
    tertiary: child ? chance(0.75) : chance(0.1),
    tertiaryStartAge: chance(0.1) ? 18.5 : int(17, 21),
    tertiaryYears: chance(0.1) ? 3.5 : int(1, 6),
    tertiaryCostAnnual: money(0, 320_000, 1_000, 0.1),
    educationSavings: money(0, 400_000, 1_000, 0.6),
    educationSavingsMonthly: money(0, 5_000, 100, 0.6),
    specialNeeds: chance(0.05),
  };
};

const randomGoal = (now: number): OtherGoal => ({
  ...newGoal(),
  id: uuid(),
  owner: pick(OWNERS),
  name: pick(['', 'Holiday', 'New car', 'Wedding']),
  cost: money(0, 1_200_000, 1_000, 0.05),
  targetYear: new Date(now).getUTCFullYear() + int(-2, 20),
  existingSavings: money(0, 300_000, 500, 0.4),
  monthlyContribution: money(0, 10_000, 100, 0.4),
  priority: pick(['high', 'medium', 'low'] as const),
});

const randomEstate = (now: number): FnaDocument['estate']['client'] => ({
  ...newDocument().estate.client,
  hasWill: pick(['yes', 'yes', 'no', 'unsure'] as const),
  willDate: chance(0.3) ? '' : chance(0.03) ? '2016-02-30' : iso(now - rand() * 20 * YEAR),
  guardianNominated: pick(['yes', 'no', 'na'] as const),
  hasTrust: chance(0.15),
  additionalIncomeNeedMonthly: money(0, 15_000, 500, 0.8),
  additionalCapitalNeed: money(0, 2_000_000, 10_000, 0.8),
  portedAbatement: chance(0.85) ? 0 : money(0, 4_000_000, 50_000),
  cashBequests: money(0, 500_000, 5_000, 0.8),
  accrualCommencementValue: money(0, 2_000_000, 10_000, 0.3),
  funeralCost: money(15_000, 120_000, 1_000),
});

const randomRetirementGoal = (): FnaDocument['retirement']['client'] => ({
  retirementAge: chance(0.08) ? 62.5 : int(55, 70),
  planningAge: int(80, 100),
  targetMode: chance(0.7) ? 'percent' : 'amount',
  targetPercent: rate(0.4, 1, 2),
  targetMonthly: money(0, 120_000, 500),
  otherIncomeMonthly: money(0, 25_000, 500, 0.7),
});

const randomAssumptions = (): FnaDocument['assumptions'] => {
  const cpi = rate(0.02, 0.08, 3);
  return {
    ...DEFAULT_ASSUMPTIONS,
    taxYear: pick(TAX_YEARS),
    cpi,
    salaryEscalation: chance(0.1) ? cpi : rate(0.03, 0.09, 3),
    preRetirementReturn: rate(0.04, 0.13, 3),
    postRetirementReturn: chance(0.1) ? cpi : rate(0.03, 0.11, 3),
    riskCapitalReturn: chance(0.1) ? cpi : rate(0.04, 0.1, 3),
    educationInflation: rate(0.03, 0.1, 3),
    deathIncomeReplacement: rate(0.5, 1, 2),
    incomeProtectionTarget: pick([0.75, 0.8, 0.9, 1, 1]),
    disabilityAdjustments: money(0, 300_000, 10_000),
    disabilityIncludeRetirement: chance(0.8),
    severeIllnessMonths: pick([12, 18, 24, 24, 36]),
    emergencyMonths: pick([1, 3, 3, 4.5, 6]),
    executorFeeRate: pick([0.02, 0.03, 0.035, 0.035]),
    deathIncomeTerm: pick(['auto', 'auto', 'auto', 'youngest-independent', 'spouse-retirement', 'fixed'] as const),
    deathIncomeFixedYears: chance(0.1) ? 12.5 : int(5, 25),
  };
};

const randomExpenses = (): FnaDocument['expenses'] => {
  const items: Record<string, number> = {};
  for (const item of catalog.EXPENSE_ITEMS) if (chance(0.45)) items[item.key] = cents(money(0, 15_000, 10));
  if (chance(0.05)) items['not-in-catalog'] = 999;
  const custom = Array.from({ length: chance(0.6) ? 0 : int(1, 3) }, () => ({
    id: uuid(),
    category: pick(CATEGORIES),
    label: pick(['', 'Custom']),
    amount: chance(0.1) ? -money(100, 3_000, 50) : money(0, 8_000, 50),
    essential: chance(0.5),
  }));
  return { items, custom };
};

const randomAnswers = (): Record<string, number> => {
  const answers: Record<string, number> = {};
  for (const q of RISK_QUESTIONS) if (chance(0.85)) answers[q.id] = chance(0.04) ? pick([5, -1, 2.5]) : int(0, 4);
  return answers;
};

const randomAdvice = (doc: FnaDocument) => {
  doc.advice.summary = chance(0.3) ? 'Advice summary' : '';
  doc.advice.recommendations = Array.from({ length: chance(0.6) ? 0 : int(1, 3) }, () =>
    newRecommendation({
      id: uuid(),
      area: pick(['life', 'retirement', 'estate'] as const),
      isReplacement: chance(0.4),
      replacementReasons: chance(0.5) ? 'Cheaper' : '',
      replacementConsequences: chance(0.5) ? 'New waiting periods' : '',
    }),
  );
};

const randomEngagement = (doc: FnaDocument) => {
  doc.engagement.popiaConsent = chance(0.7);
  doc.engagement.disclosureLetterProvided = chance(0.7);
  doc.engagement.ficaVerified = chance(0.7);
  if (chance(0.05)) doc.engagement.meetingDate = '';
};

const randomHousehold = (doc: FnaDocument, married: boolean) => {
  const status = married ? pick(['married', 'married', 'married', 'life-partner'] as const) : pick(['single', 'divorced', 'widowed'] as const);
  doc.household = {
    ...doc.household,
    maritalStatus: status,
    maritalRegime: status === 'married' ? pick(REGIMES) : 'na',
    includeSpouse: married ? chance(0.9) : chance(0.05),
  };
};

// ------------------------------------------------------------------ scenarios

const fresh = (now: number): FnaDocument => {
  const doc = newDocument();
  doc.id = uuid();
  doc.createdAt = doc.updatedAt = new Date(now).toISOString();
  return doc;
};

/** A household built from scratch, every field random. */
const household = (now: number, married: boolean, retiree = false): FnaDocument => {
  const doc = fresh(now);
  randomHousehold(doc, married);
  doc.client = randomPerson(now, retiree ? 58 : 19, retiree ? 95 : 70, doc.client);
  doc.spouse = randomPerson(now, retiree ? 55 : 19, retiree ? 95 : 70, doc.spouse);
  if (retiree) {
    doc.client.employmentType = 'retired';
    if (chance(0.6)) doc.spouse.employmentType = 'retired';
  }
  doc.income = { client: randomIncome(), spouse: chance(0.2) ? { ...randomIncome(), grossMonthly: 0, annualBonus: 0 } : randomIncome() };
  if (retiree) doc.income.client = { ...doc.income.client, grossMonthly: 0, annualBonus: 0, otherTaxableMonthly: money(5_000, 80_000, 100) };
  doc.dependants = Array.from({ length: retiree ? int(0, 2) : int(0, 5) }, () => randomDependant(now));
  doc.expenses = randomExpenses();
  doc.retirementFunds = Array.from({ length: int(0, 5) }, () => randomFund(pick(PEOPLE)));
  if (retiree) doc.retirementFunds.push({ ...randomFund('client'), type: pick(['living-annuity', 'life-annuity'] as const) });
  doc.assets = Array.from({ length: int(0, 9) }, randomAsset);
  doc.liabilities = Array.from({ length: int(0, 5) }, () => randomLiability(doc.assets));
  doc.policies = Array.from({ length: int(0, 8) }, () => randomPolicy(pick(PEOPLE)));
  doc.retirement = { client: randomRetirementGoal(), spouse: randomRetirementGoal() };
  if (retiree) doc.retirement.client = { ...doc.retirement.client, targetMode: 'amount', targetMonthly: money(8_000, 90_000, 500) };
  doc.goals = Array.from({ length: int(0, 4) }, () => randomGoal(now));
  doc.estate = { client: randomEstate(now), spouse: randomEstate(now) };
  doc.riskProfile = { answers: randomAnswers(), notes: '' };
  doc.assumptions = randomAssumptions();
  randomEngagement(doc);
  randomAdvice(doc);
  return doc;
};

/** The sample household with each field replaced by a random value with probability `p`, and rows added or dropped. */
const perturbedSample = (now: number, p: number): FnaDocument => {
  const doc = sampleDocument();
  doc.id = uuid();
  for (const x of [...doc.dependants, ...doc.retirementFunds, ...doc.assets, ...doc.liabilities, ...doc.policies, ...doc.goals]) x.id = uuid();
  const keep = <T>(rows: T[]) => rows.filter(() => !chance(p / 2));
  doc.client = mix(doc.client, randomPerson(now, 19, 75, doc.client), p);
  doc.spouse = mix(doc.spouse, randomPerson(now, 19, 75, doc.spouse), p);
  if (chance(p)) randomHousehold(doc, chance(0.7));
  doc.income = { client: mix(doc.income.client, randomIncome(), p), spouse: mix(doc.income.spouse, randomIncome(), p) };
  doc.dependants = keep(doc.dependants).map((d) => mix(d, randomDependant(now), p));
  if (chance(p)) doc.dependants.push(randomDependant(now));
  if (chance(p)) doc.expenses = randomExpenses();
  else for (const k of Object.keys(doc.expenses.items)) if (chance(p)) doc.expenses.items[k] = money(0, 12_000, 10);
  doc.retirementFunds = keep(doc.retirementFunds).map((f) => mix(f, randomFund(f.owner), p));
  if (chance(p)) doc.retirementFunds.push(randomFund(pick(PEOPLE)));
  doc.assets = keep(doc.assets).map((a) => mix(a, randomAsset(), p));
  if (chance(p)) doc.assets.push(randomAsset());
  doc.liabilities = keep(doc.liabilities).map((l) => mix(l, randomLiability(doc.assets), p));
  if (chance(p)) doc.liabilities.push(randomLiability(doc.assets));
  doc.policies = keep(doc.policies).map((x) => mix(x, randomPolicy(x.lifeAssured), p));
  if (chance(p)) doc.policies.push(randomPolicy(pick(PEOPLE)));
  doc.retirement = { client: mix(doc.retirement.client, randomRetirementGoal(), p), spouse: mix(doc.retirement.spouse, randomRetirementGoal(), p) };
  doc.goals = keep(doc.goals).map((g) => mix(g, randomGoal(now), p));
  doc.estate = { client: mix(doc.estate.client, randomEstate(now), p), spouse: mix(doc.estate.spouse, randomEstate(now), p) };
  if (chance(p)) doc.riskProfile = { answers: randomAnswers(), notes: '' };
  doc.assumptions = mix(doc.assumptions, randomAssumptions(), p);
  if (chance(p)) randomEngagement(doc);
  if (chance(p)) randomAdvice(doc);
  return doc;
};

/** A new document with only a few fields filled in. */
const sparse = (now: number): FnaDocument => {
  const doc = fresh(now);
  if (chance(0.7)) doc.client.dateOfBirth = birthDate(now, 18, 80);
  if (chance(0.6)) doc.income.client = { ...doc.income.client, grossMonthly: money(3_000, 120_000, 100) };
  if (chance(0.4)) doc.assets = [randomAsset()];
  if (chance(0.4)) doc.policies = [randomPolicy('client')];
  if (chance(0.3)) doc.dependants = [randomDependant(now)];
  if (chance(0.3)) doc.assumptions.taxYear = '2025/26';
  return doc;
};

/** Boundaries the engine has to handle: zero rates, returns equal to inflation, extreme ages and amounts. */
const edge = (now: number): FnaDocument => {
  const doc = household(now, chance(0.5), chance(0.3));
  const a = doc.assumptions;
  switch (int(0, 9)) {
    case 0:
      a.preRetirementReturn = a.salaryEscalation; // escalating contributions growing at the return
      a.postRetirementReturn = a.cpi; // real return of zero
      a.riskCapitalReturn = a.cpi;
      break;
    case 1:
      a.cpi = 0;
      a.salaryEscalation = 0;
      a.preRetirementReturn = 0;
      a.postRetirementReturn = 0;
      a.riskCapitalReturn = 0;
      a.educationInflation = 0;
      break;
    case 2:
      a.postRetirementReturn = 0.01; // negative real return: capital runs out
      doc.retirement.client = { ...doc.retirement.client, targetMode: 'amount', targetMonthly: 250_000 };
      break;
    case 3:
      doc.client.dateOfBirth = birthDate(now, 99, 104);
      doc.retirement.client.planningAge = 90;
      break;
    case 4:
      doc.client.dateOfBirth = iso(now + 400 * DAY); // born in the future
      doc.dependants.push({ ...randomDependant(now), relationship: 'child', dateOfBirth: iso(now + 30 * DAY) });
      break;
    case 5:
      doc.income.client = { ...doc.income.client, grossMonthly: 5_000_000, annualBonus: 20_000_000 };
      doc.assets.push({ ...randomAsset(), type: 'shares', value: 300_000_000, baseCost: 1_000_000, bequeathToSpouse: false });
      break;
    case 6:
      doc.retirement.client = { ...doc.retirement.client, retirementAge: 45, planningAge: 50 };
      doc.client.dateOfBirth = birthDate(now, 44, 44);
      break;
    case 7:
      doc.liabilities.push({ ...randomLiability([]), type: 'credit-card', interestRate: 0.29, balance: 80_000, settleOnDeath: false });
      doc.liabilities.push({ ...randomLiability([]), type: 'home-loan', creditLifeCover: true });
      break;
    case 8:
      doc.household = { ...doc.household, maritalStatus: 'married', maritalRegime: pick(['icop', 'anc-accrual'] as const), includeSpouse: chance(0.8) };
      doc.estate.client.accrualCommencementValue = 0;
      doc.estate.spouse.accrualCommencementValue = 5_000_000;
      break;
    default:
      doc.dependants = [
        { ...randomDependant(now), relationship: 'parent', monthlySupport: 6_000, supportYears: 12.5 },
        { ...randomDependant(now), relationship: 'sibling', monthlySupport: 2_500, supportYears: 7 },
        { ...randomDependant(now), relationship: 'child', dateOfBirth: birthDate(now, 20, 20), tertiaryStartAge: 18, tertiaryYears: 4 },
      ];
  }
  return doc;
};

const tagsFor = (doc: FnaDocument, scenario: string): string[] => {
  const tags = [scenario, `tax:${doc.assumptions.taxYear}`];
  const hh = doc.household;
  if (hh.includeSpouse && ['married', 'life-partner'].includes(hh.maritalStatus)) tags.push('couple');
  else tags.push('single');
  if (hh.maritalStatus === 'married') tags.push(`regime:${hh.maritalRegime}`);
  if (doc.client.employmentType === 'retired') tags.push('retiree');
  for (const d of doc.dependants) tags.push(`dependant:${d.relationship}`);
  for (const f of doc.retirementFunds) tags.push(`fund:${f.type}`);
  for (const x of doc.assets) tags.push(`asset:${x.type}`);
  for (const l of doc.liabilities) tags.push(`liability:${l.type}`);
  for (const x of doc.policies) tags.push(`policy:${x.type}`);
  if (doc.income.client.payeOverrideMonthly !== null) tags.push('paye-override');
  return [...new Set(tags)].sort();
};

const build = (i: number, now: number): { doc: FnaDocument; scenario: string } => {
  if (i === 0) return { doc: sampleDocument(), scenario: 'sample' };
  if (i === 1) return { doc: fresh(now), scenario: 'empty' };
  if (i === 2) {
    const doc = fresh(now);
    doc.assumptions.taxYear = '2025/26';
    return { doc, scenario: 'empty' };
  }
  const roll = rand();
  if (roll < 0.33) return { doc: perturbedSample(now, pick([0.05, 0.15, 0.3, 0.6])), scenario: 'sample-perturbed' };
  if (roll < 0.66) return { doc: household(now, chance(0.6)), scenario: 'household' };
  if (roll < 0.78) return { doc: household(now, chance(0.5), true), scenario: 'retiree' };
  if (roll < 0.88) return { doc: sparse(now), scenario: 'sparse' };
  return { doc: edge(now), scenario: 'edge' };
};

// ------------------------------------------------------------------ run

beforeAll(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  // Ids from newDocument(), newRecommendation() and friends: seeded, so the output is reproducible.
  vi.spyOn(crypto, 'randomUUID').mockImplementation(() => uuid() as `${string}-${string}-${string}-${string}-${string}`);
});

afterAll(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it('runs in UTC', () => {
  expect(new Date(2026, 0, 1).getTimezoneOffset()).toBe(0);
});

it('reproduces the exported sample analysis (pipeline/tests/fixtures/sample_analysis.json)', () => {
  const a = analyse(sampleFixture as unknown as FnaDocument, new Date(SAMPLE_EXPORTED_AT));
  expect(JSON.parse(JSON.stringify(a))).toEqual(sampleAnalysisFixture);
});

it(`writes ${N} documents and their TypeScript analyses`, async () => {
  mkdirSync(OUT, { recursive: true });
  writeFileSync(
    join(OUT, 'meta.json'),
    JSON.stringify({
      seed: SEED,
      n: N,
      node: process.version,
      icu: process.versions.icu,
      catalogs: {
        EXPENSE_CATEGORIES: catalog.EXPENSE_CATEGORIES,
        EXPENSE_ITEMS: catalog.EXPENSE_ITEMS,
        MARITAL_STATUS: catalog.MARITAL_STATUS,
        MARITAL_REGIME: catalog.MARITAL_REGIME,
        EMPLOYMENT_TYPE: catalog.EMPLOYMENT_TYPE,
        RELATIONSHIP: catalog.RELATIONSHIP,
        RETIREMENT_FUND_TYPE: catalog.RETIREMENT_FUND_TYPE,
        PRE_RETIREMENT_FUNDS: catalog.PRE_RETIREMENT_FUNDS,
        ASSET_TYPE: catalog.ASSET_TYPE,
        ASSET_BEHAVIOUR: catalog.ASSET_BEHAVIOUR,
        LIABILITY_TYPE: catalog.LIABILITY_TYPE,
        UNSECURED_DEBT: catalog.UNSECURED_DEBT,
        POLICY_TYPE: catalog.POLICY_TYPE,
        BENEFICIARY: catalog.BENEFICIARY,
        DISABILITY_DEFINITION: catalog.DISABILITY_DEFINITION,
        NEED_AREA: catalog.NEED_AREA,
        ACTION: catalog.ACTION,
        REPORT_STYLE: catalog.REPORT_STYLE,
        SCOPE_AREA: catalog.SCOPE_AREA,
        PROVINCES: catalog.PROVINCES,
        TITLES: catalog.TITLES,
        EDUCATION_LEVELS: catalog.EDUCATION_LEVELS,
      },
      risk: { RISK_QUESTIONS, RISK_CATEGORIES },
      defaultAssumptions: DEFAULT_ASSUMPTIONS,
      taxTables: TAX_TABLES,
      taxTableFiles: TAX_TABLE_FILES,
    }),
  );
  const out = createWriteStream(join(OUT, 'parity.jsonl'));
  const scenarios: Record<string, number> = {};
  for (let i = 0; i < N; i++) {
    // Most documents are analysed on one fixed day; a fifth at seeded instants within ±18 months of it.
    const now = i < 3 || chance(0.8) ? TODAY : TODAY + Math.round((rand() * 3 - 1.5) * YEAR);
    vi.setSystemTime(now);
    const { doc, scenario } = build(i, now);
    const analysis = analyse(doc, new Date(now));
    const recommendations = draftRecommendations(doc, analysis, new Date(now));
    scenarios[scenario] = (scenarios[scenario] ?? 0) + 1;
    const line = JSON.stringify({ i, now: new Date(now).toISOString(), tags: tagsFor(doc, scenario), doc, analysis, recommendations });
    if (!out.write(`${line}\n`)) await new Promise((r) => out.once('drain', r));
  }
  await new Promise<void>((r, j) => out.end((e?: Error | null) => (e ? j(e) : r())));
  writeFileSync(join(OUT, 'summary.json'), JSON.stringify({ n: N, seed: SEED, scenarios }, null, 2));
  expect(Object.values(scenarios).reduce((s, x) => s + x, 0)).toBe(N);
});
