import { describe, expect, it } from 'vitest';
import { analyse, computeEstate, computeIncome } from '@/lib/fna/analysis';
import { newAsset, newDependant, newDocument, newLiability, newPolicy, newRetirementFund } from '@/lib/fna/defaults';
import { conveyancingGuidelineFee, mastersFee } from '@/lib/fna/estate';
import {
  annuityFactor,
  fvEscalatingContributions,
  loanRepayment,
  monthsToRepay,
  pvGrowingAnnuity,
  pvGrowingMonthlyIncome,
  realRate,
  requiredEscalatingSaving,
} from '@/lib/fna/finance';
import { ageOn, luhnValid, parseSaId } from '@/lib/fna/idNumber';
import { scoreRiskProfile } from '@/lib/fna/riskProfile';
import { sampleDocument } from '@/lib/fna/sample';
import {
  applyBrackets,
  estateDuty,
  getTaxTable,
  incomeTax,
  medicalTaxCredit,
  personTax,
  retirementDeduction,
  retirementLumpSumTax,
  withdrawalLumpSumTax,
} from '@/lib/fna/tax';

const T27 = getTaxTable('2026/27');
const T26 = getTaxTable('2025/26');
const NOW = new Date('2026-09-29T12:00:00');

describe('SARS income tax', () => {
  it('reproduces the SARS 2026/27 worked example (R500,000, under 65, 2 medical members)', () => {
    const tax = applyBrackets(500_000, T27.brackets);
    expect(tax).toBeCloseTo(116_237, 0);
    expect(incomeTax(500_000, 40, T27)).toBeCloseTo(98_417, 0);
    expect(incomeTax(500_000, 40, T27) - medicalTaxCredit(2, T27)).toBeCloseTo(89_393, 0);
  });

  it('bracket bases reconcile for both tax years', () => {
    for (const table of [T26, T27]) {
      for (let i = 1; i < table.brackets.length; i++) {
        const prev = table.brackets[i - 1];
        const cur = table.brackets[i];
        expect(prev.base + (cur.from - prev.from) * prev.rate).toBeCloseTo(cur.base, 0);
      }
    }
  });

  it('tax thresholds produce zero tax after rebates', () => {
    expect(incomeTax(T27.thresholds.under65, 40, T27)).toBeLessThan(1);
    expect(incomeTax(T27.thresholds.age65to74, 70, T27)).toBeLessThan(1);
    expect(incomeTax(T27.thresholds.age75plus, 80, T27)).toBeLessThan(1);
    expect(incomeTax(T26.thresholds.under65, 40, T26)).toBeLessThan(1);
    expect(incomeTax(T27.thresholds.under65 + 1000, 40, T27)).toBeGreaterThan(0);
  });

  it('medical tax credits follow the s6A schedule', () => {
    expect(medicalTaxCredit(0, T27)).toBe(0);
    expect(medicalTaxCredit(1, T27)).toBe(376 * 12);
    expect(medicalTaxCredit(2, T27)).toBe(752 * 12);
    expect(medicalTaxCredit(4, T27)).toBe((752 + 2 * 254) * 12);
  });

  it('caps the s11F retirement deduction at 27.5% and the annual cap', () => {
    expect(retirementDeduction(100_000, 300_000, T27)).toBeCloseTo(82_500);
    expect(retirementDeduction(20_000, 300_000, T27)).toBe(20_000);
    expect(retirementDeduction(900_000, 5_000_000, T27)).toBe(430_000);
    expect(retirementDeduction(900_000, 5_000_000, T26)).toBe(350_000);
  });

  it('computes PAYE and UIF for an employee', () => {
    const r = personTax(
      {
        age: 40,
        grossMonthly: 50_000,
        annualBonus: 0,
        otherTaxableMonthly: 0,
        retirementContributionsAnnual: 45_000,
        employerRetirementAnnual: 0,
        medicalSchemeMembers: 1,
        uifApplies: true,
      },
      T27,
    );
    expect(r.taxable).toBe(555_000);
    const expected = 125_599 + (555_000 - 530_200) * 0.36 - 17_820 - 376 * 12;
    expect(r.annualTax).toBeCloseTo(expected, 0);
    expect(r.monthlyUif).toBeCloseTo(177.12, 2);
    expect(r.marginalRate).toBe(0.36);
  });
});

