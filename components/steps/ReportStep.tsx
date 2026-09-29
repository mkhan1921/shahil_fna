'use client';

import { CheckCircle2, Circle, ExternalLink, FileText, Printer } from 'lucide-react';
import Link from 'next/link';
import { Button, Callout, Card, CardBody, CardHeader, StepHeader } from '../ui';
import type { StepKey, StepProps } from '../workspace/types';

export default function ReportStep({ doc, analysis, practice, go }: StepProps) {
  const e = doc.engagement;
  const checks: { label: string; ok: boolean; step: StepKey | 'settings' }[] = [
    { label: 'Practice details: FSP name, licence number and representative', ok: !!(practice?.fspName && practice.fspNumber && practice.adviserName), step: 'settings' },
    { label: 'POPIA consent recorded', ok: e.popiaConsent, step: 'engagement' },
    { label: 'FSP disclosures provided', ok: e.disclosureLetterProvided, step: 'engagement' },
    { label: 'FICA verification', ok: e.ficaVerified, step: 'engagement' },
    { label: 'Client objectives recorded', ok: !!e.clientObjectives.trim(), step: 'engagement' },
    { label: 'Client date of birth captured', ok: analysis.incomes.every((i) => i.ageKnown), step: 'profile' },
    { label: 'Risk profile completed', ok: analysis.risk.complete, step: 'risk' },
    { label: 'Recommendations recorded', ok: doc.advice.recommendations.length > 0, step: 'advice' },
    { label: 'Every recommendation has a reason', ok: doc.advice.recommendations.every((r) => r.rationale.trim().length > 20), step: 'advice' },
    {
      label: 'Replacement disclosures complete',
      ok: doc.advice.recommendations.filter((r) => r.isReplacement).every((r) => r.replacementReasons && r.replacementConsequences && r.replacementChecklist.length >= 5),
      step: 'advice',
    },
    { label: 'Client decisions captured', ok: doc.advice.recommendations.every((r) => r.clientDecision !== 'pending'), step: 'advice' },
  ];
  const done = checks.filter((c) => c.ok).length;

  return (
    <>
      <StepHeader
        eyebrow="Step 14"
        title="Report"
        description="A client-ready financial needs analysis and record of advice, formatted for A4. Open it, then print or save as PDF from the browser."
        actions={
          <>
            <Link href={`/report?id=${doc.id}`} target="_blank" className="focus-ring inline-flex h-9 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3.5 text-sm font-medium hover:bg-wash">
              <ExternalLink size={15} /> Open in new tab
            </Link>
            <Link href={`/report?id=${doc.id}&print=1`} target="_blank" className="focus-ring inline-flex h-9 items-center gap-1.5 rounded-lg bg-brand px-3.5 text-sm font-medium text-white hover:bg-brand-2">
              <Printer size={15} /> Print / save PDF
            </Link>
          </>
        }
      />
      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <Card>
          <CardHeader title="Compliance checklist" description={`${done} of ${checks.length} complete`} />
          <CardBody className="py-2">
            <ul className="divide-y divide-line">
              {checks.map((c) => (
                <li key={c.label} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                  <span className="flex items-center gap-2.5">
                    {c.ok ? <CheckCircle2 size={17} className="shrink-0 text-good-ink" aria-label="Complete" /> : <Circle size={17} className="shrink-0 text-faint" aria-label="Outstanding" />}
                    <span className={c.ok ? 'text-ink' : 'text-ink-2'}>{c.label}</span>
                  </span>
                  {!c.ok &&
                    (c.step === 'settings' ? (
                      <Link href="/settings" className="text-xs font-medium text-brand underline underline-offset-2">
                        Fix
                      </Link>
                    ) : (
                      <button type="button" onClick={() => go(c.step as StepKey)} className="text-xs font-medium text-brand underline underline-offset-2">
                        Fix
                      </button>
                    ))}
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardBody className="space-y-3 text-sm text-ink-2">
              <FileText className="text-brand-2" />
              <p>A concise 8–10 page report:</p>
              <ul className="list-disc space-y-1 pl-5 text-[13px]">
                <li>Summary: needs at a glance, findings, recommendations</li>
                <li>Your situation: family, income, cash flow, net worth, cover</li>
                <li>Protection needs &amp; estate liquidity, side by side</li>
                <li>Retirement projections, education &amp; goals, risk profile</li>
                <li>Record of advice with 20-year cost pattern</li>
                <li>Assumptions, FSP disclosures, complaints &amp; signatures</li>
                <li>Optional appendix with line-by-line calculations</li>
              </ul>
            </CardBody>
          </Card>
          <Callout tone="info">In the print dialog choose “Save as PDF”, A4, with background graphics enabled.</Callout>
          <Button className="w-full" onClick={() => go('advice')}>
            Back to advice
          </Button>
        </div>
      </div>
    </>
  );
}
