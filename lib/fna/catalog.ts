import type {
  AssetType,
  Beneficiary,
  DependantRelationship,
  DisabilityDefinition,
  EmploymentType,
  ExpenseCategory,
  LiabilityType,
  MaritalRegime,
  MaritalStatus,
  NeedArea,
  PolicyType,
  RecommendationAction,
  RetirementFundType,
  ScopeArea,
} from './types';

export interface ExpenseItem {
  key: string;
  label: string;
  category: ExpenseCategory;
  essential: boolean;
  hint?: string;
}

export const EXPENSE_CATEGORIES: { key: ExpenseCategory; label: string }[] = [
  { key: 'housing', label: 'Housing' },
  { key: 'transport', label: 'Transport' },
  { key: 'household', label: 'Groceries & household' },
  { key: 'communication', label: 'Connectivity & subscriptions' },
  { key: 'insurance', label: 'Short-term insurance' },
  { key: 'medical', label: 'Medical' },
  { key: 'children', label: 'Children & education' },
  { key: 'personal', label: 'Personal & lifestyle' },
  { key: 'family', label: 'Family support & giving' },
  { key: 'other', label: 'Other' },
];

/**
 * Living expenses only. Debt repayments, life/risk premiums and savings
 * contributions are captured in their own sections so nothing is counted twice.
 */
export const EXPENSE_ITEMS: ExpenseItem[] = [
  { key: 'rent', label: 'Rent', category: 'housing', essential: true, hint: 'Bond repayments are captured under Liabilities' },
  { key: 'rates', label: 'Rates & taxes', category: 'housing', essential: true },
  { key: 'levies', label: 'Levies / HOA', category: 'housing', essential: true },
  { key: 'utilities', label: 'Electricity & water', category: 'housing', essential: true },
  { key: 'security', label: 'Security / armed response', category: 'housing', essential: true },
  { key: 'domestic', label: 'Domestic worker & garden', category: 'housing', essential: false },
  { key: 'maintenance', label: 'Home maintenance', category: 'housing', essential: false },
  { key: 'backup-power', label: 'Solar / backup power', category: 'housing', essential: false },

  { key: 'fuel', label: 'Fuel', category: 'transport', essential: true, hint: 'Vehicle finance is captured under Liabilities' },
  { key: 'vehicle-maintenance', label: 'Vehicle service & tyres', category: 'transport', essential: true },
  { key: 'tolls-parking', label: 'Tolls, parking & licence', category: 'transport', essential: true },
  { key: 'tracking', label: 'Tracking device', category: 'transport', essential: false },
  { key: 'public-transport', label: 'Taxi, Uber & public transport', category: 'transport', essential: true },

  { key: 'groceries', label: 'Groceries & toiletries', category: 'household', essential: true },
  { key: 'cleaning', label: 'Cleaning & household goods', category: 'household', essential: true },

  { key: 'cellphone', label: 'Cellphone & data', category: 'communication', essential: true },
  { key: 'internet', label: 'Fibre / internet', category: 'communication', essential: true },
  { key: 'tv', label: 'DStv & streaming', category: 'communication', essential: false },
  { key: 'subscriptions', label: 'Other subscriptions', category: 'communication', essential: false },

  { key: 'home-insurance', label: 'Buildings & contents insurance', category: 'insurance', essential: true },
  { key: 'car-insurance', label: 'Vehicle insurance', category: 'insurance', essential: true },
  { key: 'other-st', label: 'Other short-term insurance', category: 'insurance', essential: false },

  { key: 'medical-aid', label: 'Medical aid (not via payroll)', category: 'medical', essential: true },
  { key: 'gap-cover', label: 'Gap cover', category: 'medical', essential: true },
  { key: 'medical-oop', label: 'Out-of-pocket medical', category: 'medical', essential: true },

  { key: 'school-fees', label: 'School fees', category: 'children', essential: true },
  { key: 'childcare', label: 'Crèche, aftercare & au pair', category: 'children', essential: true },
  { key: 'extramurals', label: 'Extra-murals & tutoring', category: 'children', essential: false },
  { key: 'school-transport', label: 'School transport', category: 'children', essential: true },
  { key: 'school-other', label: 'Uniforms, books & stationery', category: 'children', essential: true },

  { key: 'clothing', label: 'Clothing', category: 'personal', essential: false },
  { key: 'personal-care', label: 'Personal care & grooming', category: 'personal', essential: false },
  { key: 'gym', label: 'Gym & sport', category: 'personal', essential: false },
  { key: 'dining', label: 'Eating out & entertainment', category: 'personal', essential: false },
  { key: 'holidays', label: 'Holidays (monthly provision)', category: 'personal', essential: false },
  { key: 'hobbies', label: 'Hobbies', category: 'personal', essential: false },
  { key: 'pets', label: 'Pets', category: 'personal', essential: false },

  { key: 'family-support', label: 'Support to parents / extended family', category: 'family', essential: true },
  { key: 'maintenance-paid', label: 'Maintenance / child support paid', category: 'family', essential: true },
  { key: 'donations', label: 'Tithes & donations', category: 'family', essential: false },
  { key: 'gifts', label: 'Gifts & celebrations', category: 'family', essential: false },

  { key: 'bank-fees', label: 'Bank fees', category: 'other', essential: true },
  { key: 'other', label: 'Other', category: 'other', essential: false },
];

