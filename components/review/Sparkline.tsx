'use client';

/**
 * The shape of a fact's value, small enough to sit beside its phrase in the popover (§11.1):
 * a density for ranges, bars for weighted options and point values, a meter for yes / no, and
 * bars per label for categories. The median is marked in ink; the value before the call, when
 * there was one, is a dashed line, so the adviser sees where the old figure sits.
 */
import { categoricalProbs, isBoolean, isCategorical, median, pTrue, pyPercent, quantile, readable, shape } from '@/lib/review/dist';
import type { Quantity } from '@/lib/review/types';

const W = 232;
const H = 40;
const PAD = 4;
const SERIES = 'var(--color-series-1)';

export default function Sparkline({ value, prior, label }: { value: Quantity; prior?: number | null; label: string }) {
  const d = value.dist;

  if (isBoolean(d)) {
    const p = pTrue(d);
    return (
      <div role="img" aria-label={`${label}: ${pyPercent(p)} likely`} className="w-[232px]">
        <div className="h-2 overflow-hidden rounded-full bg-brand-soft">
          <div className="h-full rounded-full" style={{ width: `${Math.max(2, p * 100)}%`, background: SERIES }} />
        </div>
        <div className="mt-0.5 flex justify-between text-[10px] text-muted tabular">
          <span>{pyPercent(p)} yes</span>
          <span>{pyPercent(1 - p)} no</span>
        </div>
      </div>
    );
  }

  if (isCategorical(d)) {
    const probs = Object.entries(categoricalProbs(d))
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4);
    return (
      <div role="img" aria-label={`${label}: ${probs.map(([k, p]) => `${k} ${pyPercent(p)}`).join(', ')}`} className="w-[232px] space-y-0.5">
        {probs.map(([k, p]) => (
          <div key={k} className="grid grid-cols-[6.5rem_1fr_2.5rem] items-center gap-1.5 text-[10.5px]">
            <span className="truncate text-ink-2" title={k}>
              {k}
            </span>
            <span className="h-1.5 rounded-r-[3px]" style={{ width: `${Math.max(3, p * 100)}%`, background: SERIES }} />
            <span className="text-right text-muted tabular">{pyPercent(p)}</span>
          </div>
        ))}
      </div>
    );
  }

  const s = shape(d);
  const x = (v: number) => PAD + ((v - s.lo) / (s.hi - s.lo || 1)) * (W - 2 * PAD);
  const maxY = Math.max(...s.ys, 0);
  const y = (v: number) => H - PAD - (maxY > 0 ? (v / maxY) * (H - 2 * PAD - 6) : 0);
  const area = maxY > 0 ? `M${x(s.xs[0])},${H - PAD} ${s.xs.map((v, i) => `L${x(v).toFixed(1)},${y(s.ys[i]).toFixed(1)}`).join(' ')} L${x(s.xs[s.xs.length - 1])},${H - PAD} Z` : '';
  const maxSpike = Math.max(...s.spikes.map((k) => k.weight), 0);
  const p10 = quantile(d, 0.1);
  const p50 = median(d);
  const p90 = quantile(d, 0.9);
  // Ages, years and counts read whole ("62", not "62.2614"): the figure is a quantile, not a statement.
  const fmt = (v: number) => readable(value, v);
  const showPrior = prior !== null && prior !== undefined && Number.isFinite(prior) && prior >= s.lo && prior <= s.hi;

  return (
    <figure className="m-0 w-[232px]">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label}: most likely ${fmt(p50)}, between ${fmt(p10)} and ${fmt(p90)} eight times in ten`} className="block overflow-visible">
        <title>{`Most likely ${fmt(p50)}; 80% between ${fmt(p10)} and ${fmt(p90)}`}</title>
        <line x1={PAD} x2={W - PAD} y1={H - PAD} y2={H - PAD} stroke="var(--color-axis)" strokeWidth={1} />
        <rect x={x(p10)} y={H - PAD - 1.5} width={Math.max(2, x(p90) - x(p10))} height={3} rx={1.5} fill={SERIES} opacity={0.35} />
        {area && <path d={area} fill={SERIES} fillOpacity={0.18} stroke={SERIES} strokeWidth={1.5} strokeLinejoin="round" />}
        {s.spikes.map((k) => (
          <rect key={k.x} x={x(k.x) - 2} width={4} rx={2} y={H - PAD - (k.weight / (maxSpike || 1)) * (H - 2 * PAD - 6)} height={(k.weight / (maxSpike || 1)) * (H - 2 * PAD - 6)} fill={SERIES}>
            <title>{`${fmt(k.x)}: ${pyPercent(k.weight)}`}</title>
          </rect>
        ))}
        {showPrior && <line x1={x(prior as number)} x2={x(prior as number)} y1={PAD} y2={H - PAD} stroke="var(--color-muted)" strokeWidth={1} strokeDasharray="2 2" />}
        <line x1={x(p50)} x2={x(p50)} y1={PAD - 1} y2={H - PAD + 2} stroke="var(--color-ink)" strokeWidth={1.5} />
      </svg>
      <figcaption className="mt-0.5 flex justify-between text-[10px] text-muted tabular">
        <span>{fmt(p10)}</span>
        <span className="font-medium text-ink-2">{fmt(p50)}</span>
        <span>{fmt(p90)}</span>
      </figcaption>
      {showPrior && <div className="text-[10px] text-muted">dashed: the value before the call</div>}
    </figure>
  );
}
