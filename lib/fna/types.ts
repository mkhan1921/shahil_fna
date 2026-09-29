/**
 * FNA document model.
 *
 * All money values are stored as numbers in Rand. Monthly values are suffixed
 * `Monthly`; everything else is a lump sum or annual as named. Rates are
 * decimals (0.065 = 6.5%).
 */

export type PersonKey = 'client' | 'spouse';
export type Owner = PersonKey | 'joint';

export type Gender = 'male' | 'female' | '';

export type EmploymentType =
  | 'salaried'
  | 'self-employed'
  | 'commission'
  | 'contract'
  | 'retired'
  | 'unemployed'
  | 'student';

export interface Person {
  title: string;
  firstName: string;
  surname: string;
  idNumber: string;
  passportNumber: string;
  nationality: string;
  dateOfBirth: string; // ISO yyyy-mm-dd
  gender: Gender;
  smoker: boolean;
  healthNotes: string;
  hazardousPursuits: string;
  occupation: string;
  employer: string;
  employmentType: EmploymentType;
  educationLevel: string;
  email: string;
  mobile: string;
  taxNumber: string;
}

export type MaritalStatus = 'single' | 'married' | 'life-partner' | 'divorced' | 'widowed';

export type MaritalRegime =
  | 'icop' // in community of property
  | 'anc-accrual' // antenuptial contract with accrual
  | 'anc-no-accrual' // antenuptial contract excluding accrual
  | 'customary'
  | 'foreign'
  | 'na';

export interface Household {
  maritalStatus: MaritalStatus;
  maritalRegime: MaritalRegime;
  dateOfMarriage: string;
  /** Whether the spouse / partner is included in the analysis. */
  includeSpouse: boolean;
  physicalAddress: string;
  city: string;
  province: string;
  postalCode: string;
}

export type DependantRelationship = 'child' | 'parent' | 'sibling' | 'other';
export type SchoolType = 'none' | 'public' | 'private';

export interface Dependant {
  id: string;
  name: string;
  relationship: DependantRelationship;
  dateOfBirth: string;
  gender: Gender;
  /** Age at which financial dependency ends (children: typically 22–25). */
  dependencyEndAge: number;
  /** Monthly support (for non-child dependants, e.g. parents). */
  monthlySupport: number;
  /** For non-child dependants: number of years support is required. */
  supportYears: number;
  schoolType: SchoolType;
  /** Current annual school fees (today's money), incl. extra costs. */
  schoolFeesAnnual: number;
  tertiary: boolean;
  tertiaryStartAge: number;
  tertiaryYears: number;
  /** Annual tertiary cost in today's money (tuition + books + residence). */
  tertiaryCostAnnual: number;
  /** Savings already earmarked for this dependant's education. */
  educationSavings: number;
  /** Monthly contribution already going towards education savings. */
  educationSavingsMonthly: number;
  specialNeeds: boolean;
  notes: string;
}

export interface Income {
  /** Monthly gross basic salary incl. fixed taxable allowances. */
  grossMonthly: number;
  /** Annual bonus / 13th cheque / performance bonus. */
  annualBonus: number;
  /** Other taxable monthly income (rental, commission, freelance, annuity). */
  otherTaxableMonthly: number;
  /** Non-taxable monthly income (e.g. income protection benefits, maintenance). */
  nonTaxableMonthly: number;
  /** Medical scheme contribution deducted via payroll (monthly). */
  medicalAidPayrollMonthly: number;
  /** Number of people on this person's medical scheme (for tax credits). 0 = none. */
  medicalSchemeMembers: number;
  /** Other payroll deductions (union, group risk, staff loans…). */
  otherPayrollDeductionsMonthly: number;
  /** Optional override of PAYE (monthly) as per payslip. */
  payeOverrideMonthly: number | null;
  /** Whether UIF applies (employees). */
  uifApplies: boolean;
}

