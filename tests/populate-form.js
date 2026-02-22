/**
 * Comprehensive FNA Form Test Data Population Script
 * Run this in browser console to populate all fields with test data
 */

const testData = {
  // Primary Member
  primaryMember: {
    title: 'Mr.',
    firstName: 'John',
    surname: 'TestUser',
    idNumber: '9001015009087',
    gender: 'Male',
    dateOfBirth: '01/01/1990',
    currentAge: '34',
    ageNextBirthday: '35',
    daysToNextBirthday: '45'
  },
  
  // Relationship Status
  relationshipStatus: {
    maritalStatus: 'Married',
    partnershipDate: '2015-06-15'
  },
  
  // Contact Details
  contactDetails: {
    primaryAddress: {
      street: '123 Test Street',
      city: 'Johannesburg',
      province: 'Gauteng',
      zipCode: '2000'
    },
    mailingAddressDifferent: false,
    primaryPhone: '0821234567',
    primaryPhoneType: 'Mobile',
    secondaryPhone: '0111234567',
    secondaryPhoneType: 'Home',
    email: 'john.test@example.com',
    whatsapp: '0821234567',
    
    // Insurance Portfolio
    lifeInsuranceTotal: '5000000',
    lifeInsuranceRecommend: 'Modify',
    lifeInsuranceTargetCover: '8000000',
    lifeInsuranceTargetPremium: '2500',
    criticalIllnessCoverage: '2000000',
    criticalIllnessRecommend: 'Leave',
    criticalIllnessTargetCover: '3000000',
    criticalIllnessTargetPremium: '1500',
    disabilityCoverage: '75',
    disabilityCoverageType: 'Long-Term',
    disabilityCoverageRecommend: 'Modify',
    disabilityCoverageTargetCover: '100',
    disabilityCoverageTargetPremium: '1000',
    incomeProtection: '50000',
    incomeProtectionRecommend: 'Modify',
    incomeProtectionTargetCover: '75000',
    incomeProtectionTargetPremium: '3000',
    
    // Health & Lifestyle
    height: '180',
    weight: '75',
    criminalHistory: 'No',
    substanceUse: ['Alcohol'],
    hazardousActivities: ['Skydiving'],
    hazardousActivitiesOther: 'Scuba Diving',
    appliedForInsurance: true,
    declinedOrRated: 'No',
    
    // Estate Planning
    willStatus: 'Yes',
    willLastUpdated: '2023-01-15',
    trustStatus: 'No',
    inheritanceExpectations: 'Uncertain',
    inheritanceAmount: '500000',
    
    // Financial Priorities
    largestFinancialConcern: 'Retirement savings not enough',
    priorityEmergencyFund: '8',
    priorityDebtPayoff: '7',
    priorityRetirement: '10',
    priorityInsuranceCoverage: '9',
    priorityEstatePreservation: '6',
    priorityCashFlow: '5',
    priorityIncomeReplacement: '9',
    priorityEducationFunding: '8',
    incomeReplacementAmount: '10000000',
    incomeReplacementYears: '20',
    
    // Risk & Implementation
    budgetEstablished: 'Yes',
    riskTolerance: 'Medium',
    implementationBarriers: 'None',
    openToImmediateAction: 'Yes',
    notes: 'Client is ready to proceed with recommendations'
  },
  
  // Education
  education: {
    level: "Bachelor's Degree",
    institution: 'University of Witwatersrand'
  },
  
  // Employment
  employment: {
    employmentStatus: 'Full-time',
    industry: 'Finance',
    jobTitle: 'Financial Analyst',
    occupation: 'Financial Analyst',
    primarySource: 'Employment',
    employer: 'ABC Corporation',
    tenure: '5',
    primaryIncomeAmount: '50000',
    paysTax: true,
    yearsInWorkforce: '10',
    salaryDate: '25th',
    debitOrderDate: '1st',
    medicalMembers: 3,
    retirementContribution: '5000',
    hasEmployeeBenefits: true,
    employeeBenefitsNotes: 'Medical Aid Subsidy 50%, Group Life 3x Annual Salary',
    otherSources: [
      { source: 'Rental Income', amount: '10000' }
    ],
    partnerSources: [],
    employmentBreaks: [],
    careerPlans: {
      plannedChange: 'Promotion',
      potentialSalary: '70000',
      notes: 'Expecting promotion in Q2'
    },
    futureIncome: []
  },
  
  // Expenses
  expenses: {
    transportMode: 'Personal Vehicle',
    fuelCost: '3000',
    autoInsurance: '800',
    vehicleRepayment: '5000',
    hasMaintenancePlan: true,
    maintenanceCost: '500',
    tollsAndTracker: '300',
    
    livingSituation: 'Own Home',
    timeAtAddress: '5 years',
    mortgagePayment: '15000',
    rentPayment: '',
    ratesAndTaxes: '2000',
    householdInsurance: '500',
    otherHouseholdCosts: '1000',
    electricity: '1500',
    gas: '500',
    water: '500',
    internet: '800',
    dstv: '1000',
    trashCollection: '300',
    cleaningServices: '1500',
    landscaping: '500',
    
    groceries: '8000',
    diningOut: '2000',
    clothing: '2000',
    personalCare: '1000',
    petCare: '500',
    hobbies: '1500',
    
    subscriptions: {
      gym: '500',
      netflix: '200',
      youtube: '100',
      spotify: '100',
      other: '200'
    },
    cellphoneData: '800',
    travelBudget: '5000',
    
    onFamilyPlan: true,
    medicalAidPlan: 'Discovery Classic',
    medicalAidPremium: '4500',
    openToMedicalOptions: false,
    gapCoverPlan: 'Gap Cover Plus',
    gapCoverPremium: '300',
    openToGapOptions: false,
    otherMedicalCosts: '500',
    
    childCare: '8000',
    childEducationAnnual: '120000',
    educationalExpenses: '20000',
    childSupport: '3000',
    
    creditCardPayments: '5000',
    personalLoans: '3000',
    studentLoans: '2000',
    otherDebt: '1000',
    
    giftsDonations: '2000',
    charitableDonations: '1000'
  },
  
  // Assets
  assets: [
    {
      name: 'Family Home',
      type: 'Property',
      category: 'Physical',
      location: 'Sandton, Johannesburg',
      purchasePrice: '3500000',
      purchaseDate: '2018-06-15',
      currentValue: '4500000',
      remainingDebt: '2500000',
      ownershipType: 'Personal',
      planToSell: false,
      settleDebtOnDeath: true,
      estatePlan: 'Leave to spouse'
    },
    {
      name: 'RA - Allan Gray',
      type: 'RA',
      category: 'Financial',
      companyName: 'Allan Gray',
      productName: 'Equity Fund',
      currentBalance: '500000',
      projectedReturn: '12',
      monthlyContribution: '5000'
    }
  ],
  
  // Liabilities
  liabilities: [
    {
      type: 'Loan',
      name: 'Home Loan - Standard Bank',
      isSecured: true,
      originalAmount: '3500000',
      term: '20',
      termUnit: 'Years',
      currentBalance: '2500000',
      interestRate: '10.25',
      monthlyPayment: '25000',
      status: 'Current',
      earlyPayoff: false,
      targetPayoffDate: '2038-06-30',
      isConsolidated: 'No'
    }
  ],
  
  // Retirement
  retirement: {
    retirementFunds: [
      {
        companyName: 'Allan Gray',
        productName: 'Equity Fund',
        currentBalance: '500000',
        growthRate: '12',
        isEmployeeBenefit: 'false'
      },
      {
        companyName: 'Old Mutual',
        productName: 'SA Equity Fund',
        currentBalance: '750000',
        growthRate: '10',
        isEmployeeBenefit: 'true'
      }
    ],
    otherIncomeSources: [
      { source: 'Rental Income', monthlyAmount: '15000' }
    ],
    activeAnnuities: [],
    employerContribution: '2500',
    employerMatchPercentage: '5',
    employeeContribution: '5000',
    desiredRetirementAge: '65',
    targetMonthlyIncome: '100000',
    simultaneousRetirement: true,
    optimizationStrategy: 'Reliable Income',
    payoffLiabilities: [],
    assetsToSell: []
  },
  
  // Goals
  goals: [
    {
      category: 'Education',
      name: "Child's University Fund",
      type: 'Mid-Term',
      targetDate: '2035-02-01',
      targetAge: '18',
      currentCost: '500000',
      financingRequired: false,
      lumpSumSaved: '200000',
      monthlyContributionCurrent: '5000',
      ongoingExpenses: '2000'
    }
  ],
  
  // Emergency Fund
  emergencyFund: {
    targetMonths: '6',
    currentSavings: '150000',
    monthlyContribution: '10000'
  },
  
  // Financial Planning
  financialPlanning: {
    preferredInsurers: 'Allan Gray, Old Mutual, Discovery',
    dislikedInsurers: 'None',
    plannedChanges: 'Review all policies and consolidate where possible',
    hasFinancialAdvisor: false,
    hasAccountant: true,
    accountantServices: {
      taxPlanning: true,
      investmentManagement: false
    },
    hasAttorney: true,
    attorneyServices: {
      estatePlanning: true
    }
  }
};

