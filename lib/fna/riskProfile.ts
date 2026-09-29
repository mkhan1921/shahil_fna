/**
 * Investor risk profile questionnaire.
 *
 * Assesses both risk tolerance (willingness) and risk capacity (ability), as
 * required for suitability under section 8 of the FAIS General Code of
 * Conduct. The overall profile is the lower of the two dimensions, so a
 * client is never profiled above their capacity to absorb losses.
 */

export interface RiskQuestion {
  id: string;
  dimension: 'tolerance' | 'capacity';
  question: string;
  options: { label: string; score: number }[];
}

export const RISK_QUESTIONS: RiskQuestion[] = [
  {
    id: 'horizon',
    dimension: 'capacity',
    question: 'When will you need to start drawing on the majority of this money?',
    options: [
      { label: 'Within 1 year', score: 1 },
      { label: '1 – 3 years', score: 2 },
      { label: '3 – 5 years', score: 3 },
      { label: '5 – 10 years', score: 4 },
      { label: 'More than 10 years', score: 5 },
    ],
  },
  {
    id: 'income-stability',
    dimension: 'capacity',
    question: 'How secure is your current and future income?',
    options: [
      { label: 'Very insecure / no regular income', score: 1 },
      { label: 'Somewhat insecure (commission, contract)', score: 2 },
      { label: 'Reasonably secure', score: 3 },
      { label: 'Secure', score: 4 },
      { label: 'Very secure, with other income sources', score: 5 },
    ],
  },
  {
    id: 'emergency',
    dimension: 'capacity',
    question: 'If you lost your income, how long could you cover your expenses from savings (excluding retirement funds)?',
    options: [
      { label: 'Less than 1 month', score: 1 },
      { label: '1 – 3 months', score: 2 },
      { label: '3 – 6 months', score: 3 },
      { label: '6 – 12 months', score: 4 },
      { label: 'More than 12 months', score: 5 },
    ],
  },
  {
    id: 'proportion',
    dimension: 'capacity',
    question: 'What proportion of your total investable assets does this investment represent?',
    options: [
      { label: 'More than 75%', score: 1 },
      { label: '50% – 75%', score: 2 },
      { label: '25% – 50%', score: 3 },
      { label: '10% – 25%', score: 4 },
      { label: 'Less than 10%', score: 5 },
    ],
  },
  {
    id: 'knowledge',
    dimension: 'tolerance',
    question: 'How would you describe your investment knowledge and experience?',
    options: [
      { label: 'None — I have only used bank savings', score: 1 },
      { label: 'Limited — some unit trusts or a retirement annuity', score: 2 },
      { label: 'Moderate — I understand how markets and asset classes work', score: 3 },
      { label: 'Good — I actively follow and manage investments', score: 4 },
      { label: 'Advanced — professional or extensive experience', score: 5 },
    ],
  },
  {
    id: 'objective',
    dimension: 'tolerance',
    question: 'Which statement best describes your main investment objective?',
    options: [
      { label: 'Protect my capital — I cannot accept losses', score: 1 },
      { label: 'Mostly stability, with some growth', score: 2 },
      { label: 'A balance of growth and stability', score: 3 },
      { label: 'Mostly growth, accepting short-term volatility', score: 4 },
      { label: 'Maximum long-term growth', score: 5 },
    ],
  },
  {
    id: 'drawdown',
    dimension: 'tolerance',
    question: 'Your R100,000 investment falls to R80,000 in a market crash. What would you do?',
    options: [
      { label: 'Sell everything immediately', score: 1 },
      { label: 'Sell some to limit further losses', score: 2 },
      { label: 'Do nothing and wait for recovery', score: 3 },
      { label: 'Stay invested and review with my adviser', score: 4 },
      { label: 'Invest more while prices are low', score: 5 },
    ],
  },
  {
    id: 'tradeoff',
    dimension: 'tolerance',
    question: 'Which range of one-year outcomes on R100,000 would you be most comfortable with?',
    options: [
      { label: 'R99,000 to R107,000', score: 1 },
      { label: 'R94,000 to R112,000', score: 2 },
      { label: 'R88,000 to R118,000', score: 3 },
      { label: 'R80,000 to R126,000', score: 4 },
      { label: 'R70,000 to R140,000', score: 5 },
    ],
  },
];