describe('Lump sum tax and estate duty', () => {
  it('taxes a R1m retirement lump sum at R101,700', () => {
    expect(retirementLumpSumTax(1_000_000, T27)).toBeCloseTo(101_700, 0);
    expect(retirementLumpSumTax(550_000, T27)).toBe(0);
  });

  it('aggregates prior lump sums', () => {
    expect(retirementLumpSumTax(500_000, T27, 500_000)).toBeCloseTo(applyBrackets(1_000_000, T27.retirementLumpSum), 0);
  });

  it('applies the withdrawal table', () => {
    expect(withdrawalLumpSumTax(27_500, T27)).toBe(0);
    expect(withdrawalLumpSumTax(726_000, T27)).toBeCloseTo(125_730, 0);
  });

  it('charges estate duty at 20% then 25%', () => {
    expect(estateDuty(6_000_000 - 3_500_000, T27)).toBe(500_000);
    expect(estateDuty(40_000_000, T27)).toBe(30_000_000 * 0.2 + 10_000_000 * 0.25);
  });

  it("uses the 2018 Master's fee tariff", () => {
    expect(mastersFee(200_000)).toBe(0);
    expect(mastersFee(350_000)).toBe(600);
    expect(mastersFee(1_000_000)).toBe(600 + 6 * 200);
    expect(mastersFee(10_000_000)).toBe(7_000);
  });

  it('matches the LSSA 2026 conveyancing guideline points', () => {
    expect(conveyancingGuidelineFee(1_000_000)).toBe(26_275);
    expect(conveyancingGuidelineFee(2_000_000)).toBe(36_875);
    expect(conveyancingGuidelineFee(3_000_000)).toBe(47_475);
    expect(conveyancingGuidelineFee(5_000_000)).toBe(68_675);
    expect(conveyancingGuidelineFee(10_000_000)).toBe(95_375);
  });
});

describe('Time value of money', () => {
  it('matches the growing annuity-due test vector (R240k p.a., 15 yrs, 9% / 5%)', () => {
    expect(pvGrowingAnnuity(240_000, 15, 0.09, 0.05, true)).toBeGreaterThan(2_800_000);
    expect(pvGrowingAnnuity(240_000, 15, 0.09, 0.05, true)).toBeLessThan(2_815_000);
  });

  it('monthly escalating income PV equals the level annuity at the real rate', () => {
    const r = Math.pow(1 + realRate(0.09, 0.05), 1 / 12) - 1;
    const expected = 10_000 * annuityFactor(r, 120) * (1 + r);
    expect(pvGrowingMonthlyIncome(10_000, 10, 0.09, 0.05)).toBeCloseTo(expected, 4);
  });

  it('inverts escalating contributions exactly', () => {
    const monthly = requiredEscalatingSaving(1_000_000, 15, 0.085, 0.055);
    expect(fvEscalatingContributions(monthly, 15, 0.085, 0.055)).toBeCloseTo(1_000_000, 2);
  });

  it('amortises loans consistently', () => {
    const pmt = loanRepayment(1_000_000, 0.1075, 240);
    expect(monthsToRepay(1_000_000, 0.1075, pmt)).toBe(240);
    expect(monthsToRepay(100_000, 0.2, 1_000)).toBe(Infinity);
  });
});

describe('SA ID numbers', () => {
  it('validates the Luhn check and extracts details', () => {
    expect(luhnValid('8001015009087')).toBe(true);
    const r = parseSaId('8001015009087', NOW);
    expect(r).toMatchObject({ valid: true, dateOfBirth: '1980-01-01', gender: 'male', citizenship: 'SA citizen' });
  });

  it('rejects typos and impossible dates', () => {
    expect(parseSaId('8001015009088', NOW).valid).toBe(false);
    expect(parseSaId('8002305009087', NOW).valid).toBe(false);
    expect(parseSaId('12345', NOW).valid).toBe(false);
  });

  it('resolves the century and gender', () => {
    expect(parseSaId('8909030456085', NOW)).toMatchObject({ valid: true, dateOfBirth: '1989-09-03', gender: 'female' });
    expect(ageOn('1986-04-12', NOW)).toBe(40);
    expect(ageOn('1986-10-12', NOW)).toBe(39);
  });
});

