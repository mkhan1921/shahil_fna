export interface RelationshipStatusData {
  maritalStatus: string;
  partnershipDate: string;
}

export interface Address {
  street: string;
  city: string;
  province: string;
  zipCode: string;
}

export interface ContactDetailsData {
  primaryAddress: Address;
  mailingAddress: Address;
  mailingAddressDifferent?: boolean;
  primaryPhone: string;
  primaryPhoneType: string;
  secondaryPhone: string;
  secondaryPhoneType: string;
  email: string;
  whatsapp: string;
  whatsappSameAsPhone?: boolean;

  // Insurance Portfolio Review
  lifeInsuranceTotal?: string;
  lifeInsuranceRecommend?: 'Leave' | 'Modify' | 'Replace';
  lifeInsuranceTargetCover?: string;
  lifeInsuranceTargetPremium?: string;

  disabilityCoverage?: string;
  disabilityCoverageRecommend?: 'Leave' | 'Modify' | 'Replace';
  disabilityCoverageTargetCover?: string;
  disabilityCoverageTargetPremium?: string;
  disabilityCoverageType?: 'Short-Term' | 'Long-Term';

  criticalIllnessCoverage?: string;
  criticalIllnessRecommend?: 'Leave' | 'Modify' | 'Replace';
  criticalIllnessTargetCover?: string;
  criticalIllnessTargetPremium?: string;

  incomeProtection?: string;
  incomeProtectionRecommend?: 'Leave' | 'Modify' | 'Replace';
  incomeProtectionTargetCover?: string;
  incomeProtectionTargetPremium?: string;

  // Health & Lifestyle
  hazardousActivities?: string[];
  hazardousActivitiesOther?: string;
  criminalHistory?: 'Yes' | 'No';
  substanceUse?: string[];
  substanceFrequency?: string;
  appliedForInsurance?: boolean;
  declinedOrRated?: string;
  height?: string;
  weight?: string;

  // Estate Planning
  willStatus?: 'Yes' | 'No' | 'In Progress';
  willLastUpdated?: string;
  trustStatus?: 'Yes' | 'No' | 'Already in place';
  inheritanceExpectations?: 'Yes' | 'No' | 'Uncertain';
  inheritanceAmount?: string;

  // Financial Priorities & Concerns
  largestFinancialConcern?: string;
  priorityEmergencyFund?: string;
  priorityDebtPayoff?: string;
  priorityRetirement?: string;
  priorityInsuranceCoverage?: string;
  priorityEstatePreservation?: string;
  priorityCashFlow?: string;
  priorityIncomeReplacement?: string;
  incomeReplacementAmount?: string;
  incomeReplacementYears?: string;
  priorityEducationFunding?: string;

  // Risk & Implementation
  budgetEstablished?: 'Yes' | 'No';
  riskTolerance?: 'Low' | 'Low-Medium' | 'Medium' | 'Medium-High' | 'High';
  implementationBarriers?: 'None' | 'Budget Constraints' | 'Uncertainty' | 'Timing';
  openToImmediateAction?: 'Yes' | 'No';
  notes?: string;
}

export interface EducationData {
  level: string;
  institution: string;
}

export interface IncomeSource {
  source: string;
  amount: string;
}

export interface CareerPlan {
  plannedChange: string;
  potentialSalary: string;
  notes: string;
}

export interface EmploymentBreak {
  reason: string;
  duration: string;
}

export interface FutureIncome {
  source: string;
  amount: string;
  startAge: string;
}

export interface EmploymentData {
  employmentStatus: string;
  industry: string;
  jobTitle: string;
  primarySource: string;
  employer: string;
  occupation: string;
  tenure: string;
  primaryIncomeAmount: string;
  paysTax: boolean;
  yearsInWorkforce: string;
  salaryDate?: string; // e.g. "25th"
  debitOrderDate?: string; // e.g. "1st"
  otherSources: IncomeSource[];
  partnerSources: IncomeSource[];
  employmentBreaks: EmploymentBreak[];
  careerPlans: CareerPlan;
  futureIncome: FutureIncome[];
  medicalMembers: number;
  retirementContribution: string;
  hasEmployeeBenefits?: boolean;
  employeeBenefitsNotes?: string;
  employerContributionIncrease?: string; // Annual % increase
  employeeContributionIncrease?: string; // Annual % increase
}