export interface RiskCategory {
  key: 'conservative' | 'moderately-conservative' | 'moderate' | 'moderately-aggressive' | 'aggressive';
  label: string;
  description: string;
  equity: string;
  horizon: string;
  target: string;
}

export const RISK_CATEGORIES: RiskCategory[] = [
  {
    key: 'conservative',
    label: 'Conservative',
    description: 'Capital preservation is the priority. Low tolerance for short-term losses.',
    equity: '0 – 25% growth assets',
    horizon: '1 – 3 years',
    target: 'CPI + 1% to 2%',
  },
  {
    key: 'moderately-conservative',
    label: 'Cautious',
    description: 'Mostly stable returns with modest growth; limited short-term volatility.',
    equity: '25 – 40% growth assets',
    horizon: '3 years +',
    target: 'CPI + 2% to 3%',
  },
  {
    key: 'moderate',
    label: 'Moderate',
    description: 'Balanced growth and stability; accepts periodic negative returns.',
    equity: '40 – 60% growth assets',
    horizon: '5 years +',
    target: 'CPI + 3% to 5%',
  },
  {
    key: 'moderately-aggressive',
    label: 'Moderately aggressive',
    description: 'Long-term growth focus; comfortable with meaningful volatility.',
    equity: '60 – 75% growth assets',
    horizon: '7 years +',
    target: 'CPI + 5% to 6%',
  },
  {
    key: 'aggressive',
    label: 'Aggressive',
    description: 'Maximum long-term growth; accepts large short-term drawdowns.',
    equity: '75 – 100% growth assets',
    horizon: '10 years +',
    target: 'CPI + 6% +',
  },
];

export interface RiskProfileResult {
  complete: boolean;
  answered: number;
  total: number;
  toleranceScore: number;
  capacityScore: number;
  tolerance: RiskCategory | null;
  capacity: RiskCategory | null;
  profile: RiskCategory | null;
  mismatch: boolean;
}

const categoryFor = (average: number): RiskCategory => {
  if (average < 1.8) return RISK_CATEGORIES[0];
  if (average < 2.6) return RISK_CATEGORIES[1];
  if (average < 3.4) return RISK_CATEGORIES[2];
  if (average < 4.2) return RISK_CATEGORIES[3];
  return RISK_CATEGORIES[4];
};

export const scoreRiskProfile = (answers: Record<string, number>): RiskProfileResult => {
  const scored = (dimension: RiskQuestion['dimension']) => {
    const qs = RISK_QUESTIONS.filter((q) => q.dimension === dimension);
    const values = qs
      .map((q) => (answers[q.id] !== undefined ? q.options[answers[q.id]]?.score : undefined))
      .filter((v): v is number => typeof v === 'number');
    return { values, total: qs.length };
  };
  const tol = scored('tolerance');
  const cap = scored('capacity');
  const answered = tol.values.length + cap.values.length;
  const total = tol.total + cap.total;
  const avg = (v: number[]) => (v.length ? v.reduce((a, b) => a + b, 0) / v.length : 0);
  const tolerance = tol.values.length ? categoryFor(avg(tol.values)) : null;
  const capacity = cap.values.length ? categoryFor(avg(cap.values)) : null;
  let profile: RiskCategory | null = null;
  if (tolerance && capacity) {
    const ti = RISK_CATEGORIES.indexOf(tolerance);
    const ci = RISK_CATEGORIES.indexOf(capacity);
    profile = RISK_CATEGORIES[Math.min(ti, ci)];
  }
  return {
    complete: answered === total,
    answered,
    total,
    toleranceScore: avg(tol.values),
    capacityScore: avg(cap.values),
    tolerance,
    capacity,
    profile,
    mismatch: !!tolerance && !!capacity && Math.abs(RISK_CATEGORIES.indexOf(tolerance) - RISK_CATEGORIES.indexOf(capacity)) >= 2,
  };
};
