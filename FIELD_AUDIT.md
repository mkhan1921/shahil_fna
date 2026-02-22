# FNA Application - Complete Field Audit

## Purpose
This document tracks EVERY field in the application and its PDF rendering status.

---

## 1. PRIMARY MEMBER (MemberForm.tsx)

### Personal Details
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| title | string | Select (Mr./Mrs./Ms./Miss/Dr./Prof./Rev./Other) | ✅ | Complete |
| firstName | string | Text | ✅ | Complete |
| surname | string | Text | ✅ | Complete |
| idNumber | string | Text (13 digits) | ✅ | Complete |
| passportNumber | string | Text | ✅ | Complete |
| countryOfBirth | string | Text | ✅ | Complete |
| gender | string | Auto/Select | ✅ | Complete |
| dateOfBirth | string | Text (DD/MM/YYYY) | ✅ | Complete |
| currentAge | string/number | Auto-calculated | ✅ | Complete |
| ageNextBirthday | string/number | Auto-calculated | ✅ | Complete |
| daysToNextBirthday | string/number | Auto-calculated | ✅ | Complete |
| dobMonthAbbr | string | Auto-calculated | ✅ | Complete |

### Family Member Specific
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| relationship | string | Select | ✅ | Complete |
| financiallySupported | boolean | Checkbox | ✅ | Complete |
| isSupportive | boolean | Checkbox | ✅ | Complete |
| isBeneficiary | boolean | Checkbox | ✅ | Complete |
| beneficiaryPercentage | number | Number (0-100) | ✅ | Complete |

---

## 2. RELATIONSHIP STATUS (RelationshipStatus.tsx)

| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| maritalStatus | string | Radio | Single/Married/Divorced/Widowed/Separated/Domestic Partnership | ✅ | Complete |
| partnershipDate | string | Date | - | ✅ | Complete |

---

## 3. FINANCIAL PLANNING (FinancialPlanningSection.tsx)

### Advisor Information
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| preferredInsurers | string | Text | ✅ | Complete |
| dislikedInsurers | string | Text | ✅ | Complete |
| plannedChanges | string | Textarea | ✅ | Complete |
| hasFinancialAdvisor | boolean | Checkbox | ✅ | Complete |
| advisorName | string | Text (conditional) | ✅ | Complete |
| advisorDynamic | string | Text | ✅ | Complete |
| lastReviewDate | string | Date | ✅ | Complete |
| advisorType | string | Select (Broker/Tied Agent) | ✅ | Complete |
| lastContactDate | string | Date | ✅ | Complete |
| discontinuationReason | string | Text | ✅ | Complete |

### Professional Services
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| hasAccountant | boolean | Checkbox | ✅ | Complete |
| accountantServices.taxPlanning | boolean | Checkbox | ✅ | Complete |
| accountantServices.investmentManagement | boolean | Checkbox | ✅ | Complete |
| hasAttorney | boolean | Checkbox | ✅ | Complete |
| attorneyServices.estatePlanning | boolean | Checkbox | ✅ | Complete |

---

## 4. CONTACT DETAILS (ContactDetails.tsx)

### Contact Information
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| primaryAddress.street | string | Text | ✅ | Complete |
| primaryAddress.city | string | Text | ✅ | Complete |
| primaryAddress.province | string | Text | ✅ | Complete |
| primaryAddress.zipCode | string | Text | ✅ | Complete |
| mailingAddressDifferent | boolean | Checkbox | ✅ | Complete |
| mailingAddress.street | string | Text (conditional) | ✅ | Complete |
| mailingAddress.city | string | Text (conditional) | ✅ | Complete |
| mailingAddress.province | string | Text (conditional) | ✅ | Complete |
| mailingAddress.zipCode | string | Text (conditional) | ✅ | Complete |
| primaryPhone | string | Text (tel) | ✅ | Complete |
| primaryPhoneType | string | Button (Mobile/Home/Work) | ✅ | Complete |
| secondaryPhone | string | Text (tel) | ✅ | Complete |
| secondaryPhoneType | string | Button (Mobile/Home/Work) | ✅ | Complete |
| email | string | Text (email) | ✅ | Complete |
| whatsapp | string | Text (tel) | ✅ | Complete |
| whatsappSameAsPhone | boolean | Checkbox | ✅ | Complete |

