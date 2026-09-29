import type { Metadata } from 'next';
import AppShell from '@/components/AppShell';
import { DEFAULT_ASSUMPTIONS } from '@/lib/fna/defaults';
import { TAX_TABLES } from '@/lib/fna/tax';

export const metadata: Metadata = { title: 'Methodology' };

const r = (n: number) => `R${n.toLocaleString('en-ZA')}`;
const p = (n: number, d = 1) => `${(n * 100).toFixed(d)}%`;

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-20 rounded-xl border border-line bg-surface p-6">
      <h2 className="mb-3 text-lg font-semibold text-ink">{title}</h2>
      <div className="space-y-3 text-sm leading-6 text-ink-2 [&_code]:rounded [&_code]:bg-canvas [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-[12.5px] [&_li]:ml-5 [&_li]:list-disc">{children}</div>
    </section>
  );
}

export default function MethodologyPage() {
  const t = TAX_TABLES['2026/27'];
  const prev = TAX_TABLES['2025/26'];
  const a = DEFAULT_ASSUMPTIONS;
  const toc = [
    ['tax', 'Income tax'],
    ['death', 'Life cover'],
    ['disability', 'Disability & income protection'],
    ['illness', 'Severe illness & funeral'],
    ['estate', 'Estate liquidity & duty'],
    ['retirement', 'Retirement'],
    ['education', 'Education & goals'],
    ['assumptions', 'Default assumptions'],
    ['compliance', 'Compliance framework'],
  ];
  return (
    <AppShell>
      <main className="mx-auto grid max-w-7xl gap-8 px-4 py-8 sm:px-6 lg:grid-cols-[14rem_1fr]">
        <nav className="hidden lg:block" aria-label="Contents">
          <div className="sticky top-20 space-y-1 text-sm">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-faint">Contents</div>
            {toc.map(([id, label]) => (
              <a key={id} href={`#${id}`} className="block rounded-md px-2 py-1 text-ink-2 hover:bg-surface hover:text-ink">
                {label}
              </a>
            ))}
          </div>
        </nav>
        <div className="space-y-6">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Methodology & sources</h1>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-muted">
              How every figure in the analysis is calculated, with the legislation and sources behind it. Reviewed against the Budget 2026 announcements (25 February 2026) and
              the FAIS General Code of Conduct. Every calculation is covered by automated tests.
            </p>
          </div>

          <Section id="tax" title="Income tax (SARS 2026/27)">
            <p>
              Tax is calculated on the {t.period} tables. Budget 2026 applied a 3.4% inflation adjustment — the first since 2023/24. The 2025/26 tables remain available for
              back-dated analyses.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[30rem] text-[13px] tabular">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-muted">
                    <th className="py-1.5">Taxable income</th>
                    <th className="py-1.5">Tax 2026/27</th>
                  </tr>
                </thead>
                <tbody>
                  {t.brackets.map((b, i) => {
                    const next = t.brackets[i + 1];
                    return (
                      <tr key={b.from} className="border-b border-line/70">
                        <td className="py-1.5">{next ? `${r(b.from + 1)} – ${r(next.from)}` : `${r(b.from + 1)} and above`}</td>
                        <td className="py-1.5">{b.base ? `${r(b.base)} + ${p(b.rate, 0)} above ${r(b.from)}` : `${p(b.rate, 0)} of taxable income`}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <ul>
              <li>
                Rebates {r(t.rebates.primary)} (all), {r(t.rebates.secondary)} (65+), {r(t.rebates.tertiary)} (75+). Thresholds {r(t.thresholds.under65)} / {r(t.thresholds.age65to74)} /{' '}
                {r(t.thresholds.age75plus)}. (2025/26 primary rebate: {r(prev.rebates.primary)}.)
              </li>
              <li>
                Medical scheme fees tax credit (s6A): {r(t.medicalCredits.main)} per month for each of the first two members, {r(t.medicalCredits.additional)} for each additional
                dependant.
              </li>
              <li>
                Retirement fund deduction (s11F): contributions up to 27.5% of the greater of remuneration or taxable income, capped at {r(t.retirementDeduction.cap)} a year
                (announced in Budget 2026; {r(prev.retirementDeduction.cap)} previously). Employer contributions are a taxable fringe benefit and deemed member contributions.
              </li>
              <li>UIF: 1% of remuneration up to {r(t.uif.ceilingMonthly)} per month (maximum R177.12).</li>
              <li>
                Bonuses are taxed at the margin: the PAYE on regular income excludes the bonus, and the bonus is shown net of the additional tax it attracts.
              </li>
              <li>
                Lump sums: retirement/death table (0% to R550,000, then 18%, 27%, 36%) and withdrawal table (0% to R27,500). Two-pot savings withdrawals are taxed at the marginal
                rate.
              </li>
            </ul>
          </Section>

          <Section id="death" title="Life cover (capital needs on death)">
            <p>The need is the capital required on death, less capital already available:</p>
            <ul>
              <li>Debts to be settled (excluding debt covered by credit life), estate costs, CGT, estate duty, funeral costs, cash bequests and any accrual claim payable.</li>
              <li>An adjustment fund of {a.emergencyMonths} months of the deceased&apos;s after-tax income.</li>
              <li>Tertiary education capital for dependent children (present value of future costs, less education savings).</li>
              <li>
                Family income: {p(a.deathIncomeReplacement, 0)} of the deceased&apos;s after-tax income, increasing with CPI, until the youngest child reaches independence (and a
                non-earning spouse reaches retirement). Capitalised as{' '}
                <code>PV = PMT × [1 − (1 + r)^−n] / r × (1 + r)</code> with <code>r = ((1 + i)/(1 + g))^(1/12) − 1</code> — a monthly annuity-due at the real rate.
              </li>
            </ul>
            <p>
              Available capital: existing life cover (personal and group; buy-and-sell cover excluded), family income benefits (present value), retirement fund death benefits net of
              lump-sum tax, and liquid assets. The family home and personal-use assets are not treated as available. Retirement fund benefits are allocated by trustees under s37C of
              the Pension Funds Act and are not available to the executor.
            </p>
          </Section>

          <Section id="disability" title="Disability & income protection">
            <ul>
              <li>
                Income protection target: {p(a.incomeProtectionTarget, 0)} of after-tax income. Since 1 March 2015 premiums are not deductible and benefits are tax-free (s10(1)(gI)),
                so the comparison is net-to-net. Only benefits payable to at least age 60 count as permanent cover; temporary benefits are noted separately.
              </li>
              <li>
                Lump-sum disability: debts settled on disability, {r(a.disabilityAdjustments)} for home, vehicle and medical adaptations, plus the present value of member and employer
                retirement contributions lost to retirement age (escalating with salary, discounted at the pre-retirement return).
              </li>
              <li>If income protection is not taken up, the capitalised income gap is disclosed as an alternative lump-sum need.</li>
            </ul>
          </Section>

          <Section id="illness" title="Severe illness & funeral">
            <ul>
              <li>
                Severe illness: {a.severeIllnessMonths} months of gross income (2× annual, within the 1–3× range used by SA advisers) to fund treatment outside medical aid, recovery time
                and lifestyle adjustments. Accelerated benefits are flagged because a claim reduces life cover.
              </li>
              <li>
                Funeral: the funeral cost per person (default {r(a.funeralAdult)}), less funeral cover and personal life cover with a nominated beneficiary, which typically pays within
                days. Bank accounts are frozen on death.
              </li>
            </ul>
          </Section>

          <Section id="estate" title="Estate liquidity, estate duty and CGT on death">
            <ul>
              <li>
                Marital regime: in community of property, half of the joint estate is attributed to the deceased. With accrual, the accrual claim is estimated from each spouse&apos;s net
                growth over the commencement value in the antenuptial contract (Matrimonial Property Act s3–s4).
              </li>
              <li>
                Gross estate: assets owned (share of joint assets) plus policies payable to the estate. Policies with nominated beneficiaries are excluded from executor&apos;s fees but
                included as deemed property for estate duty (s3(3)(a)). Retirement funds are excluded (s3(2)(i)).
              </li>
              <li>
                Costs: executor&apos;s fees {p(a.executorFeeRate)} + VAT (4.025%); Master&apos;s fees per the 2018 tariff (R600 plus R200 per R100,000 above R400,000, max R7,000);
                conveyancing per the LSSA guideline tariff effective 1 July 2026 plus VAT and disbursements; bond cancellation, advertising and final tax returns. No transfer duty on
                inheritance.
              </li>
              <li>
                CGT: deemed disposal at market value, roll-over for assets inherited by a spouse, primary residence exclusion {r(t.cgt.primaryResidence)}, death-year exclusion{' '}
                {r(t.cgt.deathExclusion)}, 40% inclusion, taxed at the deceased&apos;s marginal rate.
              </li>
              <li>
                Estate duty: 20% of the dutiable amount up to R30m and 25% above, after the s4(q) spousal deduction and the {r(t.estateDuty.abatement)} abatement (plus any unused abatement
                ported from a predeceased spouse, s4A).
              </li>
              <li>Liquidity: cash required (debts, costs, taxes, bequests) compared with liquid assets and policies payable to the estate.</li>
            </ul>
          </Section>

          <Section id="retirement" title="Retirement">
            <ul>
              <li>
                Projection: current retirement fund values plus assets earmarked for retirement, grown monthly at the pre-retirement return; member and employer contributions escalate
                annually with salary.
              </li>
              <li>
                Target: a percentage of current gross income (default 75%, the common SA net replacement benchmark) or a rand amount in today&apos;s money, less other retirement income.
              </li>
              <li>
                Capital required: present value at retirement of the inflating target income to the planning age (default 90–95), at the post-retirement return.
              </li>
              <li>Additional saving: the escalating monthly contribution that closes the shortfall — <code>FV = C × [(1+i)^n − (1+e)^n] / (i − e)</code>, simulated monthly.</li>
              <li>
                Sustainability: the initial drawdown is tested against the 2.5%–17.5% living annuity limits and the ~4–5% generally regarded as sustainable (ASISA). Capital
                depletion age is simulated.
              </li>
            </ul>
          </Section>

          <Section id="education" title="Education, emergency fund and goals">
            <ul>
              <li>
                Tertiary costs inflate at the education inflation assumption ({p(a.educationInflation)}; 2026 Stats SA: tuition +5.4%, school +6.2%, tertiary +4.2%) and are paid at the
                start of each academic year. Existing savings are projected forward; the shortfall is funded by an escalating monthly saving.
              </li>
              <li>Emergency fund: {a.emergencyMonths} months of essential living expenses, debt repayments and risk premiums (6 months suggested for variable income).</li>
              <li>Other goals are inflated at CPI and funded from projected savings plus a monthly contribution.</li>
            </ul>
          </Section>

          <Section id="assumptions" title="Default assumptions">
            <div className="grid gap-x-8 sm:grid-cols-2">
              {[
                ['CPI', p(a.cpi), 'SARB 3% target ±1pp (Nov 2025); CPI 4.4% Aug 2026'],
                ['Salary escalation', p(a.salaryEscalation), 'CPI + 1%'],
                ['Pre-retirement return', p(a.preRetirementReturn), 'CPI + 4% (balanced, Reg 28)'],
                ['Post-retirement return', p(a.postRetirementReturn), 'CPI + 3%'],
                ['Capital for dependants', p(a.riskCapitalReturn), 'CPI + 2.5%'],
                ['Education inflation', p(a.educationInflation), 'CPI + 2%'],
                ['Prime lending rate', '10.75%', 'From 25 Sept 2026 (repo 7.25%)'],
              ].map(([k, v, s]) => (
                <div key={k} className="flex items-baseline justify-between gap-3 border-b border-line/70 py-2">
                  <span>{k}</span>
                  <span className="text-right">
                    <span className="font-medium text-ink tabular">{v}</span>
                    <span className="block text-xs text-muted">{s}</span>
                  </span>
                </div>
              ))}
            </div>
          </Section>

          <Section id="compliance" title="Compliance framework">
            <ul>
              <li>FAIS General Code of Conduct: disclosures (s3–s7), projections (s7A), suitability and replacements (s8), record of advice (s9), limited advice warnings (s8(4)).</li>
              <li>Long-term Insurance Policyholder Protection Rules: replacement advice record (rule 19).</li>
              <li>POPIA: consent to process personal and special personal information (s11, s26–27), direct marketing opt-in (s69).</li>
              <li>FICA: client identification and PEP/DPIP screening; five-year record retention.</li>
              <li>Treating Customers Fairly: the six outcomes are set out in every report.</li>
            </ul>
            <p className="text-xs text-muted">
              Disclaimer wording is a starting point — your compliance officer should approve it. This tool supports, but does not replace, the adviser&apos;s professional judgement.
            </p>
          </Section>
        </div>
      </main>
    </AppShell>
  );
}