export interface FamilyMember {
  title: string;
  firstName: string;
  surname: string;
  idNumber: string;
  passportNumber: string;
  countryOfBirth: string;
  gender: string;
  dateOfBirth: string;
  currentAge: string | number;
  ageNextBirthday: string | number;
  daysToNextBirthday: string | number;
  relationship?: string;
  financiallySupported?: boolean;
  isSupportive?: boolean;
  isBeneficiary?: boolean;
  beneficiaryPercentage?: number;
  dobMonthAbbr?: string;
}

export interface FinancialPlanningData {
  preferredInsurers: string;
  dislikedInsurers: string;
  plannedChanges: string;
  hasFinancialAdvisor: boolean;
  advisorName?: string;
  advisorDynamic?: string;
  lastReviewDate?: string;
  advisorType?: 'Broker' | 'Tied Agent';
  lastContactDate?: string;
  discontinuationReason?: string;
  hasAccountant: boolean;
  accountantServices: {
    taxPlanning: boolean;
    investmentManagement: boolean;
  };
  hasAttorney: boolean;
  attorneyServices: {
    estatePlanning: boolean;
  };
}

export interface ExpensesData {
  // Transport
  transportMode: string;
  fuelCost: string;
  autoInsurance: string;
  autoInsurancePolicy?: string; // Policy Name
  vehicleRepayment: string;
  hasMaintenancePlan: boolean;
  maintenanceCost: string;
  tollsAndTracker: string;
  
  // Living
  livingSituation: string;
  timeAtAddress: string;
  
  // Groceries & Dining
  groceries: string;
  diningOut: string;
  
  // Medical
  onFamilyPlan: boolean;
  medicalAidPlan: string;
  medicalAidPremium: string;
  openToMedicalOptions: boolean;
  gapCoverPlan: string;
  gapCoverPremium: string;
  openToGapOptions: boolean;
  otherMedicalCosts: string;
  
  // Children & Education
  childCare: string;
  childEducationAnnual: string;
  educationalExpenses: string;
  
  // Household
  cleaningServices: string;
  landscaping: string;
  mortgagePayment: string;
  rentPayment: string;
  ratesAndTaxes: string;
  householdInsurance: string;
  householdInsurancePolicy?: string; // Policy Name
  otherHouseholdCosts: string;
  electricity: string;
  gas: string;
  water: string;
  internet: string;
  dstv: string;
  trashCollection: string;
  
  // Personal & Lifestyle
  clothing: string;
  personalCare: string;
  petCare: string;
  hobbies: string;
  subscriptions: {
    gym: string;
    netflix: string;
    youtube: string;
    spotify: string;
    other: string;
  };
  cellphoneData: string;
  travelBudget: string;
  
  // Giving
  giftsDonations: string;
  charitableDonations: string;
  
  // Debt
  hasCreditCard?: boolean;
  creditCardPayments: string;
  personalLoans: string;
  studentLoans: string;
  otherDebt: string;
  childSupport: string;
}

export interface Asset {
  id: string;
  name: string;
  type: string;
  category: 'Physical' | 'Financial';
  
  // Physical / Detailed Fields
  location?: string;
  purchasePrice?: string;
  purchaseDate?: string;
  currentValue?: string;
  appreciationRate?: string;
  
  // Debt / Financing
  isFinanced?: boolean; // Owned vs Financed
  amountPaid?: string; // or OutstandingBalance? User prompt says "Amount Paid" then "Remaining Debt"
  remainingDebt?: string;
  monthlyRepayment?: string;
  interestRate?: string; // Optional helper for calculation
  
