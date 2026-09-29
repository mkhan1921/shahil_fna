'use client';

import { CheckCircle2, Circle, ExternalLink, Printer, Wand2 } from 'lucide-react';
import Link from 'next/link';
import type { PersonAnalysis, Status } from '@/lib/fna/analysis';
import { ACTION, NEED_AREA } from '@/lib/fna/catalog';
import { DISCLAIMERS, REPLACEMENT_FACTORS } from '@/lib/fna/compliance';
import { newRecommendation } from '@/lib/fna/defaults';
import { draftRecommendations } from '@/lib/fna/recommend';
import type { ClientDecision } from '@/lib/fna/types';
import { money, moneyCompact, pct } from '@/lib/format';
import { AllocationBar, RetirementChart } from '../../charts';
import Findings from '../../Findings';
import { CompareRows, GlanceTable, NeedCompare, type GlanceCell } from '../../report/Compare';
import { firstNameOf, personOptions } from '../shared';
import { cx } from '../../ui';
import { AddRow, AreaC, Cells, CheckC, DateC, Item, MoneyC, PctC, ReadC, Section, SelectC, Sub, TextC, YesNoC } from '../cells';
import { focusRow } from '../focus';
import type { SectionProps } from '../types';

const estateCell = (p: PersonAnalysis): GlanceCell => {
  const e = p.estate;
  const status: Status = e.cashRequired <= 0 ? 'na' : e.liquidityShortfall <= 0 ? 'covered' : e.cashAvailable / e.cashRequired >= 0.6 ? 'partial' : 'shortfall';
  return { need: e.cashRequired, provision: Math.min(e.cashAvailable, e.cashRequired), shortfall: e.liquidityShortfall, status };
};

const Pad = ({ children, className }: { children: React.ReactNode; className?: string }) => <div className={cx('border-b border-line bg-white px-3 py-2', className)}>{children}</div>;

/* ================================================================== */
/* Analysis                                                            */
/* ================================================================== */

