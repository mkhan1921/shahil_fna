'use client';

import { ArrowLeft, ClipboardCheck, Eye, EyeOff, Maximize2, Minimize2, Moon, Sun, X } from 'lucide-react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { applies, isPerPerson, peopleFor, QUESTIONS, verdicts, type QuestionKey } from '@/components/present/answers';
import { StatusIcon } from '@/components/present/charts';
import { ContentsSection, NAV_LABEL, PlanSection, QuestionSection, sectionId, type SectionCtx } from '@/components/present/sections';
import { PRESENT_CSS } from '@/components/present/theme';
import { garamond } from '@/components/report/vintage/font';
import { cx } from '@/components/ui';
import type { Status } from '@/lib/fna/analysis';
import { analyse } from '@/lib/fna/analysis';
import { defaultPractice } from '@/lib/fna/defaults';
import { applyPlan, emptyPlan, normalisePlan, planAfterAdvice, planToAdvice, summarisePlan, type AdviceUpdate } from '@/lib/fna/plan';
import { draftRecommendations } from '@/lib/fna/recommend';
import { sampleDocument } from '@/lib/fna/sample';
import type { FnaDocument, PlanLevers, PracticeProfile } from '@/lib/fna/types';
import { dateLong, money } from '@/lib/format';
import { getDocument, getPlan, getPractice, saveDocument, savePlan } from '@/lib/store/db';

const THEME_KEY = 'fna-present-theme';
const RANK: Record<Status, number> = { na: 0, covered: 1, partial: 2, shortfall: 3 };
const DOT: Record<Status, string> = { na: 'var(--pv-faint)', covered: 'var(--pv-good)', partial: 'var(--pv-warning)', shortfall: 'var(--pv-critical)' };

