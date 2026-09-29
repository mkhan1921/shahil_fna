import { AlertTriangle, CheckCircle2, Minus, OctagonAlert } from 'lucide-react';
import type { ReactNode } from 'react';
import type { Line, Status } from '@/lib/fna/analysis';
import type { RiskCategory } from '@/lib/fna/riskProfile';
import { RISK_CATEGORIES } from '@/lib/fna/riskProfile';
import { money, moneyCompact, pct } from '@/lib/format';
import { cx } from '../ui';

/*
 * Print-first charts for the client report. Every chart:
 * - is plain HTML/SVG, so it prints crisply on A4 and keeps its colours (print-color-adjust);
 * - pairs colour with an icon, label or pattern, so it reads in greyscale and for colour-blind readers;
 * - sits beside the table that carries the exact figures, so the record of advice never depends on a picture;
 * - carries an aria-label summarising what it shows.
 */

/* Categorical slots in the validated order; green and red are kept for status. */
const PALETTE = [
  'var(--color-series-1)',
  'var(--color-series-2)',
  'var(--color-series-3)',
  'var(--color-series-4)',
  'var(--color-series-7)',
  'var(--color-series-5)',
  'var(--color-brand-2)',
  'var(--color-faint)',
];

const STATUS_FILL: Record<Status, string> = {
  covered: 'var(--color-good)',
  partial: 'var(--color-warning)',
  shortfall: 'var(--color-critical)',
  na: 'var(--color-line-strong)',
};
const STATUS_INK: Record<Status, string> = {
  covered: 'var(--color-good-ink)',
  partial: 'var(--color-warning-ink)',
  shortfall: 'var(--color-critical-ink)',
  na: 'var(--color-muted)',
};
const STATUS_ICON: Record<Status, typeof CheckCircle2> = { covered: CheckCircle2, partial: AlertTriangle, shortfall: OctagonAlert, na: Minus };
export const STATUS_SHORT: Record<Status, string> = { covered: 'On track', partial: 'Partly funded', shortfall: 'Shortfall', na: 'No need' };

/** Diagonal hatching marks a gap, so it reads without colour. */
const SHORTFALL_BG = 'repeating-linear-gradient(135deg, var(--color-critical-soft) 0 3px, var(--color-critical) 3px 4.5px)';

const baseLabel = (l: string) => l.replace(/\s*\([^)]*\)\s*$/, '').trim();

/** Keep the largest positive lines and fold the rest into “Other”, for a legible stack. */
export function topSegments(lines: Line[], max = 5, other = 'Other costs'): Seg[] {
  const positive = lines.filter((l) => l.amount > 0.5).map((l) => ({ label: baseLabel(l.label), value: l.amount }));
  if (positive.length <= max) return positive;
  const sorted = [...positive].sort((a, b) => b.value - a.value);
  const keep = new Set(sorted.slice(0, max - 1).map((s) => s.label));
  const rest = positive.filter((s) => !keep.has(s.label)).reduce((t, s) => t + s.value, 0);
  return [...positive.filter((s) => keep.has(s.label)), { label: other, value: rest }];
}

/* ------------------------------------------------------------------ */

/** A numbered figure with a title, the chart, and a caption naming its source and basis. */
export function Figure({ number, title, caption, children, className }: { number?: string; title: ReactNode; caption?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <figure className={cx('avoid-break', className)}>
      <div className="mb-2 flex items-baseline gap-2">
        {number && <span className="text-[9.5px] font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--report-brand)' }}>Figure {number}</span>}
        <span className="text-[11.5px] font-semibold text-ink">{title}</span>
      </div>
      {children}
      {caption && <figcaption className="mt-1.5 text-[9.5px] leading-[1.45] text-muted">{caption}</figcaption>}
    </figure>
  );
}

function Legend({ items }: { items: { label: string; color?: string; hatch?: boolean }[] }) {
  return (
    <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-ink-2">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 shrink-0 rounded-[2px]" style={{ background: i.hatch ? SHORTFALL_BG : i.color }} aria-hidden />
          {i.label}
        </li>
      ))}
    </ul>
  );
}

