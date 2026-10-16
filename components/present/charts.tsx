'use client';

import { AlertTriangle, CheckCircle2, Minus, OctagonAlert } from 'lucide-react';
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import type { RetirementResult, Status } from '@/lib/fna/analysis';
import { money, moneyCompact, pct } from '@/lib/format';
import { cx } from '../ui';
import { measureStatus, shortOf, type Measure, type QuestionKey } from './answers';
import { runningTotals, type Bridge, type CostSeries, type FundedRow } from './brief';

/*
 * Charts in the Highcharts manner, drawn without the library: Inter type,
 * whisper gridlines, data labels on the marks, a dark shared tooltip, the
 * series fading back when another is hovered, and a grow-in animation the
 * first time a chart scrolls into view. The plan is the one action colour;
 * everything it is measured against is a warm grey.
 */

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/* ------------------------------------------------------------------ */
/* Status marks                                                        */
/* ------------------------------------------------------------------ */

const STATUS: Record<Status, { icon: typeof CheckCircle2; color: string }> = {
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
      <span>{text}</span>
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Scales, sizes, visibility                                           */
/* ------------------------------------------------------------------ */

/** Round axis ticks: steps of 1, 2, 2.5 or 5 × 10ⁿ. */
export function niceScale(lo: number, hi: number, count = 5) {
  if (hi - lo < 1e-9) hi = lo + 1;
  const raw = (hi - lo) / count;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? 10 * p;
  // A small overshoot past zero (a surplus) gets a little room, not a whole step.
  const min = lo < 0 && -lo < step * 0.35 ? lo * 1.2 : Math.floor(lo / step + 1e-9) * step;
  const max = Math.ceil(hi / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let v = Math.ceil(min / step - 1e-9) * step; v <= max + step / 2; v += step) ticks.push(Math.round(v / step) * step);
  return { min, max, ticks };
}

export const axisMoney = (v: number) => {
  const a = Math.abs(v);
  const s = v < 0 ? '−' : '';
  if (a >= 1e6) return `${s}R${(a / 1e6).toFixed(a % 1e6 === 0 ? 0 : 1).replace(/\.0$/, '')}m`;
  if (a >= 1e3) return `${s}R${(a / 1e3).toFixed(a % 1e3 === 0 ? 0 : 1).replace(/\.0$/, '')}k`;
  return `${s}R${Math.round(a)}`;
};

/** Becomes true the first time the element scrolls into view, so charts grow in as the reader arrives. */
function useInView<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    if (typeof IntersectionObserver === 'undefined') return setSeen(true);
    const io = new IntersectionObserver(([e]) => e.isIntersecting && setSeen(true), { threshold: 0.15 });
    io.observe(el);
    return () => io.disconnect();
  }, [seen]);
  return [ref, seen] as const;
}

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

/* ------------------------------------------------------------------ */
/* Plate, legend, tooltip                                              */
/* ------------------------------------------------------------------ */

export function Plate({ title, sub, children, legend, className }: { title: ReactNode; sub?: ReactNode; children: ReactNode; legend?: ReactNode; className?: string }) {
  return (
    <figure className={cx('pv-plate m-0', className)}>
      <figcaption>
        <h3 className="pv-plate-title">{title}</h3>
        {sub && <p className="pv-plate-sub">{sub}</p>}
      </figcaption>
      {children}
      {legend}
    </figure>
  );
}

export function Legend({ items }: { items: { label: string; color: string; line?: boolean }[] }) {
  return (
    <div className="pv-legend">
      {items.map((i) => (
        <span key={i.label}>
          <i className={cx(i.line && 'is-line')} style={{ background: i.color }} aria-hidden />
          {i.label}
        </span>
      ))}
    </div>
  );
}

interface TipState {
  x: number;
  y: number;
  w: number;
  content: ReactNode;
}

function useTip() {
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<TipState | null>(null);
  const show = (e: PointerEvent, content: ReactNode) => {
    const box = ref.current?.getBoundingClientRect();
    if (box) setTip({ x: e.clientX - box.left, y: e.clientY - box.top, w: box.width, content });
  };
  return { ref, tip, show, hide: () => setTip(null) };
}

