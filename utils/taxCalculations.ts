export const TAX_YEAR_2025 = {
  brackets: [
    { limit: 251800, rate: 0.18, base: 0 },
    { limit: 393600, rate: 0.26, base: 45324 },
    { limit: 545000, rate: 0.31, base: 82192 },
    { limit: 715900, rate: 0.36, base: 129126 },
    { limit: 911850, rate: 0.39, base: 190650 },
    { limit: 1963250, rate: 0.41, base: 267070.5 },
    { limit: Infinity, rate: 0.45, base: 698144.5 }
  ],
  rebates: {
    primary: 18075,
    secondary: 9913, // 65+
    tertiary: 3312   // 75+
  },
  thresholds: {
    under65: 100417,
    age65to75: 155483,
    age75plus: 173883
  },
  medicalCredits: {
    mainMember: 388,
    firstDependent: 388,
    additionalDependent: 262
  }
};

export const calculateTax = (annualIncome: number, age: number, medicalMembers: number = 0) => {
  if (!annualIncome || annualIncome <= 0) return { taxBeforeRebates: 0, rebate: 0, medicalCredit: 0, finalTax: 0, netIncome: 0 };

  // 1. Calculate Tax on Income
  let tax = 0;
  for (let i = 0; i < TAX_YEAR_2025.brackets.length; i++) {
    const bracket = TAX_YEAR_2025.brackets[i];
    if (annualIncome <= bracket.limit) {
      const prevLimit = i > 0 ? TAX_YEAR_2025.brackets[i - 1].limit : 0;
      tax = bracket.base + (annualIncome - prevLimit) * bracket.rate;
      break;
    }
  }

  // 2. Calculate Rebates
  let rebate = TAX_YEAR_2025.rebates.primary;
  if (age >= 65) rebate += TAX_YEAR_2025.rebates.secondary;
  if (age >= 75) rebate += TAX_YEAR_2025.rebates.tertiary;

  // 3. Calculate Medical Credits (Monthly * 12)
  let medicalCredit = 0;
  if (medicalMembers > 0) {
    medicalCredit += TAX_YEAR_2025.medicalCredits.mainMember;
    if (medicalMembers > 1) {
      medicalCredit += TAX_YEAR_2025.medicalCredits.firstDependent;
    }
    if (medicalMembers > 2) {
      medicalCredit += (medicalMembers - 2) * TAX_YEAR_2025.medicalCredits.additionalDependent;
    }
    medicalCredit *= 12; // Annualize
  }

  // 4. Final Tax Payable
  let finalTax = tax - rebate - medicalCredit;
  if (finalTax < 0) finalTax = 0;

  return {
    taxBeforeRebates: tax,
    rebate,
    medicalCredit,
    finalTax,
    netIncome: annualIncome - finalTax
  };
};
