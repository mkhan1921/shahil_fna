/**
 * Draft recommendations from the analysis. These are starting points for the
 * adviser, who must confirm suitability, choose products and edit rationale.
 */
import type { Analysis } from './analysis';
import { UNSECURED_DEBT } from './catalog';
import { newRecommendation } from './defaults';
import { getTaxTable, retirementDeduction } from './tax';
import type { FnaDocument, PersonKey, Recommendation } from './types';

const roundUp = (n: number, step: number) => Math.ceil(n / step) * step;
const r = (n: number) => `R${Math.round(n).toLocaleString('en-ZA')}`;

export const draftRecommendations = (doc: FnaDocument, a: Analysis): Recommendation[] => {
  const out: Recommendation[] = [];
  const table = getTaxTable(doc.assumptions.taxYear);

  for (const key of a.people) {
    const p = a.persons[key];
    if (!p) continue;
    const name = p.name.split(' ')[0];
    const personal = doc.policies.filter((x) => x.lifeAssured === key && !x.isGroup);
    const has = (t: string) => personal.some((x) => x.type === t);

    if (p.death.shortfall > 25_000) {
      const estateNote =
        p.estate.liquidityShortfall > 0
          ? ` At least ${r(p.estate.liquidityShortfall)} should be payable to the estate (or to an heir who undertakes to fund it) to meet the estate liquidity shortfall without selling assets.`
          : '';
      out.push(
        newRecommendation({
          area: 'life',
          lifeAssured: key,
          action: has('life') ? 'increase' : 'new',
          productType: 'Life cover',
          amount: roundUp(p.death.shortfall, 50_000),
          rationale: `The capital needed if ${name} dies is ${r(p.death.need)} against existing provision of ${r(p.death.provision)}. The shortfall of ${r(p.death.shortfall)} would leave debts, estate costs, education and ${p.deathIncomeYears} years of family income (${r(p.deathIncomeMonthly)} p.m. in today’s money) unfunded.${estateNote} Group cover ends when employment ends, so personally owned cover is preferred.`,
        }),
      );
    }

    if (p.incomeProtection.shortfall > 500) {
      const months = a.ratios.emergencyMonths;
      const waiting = months >= 3 ? '3-month' : months >= 1 ? '1-month' : '7- or 14-day';
      out.push(
        newRecommendation({
          area: 'income-protection',
          lifeAssured: key,
          action: has('income-protection') ? 'increase' : 'new',
          productType: 'Income protection (to age 65, CPI-linked)',
          amount: roundUp(p.incomeProtection.shortfall, 500),
          rationale: `${name}’s after-tax income is ${r(p.income.afterTaxMonthly)} p.m. and existing income protection to retirement is ${r(p.incomeProtection.provision)} p.m. A benefit of ${r(roundUp(p.incomeProtection.shortfall, 500))} p.m. restores cover to ${Math.round(doc.assumptions.incomeProtectionTarget * 100)}% of after-tax income. Benefits are tax-free. A ${waiting} waiting period is suggested given an emergency fund of ${months.toFixed(1)} months.`,
        }),
      );
    }

    if (p.disability.shortfall > 25_000) {
      out.push(
        newRecommendation({
          area: 'disability',
          lifeAssured: key,
          action: has('disability-lump') ? 'increase' : 'new',
          productType: 'Lump-sum disability (own/suited occupation)',
          amount: roundUp(p.disability.shortfall, 50_000),
          rationale: `If ${name} became permanently disabled, ${r(p.disability.need)} would be needed to settle debt, adapt the home and vehicle, and replace retirement contributions that stop. Existing lump-sum disability cover is ${r(p.disability.provision)}.`,
        }),
      );
    }

    if (p.severeIllness.shortfall > 25_000) {
      out.push(
        newRecommendation({
          area: 'severe-illness',
          lifeAssured: key,
          action: has('severe-illness') ? 'increase' : 'new',
          productType: 'Severe illness cover (comprehensive, severity-based)',
          amount: roundUp(p.severeIllness.shortfall, 50_000),
          rationale: `A severe illness benefit of about ${doc.assumptions.severeIllnessMonths} months’ gross income (${r(p.severeIllness.need)}) funds treatment not covered by medical aid, recovery time and lifestyle adjustments. Existing cover is ${r(p.severeIllness.provision)}.`,
        }),
      );
    }

    if (p.funeral.shortfall > 2_000) {
      out.push(
        newRecommendation({
          area: 'funeral',
          lifeAssured: key,
          action: has('funeral') ? 'increase' : 'new',
          productType: 'Funeral cover',
          amount: roundUp(p.funeral.shortfall, 5_000),
          rationale: `Bank accounts are frozen on death. Funeral cover pays within 48 hours to meet funeral costs of about ${r(p.funeral.need)}.`,
        }),
      );
    }

    const ret = p.retirement;
    if (!ret.retired && ret.additionalMonthly > 100) {
      const monthly = roundUp(ret.additionalMonthly, 100);
      const inc = p.income;
      const current = (inc.retirementPayrollMonthly + inc.retirementOwnMonthly + inc.retirementEmployerMonthly) * 12;
      const headroom = Math.max(0, retirementDeduction(current + monthly * 12, inc.tax.remuneration, table) - retirementDeduction(current, inc.tax.remuneration, table));
      const deductible = Math.min(monthly * 12, headroom);
      const afterTax = monthly - (deductible * inc.tax.marginalRate) / 12;
      out.push(
        newRecommendation({
          area: 'retirement',
          lifeAssured: key,
          action: 'new',
          productType: 'Retirement annuity (Regulation 28 compliant)',
          amount: monthly,
          premiumMonthly: monthly,
          premiumEscalation: doc.assumptions.salaryEscalation,
          rationale: `Projected retirement capital funds ${Math.round(ret.funded * 100)}% of the capital needed for ${r(ret.targetMonthlyToday)} p.m. (today’s money) from age ${ret.retirementAge}. An additional ${r(monthly)} p.m., escalating at ${(doc.assumptions.salaryEscalation * 100).toFixed(1)}% a year, closes the gap. ${deductible > 0 ? `Contributions are deductible under s11F, reducing the after-tax cost to about ${r(afterTax)} p.m.` : 'The s11F deduction limit is already used; consider a tax-free savings account.'}`,
        }),
      );
    }

    const will = doc.estate[key].hasWill;
    if (will !== 'yes' || p.estate.estateDuty > 0) {
      out.push(
        newRecommendation({
          area: 'estate',
          lifeAssured: key,
          action: 'refer',
          productType: will !== 'yes' ? 'Will drafting' : 'Estate plan review',
          rationale:
            will !== 'yes'
              ? `${name} does not have a confirmed valid will. A will (with a testamentary trust and guardian nomination for minor children) ensures assets go to the intended heirs and avoids the Guardian’s Fund.`
              : `Estate duty of ${r(p.estate.estateDuty)} is estimated. Refer for estate structuring (spousal bequests, trusts, donations).`,
        }),
      );
    }
  }

  for (const e of a.education) {
    if (e.lumpSumRequired > 1_000) {
      out.push(
        newRecommendation({
          area: 'education',
          lifeAssured: 'client',
          action: 'new',
          productType: 'Education funding from capital',
          amount: roundUp(e.lumpSumRequired, 5_000),
          rationale: `${e.name} is already studying. About ${r(e.lumpSumRequired)} is needed to fund the remaining years of study and should be set aside in a low-risk, accessible investment.`,
        }),
      );
    }
    if (e.monthlyRequired > 100) {
      out.push(
        newRecommendation({
          area: 'education',
          lifeAssured: 'client',
          action: 'new',
          productType: 'Education investment (TFSA or unit trust)',
          amount: roundUp(e.monthlyRequired, 100),
          premiumMonthly: roundUp(e.monthlyRequired, 100),
          premiumEscalation: doc.assumptions.cpi,
          rationale: `${e.name}’s tertiary education will cost about ${r(e.capitalAtStart)} from ${new Date().getFullYear() + Math.round(e.yearsToTertiary)}. Saving ${r(roundUp(e.monthlyRequired, 100))} p.m. (escalating with inflation) closes the projected shortfall. A tax-free savings account (R${table.tfsa.annual.toLocaleString('en-ZA')} a year limit) avoids tax on growth.`,
        }),
      );
    }
  }

  if (a.emergency.shortfall > 5_000) {
    out.push(
      newRecommendation({
        area: 'emergency',
        lifeAssured: 'client',
        action: 'new',
        productType: 'Money market / notice account',
        amount: roundUp(a.emergency.shortfall, 5_000),
        premiumMonthly: roundUp(a.emergency.shortfall / 12, 100),
        premiumEscalation: 0,
        rationale: `An emergency fund of ${r(a.emergency.need)} (${doc.assumptions.emergencyMonths} months of essential outgoings) protects against job loss and income protection waiting periods. Build the ${r(a.emergency.shortfall)} shortfall over 12 months, or from the annual bonus.`,
      }),
    );
  }

  const expensive = doc.liabilities.filter((l) => UNSECURED_DEBT.includes(l.type) && l.interestRate >= 0.18 && l.balance > 0);
  if (expensive.length) {
    const total = expensive.reduce((s, l) => s + l.balance, 0);
    out.push(
      newRecommendation({
        area: 'debt',
        lifeAssured: 'client' as PersonKey,
        action: 'restructure',
        productType: 'Debt reduction plan',
        amount: total,
        rationale: `Settle ${r(total)} of high-interest unsecured debt (${expensive.map((l) => `${l.description || l.type} at ${(l.interestRate * 100).toFixed(1)}%`).join(', ')}) — highest rate first — using the annual bonus or surplus. This is a guaranteed, tax-free return equal to the interest rate.`,
      }),
    );
  }

  return out;
};
