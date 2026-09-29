'use client';

import { AlertTriangle, CheckCircle2, Minus, OctagonAlert } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import type { RetirementYear, Status } from '@/lib/fna/analysis';
import { money, moneyCompact, pct } from '@/lib/format';
import { cx } from './ui';

/* Colours follow the validated reference palette: categorical slots in fixed
   order, status colours reserved for state and always paired with icon+label. */
const SERIES = ['var(--color-series-1)', 'var(--color-series-2)', 'var(--color-series-3)', 'var(--color-series-4)', 'var(--color-series-7)'];

const STATUS_FILL: Record<Status, { fill: string; track: string }> = {
  covered: { fill: 'var(--color-good)', track: 'var(--color-good-soft)' },
  partial: { fill: 'var(--color-warning)', track: 'var(--color-warning-soft)' },
  shortfall: { fill: 'var(--color-critical)', track: 'var(--color-critical-soft)' },
  na: { fill: 'var(--color-line-strong)', track: 'var(--color-canvas)' },
};

export function Meter({ value, total, status, height = 6 }: { value: number; total: number; status: Status; height?: number }) {
  const ratio = total > 0 ? Math.min(1, Math.max(0, value / total)) : status === 'na' ? 0 : 1;
  const s = STATUS_FILL[status];
  return (
    <div
      className="w-full overflow-hidden rounded-full"
      style={{ height, background: s.track }}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(ratio * 100)}
      aria-label={`${Math.round(ratio * 100)}% provided`}
    >
      <div className="h-full rounded-full" style={{ width: `${ratio * 100}%`, background: s.fill, minWidth: ratio > 0 ? height : 0 }} />
    </div>
  );
}

const STATUS_ICON: Record<Status, typeof CheckCircle2> = { covered: CheckCircle2, partial: AlertTriangle, shortfall: OctagonAlert, na: Minus };
const STATUS_LABEL: Record<Status, string> = { covered: 'Covered', partial: 'Partial', shortfall: 'Shortfall', na: 'No need' };

