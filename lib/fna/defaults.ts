import { DEFAULT_TAX_YEAR } from './tax';
import type {
  AdviceRecord,
  Assumptions,
  Asset,
  Dependant,
  Engagement,
  EstateProfile,
  FnaDocument,
  Income,
  Liability,
  OtherGoal,
  Person,
  Policy,
  PracticeProfile,
  Recommendation,
  RetirementFund,
  RetirementGoal,
} from './types';

export const SCHEMA_VERSION = 2;

export const uid = (): string =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

/**
 * Default economic and planning assumptions. Each is editable per client and
 * disclosed in the report. See /methodology for the rationale and sources.
 */
export const DEFAULT_ASSUMPTIONS: Assumptions = {
  taxYear: DEFAULT_TAX_YEAR,
  cpi: 0.045,
  salaryEscalation: 0.055,
  preRetirementReturn: 0.085,
  postRetirementReturn: 0.075,
  riskCapitalReturn: 0.07,
  educationInflation: 0.065,
  deathIncomeReplacement: 0.75,
  incomeProtectionTarget: 1,
  disabilityAdjustments: 150_000,
  disabilityIncludeRetirement: true,
  severeIllnessMonths: 24,
  emergencyMonths: 3,
  funeralAdult: 50_000,
  funeralChild: 25_000,
  executorFeeRate: 0.035,
  vatRate: 0.15,
  deathIncomeTerm: 'auto',
  deathIncomeFixedYears: 15,
};

export const emptyPerson = (): Person => ({
  title: '',
  firstName: '',
  surname: '',
  idNumber: '',
  passportNumber: '',
  nationality: 'South African',
  dateOfBirth: '',
  gender: '',
  smoker: false,
  healthNotes: '',
  hazardousPursuits: '',
  occupation: '',
  employer: '',
  employmentType: 'salaried',
  educationLevel: '',
  email: '',
  mobile: '',
  taxNumber: '',
});

export const emptyIncome = (): Income => ({
  grossMonthly: 0,
  annualBonus: 0,
  otherTaxableMonthly: 0,
  nonTaxableMonthly: 0,
  medicalAidPayrollMonthly: 0,
  medicalSchemeMembers: 0,
  otherPayrollDeductionsMonthly: 0,
  payeOverrideMonthly: null,
  uifApplies: true,
});

export const defaultRetirementGoal = (): RetirementGoal => ({
  retirementAge: 65,
  planningAge: 92,
  targetMode: 'percent',
  targetPercent: 0.75,
  targetMonthly: 0,
  otherIncomeMonthly: 0,
});

export const defaultEstate = (): EstateProfile => ({
  hasWill: 'unsure',
  willDate: '',
  willLocation: '',
  executor: '',
  guardianNominated: 'na',
  hasTrust: false,
  trustNotes: '',
  additionalIncomeNeedMonthly: 0,
  additionalCapitalNeed: 0,
  portedAbatement: 0,
  cashBequests: 0,
  accrualCommencementValue: 0,
  funeralCost: 50_000,
  notes: '',
});

export const defaultEngagement = (): Engagement => ({
  meetingDate: new Date().toISOString().slice(0, 10),
  meetingType: 'in-person',
  adviceType: 'comprehensive',
  scope: ['risk', 'retirement', 'investment', 'estate', 'education', 'debt'],
  limitations: '',
  disclosureLetterProvided: false,
  popiaConsent: false,
  popiaConsentDate: '',
  specialInfoConsent: false,
  ficaVerified: false,
  clientDeclinedFna: false,
  declineReason: '',
  conflictsDisclosed: false,
  remunerationDisclosed: false,
  pepStatus: 'no',
  marketingOptIn: false,
  copyProvided: false,
  reasonForAdvice: '',
  clientObjectives: '',
  clientConcerns: '',
  otherAdvisers: '',
});