/** Explains the status icons in words, so the chart reads in black and white. */
function StatusKey({ note }: { note?: string }) {
  const items: [Status, string][] = [
    ['covered', 'on track (95%+)'],
    ['partial', 'partly funded (60–95%)'],
    ['shortfall', 'shortfall (under 60%)'],
  ];
  return (
    <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[9px] text-muted">
      {note && <span>{note}</span>}
      {items.map(([s, text]) => {
        const Icon = STATUS_ICON[s];
        return (
          <span key={s} className="inline-flex items-center gap-1">
            <Icon size={10} aria-hidden style={{ color: STATUS_INK[s] }} />
            {text}
          </span>
        );
      })}
    </p>
  );
}

/* ------------------------------------------------------------------ */

/** Count of needs by status, each with its icon — the one-line verdict of the summary. */
export function StatusTally({ statuses }: { statuses: Status[] }) {
  const order: Status[] = ['covered', 'partial', 'shortfall'];
  const counts = order.map((s) => ({ s, n: statuses.filter((x) => x === s).length }));
  const total = counts.reduce((t, c) => t + c.n, 0);
  return (
    <div className="flex items-stretch gap-2" role="img" aria-label={counts.map((c) => `${c.n} ${STATUS_SHORT[c.s].toLowerCase()}`).join(', ')}>
      {counts.map(({ s, n }) => {
        const Icon = STATUS_ICON[s];
        return (
          <div key={s} className="flex flex-1 items-center gap-2.5 rounded-md border px-3 py-2" style={{ borderColor: STATUS_FILL[s], background: `color-mix(in srgb, ${STATUS_FILL[s]} 9%, white)` }}>
            <Icon size={18} aria-hidden style={{ color: STATUS_INK[s] }} />
            <div>
              <div className="text-[18px] font-semibold leading-none tabular" style={{ color: STATUS_INK[s] }}>
                {n}
                <span className="text-[10px] font-normal text-muted"> of {total}</span>
              </div>
              <div className="mt-0.5 text-[10px] font-medium text-ink-2">{STATUS_SHORT[s]}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */

export interface FundedRow {
  label: string;
  sub?: string;
  funded: number;
  status: Status;
  /** Short text at the end of the bar, e.g. “R1.2m short”. */
  detail?: string;
}

/** Share of each need already provided for, against a 100% line. */
export function FundedBars({ rows }: { rows: FundedRow[] }) {
  const visible = rows.filter((r) => r.status !== 'na');
  if (!visible.length) return <p className="text-[10.5px] text-muted">No needs were identified.</p>;
  return (
    <div role="img" aria-label={visible.map((r) => `${r.label}${r.sub ? ` (${r.sub})` : ''}: ${pct(r.funded)} provided, ${STATUS_SHORT[r.status]}`).join('; ')}>
      <div className="grid grid-cols-[minmax(0,10.75rem)_1fr_minmax(0,9.25rem)] items-end gap-x-3 pb-1 text-[9px] text-muted">
        <span />
        <div className="relative h-3 tabular">
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <span key={t} className="absolute -translate-x-1/2" style={{ left: `${t * 100}%` }}>
              {pct(t)}
            </span>
          ))}
        </div>
        <span />
      </div>
      <div className="space-y-1.5">
        {visible.map((r) => {
          const Icon = STATUS_ICON[r.status];
          return (
            <div key={r.label + (r.sub ?? '')} className="grid grid-cols-[minmax(0,10.75rem)_1fr_minmax(0,9.25rem)] items-center gap-x-3 text-[10.5px]">
              <span className="truncate text-ink-2">
                {r.label}
                {r.sub && <span className="text-muted"> · {r.sub}</span>}
              </span>
              <div className="relative h-3.5 rounded-[3px] bg-canvas">
                {[0.25, 0.5, 0.75].map((t) => (
                  <span key={t} className="absolute inset-y-0 w-px bg-white" style={{ left: `${t * 100}%` }} aria-hidden />
                ))}
                <div className="absolute inset-y-0 left-0 rounded-[3px]" style={{ width: `${Math.max(r.funded > 0 ? 1.5 : 0, Math.min(1, r.funded) * 100)}%`, background: STATUS_FILL[r.status] }} />
                <span className="absolute -inset-y-0.5 right-0 w-[2px] bg-ink" aria-hidden />
              </div>
              <span className="flex items-center gap-1.5 tabular">
                <Icon size={12} aria-hidden style={{ color: STATUS_INK[r.status] }} />
                <span className="font-semibold" style={{ color: STATUS_INK[r.status] }}>
                  {pct(r.funded)}
                </span>
                {r.detail && <span className="truncate text-muted">{r.detail}</span>}
              </span>
            </div>
          );
        })}
      </div>
      <StatusKey note="The dark line marks 100% of the need." />
    </div>
  );
}

/* ------------------------------------------------------------------ */

export interface Seg {
  label: string;
  value: number;
  /** A gap to be closed: drawn hatched in red, labelled “Shortfall”. */
  shortfall?: boolean;
}

export interface StackBar {
  label: string;
  sub?: string;
  segments: Seg[];
  /** Text after the bar; defaults to the bar’s total. */
  total?: ReactNode;
}

/**
 * Horizontal stacked bars on one shared scale, so bars can be compared by
 * length. Segment colours are keyed by label, so the same component keeps the
 * same colour across bars and people.
 */
export function StackedBars({ bars, format = moneyCompact, legend = true }: { bars: StackBar[]; format?: (n: number) => string; legend?: boolean }) {
  const colorOf = new Map<string, string>();
  for (const b of bars) for (const s of b.segments) if (!s.shortfall && s.value > 0 && !colorOf.has(s.label)) colorOf.set(s.label, PALETTE[colorOf.size % PALETTE.length]);
  const sumOf = (b: StackBar) => b.segments.reduce((t, s) => t + Math.max(0, s.value), 0);
  const max = Math.max(1, ...bars.map(sumOf));
  const hasShortfall = bars.some((b) => b.segments.some((s) => s.shortfall && s.value > 0));
  const describe = (b: StackBar) =>
    `${b.label}${b.sub ? ` ${b.sub}` : ''}: ${b.segments
      .filter((s) => s.value > 0)
      .map((s) => `${s.shortfall ? 'shortfall' : s.label} ${money(s.value)}`)
      .join(', ')}`;
  return (
    <div role="img" aria-label={bars.map(describe).join('; ')}>
      <div className="space-y-1.5">
        {bars.map((b) => {
          const total = sumOf(b);
          return (
            <div key={b.label + (b.sub ?? '')} className="grid grid-cols-[minmax(0,8.5rem)_1fr_minmax(0,4.75rem)] items-center gap-x-3 text-[10.5px]">
              <span className="truncate text-ink-2">
                {b.label}
                {b.sub && <span className="text-muted"> · {b.sub}</span>}
              </span>
              <div className="flex h-4 gap-px">
                {b.segments
                  .filter((s) => s.value > 0)
                  .map((s, i, arr) => (
                    <div
                      key={s.label + i}
                      className={cx('h-full', i === 0 && 'rounded-l-[3px]', i === arr.length - 1 && 'rounded-r-[3px]')}
                      style={{ width: `${(s.value / max) * 100}%`, background: s.shortfall ? SHORTFALL_BG : colorOf.get(s.label) }}
                      title={`${s.shortfall ? 'Shortfall' : s.label}: ${money(s.value)}`}
                    />
                  ))}
                {total <= 0 && <span className="text-[10px] text-muted">None</span>}
              </div>
              <span className="text-right font-semibold text-ink tabular">{b.total ?? format(total)}</span>
            </div>
          );
        })}
      </div>
      {legend && <Legend items={[...[...colorOf].map(([label, color]) => ({ label, color })), ...(hasShortfall ? [{ label: 'Shortfall', hatch: true }] : [])]} />}
    </div>
  );
}

/* ------------------------------------------------------------------ */

export interface Gauge {
  label: string;
  value: number;
  display: string;
  /** Guideline band, in the same units as value. */
  good: { min?: number; max?: number };
  guide: string;
  scaleMax: number;
}

/** Each ratio on its own scale with the guideline band shaded and a marker for the client. */
export function RatioGauges({ items }: { items: Gauge[] }) {
  return (
    <div className="grid grid-cols-1 gap-y-2" role="img" aria-label={items.map((g) => `${g.label} ${g.display}, guideline ${g.guide}`).join('; ')}>
      {items.map((g) => {
        const lo = g.good.min ?? 0;
        const hi = g.good.max ?? g.scaleMax;
        const within = g.value >= lo && g.value <= hi;
        const pos = Math.min(1, Math.max(0, g.value / g.scaleMax));
        const Icon = within ? CheckCircle2 : AlertTriangle;
        const tone = within ? 'var(--color-good-ink)' : 'var(--color-warning-ink)';
        return (
          <div key={g.label} className="text-[10.5px]">
            <div className="mb-0.5 flex items-baseline justify-between gap-3">
              <span className="text-ink-2">{g.label}</span>
              <span className="flex items-center gap-1.5 tabular">
                <Icon size={12} aria-hidden style={{ color: tone }} className="self-center" />
                <span className="font-semibold text-ink">{g.display}</span>
                <span className="text-muted">guide {g.guide}</span>
              </span>
            </div>
            <div className="relative h-3">
              <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-canvas" />
              <div
                className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full"
                style={{ left: `${(lo / g.scaleMax) * 100}%`, width: `${((Math.min(hi, g.scaleMax) - lo) / g.scaleMax) * 100}%`, background: 'var(--color-good-soft)', boxShadow: 'inset 0 0 0 1px var(--color-good)' }}
              />
              <div className="absolute top-0 h-3 w-3 -translate-x-1/2 rounded-full border-2 border-white" style={{ left: `${pos * 100}%`, background: 'var(--color-ink)' }} />
            </div>
          </div>
        );
      })}
      <p className="flex flex-wrap items-center gap-x-3 text-[9px] text-muted">
        <span className="inline-flex items-center gap-1">
          <span className="h-1.5 w-3 rounded-full" style={{ background: 'var(--color-good-soft)', boxShadow: 'inset 0 0 0 1px var(--color-good)' }} aria-hidden /> guideline range
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-ink" aria-hidden /> your position
        </span>
        <span className="inline-flex items-center gap-1">
          <CheckCircle2 size={10} aria-hidden style={{ color: 'var(--color-good-ink)' }} /> within guideline
        </span>
        <span className="inline-flex items-center gap-1">
          <AlertTriangle size={10} aria-hidden style={{ color: 'var(--color-warning-ink)' }} /> outside guideline
        </span>
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */

/** Monthly cost per year as stacked columns, one colour per recommendation. */
export function CostColumns({ series, years = 20 }: { series: { label: string; monthly: number; escalation: number }[]; years?: number }) {
  const W = 640;
  const H = 170;
  const m = { l: 52, r: 8, t: 12, b: 24 };
  const ys = Array.from({ length: years }, (_, i) => i + 1);
  const at = (s: (typeof series)[number], y: number) => s.monthly * Math.pow(1 + s.escalation, y - 1);
  const totals = ys.map((y) => series.reduce((t, s) => t + at(s, y), 0));
  const rawMax = Math.max(1, ...totals);
  const exp = Math.pow(10, Math.floor(Math.log10(rawMax)));
  const yMax = [1, 2, 2.5, 5, 10].map((f) => f * exp).find((v) => v >= rawMax * 1.08) ?? 10 * exp;
  const bw = (W - m.l - m.r) / years;
  const sy = (v: number) => H - m.b - (v / yMax) * (H - m.t - m.b);
  const labelled = new Set([1, 5, 10, 15, 20]);
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`Total monthly cost rises from ${money(totals[0])} in year 1 to ${money(totals[years - 1])} in year ${years}`}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={m.l} x2={W - m.r} y1={sy(f * yMax)} y2={sy(f * yMax)} stroke="var(--color-grid)" />
            <text x={m.l - 6} y={sy(f * yMax)} dy="0.32em" textAnchor="end" fontSize={10} fill="var(--color-muted)">
              {money(f * yMax)}
            </text>
          </g>
        ))}
        {ys.map((y, i) => {
          let base = 0;
          return (
            <g key={y}>
              {series.map((s, si) => {
                const v = at(s, y);
                const y0 = sy(base + v);
                const h = sy(base) - y0;
                base += v;
                return <rect key={si} x={m.l + i * bw + 2} y={y0} width={bw - 4} height={Math.max(0, h - 0.5)} fill={PALETTE[si % PALETTE.length]} />;
              })}
              {labelled.has(y) && (
                <text x={m.l + i * bw + bw / 2} y={sy(totals[i]) - 3} textAnchor="middle" fontSize={9.5} fontWeight={600} fill="var(--color-ink)">
                  {moneyCompact(totals[i])}
                </text>
              )}
              <text x={m.l + i * bw + bw / 2} y={H - m.b + 14} textAnchor="middle" fontSize={9.5} fill={labelled.has(y) ? 'var(--color-ink-2)' : 'var(--color-faint)'}>
                {y}
              </text>
            </g>
          );
        })}
        <line x1={m.l} x2={W - m.r} y1={sy(0)} y2={sy(0)} stroke="var(--color-axis)" />
      </svg>
      <Legend items={series.map((s, i) => ({ label: s.label, color: PALETTE[i % PALETTE.length] }))} />
    </div>
  );
}

