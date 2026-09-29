/**
 * The FNA engine. Pure functions only: document in, analysis out.
 *
 * Every figure in the report is produced here so the UI, the report and the
 * tests all share one source of truth.
 */
import { ASSET_BEHAVIOUR, EXPENSE_CATEGORIES, EXPENSE_ITEMS, PRE_RETIREMENT_FUNDS, UNSECURED_DEBT } from './catalog';
import { BOND_CANCELLATION, ESTATE_SUNDRIES, conveyancingEstimate, mastersFee } from './estate';
import {
  fv,
  fvEscalatingContributions,
  monthlyRate,
  pv,
  pvGrowingMonthlyIncome,
  requiredEscalatingSaving,
  sum,
} from './finance';
import { ageOn, exactAge } from './idNumber';
import { scoreRiskProfile, type RiskProfileResult } from './riskProfile';
import {
  estateDuty,
  getTaxTable,
  incrementalTax,
  personTax,
  retirementLumpSumTax,
  type PersonTaxResult,
  type TaxTable,
} from './tax';
import type {
  Asset,
  Dependant,
  ExpenseCategory,
  FnaDocument,
  Liability,
  NeedArea,
  Owner,
  Person,
  PersonKey,
  Policy,
  RetirementFund,
} from './types';

export const FALLBACK_AGE = 40;

/* ------------------------------------------------------------------ */
/* Result types                                                        */
/* ------------------------------------------------------------------ */

export interface Line {
  label: string;
  amount: number;
  note?: string;
}

export interface PersonIncome {
  key: PersonKey;
  name: string;
  age: number;
  /** Age at the end of the tax year (used for rebates). */
  taxAge: number;
  ageKnown: boolean;
  exactAge: number;
  tax: PersonTaxResult;
  /** Salary / business income plus bonus, per month (excludes passive income). */
  earnedGrossMonthly: number;
  /** After-tax earned income per month; 0 when not working. */
  earnedAfterTaxMonthly: number;
  /** Economically active and below retirement age. */
  working: boolean;
  /** Regular (non-bonus) monthly gross incl. other taxable income. */
  grossMonthly: number;
  /** Gross monthly equivalent incl. bonus (annual / 12). */
  grossMonthlyEquivalent: number;
  payeMonthly: number;
  uifMonthly: number;
  retirementPayrollMonthly: number;
  retirementEmployerMonthly: number;
  retirementOwnMonthly: number;
  medicalPayrollMonthly: number;
  otherDeductionsMonthly: number;
  nonTaxableMonthly: number;
  /** Regular monthly take-home pay (excludes bonus). */
  takeHomeMonthly: number;
  /** After-tax income incl. bonus, per month — the basis for income replacement. */
  afterTaxMonthly: number;
  bonusNet: number;
}

export interface Cashflow {
  takeHome: number;
  livingExpenses: number;
  essentialExpenses: number;
  byCategory: { key: ExpenseCategory; label: string; amount: number }[];
  debtRepayments: number;
  riskPremiums: number;
  savingsContributions: number;
  totalOutgoings: number;
  surplus: number;
  bonusNet: number;
}

export interface Ratios {
  debtToIncome: number;
  unsecuredDebtToIncome: number;
  housingToIncome: number;
  savingsRate: number;
  premiumsToIncome: number;
  emergencyMonths: number;
}

export interface NetWorth {
  assets: number;
  retirement: number;
  liabilities: number;
  netWorth: number;
  liquidAssets: number;
  byGroup: { key: string; label: string; amount: number }[];
}

export type Status = 'covered' | 'partial' | 'shortfall' | 'na';

export interface NeedResult {
  area: NeedArea;
  person?: PersonKey;
  title: string;
  need: number;
  provision: number;
  shortfall: number;
  /** Funding ratio 0..1 (provision / need). */
  funded: number;
  status: Status;
  monthly?: boolean;
  needLines: Line[];
  provisionLines: Line[];
  notes: string[];
}

export interface EstateResult {
  person: PersonKey;
  grossEstate: number;
  deemedProperty: number;
  liabilities: number;
  executorFees: number;
  mastersFees: number;
  conveyancing: number;
  bondCancellation: number;
  sundries: number;
  funeral: number;
  cgt: number;
  capitalGains: number;
  accrualPayable: number;
  accrualReceivable: number;
  netEstate: number;
  spouseDeduction: number;
  abatement: number;
  dutiable: number;
  estateDuty: number;
  totalCosts: number;
  cashRequired: number;
  cashAvailable: number;
  liquidityShortfall: number;
  lines: Line[];
  notes: string[];
}

export interface RetirementYear {
  age: number;
  year: number;
  capital: number;
  capitalReal: number;
  contribution: number;
  withdrawal: number;
  phase: 'accumulation' | 'retirement';
}

export interface RetirementResult {
  person: PersonKey;
  retired: boolean;
  currentAge: number;
  retirementAge: number;
  planningAge: number;
  yearsToRetirement: number;
  yearsInRetirement: number;
  currentCapital: number;
  monthlyContributions: number;
  projectedCapital: number;
  projectedCapitalReal: number;
  targetMonthlyToday: number;
  otherIncomeToday: number;
  targetMonthlyAtRetirement: number;
  requiredCapital: number;
  requiredCapitalReal: number;
  shortfall: number;
  shortfallReal: number;
  additionalMonthly: number;
  sustainableMonthlyToday: number;
  replacementRatio: number;
  targetReplacementRatio: number;
  initialDrawdown: number;
  depletionAge: number | null;
  funded: number;
  status: Status;
  timeline: RetirementYear[];
  notes: string[];
}

export interface EducationResult {
  dependantId: string;
  name: string;
  age: number;
  yearsToTertiary: number;
  tertiaryPV: number;
  schoolPV: number;
  capitalAtStart: number;
  projectedSavings: number;
  shortfallAtStart: number;
  shortfallToday: number;
  monthlyRequired: number;
  /** Shortfall that must be funded now (study already under way). */
  lumpSumRequired: number;
  funded: number;
  status: Status;
  schedule: { year: number; age: number; cost: number }[];
}

export interface GoalResult {
  id: string;
  name: string;
  years: number;
  futureCost: number;
  projected: number;
  shortfall: number;
  monthlyRequired: number;
  funded: number;
  status: Status;
}

export interface Finding {
  severity: 'critical' | 'warning' | 'info' | 'positive';
  area: NeedArea | 'data' | 'compliance' | 'cashflow';
  person?: PersonKey;
  title: string;
  detail: string;
}

export interface PersonAnalysis {
  key: PersonKey;
  name: string;
  income: PersonIncome;
  death: NeedResult;
  disability: NeedResult;
  incomeProtection: NeedResult;
  severeIllness: NeedResult;
  funeral: NeedResult;
  estate: EstateResult;
  retirement: RetirementResult;
  deathIncomeYears: number;
  deathIncomeMonthly: number;
}