export const MARITAL_STATUS: Record<MaritalStatus, string> = {
  single: 'Single',
  married: 'Married',
  'life-partner': 'Life partner',
  divorced: 'Divorced',
  widowed: 'Widowed',
};

export const MARITAL_REGIME: Record<MaritalRegime, string> = {
  icop: 'In community of property',
  'anc-accrual': 'Out of community — with accrual',
  'anc-no-accrual': 'Out of community — without accrual',
  customary: 'Customary marriage',
  foreign: 'Foreign marriage / other',
  na: 'Not applicable',
};

export const EMPLOYMENT_TYPE: Record<EmploymentType, string> = {
  salaried: 'Salaried employee',
  'self-employed': 'Self-employed / business owner',
  commission: 'Commission earner',
  contract: 'Contractor',
  retired: 'Retired',
  unemployed: 'Not working',
  student: 'Student',
};

export const RELATIONSHIP: Record<DependantRelationship, string> = {
  child: 'Child',
  parent: 'Parent',
  sibling: 'Sibling',
  other: 'Other dependant',
};

export const RETIREMENT_FUND_TYPE: Record<RetirementFundType, string> = {
  pension: 'Pension fund',
  provident: 'Provident fund',
  'retirement-annuity': 'Retirement annuity',
  'preservation-pension': 'Pension preservation fund',
  'preservation-provident': 'Provident preservation fund',
  'living-annuity': 'Living annuity',
  'life-annuity': 'Guaranteed (life) annuity',
};

export const PRE_RETIREMENT_FUNDS: RetirementFundType[] = [
  'pension',
  'provident',
  'retirement-annuity',
  'preservation-pension',
  'preservation-provident',
];

export const ASSET_TYPE: Record<AssetType, string> = {
  'primary-residence': 'Primary residence',
  'residential-property': 'Other residential property',
  'commercial-property': 'Commercial property',
  vehicle: 'Vehicle',
  'personal-effects': 'Household contents & personal effects',
  cash: 'Bank / savings account',
  'money-market': 'Money market / notice deposit',
  'unit-trust': 'Unit trusts (discretionary)',
  shares: 'Shares / ETFs',
  tfsa: 'Tax-free savings account',
  endowment: 'Endowment / sinking fund',
  offshore: 'Offshore investments',
  'business-interest': 'Business interest',
  'loan-account': 'Loan account',
  crypto: 'Crypto assets',
  other: 'Other asset',
};

export interface AssetBehaviour {
  /** Can be realised within ~30 days without major loss. */
  liquid: boolean;
  /** Personal-use asset excluded from CGT (8th Schedule para 53). */
  cgtExempt: boolean;
  group: 'property' | 'lifestyle' | 'liquid' | 'investment' | 'business';
}

