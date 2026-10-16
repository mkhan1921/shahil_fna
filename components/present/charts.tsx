'use client';

import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import type { RetirementResult, Status } from '@/lib/fna/analysis';
import { money, moneyCompact, pct } from '@/lib/format';
import { cx } from '../ui';
import { Fleuron, Manicule, roman } from '../report/vintage/ink';
import { measureStatus, shortOf, type Measure, type QuestionKey } from './answers';
import { runningTotals, type Bridge, type BridgeRow, type CostSeries, type FundedRow } from './brief';

/*
 * The session's plates, engraved as the vintage report's are: sepia ink for
 * what is there, red for what is short, and blue for what the plan adds.
 * Parts are told apart by hatching and stipple rather than by colour, labels
 * are set in the text face, and each plate answers a pointer or a tap with a
 * paper slip giving the exact figure. The figures move as the levers do.
 */

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/* ------------------------------------------------------------------ */
/* Marks: the manicule for a matter needing attention, the fleuron     */
/* for one in good order                                               */
/* ------------------------------------------------------------------ */

export function Mark({ status }: { status: Status }) {
  if (status === 'covered') return <Fleuron />;
  if (status === 'na')
    return (
      <span className="pv-mark-na" aria-hidden>
        —
      </span>
    );
  return <Manicule red={status === 'shortfall'} />;
}