export type RetirementFundType =
  | 'pension'
  | 'provident'
  | 'retirement-annuity'
  | 'preservation-pension'
  | 'preservation-provident'
  | 'living-annuity'
  | 'life-annuity';

export interface RetirementFund {
  id: string;
  owner: PersonKey;
  type: RetirementFundType;
  provider: string;
  name: string;
  /** Total current fund value (all components). */
  value: number;
  /** Two-pot savings component balance (included in `value`). */
  savingsPot: number;
  /** Member contribution per month. */
  employeeMonthly: number;
  /** Employer contribution per month (retirement funding portion). */
  employerMonthly: number;
  /** True if the member contribution is deducted via payroll. */
  viaPayroll: boolean;
  /** Beneficiaries nominated (s37C still gives trustees discretion). */
  beneficiariesNominated: boolean;
  /** For living annuities: current annual drawdown rate. */
  drawdownRate: number;
  notes: string;
}

export type AssetType =
  | 'primary-residence'
  | 'residential-property'
  | 'commercial-property'
  | 'vehicle'
  | 'personal-effects'
  | 'cash'
  | 'money-market'
  | 'unit-trust'
  | 'shares'
  | 'tfsa'
  | 'endowment'
  | 'offshore'
  | 'business-interest'
  | 'loan-account'
  | 'crypto'
  | 'other';

export type Earmark = 'general' | 'emergency' | 'retirement' | 'education' | 'goal';

export interface Asset {
  id: string;
  owner: Owner;
  type: AssetType;
  description: string;
  provider: string;
  value: number;
  /** Base cost for Capital Gains Tax (0 = unknown → treated as no gain). */
  baseCost: number;
  monthlyContribution: number;
  earmark: Earmark;
  /** Monthly income produced (e.g. rental), already included in income if taxable. */
  monthlyIncome: number;
  /** Whether this asset passes to the surviving spouse on death. */
  bequeathToSpouse: boolean;
  notes: string;
}

export type LiabilityType =
  | 'home-loan'
  | 'vehicle-finance'
  | 'personal-loan'
  | 'credit-card'
  | 'store-card'
  | 'overdraft'
  | 'student-loan'
  | 'business-loan'
  | 'tax'
  | 'other';

export interface Liability {
  id: string;
  owner: Owner;
  type: LiabilityType;
  lender: string;
  description: string;
  balance: number;
  interestRate: number;
  monthlyRepayment: number;
  linkedAssetId: string;
  /** Settled by a credit life / bond protection policy on death. */
  creditLifeCover: boolean;
  /** Settle from capital on death (default true). */
  settleOnDeath: boolean;
  /** Settle from capital on disability (default true). */
  settleOnDisability: boolean;
}

export type PolicyType =
  | 'life'
  | 'disability-lump'
  | 'income-protection'
  | 'severe-illness'
  | 'funeral'
  | 'family-income';

export type Beneficiary = 'estate' | 'spouse' | 'children' | 'trust' | 'other' | 'business';

export type DisabilityDefinition = 'own-occupation' | 'suited-occupation' | 'any-occupation' | 'functional' | 'activities-of-daily-living';

export interface Policy {
  id: string;
  lifeAssured: PersonKey;
  type: PolicyType;
  insurer: string;
  policyNumber: string;
  /** Employer group scheme (approved/unapproved). */
  isGroup: boolean;
  /** Lump sum cover amount (life, disability, severe illness, funeral). */
  cover: number;
  /** Monthly benefit (income protection, family income). */
  monthlyBenefit: number;
  /** Family income benefit: number of years the income is paid. */
  benefitTermYears: number;
  /** Income protection: waiting period in months. */
  waitingPeriodMonths: number;
  /** Income protection: benefit payable until this age (0 = temporary only). */
  benefitToAge: number;
  beneficiary: Beneficiary;
  /** For disability policies. */
  definition: DisabilityDefinition;
  /** Severe illness / disability accelerated from the life cover. */
  accelerated: boolean;
  premiumMonthly: number;
  premiumEscalation: number;
  coverEscalation: number;
  inceptionDate: string;
  notes: string;
}