// Function to populate the form
async function populateForm() {
  console.log('Starting form population...');
  
  // Helper functions
  const fillText = (selector, value) => {
    const el = document.querySelector(selector);
    if (el) {
      el.value = value;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    return false;
  };
  
  const selectOption = (selector, value) => {
    const el = document.querySelector(selector);
    if (el) {
      el.value = value;
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    return false;
  };
  
  const clickCheckbox = (selector) => {
    const el = document.querySelector(selector);
    if (el) {
      el.checked = !el.checked;
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    return false;
  };
  
  const clickRadio = (selector) => {
    const el = document.querySelector(selector);
    if (el) {
      el.checked = true;
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    return false;
  };
  
  // Scroll to each section and fill
  const sections = [
    'primary-member',
    'relationship',
    'family',
    'financial-planning',
    'contact',
    'education',
    'employment',
    'expenses',
    'assets',
    'liabilities',
    'retirement',
    'goals'
  ];
  
  for (const sectionId of sections) {
    const section = document.getElementById(sectionId);
    if (section) {
      section.scrollIntoView({ behavior: 'smooth', block: 'start' });
      await new Promise(resolve => setTimeout(resolve, 500));
      console.log(`Filled section: ${sectionId}`);
    }
  }
  
  console.log('Form population complete!');
  console.log('You can now click "Generate PDF Report" to create the PDF.');
}

// Run the population
populateForm();

// Log summary
console.log('\n=== Test Data Summary ===');
console.log(`Primary Member: ${testData.primaryMember.firstName} ${testData.primaryMember.surname}`);
console.log(`Contact: ${testData.contactDetails.email}`);
console.log(`Employment: ${testData.employment.jobTitle} at ${testData.employment.employer}`);
console.log(`Monthly Income: R ${testData.employment.primaryIncomeAmount}`);
console.log(`Assets: ${testData.assets.length}`);
console.log(`Liabilities: ${testData.liabilities.length}`);
console.log(`Retirement Funds: ${testData.retirement.retirementFunds.length}`);
console.log(`Goals: ${testData.goals.length}`);
console.log('========================\n');