export function StatusMark({ status, text, className }: { status: Status; text: ReactNode; className?: string }) {
  return (
    <span className={cx('pv-status', status === 'shortfall' && 'is-short', className)}>
      <Mark status={status} />
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
  return { min, max, ticks, step };
}

export const axisMoney = (v: number) => {
  const a = Math.abs(v);
  const s = v < 0 ? '−' : '';
  if (a >= 1e6) return `${s}R${(a / 1e6).toFixed(a % 1e6 === 0 ? 0 : 1).replace(/\.0$/, '')}m`;
  if (a >= 1e3) return `${s}R${(a / 1e3).toFixed(a % 1e3 === 0 ? 0 : 1).replace(/\.0$/, '')}k`;
  return `${s}R${Math.round(a)}`;
};

/** Becomes true the first time the element scrolls into view, so a plate draws itself as the reader arrives. */
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
/* Plate, key, slip                                                    */
/* ------------------------------------------------------------------ */

export type Tone = 'add' | 'less' | 'total' | 'plan' | 'bad' | 'good' | 'box' | 'c1' | 'c2' | 'c3' | 'c4';
export interface KeyItem {
  label: string;
  tone?: Tone;
  line?: 'ink' | 'plan' | 'dash' | 'dot';
  /** A cross, as drawn where savings run out. */
  cross?: 'red' | 'ink';
}

const Swatch = ({ tone, line, cross, small }: { tone?: Tone; line?: KeyItem['line']; cross?: KeyItem['cross']; small?: boolean }) => (
  <i className={cx('pv-sw', small && 'is-sm', tone && `t-${tone}`, line && `is-line l-${line}`, cross && `is-cross x-${cross}`)} aria-hidden />
);

export function Key({ items }: { items: KeyItem[] }) {
  return (
    <ul className="pv-key">
      {items.map((k) => (
        <li key={k.label}>
          <Swatch tone={k.tone} line={k.line} cross={k.cross} />
          {k.label}
        </li>
      ))}
    </ul>
  );
}

/** “Plate IV” — what it shows — the engraving — its key — how to read it. */
export function Plate({ n, title, children, keyItems, read, className }: { n: number; title: ReactNode; children: ReactNode; keyItems?: KeyItem[]; read?: ReactNode; className?: string }) {
  return (
    <figure className={cx('pv-plate', className)}>
      <figcaption className="pv-plate-head">
        <span className="pv-plate-no">Plate {roman(n)}</span>
        <span className="pv-plate-title">{title}</span>
      </figcaption>
      {children}
      {keyItems && keyItems.length > 0 && <Key items={keyItems} />}
      {read && (
        <p className="pv-plate-read">
          <i>How to read it.</i> {read}
        </p>
      )}
    </figure>
  );
}

interface TipState {
  x: number;
  y: number;
  w: number;
  content: ReactNode;
}

/** Clears a hover when the reader touches anywhere outside the element. */
function useTouchAway(ref: { current: Element | null }, on: boolean, clear: () => void) {
  useEffect(() => {
    if (!on) return;
    const away = (e: Event) => {
      if (!ref.current?.contains(e.target as Node)) clear();
    };
    document.addEventListener('pointerdown', away);
    return () => document.removeEventListener('pointerdown', away);
  }, [ref, on, clear]);
}

function useTip() {
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<TipState | null>(null);
  const hide = useCallback(() => setTip(null), []);
  // A finger lifting is not leaving: on a touch screen the slip stays until the reader touches elsewhere.
  useTouchAway(ref, !!tip, hide);
  const show = (e: PointerEvent, content: ReactNode) => {
    const box = ref.current?.getBoundingClientRect();
    if (box) setTip({ x: e.clientX - box.left, y: e.clientY - box.top, w: box.width, content });
  };
  const leave = (e: PointerEvent) => e.pointerType !== 'touch' && hide();
  return { ref, tip, show, hide, leave };
}

/** The paper slip that gives a mark's exact figure. */
function Slip({ tip }: { tip: TipState | null }) {
  if (!tip) return null;
  const left = clamp(tip.x, 110, Math.max(110, tip.w - 110));
  return (
    <div role="tooltip" className="pv-tip" style={{ left, top: tip.y, transform: 'translate(-50%, calc(-100% - 16px))' }}>
      {tip.content}
    </div>
  );
}

const SlipRow = ({ tone, line, children }: { tone?: Tone; line?: KeyItem['line']; children: ReactNode }) => (
  <div className="pv-tip-row">
    <Swatch tone={tone} line={line} small />
    <span>{children}</span>
  </div>
);

/* ------------------------------------------------------------------ */
/* Bridge — a need built up from its parts, then met line by line      */
/* ------------------------------------------------------------------ */

const toneOf = (r: BridgeRow): Tone => (r.kind === 'result' ? (r.tone === 'bad' ? 'bad' : 'good') : r.kind);

export function BridgeChart({ bridge }: { bridge: Bridge }) {
  const [view, inView] = useInView<HTMLDivElement>();
  const t = useTip();
  const runs = runningTotals(bridge.rows);
  const values = runs.flatMap((r) => [r.from, r.to]).concat(bridge.target ? [bridge.target.value] : []);
  const s = niceScale(Math.min(0, ...values), Math.max(0, ...values), 5);
  const X = (v: number) => ((v - s.min) / (s.max - s.min)) * 100;
  const zero = Math.max(0, s.ticks.indexOf(0));
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
      onPointerLeave={t.leave}
    >
      <div className="pv-rows relative">
        {bridge.rows.map((r, i) => {
          const { from, to } = runs[i];
          const lo = Math.min(from, to);
          const hi = Math.max(from, to);
          const sum = r.kind === 'total' || r.kind === 'result';
          const tone = toneOf(r);
          const next = bridge.rows[i + 1];
          const value = sum ? fmt(Math.abs(r.value)) : signed(r.value, fmt);
          const content = (
            <>
              <div className="pv-tip-head">{r.label}</div>
              <SlipRow tone={tone}>
                <b>{sum ? full(Math.abs(r.value)) : signed(r.value, full)}</b>
              </SlipRow>
              {!sum && <div className="pv-tip-muted">bringing the total to {full(to)}</div>}
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
                    className={cx('pv-b', `t-${tone}`, !sum && to < from && 'is-neg')}
                    style={{ left: `${X(lo)}%`, width: `max(3px, ${X(hi) - X(lo)}%)`, transitionDelay: `0s, 0s, 0s, ${i * 45}ms` }}
                    aria-hidden
                  />
                )}
                <span className={cx('pv-bval', tone === 'bad' && 'is-bad', tone === 'plan' && 'is-plan')} style={{ left: `calc(${X(hi)}% + 8px)` }}>
                  {r.kind === 'result' && Math.abs(r.value) <= 0.5 ? 'nothing' : value}
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
          {s.ticks.map((v, i) => (
            // On a narrow screen every other label is dropped, always keeping R0.
            <span key={v} className={cx((i - zero) % 2 !== 0 && 'is-alt')} style={{ left: `${X(v)}%` }}>
              {axisMoney(v)}
            </span>
          ))}
        </div>
      </div>
      <Slip tip={t.tip} />
    </div>
  );
}

/** The key to a bridge, in the words of the question it belongs to. */
export const bridgeKey = (bridge: Bridge, words: Partial<Record<'add' | 'total' | 'less' | 'plan' | 'bad' | 'good', string>>): KeyItem[] => {
  const has = (k: Tone) => bridge.rows.some((r) => toneOf(r) === k);
  return (['add', 'total', 'less', 'plan', 'good', 'bad'] as const).filter((k) => words[k] && has(k)).map((k) => ({ label: words[k]!, tone: k }));
};

/* ------------------------------------------------------------------ */
/* Graph paper — retirement savings by age                             */
/* ------------------------------------------------------------------ */

export function ProjectionChart({ today, plan }: { today: RetirementResult; plan: RetirementResult }) {
  const [box, { w: W, h: H }] = useSize<HTMLDivElement>({ w: 840, h: 340 });
  const [view, inView] = useInView<HTMLDivElement>();
  const small = W < 560;
  const fs = small ? 14 : 15.5;
  const m = { l: small ? 54 : 70, r: 12, t: 14, b: small ? 38 : 44 };
  const pw = Math.max(10, W - m.l - m.r);
  const ph = Math.max(10, H - m.t - m.b);
  const a0 = Math.floor(Math.min(today.currentAge, plan.currentAge));
  const a1 = Math.max(today.planningAge, plan.planningAge, a0 + 5);
  const peak = Math.max(1, plan.requiredCapitalReal, ...[today, plan].flatMap((r) => r.timeline.map((x) => x.capitalReal)));
  const ys = niceScale(0, peak * 1.06, small ? 4 : 5);
  const sx = (age: number) => m.l + ((age - a0) / (a1 - a0)) * pw;
  const sy = (v: number) => m.t + ph - (clamp(v, 0, ys.max) / ys.max) * ph;
  const pts = (r: RetirementResult) => r.timeline.filter((x) => x.age >= a0 && x.age <= a1);
  const path = (r: RetirementResult) =>
    pts(r)
      .map((x, i) => `${i ? 'L' : 'M'}${sx(x.age).toFixed(1)},${sy(x.capitalReal).toFixed(1)}`)
      .join(' ');
  const same = Math.abs(today.retirementAge - plan.retirementAge) < 0.01 && today.timeline.every((x, i) => Math.abs(x.capitalReal - (plan.timeline[i]?.capitalReal ?? 0)) < 1);
  const out = (r: RetirementResult) => (r.depletionAge !== null && r.depletionAge < r.planningAge ? r.depletionAge : null);
  const svg = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const unhover = useCallback(() => setHover(null), []);
  useTouchAway(svg, hover !== null, unhover);
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

  // Graph paper: a fine rule for each year (or two) and each fifth of a step, a stronger one every five years and every step.
  const yearEvery = pw / (a1 - a0) < 6 ? 2 : 1;
  const vLines: { x: number; major: boolean }[] = [];
  for (let a = a0; a <= a1; a += yearEvery) vLines.push({ x: sx(a), major: a % 5 === 0 });
  const hLines: { y: number; major: boolean }[] = [];
  for (let k = 0; k <= Math.round(ys.max / (ys.step / 5)); k++) hLines.push({ y: sy((k * ys.step) / 5), major: k % 5 === 0 });
  const ages: number[] = [];
  for (let x = Math.ceil(a0 / (small ? 10 : 5)) * (small ? 10 : 5); x <= a1; x += small ? 10 : 5) ages.push(x);

  const planInk = same ? 'var(--pv-ink)' : 'var(--pv-plan)';
  const retX = sx(plan.retirementAge);
  const needY = sy(plan.requiredCapitalReal);
  const projY = sy(plan.projectedCapitalReal);
  const short = plan.requiredCapitalReal - plan.projectedCapitalReal;
  const pOut = out(plan);
  const tOut = out(today);
  const halo = { stroke: 'var(--pv-graph)', strokeWidth: 4, paintOrder: 'stroke' as const, strokeLinejoin: 'round' as const };
  const hx = hover !== null ? sx(hover) : 0;
  const outcome = (r: RetirementResult) => (out(r) !== null ? `runs out at about ${Math.floor(out(r)!)}` : `lasts beyond ${r.planningAge}`);

  return (
    <div ref={view} className={cx('pv-chart', inView && 'is-in')}>
      <div ref={box} className="relative h-[clamp(260px,40vh,360px)]">
        <svg
          ref={svg}
          viewBox={`0 0 ${W} ${H}`}
          width={W}
          height={H}
          className="pv-svg h-full w-full touch-pan-y outline-none"
          role="img"
          aria-label={`Savings by age in today’s money. ${same ? '' : `With the plan, `}${moneyCompact(plan.projectedCapitalReal)} at ${plan.retirementAge} against ${moneyCompact(plan.requiredCapitalReal)} needed; the money ${outcome(plan)}.${same ? '' : ` On today’s path it ${outcome(today)}.`}`}
          tabIndex={0}
          onPointerMove={move}
          onPointerDown={move}
          onPointerLeave={(e) => e.pointerType !== 'touch' && setHover(null)}
          onKeyDown={key}
          onBlur={() => setHover(null)}
        >
          <defs>
            <clipPath id="pv-reveal">
              <rect x={0} y={0} height={H} width={inView ? W : 0} style={{ transition: 'width 1.2s cubic-bezier(.2,.7,.2,1)' }} />
            </clipPath>
          </defs>
          <rect x={m.l} y={m.t} width={pw} height={ph} fill="var(--pv-graph)" />
          {vLines.map((l) => (
            <line key={`v${l.x}`} x1={l.x} x2={l.x} y1={m.t} y2={m.t + ph} stroke={l.major ? 'var(--pv-grid-major)' : 'var(--pv-grid-minor)'} strokeWidth={l.major ? 0.9 : 0.6} />
          ))}
          {hLines.map((l) => (
            <line key={`h${l.y}`} x1={m.l} x2={m.l + pw} y1={l.y} y2={l.y} stroke={l.major ? 'var(--pv-grid-major)' : 'var(--pv-grid-minor)'} strokeWidth={l.major ? 0.9 : 0.6} />
          ))}
          <rect x={m.l} y={m.t} width={pw} height={ph} fill="none" stroke="var(--pv-ink)" strokeWidth={1.1} />
          {ys.ticks.map((v) => (
            <g key={v}>
              <line x1={m.l} x2={m.l + 6} y1={sy(v)} y2={sy(v)} stroke="var(--pv-ink)" strokeWidth={1} />
              <text x={m.l - 8} y={sy(v) + 5} fontSize={fs} textAnchor="end">
                {axisMoney(v)}
              </text>
            </g>
          ))}
          {ages.map((x) => (
            <g key={x}>
              <line x1={sx(x)} x2={sx(x)} y1={m.t + ph} y2={m.t + ph - 6} stroke="var(--pv-ink)" strokeWidth={1} />
              <text x={sx(x)} y={m.t + ph + fs + 5} fontSize={fs} textAnchor="middle">
                {x}
              </text>
            </g>
          ))}
          {/* retirement */}
          <line x1={retX} x2={retX} y1={m.t} y2={m.t + ph} stroke="var(--pv-ink-2)" strokeWidth={1} strokeDasharray="2 3" />
          <text transform={`translate(${retX - 6} ${m.t + ph - 8}) rotate(-90)`} fontSize={fs} fontStyle="italic" fill="var(--pv-ink-2)" {...halo}>
            retires at {plan.retirementAge}
          </text>
          {/* capital needed */}
          {plan.requiredCapitalReal > 0.5 && (
            <g>
              <line x1={m.l} x2={m.l + pw} y1={needY} y2={needY} stroke="var(--pv-ink)" strokeWidth={1.1} strokeDasharray="6 4" />
              <text x={m.l + 8} y={needY > m.t + 26 ? needY - 7 : needY + fs + 4} fontSize={fs} fontStyle="italic" {...halo}>
                capital needed at {plan.retirementAge}: {moneyCompact(plan.requiredCapitalReal)}
              </text>
            </g>
          )}
          <g clipPath="url(#pv-reveal)">
            {!same && <path d={path(today)} fill="none" stroke="var(--pv-ink)" strokeWidth={1.5} strokeLinejoin="round" />}
            <path d={path(plan)} fill="none" stroke={planInk} strokeWidth={same ? 2 : 2.6} strokeLinejoin="round" />
            {pts(plan)
              .filter((x) => (Math.round(x.age) - a0) % 5 === 0 && x.capitalReal > 0.5)
              .map((x) => (
                <circle key={x.age} cx={sx(x.age)} cy={sy(x.capitalReal)} r={3.4} fill="var(--pv-graph)" stroke={planInk} strokeWidth={1.4} />
              ))}
          </g>
          {/* the shortfall at retirement, bracketed in red */}
          {short > 0.5 && (
            <g>
              <line x1={retX + 10} x2={retX + 10} y1={needY} y2={projY} stroke="var(--pv-red)" strokeWidth={1.4} />
              <line x1={retX + 5} x2={retX + 15} y1={needY} y2={needY} stroke="var(--pv-red)" strokeWidth={1.4} />
              <line x1={retX + 5} x2={retX + 15} y1={projY} y2={projY} stroke="var(--pv-red)" strokeWidth={1.4} />
              <text x={retX + 20} y={(needY + projY) / 2 + 5} fontSize={fs} fontWeight={600} fill="var(--pv-red)" {...halo}>
                short {moneyCompact(short)}
              </text>
            </g>
          )}
          {/* where the money runs out */}
          {!same && tOut !== null && (
            <path d={`M${sx(tOut) - 4},${sy(0) - 4} L${sx(tOut) + 4},${sy(0) + 4} M${sx(tOut) - 4},${sy(0) + 4} L${sx(tOut) + 4},${sy(0) - 4}`} stroke="var(--pv-ink)" strokeWidth={1.4} />
          )}
          {pOut !== null && (
            <path d={`M${sx(pOut) - 6},${sy(0) - 6} L${sx(pOut) + 6},${sy(0) + 6} M${sx(pOut) - 6},${sy(0) + 6} L${sx(pOut) + 6},${sy(0) - 6}`} stroke="var(--pv-red)" strokeWidth={2.2} />
          )}
          {hover !== null && (
            <g>
              <line x1={hx} x2={hx} y1={m.t} y2={m.t + ph} stroke="var(--pv-ink)" strokeWidth={1} />
              {!same && <circle cx={hx} cy={sy(at(today, hover))} r={4.5} fill="var(--pv-slip)" stroke="var(--pv-ink)" strokeWidth={1.6} />}
              <circle cx={hx} cy={sy(at(plan, hover))} r={5.5} fill="var(--pv-slip)" stroke={planInk} strokeWidth={2.2} />
            </g>
          )}
        </svg>
        {hover !== null && (
          <div role="tooltip" className="pv-tip" style={{ left: clamp(hx, 110, Math.max(110, W - 110)), top: 4, transform: 'translateX(-50%)' }}>
            <div className="pv-tip-head">At age {hover}</div>
            <SlipRow line={same ? 'ink' : 'plan'}>
              <b>{money(at(plan, hover))}</b> {same ? 'saved' : 'with the plan'}
            </SlipRow>
            {!same && (
              <SlipRow line="ink">
                <b>{money(at(today, hover))}</b> on today’s path
              </SlipRow>
            )}
            <div className="pv-tip-muted">in today’s money</div>
          </div>
        )}
      </div>
    </div>
  );
}

export const projectionKey = (today: RetirementResult, plan: RetirementResult): KeyItem[] => {
  const same = Math.abs(today.retirementAge - plan.retirementAge) < 0.01 && today.timeline.every((x, i) => Math.abs(x.capitalReal - (plan.timeline[i]?.capitalReal ?? 0)) < 1);
  const out = (r: RetirementResult) => (r.depletionAge !== null && r.depletionAge < r.planningAge ? Math.floor(r.depletionAge) : null);
  const items: KeyItem[] = same
    ? [{ label: 'savings', line: 'ink' }]
    : [
        { label: 'with the plan', line: 'plan' },
        { label: 'on today’s path', line: 'ink' },
      ];
  items.push({ label: 'capital needed', line: 'dash' });
  if (out(plan) !== null) items.push({ label: `the money runs out at about ${out(plan)}${same ? '' : ', with the plan'}`, cross: 'red' });
  if (!same && out(today) !== null) items.push({ label: `on today’s path, at about ${out(today)}`, cross: 'ink' });
  return items;
};

/* ------------------------------------------------------------------ */
/* Columns — the cost of studies, year by year                         */
/* ------------------------------------------------------------------ */

const SERIES_TONE: Tone[] = ['c1', 'c2', 'c3', 'c4'];

export function CostColumns({ series }: { series: CostSeries[] }) {
  const [view, inView] = useInView<HTMLDivElement>();
  const t = useTip();
  const years = series.flatMap((s) => s.points.map((p) => p.year));
  if (!years.length) return null;
  const y0 = Math.min(...years);
  const y1 = Math.max(...years);
  const cols = Array.from({ length: y1 - y0 + 1 }, (_, i) => {
    const year = y0 + i;
    return { year, parts: series.map((s, j) => ({ name: s.name, tone: SERIES_TONE[j % SERIES_TONE.length], cost: s.points.find((p) => p.year === year)?.cost ?? 0 })) };
  });
  const top = Math.max(1, ...cols.map((c) => c.parts.reduce((s, p) => s + p.cost, 0)));
  const ys = niceScale(0, top, 4);
  const n = cols.length;
  const slot = 100 / n;
  const skip = n > 12 ? 3 : n > 7 ? 2 : 1;
  return (
    <div
      ref={(el) => {
        view.current = el;
        t.ref.current = el;
      }}
      className={cx('pv-chart', inView && 'is-in')}
      onPointerLeave={t.leave}
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
              <div className="pv-tip-head">In {c.year}</div>
              {c.parts
                .filter((p) => p.cost > 0.5)
                .map((p) => (
                  <SlipRow key={p.name} tone={p.tone}>
                    <b>{money(p.cost)}</b> for {p.name}
                  </SlipRow>
                ))}
            </>
          );
          return (
            <div
              key={c.year}
              className="pv-col"
              style={{ left: `calc(${i * slot}% + 3px)`, width: `calc(${slot}% - 6px)`, height: `${(total / ys.max) * 100}%`, transitionDelay: `${i * 40}ms, 0s, 0s` }}
              onPointerMove={(e) => total > 0.5 && t.show(e, content)}
              onPointerDown={(e) => total > 0.5 && t.show(e, content)}
            >
              {c.parts.map((p) => (p.cost > 0.5 ? <i key={p.name} className={`t-${p.tone}`} style={{ height: `${(p.cost / total) * 100}%` }} /> : null))}
            </div>
          );
        })}
      </div>
      <div className="relative ml-[54px] h-7 md:ml-[62px]" aria-hidden>
        {cols.map((c, i) =>
          i % skip === 0 ? (
            <span key={c.year} className="pv-xtick" style={{ left: `${(i + 0.5) * slot}%` }}>
              {c.year}
            </span>
          ) : null,
        )}
      </div>
      <Slip tip={t.tip} />
    </div>
  );
}