/** Need-versus-provision rows: the bar is the funded share of each need. */
export function NeedMeters({
  rows,
  compact = false,
}: {
  rows: { label: string; sub?: string; need: number; provision: number; shortfall: number; status: Status; monthly?: boolean }[];
  compact?: boolean;
}) {
  return (
    <div className={cx('divide-y divide-line', compact && 'text-[12px]')}>
      {rows.map((r) => {
        const Icon = STATUS_ICON[r.status];
        const fmt = (n: number) => (r.monthly ? `${money(n)} p.m.` : money(n));
        return (
          <div key={r.label + (r.sub ?? '')} className={cx('grid items-center gap-x-4 gap-y-1', compact ? 'grid-cols-[1fr_auto] py-2' : 'grid-cols-1 py-3 sm:grid-cols-[minmax(0,14rem)_1fr_auto]')}>
            <div className="min-w-0">
              <div className="truncate font-medium text-ink">{r.label}</div>
              {r.sub && <div className="truncate text-xs text-muted">{r.sub}</div>}
            </div>
            <div className={cx(compact ? 'col-span-2 row-start-2' : '')}>
              <Meter value={r.provision} total={r.need} status={r.status} height={compact ? 6 : 8} />
              <div className="mt-1 flex justify-between text-[11px] text-muted tabular">
                <span>Provided {fmt(r.provision)}</span>
                <span>Need {fmt(r.need)}</span>
              </div>
            </div>
            <div className={cx('flex items-center gap-1.5 text-right', compact ? 'col-start-2 row-start-1 justify-end' : 'justify-end')}>
              <Icon size={14} aria-hidden style={{ color: STATUS_FILL[r.status].fill }} />
              <span className="text-[13px] font-semibold text-ink tabular">
                {r.shortfall > 0.5 ? `−${r.monthly ? money(r.shortfall) : moneyCompact(r.shortfall)}` : STATUS_LABEL[r.status]}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Simple single-series horizontal bars with the value at the tip. */
export function BarList({ items, format = money, color = SERIES[0] }: { items: { label: string; value: number }[]; format?: (n: number) => string; color?: string }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div className="space-y-2">
      {items.map((i) => (
        <div key={i.label} className="grid grid-cols-[minmax(0,11rem)_1fr] items-center gap-3 text-[13px]" title={`${i.label}: ${format(i.value)}`}>
          <span className="truncate text-ink-2">{i.label}</span>
          <div className="flex items-center gap-2">
            <div className="h-3.5 rounded-r-[4px]" style={{ width: `${Math.max(0.5, (i.value / max) * 78)}%`, background: color }} />
            <span className="shrink-0 text-xs font-medium text-ink tabular">{format(i.value)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

/** One stacked bar showing where take-home pay goes, with a legend. */
export function AllocationBar({ segments, total }: { segments: { label: string; value: number }[]; total: number }) {
  const positive = segments.filter((s) => s.value > 0);
  const sumValues = positive.reduce((a, s) => a + s.value, 0);
  const base = Math.max(total, sumValues, 1);
  return (
    <div>
      <div className="flex h-5 w-full gap-[2px] overflow-hidden rounded-[4px] bg-canvas">
        {positive.map((s, i) => (
          <div
            key={s.label}
            className="h-full first:rounded-l-[4px] last:rounded-r-[4px]"
            style={{ width: `${(s.value / base) * 100}%`, background: SERIES[i % SERIES.length] }}
            title={`${s.label}: ${money(s.value)} (${pct(s.value / base)})`}
          />
        ))}
      </div>
      <ul className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-2">
        {positive.map((s, i) => (
          <li key={s.label} className="flex items-center justify-between gap-3">
            <span className="flex min-w-0 items-center gap-2 text-ink-2">
              <span className="h-2.5 w-2.5 shrink-0 rounded-[3px]" style={{ background: SERIES[i % SERIES.length] }} aria-hidden />
              <span className="truncate">{s.label}</span>
            </span>
            <span className="tabular text-ink">
              {money(s.value)} <span className="text-muted">· {pct(s.value / base)}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const niceMax = (v: number) => {
  if (v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / exp;
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return nice * exp;
};

/**
 * Retirement capital (in today's money) from now to the planning age,
 * with the capital required at retirement marked for comparison.
 */
export function RetirementChart({
  timeline,
  retirementAge,
  requiredReal,
  interactive = true,
  height = 240,
  viewWidth = 640,
}: {
  timeline: RetirementYear[];
  retirementAge: number;
  requiredReal: number;
  interactive?: boolean;
  height?: number;
  /** SVG coordinate width; smaller values render larger text when the chart is narrow. */
  viewWidth?: number;
}) {
  const W = viewWidth;
  const H = height;
  const m = { l: 58, r: 18, t: 18, b: 30 };
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);

  const data = useMemo(() => {
    // One point per age (keep the last entry for each age).
    const byAge = new Map<number, RetirementYear>();
    for (const p of timeline) byAge.set(p.age, p);
    return [...byAge.values()].sort((a, b) => a.age - b.age);
  }, [timeline]);

  if (data.length < 2) return <div className="py-10 text-center text-sm text-muted">Not enough information to project.</div>;

  const x0 = data[0].age;
  const x1 = data[data.length - 1].age;
  const yMax = niceMax(Math.max(requiredReal, ...data.map((d) => d.capitalReal)) * 1.05);
  const sx = (age: number) => m.l + ((age - x0) / Math.max(1, x1 - x0)) * (W - m.l - m.r);
  const sy = (v: number) => H - m.b - (v / yMax) * (H - m.t - m.b);
  const line = data.map((d, i) => `${i ? 'L' : 'M'}${sx(d.age).toFixed(1)},${sy(d.capitalReal).toFixed(1)}`).join('');
  const area = `${line}L${sx(x1).toFixed(1)},${sy(0)}L${sx(x0).toFixed(1)},${sy(0)}Z`;
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * yMax);
  const step = x1 - x0 > 40 || W < 500 ? 10 : 5;
  const xTicks: number[] = [];
  for (let a = Math.ceil(x0 / step) * step; a <= x1; a += step) xTicks.push(a);
  const retX = sx(Math.min(Math.max(retirementAge, x0), x1));
  const hovered = hover !== null ? data[hover] : null;

  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const rect = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const age = x0 + ((px - m.l) / (W - m.l - m.r)) * (x1 - x0);
    let best = 0;
    for (let i = 0; i < data.length; i++) if (Math.abs(data[i].age - age) < Math.abs(data[best].age - age)) best = i;
    setHover(best);
  };

  return (
    <div ref={ref} className="relative">
      <div className="mb-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-ink-2">
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded" style={{ background: SERIES[0] }} aria-hidden /> Projected capital (today’s money)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: SERIES[1] }} aria-hidden /> Capital needed at retirement
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Retirement capital projection">
        {yTicks.map((t) => (
          <g key={t}>
            <line x1={m.l} x2={W - m.r} y1={sy(t)} y2={sy(t)} stroke="var(--color-grid)" strokeWidth={1} />
            <text x={m.l - 8} y={sy(t)} dy="0.32em" textAnchor="end" fontSize={10.5} fill="var(--color-muted)" className="tabular">
              {moneyCompact(t)}
            </text>
          </g>
        ))}
        {xTicks.map((a) => (
          <text key={a} x={sx(a)} y={H - m.b + 18} textAnchor="middle" fontSize={10.5} fill="var(--color-muted)" className="tabular">
            {a}
          </text>
        ))}
        <line x1={m.l} x2={W - m.r} y1={sy(0)} y2={sy(0)} stroke="var(--color-axis)" strokeWidth={1} />
        <line x1={retX} x2={retX} y1={m.t} y2={sy(0)} stroke="var(--color-axis)" strokeWidth={1} />
        <text x={retX + 5} y={m.t + 9} fontSize={10.5} fill="var(--color-ink-2)">
          Retire at {retirementAge}
        </text>
        <path d={area} fill={SERIES[0]} fillOpacity={0.1} />
        <path d={line} fill="none" stroke={SERIES[0]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {requiredReal > 0 && (
          <circle cx={retX} cy={sy(requiredReal)} r={5} fill={SERIES[1]} stroke="var(--color-surface)" strokeWidth={2} />
        )}
        {hovered && (
          <g pointerEvents="none">
            <line x1={sx(hovered.age)} x2={sx(hovered.age)} y1={m.t} y2={sy(0)} stroke="var(--color-ink-2)" strokeWidth={1} />
            <circle cx={sx(hovered.age)} cy={sy(hovered.capitalReal)} r={4.5} fill={SERIES[0]} stroke="var(--color-surface)" strokeWidth={2} />
          </g>
        )}
        {interactive && (
          <rect x={m.l} y={m.t} width={W - m.l - m.r} height={H - m.t - m.b} fill="transparent" onMouseMove={onMove} onMouseLeave={() => setHover(null)} />
        )}
      </svg>
      {interactive && hovered && (
        <div
          className="pointer-events-none absolute z-10 rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg"
          style={{ left: `${(sx(hovered.age) / W) * 100}%`, top: 24, transform: sx(hovered.age) > W * 0.6 ? 'translateX(calc(-100% - 12px))' : 'translateX(12px)' }}
        >
          <div className="font-semibold text-ink">
            Age {hovered.age} · {hovered.year}
          </div>
          <div className="mt-1 text-ink-2 tabular">Capital today’s money: {money(hovered.capitalReal)}</div>
          <div className="text-muted tabular">Nominal: {money(hovered.capital)}</div>
          {hovered.withdrawal > 0 && <div className="text-muted tabular">Income drawn: {money(hovered.withdrawal / 12)} p.m.</div>}
          {hovered.contribution > 0 && <div className="text-muted tabular">Contributions: {money(hovered.contribution / 12)} p.m.</div>}
        </div>
      )}
    </div>
  );
}