function Tip({ tip }: { tip: TipState | null }) {
  if (!tip) return null;
  const left = clamp(tip.x, 100, Math.max(100, tip.w - 100));
  return (
    <div role="tooltip" className="pv-tip" style={{ left, top: tip.y, transform: 'translate(-50%, calc(-100% - 14px))' }}>
      {tip.content}
    </div>
  );
}

const TipRow = ({ color, children }: { color: string; children: ReactNode }) => (
  <div className="pv-tip-row">
    <i style={{ background: color }} aria-hidden />
    <span>{children}</span>
  </div>
);

/* ------------------------------------------------------------------ */
/* Bridge — a horizontal waterfall of what a need is made of           */
/* ------------------------------------------------------------------ */

const KIND_COLOR: Record<string, string> = {
  'k-add': 'var(--pv-today)',
  'k-less': 'var(--pv-soft)',
  'k-total': 'var(--pv-ink)',
  'k-plan': 'var(--pv-plan)',
  'k-bad': 'var(--pv-critical)',
  'k-good': 'var(--pv-good)',
};

export function BridgeChart({ bridge }: { bridge: Bridge }) {
  const [view, inView] = useInView<HTMLDivElement>();
  const t = useTip();
  const runs = runningTotals(bridge.rows);
  const values = runs.flatMap((r) => [r.from, r.to]).concat(bridge.target ? [bridge.target.value] : []);
  const s = niceScale(Math.min(0, ...values), Math.max(0, ...values), 5);
  const X = (v: number) => ((v - s.min) / (s.max - s.min)) * 100;
  const fmt = bridge.monthly ? money : moneyCompact;
  const full = (v: number) => (bridge.monthly ? `${money(v)} a month` : money(v));
  const signed = (v: number, f: (n: number) => string) => `${v < 0 ? '−' : '+'}${f(Math.abs(v))}`;
  return (
    <div
      ref={(el) => {
        view.current = el;
        t.ref.current = el;
      }}
      className={cx('pv-chart pv-bridge', inView && 'is-in')}
      onPointerLeave={t.hide}
    >
      <div className="pv-rows relative">
        {bridge.rows.map((r, i) => {
          const { from, to } = runs[i];
          const lo = Math.min(from, to);
          const hi = Math.max(from, to);
          const sum = r.kind === 'total' || r.kind === 'result';
          const cls = r.kind === 'result' ? (r.tone === 'bad' ? 'k-bad' : 'k-good') : `k-${r.kind}`;
          const next = bridge.rows[i + 1];
          const value = sum ? fmt(Math.abs(r.value)) : signed(r.value, fmt);
          const content = (
            <>
              <div className="pv-tip-head">{r.label}</div>
              <TipRow color={KIND_COLOR[cls]}>
                <b>{sum ? full(Math.abs(r.value)) : signed(r.value, full)}</b>
              </TipRow>
              {!sum && <div className="pv-tip-muted">Running total {full(to)}</div>}
              {r.note && <div className="pv-tip-muted">{r.note}</div>}
            </>
          );
          return (
            <div key={r.key} className={cx('pv-brow', sum && `is-${r.kind}`)} onPointerMove={(e) => t.show(e, content)} onPointerDown={(e) => t.show(e, content)}>
              <div className="pv-blabel">{r.label}</div>
              <div className="pv-btrack">
                {s.ticks.map((v) => (
                  <span key={v} className={cx('pv-grid-v', v === 0 && 'is-zero')} style={{ left: `${X(v)}%` }} aria-hidden />
                ))}
                {hi - lo > 0.5 && (
                  <span
                    className={cx('pv-bar', cls, !sum && to < from && 'is-neg')}
                    style={{ left: `${X(lo)}%`, width: `${Math.max(0.4, X(hi) - X(lo))}%`, transitionDelay: `0s, 0s, 0s, ${i * 45}ms` }}
                    aria-hidden
                  />
                )}
                <span className="pv-bval" style={{ left: `calc(${X(hi)}% + 6px)` }}>
                  {r.kind === 'result' && Math.abs(r.value) <= 0.5 ? '—' : value}
                </span>
                {next && next.kind !== 'total' && next.kind !== 'result' && <span className="pv-conn" style={{ left: `${X(to)}%` }} aria-hidden />}
              </div>
            </div>
          );
        })}
        {bridge.target && (
          <div className="pointer-events-none absolute inset-0 grid" style={{ gridTemplateColumns: 'var(--label-w) minmax(0, 1fr)' }} aria-hidden>
            <span />
            <div className="pv-btrack" style={{ height: 'auto' }}>
              <span className="pv-target" style={{ left: `${X(bridge.target.value)}%` }} />
            </div>
          </div>
        )}
      </div>
      <div className="pv-baxis" aria-hidden>
        <span />
        <div>
          {s.ticks.map((v) => (
            <span key={v} style={{ left: `${X(v)}%` }}>
              {axisMoney(v)}
            </span>
          ))}
        </div>
      </div>
      {bridge.target && (
        <p className="mt-2 text-[12.5px]" style={{ color: 'var(--pv-ink-2)' }}>
          <span className="mr-1.5 inline-block h-3 w-0 border-l-2 border-dashed align-[-2px]" style={{ borderColor: 'var(--pv-ink-2)' }} aria-hidden />
          {bridge.target.label}: {full(bridge.target.value)}
        </p>
      )}
      <Tip tip={t.tip} />
    </div>
  );
}