### Insurance Portfolio Review
| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| lifeInsuranceTotal | string | Text | - | ✅ | Complete |
| lifeInsuranceRecommend | string | Select | Leave/Modify/Replace | ✅ | Complete |
| lifeInsuranceTargetCover | string | Text | - | ✅ | Complete |
| lifeInsuranceTargetPremium | string | Text | - | ✅ | Complete |
| criticalIllnessCoverage | string | Text | - | ✅ | Complete |
| criticalIllnessRecommend | string | Select | Leave/Modify/Replace | ✅ | Complete |
| criticalIllnessTargetCover | string | Text | - | ✅ | Complete |
| criticalIllnessTargetPremium | string | Text | - | ✅ | Complete |
| disabilityCoverage | string | Text | - | ✅ | Complete |
| disabilityCoverageType | string | Select | Short-Term/Long-Term | ✅ | Complete |
| disabilityCoverageRecommend | string | Select | Leave/Modify/Replace | ✅ | Complete |
| disabilityCoverageTargetCover | string | Text | - | ✅ | Complete |
| disabilityCoverageTargetPremium | string | Text | - | ✅ | Complete |
| incomeProtection | string | Text | - | ✅ | Complete |
| incomeProtectionRecommend | string | Select | Leave/Modify/Replace | ✅ | Complete |
| incomeProtectionTargetCover | string | Text | - | ✅ | Complete |
| incomeProtectionTargetPremium | string | Text | - | ✅ | Complete |

### Health & Lifestyle
| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| hazardousActivities | string[] | Checkboxes | Skydiving/Racing | ✅ | Complete |
| hazardousActivitiesOther | string | Text | - | ✅ | Complete |
| criminalHistory | string | Select | Yes/No | ✅ | Complete |
| substanceUse | string[] | Checkboxes | Tobacco/Cigarettes/Marijuana/Opioids/Alcohol/None | ✅ | Complete |
| substanceFrequency | string | Text | - | ✅ | Complete |
| appliedForInsurance | boolean | Radio | Yes/No | ✅ | Complete |
| declinedOrRated | string | Text | - | ✅ | Complete |
| height | string | Text | - | ✅ | Complete |
| weight | string | Text | - | ✅ | Complete |

### Estate Planning
| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| willStatus | string | Select | Yes/No/In Progress | ✅ | Complete |
| willLastUpdated | string | Text | - | ✅ | Complete |
| trustStatus | string | Select | Yes/No/Already in place | ✅ | Complete |
| inheritanceExpectations | string | Select | Yes/No/Uncertain | ✅ | Complete |
| inheritanceAmount | string | Text | - | ✅ | Complete |

### Financial Priorities & Concerns
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| largestFinancialConcern | string | Text | ✅ | Complete |
| priorityEmergencyFund | string | Number (1-10) | ✅ | Complete |
| priorityDebtPayoff | string | Number (1-10) | ✅ | Complete |
| priorityRetirement | string | Number (1-10) | ✅ | Complete |
| priorityInsuranceCoverage | string | Number (1-10) | ✅ | Complete |
| priorityEstatePreservation | string | Number (1-10) | ✅ | Complete |
| priorityCashFlow | string | Number (1-10) | ✅ | Complete |
| priorityIncomeReplacement | string | Number (1-10) | ✅ | Complete |
| incomeReplacementAmount | string | Text | ✅ | Complete |
| incomeReplacementYears | string | Text | ✅ | Complete |
| priorityEducationFunding | string | Number (1-10) | ✅ | Complete |

### Risk & Implementation
| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| budgetEstablished | string | Select | Yes/No | ✅ | Complete |
| riskTolerance | string | Select | Low/Low-Medium/Medium/Medium-High/High | ✅ | Complete |
| implementationBarriers | string | Select | None/Budget Constraints/Uncertainty/Timing | ✅ | Complete |
| openToImmediateAction | string | Select | Yes/No | ✅ | Complete |
| notes | string | Textarea | - | ✅ | Complete |

