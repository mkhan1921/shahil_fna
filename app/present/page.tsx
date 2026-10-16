'use client';

import { ArrowLeft, ChevronLeft, ChevronRight, ClipboardCheck, Eye, EyeOff, LayoutGrid, Maximize2, Minimize2, Moon, Sun, X } from 'lucide-react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { applies, QUESTIONS, TOPIC, type QuestionKey } from '@/components/present/answers';
import { AgendaScreen, PlanScreen, QuestionScreen, type ScreenCtx } from '@/components/present/screens';
import { PRESENT_CSS } from '@/components/present/theme';
import { StatusIcon } from '@/components/present/visuals';
import { cx } from '@/components/ui';
import { analyse } from '@/lib/fna/analysis';
import { defaultPractice } from '@/lib/fna/defaults';
import { applyPlan, emptyPlan, normalisePlan, planAfterAdvice, planToAdvice, summarisePlan, type AdviceUpdate } from '@/lib/fna/plan';
import { draftRecommendations } from '@/lib/fna/recommend';
import { sampleDocument } from '@/lib/fna/sample';
import type { FnaDocument, PersonKey, PlanLevers, PracticeProfile } from '@/lib/fna/types';
import { dateLong, money } from '@/lib/format';
import { getDocument, getPlan, getPractice, saveDocument, savePlan } from '@/lib/store/db';

type Screen = 'agenda' | QuestionKey | 'plan';

const THEME_KEY = 'fna-present-theme';
const isScreen = (s: string | null | undefined): s is Screen => s === 'agenda' || s === 'plan' || QUESTIONS.includes(s as QuestionKey);