describe('Estate calculation', () => {
  it('applies executor fees, abatement and duty for a single person', () => {
    const doc = newDocument();
    doc.client.dateOfBirth = '1960-01-01';
    const house = newAsset('client');
    Object.assign(house, { type: 'residential-property', value: 3_000_000, bequeathToSpouse: false });
    const cash = newAsset('client');
    Object.assign(cash, { type: 'cash', value: 1_000_000, bequeathToSpouse: false });
    doc.assets = [house, cash];
    const income = computeIncome(doc, 'client', T27, NOW);
    const e = computeEstate(doc, 'client', income, T27, false);
    expect(e.grossEstate).toBe(4_000_000);
    expect(e.executorFees).toBeCloseTo(4_000_000 * 0.035 * 1.15, 2);
    expect(e.cgt).toBe(0);
    const expectedDutiable = e.netEstate - 3_500_000;
    expect(e.dutiable).toBeCloseTo(expectedDutiable, 2);
    expect(e.estateDuty).toBeCloseTo(expectedDutiable * 0.2, 2);
  });

  it('excludes nominated-beneficiary policies from the gross estate but includes them as deemed property', () => {
    const doc = newDocument();
    const p = newPolicy('client');
    Object.assign(p, { type: 'life', cover: 2_000_000, beneficiary: 'children' });
    const q = newPolicy('client');
    Object.assign(q, { type: 'life', cover: 500_000, beneficiary: 'estate' });
    doc.policies = [p, q];
    const income = computeIncome(doc, 'client', T27, NOW);
    const e = computeEstate(doc, 'client', income, T27, false);
    expect(e.grossEstate).toBe(500_000);
    expect(e.deemedProperty).toBe(2_000_000);
    expect(e.cashAvailable).toBe(500_000);
  });

  it('attributes half the joint estate when married in community of property', () => {
    const doc = newDocument();
    doc.household = { ...doc.household, maritalStatus: 'married', maritalRegime: 'icop', includeSpouse: true };
    const a = newAsset('client');
    Object.assign(a, { type: 'unit-trust', value: 1_000_000 });
    doc.assets = [a];
    const income = computeIncome(doc, 'client', T27, NOW);
    const e = computeEstate(doc, 'client', income, T27, true);
    expect(e.grossEstate).toBe(500_000);
  });

  it('charges CGT on death above the R440,000 exclusion and rolls over spousal bequests', () => {
    const doc = newDocument();
    doc.client.dateOfBirth = '1970-01-01';
    const shares = newAsset('client');
    Object.assign(shares, { type: 'shares', value: 2_000_000, baseCost: 500_000, bequeathToSpouse: false });
    doc.assets = [shares];
    const income = computeIncome(doc, 'client', T27, NOW);
    const e = computeEstate(doc, 'client', income, T27, false);
    expect(e.capitalGains).toBe(1_500_000);
    // taxable gain = (1.5m - 440k) * 40% = 424,000 taxed at the margin from zero income.
    expect(e.cgt).toBeCloseTo(incomeTax(424_000, 56, T27), 0);

    doc.household = { ...doc.household, maritalStatus: 'married', maritalRegime: 'anc-no-accrual', includeSpouse: true };
    shares.bequeathToSpouse = true;
    const e2 = computeEstate(doc, 'client', income, T27, true);
    expect(e2.cgt).toBe(0);
    expect(e2.estateDuty).toBe(0);
  });

  it('excludes the primary residence gain up to the exclusion', () => {
    const doc = newDocument();
    const home = newAsset('client');
    Object.assign(home, { type: 'primary-residence', value: 4_000_000, baseCost: 1_500_000, bequeathToSpouse: false });
    doc.assets = [home];
    const income = computeIncome(doc, 'client', T27, NOW);
    const e = computeEstate(doc, 'client', income, T27, false);
    expect(e.capitalGains).toBe(0); // gain 2.5m < 3m exclusion
  });
});