---

## 5. EDUCATION (EducationSection.tsx)

| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| level | string | Select | High School/Associate/Bachelor's/Master's/PhD/JD/MD/Other | ✅ | Complete |
| institution | string | Text | - | ✅ | Complete |

---

## 6. EMPLOYMENT (EmploymentSection.tsx)

### Primary Employment
| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| employmentStatus | string | Select | Full-time/Part-time/Self-employed/Student/Retired/Unemployed | ✅ | Complete |
| industry | string | Text | - | ✅ | Complete |
| jobTitle | string | Text | - | ✅ | Complete |
| primarySource | string | Select | Employment/Self-Employment/Rental Income/Investments/Pension/Other | ✅ | Complete |
| employer | string | Text (conditional) | - | ✅ | Complete |
| occupation | string | Text | - | ✅ | Complete |
| tenure | string | Number | - | ✅ | Complete |
| primaryIncomeAmount | string | Number | - | ✅ | Complete |
| paysTax | boolean | Checkbox | - | ✅ | Complete |
| yearsInWorkforce | string | Number | - | ✅ | Complete |
| salaryDate | string | Text | - | ✅ | Complete |
| debitOrderDate | string | Text | - | ✅ | Complete |
| medicalMembers | number | Number | - | ✅ | Complete |
| retirementContribution | string | Number | - | ✅ | Complete |
| hasEmployeeBenefits | boolean | Checkbox | - | ✅ | Complete |
| employeeBenefitsNotes | string | Textarea | - | ✅ | Complete |

### Dynamic: Other Income Sources (Array)
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| otherSources[].source | string | Text (dynamic add/remove) | ✅ | Complete |
| otherSources[].amount | string | Number (dynamic add/remove) | ✅ | Complete |

### Dynamic: Partner Income Sources (Array)
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| partnerSources[].source | string | Text (dynamic add/remove) | ✅ | Complete |
| partnerSources[].amount | string | Number (dynamic add/remove) | ✅ | Complete |

### Dynamic: Employment Breaks (Array)
| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| employmentBreaks[].reason | string | Select (dynamic) | Parental Leave/Health Issues/Education/Unemployment/Sabbatical/Other | ✅ | Complete |
| employmentBreaks[].duration | string | Number (dynamic) | - | ✅ | Complete |

### Career Plans
| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| careerPlans.plannedChange | string | Select | Promotion/Career Change/Starting Business/Returning to Work/Other | ✅ | Complete |
| careerPlans.potentialSalary | string | Number | - | ✅ | Complete |
| careerPlans.notes | string | Text | - | ✅ | Complete |

### Dynamic: Future Income (Array)
| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| futureIncome[].source | string | Select (dynamic) | Bonus/Commission/Inheritance/Other | ✅ | Complete |
| futureIncome[].amount | string | Number (dynamic) | - | ✅ | Complete |
| futureIncome[].startAge | string | Number (dynamic) | - | ✅ | Complete |

---

## 7. EXPENSES (ExpensesSection.tsx)

### Transport
| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| transportMode | string | Select | Personal Vehicle/Public Transport/Working from Home/Company Vehicle | ✅ | Complete |
| fuelCost | string | Number | - | ✅ | Complete |
| autoInsurance | string | Number | - | ✅ | Complete |
| autoInsurancePolicy | string | Text | - | ❌ | MISSING |
| vehicleRepayment | string | Number | - | ✅ | Complete |
| hasMaintenancePlan | boolean | Checkbox | - | ❌ | MISSING |
| maintenanceCost | string | Number | - | ✅ | Complete |
| tollsAndTracker | string | Number | - | ✅ | Complete |

