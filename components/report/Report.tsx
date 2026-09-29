import type { Analysis, GoalResult, PersonAnalysis, Status } from '@/lib/fna/analysis';
import {
  ACTION,
  ASSET_TYPE,
  BENEFICIARY,
  EMPLOYMENT_TYPE,
  LIABILITY_TYPE,
  MARITAL_REGIME,
  MARITAL_STATUS,
  NEED_AREA,
  POLICY_TYPE,
  RELATIONSHIP,
  RETIREMENT_FUND_TYPE,
  SCOPE_AREA,
} from '@/lib/fna/catalog';
import { DISCLAIMERS, OMBUDS, REPLACEMENT_FACTORS } from '@/lib/fna/compliance';
import { ageNextBirthday, ageOn } from '@/lib/fna/idNumber';
import { RISK_QUESTIONS } from '@/lib/fna/riskProfile';
import type { FnaDocument, PersonKey, PracticeProfile, Recommendation } from '@/lib/fna/types';
import { dateLong, money, moneyCompact, pct } from '@/lib/format';
import { AllocationBar, RetirementChart } from '../charts';
import Findings from '../Findings';
import NeedBreakdown from '../NeedBreakdown';
import { CompareRows, GlanceTable, NeedCompare, type GlanceCell } from './Compare';
import { KV, Note, ReportSection, Signature, SubHeading, Table } from './parts';

const STATUS_WORD: Record<Status, string> = { covered: 'On track', partial: 'Partially funded', shortfall: 'Shortfall', na: 'No need identified' };

const fullName = (doc: FnaDocument, k: PersonKey) => {
  const p = doc[k];
  return [p.title, p.firstName, p.surname].filter(Boolean).join(' ') || (k === 'client' ? 'Client' : 'Spouse');
};
const first = (doc: FnaDocument, k: PersonKey) => doc[k].firstName || (k === 'client' ? 'Client' : 'Spouse');
const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const PATTERN_YEARS = [1, 2, 3, 4, 5, 10, 15, 20];

const estateCell = (p: PersonAnalysis): GlanceCell => {
  const e = p.estate;
  const status: Status = e.cashRequired <= 0 ? 'na' : e.liquidityShortfall <= 0 ? 'covered' : e.cashAvailable / e.cashRequired >= 0.6 ? 'partial' : 'shortfall';
  return { need: e.cashRequired, provision: Math.min(e.cashAvailable, e.cashRequired), shortfall: e.liquidityShortfall, status };
};

