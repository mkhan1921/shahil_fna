'use client';

import { ArrowRight, BarChart3, Landmark, ListChecks, Sunset, Umbrella } from 'lucide-react';
import { useState } from 'react';
import type { Status } from '@/lib/fna/analysis';
import type { PersonKey } from '@/lib/fna/types';
import { money, moneyCompact, pct } from '@/lib/format';
import { AllocationBar, NeedMeters, RetirementChart } from '../charts';
import Findings from '../Findings';
import NeedBreakdown from '../NeedBreakdown';
import { Button, Card, CardBody, CardHeader, Row, Segmented, Stat, StatusPill, StepHeader } from '../ui';
import type { StepProps } from '../workspace/types';
import { firstNameOf } from './shared';

type ScoreRow = { label: string; sub?: string; need: number; provision: number; shortfall: number; status: Status; monthly?: boolean };

export default function AnalysisStep({ doc, analysis, go }: StepProps) {
  const [who, setWho] = useState<PersonKey>('client');
  const person = analysis.persons[who] ?? analysis.persons.client!;
  const cf = analysis.cashflow;
  const r = person.retirement;

  const scoreRows: ScoreRow[] = analysis.people.flatMap((k): ScoreRow[] => {
    const p = analysis.persons[k]!;
    const sub = analysis.people.length > 1 ? firstNameOf(doc, k) : undefined;
    return [
      { label: 'Life cover', sub, ...p.death },
      { label: 'Income protection', sub, ...p.incomeProtection },
      { label: 'Disability (lump sum)', sub, ...p.disability },
      { label: 'Severe illness', sub, ...p.severeIllness },
      { label: 'Estate liquidity', sub, need: p.estate.cashRequired, provision: Math.min(p.estate.cashAvailable, p.estate.cashRequired), shortfall: p.estate.liquidityShortfall, status: p.estate.cashRequired <= 0 ? ('na' as const) : p.estate.liquidityShortfall > 0 ? (p.estate.cashAvailable / p.estate.cashRequired >= 0.6 ? ('partial' as const) : ('shortfall' as const)) : ('covered' as const) },
      { label: 'Retirement capital', sub, need: p.retirement.requiredCapital, provision: p.retirement.projectedCapital, shortfall: p.retirement.shortfall, status: p.retirement.status },
    ];
  });
  scoreRows.push({ label: 'Emergency fund', sub: 'Household', ...analysis.emergency });
  if (analysis.educationTotal.need > 0) scoreRows.push({ label: 'Education', sub: 'Present value', ...analysis.educationTotal });

  return (
    <>
      <StepHeader
        eyebrow="Step 12"
        title="Needs analysis"
        description="Every need compared with existing provision, based on the information and assumptions captured. Use this to discuss priorities with the client before recording advice."
        actions={
          <Button variant="primary" icon={<ArrowRight size={15} />} onClick={() => go('advice')}>
            Record advice
          </Button>
        }
      />
      <div className="space-y-6">
        <div className="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
          <Card>
            <CardHeader icon={<BarChart3 size={18} />} title="Needs scorecard" description="Bars show the share of each need already provided for." />
            <CardBody className="py-2">
              <NeedMeters rows={scoreRows} />
            </CardBody>
          </Card>
          <Card>
            <CardHeader icon={<ListChecks size={18} />} title="Key findings" description={`${analysis.findings.filter((f) => f.severity === 'critical').length} critical`} />
            <CardBody className="max-h-[36rem] overflow-y-auto py-1">
              <Findings findings={analysis.findings} />
            </CardBody>
          </Card>
        </div>

        <Card>
          <CardHeader title="Household finances" />
          <CardBody className="space-y-6">
            <div className="grid grid-cols-2 gap-6 lg:grid-cols-5">
              <Stat label="Take-home pay" value={money(cf.takeHome)} sub="per month" />
              <Stat label={cf.surplus < 0 ? 'Monthly deficit' : 'Monthly surplus'} value={money(cf.surplus)} tone={cf.surplus < 0 ? 'critical' : undefined} />
              <Stat label="Net worth" value={moneyCompact(analysis.netWorth.netWorth)} />
              <Stat label="Savings rate" value={pct(analysis.ratios.savingsRate)} sub="Target 15%+ of gross" tone={analysis.ratios.savingsRate >= 0.15 ? 'good' : undefined} />
              <Stat label="Debt / gross income" value={pct(analysis.ratios.debtToIncome)} sub="Guideline < 36%" tone={analysis.ratios.debtToIncome > 0.36 ? 'critical' : undefined} />
            </div>
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
          </CardBody>
        </Card>

        {analysis.people.length > 1 && (
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-ink">Detailed analysis</h3>
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted">Show analysis for</span>
              <Segmented value={who} onChange={setWho} options={analysis.people.map((k) => ({ value: k, label: firstNameOf(doc, k) }))} />
            </div>
          </div>
        )}

        <Card>
          <CardHeader icon={<Umbrella size={18} />} title={`Life cover — if ${person.name} dies`} actions={<StatusPill status={person.death.status} />} />
          <CardBody className="space-y-4">
            <NeedBreakdown need={person.death} />
            <ul className="list-disc space-y-1 pl-5 text-xs leading-5 text-muted">
              {person.death.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </CardBody>
        </Card>

        <div className="grid gap-6 xl:grid-cols-2">
          {[person.incomeProtection, person.disability, person.severeIllness, person.funeral].map((n) => (
            <Card key={n.area}>
              <CardHeader title={n.title} actions={<StatusPill status={n.status} />} />
              <CardBody className="space-y-3">
                <NeedBreakdown need={n} />
                {n.notes.length > 0 && (
                  <ul className="list-disc space-y-1 pl-5 text-xs leading-5 text-muted">
                    {n.notes.map((x) => (
                      <li key={x}>{x}</li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>
          ))}
        </div>

        <Card>
          <CardHeader icon={<Sunset size={18} />} title={`Retirement — ${person.name}`} actions={<StatusPill status={r.status} />} />
          <CardBody className="grid gap-8 lg:grid-cols-[1fr_18rem]">
            <RetirementChart timeline={r.timeline} retirementAge={r.retirementAge} requiredReal={r.requiredCapitalReal} />
            <div className="space-y-0.5">
              <Row label="Years to retirement" value={r.yearsToRetirement.toFixed(1)} />
              <Row label="Target income (today)" value={`${money(r.targetMonthlyToday)} p.m.`} />
              <Row label="Target at retirement (nominal)" value={`${money(r.targetMonthlyAtRetirement + r.otherIncomeToday * Math.pow(1 + doc.assumptions.cpi, r.yearsToRetirement))} p.m.`} muted />
              <Row label="Projected capital" value={money(r.projectedCapital)} />
              <Row label="Capital required" value={money(r.requiredCapital)} />
              <Row label="Shortfall at retirement" value={money(r.shortfall)} strong />
              <div className="my-1 border-t border-line" />
              <Row label="Sustainable income (today)" value={`${money(r.sustainableMonthlyToday)} p.m.`} />
              <Row label="Income replacement" value={`${pct(r.replacementRatio)} of ${pct(r.targetReplacementRatio)}`} />
              <Row label="Initial drawdown needed" value={pct(r.initialDrawdown, 1)} />
              <Row label="Capital lasts to age" value={r.depletionAge ? Math.floor(r.depletionAge).toString() : `${r.planningAge}+`} />
              <Row label="Extra saving to close gap" value={r.additionalMonthly > 0 ? `${money(r.additionalMonthly)} p.m.` : '—'} strong />
              <ul className="list-disc space-y-1 pl-5 pt-3 text-xs leading-5 text-muted">
                {r.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader icon={<Landmark size={18} />} title={`Estate — ${person.name}`} description="Estimated costs of winding up the estate if death occurred today." />
          <CardBody className="grid gap-8 lg:grid-cols-2">
            <div className="space-y-0.5 text-sm">
              <Row label="Gross estate (through executor)" value={money(person.estate.grossEstate)} />
              <Row label="Deemed property (policies)" value={money(person.estate.deemedProperty)} muted />
              <Row label="Liabilities & costs" value={money(person.estate.liabilities + person.estate.totalCosts - person.estate.estateDuty - person.estate.cgt)} muted />
              <Row label="Net estate" value={money(person.estate.netEstate)} />
              <Row label="Less spousal bequests s4(q)" value={money(person.estate.spouseDeduction)} muted />
              <Row label="Less abatement s4A" value={money(person.estate.abatement)} muted />
              <Row label="Dutiable amount" value={money(person.estate.dutiable)} />
              <Row label="Estate duty" value={money(person.estate.estateDuty)} strong />
              <Row label="Capital gains subject to CGT" value={money(person.estate.capitalGains)} muted />
            </div>
            <div className="space-y-0.5 text-sm">
              {person.estate.lines.filter((l) => l.amount > 0).map((l) => (
                <Row key={l.label} label={l.label} value={money(l.amount)} />
              ))}
              <div className="my-1 border-t border-line" />
              <Row label="Cash required" value={money(person.estate.cashRequired)} strong />
              <Row label="Liquid assets & estate policies" value={money(person.estate.cashAvailable)} />
              <Row
                label="Liquidity shortfall"
                value={<span className={person.estate.liquidityShortfall > 0 ? 'text-critical-ink' : 'text-good-ink'}>{person.estate.liquidityShortfall > 0 ? money(person.estate.liquidityShortfall) : 'None'}</span>}
                strong
              />
              {person.estate.notes.length > 0 && (
                <ul className="list-disc space-y-1 pl-5 pt-2 text-xs leading-5 text-muted">
                  {person.estate.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              )}
            </div>
          </CardBody>
        </Card>

        {analysis.education.length > 0 && (
          <Card>
            <CardHeader title="Education funding" />
            <CardBody className="overflow-x-auto p-0">
              <table className="w-full min-w-[40rem] text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-muted">
                    <th className="px-5 py-2.5 font-medium">Child</th>
                    <th className="px-5 py-2.5 text-right font-medium">Starts in</th>
                    <th className="px-5 py-2.5 text-right font-medium">Needed at start</th>
                    <th className="px-5 py-2.5 text-right font-medium">Projected savings</th>
                    <th className="px-5 py-2.5 text-right font-medium">Save monthly</th>
                    <th className="px-5 py-2.5 text-right font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="tabular">
                  {analysis.education.map((e) => (
                    <tr key={e.dependantId} className="border-b border-line last:border-0">
                      <td className="px-5 py-2.5">{e.name}</td>
                      <td className="px-5 py-2.5 text-right">{e.capitalAtStart > 0 ? `${Math.round(e.yearsToTertiary)} yrs` : '—'}</td>
                      <td className="px-5 py-2.5 text-right">{money(e.capitalAtStart, { dash: true })}</td>
                      <td className="px-5 py-2.5 text-right">{money(e.projectedSavings, { dash: true })}</td>
                      <td className="px-5 py-2.5 text-right">{e.monthlyRequired > 0 ? money(e.monthlyRequired) : '—'}</td>
                      <td className="px-5 py-2.5 text-right">
                        <StatusPill status={e.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardBody>
          </Card>
        )}
      </div>
    </>
  );
}