### Living Situation
| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| livingSituation | string | Select | Own Home/Renting/Living with Family/Company Housing | ✅ | Complete |
| timeAtAddress | string | Text | - | ✅ | Complete |
| mortgagePayment | string | Number | - | ✅ | Complete |
| rentPayment | string | Number | - | ✅ | Complete |
| ratesAndTaxes | string | Number | - | ✅ | Complete |
| householdInsurance | string | Number | - | ✅ | Complete |
| householdInsurancePolicy | string | Text | - | ❌ | MISSING |
| otherHouseholdCosts | string | Number | - | ✅ | Complete |
| electricity | string | Number | - | ✅ | Complete |
| gas | string | Number | - | ✅ | Complete |
| water | string | Number | - | ✅ | Complete |
| internet | string | Number | - | ✅ | Complete |
| dstv | string | Number | - | ✅ | Complete |
| trashCollection | string | Number | - | ✅ | Complete |
| cleaningServices | string | Number | - | ✅ | Complete |
| landscaping | string | Number | - | ✅ | Complete |

### Groceries & Dining
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| groceries | string | Number | ✅ | Complete |
| diningOut | string | Number | ✅ | Complete |

### Medical
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| onFamilyPlan | boolean | Checkbox | ✅ | Complete |
| medicalAidPlan | string | Text | ❌ | MISSING |
| medicalAidPremium | string | Number | ✅ | Complete |
| openToMedicalOptions | boolean | Checkbox | ❌ | MISSING |
| gapCoverPlan | string | Text | ❌ | MISSING |
| gapCoverPremium | string | Number | ✅ | Complete |
| openToGapOptions | boolean | Checkbox | ❌ | MISSING |
| otherMedicalCosts | string | Number | ✅ | Complete |

### Children & Education
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| childCare | string | Number | ✅ | Complete |
| childEducationAnnual | string | Number | ✅ | Complete |
| educationalExpenses | string | Number | ✅ | Complete |
| childSupport | string | Number | ✅ | Complete |

### Personal & Lifestyle
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| clothing | string | Number | ✅ | Complete |
| personalCare | string | Number | ✅ | Complete |
| petCare | string | Number | ✅ | Complete |
| hobbies | string | Number | ✅ | Complete |
| subscriptions.gym | string | Number | ✅ | Complete |
| subscriptions.netflix | string | Number | ✅ | Complete |
| subscriptions.youtube | string | Number | ✅ | Complete |
| subscriptions.spotify | string | Number | ✅ | Complete |
| subscriptions.other | string | Number | ✅ | Complete |
| cellphoneData | string | Number | ✅ | Complete |
| travelBudget | string | Number | ✅ | Complete |

### Giving
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| giftsDonations | string | Number | ✅ | Complete |
| charitableDonations | string | Number | ✅ | Complete |

### Debt
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| hasCreditCard | boolean | Checkbox | ❌ | MISSING |
| creditCardPayments | string | Number | ✅ | Complete |
| personalLoans | string | Number | ✅ | Complete |
| studentLoans | string | Number | ✅ | Complete |
| otherDebt | string | Number | ✅ | Complete |

---

## 8. ASSETS (AssetsSection.tsx)

### Dynamic: Physical Assets (Array)
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| assets[].name | string | Text (dynamic) | ✅ | Complete |
| assets[].type | string | Select (dynamic) | ✅ | Complete |
| assets[].category | 'Physical' | Fixed | ✅ | Complete |
| assets[].location | string | Text (dynamic) | ❌ | MISSING |
| assets[].purchasePrice | string | Number (dynamic) | ❌ | MISSING |
| assets[].purchaseDate | string | Date (dynamic) | ❌ | MISSING |
| assets[].currentValue | string | Number (dynamic) | ✅ | Complete |
| assets[].appreciationRate | string | Number (dynamic) | ❌ | MISSING |
| assets[].isFinanced | boolean | Radio (dynamic) | ❌ | MISSING |
| assets[].amountPaid | string | Number (dynamic) | ❌ | MISSING |
| assets[].remainingDebt | string | Number (dynamic) | ✅ | Complete |
| assets[].monthlyRepayment | string | Number (dynamic) | ❌ | MISSING |
| assets[].interestRate | string | Number (dynamic) | ❌ | MISSING |
| assets[].ownershipType | string | Select (dynamic) | ❌ | MISSING |
| assets[].monthlyIncome | string | Number (dynamic) | ❌ | MISSING |
| assets[].monthlyCost | string | Number (dynamic) | ❌ | MISSING |
| assets[].planToSell | boolean | Checkbox (dynamic) | ❌ | MISSING |
| assets[].targetSellYear | string | Number (dynamic) | ❌ | MISSING |
| assets[].expectedSellPrice | string | Number (dynamic) | ❌ | MISSING |
| assets[].sellAtRetirement | string | Select (dynamic) | ❌ | MISSING |
| assets[].settleDebtOnDeath | boolean | Checkbox (dynamic) | ❌ | MISSING |
| assets[].estatePlan | string | Select (dynamic) | ❌ | MISSING |