export const costKey = (series: CostSeries[]): KeyItem[] => (series.length > 1 ? series.map((s, j) => ({ label: s.name, tone: SERIES_TONE[j % SERIES_TONE.length] })) : []);

/* ------------------------------------------------------------------ */
/* Strips — each need as a box, filled by what is in place             */
/* ------------------------------------------------------------------ */

/** The parts of one strip, as shares of its box: in place today, added by the plan, still short. */
const stripParts = (today: number, plan: number) => {
  const a = clamp(Math.min(today, plan), 0, 1);
  const b = clamp(plan, 0, 1);
  return { today: a, plan: Math.max(0, b - a), short: Math.max(0, 1 - b) };
};

function StripTrack({ width, today, plan }: { width: number; today: number; plan: number }) {
  const p = stripParts(today, plan);
  const w = (f: number) => `${f * width * 100}%`;
  return (
    <span className="pv-strack" aria-hidden>
      <i className="pv-box" style={{ width: w(1) }} />
      {p.today > 0.002 && <i className="t-add" style={{ left: 0, width: w(p.today) }} />}
      {p.plan > 0.002 && <i className="t-plan" style={{ left: w(p.today), width: w(p.plan) }} />}
      {p.short > 0.002 && <i className="t-bad" style={{ left: w(p.today + p.plan), width: w(p.short) }} />}
    </span>
  );
}