/* ------------------------------------------------------------------ */

/** The five risk categories as a scale, with the recommended profile and its two inputs marked. */
export function RiskScale({ profile, tolerance, capacity }: { profile: RiskCategory; tolerance: RiskCategory | null; capacity: RiskCategory | null }) {
  const idx = (c: RiskCategory | null) => (c ? RISK_CATEGORIES.findIndex((x) => x.key === c.key) : -1);
  const p = idx(profile);
  const marks = [
    { i: idx(tolerance), label: 'Tolerance' },
    { i: idx(capacity), label: 'Capacity' },
  ].filter((mk) => mk.i >= 0);
  return (
    <div role="img" aria-label={`Recommended profile ${profile.label}, on a scale from conservative to aggressive${marks.map((mk) => `; ${mk.label.toLowerCase()} ${RISK_CATEGORIES[mk.i].label}`).join('')}`}>
      <div className="grid grid-cols-5 gap-1">
        {RISK_CATEGORIES.map((c, i) => (
          <div
            key={c.key}
            className={cx('rounded-[3px] px-1.5 py-1.5 text-center text-[9.5px] leading-tight', i === p ? 'font-semibold text-white' : 'text-ink-2')}
            style={{ background: i === p ? 'var(--report-brand)' : `color-mix(in srgb, var(--color-brand-2) ${8 + i * 6}%, white)` }}
          >
            {c.label}
            <div className={cx('mt-0.5 text-[8.5px] font-normal', i === p ? 'text-white/85' : 'text-muted')}>{c.equity.replace(' growth assets', '')}</div>
          </div>
        ))}
      </div>
      <div className="relative mt-1 h-4 text-[9px] text-ink-2">
        {marks.map((mk, j) => (
          <span key={mk.label} className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: `${mk.i * 20 + 10 + (marks.length > 1 && marks[0].i === marks[1].i ? (j ? 4 : -4) : 0)}%` }}>
            ▲ {mk.label}
          </span>
        ))}
      </div>
      <div className="flex justify-between text-[9px] text-muted">
        <span>← Lower risk, lower expected return</span>
        <span>Higher risk, higher expected return →</span>
      </div>
    </div>
  );
}