export const defaultAdvice = (): AdviceRecord => ({
  summary: '',
  recommendations: [],
  productsConsidered: '',
  clientDeviations: '',
  feesAndCommission:
    'Commission on long-term insurance products is paid by the product supplier within the limits prescribed by the Regulations under the Long-term Insurance Act and disclosed in the product quotation. No fee is charged to the client unless agreed in writing.',
  conflictsOfInterest: '',
  nextReviewDate: '',
  clientSignedAt: '',
  adviserSignedAt: '',
});

export const newDocument = (assumptions: Assumptions = DEFAULT_ASSUMPTIONS): FnaDocument => {
  const now = new Date().toISOString();
  return {
    id: uid(),
    schemaVersion: SCHEMA_VERSION,
    createdAt: now,
    updatedAt: now,
    status: 'draft',
    client: emptyPerson(),
    spouse: { ...emptyPerson(), employmentType: 'salaried' },
    household: {
      maritalStatus: 'single',
      maritalRegime: 'na',
      dateOfMarriage: '',
      includeSpouse: false,
      physicalAddress: '',
      city: '',
      province: '',
      postalCode: '',
    },
    dependants: [],
    income: { client: emptyIncome(), spouse: emptyIncome() },
    expenses: { items: {}, custom: [] },
    retirementFunds: [],
    assets: [],
    liabilities: [],
    policies: [],
    retirement: { client: defaultRetirementGoal(), spouse: defaultRetirementGoal() },
    goals: [],
    estate: { client: defaultEstate(), spouse: defaultEstate() },
    riskProfile: { answers: {}, notes: '' },
    assumptions: { ...assumptions },
    engagement: defaultEngagement(),
    advice: defaultAdvice(),
    notes: '',
  };
};

export const newDependant = (): Dependant => ({
  id: uid(),
  name: '',
  relationship: 'child',
  dateOfBirth: '',
  gender: '',
  dependencyEndAge: 23,
  monthlySupport: 0,
  supportYears: 0,
  schoolType: 'public',
  schoolFeesAnnual: 0,
  tertiary: true,
  tertiaryStartAge: 19,
  tertiaryYears: 4,
  tertiaryCostAnnual: 150_000,
  educationSavings: 0,
  educationSavingsMonthly: 0,
  specialNeeds: false,
  notes: '',
});

export const newRetirementFund = (owner: RetirementFund['owner'] = 'client'): RetirementFund => ({
  id: uid(),
  owner,
  type: 'pension',
  provider: '',
  name: '',
  value: 0,
  savingsPot: 0,
  employeeMonthly: 0,
  employerMonthly: 0,
  viaPayroll: true,
  beneficiariesNominated: false,
  drawdownRate: 0.05,
  notes: '',
});

export const newAsset = (owner: Asset['owner'] = 'client'): Asset => ({
  id: uid(),
  owner,
  type: 'cash',
  description: '',
  provider: '',
  value: 0,
  baseCost: 0,
  monthlyContribution: 0,
  earmark: 'general',
  monthlyIncome: 0,
  bequeathToSpouse: true,
  notes: '',
});

export const newLiability = (owner: Liability['owner'] = 'client'): Liability => ({
  id: uid(),
  owner,
  type: 'home-loan',
  lender: '',
  description: '',
  balance: 0,
  interestRate: 0.1075, // prime from 25 Sept 2026
  monthlyRepayment: 0,
  linkedAssetId: '',
  creditLifeCover: false,
  settleOnDeath: true,
  settleOnDisability: true,
});

export const newPolicy = (lifeAssured: Policy['lifeAssured'] = 'client'): Policy => ({
  id: uid(),
  lifeAssured,
  type: 'life',
  insurer: '',
  policyNumber: '',
  isGroup: false,
  cover: 0,
  monthlyBenefit: 0,
  benefitTermYears: 0,
  waitingPeriodMonths: 3,
  benefitToAge: 65,
  beneficiary: 'spouse',
  definition: 'own-occupation',
  accelerated: false,
  premiumMonthly: 0,
  premiumEscalation: 0,
  coverEscalation: 0,
  inceptionDate: '',
  notes: '',
});