export function AnalysisSection({ doc, analysis: a }: SectionProps) {
  const P = a.people.map((k) => a.persons[k]!);
  const names = a.people.map((k) => firstNameOf(doc, k));
  const cf = a.cashflow;
  const glance: { label: string; cells: (GlanceCell | undefined)[]; household?: boolean }[] = [
    { label: 'Life cover', cells: P.map((p) => p.death) },
    { label: 'Income protection', cells: P.map((p) => ({ ...p.incomeProtection, monthly: true })) },
    { label: 'Disability (lump sum)', cells: P.map((p) => p.disability) },
    { label: 'Severe illness', cells: P.map((p) => p.severeIllness) },
    { label: 'Funeral', cells: P.map((p) => p.funeral) },
    { label: 'Estate liquidity', cells: P.map(estateCell) },
    { label: 'Retirement capital', cells: P.map((p) => ({ need: p.retirement.requiredCapital, provision: p.retirement.projectedCapital, shortfall: p.retirement.shortfall, status: p.retirement.status })) },
    { label: 'Emergency fund', cells: [a.emergency], household: true },
  ];
  if (a.educationTotal.need > 0) glance.push({ label: 'Education (PV)', cells: [a.educationTotal], household: true });
  const critical = a.findings.filter((f) => f.severity === 'critical').length;

  return (
    <Section id="analysis" title="12 · Needs analysis" right={<span>{critical} critical finding{critical === 1 ? '' : 's'} · updates as you type</span>}>
      <Cells>
        <ReadC label="Take-home p.m." value={money(cf.takeHome)} span={2} />
        <ReadC label={cf.surplus < 0 ? 'Deficit p.m.' : 'Surplus p.m.'} value={money(cf.surplus)} tone={cf.surplus < 0 ? 'bad' : 'good'} span={2} />
        <ReadC label="Net worth" value={moneyCompact(a.netWorth.netWorth)} span={2} />
        <ReadC label="Savings rate" value={pct(a.ratios.savingsRate)} sub="≥ 15%" tone={a.ratios.savingsRate >= 0.15 ? 'good' : undefined} span={2} />
        <ReadC label="Debt / gross" value={pct(a.ratios.debtToIncome)} sub="< 36%" tone={a.ratios.debtToIncome > 0.36 ? 'bad' : undefined} span={2} />
        <ReadC label="Emergency fund" value={`${a.ratios.emergencyMonths.toFixed(1)} months`} tone={a.emergency.status === 'covered' ? 'good' : 'bad'} span={2} />
      </Cells>
      <div className="grid border-b border-line xl:grid-cols-[1.25fr_1fr]">
        <div className="border-line bg-white px-3 py-2 xl:border-r">
          <GlanceTable names={names} rows={glance} />
        </div>
        <div className="max-h-[22rem] overflow-y-auto bg-white px-3 py-1">
          <Findings findings={a.findings} dense />
        </div>
      </div>
      <Pad>
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
      </Pad>

      <Sub right={<span>Family income {pct(doc.assumptions.deathIncomeReplacement)} of after-tax for {P.map((p) => `${p.name.split(' ')[0]} ${p.deathIncomeYears} yrs`).join(', ')}</span>}>Life cover — if death occurred today</Sub>
      <Pad>
        <NeedCompare names={names} needs={P.map((p) => p.death)} />
      </Pad>
      <div className="grid border-b border-line xl:grid-cols-2">
        <div className="border-line xl:border-r">
          <Sub>Income protection (monthly, after tax)</Sub>
          <Pad className="border-b-0">
            <NeedCompare names={names} needs={P.map((p) => p.incomeProtection)} monthly />
          </Pad>
          <Sub>Disability lump sum</Sub>
          <Pad className="border-b-0">
            <NeedCompare names={names} needs={P.map((p) => p.disability)} />
          </Pad>
        </div>
        <div>
          <Sub>Severe illness & funeral</Sub>
          <Pad className="border-b-0">
            <CompareRows
              names={names}
              strongRows={['Severe illness shortfall', 'Funeral shortfall']}
              rows={[
                { label: `Severe illness need (${doc.assumptions.severeIllnessMonths} months)`, values: P.map((p) => money(p.severeIllness.need)) },
                { label: 'Less existing cover', values: P.map((p) => money(-p.severeIllness.provision)) },
                { label: 'Severe illness shortfall', values: P.map((p) => money(p.severeIllness.shortfall)) },
                { label: 'Funeral cost', values: P.map((p) => money(p.funeral.need)) },
                { label: 'Funeral shortfall', values: P.map((p) => money(p.funeral.shortfall)) },
              ]}
            />
          </Pad>
          <Sub>Estate liquidity & duty</Sub>
          <Pad className="border-b-0">
            <CompareRows
              names={names}
              strongRows={['Estate duty', 'Liquidity shortfall']}
              rows={[
                { label: 'Gross estate / deemed property', values: P.map((p) => `${moneyCompact(p.estate.grossEstate)} / ${moneyCompact(p.estate.deemedProperty)}`) },
                { label: 'Executor’s fees', values: P.map((p) => money(p.estate.executorFees)) },
                { label: 'CGT on death', values: P.map((p) => money(p.estate.cgt)) },
                { label: 'Estate duty', values: P.map((p) => money(p.estate.estateDuty)) },
                { label: 'Cash required / available', values: P.map((p) => `${moneyCompact(p.estate.cashRequired)} / ${moneyCompact(p.estate.cashAvailable)}`) },
                { label: 'Liquidity shortfall', values: P.map((p) => (p.estate.liquidityShortfall > 0 ? money(p.estate.liquidityShortfall) : 'None')) },
              ]}
            />
          </Pad>
        </div>
      </div>

      <Sub>Retirement projection (today’s money)</Sub>
      <div className={cx('grid gap-px border-b border-line bg-line', P.length > 1 && 'xl:grid-cols-2')}>
        {P.map((p) => (
          <div key={p.key} className="bg-white px-3 py-2">
            <div className="mb-1 text-[11.5px] font-semibold text-ink">{p.name}</div>
            <RetirementChart timeline={p.retirement.timeline} retirementAge={p.retirement.retirementAge} requiredReal={p.retirement.requiredCapitalReal} height={190} viewWidth={P.length > 1 ? 460 : 720} />
          </div>
        ))}
      </div>
      {P.flatMap((p) => p.retirement.notes.filter((n) => n.includes('drawdown') || n.includes('17.5%')).map((n) => ({ n, who: p.name }))).map(({ n, who }) => (
        <div key={who + n} className="border-b border-line bg-warning-soft px-3 py-1 text-[11px] text-ink-2">
          <strong>{who.split(' ')[0]}:</strong> {n}
        </div>
      ))}
    </Section>
  );
}