  // Ownership & Income
  ownershipType?: string; // Personal/Trust/Joint/Company
  monthlyIncome?: string;
  monthlyCost?: string;
  
  // Sale & Estate
  planToSell?: boolean;
  targetSellYear?: string;
  expectedSellPrice?: string;
  sellAtRetirement?: string; // Yes/No/Unsure
  settleDebtOnDeath?: boolean;
  estatePlan?: string;
  
  // Financial / Investment Fields
  companyName?: string; // Also used as Product Name
  currentBalance?: string;
  projectedReturn?: string;
  monthlyContribution?: string;
}

export interface Liability {
  id: string;
  type: 'Loan' | 'Credit Card' | 'Other';
  name: string; // Liability Name or Lender
  isSecured: boolean; // Secured or Unsecured
  linkedAssetId?: string; // Optional link to an Asset
  
  // Loan Details
  originalAmount?: string;
  term?: string; // e.g. "20 years" or "60 months"
  termUnit?: 'Years' | 'Months';
  
  currentBalance: string;
  interestRate: string;
  monthlyPayment: string;
  
  status?: 'Current' | 'Delinquent';
  earlyPayoff?: boolean; // Yes/No
  
  // Student Loan Specific
  targetPayoffDate?: string;
  isConsolidated?: string; // Yes | No | Already Consolidated
}

export interface RetirementFund {
  id: string;
  companyName: string;
  productName: string;
  currentBalance: string;
  growthRate: string; // %
  isEmployeeBenefit?: string; // "true" | "false"
}

export interface RetirementIncome {
  id: string;
  source: string;
  monthlyAmount: string;
}

export interface RetirementData {
  // Savings
  retirementFunds: RetirementFund[];
  otherIncomeSources: RetirementIncome[];
  activeAnnuities: RetirementIncome[]; // "Draw from them"
  
  // Contributions
  employerContribution?: string; // Amount or could be calc from match
  employerMatchPercentage?: string;
  employerContributionIncrease?: string; // Annual % increase
  employeeContribution?: string;
  employeeContributionIncrease?: string; // Annual % increase
  // Total calc on fly
  
  // Projections
  desiredRetirementAge?: string;
  targetMonthlyIncome?: string; // "Target monthly income today"
  
  // Rules & Analysis
  simultaneousRetirement?: boolean;
  
  // Strategies & Liquidity events
  payoffLiabilities?: string[]; // IDs of liabilities to pay off
  assetsToSell?: string[]; // IDs of assets to sell
  optimizationStrategy?: 'Reliable Income' | 'Max Drawdown' | 'Tax Efficient';
  // Earliest age, current potential income - auto calculated
}

export interface Goal {
  id: string;
  category: string; // 'Education' | 'Car' | 'Family Support' | 'Home Purchase' | 'Business' | 'Other'
  name: string;
  type: string; // 'Short-Term' | 'Mid-Term' | 'Long-Term'
  targetDate?: string; // YYYY-MM-DD
  targetAge?: string;
  currentCost: string;
  
  // Financing
  financingRequired: boolean;
  saveDepositOnly?: boolean;
  depositAmount?: string;
  loanTerm?: string; // Months
  interestRate?: string; // %
  
  // Savings
  lumpSumSaved?: string;
  monthlyContributionCurrent?: string;
  
  ongoingExpenses?: string;
}

export interface EmergencyFund {
  targetMonths: string;
  currentSavings: string;
  monthlyContribution: string;
}

export interface FormData {
  clientName: string;
  primaryMember: FamilyMember;
  familyMembers: FamilyMember[];
  contactDetails: ContactDetailsData;
  financialPlanning: FinancialPlanningData;
  education: EducationData;
  employment: EmploymentData;
  assets: Asset[];
  liabilities: Liability[];
  retirement: RetirementData;
  goals: Goal[];
  emergencyFund: EmergencyFund;
  relationshipStatus: RelationshipStatusData;
  expenses: ExpensesData;
  lineItemNotes: Record<string, string>; // Key: sectionId-fieldId, Value: note content
}