describe('Full analysis of the sample household', () => {
  const doc = sampleDocument();
  const a = analyse(doc, NOW);

  it('analyses both spouses', () => {
    expect(a.people).toEqual(['client', 'spouse']);
    expect(a.persons.client?.income.age).toBe(40);
    expect(a.persons.spouse?.income.age).toBe(37);
  });

  it('produces a sane cash flow', () => {
    expect(a.cashflow.takeHome).toBeGreaterThan(80_000);
    expect(a.cashflow.takeHome).toBeLessThan(110_000);
    expect(Number.isFinite(a.cashflow.surplus)).toBe(true);
  });

  it('identifies life cover and income protection needs', () => {
    const c = a.persons.client!;
    expect(c.death.need).toBeGreaterThan(c.death.provision * 0.5);
    expect(c.incomeProtection.need).toBeGreaterThan(40_000);
    expect(c.incomeProtection.provision).toBe(50_000);
    expect(c.deathIncomeYears).toBeGreaterThan(10);
  });

  it('flags the missing will and guardian', () => {
    expect(a.findings.some((f) => f.title.includes('no confirmed valid will'))).toBe(true);
    expect(a.findings.some((f) => f.title.includes('guardian'))).toBe(true);
  });

  it('projects retirement with a coherent timeline', () => {
    const r = a.persons.client!.retirement;
    expect(r.projectedCapital).toBeGreaterThan(r.currentCapital);
    expect(r.requiredCapital).toBeGreaterThan(0);
    expect(r.timeline[0].capital).toBeCloseTo(r.currentCapital, 0);
    const atRet = r.timeline.find((t) => t.phase === 'retirement');
    expect(atRet).toBeDefined();
    if (r.shortfall > 0) {
      expect(r.additionalMonthly).toBeGreaterThan(0);
    }
  });

  it('computes education funding per child', () => {
    expect(a.education).toHaveLength(2);
    const neo = a.education.find((e) => e.name.startsWith('Neo'))!;
    expect(neo.yearsToTertiary).toBe(8);
    expect(neo.schedule).toHaveLength(4);
    expect(neo.capitalAtStart).toBeGreaterThan(4 * 150_000);
  });

  it('scores the risk profile', () => {
    expect(a.risk.complete).toBe(true);
    expect(a.risk.profile).not.toBeNull();
  });

  it('is complete except for advice', () => {
    expect(a.completenessScore).toBeGreaterThan(0.8);
  });
});

describe('Edge cases', () => {
  it('handles an empty document without NaN', () => {
    const a = analyse(newDocument(), NOW);
    const json = JSON.stringify(a);
    expect(json.includes('NaN')).toBe(false);
    expect(json.includes('null')).toBe(true);
  });

  it('handles a retiree drawing a living annuity', () => {
    const doc = newDocument();
    doc.client.dateOfBirth = '1955-01-01';
    doc.client.employmentType = 'retired';
    const la = newRetirementFund('client');
    Object.assign(la, { type: 'living-annuity', value: 3_000_000, drawdownRate: 0.05 });
    doc.retirementFunds = [la];
    doc.retirement.client = { ...doc.retirement.client, targetMode: 'amount', targetMonthly: 20_000 };
    const a = analyse(doc, NOW);
    const r = a.persons.client!.retirement;
    expect(r.retired).toBe(true);
    expect(r.yearsToRetirement).toBe(0);
    expect(r.currentCapital).toBe(3_000_000);
    expect(Number.isFinite(r.requiredCapital)).toBe(true);
  });

  it('does not count credit-life debts in the death need', () => {
    const doc = newDocument();
    const l = newLiability('client');
    Object.assign(l, { balance: 500_000, creditLifeCover: true });
    doc.liabilities = [l];
    const a = analyse(doc, NOW);
    expect(a.persons.client!.estate.liabilities).toBe(0);
  });

  it('keeps the risk profile at the lower of tolerance and capacity', () => {
    const r = scoreRiskProfile({ horizon: 0, 'income-stability': 0, emergency: 0, proportion: 0, knowledge: 4, objective: 4, drawdown: 4, tradeoff: 4 });
    expect(r.profile?.key).toBe('conservative');
    expect(r.mismatch).toBe(true);
  });
});

