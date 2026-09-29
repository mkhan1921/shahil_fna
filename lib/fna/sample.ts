/**
 * A fictional sample household used for demos, onboarding and tests.
 * Any resemblance to real persons is coincidental.
 */
import { newAsset, newDependant, newDocument, newGoal, newLiability, newPolicy, newRetirementFund } from './defaults';
import type { FnaDocument } from './types';

export const sampleDocument = (): FnaDocument => {
  const doc = newDocument();
  doc.status = 'in-progress';
  doc.engagement = {
    ...doc.engagement,
    meetingType: 'in-person',
    adviceType: 'comprehensive',
    disclosureLetterProvided: true,
    popiaConsent: true,
    popiaConsentDate: doc.engagement.meetingDate,
    specialInfoConsent: true,
    ficaVerified: true,
    conflictsDisclosed: true,
    remunerationDisclosed: true,
    reasonForAdvice: 'Annual review requested after the birth of their second child and Thabo’s promotion.',
    clientObjectives:
      'Make sure the family can stay in the home and the children can finish their education if something happens to either of us. Retire comfortably at 65. Understand whether our wills and estate are in order.',
    clientConcerns: 'Existing cover was taken out years ago and may be too low. Lerato has no will.',
  };

  doc.client = {
    ...doc.client,
    title: 'Mr',
    firstName: 'Thabo',
    surname: 'Mokoena',
    idNumber: '8604125123084',
    dateOfBirth: '1986-04-12',
    gender: 'male',
    occupation: 'Financial manager',
    employer: 'Highveld Logistics (Pty) Ltd',
    employmentType: 'salaried',
    educationLevel: 'Professional qualification (CA, CFP®, etc.)',
    email: 'thabo@example.co.za',
    mobile: '082 555 0142',
  };
  doc.spouse = {
    ...doc.spouse,
    title: 'Mrs',
    firstName: 'Lerato',
    surname: 'Mokoena',
    idNumber: '8909030456085',
    dateOfBirth: '1989-09-03',
    gender: 'female',
    occupation: 'HR business partner',
    employer: 'Brightwater Retail Group',
    employmentType: 'salaried',
    educationLevel: "Honours / postgraduate diploma",
    email: 'lerato@example.co.za',
    mobile: '083 555 0199',
  };
  doc.household = {
    ...doc.household,
    maritalStatus: 'married',
    maritalRegime: 'anc-accrual',
    dateOfMarriage: '2014-11-22',
    includeSpouse: true,
    physicalAddress: '14 Acacia Crescent, Carlswald',
    city: 'Midrand',
    province: 'Gauteng',
    postalCode: '1685',
  };

  const neo = newDependant();
  Object.assign(neo, {
    name: 'Neo Mokoena',
    dateOfBirth: '2015-02-18',
    gender: 'male',
    schoolType: 'private',
    schoolFeesAnnual: 98_000,
    tertiary: true,
    tertiaryStartAge: 19,
    tertiaryYears: 4,
    tertiaryCostAnnual: 150_000,
    educationSavings: 85_000,
    educationSavingsMonthly: 1_500,
  });
  const kagiso = newDependant();
  Object.assign(kagiso, {
    name: 'Kagiso Mokoena',
    dateOfBirth: '2019-07-07',
    gender: 'female',
    schoolType: 'public',
    schoolFeesAnnual: 34_000,
    tertiary: true,
    tertiaryStartAge: 19,
    tertiaryYears: 3,
    tertiaryCostAnnual: 150_000,
    educationSavings: 0,
    educationSavingsMonthly: 0,
  });
  const mother = newDependant();
  Object.assign(mother, {
    name: 'Mmabatho Mokoena',
    relationship: 'parent',
    dateOfBirth: '1958-06-30',
    gender: 'female',
    dependencyEndAge: 90,
    monthlySupport: 3_500,
    supportYears: 15,
    schoolType: 'none',
    tertiary: false,
    tertiaryCostAnnual: 0,
  });
  doc.dependants = [neo, kagiso, mother];

  doc.income.client = {
    ...doc.income.client,
    grossMonthly: 100_000,
    annualBonus: 100_000,
    medicalAidPayrollMonthly: 9_850,
    medicalSchemeMembers: 4,
    otherPayrollDeductionsMonthly: 0,
  };
  doc.income.spouse = {
    ...doc.income.spouse,
    grossMonthly: 55_000,
    annualBonus: 27_500,
    medicalSchemeMembers: 0,
  };

  doc.expenses.items = {
    rates: 1_450,
    utilities: 3_400,
    security: 550,
    domestic: 2_600,
    maintenance: 600,
    fuel: 4_800,
    'vehicle-maintenance': 600,
    'tolls-parking': 500,
    tracking: 250,
    groceries: 9_000,
    cleaning: 800,
    cellphone: 1_300,
    internet: 799,
    tv: 450,
    subscriptions: 250,
    'home-insurance': 1_150,
    'car-insurance': 2_200,
    'gap-cover': 480,
    'medical-oop': 600,
    'school-fees': 10_950,
    childcare: 1_500,
    extramurals: 800,
    'school-other': 400,
    clothing: 700,
    'personal-care': 600,
    gym: 500,
    dining: 1_000,
    holidays: 800,
    'family-support': 3_500,
    donations: 500,
    'bank-fees': 350,
  };

  const home = newAsset('joint');
  Object.assign(home, {
    type: 'primary-residence',
    description: 'Family home, Carlswald',
    value: 2_850_000,
    baseCost: 1_950_000,
  });
  const car1 = newAsset('client');
  Object.assign(car1, { type: 'vehicle', description: 'Toyota Fortuner 2.8 GD-6', value: 520_000 });
  const car2 = newAsset('spouse');
  Object.assign(car2, { type: 'vehicle', description: 'VW Polo 1.0 TSI', value: 245_000 });
  const contents = newAsset('joint');
  Object.assign(contents, { type: 'personal-effects', description: 'Household contents', value: 350_000 });
  const cheque = newAsset('joint');
  Object.assign(cheque, { type: 'cash', description: 'Joint cheque & savings', provider: 'Bank', value: 48_000 });
  const emergency = newAsset('client');
  Object.assign(emergency, {
    type: 'money-market',
    description: 'Emergency fund',
    provider: 'Money market',
    value: 95_000,
    earmark: 'emergency',
    monthlyContribution: 1_000,
  });
  const ut = newAsset('client');
  Object.assign(ut, {
    type: 'unit-trust',
    description: 'Balanced unit trust portfolio',
    provider: 'LISP platform',
    value: 410_000,
    baseCost: 280_000,
    monthlyContribution: 1_000,
  });
  const tfsa = newAsset('spouse');
  Object.assign(tfsa, {
    type: 'tfsa',
    description: 'Tax-free savings account',
    provider: 'LISP platform',
    value: 168_000,
    baseCost: 140_000,
    monthlyContribution: 1_500,
  });
  doc.assets = [home, car1, car2, contents, cheque, emergency, ut, tfsa];

  const pf = newRetirementFund('client');
  Object.assign(pf, {
    type: 'pension',
    provider: 'Employer umbrella fund',
    name: 'Highveld Logistics Pension Fund',
    value: 1_320_000,
    savingsPot: 38_000,
    employeeMonthly: 7_500,
    employerMonthly: 10_000,
    viaPayroll: true,
    beneficiariesNominated: true,
  });
  const ra = newRetirementFund('client');
  Object.assign(ra, {
    type: 'retirement-annuity',
    provider: 'Retirement annuity provider',
    name: 'Retirement annuity',
    value: 315_000,
    savingsPot: 9_000,
    employeeMonthly: 2_500,
    employerMonthly: 0,
    viaPayroll: false,
    beneficiariesNominated: false,
  });
  const spf = newRetirementFund('spouse');
  Object.assign(spf, {
    type: 'provident',
    provider: 'Employer umbrella fund',
    name: 'Brightwater Provident Fund',
    value: 560_000,
    savingsPot: 21_000,
    employeeMonthly: 4_125,
    employerMonthly: 5_500,
    viaPayroll: true,
    beneficiariesNominated: true,
  });
  const pres = newRetirementFund('spouse');
  Object.assign(pres, {
    type: 'preservation-pension',
    provider: 'Preservation fund',
    name: 'Pension preservation fund',
    value: 184_000,
    employeeMonthly: 0,
    employerMonthly: 0,
    viaPayroll: false,
    beneficiariesNominated: false,
  });
  doc.retirementFunds = [pf, ra, spf, pres];

  const bond = newLiability('joint');
  Object.assign(bond, {
    type: 'home-loan',
    lender: 'Bank',
    description: 'Home loan',
    balance: 1_850_000,
    interestRate: 0.1075,
    monthlyRepayment: 18_800,
    linkedAssetId: home.id,
  });
  const vaf = newLiability('client');
  Object.assign(vaf, {
    type: 'vehicle-finance',
    lender: 'Bank',
    description: 'Fortuner finance',
    balance: 335_000,
    interestRate: 0.1225,
    monthlyRepayment: 7_900,
    linkedAssetId: car1.id,
    creditLifeCover: true,
  });
  const cc = newLiability('client');
  Object.assign(cc, { type: 'credit-card', lender: 'Bank', description: 'Credit card', balance: 32_000, interestRate: 0.2075, monthlyRepayment: 1_600 });
  const pl = newLiability('spouse');
  Object.assign(pl, { type: 'personal-loan', lender: 'Bank', description: 'Personal loan', balance: 48_000, interestRate: 0.235, monthlyRepayment: 2_150 });
  doc.liabilities = [bond, vaf, cc, pl];

  const life1 = newPolicy('client');
  Object.assign(life1, {
    type: 'life',
    insurer: 'Life insurer A',
    policyNumber: 'L-40211873',
    cover: 2_500_000,
    beneficiary: 'spouse',
    premiumMonthly: 985,
    premiumEscalation: 0.08,
    coverEscalation: 0.05,
    inceptionDate: '2015-03-01',
  });
  const si1 = newPolicy('client');
  Object.assign(si1, {
    type: 'severe-illness',
    insurer: 'Life insurer A',
    policyNumber: 'L-40211873',
    cover: 500_000,
    accelerated: true,
    premiumMonthly: 465,
    inceptionDate: '2015-03-01',
  });
  const gl1 = newPolicy('client');
  Object.assign(gl1, {
    type: 'life',
    isGroup: true,
    insurer: 'Employer group scheme',
    cover: 3_600_000,
    beneficiary: 'spouse',
    notes: '3 × annual salary',
  });
  const gip1 = newPolicy('client');
  Object.assign(gip1, {
    type: 'income-protection',
    isGroup: true,
    insurer: 'Employer group scheme',
    monthlyBenefit: 50_000,
    waitingPeriodMonths: 3,
    benefitToAge: 65,
    notes: '50% of salary to age 65',
  });
  const life2 = newPolicy('spouse');
  Object.assign(life2, {
    type: 'life',
    insurer: 'Life insurer B',
    policyNumber: 'B-7730019',
    cover: 1_000_000,
    beneficiary: 'estate',
    premiumMonthly: 410,
    premiumEscalation: 0.06,
    inceptionDate: '2017-08-01',
  });
  const gl2 = newPolicy('spouse');
  Object.assign(gl2, {
    type: 'life',
    isGroup: true,
    insurer: 'Employer group scheme',
    cover: 1_320_000,
    beneficiary: 'spouse',
    notes: '2 × annual salary',
  });
  const fun = newPolicy('spouse');
  Object.assign(fun, { type: 'funeral', insurer: 'Funeral insurer', cover: 30_000, premiumMonthly: 185 });
  doc.policies = [life1, si1, gl1, gip1, life2, gl2, fun];

  doc.retirement.client = { ...doc.retirement.client, retirementAge: 65, planningAge: 90, targetMode: 'percent', targetPercent: 0.75 };
  doc.retirement.spouse = { ...doc.retirement.spouse, retirementAge: 63, planningAge: 95, targetMode: 'percent', targetPercent: 0.75 };

  doc.estate.client = {
    ...doc.estate.client,
    hasWill: 'yes',
    willDate: '2016-05-10',
    willLocation: 'Attorney’s safe custody',
    executor: 'Bank trust company',
    guardianNominated: 'no',
    accrualCommencementValue: 250_000,
    funeralCost: 50_000,
  };
  doc.estate.spouse = {
    ...doc.estate.spouse,
    hasWill: 'no',
    guardianNominated: 'no',
    accrualCommencementValue: 120_000,
    funeralCost: 50_000,
  };

  const holiday = newGoal();
  Object.assign(holiday, { name: 'Family holiday overseas', cost: 120_000, targetYear: new Date().getFullYear() + 2, existingSavings: 15_000, monthlyContribution: 1_500, priority: 'low' });
  const car = newGoal();
  Object.assign(car, { owner: 'spouse', name: 'Replace Polo (cash deposit)', cost: 150_000, targetYear: new Date().getFullYear() + 3, existingSavings: 20_000, monthlyContribution: 0, priority: 'medium' });
  doc.goals = [holiday, car];

  doc.riskProfile.answers = { horizon: 4, 'income-stability': 3, emergency: 2, proportion: 2, knowledge: 2, objective: 3, drawdown: 2, tradeoff: 2 };

  return doc;
};
