'use client';

import { AlertTriangle, CheckCircle2, ChevronDown, Minus, OctagonAlert } from 'lucide-react';
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import type { Line, RetirementResult, Status } from '@/lib/fna/analysis';
import { money, moneyCompact } from '@/lib/format';
import { cx } from '../ui';
import { measureStatus, shortOf, type Measure } from './answers';

/*
 * The few visuals presentation mode uses. Every need is drawn the same way, so
 * the family learns to read it once: grey is what is in place today, blue is
 * what the plan adds, and the red-hatched part is what is still short.
 */

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/* ------------------------------------------------------------------ */
/* Status: always an icon and a word, never colour alone               */
/* ------------------------------------------------------------------ */

export const STATUS: Record<Status, { icon: typeof CheckCircle2; color: string }> = {
  covered: { icon: CheckCircle2, color: 'var(--pv-good)' },
  partial: { icon: AlertTriangle, color: 'var(--pv-warning)' },
  shortfall: { icon: OctagonAlert, color: 'var(--pv-critical)' },
  na: { icon: Minus, color: 'var(--pv-muted)' },
};

export function StatusIcon({ status, size = 16, className }: { status: Status; size?: number; className?: string }) {
  const S = STATUS[status];
  return <S.icon size={size} aria-hidden style={{ color: S.color }} className={cx('shrink-0', className)} />;
}