describe('Regression tests from the calculation audit', () => {
  const base = () => {
    const doc = newDocument();
    doc.client.dateOfBirth = '1980-06-15';
    doc.income.client.grossMonthly = 100_000;
    return doc;
  };

  it('capitalises the actual support for parents instead of extending full income replacement', () => {
    const doc = base();
    doc.dependants = [{ ...newDependant(), relationship: 'parent', dateOfBirth: '1955-01-01', monthlySupport: 3_500, supportYears: 15, tertiary: false, schoolType: 'none' }];
    const a = analyse(doc, NOW);
    const death = a.persons.client!.death;
    const support = death.needLines.find((l) => l.label.startsWith('Support for parents'))!;
    expect(support.amount).toBeGreaterThan(400_000);
    expect(support.amount).toBeLessThan(700_000);
    expect(a.persons.client!.deathIncomeYears).toBe(0);
  });

  it('starts drawdown today for someone who retired early', () => {
    const doc = newDocument();
    doc.client.dateOfBirth = '1971-09-01';
    doc.client.employmentType = 'retired';
    const la = newRetirementFund('client');
    Object.assign(la, { type: 'living-annuity', value: 5_000_000 });
    doc.retirementFunds = [la];
    doc.retirement.client = { ...doc.retirement.client, targetMode: 'amount', targetMonthly: 30_000, planningAge: 92 };
    const r = analyse(doc, NOW).persons.client!.retirement;
    expect(r.retired).toBe(true);
    expect(r.yearsInRetirement).toBeGreaterThan(36);
    expect(r.retirementAge).toBe(55);
    expect(r.depletionAge!).toBeLessThan(80);
  });

  it('gives retirees no income protection, disability or severe illness need', () => {
    const doc = newDocument();
    doc.client.dateOfBirth = '1954-01-01';
    doc.client.employmentType = 'retired';
    doc.income.client.otherTaxableMonthly = 30_000;
    const a = analyse(doc, NOW);
    const p = a.persons.client!;
    expect(p.incomeProtection.need).toBe(0);
    expect(p.disability.need).toBe(0);
    expect(p.severeIllness.need).toBe(0);
    expect(a.findings.some((f) => f.area === 'income-protection')).toBe(false);
  });

  it('respects "settle on death" for debts', () => {
    const doc = base();
    const l = newLiability('client');
    Object.assign(l, { balance: 1_000_000, settleOnDeath: false });
    doc.liabilities = [l];
    const e = analyse(doc, NOW).persons.client!.estate;
    expect(e.liabilities).toBe(0);
  });

  it('never produces an infinite saving requirement just before retirement', () => {
    const doc = newDocument();
    doc.client.dateOfBirth = '1961-10-05';
    doc.income.client.grossMonthly = 50_000;
    const a = analyse(doc, NOW);
    expect(Number.isFinite(a.persons.client!.retirement.additionalMonthly)).toBe(true);
    expect(JSON.stringify(a).includes('Infinity')).toBe(false);
  });

  it('applies the secondary rebate based on age at the end of the tax year', () => {
    const doc = newDocument();
    doc.client.dateOfBirth = '1961-12-01';
    doc.income.client.grossMonthly = 40_000;
    const inc = computeIncome(doc, 'client', T27, NOW);
    expect(inc.age).toBe(64);
    expect(inc.taxAge).toBe(65);
    expect(inc.tax.rebates).toBe(T27.rebates.primary + T27.rebates.secondary);
  });

  it('counts funeral cover as provision against the funeral cost in the death need', () => {
    const doc = base();
    const f = newPolicy('client');
    Object.assign(f, { type: 'funeral', cover: 50_000 });
    doc.policies = [f];
    const death = analyse(doc, NOW).persons.client!.death;
    expect(death.provisionLines.find((l) => l.label === 'Funeral cover')?.amount).toBe(50_000);
  });

  it('reports a lump sum for a child already studying and uses education-earmarked assets', () => {
    const doc = base();
    doc.dependants = [{ ...newDependant(), name: 'Student', dateOfBirth: '2006-03-01', tertiaryStartAge: 19, tertiaryYears: 4, tertiaryCostAnnual: 150_000 }];
    const a1 = analyse(doc, NOW).education[0];
    expect(a1.yearsToTertiary).toBe(0);
    expect(a1.lumpSumRequired).toBeGreaterThan(0);
    const ed = newAsset('client');
    Object.assign(ed, { type: 'unit-trust', value: 1_000_000, earmark: 'education' });
    doc.assets = [ed];
    const a2 = analyse(doc, NOW).education[0];
    expect(a2.lumpSumRequired).toBe(0);
    expect(a2.status).toBe('covered');
  });

  it('excludes credit-life debt from the deceased side of the accrual calculation', () => {
    const doc = sampleDocument();
    const withCl = analyse(doc, NOW).persons.client!.estate.accrualPayable;
    doc.liabilities = doc.liabilities.map((l) => ({ ...l, creditLifeCover: false }));
    const without = analyse(doc, NOW).persons.client!.estate.accrualPayable;
    expect(withCl).toBeGreaterThan(without);
  });
});