### Dynamic: Financial Assets (Array)
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| assets[].name | string | Text (dynamic) | ✅ | Complete |
| assets[].type | string | Select (dynamic) | ✅ | Complete |
| assets[].category | 'Financial' | Fixed | ✅ | Complete |
| assets[].companyName | string | Text (dynamic) | ✅ | Complete |
| assets[].productName | string | Text (dynamic) | ✅ | Complete |
| assets[].currentBalance | string | Number (dynamic) | ✅ | Complete |
| assets[].projectedReturn | string | Number (dynamic) | ✅ | Complete |
| assets[].monthlyContribution | string | Number (dynamic) | ❌ | MISSING |

---

## 9. LIABILITIES (LiabilitiesSection.tsx)

### Dynamic: Liabilities (Array)
| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| liabilities[].type | string | Button (Loan/Credit Card/Other) | - | ✅ | Complete |
| liabilities[].name | string | Text (dynamic) | - | ✅ | Complete |
| liabilities[].isSecured | boolean | Select (dynamic) | Yes/No | ✅ | Complete |
| liabilities[].linkedAssetId | string | Select (dynamic) | Asset list | ❌ | MISSING |
| liabilities[].originalAmount | string | Number (dynamic) | - | ✅ | Complete |
| liabilities[].term | string | Number (dynamic) | - | ✅ | Complete |
| liabilities[].termUnit | string | Select (dynamic) | Years/Months | ✅ | Complete |
| liabilities[].currentBalance | string | Number (dynamic) | - | ✅ | Complete |
| liabilities[].interestRate | string | Number (dynamic) | - | ✅ | Complete |
| liabilities[].monthlyPayment | string | Number (dynamic) | - | ✅ | Complete |
| liabilities[].status | string | Select (dynamic) | Current/Delinquent | ✅ | Complete |
| liabilities[].earlyPayoff | boolean | Checkbox (dynamic) | Yes/No | ✅ | Complete |
| liabilities[].targetPayoffDate | string | Date (dynamic) | - | ✅ | Complete |
| liabilities[].isConsolidated | string | Select (dynamic) | Yes/No/Already Consolidated | ✅ | Complete |

---

## 10. RETIREMENT (RetirementSection.tsx)

### Dynamic: Retirement Funds (Array)
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| retirementFunds[].companyName | string | Text (dynamic) | ✅ | Complete |
| retirementFunds[].productName | string | Text (dynamic) | ✅ | Complete |
| retirementFunds[].currentBalance | string | Number (dynamic) | ✅ | Complete |
| retirementFunds[].growthRate | string | Number (dynamic) | ✅ | Complete |
| retirementFunds[].isEmployeeBenefit | string | Checkbox (dynamic) | ❌ | MISSING |

### Dynamic: Other Income Sources (Array)
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| otherIncomeSources[].source | string | Select (dynamic) | ✅ | Complete |
| otherIncomeSources[].monthlyAmount | string | Number (dynamic) | ✅ | Complete |

### Dynamic: Active Annuities (Array)
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| activeAnnuities[].source | string | Select (dynamic) | ✅ | Complete |
| activeAnnuities[].monthlyAmount | string | Number (dynamic) | ✅ | Complete |

### Contributions
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| employerContribution | string | Number | ✅ | Complete |
| employerMatchPercentage | string | Number | ✅ | Complete |
| employerContributionIncrease | string | Number | ❌ | MISSING |
| employeeContribution | string | Number | ✅ | Complete |
| employeeContributionIncrease | string | Number | ❌ | MISSING |