function PresentView() {
  const params = useSearchParams();
  const id = params?.get('id') ?? null;
  const sampleParam = params?.get('sample');
  const sample = sampleParam !== null && sampleParam !== undefined && sampleParam !== '0';
  const start = params?.get('q');

  const [doc, setDoc] = useState<FnaDocument | null | undefined>(undefined);
  const [practice, setPractice] = useState<PracticeProfile>(defaultPractice());
  const [plan, setPlanState] = useState<PlanLevers>(emptyPlan());
  const [levers, setLevers] = useState(true);
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [full, setFull] = useState(false);
  const [active, setActive] = useState('contents');
  const [confirm, setConfirm] = useState<(AdviceUpdate & { fresh: FnaDocument }) | null>(null);
  const [notice, setNotice] = useState<{ text: string; href?: string } | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const head = useRef<HTMLElement>(null);
  const nav = useRef<HTMLElement>(null);
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
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    else void root.current?.requestFullscreen?.().catch(() => {});
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
  const order = useMemo<QuestionKey[]>(() => (doc && today ? QUESTIONS.filter((q) => applies(q, { doc, today, planned: today })) : []), [doc, today]);
  const ids = useMemo(() => ['contents', ...order.map(sectionId), 'plan'], [order]);

  const go = useCallback((target: string) => {
    const el = document.getElementById(target);
    if (!el) return;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
    setActive(target);
  }, []);

  /* Open on the question asked for in the link, if any. */
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current || !doc || !start) return;
    opened.current = true;
    const target = start === 'plan' ? 'plan' : QUESTIONS.includes(start as QuestionKey) ? sectionId(start as QuestionKey) : null;
    if (target) requestAnimationFrame(() => document.getElementById(target)?.scrollIntoView({ block: 'start' }));
  }, [doc, start]);

  /* The contents bar follows the reader: the last section whose heading has passed under the bar. */
  useEffect(() => {
    if (!doc) return;
    const scroller: HTMLElement | Window = full && root.current ? root.current : window;
    let raf = 0;
    const update = () => {
      const line = (head.current?.getBoundingClientRect().bottom ?? 0) + 90;
      let current = ids[0];
      for (const s of ids) {
        const el = document.getElementById(s);
        if (el && el.getBoundingClientRect().top <= line) current = s;
      }
      const el = scroller === window ? document.documentElement : (scroller as HTMLElement);
      const top = scroller === window ? window.scrollY : el.scrollTop;
      if (top + el.clientHeight >= el.scrollHeight - 4) current = ids[ids.length - 1];
      setActive(current);
    };
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(update);
    };
    scroller.addEventListener('scroll', onScroll, { passive: true });
    update();
    return () => {
      scroller.removeEventListener('scroll', onScroll);
      cancelAnimationFrame(raf);
    };
  }, [doc, ids, full]);
  useEffect(() => {
    const bar = nav.current;
    const link = bar?.querySelector<HTMLElement>(`[data-target="${active}"]`);
    if (bar && link) bar.scrollTo({ left: link.offsetLeft - bar.clientWidth / 2 + link.clientWidth / 2, behavior: 'smooth' });
  }, [active]);

  /* Keys: ← → between sections, F full screen, H hide the levers, T theme. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || el?.closest('input, textarea, select, [contenteditable="true"]')) return;
      if (dialogOpen.current) {
        if (e.key === 'Escape') setConfirm(null);
        return;
      }
      const k = e.key.toLowerCase();
      const i = ids.indexOf(active);
      if (k === 'arrowright' || k === 'pagedown') go(ids[Math.min(ids.length - 1, i + 1)]);
      else if (k === 'arrowleft' || k === 'pageup') go(ids[Math.max(0, i - 1)]);
      else if (k === 'f') toggleFull();
      else if (k === 'h') setLevers((v) => !v);
      else if (k === 't') toggleTheme();
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [ids, active, go, toggleFull, toggleTheme]);

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

  const c: SectionCtx = { doc, today, planned, plan, setPlan, summary, levers, go };
  const household = today.people.map((k) => today.persons[k]!.name).join(' & ');
  const back = sample ? '/' : `/client?id=${doc.id}`;
  const cost = planned.cashflow.totalOutgoings - today.cashflow.totalOutgoings;
  const prepared = `Prepared ${practice.adviserName ? `by ${practice.adviserName} ` : ''}on ${dateLong(new Date())}${practice.practiceName ? ` · ${practice.practiceName}` : ''}`;
  const worst = (q: QuestionKey): Status => {
    const ks = isPerPerson(q) ? peopleFor(q, c) : (['client'] as const);
    return ks.map((k) => verdicts(q, c, k).plan.status).reduce<Status>((w, s) => (RANK[s] > RANK[w] ? s : w), 'na');
  };

  const prepare = async () => {
    if (!id) return;
    const fresh = await getDocument(id);
    if (!fresh) return;
    setConfirm({ ...planToAdvice(fresh, plan, analyse(fresh), dateLong(new Date())), fresh });
  };
  const apply = async () => {
    if (!confirm || !id) return;
    const { fresh, recommendations, summary: text, retirement, goals } = confirm;
    const next: FnaDocument = {
      ...fresh,
      retirement,
      goals,
      advice: { ...fresh.advice, recommendations, summary: text },
      status: fresh.status === 'draft' ? 'in-progress' : fresh.status,
      updatedAt: new Date().toISOString(),
    };
    await saveDocument(next);
    dirty.current = true;
    setPlanState(planAfterAdvice(plan));
    setDoc(next);
    setConfirm(null);
    setNotice({ text: 'Added to the record of advice. The client’s decisions are marked “pending” until confirmed.', href: `/client?id=${id}&s=advice` });
  };

  const tool = (text: string, onClick: () => void, icon: ReactNode, pressed?: boolean) => (
    <button type="button" onClick={onClick} aria-label={text} aria-pressed={pressed} title={text} className="pv-icon-btn">
      {icon}
    </button>
  );
  const navLink = (target: string, label: string, extra?: ReactNode) => (
    <a
      key={target}
      href={`#${target}`}
      data-target={target}
      aria-current={active === target ? 'true' : undefined}
      onClick={(e) => {
        e.preventDefault();
        go(target);
      }}
    >
      {extra}
      {label}
    </a>
  );

  return (
    <div ref={root} className={cx('pv min-h-screen', garamond.variable, full && 'overflow-y-auto')} data-theme={theme}>
      <style>{PRESENT_CSS}</style>
      <header ref={head} className="pv-head">
        <div className="pv-head-row">
          <Link href={back} className="pv-icon-btn shrink-0" aria-label={sample ? 'Back to clients' : 'Back to the client file'} title={sample ? 'Back to clients' : 'Back to the client file'}>
            <ArrowLeft size={18} />
          </Link>
          <div className="pv-head-title">
            <div>{practice.practiceName || 'The planning session'}</div>
            <div>{household}</div>
          </div>
          <div className="flex shrink-0 items-center">
            {tool(levers ? 'Hide the levers (H)' : 'Show the levers (H)', () => setLevers((v) => !v), levers ? <EyeOff size={18} /> : <Eye size={18} />, !levers)}
            {tool(theme === 'dark' ? 'Paper (T)' : 'Night (T)', toggleTheme, theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />)}
            <span className="hidden sm:inline-flex">{tool(full ? 'Leave full screen (F)' : 'Full screen (F)', toggleFull, full ? <Minimize2 size={18} /> : <Maximize2 size={18} />)}</span>
          </div>
        </div>
        <nav ref={nav} className="pv-nav" aria-label="Sections">
          {navLink('contents', 'Contents')}
          {order.map((q) => navLink(sectionId(q), NAV_LABEL[q], <span className="pv-dot" style={{ background: DOT[worst(q)] }} aria-hidden />))}
          {navLink('plan', summary.changes.length ? `The plan · ${summary.changes.length}` : 'The plan')}
        </nav>
      </header>

      <main className="pv-main">
        <ContentsSection c={c} list={order} title={household} prepared={prepared} />
        {order.map((q, i) => (
          <QuestionSection key={q} q={q} n={i + 1} c={c} />
        ))}
        <PlanSection
          c={c}
          n={order.length + 1}
          action={
            summary.changes.length > 0 && (
              <button
                type="button"
                onClick={prepare}
                disabled={sample}
                title={sample ? 'This is the sample family — nothing is saved.' : 'Write the agreed plan into the record of advice'}
                className="pv-button"
              >
                <ClipboardCheck size={18} /> Add to the record of advice
              </button>
            )
          }
        />
      </main>

      <footer className="pv-foot">
        <div className="pv-foot-row">
          {summary.changes.length === 0 ? (
            <span className="truncate italic" style={{ color: 'var(--pv-ink-2)' }}>
              No changes yet ·{' '}
              <b className="not-italic" style={{ color: 'var(--pv-ink)' }}>
                {money(summary.surplusToday)}
              </b>{' '}
              a month free today
            </span>
          ) : (
            <>
              <StatusIcon status={summary.surplusPlan >= -0.5 ? 'covered' : 'shortfall'} />
              <span className="truncate" style={{ color: 'var(--pv-ink-2)' }}>
                <span className="pv-sc">The plan</span>
                {cost > 0.5 && (
                  <>
                    {' '}
                    costs <b style={{ color: 'var(--pv-ink)' }}>{money(cost)}</b> a month
                  </>
                )}{' '}
                · leaves <b style={{ color: 'var(--pv-ink)' }}>{money(summary.surplusPlan)}</b> free
              </span>
              {active !== 'plan' && (
                <button type="button" onClick={() => go('plan')} className="pv-link-btn shrink-0 !py-1">
                  See it
                </button>
              )}
            </>
          )}
        </div>
      </footer>

      {notice && (
        <div
          role="status"
          className="pv-card fixed bottom-20 left-1/2 z-40 flex w-[min(36rem,calc(100%-2rem))] -translate-x-1/2 items-start gap-3 px-4 py-3 text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.25)]"
        >
          <span className="flex-1">
            {notice.text}
            {notice.href && (
              <>
                {' '}
                <Link href={notice.href} className="font-semibold underline underline-offset-2" style={{ color: 'var(--pv-plan)' }}>
                  Open the record
                </Link>
              </>
            )}
          </span>
          <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss" className="pv-icon-btn !h-7 !w-7">
            <X size={16} />
          </button>
        </div>
      )}

      {confirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-labelledby="pv-confirm-title">
          <div className="absolute inset-0 bg-black/50" onClick={() => setConfirm(null)} />
          <div className="pv-card relative w-full max-w-lg p-6 shadow-2xl">
            <h2 id="pv-confirm-title" className="pv-sc m-0 text-[21px] font-normal">
              Add the agreed plan to the record of advice?
            </h2>
            <p className="mt-1 text-[16px] italic" style={{ color: 'var(--pv-ink-2)' }}>
              These changes will be made to {household}’s file:
            </p>
            <ul className="mt-4 max-h-[50vh] list-none space-y-2 overflow-y-auto p-0 text-[16px]">
              {confirm.lines.map((l) => (
                <li key={l} className="flex gap-2">
                  <span className="mt-2.5 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: 'var(--pv-plan)' }} aria-hidden />
                  {l}
                </li>
              ))}
              {confirm.lines.length === 0 && <li style={{ color: 'var(--pv-ink-2)' }}>Nothing to add — the record already reflects this plan.</li>}
            </ul>
            <p className="mt-4 text-[14.5px] italic" style={{ color: 'var(--pv-ink-2)' }}>
              Recommendations are added as “pending” for the client to accept. Review the rationale and quotes in the client file before the report is signed.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={() => setConfirm(null)} className="pv-link-btn">
                Cancel
              </button>
              <button type="button" autoFocus onClick={apply} disabled={confirm.lines.length === 0} className="pv-button">
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