export const ASSET_BEHAVIOUR: Record<AssetType, AssetBehaviour> = {
  'primary-residence': { liquid: false, cgtExempt: false, group: 'property' },
  'residential-property': { liquid: false, cgtExempt: false, group: 'property' },
  'commercial-property': { liquid: false, cgtExempt: false, group: 'property' },
  vehicle: { liquid: false, cgtExempt: true, group: 'lifestyle' },
  'personal-effects': { liquid: false, cgtExempt: true, group: 'lifestyle' },
  cash: { liquid: true, cgtExempt: true, group: 'liquid' },
  'money-market': { liquid: true, cgtExempt: true, group: 'liquid' },
  'unit-trust': { liquid: true, cgtExempt: false, group: 'investment' },
  shares: { liquid: true, cgtExempt: false, group: 'investment' },
  tfsa: { liquid: true, cgtExempt: true, group: 'investment' },
  endowment: { liquid: true, cgtExempt: true, group: 'investment' },
  offshore: { liquid: true, cgtExempt: false, group: 'investment' },
  'business-interest': { liquid: false, cgtExempt: false, group: 'business' },
  'loan-account': { liquid: false, cgtExempt: true, group: 'business' },
  crypto: { liquid: true, cgtExempt: false, group: 'investment' },
  other: { liquid: false, cgtExempt: false, group: 'lifestyle' },
};

export const LIABILITY_TYPE: Record<LiabilityType, string> = {
  'home-loan': 'Home loan / bond',
  'vehicle-finance': 'Vehicle finance',
  'personal-loan': 'Personal loan',
  'credit-card': 'Credit card',
  'store-card': 'Store / retail account',
  overdraft: 'Overdraft',
  'student-loan': 'Student loan',
  'business-loan': 'Business loan / surety',
  tax: 'SARS liability',
  other: 'Other debt',
};

export const UNSECURED_DEBT: LiabilityType[] = ['personal-loan', 'credit-card', 'store-card', 'overdraft', 'other'];

export const POLICY_TYPE: Record<PolicyType, string> = {
  life: 'Life cover',
  'disability-lump': 'Disability — lump sum',
  'income-protection': 'Income protection',
  'severe-illness': 'Severe / critical illness',
  funeral: 'Funeral cover',
  'family-income': 'Family income benefit / spouse pension',
};

export const BENEFICIARY: Record<Beneficiary, string> = {
  estate: 'Estate (no nomination)',
  spouse: 'Spouse / partner',
  children: 'Children',
  trust: 'Trust',
  other: 'Other nominee',
  business: 'Business (buy-and-sell / key person)',
};

export const DISABILITY_DEFINITION: Record<DisabilityDefinition, string> = {
  'own-occupation': 'Own occupation',
  'suited-occupation': 'Suited occupation',
  'any-occupation': 'Any occupation',
  functional: 'Functional impairment',
  'activities-of-daily-living': 'Activities of daily living',
};

export const NEED_AREA: Record<NeedArea, string> = {
  life: 'Life cover (death)',
  disability: 'Disability — capital',
  'income-protection': 'Income protection',
  'severe-illness': 'Severe illness',
  funeral: 'Funeral',
  estate: 'Estate liquidity & planning',
  retirement: 'Retirement',
  education: 'Education funding',
  emergency: 'Emergency fund',
  investment: 'Investments',
  debt: 'Debt management',
  medical: 'Medical aid & gap cover',
  other: 'Other',
};

export const ACTION: Record<RecommendationAction, string> = {
  new: 'New',
  increase: 'Increase',
  reduce: 'Reduce',
  replace: 'Replace',
  cancel: 'Cancel',
  maintain: 'Maintain',
  restructure: 'Restructure',
  refer: 'Refer to specialist',
};

export const SCOPE_AREA: Record<ScopeArea, string> = {
  risk: 'Risk protection (life, disability, illness)',
  retirement: 'Retirement planning',
  investment: 'Investments & savings',
  estate: 'Estate planning',
  education: 'Education funding',
  debt: 'Debt & cash-flow management',
  medical: 'Medical aid & gap cover',
  'short-term': 'Short-term insurance',
};

export const PROVINCES = [
  'Eastern Cape',
  'Free State',
  'Gauteng',
  'KwaZulu-Natal',
  'Limpopo',
  'Mpumalanga',
  'North West',
  'Northern Cape',
  'Western Cape',
];

export const TITLES = ['Mr', 'Mrs', 'Ms', 'Miss', 'Dr', 'Prof', 'Adv', 'Rev'];

export const EDUCATION_LEVELS = [
  'Below matric',
  'Matric / NSC',
  'Certificate / diploma',
  "Bachelor's degree",
  'Honours / postgraduate diploma',
  "Master's degree",
  'Doctorate',
  'Professional qualification (CA, CFP®, etc.)',
];
