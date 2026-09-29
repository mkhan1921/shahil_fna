'use client';

import { ArrowLeft, ArrowRight, Check, CloudOff, Download, FileText, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState, type ComponentType } from 'react';
import AppShell from '../AppShell';
import ExportDialog from '../ExportDialog';
import AnalysisStep from '../steps/AnalysisStep';
import Advice from '../steps/Advice';
import Assets from '../steps/Assets';
import Assumptions from '../steps/Assumptions';
import Cover from '../steps/Cover';
import Engagement from '../steps/Engagement';
import Estate from '../steps/Estate';
import Expenses from '../steps/Expenses';
import Goals from '../steps/Goals';
import Income from '../steps/Income';
import Liabilities from '../steps/Liabilities';
import Profile from '../steps/Profile';
import ReportStep from '../steps/ReportStep';
import Risk from '../steps/Risk';
import { nameOf } from '../steps/shared';
import { Button, EmptyState, cx } from '../ui';
import LiveSummary from './LiveSummary';
import { STEPS, type StepKey, type StepProps } from './types';
import { useDocument, usePractice } from '@/lib/store/hooks';
import { scoreRiskProfile } from '@/lib/fna/riskProfile';
import type { Analysis } from '@/lib/fna/analysis';
import type { FnaDocument } from '@/lib/fna/types';

const COMPONENTS: Record<StepKey, ComponentType<StepProps>> = {
  engagement: Engagement,
  profile: Profile,
  income: Income,
  expenses: Expenses,
  assets: Assets,
  liabilities: Liabilities,
  cover: Cover,
  goals: Goals,
  estate: Estate,
  risk: Risk,
  assumptions: Assumptions,
  analysis: AnalysisStep,
  advice: Advice,
  report: ReportStep,
};

const stepDone = (key: StepKey, doc: FnaDocument, a: Analysis): boolean | null => {
  switch (key) {
    case 'engagement':
      return doc.engagement.popiaConsent && doc.engagement.disclosureLetterProvided;
    case 'profile':
      return !!(doc.client.firstName && doc.client.dateOfBirth);
    case 'income':
      return a.incomes.some((i) => i.grossMonthly > 0);
    case 'expenses':
      return a.cashflow.livingExpenses > 0;
    case 'assets':
      return doc.assets.length + doc.retirementFunds.length > 0;
    case 'liabilities':
      return doc.liabilities.length > 0 ? true : null;
    case 'cover':
      return doc.policies.length > 0 ? true : null;
    case 'goals':
      return null;
    case 'estate':
      return a.people.every((k) => doc.estate[k].hasWill !== 'unsure');
    case 'risk':
      return scoreRiskProfile(doc.riskProfile.answers).complete;
    case 'advice':
      return doc.advice.recommendations.length > 0;
    default:
      return null;
  }
};

function SaveIndicator({ state }: { state: string }) {
  if (state === 'saving' || state === 'pending')
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-muted">
        <Loader2 size={13} className="animate-spin" /> Saving
      </span>
    );
  if (state === 'error')
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-critical-ink">
        <CloudOff size={13} /> Not saved
      </span>
    );
  if (state === 'saved')
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-muted">
        <Check size={13} className="text-good-ink" /> Saved on this device
      </span>
    );
  return null;
}

