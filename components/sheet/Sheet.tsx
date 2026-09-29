'use client';

import { ArrowLeft, Check, CloudOff, Download, FileText, Keyboard, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Analysis } from '@/lib/fna/analysis';
import { scoreRiskProfile } from '@/lib/fna/riskProfile';
import type { FnaDocument } from '@/lib/fna/types';
import { useDocument, usePractice } from '@/lib/store/hooks';
import AppShell from '../AppShell';
import ExportDialog from '../ExportDialog';
import { EmptyState, cx } from '../ui';
import { handleEnterNavigation } from './focus';
import LiveSummary from './LiveSummary';
import { BudgetSection, EngagementSection, IncomeSection, PeopleSection } from './sections/Discovery';
import { AssumptionsSection, EstateSection, GoalsSection, RiskSection } from './sections/Planning';
import { AdviceSection, AnalysisSection, ReportSection } from './sections/Results';
import { AssetsSection, CoverSection, LiabilitiesSection } from './sections/Wealth';
import { nameOf } from './shared';

const NAV: { id: string; label: string; done?: (doc: FnaDocument, a: Analysis) => boolean | null }[] = [
  { id: 'engagement', label: 'Engagement & consent', done: (d) => d.engagement.popiaConsent && d.engagement.disclosureLetterProvided },
  { id: 'people', label: 'Client & family', done: (d) => !!(d.client.firstName && d.client.dateOfBirth) },
  { id: 'income', label: 'Income & tax', done: (_d, a) => a.incomes.some((i) => i.grossMonthly > 0) },
  { id: 'budget', label: 'Budget', done: (_d, a) => a.cashflow.livingExpenses > 0 },
  { id: 'assets', label: 'Assets & funds', done: (d) => d.assets.length + d.retirementFunds.length > 0 },
  { id: 'liabilities', label: 'Liabilities', done: (d) => (d.liabilities.length ? true : null) },
  { id: 'cover', label: 'Existing cover', done: (d) => (d.policies.length ? true : null) },
  { id: 'goals', label: 'Goals' },
  { id: 'estate', label: 'Estate', done: (d, a) => a.people.every((k) => d.estate[k].hasWill !== 'unsure') },
  { id: 'risk', label: 'Risk profile', done: (d) => scoreRiskProfile(d.riskProfile.answers).complete },
  { id: 'assumptions', label: 'Assumptions' },
  { id: 'analysis', label: 'Needs analysis' },
  { id: 'advice', label: 'Advice & record', done: (d) => d.advice.recommendations.length > 0 },
  { id: 'report', label: 'Report' },
];

const LEGACY_STEP: Record<string, string> = { profile: 'people', expenses: 'budget' };

function SaveIndicator({ state }: { state: string }) {
  if (state === 'saving' || state === 'pending')
    return (
      <span className="inline-flex items-center gap-1 text-[11.5px] text-muted">
        <Loader2 size={12} className="animate-spin" /> Saving
      </span>
    );
  if (state === 'error')
    return (
      <span className="inline-flex items-center gap-1 text-[11.5px] text-critical-ink">
        <CloudOff size={12} /> Not saved
      </span>
    );
  if (state === 'saved')
    return (
      <span className="inline-flex items-center gap-1 text-[11.5px] text-muted">
        <Check size={12} className="text-good-ink" /> Saved
      </span>
    );
  return null;
}