/** Each child's studies or each goal: the box is what will be needed, filled by what is expected to be saved. */
export function NeedStrips({ rows, met }: { rows: Measure[]; met: string }) {
  const [view, inView] = useInView<HTMLDivElement>();
  const scale = Math.max(1, ...rows.map((r) => r.need));
  return (
    <div ref={view} className={cx('pv-chart pv-strips', inView && 'is-in')}>
      {rows.map((r) => {
        const status = measureStatus(r);
        const short = shortOf(r);
        const share = (v: number) => (r.need > 0.5 ? v / r.need : 1);
        return (
          <div
            key={r.key}
            className="pv-strip"
            title={`${r.label}: ${money(r.need)} needed ${r.when}; ${money(r.today)} expected today${r.plan > r.today + 0.5 ? `, ${money(r.plan)} with the plan` : ''}.`}
          >
            <span className="pv-strip-label">
              <b>{r.label}</b>
              <span>
                {moneyCompact(r.need)} needed {r.when}
              </span>
            </span>
            <StripTrack width={r.need / scale} today={share(r.today)} plan={share(r.plan)} />
            <span className={cx('pv-strip-end', status !== 'covered' && 'is-short')}>{status === 'covered' ? met : `${moneyCompact(short)} short`}</span>
          </div>
        );
      })}
    </div>
  );
}