export default function Workspace() {
  const params = useSearchParams();
  const router = useRouter();
  const id = params?.get('id') ?? null;
  const stepParam = (params?.get('s') as StepKey) || 'engagement';
  const [step, setStep] = useState<StepKey>(STEPS.some((s) => s.key === stepParam) ? stepParam : 'engagement');
  const { doc, analysis, loading, update, saveState } = useDocument(id);
  const { practice } = usePractice();
  const [exporting, setExporting] = useState(false);

  const go = useCallback(
    (s: StepKey) => {
      setStep(s);
      router.replace(`/client?id=${id}&s=${s}`, { scroll: false });
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    [id, router],
  );

  useEffect(() => {
    if (stepParam !== step && STEPS.some((s) => s.key === stepParam)) setStep(stepParam);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepParam]);

  const index = STEPS.findIndex((s) => s.key === step);
  const done = useMemo(() => (doc && analysis ? Object.fromEntries(STEPS.map((s) => [s.key, stepDone(s.key, doc, analysis)])) : {}), [doc, analysis]);

  if (loading) return <AppShell wide><div className="p-16 text-center text-sm text-muted">Loading client…</div></AppShell>;
  if (!doc || !analysis)
    return (
      <AppShell>
        <div className="mx-auto max-w-lg p-10">
          <EmptyState title="Client not found" description="This client file is not stored in this browser. Import it from an export file." action={<Link href="/" className="text-sm font-medium text-brand underline">Back to clients</Link>} />
        </div>
      </AppShell>
    );

  const Step = COMPONENTS[step];
  const title = analysis.people.map((k) => nameOf(doc, k)).join(' & ');
  const groups = ['Discover', 'Analyse', 'Advise'] as const;

  return (
    <AppShell
      wide
      actions={
        <div className="flex items-center gap-2">
          <SaveIndicator state={saveState} />
          <Button size="sm" variant="ghost" icon={<Download size={15} />} onClick={() => setExporting(true)}>
            <span className="hidden sm:inline">Export</span>
          </Button>
          <Link href={`/report?id=${doc.id}`} target="_blank" className="focus-ring inline-flex h-8 items-center gap-1.5 rounded-lg bg-brand px-3 text-[13px] font-medium text-white hover:bg-brand-2">
            <FileText size={15} /> Report
          </Link>
        </div>
      }
    >
      <div className="mx-auto flex max-w-[1600px] gap-6 px-4 py-6 sm:px-6">
        <aside className="no-print sticky top-20 hidden h-[calc(100vh-6rem)] w-60 shrink-0 overflow-y-auto lg:block">
          <Link href="/" className="mb-4 inline-flex items-center gap-1 text-xs font-medium text-muted hover:text-ink">
            <ArrowLeft size={13} /> All clients
          </Link>
          <div className="mb-5">
            <div className="truncate text-[15px] font-semibold text-ink" title={title}>
              {title}
            </div>
            <div className="mt-0.5 text-xs text-muted">{Math.round(analysis.completenessScore * 100)}% complete</div>
          </div>
          <nav aria-label="FNA steps" className="space-y-5">
            {groups.map((g) => (
              <div key={g}>
                <div className="mb-1.5 px-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">{g}</div>
                <ul className="space-y-0.5">
                  {STEPS.filter((s) => s.group === g).map((s) => {
                    const active = s.key === step;
                    const state = done[s.key];
                    return (
                      <li key={s.key}>
                        <button
                          type="button"
                          onClick={() => go(s.key)}
                          aria-current={active ? 'step' : undefined}
                          className={cx(
                            'focus-ring flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors',
                            active ? 'bg-brand text-white' : 'text-ink-2 hover:bg-surface hover:text-ink',
                          )}
                        >
                          <span
                            className={cx(
                              'flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[9px]',
                              active ? 'border-white/60' : state ? 'border-good bg-good text-white' : 'border-line-strong',
                            )}
                            aria-hidden
                          >
                            {state && !active ? <Check size={10} strokeWidth={3} /> : null}
                          </span>
                          <span className="truncate">{s.label}</span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </nav>
        </aside>

        <main className="min-w-0 flex-1">
          <div className="no-print mb-4 lg:hidden">
            <label className="sr-only" htmlFor="step-select">
              Step
            </label>
            <select id="step-select" className="control" value={step} onChange={(e) => go(e.target.value as StepKey)}>
              {STEPS.map((s, i) => (
                <option key={s.key} value={s.key}>
                  {i + 1}. {s.label}
                </option>
              ))}
            </select>
          </div>

          <Step doc={doc} update={update} analysis={analysis} practice={practice} go={go} />

          <div className="no-print mt-8 flex items-center justify-between border-t border-line pt-5">
            {index > 0 ? (
              <Button icon={<ArrowLeft size={15} />} onClick={() => go(STEPS[index - 1].key)}>
                {STEPS[index - 1].label}
              </Button>
            ) : (
              <span />
            )}
            {index < STEPS.length - 1 && (
              <Button variant="primary" onClick={() => go(STEPS[index + 1].key)}>
                {STEPS[index + 1].label} <ArrowRight size={15} />
              </Button>
            )}
          </div>
        </main>

        {step !== 'analysis' && step !== 'report' && (
          <aside className="no-print sticky top-20 hidden h-[calc(100vh-6rem)] w-72 shrink-0 overflow-y-auto 2xl:block">
            <LiveSummary analysis={analysis} go={go} />
          </aside>
        )}
      </div>
      <ExportDialog doc={doc} open={exporting} onClose={() => setExporting(false)} />
    </AppShell>
  );
}
