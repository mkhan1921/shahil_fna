/**
 * Time-value-of-money primitives.
 *
 * Conventions: annual rates are effective annual rates. Monthly equivalents
 * are derived geometrically, i.e. (1 + annual)^(1/12) − 1, so that twelve
 * monthly compounding periods reproduce the annual rate exactly.
 */

export const monthlyRate = (annual: number): number => Math.pow(1 + annual, 1 / 12) - 1;

/** Real (inflation-adjusted) rate: (1 + nominal) / (1 + inflation) − 1. */
export const realRate = (nominal: number, inflation: number): number => (1 + nominal) / (1 + inflation) - 1;

/** Present value of an ordinary annuity factor, a(n, r). */
export const annuityFactor = (rate: number, periods: number): number => {
  if (periods <= 0) return 0;
  if (Math.abs(rate) < 1e-12) return periods;
  return (1 - Math.pow(1 + rate, -periods)) / rate;
};

/**
 * Present value of a monthly income that escalates with `growth` p.a., paid
 * monthly in advance for `years`, discounted at `discount` p.a.
 *
 * Uses the real-rate identity: escalating payments discounted at a nominal
 * rate equal level payments discounted at the real rate.
 */
export const pvGrowingMonthlyIncome = (
  monthlyToday: number,
  years: number,
  discount: number,
  growth: number,
  inAdvance = true,
): number => {
  if (monthlyToday <= 0 || years <= 0) return 0;
  const r = monthlyRate(realRate(discount, growth));
  const n = Math.round(years * 12);
  const factor = annuityFactor(r, n) * (inAdvance ? 1 + r : 1);
  return monthlyToday * factor;
};

/**
 * Present value of an annual payment escalating at `growth`, discounted at
 * `discount`, for `years` payments. `due` = first payment now (annuity due).
 */
export const pvGrowingAnnuity = (payment: number, years: number, discount: number, growth: number, due = true): number => {
  if (payment <= 0 || years <= 0) return 0;
  if (Math.abs(discount - growth) < 1e-12) return due ? years * payment : (years * payment) / (1 + discount);
  const factor = (1 - Math.pow((1 + growth) / (1 + discount), years)) / (discount - growth);
  return payment * factor * (due ? 1 + discount : 1);
};

/** Future value of a lump sum. */
export const fv = (pv: number, annual: number, years: number): number => pv * Math.pow(1 + annual, years);

/** Present value of a future lump sum. */
export const pv = (future: number, annual: number, years: number): number =>
  years <= 0 ? future : future / Math.pow(1 + annual, years);

/**
 * Future value of monthly contributions that escalate once a year, invested
 * at `annualReturn`, contributions paid at the end of each month.
 * Simulated month by month for exactness with annual escalation steps.
 */
export const fvEscalatingContributions = (
  monthly: number,
  years: number,
  annualReturn: number,
  escalation: number,
): number => {
  if (monthly <= 0 || years <= 0) return 0;
  const r = monthlyRate(annualReturn);
  const months = Math.round(years * 12);
  let value = 0;
  let contribution = monthly;
  for (let m = 1; m <= months; m++) {
    value = value * (1 + r) + contribution;
    if (m % 12 === 0) contribution *= 1 + escalation;
  }
  return value;
};

/** Level monthly repayment on a loan (ordinary annuity). */
export const loanRepayment = (principal: number, annualNominalRate: number, months: number): number => {
  if (principal <= 0 || months <= 0) return 0;
  const r = annualNominalRate / 12;
  if (r === 0) return principal / months;
  return (principal * r) / (1 - Math.pow(1 + r, -months));
};

/**
 * Months to repay a loan at a given instalment (nominal annual rate,
 * compounded monthly as per South African credit agreements).
 * Returns Infinity when the instalment does not cover the interest.
 */
export const monthsToRepay = (balance: number, annualNominalRate: number, instalment: number): number => {
  if (balance <= 0) return 0;
  if (instalment <= 0) return Infinity;
  const r = annualNominalRate / 12;
  if (r === 0) return Math.ceil(balance / instalment);
  const interest = balance * r;
  if (instalment <= interest) return Infinity;
  return Math.ceil(-Math.log(1 - (balance * r) / instalment) / Math.log(1 + r) - 1e-9);
};

/** Total interest still payable on a loan at the current instalment. */
export const remainingInterest = (balance: number, annualNominalRate: number, instalment: number): number => {
  const n = monthsToRepay(balance, annualNominalRate, instalment);
  if (!Number.isFinite(n)) return Infinity;
  // The final instalment is usually smaller; approximate via amortisation.
  const r = annualNominalRate / 12;
  let b = balance;
  let paid = 0;
  for (let i = 0; i < n && b > 0.005; i++) {
    const interest = b * r;
    const payment = Math.min(instalment, b + interest);
    paid += payment;
    b = b + interest - payment;
  }
  return Math.max(0, paid - balance);
};

/** Level monthly saving (escalating yearly) required to reach a future target. */
export const requiredEscalatingSaving = (
  target: number,
  years: number,
  annualReturn: number,
  escalation: number,
): number => {
  if (target <= 0) return 0;
  if (years <= 0) return Infinity;
  const unit = fvEscalatingContributions(1, years, annualReturn, escalation);
  return unit > 0 ? target / unit : Infinity;
};

export const clamp = (value: number, min: number, max: number): number => Math.min(max, Math.max(min, value));

export const sum = <T>(items: readonly T[], pick: (item: T) => number): number =>
  items.reduce((total, item) => total + (Number.isFinite(pick(item)) ? pick(item) : 0), 0);

export const round = (value: number, step = 1): number => Math.round(value / step) * step;
