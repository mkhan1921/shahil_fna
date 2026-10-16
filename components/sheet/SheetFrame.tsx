'use client';

import { ArrowLeft, Check, CloudOff, Keyboard, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState, type FocusEvent, type KeyboardEvent, type ReactNode } from 'react';
import type { Analysis } from '@/lib/fna/analysis';
import { scoreRiskProfile } from '@/lib/fna/riskProfile';
import type { FnaDocument, PracticeProfile } from '@/lib/fna/types';
import AppShell from '../AppShell';
import { cx } from '../ui';
import { handleEnterNavigation } from './focus';
import { nameOf } from './shared';
import { BudgetSection, EngagementSection, IncomeSection, PeopleSection } from './sections/Discovery';
import { AssumptionsSection, EstateSection, GoalsSection, RiskSection } from './sections/Planning';
import { AdviceSection, AnalysisSection, ReportSection } from './sections/Results';
import { AssetsSection, CoverSection, LiabilitiesSection } from './sections/Wealth';
import type { Update } from './types';

export const NAV: { id: string; label: string; done?: (doc: FnaDocument, a: Analysis) => boolean | null }[] = [
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

export function SaveIndicator({ state }: { state: string }) {
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

export const goToSection = (section: string) => document.getElementById(section)?.scrollIntoView({ behavior: 'smooth', block: 'start' });

/**
 * The data-entry sheet's frame: the section nav, every section, and a rail on the right. The plain
 * client sheet puts the live summary in the rail; a call review puts its queue and impact strip
 * there, and lays its top bar above the sections.
 */
export default function SheetFrame({
  doc,
  analysis,
  update,
  practice,
  rail,
  actions,
  banner,
  back = { href: '/', label: 'All clients' },
  railClassName = 'hidden w-60 2xl:block',
  navClassName = 'hidden lg:flex',
  keyboardHelp,
  onMainKeyDown,
  onMainBlur,
}: {
  doc: FnaDocument;
  analysis: Analysis;
  update: Update;
  practice: PracticeProfile | null;
  rail: (go: (section: string) => void) => ReactNode;
  actions?: ReactNode;
  /** Shown above the sections (a review's top bar). */
  banner?: ReactNode;
  back?: { href: string; label: string };
  railClassName?: string;
  navClassName?: string;
  keyboardHelp?: ReactNode;
  onMainKeyDown?: (e: KeyboardEvent<HTMLElement>) => void;
  onMainBlur?: (e: FocusEvent<HTMLElement>) => void;
}) {
  const [active, setActive] = useState('engagement');
  const go = useCallback((section: string) => goToSection(section), []);

  // Scroll-spy for the section nav.
  useEffect(() => {
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
  }, [doc.id]);

  const done = useMemo(() => Object.fromEntries(NAV.map((n) => [n.id, n.done ? n.done(doc, analysis) : null])), [doc, analysis]);
  const props = { doc, update, analysis, practice };
  const title = analysis.people.map((k) => nameOf(doc, k)).join(' & ');

  return (
    <AppShell wide actions={actions}>
      {banner}
      <div className="mx-auto flex max-w-[1760px] gap-4 px-3 py-3 sm:px-4">
        <aside className={cx('no-print sticky top-[3.75rem] h-[calc(100vh-4.5rem)] w-44 shrink-0 flex-col overflow-y-auto', navClassName)}>
          <Link href={back.href} tabIndex={-1} className="mb-2 inline-flex items-center gap-1 text-[11.5px] font-medium text-muted hover:text-ink">
            <ArrowLeft size={12} /> {back.label}
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
                  className={cx('flex items-center gap-2 rounded-md px-2 py-1 text-[12px] transition-colors', isActive ? 'bg-brand text-white' : 'text-ink-2 hover:bg-surface hover:text-ink')}
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
            {keyboardHelp}
            <div className="flex items-center gap-1 pt-1">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-warning" aria-hidden /> still needed
            </div>
            <div className="flex items-center gap-1">
              <span className="inline-block h-2.5 w-3 rounded-sm border border-line bg-[#eff3f7]" aria-hidden /> calculated
            </div>
          </div>
        </aside>

        <main
          className="min-w-0 flex-1 space-y-3"
          onKeyDown={(e) => {
            onMainKeyDown?.(e);
            if (!e.defaultPrevented) handleEnterNavigation(e);
          }}
          onBlur={onMainBlur}
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

        <aside className={cx('no-print sticky top-[3.75rem] h-[calc(100vh-4.5rem)] shrink-0 overflow-y-auto', railClassName)}>{rail(go)}</aside>
      </div>
    </AppShell>
  );
}