/* ================================================================== */
/* Advice                                                              */
/* ================================================================== */

const RISK_AREAS = ['life', 'disability', 'income-protection', 'severe-illness', 'funeral'];

export function AdviceSection({ doc, update, analysis }: SectionProps) {
  const adv = doc.advice;
  const recs = adv.recommendations;
  const newCosts = recs.filter((r) => r.clientDecision !== 'declined').reduce((s, r) => s + (r.premiumMonthly || 0), 0);
  const after = analysis.cashflow.surplus - newCosts;
  const lives = personOptions(doc);

  const draft = () => {
    const drafts = draftRecommendations(doc, analysis);
    update((d) => {
      for (const r of drafts) {
        if (!d.advice.recommendations.some((x) => x.area === r.area && x.lifeAssured === r.lifeAssured && x.productType === r.productType)) d.advice.recommendations.push(r);
      }
    });
  };

  return (
    <Section
      id="advice"
      title="13 · Advice & record of advice"
      right={
        <>
          <span>
            {recs.length} recommendations · new cost <strong className="text-ink tabular">{money(newCosts)}</strong> p.m. · after{' '}
            <strong className={after < 0 ? 'text-critical-ink tabular' : 'text-good-ink tabular'}>{money(after)}</strong>
          </span>
          <button type="button" onClick={draft} className="focus-ring inline-flex items-center gap-1 rounded bg-brand px-2 py-0.5 font-semibold text-white hover:bg-brand-2">
            <Wand2 size={12} /> Draft from analysis
          </button>
        </>
      }
    >
      <Cells>
        <AreaC label="Summary of advice (plain language)" value={adv.summary} onChange={(v) => update((d) => void (d.advice.summary = v))} rows={2} />
      </Cells>
      <Sub right={<span>GCC s9: product, reasons, replacement, client decision</span>}>Recommendations</Sub>
      {recs.map((r, i) => {
        const set = (fn: (x: typeof r) => void) => update((d) => fn(d.advice.recommendations[i]));
        const monthlyBenefit = r.area === 'income-protection';
        return (
          <Item key={r.id} id={r.id} n={i + 1} onRemove={() => update((d) => void d.advice.recommendations.splice(i, 1))}>
            <SelectC label="Need" value={r.area} onChange={(v) => set((x) => void (x.area = v))} options={NEED_AREA} span={2} />
            <SelectC label="Action" value={r.action} onChange={(v) => set((x) => void (x.action = v))} options={ACTION} span={1} />
            {lives.length > 1 && <SelectC label="For" value={r.lifeAssured} onChange={(v) => set((x) => void (x.lifeAssured = v))} options={lives} span={1} />}
            <TextC label="Product type" value={r.productType} onChange={(v) => set((x) => void (x.productType = v))} span={3} />
            <TextC label="Supplier" value={r.provider} onChange={(v) => set((x) => void (x.provider = v))} span={2} />
            <TextC label="Product" value={r.productName} onChange={(v) => set((x) => void (x.productName = v))} span={2} />
            <MoneyC label={monthlyBenefit ? 'Benefit p.m.' : RISK_AREAS.includes(r.area) ? 'Cover' : 'Amount'} value={r.amount} onChange={(v) => set((x) => void (x.amount = v))} span={2} />
            <MoneyC label="Premium p.m." value={r.premiumMonthly} onChange={(v) => set((x) => void (x.premiumMonthly = v))} span={1} />
            <PctC label="Esc. p.a." value={r.premiumEscalation} onChange={(v) => set((x) => void (x.premiumEscalation = v))} span={1} />
            <SelectC
              label="Client decision"
              value={r.clientDecision}
              onChange={(v: ClientDecision) => set((x) => void (x.clientDecision = v))}
              options={{ pending: 'Pending', accepted: 'Accepted', deferred: 'Deferred', declined: 'Declined' }}
              span={2}
            />
            {(r.clientDecision === 'declined' || r.clientDecision === 'deferred') && <TextC label="Client’s reason" value={r.clientReason} onChange={(v) => set((x) => void (x.clientReason = v))} span={3} />}
            <YesNoC label="Replacement" value={r.isReplacement} onChange={(v) => set((x) => void (x.isReplacement = v))} span={1} />
            <AreaC label="Why this is suitable (s9(1)(c))" value={r.rationale} onChange={(v) => set((x) => void (x.rationale = v))} span={12} />
            {r.isReplacement && (
              <>
                <TextC label="Product replaced (insurer, policy, cover, premium)" value={r.replacedPolicy} onChange={(v) => set((x) => void (x.replacedPolicy = v))} span={12} />
                {REPLACEMENT_FACTORS.map((f) => (
                  <CheckC
                    key={f.key}
                    span={4}
                    label={f.label}
                    checked={r.replacementChecklist.includes(f.key)}
                    onChange={(on) =>
                      set((x) => {
                        const s = new Set(x.replacementChecklist);
                        if (on) s.add(f.key);
                        else s.delete(f.key);
                        x.replacementChecklist = [...s];
                      })
                    }
                  />
                ))}
                <AreaC label="Cost comparison" value={r.replacementCostComparison} onChange={(v) => set((x) => void (x.replacementCostComparison = v))} span={4} />
                <AreaC label="Consequences (benefits lost, waiting periods…)" value={r.replacementConsequences} onChange={(v) => set((x) => void (x.replacementConsequences = v))} span={4} />
                <AreaC label="Why replacing is more suitable" value={r.replacementReasons} onChange={(v) => set((x) => void (x.replacementReasons = v))} span={4} />
              </>
            )}
          </Item>
        );
      })}
      <AddRow
        label="Add recommendation"
        empty={recs.length ? undefined : 'Or use “Draft from analysis” to start from the shortfalls'}
        onAdd={() => {
          const r = newRecommendation();
          update((d) => void d.advice.recommendations.push(r));
          focusRow(r.id);
        }}
      />
      {recs.some((r) => r.isReplacement) && <div className="border-b border-line bg-warning-soft px-3 py-1 text-[11px] text-ink-2">{DISCLAIMERS.replacement}</div>}
      <Sub>Record of advice</Sub>
      <Cells>
        <AreaC label="Products considered but not recommended, and why (s9(1)(b))" value={adv.productsConsidered} onChange={(v) => update((d) => void (d.advice.productsConsidered = v))} span={6} />
        <AreaC label="Where the client departed from the advice" value={adv.clientDeviations} onChange={(v) => update((d) => void (d.advice.clientDeviations = v))} span={6} />
        <AreaC label="Fees & commission (in rand)" value={adv.feesAndCommission} onChange={(v) => update((d) => void (d.advice.feesAndCommission = v))} span={6} />
        <AreaC label="Conflicts of interest" value={adv.conflictsOfInterest} onChange={(v) => update((d) => void (d.advice.conflictsOfInterest = v))} span={6} />
        <DateC label="Next review" value={adv.nextReviewDate} onChange={(v) => update((d) => void (d.advice.nextReviewDate = v))} span={2} />
        <DateC label="Client signed" value={adv.clientSignedAt} onChange={(v) => update((d) => void (d.advice.clientSignedAt = v))} span={2} />
        <DateC label="Adviser signed" value={adv.adviserSignedAt} onChange={(v) => update((d) => void (d.advice.adviserSignedAt = v))} span={2} />
        <CheckC label="Copy of record given to client (s9(2))" checked={doc.engagement.copyProvided} onChange={(v) => update((d) => void (d.engagement.copyProvided = v))} span={3} />
        <CheckC label="Advice given — mark client complete" checked={doc.status === 'advice-given'} onChange={(v) => update((d) => void (d.status = v ? 'advice-given' : 'in-progress'))} span={3} />
      </Cells>
    </Section>
  );
}