export default function Sheet() {
  const params = useSearchParams();
  const id = params?.get('id') ?? null;
  const initial = params?.get('s') ?? null;
  const { doc, analysis, loading, update, saveState } = useDocument(id);
  const { practice } = usePractice();
  const [exporting, setExporting] = useState(false);
  const [active, setActive] = useState('engagement');

  const go = useCallback((section: string) => {
    document.getElementById(section)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  // Scroll-spy for the section nav.
  useEffect(() => {
    if (!doc) return;
    const sections = Array.from(document.querySelectorAll<HTMLElement>('[data-section]'));
    const obs = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: '-15% 0px -70% 0px' },
    );
    sections.forEach((s) => obs.observe(s));
    return () => obs.disconnect();
  }, [doc?.id, !!doc]); // eslint-disable-line react-hooks/exhaustive-deps

  // Deep link (?s=section) and focus the first empty name on a new client.
  useEffect(() => {
    if (!doc) return;
    const target = initial ? LEGACY_STEP[initial] ?? initial : null;
    if (target && document.getElementById(target)) requestAnimationFrame(() => go(target));
    else if (!doc.client.firstName) {
      document.getElementById('client-first-name')?.focus();
    }
  }, [doc?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const done = useMemo(() => (doc && analysis ? Object.fromEntries(NAV.map((n) => [n.id, n.done ? n.done(doc, analysis) : null])) : {}), [doc, analysis]);

  if (loading)
    return (
      <AppShell wide>
        <div className="p-16 text-center text-sm text-muted">Loading client…</div>
      </AppShell>
    );
  if (!doc || !analysis)
    return (
      <AppShell>
        <div className="mx-auto max-w-lg p-10">
          <EmptyState
            title="Client not found"
            description="This client file is not stored in this browser. Import it from an export file."
            action={
              <Link href="/" className="text-sm font-medium text-brand underline">
                Back to clients
              </Link>
            }
          />
        </div>
      </AppShell>
    );

  const title = analysis.people.map((k) => nameOf(doc, k)).join(' & ');
  const props = { doc, update, analysis, practice };

  return (
    <AppShell
      wide
      actions={
        <div className="flex items-center gap-2">
          <SaveIndicator state={saveState} />
          <button type="button" tabIndex={-1} onClick={() => setExporting(true)} className="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-[13px] font-medium text-ink-2 hover:bg-wash">
            <Download size={15} /> <span className="hidden sm:inline">Export</span>
          </button>
          <Link href={`/report?id=${doc.id}`} target="_blank" tabIndex={-1} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-brand px-3 text-[13px] font-medium text-white hover:bg-brand-2">
            <FileText size={15} /> Report
          </Link>
        </div>
      }
    >
      <div className="mx-auto flex max-w-[1760px] gap-4 px-3 py-3 sm:px-4">
        <aside className="no-print sticky top-[3.75rem] hidden h-[calc(100vh-4.5rem)] w-44 shrink-0 flex-col overflow-y-auto lg:flex">
          <Link href="/" tabIndex={-1} className="mb-2 inline-flex items-center gap-1 text-[11.5px] font-medium text-muted hover:text-ink">
            <ArrowLeft size={12} /> All clients
          </Link>
          <div className="mb-2 truncate text-[13px] font-semibold leading-tight text-ink" title={title}>
            {title}
          </div>
          <nav aria-label="Sections" className="space-y-px">
            {NAV.map((n) => {
              const state = done[n.id];
              const isActive = active === n.id;
              return (
                <a
                  key={n.id}
                  href={`#${n.id}`}
                  tabIndex={-1}
                  onClick={(e) => {
                    e.preventDefault();
                    go(n.id);
                  }}
                  className={cx(
                    'flex items-center gap-2 rounded-md px-2 py-1 text-[12px] transition-colors',
                    isActive ? 'bg-brand text-white' : 'text-ink-2 hover:bg-surface hover:text-ink',
                  )}
                >
                  <span
                    className={cx(
                      'flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border',
                      isActive ? 'border-white/60' : state ? 'border-good bg-good text-white' : 'border-line-strong',
                    )}
                    aria-hidden
                  >
                    {state && !isActive ? <Check size={9} strokeWidth={3.5} /> : null}
                  </span>
                  <span className="truncate">{n.label}</span>
                </a>
              );
            })}
          </nav>
          <div className="mt-auto space-y-0.5 pt-4 text-[10.5px] leading-4 text-muted">
            <div className="flex items-center gap-1 font-semibold text-ink-2">
              <Keyboard size={12} /> Keyboard
            </div>
            <div>
              <kbd className="font-sans font-semibold">Tab</kbd> / <kbd className="font-sans font-semibold">Enter</kbd> next field
            </div>
            <div>
              <kbd className="font-sans font-semibold">Shift</kbd> + either: back
            </div>
            <div>Type to pick in lists (Y/N)</div>
          </div>
        </aside>

        <main
          className="min-w-0 flex-1 overflow-hidden rounded-xl border border-line-strong bg-white shadow-[0_1px_3px_rgba(16,24,40,0.06)]"
          onKeyDown={handleEnterNavigation}
        >
          <EngagementSection {...props} />
          <PeopleSection {...props} />
          <IncomeSection {...props} />
          <BudgetSection {...props} />
          <AssetsSection {...props} />
          <LiabilitiesSection {...props} />
          <CoverSection {...props} />
          <GoalsSection {...props} />
          <EstateSection {...props} />
          <RiskSection {...props} />
          <AssumptionsSection {...props} />
          <AnalysisSection {...props} />
          <AdviceSection {...props} />
          <ReportSection {...props} />
        </main>

        <aside className="no-print sticky top-[3.75rem] hidden h-[calc(100vh-4.5rem)] w-60 shrink-0 overflow-y-auto 2xl:block">
          <LiveSummary analysis={analysis} go={go} />
        </aside>
      </div>
      <ExportDialog doc={doc} open={exporting} onClose={() => setExporting(false)} />
    </AppShell>
  );
}