export interface Analysis {
  table: TaxTable;
  people: PersonKey[];
  persons: Record<PersonKey, PersonAnalysis | undefined>;
  incomes: PersonIncome[];
  cashflow: Cashflow;
  ratios: Ratios;
  netWorth: NetWorth;
  emergency: NeedResult;
  education: EducationResult[];
  educationTotal: NeedResult;
  goals: GoalResult[];
  risk: RiskProfileResult;
  findings: Finding[];
  completeness: { section: string; complete: boolean; detail: string }[];
  completenessScore: number;
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

export const personName = (p: Person, fallback: string): string => {
  const name = `${p.firstName} ${p.surname}`.trim();
  return name || fallback;
};

/** Share of an owned item attributable to `key` (joint = 50%). */
export const shareOf = (owner: Owner, key: PersonKey, icop: boolean): number => {
  if (icop) return 0.5;
  if (owner === 'joint') return 0.5;
  return owner === key ? 1 : 0;
};

const statusFor = (funded: number, need: number): Status => {
  if (need <= 0.5) return 'na';
  if (funded >= 0.95) return 'covered';
  if (funded >= 0.6) return 'partial';
  return 'shortfall';
};

const makeNeed = (
  area: NeedArea,
  title: string,
  needLines: Line[],
  provisionLines: Line[],
  notes: string[] = [],
  person?: PersonKey,
  monthly = false,
): NeedResult => {
  const need = Math.max(0, sum(needLines, (l) => l.amount));
  const provision = Math.max(0, sum(provisionLines, (l) => l.amount));
  const funded = need > 0 ? Math.min(1, provision / need) : 1;
  return {
    area,
    person,
    title,
    need,
    provision,
    shortfall: Math.max(0, need - provision),
    funded,
    status: statusFor(funded, need),
    monthly,
    needLines: needLines.filter((l) => Math.abs(l.amount) >= 0.5),
    provisionLines: provisionLines.filter((l) => Math.abs(l.amount) >= 0.5),
    notes,
  };
};

const isChildDependent = (d: Dependant, age: number) => d.relationship === 'child' && age < d.dependencyEndAge;

/* ------------------------------------------------------------------ */
/* Income & tax                                                        */
/* ------------------------------------------------------------------ */

const fundsFor = (doc: FnaDocument, key: PersonKey) =>
  doc.retirementFunds.filter((f) => f.owner === key && PRE_RETIREMENT_FUNDS.includes(f.type));

export const computeIncome = (doc: FnaDocument, key: PersonKey, table: TaxTable, now: Date): PersonIncome => {
  const person = doc[key];
  const inc = doc.income[key];
  const knownAge = ageOn(person.dateOfBirth, now);
  const age = knownAge ?? FALLBACK_AGE;
  // Rebates depend on age on the last day of the year of assessment.
  const taxAge = ageOn(person.dateOfBirth, new Date(`${table.endDate}T00:00:00`)) ?? age;
  const funds = fundsFor(doc, key);
  const retirementPayroll = sum(funds.filter((f) => f.viaPayroll), (f) => f.employeeMonthly);
  const retirementOwn = sum(funds.filter((f) => !f.viaPayroll), (f) => f.employeeMonthly);
  const retirementEmployer = sum(funds, (f) => f.employerMonthly);
  const notEmployed = ['self-employed', 'retired', 'unemployed', 'student'].includes(person.employmentType);

  const baseInput = {
    age: taxAge,
    grossMonthly: inc.grossMonthly,
    otherTaxableMonthly: inc.otherTaxableMonthly,
    retirementContributionsAnnual: (retirementPayroll + retirementOwn + retirementEmployer) * 12,
    employerRetirementAnnual: retirementEmployer * 12,
    medicalSchemeMembers: inc.medicalSchemeMembers,
    uifApplies: inc.uifApplies && !notEmployed,
  };
  // Assessed tax (all deductions) and the payroll view (debit-order RA relief only arrives on assessment).
  const tax = personTax({ ...baseInput, annualBonus: inc.annualBonus }, table);
  const taxExBonus = personTax({ ...baseInput, annualBonus: 0 }, table);
  const payroll = personTax(
    { ...baseInput, annualBonus: 0, retirementContributionsAnnual: (retirementPayroll + retirementEmployer) * 12 },
    table,
  );
  const bonusTax = Math.max(0, tax.annualTax - taxExBonus.annualTax);

  const payeMonthly = inc.payeOverrideMonthly ?? payroll.monthlyPaye;
  const grossMonthly = inc.grossMonthly + inc.otherTaxableMonthly;
  const takeHome =
    grossMonthly +
    inc.nonTaxableMonthly -
    payeMonthly -
    tax.monthlyUif -
    retirementPayroll -
    inc.medicalAidPayrollMonthly -
    inc.otherPayrollDeductionsMonthly;
  const bonusNet = Math.max(0, inc.annualBonus - bonusTax);
  const annualTax = inc.payeOverrideMonthly !== null ? inc.payeOverrideMonthly * 12 + bonusTax : tax.annualTax;
  const afterTaxMonthly = (tax.grossAnnual + inc.nonTaxableMonthly * 12 - annualTax - tax.monthlyUif * 12) / 12;

  // Earned income (salary/business income and bonus) is what income protection and illness cover insure.
  const earnedAnnual = inc.grossMonthly * 12 + inc.annualBonus;
  const avgTaxRate = tax.grossAnnual > 0 ? annualTax / tax.grossAnnual : 0;
  const earnedAfterTaxMonthly = Math.max(0, (earnedAnnual * (1 - avgTaxRate) - tax.monthlyUif * 12) / 12);
  const working = !['retired', 'unemployed', 'student'].includes(person.employmentType) && age < doc.retirement[key].retirementAge;

  return {
    key,
    name: personName(person, key === 'client' ? 'Client' : 'Spouse'),
    age,
    taxAge,
    ageKnown: knownAge !== null,
    exactAge: exactAge(person.dateOfBirth, now) ?? age + 0.5,
    tax,
    grossMonthly,
    grossMonthlyEquivalent: grossMonthly + inc.annualBonus / 12,
    earnedGrossMonthly: earnedAnnual / 12,
    earnedAfterTaxMonthly: working ? earnedAfterTaxMonthly : 0,
    working,
    payeMonthly,
    uifMonthly: tax.monthlyUif,
    retirementPayrollMonthly: retirementPayroll,
    retirementEmployerMonthly: retirementEmployer,
    retirementOwnMonthly: retirementOwn,
    medicalPayrollMonthly: inc.medicalAidPayrollMonthly,
    otherDeductionsMonthly: inc.otherPayrollDeductionsMonthly,
    nonTaxableMonthly: inc.nonTaxableMonthly,
    takeHomeMonthly: Math.max(0, takeHome),
    afterTaxMonthly: Math.max(0, afterTaxMonthly),
    bonusNet,
  };
};

/* ------------------------------------------------------------------ */
/* Household: cash flow, ratios and net worth                          */
/* ------------------------------------------------------------------ */

export const livingExpenses = (doc: FnaDocument) => {
  const byCategory = new Map<ExpenseCategory, number>();
  let total = 0;
  let essential = 0;
  for (const item of EXPENSE_ITEMS) {
    const amount = doc.expenses.items[item.key] ?? 0;
    if (!amount) continue;
    total += amount;
    if (item.essential) essential += amount;
    byCategory.set(item.category, (byCategory.get(item.category) ?? 0) + amount);
  }
  for (const c of doc.expenses.custom) {
    if (!c.amount) continue;
    total += c.amount;
    if (c.essential) essential += c.amount;
    byCategory.set(c.category, (byCategory.get(c.category) ?? 0) + c.amount);
  }
  return {
    total,
    essential,
    byCategory: EXPENSE_CATEGORIES.map((c) => ({ key: c.key, label: c.label, amount: byCategory.get(c.key) ?? 0 })),
  };
};

const activePolicies = (doc: FnaDocument, people: PersonKey[]) => doc.policies.filter((p) => people.includes(p.lifeAssured));

const computeCashflow = (doc: FnaDocument, incomes: PersonIncome[], people: PersonKey[]): Cashflow => {
  const exp = livingExpenses(doc);
  const takeHome = sum(incomes, (i) => i.takeHomeMonthly);
  const debtRepayments = sum(doc.liabilities, (l) => l.monthlyRepayment);
  const riskPremiums = sum(
    activePolicies(doc, people).filter((p) => !p.isGroup),
    (p) => p.premiumMonthly,
  );
  const savingsContributions =
    sum(doc.assets, (a) => a.monthlyContribution) +
    sum(incomes, (i) => i.retirementOwnMonthly) +
    sum(doc.dependants, (d) => d.educationSavingsMonthly);
  const totalOutgoings = exp.total + debtRepayments + riskPremiums + savingsContributions;
  return {
    takeHome,
    livingExpenses: exp.total,
    essentialExpenses: exp.essential,
    byCategory: exp.byCategory,
    debtRepayments,
    riskPremiums,
    savingsContributions,
    totalOutgoings,
    surplus: takeHome - totalOutgoings,
    bonusNet: sum(incomes, (i) => i.bonusNet),
  };
};

const GROUP_LABELS: Record<string, string> = {
  property: 'Property',
  lifestyle: 'Vehicles & personal',
  liquid: 'Cash & money market',
  investment: 'Investments',
  business: 'Business interests',
  retirement: 'Retirement funds',
};

const computeNetWorth = (doc: FnaDocument): NetWorth => {
  const groups = new Map<string, number>();
  for (const a of doc.assets) {
    const g = ASSET_BEHAVIOUR[a.type].group;
    groups.set(g, (groups.get(g) ?? 0) + a.value);
  }
  // Guaranteed (life) annuities have no capital value to the household once purchased.
  const retirement = sum(doc.retirementFunds.filter((f) => f.type !== 'life-annuity'), (f) => f.value);
  if (retirement) groups.set('retirement', retirement);
  const assets = sum(doc.assets, (a) => a.value);
  const liabilities = sum(doc.liabilities, (l) => l.balance);
  return {
    assets,
    retirement,
    liabilities,
    netWorth: assets + retirement - liabilities,
    liquidAssets: sum(doc.assets.filter((a) => ASSET_BEHAVIOUR[a.type].liquid), (a) => a.value),
    byGroup: ['property', 'investment', 'retirement', 'liquid', 'business', 'lifestyle']
      .map((key) => ({ key, label: GROUP_LABELS[key], amount: groups.get(key) ?? 0 }))
      .filter((g) => g.amount > 0),
  };
};

/* ------------------------------------------------------------------ */
/* Education                                                           */
/* ------------------------------------------------------------------ */

export const computeEducation = (doc: FnaDocument, now: Date): EducationResult[] => {
  const a = doc.assumptions;
  const year = now.getFullYear();
  // Assets earmarked for education are shared across children in proportion to each child's need.
  const pool = doc.assets.filter((x) => x.earmark === 'education');
  const poolValue = sum(pool, (x) => x.value);
  const poolMonthly = sum(pool, (x) => x.monthlyContribution);

  const base = doc.dependants
    .filter((d) => d.relationship === 'child')
    .map((d) => {
      const age = ageOn(d.dateOfBirth, now) ?? 0;
      const schedule: EducationResult['schedule'] = [];
      // School fees: Grade R (the year the child turns 6) to matric (turns 18), paid in advance.
      let schoolPV = 0;
      if (d.schoolType !== 'none' && d.schoolFeesAnnual > 0) {
        for (let t = Math.max(0, 6 - age); age + t <= 18; t++) {
          const cost = d.schoolFeesAnnual * Math.pow(1 + a.educationInflation, t);
          schoolPV += pv(cost, a.riskCapitalReturn, t);
        }
      }
      let tertiaryPV = 0;
      let capitalAtStart = 0;
      const yearsToTertiary = Math.max(0, d.tertiaryStartAge - age);
      if (d.tertiary && d.tertiaryCostAnnual > 0) {
        const alreadyStudied = Math.max(0, age - d.tertiaryStartAge);
        const remaining = Math.max(0, d.tertiaryYears - alreadyStudied);
        for (let k = 0; k < remaining; k++) {
          const t = yearsToTertiary + k;
          const cost = d.tertiaryCostAnnual * Math.pow(1 + a.educationInflation, t);
          schedule.push({ year: year + t, age: age + t, cost });
          tertiaryPV += pv(cost, a.riskCapitalReturn, t);
          capitalAtStart += pv(cost, a.riskCapitalReturn, k);
        }
      }
      return { d, age, schedule, schoolPV, tertiaryPV, capitalAtStart, yearsToTertiary };
    });

  const totalPV = sum(base, (b) => b.tertiaryPV);
  return base.map(({ d, age, schedule, schoolPV, tertiaryPV, capitalAtStart, yearsToTertiary }) => {
    const share = totalPV > 0 ? tertiaryPV / totalPV : 0;
    const savings = d.educationSavings + poolValue * share;
    const monthly = d.educationSavingsMonthly + poolMonthly * share;
    const projectedSavings = fv(savings, a.riskCapitalReturn, yearsToTertiary) + fvEscalatingContributions(monthly, yearsToTertiary, a.riskCapitalReturn, a.cpi);
    const shortfallAtStart = Math.max(0, capitalAtStart - projectedSavings);
    const shortfallToday = pv(shortfallAtStart, a.riskCapitalReturn, yearsToTertiary);
    const canSave = yearsToTertiary >= 1;
    const monthlyRequired = shortfallAtStart > 0 && canSave ? requiredEscalatingSaving(shortfallAtStart, yearsToTertiary, a.riskCapitalReturn, a.cpi) : 0;
    const funded = capitalAtStart > 0 ? Math.min(1, projectedSavings / capitalAtStart) : 1;
    return {
      dependantId: d.id,
      name: d.name || 'Child',
      age,
      yearsToTertiary,
      tertiaryPV,
      schoolPV,
      capitalAtStart,
      projectedSavings,
      shortfallAtStart,
      shortfallToday,
      monthlyRequired,
      lumpSumRequired: shortfallAtStart > 0 && !canSave ? shortfallAtStart : 0,
      funded,
      status: statusFor(funded, capitalAtStart),
      schedule,
    };
  });
};

/* ------------------------------------------------------------------ */
/* Estate                                                              */
/* ------------------------------------------------------------------ */

const lifePoliciesOn = (doc: FnaDocument, key: PersonKey) =>
  doc.policies.filter((p) => p.lifeAssured === key && p.type === 'life');

/**
 * Net value (excl. retirement funds and policies) owned by `key` — used for accrual.
 * For the deceased, debts settled by credit life on death are excluded.
 */
const ownNetEstate = (doc: FnaDocument, key: PersonKey, deceased: boolean): number =>
  sum(doc.assets, (a) => a.value * shareOf(a.owner, key, false)) -
  sum(
    doc.liabilities.filter((l) => !(deceased && l.creditLifeCover)),
    (l) => l.balance * shareOf(l.owner, key, false),
  );

export const computeEstate = (
  doc: FnaDocument,
  key: PersonKey,
  income: PersonIncome,
  table: TaxTable,
  hasSpouse: boolean,
): EstateResult => {
  const a = doc.assumptions;
  const profile = doc.estate[key];
  const other: PersonKey = key === 'client' ? 'spouse' : 'client';
  const icop = hasSpouse && doc.household.maritalStatus === 'married' && doc.household.maritalRegime === 'icop';
  const notes: string[] = [];

  const assets = doc.assets.map((asset) => ({ asset, share: shareOf(asset.owner, key, icop) })).filter((x) => x.share > 0);
  const liabilities = doc.liabilities.map((l) => ({ l, share: shareOf(l.owner, key, icop) })).filter((x) => x.share > 0);
  const policies = lifePoliciesOn(doc, key);

  const assetValue = sum(assets, (x) => x.asset.value * x.share);
  const policiesToEstate = sum(policies.filter((p) => p.beneficiary === 'estate'), (p) => p.cover);
  const deemedPolicies = policies.filter((p) => p.beneficiary !== 'estate' && p.beneficiary !== 'business');
  const deemedProperty = sum(deemedPolicies, (p) => p.cover);

  // Accrual claim (Matrimonial Property Act s3–s4) for ANC with accrual.
  let accrualPayable = 0;
  let accrualReceivable = 0;
  if (hasSpouse && doc.household.maritalStatus === 'married' && doc.household.maritalRegime === 'anc-accrual') {
    const mine = Math.max(0, ownNetEstate(doc, key, true) - profile.accrualCommencementValue);
    const theirs = Math.max(0, ownNetEstate(doc, other, false) - doc.estate[other].accrualCommencementValue);
    if (mine > theirs) accrualPayable = (mine - theirs) / 2;
    else accrualReceivable = (theirs - mine) / 2;
    notes.push('Accrual claim estimated from current net asset values less commencement values in the antenuptial contract.');
  }
  if (icop) {
    notes.push(
      "Married in community of property: half of the joint estate is attributed to the deceased, but the executor administers — and charges fees on — the whole joint estate.",
    );
  }

  const grossEstate = assetValue + policiesToEstate + accrualReceivable;
  const unsecured = liabilities.filter((x) => !x.l.creditLifeCover);
  // All debts reduce the dutiable estate; only those to be settled need cash (others are taken over by heirs).
  const debtsAll = sum(unsecured, (x) => x.l.balance * x.share);
  const debts = sum(unsecured.filter((x) => x.l.settleOnDeath), (x) => x.l.balance * x.share);
  if (debtsAll > debts) notes.push('Some debts are assumed to be taken over by heirs rather than settled; lenders must consent to this.');

  const feeBase = icop ? sum(doc.assets, (x) => x.value) + policiesToEstate : grossEstate;
  const executorFees = feeBase * a.executorFeeRate * (1 + a.vatRate);
  const masters = mastersFee(feeBase);
  const properties = assets.filter((x) =>
    ['primary-residence', 'residential-property', 'commercial-property'].includes(x.asset.type),
  );
  // Transfer to the surviving spouse still requires conveyancing; ICOP halves may pass by endorsement.
  const conveyancing = sum(properties, (x) => conveyancingEstimate(x.asset.value * x.share, a.vatRate));
  const sundries = grossEstate > 0 ? ESTATE_SUNDRIES : 0;
  const bondCancellation =
    liabilities.filter((x) => x.l.type === 'home-loan' && x.l.balance > 0 && x.l.settleOnDeath).length * BOND_CANCELLATION;
  const funeral = profile.funeralCost;

  // Capital gains tax on the deemed disposal at death (s9HA / para 40 of the Eighth Schedule).
  let gains = 0;
  for (const { asset, share } of assets) {
    if (ASSET_BEHAVIOUR[asset.type].cgtExempt) continue;
    if (hasSpouse && asset.bequeathToSpouse) continue; // roll-over to surviving spouse (s25(4))
    if (asset.baseCost <= 0) continue;
    let gain = (asset.value - asset.baseCost) * share;
    if (asset.type === 'primary-residence') gain -= table.cgt.primaryResidence * share;
    gains += Math.max(0, gain);
  }
  const netGain = Math.max(0, gains - table.cgt.deathExclusion);
  const taxableGain = netGain * table.cgt.inclusionRate;
  const cgt = incrementalTax(income.tax.taxable, taxableGain, income.taxAge, table);
  if (assets.some((x) => !ASSET_BEHAVIOUR[x.asset.type].cgtExempt && x.asset.baseCost <= 0 && !(hasSpouse && x.asset.bequeathToSpouse))) {
    notes.push('Some assets have no base cost captured, so CGT on death may be understated.');
  }

  const costs = executorFees + masters + conveyancing + bondCancellation + sundries + funeral;
  const netEstate = Math.max(0, grossEstate + deemedProperty - debtsAll - costs - cgt - accrualPayable);

  // s4(q): property accruing to the surviving spouse.
  const toSpouse = hasSpouse
    ? sum(assets.filter((x) => x.asset.bequeathToSpouse), (x) => x.asset.value * x.share) +
      sum(deemedPolicies.filter((p) => p.beneficiary === 'spouse'), (p) => p.cover)
    : 0;
  const spouseDeduction = Math.min(netEstate, toSpouse);
  // s4A: the surviving spouse can use the predeceased spouse's unused abatement (total max 2 × R3.5m).
  const abatement = table.estateDuty.abatement + Math.min(Math.max(0, profile.portedAbatement), table.estateDuty.abatement);
  const dutiable = Math.max(0, netEstate - spouseDeduction - abatement);
  const duty = estateDuty(dutiable, table);

  const cashAvailable =
    sum(
      assets.filter((x) => ASSET_BEHAVIOUR[x.asset.type].liquid),
      (x) => x.asset.value * x.share,
    ) + policiesToEstate;
  const cashRequired = debts + costs + cgt + duty + profile.cashBequests + accrualPayable;
  const liquidityShortfall = Math.max(0, cashRequired - cashAvailable);

  if (!hasSpouse && toSpouse === 0 && doc.household.maritalStatus === 'married') {
    notes.push('Spouse not included in the analysis — the s4(q) spousal deduction and CGT roll-over were not applied.');
  }

  return {
    person: key,
    grossEstate,
    deemedProperty,
    liabilities: debts,
    executorFees,
    mastersFees: masters,
    conveyancing,
    bondCancellation,
    sundries,
    funeral,
    cgt,
    capitalGains: gains,
    accrualPayable,
    accrualReceivable,
    netEstate,
    spouseDeduction,
    abatement,
    dutiable,
    estateDuty: duty,
    totalCosts: costs + cgt + duty,
    cashRequired,
    cashAvailable,
    liquidityShortfall,
    lines: [
      { label: 'Debts payable by the estate', amount: debts },
      { label: `Executor's fees (${(a.executorFeeRate * 100).toFixed(1)}% + VAT)`, amount: executorFees },
      { label: "Master's fees", amount: masters },
      { label: 'Conveyancing (transfer to heirs)', amount: conveyancing },
      { label: 'Bond cancellation', amount: bondCancellation },
      { label: 'Advertising, tax returns & sundries', amount: sundries },
      { label: 'Funeral costs', amount: funeral },
      { label: 'Capital gains tax on death', amount: cgt },
      { label: 'Estate duty', amount: duty },
      { label: 'Accrual claim payable to spouse', amount: accrualPayable },
      { label: 'Cash bequests', amount: profile.cashBequests },
    ],
    notes,
  };
};

/* ------------------------------------------------------------------ */
/* Death (life cover) need                                             */
/* ------------------------------------------------------------------ */

const netRetirementDeathBenefit = (funds: RetirementFund[], table: TaxTable): number => {
  const lumpable = funds.filter((f) => f.type !== 'life-annuity');
  const total = sum(lumpable, (f) => f.value);
  return Math.max(0, total - retirementLumpSumTax(total, table));
};

const deathIncomeTerm = (
  doc: FnaDocument,
  key: PersonKey,
  hasSpouse: boolean,
  spouseIncome: PersonIncome | undefined,
  now: Date,
): number => {
  const a = doc.assumptions;
  if (a.deathIncomeTerm === 'fixed') return a.deathIncomeFixedYears;
  const childYears = Math.max(
    0,
    ...doc.dependants
      .filter((d) => d.relationship === 'child')
      .map((d) => {
        const age = ageOn(d.dateOfBirth, now) ?? 0;
        return isChildDependent(d, age) ? d.dependencyEndAge - age : 0;
      }),
  );
  const other: PersonKey = key === 'client' ? 'spouse' : 'client';
  const spouseYears =
    hasSpouse && spouseIncome ? Math.max(0, doc.retirement[other].retirementAge - spouseIncome.age) : 0;
  if (a.deathIncomeTerm === 'youngest-independent') return childYears;
  if (a.deathIncomeTerm === 'spouse-retirement') return spouseYears;
  // auto: until the youngest child is independent; a non-earning spouse to retirement age.
  // Support for parents and other dependants is capitalised separately.
  const spouseDependent = hasSpouse && spouseIncome ? spouseIncome.afterTaxMonthly < 1 : false;
  return Math.max(childYears, spouseDependent ? spouseYears : 0);
};

const computeDeath = (
  doc: FnaDocument,
  key: PersonKey,
  income: PersonIncome,
  estate: EstateResult,
  education: EducationResult[],
  table: TaxTable,
  hasSpouse: boolean,
  spouseIncome: PersonIncome | undefined,
  now: Date,
) => {
  const a = doc.assumptions;
  const profile = doc.estate[key];
  const icop = hasSpouse && doc.household.maritalStatus === 'married' && doc.household.maritalRegime === 'icop';
  const notes: string[] = [];

  const years = deathIncomeTerm(doc, key, hasSpouse, spouseIncome, now);
  const continuingIncome = sum(
    doc.assets.filter((x) => x.monthlyIncome > 0 && x.bequeathToSpouse),
    (x) => x.monthlyIncome * shareOf(x.owner, key, icop) * (1 - income.tax.marginalRate),
  );
  const incomeToReplace = Math.max(0, income.afterTaxMonthly - continuingIncome);
  const replacementMonthly = years > 0 ? incomeToReplace * a.deathIncomeReplacement : 0;
  const extraYears = years > 0 ? years : a.deathIncomeFixedYears;
  const monthlyNeed = replacementMonthly + (profile.additionalIncomeNeedMonthly > 0 && extraYears > 0 ? profile.additionalIncomeNeedMonthly : 0);
  const incomeCapital =
    pvGrowingMonthlyIncome(replacementMonthly, years, a.riskCapitalReturn, a.cpi) +
    pvGrowingMonthlyIncome(profile.additionalIncomeNeedMonthly, extraYears, a.riskCapitalReturn, a.cpi);

  // Parents and other supported dependants: the actual support amount for the stated period.
  const supported = doc.dependants.filter((d) => d.relationship !== 'child' && d.monthlySupport > 0 && d.supportYears > 0);
  const supportCapital = sum(supported, (d) => pvGrowingMonthlyIncome(d.monthlySupport, d.supportYears, a.riskCapitalReturn, a.cpi));

  const educationCapital = Math.max(0, sum(education, (e) => e.tertiaryPV) - sum(doc.dependants, (d) => d.educationSavings));
  const hasDependants =
    hasSpouse ||
    supported.length > 0 ||
    doc.dependants.some((d) => d.relationship === 'child' && isChildDependent(d, ageOn(d.dateOfBirth, now) ?? 0));
  const adjustmentFund = hasDependants ? income.afterTaxMonthly * a.emergencyMonths : 0;

  const policies = doc.policies.filter((p) => p.lifeAssured === key);
  const lifeCover = sum(policies.filter((p) => p.type === 'life' && p.beneficiary !== 'business'), (p) => p.cover);
  const funeralCover = Math.min(estate.funeral, sum(policies.filter((p) => p.type === 'funeral'), (p) => p.cover));
  const familyIncome = sum(
    policies.filter((p) => p.type === 'family-income'),
    (p) => pvGrowingMonthlyIncome(p.monthlyBenefit, p.benefitTermYears || years, a.riskCapitalReturn, p.coverEscalation),
  );
  const retirement = netRetirementDeathBenefit(
    doc.retirementFunds.filter((f) => f.owner === key),
    table,
  );
  const liquid = sum(
    doc.assets.filter((x) => ASSET_BEHAVIOUR[x.type].liquid),
    (x) => x.value * shareOf(x.owner, key, icop),
  );

  if (years === 0 && income.afterTaxMonthly > 0) {
    notes.push('No dependent children or non-earning spouse were identified, so no family income replacement is included.');
  }
  if (doc.dependants.some((d) => d.specialNeeds)) {
    notes.push('A dependant has special needs and may require lifelong support — consider a special trust and extending the income term.');
  }
  notes.push(
    `Income capital: ${Math.round(a.deathIncomeReplacement * 100)}% of after-tax income for ${years} years, escalating at CPI and discounted at ${(a.riskCapitalReturn * 100).toFixed(1)}% p.a.`,
  );
  notes.push('Retirement fund death benefits are allocated by the fund trustees under s37C of the Pension Funds Act (nominations are not binding) and can take up to 12 months; they are shown net of lump-sum tax and do not provide estate liquidity.');

  const need = makeNeed(
    'life',
    'Life cover',
    [
      { label: 'Settle debts', amount: estate.liabilities },
      { label: 'Estate costs, CGT & estate duty', amount: estate.totalCosts - estate.funeral },
      { label: 'Funeral', amount: estate.funeral },
      { label: 'Cash bequests', amount: profile.cashBequests },
      { label: `Adjustment fund (${a.emergencyMonths} months' income)`, amount: adjustmentFund },
      { label: 'Tertiary education capital', amount: educationCapital },
      { label: `Income for dependants (${years} yrs)`, amount: incomeCapital, note: `${Math.round(monthlyNeed)} p.m. today` },
      { label: 'Support for parents & other dependants', amount: supportCapital },
      { label: 'Other capital needs', amount: profile.additionalCapitalNeed },
    ],
    [
      { label: 'Existing life cover', amount: lifeCover },
      { label: 'Funeral cover', amount: funeralCover },
      { label: 'Family income benefits (PV)', amount: familyIncome },
      { label: 'Retirement fund death benefits (net of tax)', amount: retirement },
      { label: 'Liquid assets & investments', amount: liquid },
    ],
    notes,
    key,
  );
  return { need, years, monthlyNeed };
};

/* ------------------------------------------------------------------ */
/* Disability, income protection, severe illness, funeral              */
/* ------------------------------------------------------------------ */

const permanentIp = (p: Policy, retirementAge: number) => p.benefitToAge >= Math.min(60, retirementAge);

const computeDisability = (doc: FnaDocument, key: PersonKey, income: PersonIncome, hasSpouse: boolean) => {
  const a = doc.assumptions;
  const icop = hasSpouse && doc.household.maritalStatus === 'married' && doc.household.maritalRegime === 'icop';
  const retirementAge = doc.retirement[key].retirementAge;
  const yearsToRetirement = Math.max(0, retirementAge - income.exactAge);
  const policies = doc.policies.filter((p) => p.lifeAssured === key && p.beneficiary !== 'business');

  const ipPolicies = policies.filter((p) => p.type === 'income-protection');
  const ipPermanent = sum(ipPolicies.filter((p) => permanentIp(p, retirementAge)), (p) => p.monthlyBenefit);
  const ipTemporary = sum(ipPolicies.filter((p) => !permanentIp(p, retirementAge)), (p) => p.monthlyBenefit);
  // Only earned income (salary / business income and bonus) is insurable; passive income continues on disability.
  const earned = income.earnedAfterTaxMonthly;
  const ipTarget = earned * a.incomeProtectionTarget;
  const ipNotes: string[] = income.working
    ? [
        `Target: ${Math.round(a.incomeProtectionTarget * 100)}% of after-tax earned income. Insurers generally limit total income protection to about 100% of after-tax income.`,
      ]
    : ['Not economically active or past retirement age — income protection does not apply.'];
  if (ipPolicies.some((p) => p.isGroup)) {
    ipNotes.push('Group income protection may be taxable if the employer owns the policy — confirm the tax treatment with the scheme.');
  }
  if (ipPermanent + ipTemporary > earned * 1.05 && earned > 0) {
    ipNotes.push('Existing income protection exceeds after-tax income — benefits may be reduced at claim stage (over-insurance).');
  }
  if (ipTemporary > 0) ipNotes.push(`Temporary (short-term) benefits of ${Math.round(ipTemporary)} p.m. only pay for a limited period.`);

  const incomeProtection = makeNeed(
    'income-protection',
    'Income protection',
    [{ label: 'Monthly income to protect', amount: ipTarget }],
    [{ label: 'Income protection to retirement', amount: ipPermanent }],
    ipNotes,
    key,
    true,
  );

  const debts = sum(
    doc.liabilities.filter((l) => l.settleOnDisability && !l.creditLifeCover),
    (l) => l.balance * shareOf(l.owner, key, icop),
  );
  // The member's own contributions can continue from an income protection benefit (it replaces after-tax
  // income before payroll deductions); the employer's contributions stop.
  const retirementCapital =
    a.disabilityIncludeRetirement && income.working
      ? pvGrowingMonthlyIncome(income.retirementEmployerMonthly, yearsToRetirement, a.preRetirementReturn, a.salaryEscalation)
      : 0;
  const incomeGap = Math.max(0, ipTarget - ipPermanent);
  const incomeGapCapital = pvGrowingMonthlyIncome(incomeGap, yearsToRetirement, a.riskCapitalReturn, a.cpi);
  const lumpCover = sum(policies.filter((p) => p.type === 'disability-lump'), (p) => p.cover);

  const disNotes = income.working
    ? ['Income replacement is addressed through income protection; the lump sum covers debt, lifestyle adaptations and lost employer retirement funding.']
    : ['Not economically active — lump-sum disability cover is generally not available or needed.'];
  if (incomeGap > 0) {
    disNotes.push(
      `If income protection is not taken up, a further ${Math.round(incomeGapCapital).toLocaleString('en-ZA')} lump sum would be needed to replace the income gap to age ${retirementAge}.`,
    );
  }
  const anyOcc = policies.filter((p) => p.type === 'disability-lump' && p.definition === 'any-occupation');
  if (anyOcc.length) disNotes.push('Some disability cover uses an "any occupation" definition, which is harder to claim on.');

  const disability = makeNeed(
    'disability',
    'Disability — lump sum',
    income.working
      ? [
          { label: 'Settle debts', amount: debts },
          { label: 'Home, vehicle & medical adaptations', amount: a.disabilityAdjustments },
          { label: `Lost employer retirement contributions (to ${retirementAge})`, amount: retirementCapital },
        ]
      : [],
    [{ label: 'Existing lump-sum disability cover', amount: lumpCover }],
    disNotes,
    key,
  );

  const siCover = sum(policies.filter((p) => p.type === 'severe-illness'), (p) => p.cover);
  const accelerated = policies.some((p) => p.type === 'severe-illness' && p.accelerated);
  const severeIllness = makeNeed(
    'severe-illness',
    'Severe illness',
    income.working ? [{ label: `${a.severeIllnessMonths} months of gross income`, amount: income.earnedGrossMonthly * a.severeIllnessMonths }] : [],
    [{ label: 'Existing severe illness cover', amount: siCover }],
    accelerated ? ['Some severe illness cover is accelerated — a claim reduces the life cover by the same amount.'] : [],
    key,
  );

  const funeralCost = doc.estate[key].funeralCost;
  const funeralCover = sum(policies.filter((p) => p.type === 'funeral'), (p) => p.cover);
  const quickLife = sum(
    policies.filter((p) => p.type === 'life' && !p.isGroup && !['estate', 'business', 'trust'].includes(p.beneficiary)),
    (p) => p.cover,
  );
  const funeral = makeNeed(
    'funeral',
    'Funeral',
    [{ label: 'Funeral costs', amount: funeralCost }],
    [
      { label: 'Funeral cover', amount: funeralCover },
      { label: 'Life cover paying a nominee within days', amount: Math.min(quickLife, Math.max(0, funeralCost - funeralCover)) },
    ],
    [
      'Bank accounts are frozen on death. Funeral policies and life cover with a nominated beneficiary pay quickly; group cover and estate proceeds can take weeks to months.',
    ],
    key,
  );

  return { incomeProtection, disability, severeIllness, funeral, incomeGapCapital };
};

/* ------------------------------------------------------------------ */
/* Retirement                                                          */
/* ------------------------------------------------------------------ */

export const computeRetirement = (doc: FnaDocument, key: PersonKey, income: PersonIncome, now: Date = new Date()): RetirementResult => {
  const a = doc.assumptions;
  const goal = doc.retirement[key];
  const notes: string[] = [];
  const currentAge = income.exactAge;
  const retired = currentAge >= goal.retirementAge || doc[key].employmentType === 'retired';
  // Someone already retired draws income from today, whatever retirement age was captured.
  const retStartAge = retired ? currentAge : goal.retirementAge;
  const retirementAge = retired ? Math.floor(currentAge) : goal.retirementAge;
  const planningAge = Math.max(goal.planningAge, Math.ceil(retStartAge) + 1);
  const yearsToRetirement = retired ? 0 : Math.max(0, goal.retirementAge - currentAge);
  const yearsInRetirement = Math.max(1, planningAge - retStartAge);

  const preFunds = doc.retirementFunds.filter((f) => f.owner === key && PRE_RETIREMENT_FUNDS.includes(f.type));
  const postFunds = doc.retirementFunds.filter((f) => f.owner === key && f.type === 'living-annuity');
  const earmarked = doc.assets.filter((x) => x.earmark === 'retirement' && (x.owner === key || x.owner === 'joint'));
  const earmarkedValue = sum(earmarked, (x) => x.value * (x.owner === 'joint' ? 0.5 : 1));
  const earmarkedMonthly = sum(earmarked, (x) => x.monthlyContribution * (x.owner === 'joint' ? 0.5 : 1));
  const currentCapital = sum(preFunds, (f) => f.value) + sum(postFunds, (f) => f.value) + earmarkedValue;
  const monthlyContributions = retired
    ? 0
    : sum(preFunds, (f) => f.employeeMonthly + f.employerMonthly) + earmarkedMonthly;

  const targetMonthlyToday =
    goal.targetMode === 'amount' ? goal.targetMonthly : income.grossMonthlyEquivalent * goal.targetPercent;
  const otherIncomeToday = goal.otherIncomeMonthly;
  const netTargetToday = Math.max(0, targetMonthlyToday - otherIncomeToday);
  const inflator = Math.pow(1 + a.cpi, yearsToRetirement);
  const targetMonthlyAtRetirement = netTargetToday * inflator;

  // Accumulation: monthly compounding with annual escalation of contributions.
  const timeline: RetirementYear[] = [];
  const rPre = monthlyRate(a.preRetirementReturn);
  const rPost = monthlyRate(a.postRetirementReturn);
  const startYear = now.getFullYear();
  let capital = currentCapital;
  let contribution = monthlyContributions;
  const wholeYearsToRet = Math.ceil(yearsToRetirement);
  timeline.push({
    age: Math.floor(currentAge),
    year: startYear,
    capital,
    capitalReal: capital,
    contribution: 0,
    withdrawal: 0,
    phase: retired ? 'retirement' : 'accumulation',
  });
  const monthsToRet = Math.round(yearsToRetirement * 12);
  for (let m = 1; m <= monthsToRet; m++) {
    capital = capital * (1 + rPre) + contribution;
    if (m % 12 === 0 || m === monthsToRet) {
      const t = m / 12;
      timeline.push({
        age: Math.floor(currentAge + t),
        year: startYear + Math.ceil(t),
        capital,
        capitalReal: capital / Math.pow(1 + a.cpi, t),
        contribution: contribution * 12,
        withdrawal: 0,
        phase: 'accumulation',
      });
    }
    if (m % 12 === 0) contribution *= 1 + a.salaryEscalation;
  }
  const projectedCapital = capital;
  const projectedCapitalReal = projectedCapital / inflator;

  // Capital required to fund the (net) target income to the planning age.
  const requiredCapital = pvGrowingMonthlyIncome(targetMonthlyAtRetirement, yearsInRetirement, a.postRetirementReturn, a.cpi);
  const shortfall = Math.max(0, requiredCapital - projectedCapital);
  const annuityFactorAtRet = requiredCapital > 0 && targetMonthlyAtRetirement > 0 ? requiredCapital / targetMonthlyAtRetirement : pvGrowingMonthlyIncome(1, yearsInRetirement, a.postRetirementReturn, a.cpi);
  const sustainableAtRet = annuityFactorAtRet > 0 ? projectedCapital / annuityFactorAtRet : 0;
  const sustainableMonthlyToday = sustainableAtRet / inflator + otherIncomeToday;
  const additionalMonthly =
    shortfall > 0 && yearsToRetirement >= 1 / 12
      ? requiredEscalatingSaving(shortfall, yearsToRetirement, a.preRetirementReturn, a.salaryEscalation)
      : 0;

  // Drawdown phase: withdraw the (inflating) target income monthly in advance.
  let withdrawal = targetMonthlyAtRetirement;
  let depletionAge: number | null = null;
  const monthsInRet = Math.round(yearsInRetirement * 12);
  for (let m = 1; m <= monthsInRet; m++) {
    capital = (capital - withdrawal) * (1 + rPost);
    if (capital <= 0 && depletionAge === null) {
      depletionAge = retStartAge + m / 12;
      capital = 0;
    }
    if (m % 12 === 0) {
      const t = yearsToRetirement + m / 12;
      timeline.push({
        age: Math.floor(retStartAge + m / 12),
        year: startYear + Math.round(t),
        capital: Math.max(0, capital),
        capitalReal: Math.max(0, capital) / Math.pow(1 + a.cpi, t),
        contribution: 0,
        withdrawal: withdrawal * 12,
        phase: 'retirement',
      });
      withdrawal *= 1 + a.cpi;
    }
  }

  const initialDrawdown = projectedCapital > 0 ? (targetMonthlyAtRetirement * 12) / projectedCapital : 0;
  if (initialDrawdown > 0.175) {
    notes.push('The required income exceeds the 17.5% maximum living annuity drawdown — the target is not achievable from this capital.');
  } else if (initialDrawdown > 0.05 && targetMonthlyAtRetirement > 0) {
    notes.push(`An initial drawdown of ${(initialDrawdown * 100).toFixed(1)}% exceeds the ~4–5% generally regarded as sustainable for a living annuity.`);
  }
  if (wholeYearsToRet > 0 && monthlyContributions === 0) notes.push('No ongoing retirement contributions are recorded.');
  notes.push(
    `Projection: ${(a.preRetirementReturn * 100).toFixed(1)}% p.a. before and ${(a.postRetirementReturn * 100).toFixed(1)}% p.a. after retirement, CPI ${(a.cpi * 100).toFixed(1)}%, contributions escalating at ${(a.salaryEscalation * 100).toFixed(1)}% p.a. Returns are not guaranteed.`,
  );

  const funded = requiredCapital > 0 ? Math.min(1, projectedCapital / requiredCapital) : 1;
  const currentGross = income.grossMonthlyEquivalent;

  return {
    person: key,
    retired,
    currentAge,
    retirementAge,
    planningAge,
    yearsToRetirement,
    yearsInRetirement,
    currentCapital,
    monthlyContributions,
    projectedCapital,
    projectedCapitalReal,
    targetMonthlyToday,
    otherIncomeToday,
    targetMonthlyAtRetirement,
    requiredCapital,
    requiredCapitalReal: requiredCapital / inflator,
    shortfall,
    shortfallReal: shortfall / inflator,
    additionalMonthly,
    sustainableMonthlyToday,
    replacementRatio: currentGross > 0 ? sustainableMonthlyToday / currentGross : 0,
    targetReplacementRatio: currentGross > 0 ? targetMonthlyToday / currentGross : 0,
    initialDrawdown,
    depletionAge,
    funded,
    status: statusFor(funded, requiredCapital),
    timeline,
    notes,
  };
};

/* ------------------------------------------------------------------ */
/* Goals & emergency fund                                              */
/* ------------------------------------------------------------------ */

const computeGoals = (doc: FnaDocument, now: Date): GoalResult[] => {
  const a = doc.assumptions;
  const pool = doc.assets.filter((x) => x.earmark === 'goal');
  const poolValue = sum(pool, (x) => x.value);
  const poolMonthly = sum(pool, (x) => x.monthlyContribution);
  const totalCost = sum(doc.goals, (g) => g.cost);
  return doc.goals.map((g) => {
    const years = Math.max(0, g.targetYear - now.getFullYear());
    const futureCost = g.cost * Math.pow(1 + a.cpi, years);
    const share = totalCost > 0 ? g.cost / totalCost : 0;
    const projected =
      fv(g.existingSavings + poolValue * share, a.riskCapitalReturn, years) +
      fvEscalatingContributions(g.monthlyContribution + poolMonthly * share, years, a.riskCapitalReturn, 0);
    const shortfall = Math.max(0, futureCost - projected);
    const monthlyRequired = shortfall > 0 && years > 0 ? requiredEscalatingSaving(shortfall, years, a.riskCapitalReturn, 0) : 0;
    const funded = futureCost > 0 ? Math.min(1, projected / futureCost) : 1;
    return {
      id: g.id,
      name: g.name || 'Goal',
      years,
      futureCost,
      projected,
      shortfall,
      monthlyRequired,
      funded,
      status: statusFor(funded, futureCost),
    };
  });
};

const computeEmergency = (doc: FnaDocument, cashflow: Cashflow): NeedResult => {
  const a = doc.assumptions;
  const essentialMonthly = cashflow.essentialExpenses + cashflow.debtRepayments + cashflow.riskPremiums;
  const earmarked = doc.assets.filter((x) => x.earmark === 'emergency');
  const pool = earmarked.length ? earmarked : doc.assets.filter((x) => x.type === 'cash' || x.type === 'money-market');
  const available = sum(pool, (x) => x.value);
  const variable = doc.client.employmentType !== 'salaried' || (doc.household.includeSpouse && !['salaried', 'retired', 'unemployed'].includes(doc.spouse.employmentType));
  const notes = [`Target: ${a.emergencyMonths} months of essential expenses, debt repayments and risk premiums (${Math.round(essentialMonthly).toLocaleString('en-ZA')} p.m.).`];
  if (variable && a.emergencyMonths < 6) notes.push('Variable or self-employed income: consider a 6-month reserve.');
  if (!earmarked.length) notes.push('No assets are earmarked as an emergency fund, so all cash and money-market balances were used.');
  return makeNeed(
    'emergency',
    'Emergency fund',
    [{ label: `${a.emergencyMonths} months of essential outgoings`, amount: essentialMonthly * a.emergencyMonths }],
    [{ label: earmarked.length ? 'Earmarked emergency savings' : 'Cash & money market', amount: available }],
    notes,
  );
};

/* ------------------------------------------------------------------ */
/* Findings & completeness                                             */
/* ------------------------------------------------------------------ */

const fmt = (n: number) => `R${Math.round(n).toLocaleString('en-ZA')}`;

const buildFindings = (doc: FnaDocument, analysis: Omit<Analysis, 'findings' | 'completeness' | 'completenessScore'>, now: Date): Finding[] => {
  const f: Finding[] = [];
  const hasMinors = doc.dependants.some((d) => d.relationship === 'child' && (ageOn(d.dateOfBirth, now) ?? 0) < 18);

  for (const key of analysis.people) {
    const p = analysis.persons[key];
    if (!p) continue;
    const who = p.name;
    if (!p.income.ageKnown) {
      f.push({ severity: 'critical', area: 'data', person: key, title: `Date of birth missing for ${who}`, detail: `Calculations assume age ${FALLBACK_AGE} until a date of birth or ID number is captured.` });
    }
    if (p.death.status === 'shortfall' || p.death.status === 'partial') {
      f.push({ severity: p.death.status === 'shortfall' ? 'critical' : 'warning', area: 'life', person: key, title: `Life cover shortfall of ${fmt(p.death.shortfall)} on ${who}`, detail: `Existing provision meets ${Math.round(p.death.funded * 100)}% of the capital your family would need.` });
    }
    if (p.incomeProtection.need > 0 && p.incomeProtection.funded < 0.95) {
      f.push({ severity: p.incomeProtection.provision === 0 ? 'critical' : 'warning', area: 'income-protection', person: key, title: `Income protection gap of ${fmt(p.incomeProtection.shortfall)} p.m. for ${who}`, detail: 'Loss of income through illness or injury is the largest financial risk for most working people.' });
    }
    if (p.disability.status === 'shortfall' || p.disability.status === 'partial') {
      f.push({ severity: 'warning', area: 'disability', person: key, title: `Disability lump-sum shortfall of ${fmt(p.disability.shortfall)} for ${who}`, detail: 'Covers debt settlement, adaptations and lost retirement contributions if permanently disabled.' });
    }
    if (p.severeIllness.status === 'shortfall') {
      f.push({ severity: 'warning', area: 'severe-illness', person: key, title: `Severe illness cover of ${fmt(p.severeIllness.shortfall)} recommended for ${who}`, detail: 'Provides a lump sum on diagnosis of cancer, heart attack, stroke and similar events.' });
    }
    if (p.estate.liquidityShortfall > 0) {
      const toSpouse = sum(
        doc.policies.filter((x) => x.lifeAssured === key && x.type === 'life' && x.beneficiary === 'spouse'),
        (x) => x.cover,
      );
      const spouseCanFund = analysis.people.length > 1 && toSpouse >= p.estate.liquidityShortfall;
      f.push({
        severity: spouseCanFund ? 'warning' : 'critical',
        area: 'estate',
        person: key,
        title: `Estate liquidity shortfall of ${fmt(p.estate.liquidityShortfall)} for ${who}`,
        detail: spouseCanFund
          ? 'The executor cannot access policies paid to a nominee. The surviving spouse could contribute from policy proceeds, but cover payable to the estate (or a spouse undertaking) avoids forced sales.'
          : 'The executor may have to sell assets (such as the family home) to pay debts, costs and taxes.',
      });
    }
    if (p.estate.estateDuty > 0) {
      f.push({ severity: 'info', area: 'estate', person: key, title: `Estate duty of ${fmt(p.estate.estateDuty)} payable on ${who}'s estate`, detail: 'Consider estate structuring (spousal bequests, trusts, loan accounts, donations) with a fiduciary specialist.' });
    }
    const will = doc.estate[key].hasWill;
    if (will !== 'yes') {
      f.push({ severity: 'critical', area: 'estate', person: key, title: `${who} has no confirmed valid will`, detail: 'Without a will the estate devolves under the Intestate Succession Act, which may not reflect your wishes.' });
    } else if (doc.estate[key].willDate) {
      const age = (now.getTime() - new Date(doc.estate[key].willDate).getTime()) / (365.25 * 864e5);
      if (age > 5) f.push({ severity: 'warning', area: 'estate', person: key, title: `${who}'s will is ${Math.floor(age)} years old`, detail: 'Wills should be reviewed every 3–5 years and after marriage, divorce or births.' });
    }
    if (hasMinors && doc.estate[key].guardianNominated !== 'yes') {
      f.push({ severity: 'warning', area: 'estate', person: key, title: `No guardian nominated by ${who} for minor children`, detail: 'Nominate a guardian in the will and consider a testamentary trust — minors cannot inherit directly (funds go to the Guardian’s Fund).' });
    }
    const r = p.retirement;
    if (!r.retired && r.requiredCapital > 0 && r.yearsToRetirement < 1 / 12 && r.shortfall > 0) {
      f.push({ severity: 'critical', area: 'retirement', person: key, title: `Retirement capital shortfall of ${fmt(r.shortfall)} for ${who}`, detail: 'Retirement is imminent — consider a later retirement date, a lower income or phased retirement.' });
    } else if (!r.retired && r.requiredCapital > 0) {
      if (r.status === 'covered') {
        f.push({ severity: 'positive', area: 'retirement', person: key, title: `${who} is on track for retirement`, detail: `Projected capital funds about ${Math.round(r.replacementRatio * 100)}% of current income from age ${r.retirementAge}.` });
      } else {
        f.push({ severity: r.status === 'shortfall' ? 'critical' : 'warning', area: 'retirement', person: key, title: `Retirement shortfall for ${who}: save an extra ${fmt(r.additionalMonthly)} p.m.`, detail: `Projected capital funds ${Math.round(r.funded * 100)}% of the target income (${Math.round(r.replacementRatio * 100)}% replacement vs ${Math.round(r.targetReplacementRatio * 100)}% targeted).` });
      }
    }
    if (r.retired && r.depletionAge !== null && r.depletionAge < r.planningAge) {
      f.push({ severity: 'critical', area: 'retirement', person: key, title: `${who}'s capital may run out at age ${Math.floor(r.depletionAge)}`, detail: 'Consider reducing the drawdown, a guaranteed annuity for core income, or other income sources.' });
    }
    const funds = doc.retirementFunds.filter((x) => x.owner === key);
    if (funds.some((x) => !x.beneficiariesNominated)) {
      f.push({ severity: 'info', area: 'retirement', person: key, title: `Nominate beneficiaries on all of ${who}'s retirement funds`, detail: 'Nominations guide the trustees under s37C and speed up payment to dependants.' });
    }
    const la = funds.filter((x) => x.type === 'living-annuity' && x.drawdownRate > 0.08);
    if (la.length) f.push({ severity: 'warning', area: 'retirement', person: key, title: `High living annuity drawdown for ${who}`, detail: 'Drawdowns above ~8% p.a. carry a high risk of capital depletion.' });
    const estatePolicies = doc.policies.filter((x) => x.lifeAssured === key && x.type === 'life' && x.beneficiary === 'estate');
    if (estatePolicies.length && p.estate.liquidityShortfall === 0) {
      f.push({ severity: 'info', area: 'estate', person: key, title: `Review beneficiary nominations on ${who}'s life policies`, detail: 'Policies paying into the estate attract executor’s fees and delay payment; nominate beneficiaries where estate liquidity is sufficient.' });
    }
  }

  const cf = analysis.cashflow;
  if (cf.takeHome > 0) {
    if (cf.surplus < 0) {
      f.push({ severity: 'critical', area: 'cashflow', title: `Monthly cash-flow deficit of ${fmt(-cf.surplus)}`, detail: 'Outgoings exceed take-home pay. Budget restructuring should precede new commitments.' });
    } else {
      f.push({ severity: 'positive', area: 'cashflow', title: `Monthly surplus of ${fmt(cf.surplus)}`, detail: 'Available to fund recommendations, build the emergency fund or reduce debt.' });
    }
  }
  const ratios = analysis.ratios;
  if (ratios.debtToIncome > 0.4) {
    f.push({ severity: 'critical', area: 'debt', title: `Debt repayments are ${Math.round(ratios.debtToIncome * 100)}% of gross income`, detail: 'Above 40% indicates high debt stress. Prioritise expensive unsecured debt.' });
  } else if (ratios.debtToIncome > 0.3) {
    f.push({ severity: 'warning', area: 'debt', title: `Debt repayments are ${Math.round(ratios.debtToIncome * 100)}% of gross income`, detail: 'Aim to keep total debt repayments below 30–36% of gross income.' });
  }
  const expensive = doc.liabilities.filter((l) => UNSECURED_DEBT.includes(l.type) && l.interestRate >= 0.18 && l.balance > 0);
  if (expensive.length) {
    f.push({ severity: 'warning', area: 'debt', title: `${expensive.length} high-interest debt account${expensive.length > 1 ? 's' : ''} (18%+)`, detail: `Settling ${fmt(sum(expensive, (l) => l.balance))} of expensive debt is a guaranteed, tax-free return.` });
  }
  if (analysis.emergency.need > 0 && analysis.emergency.status !== 'covered') {
    f.push({ severity: analysis.emergency.status === 'shortfall' ? 'warning' : 'info', area: 'emergency', title: `Emergency fund at ${ratios.emergencyMonths.toFixed(1)} months`, detail: `Build a further ${fmt(analysis.emergency.shortfall)} in an accessible money-market account.` });
  }
  for (const e of analysis.education) {
    if (e.status !== 'covered' && e.status !== 'na') {
      f.push(
        e.lumpSumRequired > 0
          ? { severity: 'warning', area: 'education', title: `Education shortfall for ${e.name}: ${fmt(e.lumpSumRequired)} needed now`, detail: 'Study is already under way, so the remaining costs must be funded from capital.' }
          : { severity: 'warning', area: 'education', title: `Education shortfall for ${e.name}: ${fmt(e.monthlyRequired)} p.m.`, detail: `Tertiary costs of about ${fmt(e.capitalAtStart)} are needed from ${now.getFullYear() + Math.round(e.yearsToTertiary)}.` },
      );
    }
  }
  for (const d of doc.dependants.filter((x) => x.specialNeeds)) {
    f.push({ severity: 'warning', area: 'estate', title: `${d.name || 'A dependant'} has special needs`, detail: 'Plan for lifelong support: a special trust (s6B) in the will, guardianship and capital beyond the normal dependency age.' });
  }
  if (doc.household.maritalStatus === 'married' && doc.household.maritalRegime === 'icop') {
    f.push({ severity: 'info', area: 'estate', title: 'Married in community of property', detail: 'Both spouses share one joint estate; business risk and debts affect both. Consider the implications with an attorney.' });
  }
  if (analysis.risk.mismatch) {
    f.push({ severity: 'warning', area: 'investment', title: 'Risk tolerance and capacity differ', detail: 'The recommended portfolio follows the lower of the two; discuss the difference with the client.' });
  }

  const e = doc.engagement;
  if (!e.popiaConsent) f.push({ severity: 'critical', area: 'compliance', title: 'POPIA consent not recorded', detail: 'Obtain the client’s consent to process personal (and special personal) information before advice is finalised.' });
  if (!e.disclosureLetterProvided) f.push({ severity: 'warning', area: 'compliance', title: 'FSP disclosures not confirmed', detail: 'Confirm that the FSP and representative disclosures (GCC s4, s5 and s7) were provided.' });
  if (!e.ficaVerified) f.push({ severity: 'warning', area: 'compliance', title: 'FICA verification outstanding', detail: 'Verify the client’s identity and residential address before implementation.' });
  const replacements = doc.advice.recommendations.filter((r) => r.isReplacement && (!r.replacementReasons || !r.replacementConsequences));
  if (replacements.length) f.push({ severity: 'critical', area: 'compliance', title: 'Replacement disclosures incomplete', detail: 'GCC s8(1)(d) requires the costs, consequences and reasons for replacing a product to be recorded.' });

  const order = { critical: 0, warning: 1, info: 2, positive: 3 };
  return f.sort((x, y) => order[x.severity] - order[y.severity]);
};

const buildCompleteness = (doc: FnaDocument, people: PersonKey[]) => {
  const items: { section: string; complete: boolean; detail: string }[] = [];
  items.push({ section: 'Engagement', complete: doc.engagement.popiaConsent && !!doc.engagement.meetingDate, detail: 'Meeting details and POPIA consent' });
  for (const k of people) {
    const p = doc[k];
    const label = k === 'client' ? 'Client' : 'Spouse';
    items.push({ section: `${label} profile`, complete: !!(p.firstName && p.surname && p.dateOfBirth && p.gender), detail: 'Name, date of birth and gender' });
    items.push({ section: `${label} income`, complete: doc.income[k].grossMonthly > 0 || ['retired', 'unemployed', 'student'].includes(p.employmentType), detail: 'Gross income' });
  }
  const exp = livingExpenses(doc);
  items.push({ section: 'Expenses', complete: exp.total > 0, detail: 'Monthly living expenses' });
  items.push({ section: 'Assets', complete: doc.assets.length > 0 || doc.retirementFunds.length > 0, detail: 'Assets and retirement funds' });
  items.push({ section: 'Existing cover', complete: doc.policies.length > 0, detail: 'Existing policies (or confirmed none)' });
  items.push({ section: 'Estate', complete: people.every((k) => doc.estate[k].hasWill !== 'unsure'), detail: 'Will status' });
  items.push({ section: 'Risk profile', complete: scoreRiskProfile(doc.riskProfile.answers).complete, detail: 'Risk questionnaire' });
  items.push({ section: 'Advice', complete: doc.advice.recommendations.length > 0 || !!doc.advice.summary, detail: 'Recommendations recorded' });
  return items;
};

/* ------------------------------------------------------------------ */
/* Orchestrator                                                        */
/* ------------------------------------------------------------------ */

export const analyse = (doc: FnaDocument, now: Date = new Date()): Analysis => {
  const table = getTaxTable(doc.assumptions.taxYear);
  const hasSpouse = doc.household.includeSpouse && ['married', 'life-partner'].includes(doc.household.maritalStatus);
  const people: PersonKey[] = hasSpouse ? ['client', 'spouse'] : ['client'];

  const incomeMap: Partial<Record<PersonKey, PersonIncome>> = {};
  for (const k of people) incomeMap[k] = computeIncome(doc, k, table, now);
  const incomes = people.map((k) => incomeMap[k]!);

  const cashflow = computeCashflow(doc, incomes, people);
  const netWorth = computeNetWorth(doc);
  const education = computeEducation(doc, now);
  const emergency = computeEmergency(doc, cashflow);

  const grossTotal = sum(incomes, (i) => i.grossMonthlyEquivalent);
  const housing =
    sum(doc.liabilities.filter((l: Liability) => l.type === 'home-loan'), (l) => l.monthlyRepayment) + (doc.expenses.items['rent'] ?? 0);
  const retirementSaving = sum(incomes, (i) => i.retirementPayrollMonthly + i.retirementEmployerMonthly + i.retirementOwnMonthly);
  const essentialMonthly = cashflow.essentialExpenses + cashflow.debtRepayments + cashflow.riskPremiums;
  const ratios: Ratios = {
    debtToIncome: grossTotal > 0 ? cashflow.debtRepayments / grossTotal : 0,
    unsecuredDebtToIncome:
      grossTotal > 0 ? sum(doc.liabilities.filter((l) => UNSECURED_DEBT.includes(l.type)), (l) => l.monthlyRepayment) / grossTotal : 0,
    housingToIncome: grossTotal > 0 ? housing / grossTotal : 0,
    // Employer contributions are part of the cost-to-company package, so they count in the base too.
    savingsRate:
      grossTotal > 0
        ? (retirementSaving + sum(doc.assets, (a: Asset) => a.monthlyContribution)) / (grossTotal + sum(incomes, (i) => i.retirementEmployerMonthly))
        : 0,
    premiumsToIncome: grossTotal > 0 ? cashflow.riskPremiums / grossTotal : 0,
    emergencyMonths: essentialMonthly > 0 ? emergency.provision / essentialMonthly : 0,
  };

  const persons: Record<PersonKey, PersonAnalysis | undefined> = { client: undefined, spouse: undefined };
  for (const k of people) {
    const income = incomeMap[k]!;
    const other: PersonKey = k === 'client' ? 'spouse' : 'client';
    const estate = computeEstate(doc, k, income, table, hasSpouse);
    const death = computeDeath(doc, k, income, estate, education, table, hasSpouse, incomeMap[other], now);
    const dis = computeDisability(doc, k, income, hasSpouse);
    persons[k] = {
      key: k,
      name: income.name,
      income,
      death: death.need,
      deathIncomeYears: death.years,
      deathIncomeMonthly: death.monthlyNeed,
      disability: dis.disability,
      incomeProtection: dis.incomeProtection,
      severeIllness: dis.severeIllness,
      funeral: dis.funeral,
      estate,
      retirement: computeRetirement(doc, k, income, now),
    };
  }

  const educationTotalNeed = sum(education, (e) => e.capitalAtStart > 0 ? pv(e.capitalAtStart, doc.assumptions.riskCapitalReturn, e.yearsToTertiary) : 0);
  const educationProvision = sum(education, (e) => pv(e.projectedSavings, doc.assumptions.riskCapitalReturn, e.yearsToTertiary));
  const educationTotal = makeNeed(
    'education',
    'Education funding',
    [{ label: 'Tertiary education (present value)', amount: educationTotalNeed }],
    [{ label: 'Education savings (present value)', amount: educationProvision }],
  );

  const partial = {
    table,
    people,
    persons,
    incomes,
    cashflow,
    ratios,
    netWorth,
    emergency,
    education,
    educationTotal,
    goals: computeGoals(doc, now),
    risk: scoreRiskProfile(doc.riskProfile.answers),
  };
  const completeness = buildCompleteness(doc, people);
  return {
    ...partial,
    findings: buildFindings(doc, partial, now),
    completeness,
    completenessScore: completeness.filter((c) => c.complete).length / completeness.length,
  };
};