export const newGoal = (): OtherGoal => ({
  id: uid(),
  owner: 'client',
  name: '',
  cost: 0,
  targetYear: new Date().getFullYear() + 5,
  existingSavings: 0,
  monthlyContribution: 0,
  priority: 'medium',
});

export const newRecommendation = (partial: Partial<Recommendation> = {}): Recommendation => ({
  id: uid(),
  area: 'life',
  lifeAssured: 'client',
  action: 'new',
  productType: '',
  provider: '',
  productName: '',
  amount: 0,
  premiumMonthly: 0,
  premiumEscalation: 0.06,
  rationale: '',
  isReplacement: false,
  replacementChecklist: [],
  replacedPolicy: '',
  replacementReasons: '',
  replacementCostComparison: '',
  replacementConsequences: '',
  clientDecision: 'pending',
  clientReason: '',
  ...partial,
});

export const defaultPractice = (): PracticeProfile => ({
  practiceName: '',
  fspName: '',
  fspNumber: '',
  companyRegistration: '',
  adviserName: '',
  representativeNumber: '',
  adviserQualifications: '',
  adviserEmail: '',
  adviserPhone: '',
  address: '',
  website: '',
  categories: 'Category I: Long-term Insurance subcategories A, B1, B1-A, B2, B2-A, C; Retail Pension Benefits; Pension Fund Benefits; Participatory interests in Collective Investment Schemes',
  complianceOfficer: '',
  complianceOfficerContact: '',
  complaintsContact: '',
  piCover: '',
  productSuppliers: '',
  conflictPolicyUrl: '',
  keyIndividual: '',
  underSupervision: false,
  supervisor: '',
  logoDataUrl: '',
  brandColor: '#0f3d5e',
  defaultAssumptions: { ...DEFAULT_ASSUMPTIONS },
});

/** Bring older / partial documents up to the current schema without losing data. */
export const normaliseDocument = (raw: Partial<FnaDocument>): FnaDocument => {
  const base = newDocument();
  const doc: FnaDocument = {
    ...base,
    ...raw,
    client: { ...base.client, ...raw.client },
    spouse: { ...base.spouse, ...raw.spouse },
    household: { ...base.household, ...raw.household },
    income: {
      client: { ...base.income.client, ...raw.income?.client },
      spouse: { ...base.income.spouse, ...raw.income?.spouse },
    },
    expenses: { items: { ...raw.expenses?.items }, custom: raw.expenses?.custom ?? [] },
    retirement: {
      client: { ...base.retirement.client, ...raw.retirement?.client },
      spouse: { ...base.retirement.spouse, ...raw.retirement?.spouse },
    },
    estate: {
      client: { ...base.estate.client, ...raw.estate?.client },
      spouse: { ...base.estate.spouse, ...raw.estate?.spouse },
    },
    riskProfile: { ...base.riskProfile, ...raw.riskProfile },
    assumptions: { ...base.assumptions, ...raw.assumptions },
    engagement: { ...base.engagement, ...raw.engagement },
    advice: { ...base.advice, ...raw.advice },
    dependants: (raw.dependants ?? []).map((d) => ({ ...newDependant(), ...d })),
    retirementFunds: (raw.retirementFunds ?? []).map((f) => ({ ...newRetirementFund(), ...f })),
    assets: (raw.assets ?? []).map((a) => ({ ...newAsset(), ...a })),
    liabilities: (raw.liabilities ?? []).map((l) => ({ ...newLiability(), ...l })),
    policies: (raw.policies ?? []).map((p) => ({ ...newPolicy(), ...p })),
    goals: (raw.goals ?? []).map((g) => ({ ...newGoal(), ...g })),
    schemaVersion: SCHEMA_VERSION,
  };
  doc.advice.recommendations = (raw.advice?.recommendations ?? []).map((r) => newRecommendation(r));
  return doc;
};