export type ExpenseCategory =
  | 'housing'
  | 'transport'
  | 'household'
  | 'communication'
  | 'insurance'
  | 'medical'
  | 'children'
  | 'personal'
  | 'family'
  | 'other';

export interface CustomExpense {
  id: string;
  category: ExpenseCategory;
  label: string;
  amount: number;
  essential: boolean;
}

export interface Expenses {
  /** Predefined line items (see EXPENSE_ITEMS); monthly amounts. */
  items: Record<string, number>;
  custom: CustomExpense[];
}

export type IncomeTargetMode = 'percent' | 'amount';

export interface RetirementGoal {
  retirementAge: number;
  /** Age the plan must fund income to. */
  planningAge: number;
  targetMode: IncomeTargetMode;
  /** % of current gross income required in retirement. */
  targetPercent: number;
  /** Required monthly income in today's money (if targetMode = amount). */
  targetMonthly: number;
  /** Other income in retirement in today's money (rental, spouse, etc.). */
  otherIncomeMonthly: number;
}

export interface OtherGoal {
  id: string;
  owner: Owner;
  name: string;
  /** Cost in today's money. */
  cost: number;
  targetYear: number;
  existingSavings: number;
  monthlyContribution: number;
  priority: 'high' | 'medium' | 'low';
}

export interface EstateProfile {
  hasWill: 'yes' | 'no' | 'unsure';
  willDate: string;
  willLocation: string;
  executor: string;
  guardianNominated: 'yes' | 'no' | 'na';
  hasTrust: boolean;
  trustNotes: string;
  /** Extra monthly income the family would need on death (e.g. childcare), today's money. */
  additionalIncomeNeedMonthly: number;
  /** Other lump sums required on death (e.g. business debt, bequest to charity). */
  additionalCapitalNeed: number;
  /** Unused abatement ported from a predeceased spouse (s4A). */
  portedAbatement: number;
  /** Specific cash bequests to be paid from the estate. */
  cashBequests: number;
  /** For ANC with accrual: net estate value at commencement (CPI-adjusted). */
  accrualCommencementValue: number;
  /** Funeral cost estimate (lump sum). */
  funeralCost: number;
  notes: string;
}

export interface Assumptions {
  taxYear: string;
  cpi: number;
  salaryEscalation: number;
  preRetirementReturn: number;
  postRetirementReturn: number;
  /** Return earned on capital invested to provide for dependants. */
  riskCapitalReturn: number;
  educationInflation: number;
  /** Required family income on death as % of the deceased's net income. */
  deathIncomeReplacement: number;
  /** Income protection target as % of net income. */
  incomeProtectionTarget: number;
  /** Lump sum for home/vehicle modification and medical costs on disability. */
  disabilityAdjustments: number;
  /** Include retirement contributions lost on disability. */
  disabilityIncludeRetirement: boolean;
  /** Severe illness need in months of gross income. */
  severeIllnessMonths: number;
  /** Emergency fund in months of essential expenses. */
  emergencyMonths: number;
  funeralAdult: number;
  funeralChild: number;
  executorFeeRate: number;
  vatRate: number;
  /** Income need on death runs until: */
  deathIncomeTerm: 'auto' | 'youngest-independent' | 'spouse-retirement' | 'fixed';
  deathIncomeFixedYears: number;
}

export type ScopeArea =
  | 'risk'
  | 'retirement'
  | 'investment'
  | 'estate'
  | 'education'
  | 'debt'
  | 'medical'
  | 'short-term';