function PresentView() {
  const params = useSearchParams();
  const id = params?.get('id') ?? null;
  const sampleParam = params?.get('sample');
  const sample = sampleParam !== null && sampleParam !== undefined && sampleParam !== '0';
  const start = params?.get('q');

  const [doc, setDoc] = useState<FnaDocument | null | undefined>(undefined);
  const [practice, setPractice] = useState<PracticeProfile>(defaultPractice());
  const [plan, setPlanState] = useState<PlanLevers>(emptyPlan());
  const [screen, setScreen] = useState<Screen>(isScreen(start) ? start : 'agenda');
  const [person, setPerson] = useState<PersonKey>('client');
  const [levers, setLevers] = useState(true);
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [full, setFull] = useState(false);
  const [confirm, setConfirm] = useState<(AdviceUpdate & { fresh: FnaDocument }) | null>(null);
  const [notice, setNotice] = useState<{ text: string; href?: string } | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const dialogOpen = useRef(false);
  dialogOpen.current = !!confirm;

  /* Load the client file (or the sample family) and any plan saved for it. */
  useEffect(() => {
    if (sample) {
      getPractice().then((p) => {
        const d = sampleDocument();
        if (!d.advice.recommendations.length) d.advice.recommendations = draftRecommendations(d, analyse(d));
        setDoc(d);
        setPractice(p);
      });
      return;
    }
    if (!id) {
      setDoc(null);
      return;
    }
    Promise.all([getDocument(id), getPractice(), getPlan(id)]).then(([d, p, stored]) => {
      setDoc(d);
      setPractice(p);
      setPlanState(normalisePlan(stored));
    });
  }, [id, sample]);

  /* Plans are saved beside the client file as they change (never for the sample family). */
  const dirty = useRef(false);
  const latest = useRef(plan);
  latest.current = plan;
  const setPlan = useCallback((update: (p: PlanLevers) => PlanLevers) => {
    dirty.current = true;
    setPlanState((p) => update(p));
  }, []);
  useEffect(() => {
    if (!id || sample || !dirty.current) return;
    const t = setTimeout(() => {
      savePlan(id, latest.current)
        .then(() => (dirty.current = false))
        .catch(() => setNotice({ text: 'The plan could not be saved on this device.' }));
    }, 500);
    return () => clearTimeout(t);
  }, [plan, id, sample]);
  useEffect(() => {
    const flush = () => {
      if (dirty.current && id && !sample) void savePlan(id, latest.current);
    };
    window.addEventListener('beforeunload', flush);
    return () => {
      window.removeEventListener('beforeunload', flush);
      flush();
    };
  }, [id, sample]);

  /* Theme: the viewer’s last choice, else the system setting. */
  useEffect(() => {
    try {
      const saved = localStorage.getItem(THEME_KEY);
      if (saved === 'light' || saved === 'dark') return setTheme(saved);
    } catch {
      /* storage unavailable */
    }
    if (window.matchMedia?.('(prefers-color-scheme: dark)').matches) setTheme('dark');
  }, []);
  const toggleTheme = useCallback(
    () =>
      setTheme((t) => {
        const next = t === 'dark' ? 'light' : 'dark';
        try {
          localStorage.setItem(THEME_KEY, next);
        } catch {
          /* storage unavailable */
        }
        return next;
      }),
    [],
  );

  const toggleFull = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void root.current?.requestFullscreen?.();
  }, []);
  useEffect(() => {
    const on = () => setFull(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', on);
    return () => document.removeEventListener('fullscreenchange', on);
  }, []);

  /* The analysis, today and with the plan. The answers follow the plan a frame behind the levers. */
  const today = useMemo(() => (doc ? analyse(doc) : null), [doc]);
  const deferred = useDeferredValue(plan);
  const planned = useMemo(() => (doc && today ? analyse(applyPlan(doc, deferred, today)) : null), [doc, today, deferred]);
  const summary = useMemo(() => (doc && today && planned ? summarisePlan(doc, deferred, today, planned) : null), [doc, today, planned, deferred]);

  /* The questions this family has something to answer for, in conversation order. */
  const order = useMemo<Screen[]>(() => (doc && today ? ['agenda', ...QUESTIONS.filter((q) => applies(q, { doc, today, planned: today })), 'plan'] : ['agenda', 'plan']), [doc, today]);
  const current: Screen = order.includes(screen) ? screen : 'agenda';
  const index = order.indexOf(current);
  const go = useCallback((s: string) => {
    if (!isScreen(s)) return;
    setScreen(s);
    window.scrollTo({ top: 0 });
    root.current?.scrollTo({ top: 0 });
  }, []);
  const step = useCallback(
    (dir: 1 | -1) => {
      const next = order[Math.min(order.length - 1, Math.max(0, index + dir))];
      if (next !== current) go(next);
    },
    [order, index, current, go],
  );

  /* Keys: ← → between screens, Home the questions, End the plan, F full screen, H hide the levers, T theme. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || el?.closest('input, textarea, select, [contenteditable="true"]')) return;
      if (dialogOpen.current) {
        if (e.key === 'Escape') setConfirm(null);
        return;
      }
      const k = e.key.toLowerCase();
      if (k === 'arrowright' || k === 'pagedown') step(1);
      else if (k === 'arrowleft' || k === 'pageup') step(-1);
      else if (k === 'home') go('agenda');
      else if (k === 'end') go('plan');
      else if (k === 'f') toggleFull();
      else if (k === 'h') setLevers((v) => !v);
      else if (k === 't') toggleTheme();
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [step, go, toggleFull, toggleTheme]);

  useEffect(() => {
    if (doc) document.title = `Planning — ${[doc.client.firstName, doc.client.surname].filter(Boolean).join(' ') || 'Client'}`;
  }, [doc]);

  if (doc === undefined) return <div className="p-16 text-center text-sm text-muted">Preparing the session…</div>;
  if (!doc || !today || !planned || !summary)
    return (
      <div className="p-16 text-center text-sm text-muted">
        Client not found on this device.{' '}
        <Link className="text-brand underline" href="/">
          Back to clients
        </Link>
      </div>
    );

  const c: ScreenCtx = { doc, today, planned, plan, setPlan, summary, person, setPerson, levers, go };
  const household = today.people.map((k) => today.persons[k]!.name).join(' & ');
  const back = sample ? '/' : `/client?id=${doc.id}`;
  const label = (s: Screen) => (s === 'agenda' ? 'Questions' : s === 'plan' ? 'The plan' : TOPIC[s]);
  const prev = index > 0 ? order[index - 1] : null;
  const next = index < order.length - 1 ? order[index + 1] : null;
  const cost = planned.cashflow.totalOutgoings - today.cashflow.totalOutgoings;

  const prepare = async () => {
    if (!id) return;
    const fresh = await getDocument(id);
    if (!fresh) return;
    setConfirm({ ...planToAdvice(fresh, plan, analyse(fresh), dateLong(new Date())), fresh });
  };
  const apply = async () => {
    if (!confirm || !id) return;
    const { fresh, recommendations, summary: text, retirement, goals } = confirm;
    const nextDoc: FnaDocument = {
      ...fresh,
      retirement,
      goals,
      advice: { ...fresh.advice, recommendations, summary: text },
      status: fresh.status === 'draft' ? 'in-progress' : fresh.status,
      updatedAt: new Date().toISOString(),
    };
    await saveDocument(nextDoc);
    dirty.current = true;
    setPlanState(planAfterAdvice(plan));
    setDoc(nextDoc);
    setConfirm(null);
    setNotice({ text: 'Added to the record of advice. The client’s decisions are marked “pending” until confirmed.', href: `/client?id=${id}&s=advice` });
  };

  const iconButton = (text: string, onClick: () => void, icon: ReactNode, pressed?: boolean) => (
    <button
      type="button"
      onClick={onClick}
      aria-label={text}
      aria-pressed={pressed}
      title={text}
      className="inline-flex h-10 w-10 items-center justify-center rounded-xl text-[var(--pv-ink-2)] transition-colors hover:bg-[var(--pv-plan-wash)] hover:text-[var(--pv-ink)]"
    >
      {icon}
    </button>
  );
  const navButton = (s: Screen, icon: ReactNode, badge?: number) => (
    <button
      type="button"
      onClick={() => go(s)}
      aria-current={current === s ? 'page' : undefined}
      className={cx(
        'inline-flex h-10 items-center gap-2 rounded-xl px-3 text-[14.5px] font-medium transition-colors',
        current === s ? 'bg-[var(--pv-plan)] text-white' : 'text-[var(--pv-ink-2)] hover:bg-[var(--pv-plan-wash)] hover:text-[var(--pv-ink)]',
      )}
    >
      {icon}
      <span className="hidden sm:inline">{label(s)}</span>
      {!!badge && <span className={cx('rounded-full px-1.5 text-[12px] font-semibold pv-tabular', current === s ? 'bg-white/25' : 'bg-[var(--pv-plan-wash)] text-[var(--pv-plan)]')}>{badge}</span>}
    </button>
  );

  return (
    <div ref={root} className={cx('pv min-h-screen font-sans', full && 'overflow-y-auto')} data-theme={theme}>
      <style>{PRESENT_CSS}</style>
      <header className="sticky top-0 z-20 border-b border-[var(--pv-border)] bg-[var(--pv-surface)]">
        <div className="flex h-16 items-center gap-3 px-4 sm:px-6">
          <Link
            href={back}
            className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-[var(--pv-ink-2)] hover:bg-[var(--pv-plan-wash)] hover:text-[var(--pv-ink)]"
            aria-label={sample ? 'Back to clients' : 'Back to the client file'}
            title={sample ? 'Back to clients' : 'Back to the client file'}
          >
            <ArrowLeft size={18} />
          </Link>
          <div className="min-w-0">
            <div className="truncate text-[11.5px] font-semibold uppercase tracking-[0.12em] text-[var(--pv-ink-2)]">{practice.practiceName || 'Planning session'}</div>
            <div className="truncate text-[16px] font-semibold">{household}</div>
          </div>
          <nav className="ml-auto flex shrink-0 items-center gap-1" aria-label="Session">
            {navButton('agenda', <LayoutGrid size={17} aria-hidden />)}
            {navButton('plan', <ClipboardCheck size={17} aria-hidden />, summary.changes.length)}
          </nav>
          <div className="flex shrink-0 items-center gap-0.5 border-l border-[var(--pv-border)] pl-2">
            {iconButton(levers ? 'Hide the levers (H)' : 'Show the levers (H)', () => setLevers((v) => !v), levers ? <EyeOff size={18} /> : <Eye size={18} />, !levers)}
            {iconButton(theme === 'dark' ? 'Light theme (T)' : 'Dark theme (T)', toggleTheme, theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />)}
            {iconButton(full ? 'Leave full screen (F)' : 'Full screen (F)', toggleFull, full ? <Minimize2 size={18} /> : <Maximize2 size={18} />)}
          </div>
        </div>
        <div className="h-[3px] bg-[var(--pv-grid)]" aria-hidden>
          <div className="pv-anim h-full" style={{ width: `${(index / Math.max(1, order.length - 1)) * 100}%`, background: practice.brandColor || 'var(--pv-plan)' }} />
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1180px] px-5 pb-32 pt-[clamp(20px,5vh,56px)] sm:px-8">
        {current === 'agenda' ? (
          <AgendaScreen c={c} list={order.filter((s): s is QuestionKey => s !== 'agenda' && s !== 'plan')} />
        ) : current === 'plan' ? (
          <PlanScreen
            c={c}
            action={
              summary.changes.length > 0 && (
                <button
                  type="button"
                  onClick={prepare}
                  disabled={sample}
                  title={sample ? 'This is the sample family — nothing is saved.' : 'Write the agreed plan into the record of advice'}
                  className="inline-flex items-center gap-2 rounded-xl bg-[var(--pv-plan)] px-5 py-3 text-[15px] font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <ClipboardCheck size={18} /> Add to the record of advice
                </button>
              )
            }
          />
        ) : (
          <QuestionScreen key={current} q={current} c={c} />
        )}
      </main>

      <footer className="fixed inset-x-0 bottom-0 z-20 border-t border-[var(--pv-border)] bg-[var(--pv-surface)]">
        <div className="mx-auto flex h-16 max-w-[1180px] items-center gap-3 px-3 sm:px-6">
          <button
            type="button"
            onClick={() => step(-1)}
            disabled={!prev}
            className="inline-flex min-w-0 items-center gap-1 rounded-xl px-3 py-2 text-[14.5px] font-medium text-[var(--pv-ink-2)] hover:bg-[var(--pv-plan-wash)] hover:text-[var(--pv-ink)] disabled:invisible"
          >
            <ChevronLeft size={18} className="shrink-0" /> <span className="hidden truncate md:inline">{prev && label(prev)}</span>
          </button>
          {current !== 'plan' ? (
            <button
              type="button"
              onClick={() => go('plan')}
              title="See the plan"
              className="mx-auto inline-flex min-w-0 items-center gap-2 rounded-xl px-3 py-2 text-[14.5px] text-[var(--pv-ink-2)] hover:bg-[var(--pv-plan-wash)]"
            >
              {summary.changes.length === 0 ? (
                <span className="truncate">
                  No changes yet · <b className="font-semibold text-[var(--pv-ink)]">{money(summary.surplusToday)}</b> a month free today
                </span>
              ) : (
                <>
                  <StatusIcon status={summary.surplusPlan >= -0.5 ? 'covered' : 'shortfall'} />
                  <span className="truncate">
                    The plan
                    {cost > 0.5 && (
                      <>
                        {' '}
                        costs <b className="font-semibold text-[var(--pv-ink)]">{money(cost)}</b> a month
                      </>
                    )}{' '}
                    · leaves <b className="font-semibold text-[var(--pv-ink)]">{money(summary.surplusPlan)}</b> free
                  </span>
                </>
              )}
            </button>
          ) : (
            <span className="mx-auto" />
          )}
          <button
            type="button"
            onClick={() => step(1)}
            disabled={!next}
            className="inline-flex min-w-0 items-center gap-1 rounded-xl px-3 py-2 text-[14.5px] font-medium text-[var(--pv-ink-2)] hover:bg-[var(--pv-plan-wash)] hover:text-[var(--pv-ink)] disabled:invisible"
          >
            <span className="hidden truncate md:inline">{next && label(next)}</span> <ChevronRight size={18} className="shrink-0" />
          </button>
        </div>
      </footer>

      {notice && (
        <div
          role="status"
          className="fixed bottom-20 left-1/2 z-40 flex w-[min(36rem,calc(100%-2rem))] -translate-x-1/2 items-start gap-3 rounded-xl border border-[var(--pv-border)] bg-[var(--pv-raised)] px-4 py-3 text-[14px] shadow-[0_8px_30px_rgba(0,0,0,0.2)]"
        >
          <span className="flex-1">
            {notice.text}
            {notice.href && (
              <>
                {' '}
                <Link href={notice.href} className="font-semibold text-[var(--pv-plan)] underline underline-offset-2">
                  Open the record
                </Link>
              </>
            )}
          </span>
          <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss" className="text-[var(--pv-muted)] hover:text-[var(--pv-ink)]">
            <X size={16} />
          </button>
        </div>
      )}

      {confirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-labelledby="pv-confirm-title">
          <div className="absolute inset-0 bg-black/50" onClick={() => setConfirm(null)} />
          <div className="pv-card relative w-full max-w-lg p-6 shadow-2xl">
            <h2 id="pv-confirm-title" className="text-[19px] font-semibold">
              Add the agreed plan to the record of advice?
            </h2>
            <p className="mt-1 text-[14px] text-[var(--pv-ink-2)]">These changes will be made to {household}’s file:</p>
            <ul className="mt-4 max-h-[50vh] space-y-2 overflow-y-auto text-[14.5px]">
              {confirm.lines.map((l) => (
                <li key={l} className="flex gap-2">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--pv-plan)]" aria-hidden />
                  {l}
                </li>
              ))}
              {confirm.lines.length === 0 && <li className="text-[var(--pv-ink-2)]">Nothing to add — the record already reflects this plan.</li>}
            </ul>
            <p className="mt-4 text-[13px] text-[var(--pv-ink-2)]">
              Recommendations are added as “pending” for the client to accept. Review the rationale and quotes in the client file before the report is signed.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={() => setConfirm(null)} className="rounded-xl px-4 py-2 text-[14.5px] font-medium text-[var(--pv-ink-2)] hover:text-[var(--pv-ink)]">
                Cancel
              </button>
              <button
                type="button"
                autoFocus
                onClick={apply}
                disabled={confirm.lines.length === 0}
                className="rounded-xl bg-[var(--pv-plan)] px-4 py-2 text-[14.5px] font-semibold text-white hover:opacity-90 disabled:opacity-40"
              >
                Add to the record
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function PresentPage() {
  return (
    <Suspense fallback={<div className="p-16 text-center text-sm text-muted">Preparing the session…</div>}>
      <PresentView />
    </Suspense>
  );
}