export const bridgeLegend = (bridge: Bridge) => {
  const has = (k: string) => bridge.rows.some((r) => r.kind === k);
  const result = bridge.rows.find((r) => r.kind === 'result');
  return (
    <Legend
      items={[
        ...(has('add') ? [{ label: 'What it is made of', color: 'var(--pv-today)' }] : []),
        ...(has('less') ? [{ label: 'Already there', color: 'var(--pv-soft)' }] : []),
        ...(has('plan') ? [{ label: 'The plan', color: 'var(--pv-plan)' }] : []),
        ...(has('total') ? [{ label: 'Total', color: 'var(--pv-ink)' }] : []),
        ...(result ? [{ label: result.tone === 'bad' ? 'Still short' : 'Met', color: result.tone === 'bad' ? 'var(--pv-critical)' : 'var(--pv-good)' }] : []),
      ]}
    />
  );
};

/* ------------------------------------------------------------------ */
/* Projection — retirement savings by age                              */
/* ------------------------------------------------------------------ */

export function ProjectionChart({ today, plan }: { today: RetirementResult; plan: RetirementResult }) {
  const [box, { w: W, h: H }] = useSize<HTMLDivElement>({ w: 900, h: 280 });
  const [view, inView] = useInView<HTMLDivElement>();
  const m = { l: 56, r: 14, t: 26, b: 30 };
  const pw = Math.max(10, W - m.l - m.r);
  const ph = Math.max(10, H - m.t - m.b);
  const a0 = Math.floor(Math.min(today.currentAge, plan.currentAge));
  const a1 = Math.max(today.planningAge, plan.planningAge, a0 + 5);
  const peak = Math.max(1, plan.requiredCapitalReal, ...[today, plan].flatMap((r) => r.timeline.map((x) => x.capitalReal)));
  const ys = niceScale(0, peak * 1.05, 4);
  const sx = (age: number) => m.l + ((age - a0) / (a1 - a0)) * pw;
  const sy = (v: number) => m.t + ph - (clamp(v, 0, ys.max) / ys.max) * ph;
  const pts = (r: RetirementResult) => r.timeline.filter((x) => x.age >= a0 && x.age <= a1);
  const path = (r: RetirementResult) =>
    pts(r)
      .map((x, i) => `${i ? 'L' : 'M'}${sx(x.age).toFixed(1)},${sy(x.capitalReal).toFixed(1)}`)
      .join(' ');
  const pp = pts(plan);
  const area = pp.length ? `${path(plan)} L${sx(pp[pp.length - 1].age).toFixed(1)},${sy(0)} L${sx(pp[0].age).toFixed(1)},${sy(0)} Z` : '';
  const every = W < 520 ? 10 : 5;
  const ages: number[] = [];
  for (let x = Math.ceil(a0 / every) * every; x <= a1; x += every) ages.push(x);
  const out = (r: RetirementResult) => (r.depletionAge !== null && r.depletionAge < r.planningAge ? r.depletionAge : null);
  const outcome = (r: RetirementResult) => (out(r) !== null ? `runs out at ${Math.floor(out(r)!)}` : `lasts beyond ${r.planningAge}`);
  const same = Math.abs(today.retirementAge - plan.retirementAge) < 0.01 && today.timeline.every((x, i) => Math.abs(x.capitalReal - (plan.timeline[i]?.capitalReal ?? 0)) < 1);
  const svg = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const at = (r: RetirementResult, age: number) => r.timeline.find((x) => x.age === age)?.capitalReal ?? 0;
  const move = (e: PointerEvent) => {
    const ctm = svg.current?.getScreenCTM();
    if (ctm) setHover(Math.round(clamp(a0 + (((e.clientX - ctm.e) / ctm.a - m.l) / pw) * (a1 - a0), a0, a1)));
  };
  const key = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    e.stopPropagation();
    setHover((h) => clamp((h ?? Math.round(plan.retirementAge)) + (e.key === 'ArrowLeft' ? -1 : 1), a0, a1));
  };
  const hx = hover !== null ? sx(hover) : 0;
  const retX = sx(plan.retirementAge);
  const needY = sy(plan.requiredCapitalReal);
  const halo = { stroke: 'var(--pv-surface)', strokeWidth: 4, paintOrder: 'stroke' as const, strokeLinejoin: 'round' as const };
  const pOut = out(plan);
  const tOut = out(today);
  return (
    <div ref={view} className={cx('pv-chart', inView && 'is-in')}>
      <div className="mb-1 flex flex-wrap gap-x-6 gap-y-1">
        {!same && (
          <span className="inline-flex items-center gap-2">
            <i className="inline-block h-[3px] w-4 rounded" style={{ background: 'var(--pv-today)' }} aria-hidden /> Today’s path: <b style={{ color: 'var(--pv-ink)' }}>{outcome(today)}</b>
          </span>
        )}
        <span className="inline-flex items-center gap-2">
          <i className="inline-block h-[3px] w-4 rounded" style={{ background: 'var(--pv-plan)' }} aria-hidden /> {same ? 'Savings:' : 'With the plan:'}{' '}
          <b style={{ color: 'var(--pv-ink)' }}>{outcome(plan)}</b>
        </span>
      </div>
      <div ref={box} className="relative h-[clamp(220px,32vh,330px)]">
        <svg
          ref={svg}
          viewBox={`0 0 ${W} ${H}`}
          width={W}
          height={H}
          className="block h-full w-full touch-none outline-none"
          role="img"
          aria-label={`Savings by age in today’s money. With the plan, ${moneyCompact(plan.projectedCapitalReal)} at ${plan.retirementAge} against ${moneyCompact(plan.requiredCapitalReal)} needed; the money ${outcome(plan)}.`}
          tabIndex={0}
          onPointerMove={move}
          onPointerDown={move}
          onPointerLeave={() => setHover(null)}
          onKeyDown={key}
          onBlur={() => setHover(null)}
        >
          <defs>
            <linearGradient id="pv-area" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--pv-plan)" stopOpacity={0.32} />
              <stop offset="100%" stopColor="var(--pv-plan)" stopOpacity={0.03} />
            </linearGradient>
            <clipPath id="pv-reveal">
              <rect x={0} y={0} height={H} width={inView ? W : 0} style={{ transition: 'width 1.1s cubic-bezier(.2,.7,.2,1)' }} />
            </clipPath>
          </defs>
          <rect x={retX} y={m.t} width={Math.max(0, sx(a1) - retX)} height={ph} fill="var(--pv-ink)" opacity={0.035} />
          <text x={sx(a1) - 6} y={m.t + 14} fontSize={11.5} textAnchor="end" fill="var(--pv-muted)">
            Retirement
          </text>
          {ys.ticks.map((v) => (
            <g key={v}>
              <line x1={m.l} x2={W - m.r} y1={sy(v)} y2={sy(v)} stroke={v === 0 ? 'var(--pv-axis)' : 'var(--pv-grid)'} strokeWidth={1} />
              <text x={m.l - 8} y={sy(v) + 4} fontSize={11.5} textAnchor="end" fill="var(--pv-muted)">
                {axisMoney(v)}
              </text>
            </g>
          ))}
          {ages.map((x) => (
            <text key={x} x={sx(x)} y={H - 8} fontSize={11.5} textAnchor="middle" fill="var(--pv-muted)">
              {x}
            </text>
          ))}
          <line x1={retX} x2={retX} y1={m.t - 10} y2={sy(0)} stroke="var(--pv-ink-2)" strokeWidth={1.2} strokeDasharray="4 4" />
          <text x={retX + 6} y={m.t - 2} fontSize={12} fill="var(--pv-ink-2)" {...halo}>
            Retires at {plan.retirementAge}
          </text>
          <g clipPath="url(#pv-reveal)">
            <path d={area} fill="url(#pv-area)" />
            {!same && <path d={path(today)} fill="none" stroke="var(--pv-today)" strokeWidth={2} strokeLinejoin="round" />}
            <path d={path(plan)} fill="none" stroke="var(--pv-plan)" strokeWidth={3} strokeLinejoin="round" />
          </g>
          {plan.requiredCapitalReal > 0.5 && (
            <g>
              <line x1={retX - 26} x2={retX + 26} y1={needY} y2={needY} stroke="var(--pv-ink)" strokeWidth={2} strokeDasharray="5 3" />
              <text x={retX - 30} y={needY + 4} fontSize={12} textAnchor="end" fill="var(--pv-ink)" {...halo}>
                needed {axisMoney(plan.requiredCapitalReal)}
              </text>
            </g>
          )}
          {!same && tOut !== null && <circle cx={sx(tOut)} cy={sy(0)} r={5} fill="var(--pv-today)" stroke="var(--pv-surface)" strokeWidth={2} />}
          {pOut !== null && <circle cx={sx(pOut)} cy={sy(0)} r={6} fill="var(--pv-critical)" stroke="var(--pv-surface)" strokeWidth={2} />}
          {hover !== null && (
            <g>
              <line x1={hx} x2={hx} y1={m.t} y2={sy(0)} stroke="var(--pv-ink-2)" strokeWidth={1} />
              {!same && <circle cx={hx} cy={sy(at(today, hover))} r={4.5} fill="var(--pv-today)" stroke="var(--pv-surface)" strokeWidth={2} />}
              <circle cx={hx} cy={sy(at(plan, hover))} r={5.5} fill="var(--pv-plan)" stroke="var(--pv-surface)" strokeWidth={2} />
            </g>
          )}
        </svg>
        {hover !== null && (
          <div role="tooltip" className="pv-tip" style={{ left: clamp(hx, 100, Math.max(100, W - 100)), top: 6, transform: 'translateX(-50%)' }}>
            <div className="pv-tip-head">Age {hover}</div>
            <TipRow color="var(--pv-plan)">
              <b>{money(at(plan, hover))}</b> {same ? 'saved' : 'with the plan'}
            </TipRow>
            {!same && (
              <TipRow color="var(--pv-today)">
                <b>{money(at(today, hover))}</b> on today’s path
              </TipRow>
            )}
            <div className="pv-tip-muted">In today’s money</div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Study costs by year                                                 */
/* ------------------------------------------------------------------ */

const SERIES = ['var(--pv-today)', 'var(--pv-ink-2)', 'var(--pv-soft)', 'var(--pv-muted)'];

export function CostColumns({ series }: { series: CostSeries[] }) {
  const [view, inView] = useInView<HTMLDivElement>();
  const t = useTip();
  const years = series.flatMap((s) => s.points.map((p) => p.year));
  if (!years.length) return null;
  const y0 = Math.min(...years);
  const y1 = Math.max(...years);
  const cols = Array.from({ length: y1 - y0 + 1 }, (_, i) => {
    const year = y0 + i;
    return { year, parts: series.map((s, j) => ({ name: s.name, color: SERIES[j % SERIES.length], cost: s.points.find((p) => p.year === year)?.cost ?? 0 })) };
  });
  const top = Math.max(1, ...cols.map((c) => c.parts.reduce((s, p) => s + p.cost, 0)));
  const ys = niceScale(0, top, 4);
  const n = cols.length;
  const slot = 100 / n;
  const skip = n > 12 ? 2 : 1;
  return (
    <div
      ref={(el) => {
        view.current = el;
        t.ref.current = el;
      }}
      className={cx('pv-chart', inView && 'is-in')}
      onPointerLeave={t.hide}
    >
      <div className="pv-cols">
        {ys.ticks.map((v) => (
          <span key={v} className="pv-grid-h" style={{ bottom: `${(v / ys.max) * 100}%` }} aria-hidden>
            <span className="pv-ytick">{axisMoney(v)}</span>
          </span>
        ))}
        {cols.map((c, i) => {
          const total = c.parts.reduce((s, p) => s + p.cost, 0);
          const content = (
            <>
              <div className="pv-tip-head">{c.year}</div>
              {c.parts
                .filter((p) => p.cost > 0.5)
                .map((p) => (
                  <TipRow key={p.name} color={p.color}>
                    <b>{money(p.cost)}</b> {p.name}
                  </TipRow>
                ))}
            </>
          );
          return (
            <div
              key={c.year}
              className="pv-col"
              style={{ left: `calc(${i * slot}% + 2px)`, width: `calc(${slot}% - 4px)`, height: `${(total / ys.max) * 100}%`, transitionDelay: `${i * 40}ms, 0s` }}
              onPointerMove={(e) => total > 0.5 && t.show(e, content)}
              onPointerDown={(e) => total > 0.5 && t.show(e, content)}
            >
              {c.parts.map((p) => (p.cost > 0.5 ? <i key={p.name} style={{ background: p.color, height: `${(p.cost / total) * 100}%` }} /> : null))}
            </div>
          );
        })}
      </div>
      <div className="relative ml-[52px] h-5" aria-hidden>
        {cols.map((c, i) =>
          i % skip === 0 ? (
            <span key={c.year} className="pv-xtick" style={{ left: `${(i + 0.5) * slot}%` }}>
              {c.year}
            </span>
          ) : null,
        )}
      </div>
      {series.length > 1 && <Legend items={series.map((s, j) => ({ label: s.name, color: SERIES[j % SERIES.length] }))} />}
      <Tip tip={t.tip} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Bullets — each child’s studies or each goal against its cost        */
/* ------------------------------------------------------------------ */

export function BulletChart({ rows, met }: { rows: Measure[]; met: string }) {
  const [view, inView] = useInView<HTMLDivElement>();
  const scale = Math.max(1, ...rows.map((r) => Math.max(r.need, r.plan, r.today))) * 1.04;
  const X = (v: number) => (clamp(v, 0, scale) / scale) * 100;
  return (
    <div ref={view} className={cx('pv-chart', inView && 'is-in')}>
      {rows.map((r) => {
        const status = measureStatus(r);
        const short = shortOf(r);
        return (
          <div key={r.key} className="pv-bullet" title={`${r.label}: ${money(r.need)} needed; ${money(r.today)} expected today, ${money(r.plan)} with the plan.`}>
            <div className="min-w-0">
              <div className="font-semibold" style={{ color: 'var(--pv-ink)' }}>
                {r.label}
              </div>
              <div>
                {moneyCompact(r.need)} needed {r.when}
              </div>
            </div>
            <div className="pv-btrack2" aria-hidden>
              <span className="pv-ghost" style={{ width: `${X(r.need)}%` }} />
              <span className="pv-fill" style={{ width: `${X(Math.min(r.today, r.plan))}%`, background: 'var(--pv-today)' }} />
              {r.plan > r.today + 0.5 && <span className="pv-fill" style={{ left: `${X(r.today)}%`, width: `${X(r.plan) - X(r.today)}%`, background: 'var(--pv-plan)' }} />}
              <span className="pv-mark" style={{ left: `${X(r.need)}%` }} />
            </div>
            <div className="text-right">
              <StatusMark status={status} text={status === 'covered' ? met : `${moneyCompact(short)} short`} className="justify-end" />
            </div>
          </div>
        );
      })}
      <Legend
        items={[
          { label: 'Expected today', color: 'var(--pv-today)' },
          ...(rows.some((r) => r.plan > r.today + 0.5) ? [{ label: 'Added by the plan', color: 'var(--pv-plan)' }] : []),
          { label: 'Needed', color: 'var(--pv-ink)', line: true },
        ]}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Dumbbell — every need, today and with the plan                      */
/* ------------------------------------------------------------------ */

export function DumbbellChart({ rows, onSelect }: { rows: FundedRow[]; onSelect?: (q: QuestionKey) => void }) {
  const [view, inView] = useInView<HTMLDivElement>();
  const t = useTip();
  const groups = [...new Set(rows.map((r) => r.group))];
  return (
    <div
      ref={(el) => {
        view.current = el;
        t.ref.current = el;
      }}
      className={cx('pv-chart', inView && 'is-in')}
      onPointerLeave={t.hide}
    >
      <div className="pv-dumb" aria-hidden>
        <span />
        <div className="relative h-4">
          {[0, 0.5, 1].map((v) => (
            <span key={v} className="pv-xtick" style={{ left: `${v * 100}%`, top: 0 }}>
              {pct(v)}
            </span>
          ))}
        </div>
        <span />
      </div>
      {groups.map((g) => (
        <div key={g}>
          <div className="pv-dumb-group">{g}</div>
          {rows
            .filter((r) => r.group === g)
            .map((r) => {
              const lo = Math.min(r.today, r.plan);
              const hi = Math.max(r.today, r.plan);
              const content = (
                <>
                  <div className="pv-tip-head">
                    {r.label} · {r.group}
                  </div>
                  <TipRow color="var(--pv-today)">
                    <b>{pct(r.today)}</b> provided for today
                  </TipRow>
                  <TipRow color="var(--pv-plan)">
                    <b>{pct(r.plan)}</b> with the plan
                  </TipRow>
                </>
              );
              return (
                <button
                  key={r.key}
                  type="button"
                  className="pv-dumb w-full text-left"
                  onClick={() => onSelect?.(r.q)}
                  onPointerMove={(e) => t.show(e, content)}
                  aria-label={`${r.label}, ${r.group}: ${pct(r.today)} today, ${pct(r.plan)} with the plan`}
                >
                  <span className="truncate" style={{ color: 'var(--pv-ink)' }}>
                    {r.label}
                  </span>
                  <span className="pv-dumb-track" aria-hidden>
                    {[0, 0.5, 1].map((v) => (
                      <span key={v} className={cx('pv-grid-v', v === 1 && 'is-zero')} style={{ left: `${v * 100}%` }} />
                    ))}
                    <span className="pv-dumb-line" style={{ left: `${lo * 100}%`, width: `${(hi - lo) * 100}%` }} />
                    <span className="pv-dumb-dot" style={{ left: `${r.today * 100}%`, background: 'var(--pv-today)' }} />
                    <span className="pv-dumb-dot" style={{ left: `${(inView ? r.plan : r.today) * 100}%`, background: 'var(--pv-plan)' }} />
                  </span>
                  <span className="pv-num text-right" style={{ color: 'var(--pv-ink)' }}>
                    {pct(r.plan)}
                  </span>
                </button>
              );
            })}
        </div>
      ))}
      <Legend
        items={[
          { label: 'Today', color: 'var(--pv-today)' },
          { label: 'With the plan', color: 'var(--pv-plan)' },
        ]}
      />
      <Tip tip={t.tip} />
    </div>
  );
}