export function StatusMark({ status, text, className }: { status: Status; text: ReactNode; className?: string }) {
  return (
    <span className={cx('inline-flex items-start gap-1.5', className)}>
      <StatusIcon status={status} className="mt-[0.2em]" />
      <span className="text-[var(--pv-ink)]">{text}</span>
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Keys and figures                                                    */
/* ------------------------------------------------------------------ */

type SwatchKind = 'today' | 'plan' | 'short' | 'lost' | 'free';

export function Swatch({ kind }: { kind: SwatchKind }) {
  const cls: Record<SwatchKind, string> = {
    today: 'bg-[var(--pv-today)]',
    plan: 'bg-[var(--pv-plan)]',
    short: 'pv-short',
    lost: 'pv-lost',
    free: 'pv-free',
  };
  return <span className={cx('inline-block h-3.5 w-3.5 shrink-0 rounded-[4px]', cls[kind])} aria-hidden />;
}

export interface Figure {
  key: string;
  label: string;
  value: string;
  unit?: string;
  swatch?: SwatchKind;
  status?: Status;
}

/** The numbers a bar shows, each keyed to its part of the bar. */
export function Figures({ items, total }: { items: Figure[]; total?: Figure }) {
  const one = (f: Figure) => (
    <div key={f.key} className="min-w-0">
      <div className="flex items-center gap-2 text-[14px] text-[var(--pv-ink-2)]">
        {f.status ? <StatusIcon status={f.status} size={15} /> : f.swatch ? <Swatch kind={f.swatch} /> : null}
        {f.label}
      </div>
      <div className="mt-0.5 whitespace-nowrap text-[clamp(22px,2.3vw,34px)] font-semibold leading-tight tracking-tight pv-tabular">
        {f.value}
        {f.unit && <span className="ml-1 text-[15px] font-normal tracking-normal text-[var(--pv-ink-2)]">{f.unit}</span>}
      </div>
    </div>
  );
  return (
    <div className="mb-4 flex flex-wrap items-end gap-x-[clamp(24px,4vw,56px)] gap-y-3">
      {items.map(one)}
      {total && <div className="ml-auto text-right">{one(total)}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Need bar                                                            */
/* ------------------------------------------------------------------ */

const fmtFor = (m: Pick<Measure, 'monthly'>) => (n: number) => (m.monthly ? money(n) : moneyCompact(n));

/** One need as a bar: the whole bar is the need; what is not covered shows through, hatched red. */
export function NeedBar({ m, scale, size = 'lg' }: { m: Measure; scale?: number; size?: 'lg' | 'sm' }) {
  const fmt = fmtFor(m);
  const base = Math.max(0, Math.min(m.today, m.plan));
  const added = Math.max(0, m.plan - m.today);
  const lost = Math.max(0, m.today - m.plan);
  const s = Math.max(scale ?? 0, m.need, m.plan, m.today, 1);
  const w = (x: number) => `${(Math.max(0, x) / s) * 100}%`;
  const label = `${m.label}: ${fmt(m.need)} needed, ${fmt(m.today)} in place today, ${fmt(m.plan)} with the plan, ${fmt(shortOf(m))} short.`;
  return (
    <div className="relative">
      <div className={cx('relative overflow-hidden', size === 'lg' ? 'h-[clamp(44px,7vh,64px)] rounded-[14px]' : 'h-9 rounded-[10px]')} role="img" aria-label={label}>
        <div className="pv-anim pv-short absolute inset-y-0 left-0 rounded-[inherit]" style={{ width: w(m.need) }} />
        {base > 0.5 && <div className="pv-anim pv-seg absolute inset-y-0 left-0 bg-[var(--pv-today)]" style={{ width: w(base) }} title={`In place: ${fmt(base)}`} />}
        {lost > 0.5 && <div className="pv-anim pv-lost absolute inset-y-0" style={{ left: w(base), width: w(lost) }} title={`Less with the plan: ${fmt(lost)}`} />}
        {added > 0.5 && <div className="pv-anim pv-seg pv-seg-after absolute inset-y-0 bg-[var(--pv-plan)]" style={{ left: w(m.today), width: w(added) }} title={`The plan adds ${fmt(added)}`} />}
      </div>
      {m.need > 0.5 && Math.max(m.plan, m.today) > m.need * 1.02 && (
        <span className="pv-anim absolute -inset-y-1.5 w-[3px] rounded-full bg-[var(--pv-ink)]" style={{ left: `calc(${w(m.need)} - 1.5px)` }} aria-hidden />
      )}
    </div>
  );
}

/** A single need, large, with its figures. */
export function NeedPanel({ m }: { m: Measure }) {
  const fmt = fmtFor(m);
  const unit = m.monthly ? 'a month' : undefined;
  const base = Math.max(0, Math.min(m.today, m.plan));
  const added = Math.max(0, m.plan - m.today);
  const lost = Math.max(0, m.today - m.plan);
  const short = shortOf(m);
  const items: Figure[] = [{ key: 'have', label: lost > 0.5 ? 'In place with the plan' : 'In place today', value: fmt(base), unit, swatch: 'today' }];
  if (added > 0.5) items.push({ key: 'add', label: 'The plan adds', value: `+${fmt(added)}`, unit, swatch: 'plan' });
  if (lost > 0.5) items.push({ key: 'lost', label: 'Less with the plan', value: `−${fmt(lost)}`, unit, swatch: 'lost' });
  const over = m.plan - m.need;
  items.push(
    short > 0.5
      ? { key: 'short', label: 'Still short', value: fmt(short), unit, swatch: 'short' }
      : over > m.need * 0.02
        ? { key: 'over', label: 'More than needed', value: fmt(over), unit, status: 'covered' }
        : { key: 'short', label: 'Still short', value: 'Nothing', status: 'covered' },
  );
  return (
    <div>
      <Figures items={items} total={{ key: 'need', label: 'Needed', value: fmt(m.need), unit }} />
      <NeedBar m={m} />
    </div>
  );
}

/** Several needs of one kind — each child's studies, each goal — on one scale. */
export function NeedRows({ rows, met }: { rows: Measure[]; met: string }) {
  const scale = Math.max(1, ...rows.map((r) => Math.max(r.need, r.plan, r.today)));
  const anyAdded = rows.some((r) => r.plan > r.today + 0.5);
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-x-6 gap-y-2 text-[14px] text-[var(--pv-ink-2)]">
        <span className="inline-flex items-center gap-2">
          <Swatch kind="today" /> Saved, or being saved, today
        </span>
        {anyAdded && (
          <span className="inline-flex items-center gap-2">
            <Swatch kind="plan" /> The plan adds
          </span>
        )}
        <span className="inline-flex items-center gap-2">
          <Swatch kind="short" /> Still short
        </span>
      </div>
      <div className="divide-y divide-[var(--pv-grid)]">
        {rows.map((r) => {
          const status = measureStatus(r);
          return (
            <div key={r.key} className="grid grid-cols-1 items-center gap-x-6 gap-y-2 py-4 sm:grid-cols-[minmax(150px,220px)_minmax(0,1fr)_minmax(130px,auto)]">
              <div className="min-w-0">
                <div className="text-balance text-[17px] font-semibold leading-snug">{r.label}</div>
                <div className="text-[13.5px] text-[var(--pv-ink-2)]">
                  {moneyCompact(r.need)} needed {r.when}
                </div>
              </div>
              <NeedBar m={r} scale={scale} size="sm" />
              <div className="text-[15px] sm:text-right">
                <StatusMark status={status} text={status === 'covered' ? met : `${moneyCompact(shortOf(r))} short`} className="sm:justify-end" />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Take-home pay: what is spoken for, what the plan adds, what is free */
/* ------------------------------------------------------------------ */

export function CapacityBar({ takeHome, today, planned }: { takeHome: number; today: number; planned: number }) {
  const base = Math.max(0, Math.min(today, planned));
  const added = Math.max(0, planned - today);
  const freed = Math.max(0, today - planned);
  const free = Math.max(0, takeHome - planned);
  const over = Math.max(0, planned - takeHome);
  const s = Math.max(takeHome, planned, today, 1);
  const w = (x: number) => `${(Math.max(0, x) / s) * 100}%`;
  const items: Figure[] = [{ key: 'today', label: 'Spoken for today', value: money(today), swatch: 'today' }];
  if (added > 0.5) items.push({ key: 'plan', label: 'The plan adds', value: `+${money(added)}`, swatch: 'plan' });
  if (freed > 0.5) items.push({ key: 'freed', label: 'The plan frees', value: `−${money(freed)}`, swatch: 'lost' });
  items.push(over > 0.5 ? { key: 'over', label: 'More than comes in', value: money(over), swatch: 'short' } : { key: 'free', label: 'Still free', value: money(free), swatch: 'free' });
  return (
    <div>
      <Figures items={items} total={{ key: 'in', label: 'Take-home pay', value: money(takeHome) }} />
      <div className="relative">
        <div
          className="relative h-[clamp(44px,7vh,64px)] overflow-hidden rounded-[14px]"
          role="img"
          aria-label={`Take-home pay ${money(takeHome)} a month. Spoken for today ${money(today)}; with the plan ${money(planned)}.`}
        >
          <div className="pv-anim pv-free absolute inset-y-0 left-0" style={{ width: w(takeHome) }} />
          {base > 0.5 && <div className="pv-anim pv-seg absolute inset-y-0 left-0 bg-[var(--pv-today)]" style={{ width: w(base) }} />}
          {freed > 0.5 && <div className="pv-anim pv-lost absolute inset-y-0" style={{ left: w(base), width: w(freed) }} />}
          {added > 0.5 && <div className="pv-anim pv-seg pv-seg-after absolute inset-y-0 bg-[var(--pv-plan)]" style={{ left: w(today), width: w(added) }} />}
          {over > 0.5 && <div className="pv-anim pv-over absolute inset-y-0 rounded-r-[14px]" style={{ left: w(takeHome), width: w(over) }} />}
        </div>
        {s > takeHome + 0.5 && <span className="pv-anim absolute -inset-y-1.5 w-[3px] rounded-full bg-[var(--pv-ink)]" style={{ left: `calc(${w(takeHome)} - 1.5px)` }} aria-hidden />}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Why this much? — what a need is made of                            */
/* ------------------------------------------------------------------ */

export function WhyThisMuch({ title, lines }: { title: string; lines: { need: Line[]; have: Line[] } }) {
  const [open, setOpen] = useState(false);
  const list = (head: string, ls: Line[]) => (
    <div>
      <div className="mb-1.5 text-[12px] font-semibold uppercase tracking-[0.1em] text-[var(--pv-ink-2)]">{head}</div>
      {ls.length ? (
        <dl className="space-y-1 text-[14.5px]">
          {ls.map((l) => (
            <div key={l.label} className="flex justify-between gap-4">
              <dt className="text-[var(--pv-ink-2)]">{l.label}</dt>
              <dd className="font-medium pv-tabular">{money(l.amount)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="text-[14.5px] text-[var(--pv-ink-2)]">Nothing yet.</p>
      )}
    </div>
  );
  return (
    <div className="mt-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="inline-flex items-center gap-1 text-[14px] font-medium text-[var(--pv-ink-2)] hover:text-[var(--pv-ink)]"
      >
        {title} <ChevronDown size={16} className={cx('transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="pv-in mt-3 grid gap-6 rounded-xl bg-[var(--pv-surface)] p-5 md:grid-cols-2">
          {list('What the money is for', lines.need)}
          {list('What is already there', lines.have)}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Will the money last? — savings by age, today and with the plan      */
/* ------------------------------------------------------------------ */

const axisMoney = (v: number) => (v >= 1e6 ? `R${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1).replace(/\.0$/, '')}m` : v >= 1e3 ? `R${Math.round(v / 1e3)}k` : `R${Math.round(v)}`);
const niceMax = (v: number) => {
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(v, 1))));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
};

/** The pixel size of an element, so a chart can draw at its true size and its text stays readable. */
function useSize<T extends HTMLElement>(fallback: { w: number; h: number }) {
  const ref = useRef<T>(null);
  const [size, setSize] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([e]) => {
      const { width, height } = e.contentRect;
      if (width > 0 && height > 0) setSize({ w: Math.round(width), h: Math.round(height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size] as const;
}

export function MoneyLasts({ today, plan }: { today: RetirementResult; plan: RetirementResult }) {
  const [box, { w: W, h: H }] = useSize<HTMLDivElement>({ w: 1000, h: 260 });
  const m = { l: 64, r: 16, t: 30, b: 34 };
  const pw = W - m.l - m.r;
  const ph = H - m.t - m.b;
  const a0 = Math.floor(Math.min(today.currentAge, plan.currentAge));
  const a1 = Math.max(today.planningAge, plan.planningAge, a0 + 5);
  const peak = Math.max(1, ...[today, plan].flatMap((r) => r.timeline.map((t) => t.capitalReal)));
  const yMax = niceMax(peak * 1.08);
  const sx = (age: number) => m.l + ((age - a0) / (a1 - a0)) * pw;
  const sy = (v: number) => m.t + ph - (clamp(v, 0, yMax) / yMax) * ph;
  const pts = (r: RetirementResult) => r.timeline.filter((t) => t.age >= a0 && t.age <= a1);
  const path = (r: RetirementResult) =>
    pts(r)
      .map((t, i) => `${i ? 'L' : 'M'}${sx(t.age).toFixed(1)},${sy(t.capitalReal).toFixed(1)}`)
      .join(' ');
  const pp = pts(plan);
  const area = pp.length ? `${path(plan)} L${sx(pp[pp.length - 1].age).toFixed(1)},${sy(0)} L${sx(pp[0].age).toFixed(1)},${sy(0)} Z` : '';
  const ages: number[] = [];
  const every = W < 520 ? 20 : 10;
  for (let a = Math.ceil(a0 / every) * every; a <= a1; a += every) ages.push(a);
  const out = (r: RetirementResult) => (r.depletionAge !== null && r.depletionAge < r.planningAge ? r.depletionAge : null);
  const outcome = (r: RetirementResult) => {
    const o = out(r);
    return o !== null ? `runs out at ${Math.floor(o)}` : `lasts beyond ${r.planningAge}`;
  };
  const tOut = out(today);
  const pOut = out(plan);
  const same = Math.abs(today.retirementAge - plan.retirementAge) < 0.01 && today.timeline.every((t, i) => Math.abs(t.capitalReal - (plan.timeline[i]?.capitalReal ?? 0)) < 1);

  const svg = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const at = (r: RetirementResult, age: number) => r.timeline.find((t) => t.age === age)?.capitalReal ?? 0;
  const move = (e: PointerEvent) => {
    const ctm = svg.current?.getScreenCTM();
    if (!ctm) return;
    setHover(Math.round(clamp(a0 + (((e.clientX - ctm.e) / ctm.a - m.l) / pw) * (a1 - a0), a0, a1)));
  };
  const key = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    e.stopPropagation();
    setHover((h) => clamp((h ?? Math.round(plan.retirementAge)) + (e.key === 'ArrowLeft' ? -1 : 1), a0, a1));
  };
  const hx = hover !== null ? sx(hover) : 0;
  const halo = { stroke: 'var(--pv-page)', strokeWidth: 5, paintOrder: 'stroke' as const, strokeLinejoin: 'round' as const };

  return (
    <div>
      <div className="mb-1 flex flex-wrap gap-x-7 gap-y-1 text-[15px] text-[var(--pv-ink-2)]">
        {!same && (
          <span className="inline-flex items-center gap-2">
            <span className="h-[3px] w-5 rounded-full bg-[var(--pv-today)]" aria-hidden /> Today’s path: <b className="font-semibold text-[var(--pv-ink)]">{outcome(today)}</b>
          </span>
        )}
        <span className="inline-flex items-center gap-2">
          <span className="h-[3px] w-5 rounded-full bg-[var(--pv-plan)]" aria-hidden /> {same ? 'Savings, in today’s money:' : 'With the plan:'}{' '}
          <b className="font-semibold text-[var(--pv-ink)]">{outcome(plan)}</b>
        </span>
      </div>
      <div ref={box} className="relative h-[clamp(170px,26vh,320px)]">
        <svg
          ref={svg}
          viewBox={`0 0 ${W} ${H}`}
          width={W}
          height={H}
          className="block h-full w-full touch-none outline-none focus-visible:ring-2 focus-visible:ring-[var(--pv-plan)]"
          role="img"
          aria-label={`Savings by age in today’s money. With the plan, ${moneyCompact(plan.projectedCapitalReal)} at ${plan.retirementAge}, and the money ${outcome(plan)}.${same ? '' : ` Today’s path ${outcome(today)}.`} Use the arrow keys to read any year.`}
          tabIndex={0}
          onPointerMove={move}
          onPointerLeave={() => setHover(null)}
          onKeyDown={key}
          onBlur={() => setHover(null)}
        >
          {[0, 0.5, 1].map((f) => (
            <g key={f}>
              <line x1={m.l} x2={W - m.r} y1={sy(f * yMax)} y2={sy(f * yMax)} stroke={f === 0 ? 'var(--pv-axis)' : 'var(--pv-grid)'} strokeWidth={1} />
              <text x={m.l - 10} y={sy(f * yMax) + 5} fontSize={14} textAnchor="end" fill="var(--pv-muted)">
                {axisMoney(f * yMax)}
              </text>
            </g>
          ))}
          {ages.map((a) => (
            <text key={a} x={sx(a)} y={H - 8} fontSize={14} textAnchor="middle" fill="var(--pv-muted)">
              {a}
            </text>
          ))}
          <line x1={sx(plan.retirementAge)} x2={sx(plan.retirementAge)} y1={m.t - 12} y2={sy(0)} stroke="var(--pv-axis)" strokeWidth={1.5} strokeDasharray="4 4" />
          <text x={sx(plan.retirementAge) + 8} y={m.t - 2} fontSize={14} fill="var(--pv-ink-2)" {...halo}>
            Retires at {plan.retirementAge}
          </text>
          <path d={area} fill="var(--pv-plan)" opacity={0.12} />
          {!same && <path d={path(today)} fill="none" stroke="var(--pv-today)" strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />}
          <path d={path(plan)} fill="none" stroke="var(--pv-plan)" strokeWidth={3.5} strokeLinejoin="round" strokeLinecap="round" />
          {!same && tOut !== null && <circle cx={sx(tOut)} cy={sy(0)} r={6} fill="var(--pv-today)" stroke="var(--pv-page)" strokeWidth={2} />}
          {pOut !== null && <circle cx={sx(pOut)} cy={sy(0)} r={7} fill="var(--pv-critical)" stroke="var(--pv-page)" strokeWidth={2} />}
          {hover !== null && (
            <g>
              <line x1={hx} x2={hx} y1={m.t} y2={sy(0)} stroke="var(--pv-ink-2)" strokeWidth={1} />
              {!same && <circle cx={hx} cy={sy(at(today, hover))} r={5} fill="var(--pv-today)" stroke="var(--pv-page)" strokeWidth={2} />}
              <circle cx={hx} cy={sy(at(plan, hover))} r={6} fill="var(--pv-plan)" stroke="var(--pv-page)" strokeWidth={2} />
            </g>
          )}
        </svg>
        {hover !== null && (
          <div
            role="tooltip"
            className="pointer-events-none absolute top-2 z-30 min-w-[11rem] rounded-lg border border-[var(--pv-border)] bg-[var(--pv-raised)] px-3 py-2 text-[14px] shadow-[0_8px_24px_rgba(0,0,0,0.18)]"
            style={{ left: `${clamp((hx / W) * 100, 14, 86)}%`, transform: 'translateX(-50%)' }}
          >
            <div className="mb-1 font-semibold">Age {hover}</div>
            <div className="flex items-center gap-2">
              <span className="h-[3px] w-3 rounded-full bg-[var(--pv-plan)]" aria-hidden />
              <span className="font-semibold pv-tabular">{money(at(plan, hover))}</span>
              <span className="text-[var(--pv-ink-2)]">{same ? 'saved' : 'with the plan'}</span>
            </div>
            {!same && (
              <div className="flex items-center gap-2">
                <span className="h-[3px] w-3 rounded-full bg-[var(--pv-today)]" aria-hidden />
                <span className="font-semibold pv-tabular">{money(at(today, hover))}</span>
                <span className="text-[var(--pv-ink-2)]">today’s path</span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