export const STRIP_KEY: KeyItem[] = [
  { label: 'what will be needed', tone: 'box' },
  { label: 'expected to be saved', tone: 'add' },
  { label: 'added by the plan', tone: 'plan' },
  { label: 'still short', tone: 'bad' },
];

/** Every need in the session at a glance, each drawn to its own full measure. */
export function ProvisionStrips({ rows, onSelect }: { rows: FundedRow[]; onSelect?: (q: QuestionKey) => void }) {
  const [view, inView] = useInView<HTMLDivElement>();
  const groups = [...new Set(rows.map((r) => r.group))];
  return (
    <div ref={view} className={cx('pv-chart pv-strips', inView && 'is-in')}>
      {groups.map((g) => (
        <div key={g}>
          <div className="pv-strip-group">{g}</div>
          {rows
            .filter((r) => r.group === g)
            .map((r) => (
              <button
                key={r.key}
                type="button"
                className="pv-strip"
                onClick={() => onSelect?.(r.q)}
                aria-label={`${r.label}, ${r.group}: ${pct(r.today)} provided for today${Math.abs(r.plan - r.today) > 0.005 ? `, ${pct(r.plan)} with the plan` : ''}. Go to the question.`}
              >
                <span className="pv-strip-label">
                  <b>{r.label}</b>
                </span>
                <StripTrack width={1} today={r.today} plan={r.plan} />
                <span className={cx('pv-strip-end', r.plan < 0.995 && 'is-short')}>
                  {pct(r.plan)}
                  {Math.abs(r.plan - r.today) > 0.005 && <small>from {pct(r.today)}</small>}
                </span>
              </button>
            ))}
        </div>
      ))}
    </div>
  );
}

export const PROVISION_KEY: KeyItem[] = [
  { label: 'provided for today', tone: 'add' },
  { label: 'added by the plan', tone: 'plan' },
  { label: 'still short', tone: 'bad' },
];