export default function Report({
  doc,
  analysis,
  practice,
  appendix = false,
}: {
  doc: FnaDocument;
  analysis: Analysis;
  practice: PracticeProfile;
  appendix?: boolean;
}) {
  const a = analysis;
  const e = doc.engagement;
  const people = a.people;
  const P = people.map((k) => a.persons[k]!);
  const names = people.map((k) => first(doc, k));
  const household = people.map((k) => fullName(doc, k)).join(' and ');
  const firm = practice.practiceName || practice.fspName || 'Financial Needs Analysis';
  const recs = doc.advice.recommendations;
  const active = recs.filter((r) => r.clientDecision !== 'declined');
  const newCommitments = active.reduce((s, r) => s + (r.premiumMonthly || 0), 0);
  const declined = recs.filter((r) => r.clientDecision === 'declined' || r.clientDecision === 'deferred');
  const replacements = recs.filter((r) => r.isReplacement);
  const patterned = recs.filter((r) => r.premiumMonthly > 0 && r.premiumEscalation > 0);
  const t = a.table;
  const cf = a.cashflow;
  const nw = a.netWorth;
  const as = doc.assumptions;
  let n = 0;
  const no = () => String(++n);

  const glanceRows: { label: string; cells: (GlanceCell | undefined)[]; household?: boolean }[] = [
    { label: 'Life cover', cells: P.map((p) => p.death) },
    { label: 'Income protection', cells: P.map((p) => ({ ...p.incomeProtection, monthly: true })) },
    { label: 'Disability (lump sum)', cells: P.map((p) => p.disability) },
    { label: 'Severe illness', cells: P.map((p) => p.severeIllness) },
    { label: 'Estate liquidity', cells: P.map(estateCell) },
    {
      label: 'Retirement capital',
      cells: P.map((p) => ({ need: p.retirement.requiredCapital, provision: p.retirement.projectedCapital, shortfall: p.retirement.shortfall, status: p.retirement.status })),
    },
    { label: 'Emergency fund', cells: [a.emergency], household: true },
  ];
  if (a.educationTotal.need > 0) glanceRows.push({ label: 'Education (present value)', cells: [a.educationTotal], household: true });

  const recName = (r: Recommendation) => `${r.productType || NEED_AREA[r.area]}`;
  const amountText = (r: Recommendation) => (r.amount ? (r.area === 'income-protection' ? `${money(r.amount)} p.m.` : money(r.amount)) : '—');

  return (
    <article className="report mx-auto bg-white text-ink" style={{ ['--report-brand' as string]: practice.brandColor || '#0f3d5e' }}>
      {/* ================================================= Title block */}
      <header className="mb-6">
        <div className="flex items-start justify-between gap-6 border-b-2 pb-4" style={{ borderColor: 'var(--report-brand)' }}>
          <div>
            {practice.logoDataUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={practice.logoDataUrl} alt={firm} className="mb-3 h-10 w-auto max-w-[200px] object-contain" />
            ) : (
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--report-brand)' }}>
                {firm}
              </div>
            )}
            <h1 className="font-serif text-[26px] font-semibold leading-tight text-ink">Financial Needs Analysis & Record of Advice</h1>
            <p className="mt-1 text-[13px] text-ink-2">
              Prepared for <strong className="text-ink">{household}</strong>
            </p>
          </div>
          <div className="shrink-0 text-right text-[10.5px] leading-[1.6] text-muted">
            <div className="text-[9px] font-semibold uppercase tracking-[0.14em]">Private & confidential</div>
            <div>{dateLong(e.meetingDate)}</div>
            <div>{e.adviceType === 'limited' ? 'Limited-scope advice' : 'Comprehensive analysis'}</div>
          </div>
        </div>
        <div className="mt-2.5 grid grid-cols-3 gap-4 text-[10.5px]">
          <div>
            <span className="text-muted">Adviser: </span>
            <span className="font-medium text-ink">{practice.adviserName || '—'}</span>
            {practice.adviserQualifications && <span className="text-muted"> · {practice.adviserQualifications}</span>}
          </div>
          <div>
            <span className="text-muted">FSP: </span>
            <span className="font-medium text-ink">{practice.fspName || '—'}</span>
            {practice.fspNumber && <span className="text-muted"> · FSP {practice.fspNumber}</span>}
          </div>
          <div className="text-right">
            <span className="text-muted">Tax basis: </span>
            <span className="font-medium text-ink">SARS {t.label}</span>
          </div>
        </div>
      </header>

      {/* ================================================= 1. Summary */}
      <ReportSection number={no()} title="Summary" breakBefore={false}>
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: 'Net worth', value: moneyCompact(nw.netWorth) },
            { label: 'Take-home pay', value: `${money(cf.takeHome)} p.m.` },
            { label: cf.surplus < 0 ? 'Monthly deficit' : 'Monthly surplus', value: money(cf.surplus) },
            { label: 'Savings rate', value: pct(a.ratios.savingsRate) },
          ].map((s) => (
            <div key={s.label} className="rounded-md border border-line px-3 py-2">
              <div className="text-[9px] font-semibold uppercase tracking-wide text-muted">{s.label}</div>
              <div className="mt-0.5 text-[16px] font-semibold text-ink tabular">{s.value}</div>
            </div>
          ))}
        </div>
        <div>
          <SubHeading aside="Existing provision compared with each need">Needs at a glance</SubHeading>
          <GlanceTable names={names} rows={glanceRows} />
        </div>
        <div>
          <SubHeading>What we found</SubHeading>
          <div className="columns-2 gap-8 [&_li]:break-inside-avoid">
            <Findings findings={a.findings.filter((f) => f.area !== 'compliance')} limit={10} dense />
          </div>
        </div>
        {recs.length > 0 && (
          <div>
            <SubHeading aside={`New monthly commitments ${money(newCommitments)}`}>What we recommend</SubHeading>
            <Table
              head={['#', 'Recommendation', 'For', 'Amount', 'Cost p.m.', 'Decision']}
              align={['l', 'l', 'l', 'r', 'r', 'l']}
              rows={recs.map((r, i) => [i + 1, `${ACTION[r.action]}: ${recName(r)}`, first(doc, r.lifeAssured), amountText(r), r.premiumMonthly ? money(r.premiumMonthly) : 'Quote', cap(r.clientDecision)])}
            />
            <p className="mt-1.5 text-[10px] text-muted">
              After these commitments the monthly {cf.surplus - newCommitments < 0 ? 'deficit' : 'surplus'} would be{' '}
              <strong className="text-ink">{money(cf.surplus - newCommitments)}</strong>
              {cf.bonusNet > 0 ? `, before the net annual bonus of ${money(cf.bonusNet)}` : ''}. Risk premiums marked “Quote” depend on underwriting.
            </p>
          </div>
        )}
      </ReportSection>

      {/* ================================================= 2. Situation */}
      <ReportSection
        number={no()}
        title="Your situation"
        intro="The analysis is based on the information you provided. Please tell us if anything is incorrect or incomplete, as it may change our advice."
      >
        <div className="grid grid-cols-[1.35fr_1fr] gap-6">
          <CompareRows
            names={names}
            rows={[
              { label: 'Age (next birthday)', values: people.map((k) => (doc[k].dateOfBirth ? `${ageOn(doc[k].dateOfBirth)} (${ageNextBirthday(doc[k].dateOfBirth)})` : '—')) },
              { label: 'ID number', values: people.map((k) => doc[k].idNumber || doc[k].passportNumber || '—') },
              { label: 'Gender · smoker', values: people.map((k) => `${cap(doc[k].gender) || '—'} · ${doc[k].smoker ? 'Smoker' : 'Non-smoker'}`) },
              { label: 'Occupation', values: people.map((k) => doc[k].occupation || '—') },
              { label: 'Employment', values: people.map((k) => EMPLOYMENT_TYPE[doc[k].employmentType]) },
            ]}
          />
          <KV
            cols={1}
            rows={[
              ['Marital status', MARITAL_STATUS[doc.household.maritalStatus]],
              ['Regime', doc.household.maritalStatus === 'married' ? MARITAL_REGIME[doc.household.maritalRegime] : 'Not applicable'],
              ['Home', [doc.household.city, doc.household.province].filter(Boolean).join(', ')],
              ['Dependants', doc.dependants.map((d) => `${d.name.split(' ')[0] || RELATIONSHIP[d.relationship]} (${ageOn(d.dateOfBirth) ?? '?'})`).join(', ') || 'None'],
            ]}
          />
        </div>
        {(e.clientObjectives || e.reasonForAdvice) && (
          <div className="rounded-md bg-wash px-4 py-3 text-[11px] leading-[1.6] text-ink-2">
            {e.reasonForAdvice && (
              <p>
                <span className="font-semibold text-ink">Why we met: </span>
                {e.reasonForAdvice}
              </p>
            )}
            {e.clientObjectives && (
              <p className="mt-1">
                <span className="font-semibold text-ink">Your objectives: </span>
                {e.clientObjectives}
              </p>
            )}
            {e.clientConcerns && (
              <p className="mt-1">
                <span className="font-semibold text-ink">Concerns: </span>
                {e.clientConcerns}
              </p>
            )}
          </div>
        )}

        <div className="grid grid-cols-2 gap-6">
          <div>
            <SubHeading aside={`SARS ${t.label}`}>Monthly income</SubHeading>
            <CompareRows
              names={names}
              strongRows={['Take-home pay']}
              rows={[
                { label: 'Gross income', values: a.incomes.map((i) => money(i.grossMonthly)) },
                { label: 'PAYE & UIF', values: a.incomes.map((i) => money(-(i.payeMonthly + i.uifMonthly))) },
                { label: 'Retirement & medical (payroll)', values: a.incomes.map((i) => money(-(i.retirementPayrollMonthly + i.medicalPayrollMonthly + i.otherDeductionsMonthly))) },
                { label: 'Take-home pay', values: a.incomes.map((i) => money(i.takeHomeMonthly)) },
                { label: 'Marginal tax rate', values: a.incomes.map((i) => pct(i.tax.marginalRate)) },
              ]}
            />
          </div>
          <div>
            <SubHeading aside={`Net worth ${moneyCompact(nw.netWorth)}`}>Assets and liabilities</SubHeading>
            <Table
              head={['', 'Value']}
              align={['l', 'r']}
              rows={[
                ...nw.byGroup.map((g) => [g.label, money(g.amount)]),
                ...doc.liabilities.map((l) => [`${l.description || LIABILITY_TYPE[l.type]} (${pct(l.interestRate, 1)})`, money(-l.balance)]),
              ]}
              foot={['Net worth', money(nw.netWorth)]}
            />
          </div>
        </div>

        <div className="avoid-break">
          <SubHeading aside={`${money(cf.takeHome)} take-home per month`}>Where your money goes</SubHeading>
          <AllocationBar
            total={cf.takeHome}
            segments={[
              { label: 'Living expenses', value: cf.livingExpenses },
              { label: 'Debt repayments', value: cf.debtRepayments },
              { label: 'Risk premiums', value: cf.riskPremiums },
              { label: 'Savings & investments', value: cf.savingsContributions },
              { label: 'Unallocated surplus', value: Math.max(0, cf.surplus) },
            ]}
          />
          <div className="mt-3 grid grid-cols-5 gap-2 text-[10px]">
            {[
              ['Debt / gross income', pct(a.ratios.debtToIncome), '< 36%'],
              ['Housing / gross income', pct(a.ratios.housingToIncome), '< 30%'],
              ['Savings rate', pct(a.ratios.savingsRate), '≥ 15%'],
              ['Risk premiums', pct(a.ratios.premiumsToIncome, 1), '5–10%'],
              ['Emergency fund', `${a.ratios.emergencyMonths.toFixed(1)} mo`, `${as.emergencyMonths}+ mo`],
            ].map(([k, v, g]) => (
              <div key={k} className="rounded bg-wash px-2 py-1.5">
                <div className="text-muted">{k}</div>
                <div className="font-semibold text-ink tabular">
                  {v} <span className="font-normal text-muted">({g})</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div>
          <SubHeading>Existing cover</SubHeading>
          {doc.policies.length ? (
            <Table
              head={['Life', 'Benefit', 'Insurer', 'Cover', 'Beneficiary', 'Premium']}
              align={['l', 'l', 'l', 'r', 'l', 'r']}
              rows={doc.policies
                .filter((p) => people.includes(p.lifeAssured))
                .map((p) => [
                  first(doc, p.lifeAssured),
                  `${POLICY_TYPE[p.type]}${p.isGroup ? ' (group)' : ''}`,
                  p.insurer || '—',
                  p.type === 'income-protection' || p.type === 'family-income' ? `${money(p.monthlyBenefit)} p.m.` : money(p.cover),
                  p.type === 'life' ? BENEFICIARY[p.beneficiary].replace(' (no nomination)', '') : '—',
                  p.isGroup ? 'Employer' : money(p.premiumMonthly),
                ])}
            />
          ) : (
            <p className="text-[11px] text-muted">No existing policies were recorded.</p>
          )}
        </div>
      </ReportSection>

      {/* ================================================= 3. Protection */}
      <ReportSection
        number={no()}
        title="Protecting your family and income"
        breakBefore={false}
        intro={`Each need is calculated, then reduced by what is already in place. Income needs are converted to a lump sum as the present value of an income that rises with inflation (${pct(as.cpi, 1)}), invested at ${pct(as.riskCapitalReturn, 1)} a year.`}
      >
        <div>
          <SubHeading aside="If death occurred today">Life cover</SubHeading>
          <NeedCompare names={names} needs={P.map((p) => p.death)} />
          <p className="mt-1.5 text-[10px] leading-[1.5] text-muted">
            Family income: {pct(as.deathIncomeReplacement)} of after-tax income for {P.map((p) => `${p.name.split(' ')[0]} ${p.deathIncomeYears} yrs`).join(', ')}. Retirement fund death
            benefits are allocated by the fund trustees (s37C, Pension Funds Act), are shown after lump-sum tax, and are not available to the executor.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-6">
          <div>
            <SubHeading aside="Monthly, after tax">Income protection</SubHeading>
            <NeedCompare names={names} needs={P.map((p) => p.incomeProtection)} monthly />
          </div>
          <div>
            <SubHeading aside="Lump sum">Severe illness & funeral</SubHeading>
            <CompareRows
              names={names}
              strongRows={['Severe illness shortfall', 'Funeral shortfall']}
              rows={[
                { label: `Severe illness need (${as.severeIllnessMonths} months’ income)`, values: P.map((p) => money(p.severeIllness.need)) },
                { label: 'Less existing cover', values: P.map((p) => money(-p.severeIllness.provision)) },
                { label: 'Severe illness shortfall', values: P.map((p) => money(p.severeIllness.shortfall)) },
                { label: 'Funeral cost', values: P.map((p) => money(p.funeral.need)) },
                { label: 'Funeral shortfall', values: P.map((p) => money(p.funeral.shortfall)) },
              ]}
            />
          </div>
        </div>
        <div>
          <SubHeading aside="Permanent disability">Disability lump sum</SubHeading>
          <NeedCompare names={names} needs={P.map((p) => p.disability)} />
          <p className="mt-1.5 text-[10px] leading-[1.5] text-muted">
            Income on disability is replaced through income protection; the lump sum settles debt, pays for adaptations and replaces retirement contributions that stop.
          </p>
        </div>

        <div>
          <SubHeading aside="If death occurred today">Estate liquidity and estate duty</SubHeading>
          <CompareRows
            names={names}
            strongRows={['Net estate', 'Estate duty', 'Cash required by the executor', 'Liquidity shortfall']}
            rows={[
              { label: 'Assets through the executor', values: P.map((p) => money(p.estate.grossEstate)) },
              { label: 'Policies paid to nominees (deemed property)', values: P.map((p) => money(p.estate.deemedProperty)) },
              { label: 'Debts', values: P.map((p) => money(-p.estate.liabilities)) },
              { label: 'Executor’s fees (3.5% + VAT)', values: P.map((p) => money(-p.estate.executorFees)) },
              {
                label: 'Master’s fees, conveyancing, funeral & other costs',
                values: P.map((p) => money(-(p.estate.mastersFees + p.estate.conveyancing + p.estate.bondCancellation + p.estate.sundries + p.estate.funeral))),
              },
              { label: 'Capital gains tax on death', values: P.map((p) => money(-p.estate.cgt)) },
              { label: 'Net estate', values: P.map((p) => money(p.estate.netEstate)) },
              { label: 'Less spousal bequests s4(q) & abatement', values: P.map((p) => money(-(p.estate.spouseDeduction + p.estate.abatement))) },
              { label: 'Estate duty', values: P.map((p) => money(p.estate.estateDuty)) },
              { label: 'Cash required by the executor', values: P.map((p) => money(p.estate.cashRequired)) },
              { label: 'Cash available in the estate', values: P.map((p) => money(p.estate.cashAvailable)) },
              { label: 'Liquidity shortfall', values: P.map((p) => (p.estate.liquidityShortfall > 0 ? money(p.estate.liquidityShortfall) : 'None')) },
              {
                label: 'Will',
                values: people.map((k) =>
                  doc.estate[k].hasWill === 'yes' ? `Yes${doc.estate[k].willDate ? ` (${doc.estate[k].willDate.slice(0, 4)})` : ''}` : doc.estate[k].hasWill === 'no' ? 'None' : 'Unconfirmed',
                ),
              },
            ]}
          />
          <p className="mt-1.5 text-[10px] leading-[1.5] text-muted">{DISCLAIMERS.estate}</p>
        </div>
      </ReportSection>

      {/* ================================================= 4. Retirement & goals */}
      <ReportSection
        number={no()}
        title="Retirement, education and goals"
        breakBefore={false}
        intro={`Savings are projected at ${pct(as.preRetirementReturn, 1)} a year with contributions rising ${pct(as.salaryEscalation, 1)} a year, then tested against the target income, rising with inflation, to the planning age at ${pct(as.postRetirementReturn, 1)} a year. Amounts are in today’s money.`}
      >
        <div className={people.length > 1 ? 'grid grid-cols-2 gap-6' : ''}>
          {P.map((p) => (
            <div key={p.key} className="avoid-break">
              <div className="mb-1 text-[11px] font-semibold text-ink">
                {p.name} <span className="font-normal text-muted">· {STATUS_WORD[p.retirement.status]}</span>
              </div>
              <RetirementChart
                timeline={p.retirement.timeline}
                retirementAge={p.retirement.retirementAge}
                requiredReal={p.retirement.requiredCapitalReal}
                interactive={false}
                height={people.length > 1 ? 190 : 200}
                viewWidth={people.length > 1 ? 380 : 640}
              />
            </div>
          ))}
        </div>
        <CompareRows
          names={names}
          strongRows={['Income replacement', 'Extra saving needed (p.m., escalating)']}
          rows={[
            { label: 'Retirement age / plan to age', values: P.map((p) => `${p.retirement.retirementAge} / ${p.retirement.planningAge}`) },
            { label: 'Retirement savings today', values: P.map((p) => money(p.retirement.currentCapital)) },
            { label: 'Contributions incl. employer (p.m.)', values: P.map((p) => money(p.retirement.monthlyContributions)) },
            { label: 'Target income (p.m.)', values: P.map((p) => `${money(p.retirement.targetMonthlyToday)} (${pct(p.retirement.targetReplacementRatio)})`) },
            { label: 'Projected capital at retirement', values: P.map((p) => money(p.retirement.projectedCapitalReal)) },
            { label: 'Capital required at retirement', values: P.map((p) => money(p.retirement.requiredCapitalReal)) },
            { label: 'Sustainable income (p.m.)', values: P.map((p) => money(p.retirement.sustainableMonthlyToday)) },
            { label: 'Income replacement', values: P.map((p) => pct(p.retirement.replacementRatio)) },
            { label: 'Capital lasts to age', values: P.map((p) => (p.retirement.depletionAge ? String(Math.floor(p.retirement.depletionAge)) : `${p.retirement.planningAge}+`)) },
            { label: 'Extra saving needed (p.m., escalating)', values: P.map((p) => (p.retirement.additionalMonthly > 0 ? money(p.retirement.additionalMonthly) : 'None')) },
          ]}
        />
        {P.some((p) => p.retirement.initialDrawdown > 0.05) && (
          <Note>
            A first-year drawdown above 4–5% of capital is generally regarded as unsustainable for a living annuity (legal range 2.5%–17.5%). Required drawdown:{' '}
            {P.map((p) => `${p.name.split(' ')[0]} ${pct(p.retirement.initialDrawdown, 1)}`).join(', ')}.
          </Note>
        )}

        {(a.education.length > 0 || a.goals.length > 0) && (
          <div className="avoid-break grid grid-cols-2 gap-6">
            {a.education.length > 0 && (
              <div>
                <SubHeading aside={`Education inflation ${pct(as.educationInflation, 1)}`}>Education</SubHeading>
                <Table
                  head={['Child', 'Starts', 'Needed then', 'Save p.m.']}
                  align={['l', 'r', 'r', 'r']}
                  rows={a.education.map((x) => [
                    x.name.split(' ')[0],
                    x.capitalAtStart > 0 ? new Date().getFullYear() + Math.round(x.yearsToTertiary) : '—',
                    money(x.capitalAtStart, { dash: true }),
                    x.monthlyRequired > 0 ? money(x.monthlyRequired) : 'Funded',
                  ])}
                />
              </div>
            )}
            {a.goals.length > 0 && (
              <div>
                <SubHeading>Other goals</SubHeading>
                <Table
                  head={['Goal', 'Year', 'Future cost', 'Save p.m.']}
                  align={['l', 'r', 'r', 'r']}
                  rows={a.goals.map((g: GoalResult) => [g.name, new Date().getFullYear() + g.years, money(g.futureCost), g.monthlyRequired > 0 ? money(g.monthlyRequired) : 'Funded'])}
                />
              </div>
            )}
          </div>
        )}

        <div>
          <SubHeading>Investor risk profile</SubHeading>
          {a.risk.profile ? (
            <p className="text-[11px] leading-[1.6] text-ink-2">
              Recommended profile: <strong className="text-ink">{a.risk.profile.label}</strong> (tolerance {a.risk.tolerance?.label.toLowerCase()}, capacity{' '}
              {a.risk.capacity?.label.toLowerCase()}). {a.risk.profile.description} Typical portfolio {a.risk.profile.equity}, horizon {a.risk.profile.horizon}, objective{' '}
              {a.risk.profile.target}.
            </p>
          ) : (
            <p className="text-[11px] text-muted">The risk profile questionnaire was not completed.</p>
          )}
        </div>
      </ReportSection>

      {/* ================================================= 5. Record of advice */}
      <ReportSection number={no()} title="Record of advice" intro="What we recommended, why it suits you, and what you decided (FAIS General Code of Conduct, section 9).">
        <p className="text-[11px] leading-[1.6] text-ink-2">
          <span className="font-semibold text-ink">Scope: </span>
          {e.adviceType === 'limited' ? 'Limited advice' : 'Comprehensive needs analysis'} covering {e.scope.map((s) => SCOPE_AREA[s].toLowerCase()).join('; ') || '—'}. Meeting held{' '}
          {e.meetingType === 'in-person' ? 'in person' : e.meetingType === 'virtual' ? 'by video' : 'by telephone'} on {dateLong(e.meetingDate)}.
        </p>
        {e.adviceType === 'limited' && (
          <Note tone="warning">
            {e.limitations && <strong className="block text-ink">{e.limitations}</strong>}
            {DISCLAIMERS.limited}
          </Note>
        )}
        {e.clientDeclinedFna && (
          <Note tone="warning">
            {e.declineReason && <strong className="block text-ink">{e.declineReason}</strong>}
            {DISCLAIMERS.declined}
          </Note>
        )}
        {doc.advice.summary && <p className="whitespace-pre-line text-[11px] leading-[1.65] text-ink-2">{doc.advice.summary}</p>}

        {recs.length === 0 ? (
          <p className="text-[11px] text-muted">No recommendations have been recorded.</p>
        ) : (
          <ol className="space-y-2.5">
            {recs.map((r, i) => (
              <li key={r.id} className="avoid-break border-b border-line/70 pb-2.5 last:border-0">
                <div className="flex items-baseline justify-between gap-4">
                  <div className="text-[11.5px] font-semibold text-ink">
                    {i + 1}. {ACTION[r.action]}: {recName(r)}
                    <span className="font-normal text-muted"> · {first(doc, r.lifeAssured)}</span>
                  </div>
                  <div className="shrink-0 text-[10.5px] text-ink-2 tabular">
                    {amountText(r)}
                    {r.premiumMonthly ? ` · ${money(r.premiumMonthly)} p.m.` : ''}
                    {r.provider ? ` · ${r.provider}` : ''}
                    <span className="ml-2 font-semibold text-ink">{cap(r.clientDecision)}</span>
                  </div>
                </div>
                {r.rationale && <p className="mt-0.5 text-[10.5px] leading-[1.55] text-ink-2">{r.rationale}</p>}
                {r.clientReason && r.clientDecision !== 'accepted' && <p className="mt-0.5 text-[10.5px] text-muted">Client’s reason: {r.clientReason}</p>}
                {r.isReplacement && (
                  <div className="mt-1.5 rounded border border-warning/50 bg-warning-soft/50 px-2.5 py-1.5 text-[10px] leading-[1.5] text-ink-2">
                    <strong className="text-ink">Replaces {r.replacedPolicy || 'an existing product'}.</strong> {r.replacementCostComparison && `Cost: ${r.replacementCostComparison}. `}
                    {r.replacementConsequences && `Consequences: ${r.replacementConsequences}. `}
                    {r.replacementReasons && `Why more suitable: ${r.replacementReasons}. `}
                    Factors discussed: {REPLACEMENT_FACTORS.filter((f) => r.replacementChecklist.includes(f.key)).map((f) => f.label.toLowerCase()).join('; ') || 'none recorded'}.
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
        {replacements.length > 0 && <Note tone="warning">{DISCLAIMERS.replacement}</Note>}

        {patterned.length > 0 && (
          <div className="avoid-break">
            <SubHeading aside="Monthly premium or contribution at the stated escalation">Cost over 20 years</SubHeading>
            <Table
              head={['Recommendation', ...PATTERN_YEARS.map((y) => `Yr ${y}`)]}
              align={['l', ...PATTERN_YEARS.map(() => 'r' as const)]}
              rows={patterned.map((r) => [
                `${recs.indexOf(r) + 1}. ${recName(r)} (${first(doc, r.lifeAssured)}, +${pct(r.premiumEscalation, 1)})`,
                ...PATTERN_YEARS.map((y) => money(r.premiumMonthly * Math.pow(1 + r.premiumEscalation, y - 1))),
              ])}
            />
          </div>
        )}

        {(doc.advice.productsConsidered || declined.length > 0 || doc.advice.clientDeviations) && (
          <div className="avoid-break space-y-2 text-[10.5px] leading-[1.55] text-ink-2">
            {doc.advice.productsConsidered && (
              <p>
                <span className="font-semibold text-ink">Products considered but not recommended: </span>
                {doc.advice.productsConsidered}
              </p>
            )}
            {(declined.length > 0 || doc.advice.clientDeviations) && (
              <>
                <p>
                  <span className="font-semibold text-ink">Where you chose not to follow the advice: </span>
                  {declined.map((r) => `${recName(r)} for ${first(doc, r.lifeAssured)} (${r.clientDecision})`).join('; ')}
                  {declined.length && doc.advice.clientDeviations ? '. ' : ''}
                  {doc.advice.clientDeviations}
                </p>
                <Note tone="warning">{DISCLAIMERS.departure}</Note>
              </>
            )}
          </div>
        )}
        <div className="grid grid-cols-2 gap-6 text-[10.5px] leading-[1.55] text-ink-2">
          <p>
            <span className="font-semibold text-ink">Fees and commission: </span>
            {doc.advice.feesAndCommission || '—'}
          </p>
          <p>
            <span className="font-semibold text-ink">Conflicts of interest: </span>
            {doc.advice.conflictsOfInterest || 'None identified in respect of this advice.'}
            {doc.advice.nextReviewDate && (
              <>
                {' '}
                <span className="font-semibold text-ink">Next review: </span>
                {dateLong(doc.advice.nextReviewDate)}.
              </>
            )}
          </p>
        </div>
      </ReportSection>

      {/* ================================================= 6. Assumptions, disclosures & declarations */}
      <ReportSection number={no()} title="Assumptions, disclosures and declarations" breakBefore={false}>
        <div className="avoid-break grid grid-cols-2 gap-6">
          <KV
            cols={1}
            rows={[
              ['Inflation (CPI)', pct(as.cpi, 1)],
              ['Salary & contribution growth', pct(as.salaryEscalation, 1)],
              ['Return before / after retirement', `${pct(as.preRetirementReturn, 1)} / ${pct(as.postRetirementReturn, 1)}`],
              ['Return on capital for dependants', pct(as.riskCapitalReturn, 1)],
              ['Education inflation', pct(as.educationInflation, 1)],
            ]}
          />
          <KV
            cols={1}
            rows={[
              ['Family income on death', `${pct(as.deathIncomeReplacement)} of after-tax income`],
              ['Income protection target', `${pct(as.incomeProtectionTarget)} of after-tax income`],
              ['Severe illness', `${as.severeIllnessMonths} months’ gross income`],
              ['Emergency fund', `${as.emergencyMonths} months`],
              ['Tax tables', `SARS ${t.label}`],
            ]}
          />
        </div>

        <div className="avoid-break grid grid-cols-2 gap-6">
          <div>
            <SubHeading>Your financial services provider</SubHeading>
            <KV
              cols={1}
              rows={[
                ['FSP', [practice.fspName, practice.fspNumber && `FSP ${practice.fspNumber}`].filter(Boolean).join(' · ')],
                ['Representative', [practice.adviserName, practice.representativeNumber].filter(Boolean).join(' · ')],
                ['Supervision', practice.underSupervision ? `Under supervision of ${practice.supervisor || '—'}` : 'Not under supervision'],
                ['PI cover', practice.piCover || 'Held'],
                ['Compliance officer', [practice.complianceOfficer, practice.complianceOfficerContact].filter(Boolean).join(' · ')],
                ['Contact', [practice.adviserPhone, practice.adviserEmail].filter(Boolean).join(' · ')],
              ]}
            />
            {practice.categories && <p className="mt-1.5 text-[9.5px] leading-[1.45] text-muted">Licensed: {practice.categories}</p>}
          </div>
          <div>
            <SubHeading>Complaints</SubHeading>
            <p className="mb-1.5 text-[10px] leading-[1.5] text-ink-2">
              Write to {practice.complaintsContact || practice.complianceOfficerContact || 'the FSP'}. If unresolved within six weeks, you may approach:
            </p>
            <ul className="space-y-1 text-[9.5px] leading-[1.45]">
              {OMBUDS.map((o) => (
                <li key={o.name}>
                  <span className="font-semibold text-ink">{o.name}</span> <span className="text-muted">— {o.contact}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="columns-2 gap-6 text-[9.5px] leading-[1.5] text-muted [&>p]:mb-1.5">
          <p>{DISCLAIMERS.projections}</p>
          <p>{DISCLAIMERS.pastPerformance}</p>
          <p>{DISCLAIMERS.tax}</p>
          <p>{DISCLAIMERS.nonDisclosure}</p>
          <p>{DISCLAIMERS.popia}</p>
          <p>{DISCLAIMERS.records}</p>
        </div>

        <div className="avoid-break">
          <SubHeading>Declarations</SubHeading>
          <p className="text-[10px] leading-[1.55] text-ink-2">
            <strong className="text-ink">Client:</strong> I confirm that the information I provided is true and complete; that I received the FSP and representative disclosures; that
            the advice, costs and any replacement consequences were explained in a way I understood; that I received a copy of this record; and that I consent to the processing of my
            personal information as described. <strong className="text-ink">Adviser:</strong> I confirm that I analysed the client’s needs, objectives, financial situation and risk
            profile, and recommended appropriate products in accordance with the FAIS Act and General Code of Conduct.
          </p>
          <div className="mt-8 grid grid-cols-3 gap-8">
            {people.map((k) => (
              <Signature key={k} label={fullName(doc, k)} date={doc.advice.clientSignedAt ? dateLong(doc.advice.clientSignedAt) : undefined} />
            ))}
            <Signature label={`Adviser: ${practice.adviserName || '—'}`} date={doc.advice.adviserSignedAt ? dateLong(doc.advice.adviserSignedAt) : undefined} />
          </div>
        </div>
      </ReportSection>

      {/* ================================================= Appendix (optional) */}
      {appendix && (
        <ReportSection title="Appendix: detailed calculations" intro="Line-by-line calculations and the full information register, for the adviser’s file.">
          {P.map((p) => (
            <div key={p.key} className="space-y-5">
              {[p.death, p.incomeProtection, p.disability, p.severeIllness, p.funeral].map((need) => (
                <div key={need.area} className="avoid-break">
                  <SubHeading aside={STATUS_WORD[need.status]}>
                    {need.title} — {p.name}
                  </SubHeading>
                  <NeedBreakdown need={need} dense />
                  {need.notes.length > 0 && (
                    <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[10px] leading-[1.5] text-muted">
                      {need.notes.map((x) => (
                        <li key={x}>{x}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
              <div className="avoid-break">
                <SubHeading>Estate costs — {p.name}</SubHeading>
                <Table head={['Cash needed by the executor', 'Amount']} align={['l', 'r']} rows={p.estate.lines.filter((l) => l.amount > 0).map((l) => [l.label, money(l.amount)])} />
              </div>
            </div>
          ))}
          <div className="avoid-break">
            <SubHeading>Asset register</SubHeading>
            <Table
              head={['Asset', 'Type', 'Owner', 'Value', 'Base cost']}
              align={['l', 'l', 'l', 'r', 'r']}
              rows={[
                ...doc.assets.map((x) => [x.description || ASSET_TYPE[x.type], ASSET_TYPE[x.type], x.owner === 'joint' ? 'Joint' : first(doc, x.owner), money(x.value), x.baseCost ? money(x.baseCost) : '—']),
                ...doc.retirementFunds.map((f) => [f.name || RETIREMENT_FUND_TYPE[f.type], RETIREMENT_FUND_TYPE[f.type], first(doc, f.owner), money(f.value), '—']),
              ]}
            />
          </div>
          {a.risk.answered > 0 && (
            <div className="avoid-break">
              <SubHeading>Risk profile questionnaire</SubHeading>
              <Table head={['Question', 'Answer']} rows={RISK_QUESTIONS.map((q) => [q.question, q.options[doc.riskProfile.answers[q.id]]?.label ?? 'Not answered'])} />
            </div>
          )}
          <div className="avoid-break">
            <SubHeading>Sources</SubHeading>
            <ul className="list-disc space-y-0.5 pl-4 text-[9.5px] leading-[1.5] text-muted">
              {t.sources.map((s) => (
                <li key={s} className="break-all">
                  {s}
                </li>
              ))}
            </ul>
          </div>
        </ReportSection>
      )}
    </article>
  );
}