/* ================================================================== */
/* Report & compliance checklist                                       */
/* ================================================================== */

export function ReportSection({ doc, analysis, practice }: SectionProps) {
  const e = doc.engagement;
  const recs = doc.advice.recommendations;
  const checks: { label: string; ok: boolean; href: string }[] = [
    { label: 'Practice: FSP name, licence no. & adviser', ok: !!(practice?.fspName && practice.fspNumber && practice.adviserName), href: '/settings' },
    { label: 'POPIA consent', ok: e.popiaConsent, href: '#engagement' },
    { label: 'FSP disclosures given', ok: e.disclosureLetterProvided, href: '#engagement' },
    { label: 'FICA verified', ok: e.ficaVerified, href: '#engagement' },
    { label: 'Objectives recorded', ok: !!e.clientObjectives.trim(), href: '#engagement' },
    { label: 'Dates of birth captured', ok: analysis.incomes.every((i) => i.ageKnown), href: '#people' },
    { label: 'Risk profile complete', ok: analysis.risk.complete, href: '#risk' },
    { label: 'Recommendations recorded', ok: recs.length > 0, href: '#advice' },
    { label: 'Every recommendation has a reason', ok: recs.length > 0 && recs.every((r) => r.rationale.trim().length > 20), href: '#advice' },
    { label: 'Replacement disclosures complete', ok: recs.filter((r) => r.isReplacement).every((r) => r.replacementReasons && r.replacementConsequences && r.replacementChecklist.length >= 5), href: '#advice' },
    { label: 'Client decisions captured', ok: recs.length > 0 && recs.every((r) => r.clientDecision !== 'pending'), href: '#advice' },
  ];
  const done = checks.filter((c) => c.ok).length;
  return (
    <Section
      id="report"
      title="14 · Report"
      right={
        <>
          <span>
            Compliance {done}/{checks.length}
          </span>
          <Link href={`/report?id=${doc.id}`} target="_blank" className="inline-flex items-center gap-1 font-semibold text-brand-2 hover:underline">
            <ExternalLink size={12} /> Open report
          </Link>
          <Link href={`/report?id=${doc.id}&print=1`} target="_blank" className="inline-flex items-center gap-1 rounded bg-brand px-2 py-0.5 font-semibold text-white hover:bg-brand-2">
            <Printer size={12} /> Print / PDF
          </Link>
        </>
      }
    >
      <div className="grid gap-px bg-line sm:grid-cols-2 xl:grid-cols-3">
        {checks.map((c) => (
          <a key={c.label} href={c.href} tabIndex={-1} className="flex items-center gap-2 bg-white px-3 py-1.5 text-[12px] hover:bg-wash">
            {c.ok ? <CheckCircle2 size={14} className="shrink-0 text-good-ink" aria-label="done" /> : <Circle size={14} className="shrink-0 text-faint" aria-label="outstanding" />}
            <span className={c.ok ? 'text-ink' : 'text-ink-2'}>{c.label}</span>
          </a>
        ))}
      </div>
    </Section>
  );
}