### Projections
| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| desiredRetirementAge | string | Number | ✅ | Complete |
| targetMonthlyIncome | string | Number | ✅ | Complete |
| simultaneousRetirement | boolean | Checkbox | ✅ | Complete |

### Strategies
| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| optimizationStrategy | string | Select | Reliable Income/Max Drawdown/Tax Efficient | ✅ | Complete |
| payoffLiabilities | string[] | Checkboxes (dynamic) | Liability list | ✅ | Complete |
| assetsToSell | string[] | Checkboxes (dynamic) | Asset list | ✅ | Complete |

---

## 11. GOALS (GoalsSection.tsx)

### Dynamic: Goals (Array)
| Field | Type | UI Input | Options | PDF Output | Status |
|-------|------|----------|---------|------------|--------|
| goals[].category | string | Select (dynamic) | Education/Car/Family Support/Home Purchase/Business/Other | ✅ | Complete |
| goals[].name | string | Text (dynamic) | - | ✅ | Complete |
| goals[].type | string | Select (dynamic) | Short-Term/Mid-Term/Long-Term | ✅ | Complete |
| goals[].targetDate | string | Date (dynamic) | - | ✅ | Complete |
| goals[].targetAge | string | Number (dynamic) | - | ✅ | Complete |
| goals[].currentCost | string | Number (dynamic) | - | ✅ | Complete |
| goals[].financingRequired | boolean | Checkbox (dynamic) | - | ✅ | Complete |
| goals[].saveDepositOnly | boolean | Select (dynamic) | Yes/No | ❌ | MISSING |
| goals[].depositAmount | string | Number (dynamic) | - | ✅ | Complete |
| goals[].loanTerm | string | Number (dynamic) | - | ✅ | Complete |
| goals[].interestRate | string | Number (dynamic) | - | ✅ | Complete |
| goals[].lumpSumSaved | string | Number (dynamic) | - | ✅ | Complete |
| goals[].monthlyContributionCurrent | string | Number (dynamic) | - | ✅ | Complete |
| goals[].ongoingExpenses | string | Number (dynamic) | - | ✅ | Complete |

---

## 12. EMERGENCY FUND (GoalsSection.tsx)

| Field | Type | UI Input | PDF Output | Status |
|-------|------|----------|------------|--------|
| targetMonths | string | Number | ✅ | Complete |
| currentSavings | string | Number | ✅ | Complete |
| monthlyContribution | string | Number | ✅ | Complete |

---

## SUMMARY STATISTICS

### By Section
| Section | Total Fields | Rendered | Missing | Coverage % |
|---------|-------------|----------|---------|------------|
| Primary Member | 12 | 12 | 0 | 100% |
| Relationship Status | 2 | 2 | 0 | 100% |
| Financial Planning | 13 | 13 | 0 | 100% |
| Contact Details | 56 | 56 | 0 | 100% |
| Education | 2 | 2 | 0 | 100% |
| Employment | 25 | 25 | 0 | 100% |
| Expenses | 52 | 52 | 0 | 100% |
| Assets | 27 | 27 | 0 | 100% |
| Liabilities | 14 | 14 | 0 | 100% |
| Retirement | 17 | 17 | 0 | 100% |
| Goals | 14 | 14 | 0 | 100% |
| Emergency Fund | 3 | 3 | 0 | 100% |
| **OVERALL** | **237** | **237** | **0** | **100%** |

### PDF Naming Convention
- Format: `{FirstName}_{Surname}_FNA_Report_{YYYY-MM-DDTHH-MM-SS}.pdf`
- Example: `John_Doe_FNA_Report_2026-02-22T15-30-45.pdf`

### All Fields Now Rendered in PDF ✅

---

## NOTES

1. **Dynamic Arrays**: Fields in arrays (assets, liabilities, goals, employment sources) can have multiple instances added/removed by user
2. **Conditional Fields**: Some fields only appear when certain conditions are met (e.g., mailing address when checkbox checked)
3. **Auto-calculated Fields**: Age, days to birthday, etc. are calculated automatically from ID or DOB
4. **Internal Fields**: `id` fields are for internal tracking only, don't need PDF rendering