export interface Engagement {
  meetingDate: string;
  meetingType: 'in-person' | 'virtual' | 'telephonic';
  adviceType: 'comprehensive' | 'limited';
  scope: ScopeArea[];
  limitations: string;
  disclosureLetterProvided: boolean;
  popiaConsent: boolean;
  popiaConsentDate: string;
  specialInfoConsent: boolean;
  ficaVerified: boolean;
  clientDeclinedFna: boolean;
  declineReason: string;
  conflictsDisclosed: boolean;
  remunerationDisclosed: boolean;
  /** Politically exposed / domestic prominent influential person (FICA). */
  pepStatus: 'no' | 'yes' | 'unknown';
  marketingOptIn: boolean;
  /** Copy of the record of advice provided to the client (GCC s9(2)). */
  copyProvided: boolean;
  reasonForAdvice: string;
  clientObjectives: string;
  clientConcerns: string;
  otherAdvisers: string;
}

export type NeedArea =
  | 'life'
  | 'disability'
  | 'income-protection'
  | 'severe-illness'
  | 'funeral'
  | 'estate'
  | 'retirement'
  | 'education'
  | 'emergency'
  | 'investment'
  | 'debt'
  | 'medical'
  | 'other';

export type RecommendationAction =
  | 'new'
  | 'increase'
  | 'reduce'
  | 'replace'
  | 'cancel'
  | 'maintain'
  | 'restructure'
  | 'refer';

export type ClientDecision = 'pending' | 'accepted' | 'declined' | 'deferred';

export interface Recommendation {
  id: string;
  area: NeedArea;
  lifeAssured: PersonKey;
  action: RecommendationAction;
  productType: string;
  provider: string;
  productName: string;
  amount: number;
  premiumMonthly: number;
  /** Annual premium escalation — drives the GCC s7(1)(c)(xiv) premium pattern. */
  premiumEscalation: number;
  rationale: string;
  isReplacement: boolean;
  /** GCC s8(1)(d)(i)–(ix) replacement factors discussed with the client. */
  replacementChecklist: string[];
  replacedPolicy: string;
  replacementReasons: string;
  replacementCostComparison: string;
  replacementConsequences: string;
  clientDecision: ClientDecision;
  clientReason: string;
}

export interface AdviceRecord {
  summary: string;
  recommendations: Recommendation[];
  productsConsidered: string;
  clientDeviations: string;
  feesAndCommission: string;
  conflictsOfInterest: string;
  nextReviewDate: string;
  clientSignedAt: string;
  adviserSignedAt: string;
}

export interface RiskProfile {
  answers: Record<string, number>;
  notes: string;
}

export interface FnaDocument {
  id: string;
  schemaVersion: number;
  createdAt: string;
  updatedAt: string;
  status: 'draft' | 'in-progress' | 'advice-given' | 'archived';
  client: Person;
  spouse: Person;
  household: Household;
  dependants: Dependant[];
  income: Record<PersonKey, Income>;
  expenses: Expenses;
  retirementFunds: RetirementFund[];
  assets: Asset[];
  liabilities: Liability[];
  policies: Policy[];
  retirement: Record<PersonKey, RetirementGoal>;
  goals: OtherGoal[];
  estate: Record<PersonKey, EstateProfile>;
  riskProfile: RiskProfile;
  assumptions: Assumptions;
  engagement: Engagement;
  advice: AdviceRecord;
  notes: string;
}

export interface PracticeProfile {
  practiceName: string;
  fspName: string;
  fspNumber: string;
  companyRegistration: string;
  adviserName: string;
  representativeNumber: string;
  adviserQualifications: string;
  adviserEmail: string;
  adviserPhone: string;
  address: string;
  website: string;
  categories: string;
  complianceOfficer: string;
  complianceOfficerContact: string;
  complaintsContact: string;
  piCover: string;
  productSuppliers: string;
  conflictPolicyUrl: string;
  keyIndividual: string;
  underSupervision: boolean;
  supervisor: string;
  logoDataUrl: string;
  brandColor: string;
  defaultAssumptions: Assumptions;
}
