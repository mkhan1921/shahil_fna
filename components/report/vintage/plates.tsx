import type { ReactNode } from 'react';
import type { RetirementResult, Status } from '@/lib/fna/analysis';
import { money, moneyCompact, pct } from '@/lib/format';
import { GRID_MAJOR, GRID_MINOR, INK, INK_2, INK_3, PAPER, RED, RULE_FAINT, ruled, ruledRect, seedOf, Swatch, ToneCircle, ToneRect, TONES, type Tone } from './ink';

/*
 * The engraved plates of the vintage report. Each is a plain SVG drawn on a
 * ~1 unit = 1 px grid, in two inks (sepia and red), with hatching and
 * stippling in place of colour so that every plate reads in black and white.
 * Every plate carries an aria-label with its figures in words.
 */

const sum = (xs: number[]) => xs.reduce((t, x) => t + x, 0);
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Break a label into lines of at most `max` characters, at word boundaries. */
export function wrap(s: string, max: number): string[] {
  const lines: string[] = [];
  let cur = '';
  for (const w of s.split(/\s+/).filter(Boolean)) {
    if (!cur) cur = w;
    else if (`${cur} ${w}`.length <= max) cur += ` ${w}`;
    else {
      lines.push(cur);
      cur = w;
    }
  }
  if (cur) lines.push(cur);
  return lines;
}

/** Spread label blocks (centre y, height h) so none overlap, staying near their targets. */
export function spread(items: { y: number; h: number }[], top: number, bottom: number): number[] {
  const order = items.map((it, i) => ({ ...it, i })).sort((a, b) => a.y - b.y);
  const pos = order.map((it) => it.y - it.h / 2);
  for (let k = 1; k < order.length; k++) pos[k] = Math.max(pos[k], pos[k - 1] + order[k - 1].h);
  const last = order.length - 1;
  if (last >= 0 && pos[last] + order[last].h > bottom) {
    pos[last] = bottom - order[last].h;
    for (let k = last - 1; k >= 0; k--) pos[k] = Math.min(pos[k], pos[k + 1] - order[k].h);
  }
  if (pos.length && pos[0] < top) {
    pos[0] = top;
    for (let k = 1; k < order.length; k++) pos[k] = Math.max(pos[k], pos[k - 1] + order[k - 1].h);
  }
  const out: number[] = new Array(items.length);
  order.forEach((it, k) => (out[it.i] = pos[k] + it.h / 2));
  return out;
}

/** A key of tones, set in running text beneath a plate. */
export function Key({ items }: { items: { label: string; fill: Tone; red?: boolean; round?: boolean }[] }) {
  return (
    <ul className="vr-key">
      {items.map((k) => (
        <li key={k.label}>
          <Swatch tone={k.fill} red={k.red} round={k.round} />
          {k.label}
        </li>
      ))}
    </ul>
  );
}

/** Consistent tone per label across all bars of one plate. */
function toneMap(labels: string[]) {
  const m = new Map<string, Tone>();
  for (const l of labels) if (!m.has(l)) m.set(l, TONES[m.size % TONES.length]);
  return m;
}

function Lines({
  x,
  y,
  lines,
  size,
  lead,
  anchor = 'start',
  fill = INK,
  className,
  style,
}: {
  x: number;
  y: number;
  lines: ReactNode[];
  size: number;
  lead?: number;
  anchor?: 'start' | 'middle' | 'end';
  fill?: string;
  className?: string;
  style?: React.CSSProperties;
}) {
  const l = lead ?? size * 1.12;
  return (
    <text x={x} y={y} fontSize={size} textAnchor={anchor} fill={fill} className={className} style={style}>
      {lines.map((t, i) => (
        <tspan key={i} x={x} dy={i === 0 ? 0 : l}>
          {t}
        </tspan>
      ))}
    </text>
  );
}

/* ================================================================== */
/* Vessels — how full is each need?                                    */
/* ================================================================== */

export interface VesselDatum {
  key: string;
  label: string;
  funded: number;
  status: Status;
  have: number;
  need: number;
  short: number;
  monthly?: boolean;
}

/** One row of laboratory vessels, each filled to the share of its need already provided for. */
export function VesselRow({ id, title, sub, items }: { id: string; title: string; sub?: string; items: VesselDatum[] }) {
  const W = 640;
  const lead = 84;
  const slot = (W - lead) / 7;
  const w = 40;
  const h = 62;
  const top = 36;
  const yb = top + h;
  const full = top + 9;
  const H = yb + 44;
  const said = items.map((v) => (v.status === 'na' ? `${v.label}: no need` : `${v.label}: ${pct(v.funded)} provided (${money(v.have)} of ${money(v.need)}${v.monthly ? ' a month' : ''})`)).join('; ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="vr-svg" role="img" aria-label={`${title}. ${said}.`}>
      <text x={0} y={top + h / 2 - 4} fontSize={13} className="sc" fill={INK}>
        {title}
      </text>
      {sub && (
        <text x={0} y={top + h / 2 + 10} fontSize={9.5} fontStyle="italic" fill={INK_2}>
          {sub}
        </text>
      )}
      {items.map((v, i) => {
        const cx = lead + slot * i + slot / 2;
        const x0 = cx - w / 2;
        const x1 = cx + w / 2;
        const na = v.status === 'na';
        const level = yb - 1 - (yb - 1 - full) * clamp(v.funded, 0, 1);
        const clip = `vr-clip-${id}-${i}`;
        const inner = `M${x0 + 1},${top + 4} L${x0 + 1},${yb - 5} Q${x0 + 1},${yb - 1} ${x0 + 5},${yb - 1} L${x1 - 5},${yb - 1} Q${x1 - 1},${yb - 1} ${x1 - 1},${yb - 5} L${x1 - 1},${top + 4} Z`;
        const body = `M${x0 - 4},${top} L${x0},${top + 4} L${x0},${yb - 5} Q${x0},${yb} ${x0 + 5},${yb} L${x1 - 5},${yb} Q${x1},${yb} ${x1},${yb - 5} L${x1},${top + 4} L${x1 + 4},${top}`;
        const label = wrap(v.label, 15).slice(0, 3);
        const c = (n: number) => moneyCompact(n);
        return (
          <g key={v.key}>
            <Lines x={cx} y={top - 8 - (label.length - 1) * 10} lines={label} size={9} lead={10} anchor="middle" className="sc" fill={na ? INK_3 : INK} />
            <clipPath id={clip}>
              <path d={inner} />
            </clipPath>
            {!na && v.funded > 0.002 && (
              <g clipPath={`url(#${clip})`}>
                <ToneRect x={x0} y={level} w={w} h={yb - level} tone="liquid" />
                <path d={`M${x0},${level - 1.4} Q${cx},${level + 2.6} ${x1},${level - 1.4}`} fill="none" stroke={INK} strokeWidth={0.9} />
              </g>
            )}
            {!na && <line x1={x0 + 1} x2={x1 - 1} y1={full} y2={full} stroke={INK_3} strokeWidth={0.7} strokeDasharray="2 1.6" />}
            {[0.25, 0.5, 0.75].map((t) => {
              const y = yb - 1 - (yb - 1 - full) * t;
              return <line key={t} x1={x1 - 1} x2={x1 - (t === 0.5 ? 9 : 6)} y1={y} y2={y} stroke={na ? RULE_FAINT : INK} strokeWidth={0.6} />;
            })}
            <line x1={x0 + 5} x2={x0 + 5} y1={top + 10} y2={yb - 9} stroke={PAPER} strokeWidth={1.6} opacity={0.85} />
            <path d={body} fill="none" stroke={na ? RULE_FAINT : INK} strokeWidth={1.05} strokeLinejoin="round" strokeDasharray={na ? '3 2' : undefined} />
            <text x={cx} y={yb + 15} fontSize={13} textAnchor="middle" fontWeight={600} fill={na ? INK_3 : v.status === 'shortfall' ? RED : INK}>
              {na ? '—' : pct(v.funded)}
            </text>
            <text x={cx} y={yb + 27} fontSize={8.4} textAnchor="middle" fill={INK_2}>
              {na ? 'no need' : `${c(v.have)} of ${c(v.need)}${v.monthly ? ' pm' : ''}`}
            </text>
            {!na && (
              <text x={cx} y={yb + 38} fontSize={8.4} textAnchor="middle" fill={v.short > 0.5 ? RED : INK_2} fontStyle={v.short > 0.5 ? undefined : 'italic'}>
                {v.short > 0.5 ? `short ${c(v.short)}${v.monthly ? ' pm' : ''}` : 'fully met'}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/* ================================================================== */
/* The balance — what you own against what you owe                     */
/* ================================================================== */

export function Balance({ own, owe }: { own: { label: string; value: number }[]; owe: { label: string; value: number }[] }) {
  const W = 700;
  const H = 340;
  const cx = 350;
  const piv = { x: cx, y: 48 };
  const half = 190;
  const A = sum(own.map((o) => o.value));
  const L = sum(owe.map((o) => o.value));
  const theta = A + L > 0 ? clamp(((A - L) / (A + L)) * 10, -10, 10) : 0;
  const rad = (theta * Math.PI) / 180;
  const ends = [
    { x: piv.x - half * Math.cos(rad), y: piv.y + half * Math.sin(rad) },
    { x: piv.x + half * Math.cos(rad), y: piv.y - half * Math.sin(rad) },
  ];
  const drop = 170;
  const bw = 80;
  const scale = 112 / Math.max(A, L, 1);
  const groundY = H - 22;

  const pan = (side: 0 | 1, items: { label: string; value: number }[], title: string, total: number) => {
    const e = ends[side];
    const py = e.y + drop;
    const sorted = [...items].filter((i) => i.value > 0).sort((a, b) => b.value - a.value);
    let cursor = py;
    const blocks = sorted.map((it) => {
      const hgt = Math.max(3, it.value * scale);
      const y = cursor - hgt;
      cursor = y;
      return { ...it, y, h: hgt };
    });
    const left = e.x - bw / 2;
    const right = e.x + bw / 2;
    const labels = blocks.map((b) => wrap(b.label, 16));
    const ys = spread(
      blocks.map((b, i) => ({ y: b.y + b.h / 2, h: labels[i].length * 10 + 14 })),
      14,
      py - 4,
    );
    const lx = side === 0 ? left - 24 : right + 24;
    const tone = (i: number): Tone => (side === 1 ? 'red' : TONES[i % TONES.length]);
    return (
      <g key={side}>
        <path d={`M${e.x},${e.y} L${e.x - 52},${py}`} stroke={INK} strokeWidth={0.7} />
        <path d={`M${e.x},${e.y} L${e.x + 52},${py}`} stroke={INK} strokeWidth={0.7} />
        {blocks.map((b, i) => (
          <g key={b.label}>
            <rect x={left} y={b.y} width={bw} height={b.h} fill={PAPER} />
            <ToneRect x={left} y={b.y} w={bw} h={b.h} tone={tone(i)} />
            <path d={ruledRect(left, b.y, bw, b.h, seedOf(b.label), 0.35)} fill="none" stroke={side === 1 ? RED : INK} strokeWidth={0.9} />
            <polyline
              points={`${side === 0 ? left : right},${b.y + b.h / 2} ${side === 0 ? left - 8 : right + 8},${b.y + b.h / 2} ${side === 0 ? lx + 3 : lx - 3},${ys[i] - 3}`}
              fill="none"
              stroke={INK_2}
              strokeWidth={0.55}
            />
            <Lines x={lx} y={ys[i] - (labels[i].length * 10 + 14) / 2 + 10} lines={[...labels[i]]} size={9.3} lead={10} anchor={side === 0 ? 'end' : 'start'} />
            <text x={lx} y={ys[i] - (labels[i].length * 10 + 14) / 2 + 10 + labels[i].length * 10} fontSize={8.6} textAnchor={side === 0 ? 'end' : 'start'} fill={side === 1 ? RED : INK_2}>
              {moneyCompact(b.value)}
            </text>
          </g>
        ))}
        {!blocks.length && (
          <text x={e.x} y={py - 8} fontSize={9.5} fontStyle="italic" textAnchor="middle" fill={INK_2}>
            nothing recorded
          </text>
        )}
        <path d={`M${e.x - 58},${py} Q${e.x},${py + 20} ${e.x + 58},${py}`} fill={PAPER} stroke={INK} strokeWidth={1} />
        <path d={`M${e.x - 48.5},${py + 3} H${e.x + 48.5} M${e.x - 36.6},${py + 6} H${e.x + 36.6} M${e.x - 22.5},${py + 8.5} H${e.x + 22.5}`} stroke={INK} strokeWidth={0.6} />
        <line x1={e.x - 58} x2={e.x + 58} y1={py} y2={py} stroke={INK} strokeWidth={1} />
        <text x={e.x} y={py + 32} fontSize={11.5} textAnchor="middle" className="sc">
          {title}
        </text>
        <text x={e.x} y={py + 46} fontSize={11} textAnchor="middle" fontWeight={600} fill={side === 1 ? RED : INK}>
          {money(total)}
        </text>
      </g>
    );
  };

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="vr-svg" role="img" aria-label={`A balance: what you own, ${money(A)}, against what you owe, ${money(L)}. Net worth ${money(A - L)}.`}>
      {/* stand */}
      <path d={ruled(cx, piv.y + 6, cx, groundY - 8, 11, 0.3)} stroke={INK} strokeWidth={4} fill="none" />
      <path d={ruled(cx - 2.2, piv.y + 8, cx - 2.2, groundY - 8, 12, 0.2)} stroke={PAPER} strokeWidth={0.8} fill="none" />
      <ToneRect x={cx - 46} y={groundY - 8} w={92} h={10} tone="dense" />
      <path d={ruledRect(cx - 46, groundY - 8, 92, 10, 13, 0.3)} fill="none" stroke={INK} strokeWidth={0.9} />
      <line x1={cx - 110} x2={cx + 110} y1={groundY + 2} y2={groundY + 2} stroke={INK} strokeWidth={0.9} />
      {Array.from({ length: 22 }, (_, i) => (
        <line key={i} x1={cx - 108 + i * 10} y1={groundY + 2} x2={cx - 114 + i * 10} y2={groundY + 9} stroke={INK} strokeWidth={0.55} />
      ))}
      {/* beam */}
      <g transform={`rotate(${-theta} ${piv.x} ${piv.y})`}>
        <rect x={piv.x - half} y={piv.y - 2.5} width={2 * half} height={5} fill={PAPER} />
        <ToneRect x={piv.x - half} y={piv.y - 2.5} w={2 * half} h={5} tone="hatch" />
        <rect x={piv.x - half} y={piv.y - 2.5} width={2 * half} height={5} fill="none" stroke={INK} strokeWidth={0.9} />
        <circle cx={piv.x - half} cy={piv.y} r={3.2} fill={PAPER} stroke={INK} strokeWidth={0.9} />
        <circle cx={piv.x + half} cy={piv.y} r={3.2} fill={PAPER} stroke={INK} strokeWidth={0.9} />
        <path d={`M${piv.x},${piv.y - 18} L${piv.x - 4},${piv.y - 2} L${piv.x + 4},${piv.y - 2} Z`} fill={INK} />
      </g>
      <path d={`M${piv.x - 9},${piv.y + 12} L${piv.x},${piv.y} L${piv.x + 9},${piv.y + 12} Z`} fill={PAPER} stroke={INK} strokeWidth={1} />
      <circle cx={piv.x} cy={piv.y} r={2.4} fill={INK} />
      {pan(0, own, 'What you own', A)}
      {pan(1, owe, 'What you owe', L)}
      {/* net-worth plaque on the pillar */}
      <rect x={cx - 64} y={150} width={128} height={48} fill={PAPER} stroke={INK} strokeWidth={1} />
      <rect x={cx - 60} y={154} width={120} height={40} fill="none" stroke={INK} strokeWidth={0.5} />
      <text x={cx} y={170} fontSize={10.5} textAnchor="middle" className="sc">
        Net worth
      </text>
      <text x={cx} y={186} fontSize={13} textAnchor="middle" fontWeight={600} fill={A - L < 0 ? RED : INK}>
        {money(A - L)}
      </text>
    </svg>
  );
}

/* ================================================================== */
/* Of every R100 — a tray of a hundred coins                           */
/* ================================================================== */

export interface CoinPart {
  label: string;
  value: number;
  fill: Tone;
  red?: boolean;
  sub?: string;
}

/** Split 100 coins among the parts by the largest-remainder method. */
export function hundredths(values: number[]): number[] {
  const total = sum(values.map((v) => Math.max(0, v)));
  if (total <= 0) return values.map(() => 0);
  const raw = values.map((v) => (Math.max(0, v) / total) * 100);
  const out = raw.map(Math.floor);
  const left = 100 - sum(out);
  raw
    .map((r, i) => ({ i, rem: r - Math.floor(r) }))
    .sort((a, b) => b.rem - a.rem)
    .slice(0, left)
    .forEach(({ i }) => out[i]++);
  return out;
}

export function CoinGrid({ parts }: { parts: CoinPart[] }) {
  const shown = parts.filter((p) => p.value > 0);
  const counts = hundredths(shown.map((p) => p.value));
  const coins: number[] = counts.flatMap((n, i) => Array.from({ length: n }, () => i));
  const pitch = 22;
  const r = 9.4;
  const W = 640;
  const H = Math.max(10 * pitch + 14, shown.length * 44 + 14);
  const legendX = 268;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="vr-svg" role="img" aria-label={`Of every R100: ${shown.map((p, i) => `${counts[i]} rand to ${p.label.toLowerCase()}`).join(', ')}.`}>
      {coins.map((pi, k) => {
        const x = 12 + (k % 10) * pitch + r;
        const y = 6 + Math.floor(k / 10) * pitch + r;
        const p = shown[pi];
        return (
          <g key={k}>
            <ToneCircle cx={x} cy={y} r={r} tone={p.fill} />
            <circle cx={x} cy={y} r={r} fill="none" stroke={p.red ? RED : INK} strokeWidth={0.85} />
            <circle cx={x} cy={y} r={r - 2.3} fill="none" stroke={p.red ? RED : INK} strokeWidth={0.4} />
          </g>
        );
      })}
      {shown.map((p, i) => {
        const y = 22 + i * 44;
        return (
          <g key={p.label}>
            <ToneCircle cx={legendX + 10} cy={y} r={r} tone={p.fill} />
            <circle cx={legendX + 10} cy={y} r={r} fill="none" stroke={p.red ? RED : INK} strokeWidth={0.85} />
            <circle cx={legendX + 10} cy={y} r={r - 2.3} fill="none" stroke={p.red ? RED : INK} strokeWidth={0.4} />
            <text x={legendX + 28} y={y - 1} fontSize={12} fill={p.red ? RED : INK}>
              {p.label}
            </text>
            {p.sub && (
              <text x={legendX + 28} y={y + 13} fontSize={9.5} fill={INK_2} fontStyle="italic">
                {p.sub}
              </text>
            )}
            <text x={W - 2} y={y + 2} fontSize={17} textAnchor="end" fontWeight={600} fill={p.red ? RED : INK}>
              R{counts[i]}
            </text>
            <line x1={legendX + 28} x2={W - 2} y1={y + 21} y2={y + 21} stroke={RULE_FAINT} strokeWidth={0.6} />
          </g>
        );
      })}
    </svg>
  );
}

/* ================================================================== */
/* Strips — one bar per person, divided into its parts                 */
/* ================================================================== */

export interface StripRow {
  label: string;
  parts: { label: string; value: number; fill: Tone; red?: boolean }[];
  total?: string;
}

export function Strips({ rows }: { rows: StripRow[] }) {
  const W = 640;
  const x0 = 92;
  const span = 452;
  const rh = 46;
  const max = Math.max(1, ...rows.map((r) => sum(r.parts.map((p) => Math.max(0, p.value)))));
  return (
    <svg
      viewBox={`0 0 ${W} ${rows.length * rh + 4}`}
      className="vr-svg"
      role="img"
      aria-label={rows.map((r) => `${r.label}: ${r.parts.map((p) => `${p.label} ${money(p.value)}`).join(', ')}`).join('; ')}
    >
      {rows.map((r, ri) => {
        const y = 6 + ri * rh;
        let x = x0;
        const total = sum(r.parts.map((p) => Math.max(0, p.value)));
        return (
          <g key={r.label}>
            <text x={0} y={y + 12} fontSize={11.5} className="sc">
              {r.label}
            </text>
            {r.parts.map((p, pi) => {
              if (p.value <= 0) return null;
              const w = (p.value / max) * span;
              const sx = x;
              x += w;
              return (
                <g key={p.label}>
                  <ToneRect x={sx} y={y} w={w} h={16} tone={p.fill} />
                  <path d={ruledRect(sx, y, w, 16, seedOf(r.label + pi), 0.3)} fill="none" stroke={p.red ? RED : INK} strokeWidth={0.85} strokeDasharray={p.red ? '3 1.6' : undefined} />
                  {w > 44 && (
                    <text x={sx + w / 2} y={y + 29} fontSize={8.8} textAnchor="middle" fill={p.red ? RED : INK_2}>
                      {moneyCompact(p.value)}
                    </text>
                  )}
                </g>
              );
            })}
            <text x={x0 + (total / max) * span + 8} y={y + 12} fontSize={10.5} fontWeight={600}>
              {r.total ?? money(total)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ================================================================== */
/* Gap bars — each need as a bar: the part in place, then the gap      */
/* ================================================================== */

export interface GapRow {
  label: string;
  sub?: string;
  need: number;
  have: number;
}

export function GapBars({ rows, monthly = false }: { rows: GapRow[]; monthly?: boolean }) {
  const shown = rows.filter((r) => r.need > 0.5);
  const W = 640;
  const x0 = 162;
  const span = 352;
  const rh = 34;
  const max = Math.max(1, ...shown.map((r) => Math.max(r.need, r.have)));
  const c = (n: number) => (monthly ? money(n) : moneyCompact(n));
  if (!shown.length) return <p className="vr-quiet">No need was identified.</p>;
  return (
    <svg
      viewBox={`0 0 ${W} ${shown.length * rh + 2}`}
      className="vr-svg"
      role="img"
      aria-label={shown
        .map((r) => `${r.label}${r.sub ? ` (${r.sub})` : ''}: ${money(r.have)} in place of ${money(r.need)} needed${r.need > r.have ? `, shortfall ${money(r.need - r.have)}` : ''}`)
        .join('; ')}
    >
      {shown.map((r, i) => {
        const y = 6 + i * rh;
        const held = Math.min(r.have, r.need);
        const gap = Math.max(0, r.need - r.have);
        const wH = (held / max) * span;
        const wG = (gap / max) * span;
        const wN = (r.need / max) * span;
        return (
          <g key={r.label + (r.sub ?? '')}>
            <text x={0} y={y + 9} fontSize={10.5}>
              {r.label}
            </text>
            {r.sub && (
              <text x={0} y={y + 21} fontSize={9} fontStyle="italic" fill={INK_2}>
                {r.sub}
              </text>
            )}
            {wH > 0 && <ToneRect x={x0} y={y} w={wH} h={15} tone="cross" />}
            {wG > 0 && <ToneRect x={x0 + wH} y={y} w={wG} h={15} tone="red" />}
            {wH > 0 && <path d={ruledRect(x0, y, wH, 15, seedOf(r.label + i), 0.3)} fill="none" stroke={INK} strokeWidth={0.85} />}
            {wG > 0 && <rect x={x0 + wH} y={y} width={wG} height={15} fill="none" stroke={RED} strokeWidth={0.85} strokeDasharray="3 1.6" />}
            {r.have > r.need && <line x1={x0 + wN} x2={x0 + wN} y1={y - 3} y2={y + 18} stroke={INK} strokeWidth={1.2} />}
            <text x={x0 + span + 10} y={y + 7} fontSize={9.4} fill={INK_2}>
              {c(r.have)} of {c(r.need)}
              {monthly ? ' pm' : ''}
            </text>
            <text x={x0 + span + 10} y={y + 19} fontSize={9.4} fill={gap > 0.5 ? RED : INK_2} fontStyle={gap > 0.5 ? undefined : 'italic'}>
              {gap > 0.5 ? `short ${c(gap)}` : 'fully met'}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ================================================================== */
/* Instruments — each ratio read off its own engraved scale            */
/* ================================================================== */

export interface Instrument {
  label: string;
  value: number;
  display: string;
  good: { min?: number; max?: number };
  guide: string;
  scaleMax: number;
  tick: (v: number) => string;
}

export function Instruments({ items }: { items: Instrument[] }) {
  const W = 640;
  const x0 = 178;
  const span = 372;
  const rh = 52;
  return (
    <svg viewBox={`0 0 ${W} ${items.length * rh + 4}`} className="vr-svg" role="img" aria-label={items.map((g) => `${g.label} ${g.display}, guide ${g.guide}`).join('; ')}>
      {items.map((g, i) => {
        const y = 24 + i * rh;
        const sx = (v: number) => x0 + (clamp(Number.isFinite(v) ? v : 0, 0, g.scaleMax) / g.scaleMax) * span;
        const lo = g.good.min ?? 0;
        const hi = Math.min(g.good.max ?? g.scaleMax, g.scaleMax);
        const within = g.value >= (g.good.min ?? -Infinity) && g.value <= (g.good.max ?? Infinity);
        const px = sx(g.value);
        return (
          <g key={g.label}>
            <text x={0} y={y - 2} fontSize={11} className="sc">
              {g.label}
            </text>
            <text x={0} y={y + 12} fontSize={9.4} fontStyle="italic" fill={INK_2}>
              guide {g.guide}
            </text>
            <ToneRect x={sx(lo)} y={y - 6} w={Math.max(0, sx(hi) - sx(lo))} h={6} tone="hatch" />
            <rect x={sx(lo)} y={y - 6} width={Math.max(0, sx(hi) - sx(lo))} height={6} fill="none" stroke={INK} strokeWidth={0.5} />
            <path d={ruled(x0, y, x0 + span, y, seedOf(g.label), 0.25)} stroke={INK} strokeWidth={1} fill="none" />
            {Array.from({ length: 21 }, (_, k) => {
              const x = x0 + (k / 20) * span;
              const major = k % 5 === 0;
              return (
                <g key={k}>
                  <line x1={x} x2={x} y1={y} y2={y + (major ? 6 : 3)} stroke={INK} strokeWidth={major ? 0.8 : 0.5} />
                  {major && (
                    <text x={x} y={y + 16} fontSize={8.4} textAnchor="middle" fill={INK_2}>
                      {g.tick((k / 20) * g.scaleMax)}
                    </text>
                  )}
                </g>
              );
            })}
            <path d={`M${px - 5},${y - 15} L${px + 5},${y - 15} L${px},${y - 7} Z`} fill={within ? INK : RED} />
            <text x={px} y={y - 18} fontSize={10.5} textAnchor="middle" fontWeight={600} fill={within ? INK : RED}>
              {g.display}
            </text>
            <text x={W - 2} y={y + 3} fontSize={10} textAnchor="end" className="sc" fill={within ? INK : RED}>
              {within ? 'within guide' : 'outside guide'}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ================================================================== */
/* Need columns — what would be needed, built up from its parts,       */
/* beside what is in place and the gap between them                    */
/* ================================================================== */

export interface ColSeg {
  label: string;
  value: number;
  gap?: boolean;
}

export interface ColGroup {
  title: string;
  sub?: string;
  needed: ColSeg[];
  held: ColSeg[];
}

export function NeedColumns({ groups, neededLabel = 'needed', heldLabel = 'in place' }: { groups: ColGroup[]; neededLabel?: string; heldLabel?: string }) {
  const GW = 344;
  const W = GW * groups.length;
  const top = 46;
  const base = 262;
  const H = base + 36;
  const max = Math.max(1, ...groups.flatMap((g) => [sum(g.needed.map((s) => s.value)), sum(g.held.map((s) => s.value))]));
  const s = (base - top) / max;
  const tones = toneMap(groups.flatMap((g) => [...g.needed, ...g.held].filter((x) => !x.gap && x.value > 0).map((x) => x.label)));

  const column = (gx: number, segs: ColSeg[], x: number, side: 'l' | 'r', key: string) => {
    const w = 56;
    let cursor = base;
    const blocks = segs
      .filter((sg) => sg.value > 0.5)
      .map((sg) => {
        const h = Math.max(1.5, sg.value * s);
        const y = cursor - h;
        cursor = y;
        return { ...sg, y, h };
      });
    const maxChars = side === 'l' ? 21 : 17;
    const labels = blocks.map((b) => wrap(b.label, maxChars));
    const heights = labels.map((l) => l.length * 10.2 + 11.5);
    const ys = spread(
      blocks.map((b, i) => ({ y: b.y + b.h / 2, h: heights[i] })),
      top - 18,
      base + 4,
    );
    const lx = side === 'l' ? gx + 112 : gx + 266;
    return (
      <g key={key}>
        {blocks.map((b, i) => {
          const red = !!b.gap;
          const ly = ys[i] - heights[i] / 2 + 8;
          const ex = side === 'l' ? x : x + w;
          return (
            <g key={b.label + i}>
              <rect x={x} y={b.y} width={w} height={b.h} fill={PAPER} />
              <ToneRect x={x} y={b.y} w={w} h={b.h} tone={red ? 'red' : (tones.get(b.label) ?? 'hatch')} />
              {red ? (
                <rect x={x} y={b.y} width={w} height={b.h} fill="none" stroke={RED} strokeWidth={1} strokeDasharray="3.5 2" />
              ) : (
                <path d={ruledRect(x, b.y, w, b.h, seedOf(key + b.label), 0.35)} fill="none" stroke={INK} strokeWidth={0.9} />
              )}
              <polyline
                points={`${ex},${b.y + b.h / 2} ${side === 'l' ? ex - 5 : ex + 5},${b.y + b.h / 2} ${side === 'l' ? lx + 3 : lx - 3},${ys[i]}`}
                fill="none"
                stroke={red ? RED : INK_2}
                strokeWidth={0.55}
              />
              <Lines x={lx} y={ly} lines={labels[i]} size={9.4} lead={10.2} anchor={side === 'l' ? 'end' : 'start'} fill={red ? RED : INK} />
              <text x={lx} y={ly + labels[i].length * 10.2} fontSize={8.9} textAnchor={side === 'l' ? 'end' : 'start'} fill={red ? RED : INK_2} fontWeight={red ? 600 : undefined}>
                {moneyCompact(b.value)}
              </text>
            </g>
          );
        })}
      </g>
    );
  };

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="vr-svg"
      style={{ maxWidth: groups.length === 1 ? '62%' : undefined, margin: groups.length === 1 ? '0 auto' : undefined }}
      role="img"
      aria-label={groups
        .map((g) => {
          const n = sum(g.needed.map((x) => x.value));
          const gap = sum(g.held.filter((x) => x.gap).map((x) => x.value));
          return `${g.title}: ${money(n)} ${neededLabel}; ${g.held.map((x) => `${x.gap ? 'shortfall' : x.label} ${money(x.value)}`).join(', ')}${gap > 0 ? '' : '; no shortfall'}`;
        })
        .join('. ')}
    >
      {groups.map((g, gi) => {
        const gx = gi * GW;
        const nx = gx + 124;
        const hx = gx + 198;
        const tn = sum(g.needed.map((x) => x.value));
        const th = sum(g.held.filter((x) => !x.gap).map((x) => x.value));
        return (
          <g key={g.title}>
            <text x={gx + 191} y={14} fontSize={13} textAnchor="middle" className="sc">
              {g.title}
            </text>
            {g.sub && (
              <text x={gx + 191} y={27} fontSize={9.5} textAnchor="middle" fontStyle="italic" fill={INK_2}>
                {g.sub}
              </text>
            )}
            {column(gx, g.needed, nx, 'l', `${g.title}-n`)}
            {column(gx, g.held, hx, 'r', `${g.title}-h`)}
            <line x1={nx - 10} x2={hx + 66} y1={base} y2={base} stroke={INK} strokeWidth={1} />
            <text x={nx + 28} y={base + 15} fontSize={10} textAnchor="middle" className="sc">
              {neededLabel}
            </text>
            <text x={nx + 28} y={base + 28} fontSize={10} textAnchor="middle" fontWeight={600}>
              {moneyCompact(tn)}
            </text>
            <text x={hx + 28} y={base + 15} fontSize={10} textAnchor="middle" className="sc">
              {heldLabel}
            </text>
            <text x={hx + 28} y={base + 28} fontSize={10} textAnchor="middle" fontWeight={600}>
              {moneyCompact(th)}
            </text>
            {gi > 0 && <line x1={gx} x2={gx} y1={6} y2={H - 6} stroke={RULE_FAINT} strokeWidth={0.6} />}
          </g>
        );
      })}
    </svg>
  );
}

/* ================================================================== */
/* Graph paper — the retirement projection                             */
/* ================================================================== */

/** Axis money: R0, R250k, R2.5m, R20m. */
const axisMoney = (v: number) => (v <= 0 ? 'R0' : v >= 1e6 ? `R${+(v / 1e6).toFixed(1)}m` : v >= 1e3 ? `R${Math.round(v / 1e3)}k` : money(v));

const niceCeil = (v: number) => {
  if (v <= 0) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(v)));
  return ([1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map((f) => f * e).find((x) => x >= v) ?? 10 * e) as number;
};

function GraphPaper({
  x,
  y,
  w,
  h,
  cols,
  rows,
  majorEvery = 5,
  rowMajorEvery = 5,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  cols: number;
  rows: number;
  majorEvery?: number;
  rowMajorEvery?: number;
}) {
  const v = Array.from({ length: cols + 1 }, (_, i) => i);
  const r = Array.from({ length: rows + 1 }, (_, i) => i);
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} fill="#f6e9d3" />
      {v.map((i) => (
        <line key={`v${i}`} x1={x + (i / cols) * w} x2={x + (i / cols) * w} y1={y} y2={y + h} stroke={i % majorEvery === 0 ? GRID_MAJOR : GRID_MINOR} strokeWidth={i % majorEvery === 0 ? 0.8 : 0.5} />
      ))}
      {r.map((i) => (
        <line
          key={`h${i}`}
          x1={x}
          x2={x + w}
          y1={y + (i / rows) * h}
          y2={y + (i / rows) * h}
          stroke={i % rowMajorEvery === 0 ? GRID_MAJOR : GRID_MINOR}
          strokeWidth={i % rowMajorEvery === 0 ? 0.8 : 0.5}
        />
      ))}
    </g>
  );
}

export function RetirementPaper({ r, name }: { r: RetirementResult; name: string }) {
  const W = 640;
  const H = 262;
  const m = { l: 62, r: 14, t: 14, b: 44 };
  const pw = W - m.l - m.r;
  const ph = H - m.t - m.b;
  const a0 = Math.floor(r.currentAge);
  const a1 = Math.max(a0 + 5, Math.ceil(r.planningAge));
  const pts = r.timeline.filter((t) => t.age >= a0 && t.age <= a1);
  const peak = Math.max(r.requiredCapitalReal, ...pts.map((t) => t.capitalReal), 1);
  const yMax = niceCeil(peak * 1.08);
  const sx = (age: number) => m.l + ((age - a0) / (a1 - a0)) * pw;
  const sy = (v: number) => m.t + ph - (clamp(v, 0, yMax) / yMax) * ph;
  const curve = pts.map((t, i) => `${i ? 'L' : 'M'}${sx(t.age).toFixed(1)},${sy(t.capitalReal).toFixed(1)}`).join(' ');
  const rx = sx(r.retirementAge);
  const short = r.requiredCapitalReal - r.projectedCapitalReal;
  const yTicks = [0, 0.2, 0.4, 0.6, 0.8, 1].map((f) => f * yMax);
  const ageTicks: number[] = [];
  for (let a = Math.ceil(a0 / 5) * 5; a <= a1; a += 5) ageTicks.push(a);
  const depleted = r.depletionAge !== null && r.depletionAge < r.planningAge;
  const labelAbove = sy(r.requiredCapitalReal) > m.t + 22;
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="vr-svg"
      role="img"
      aria-label={`${name}: savings grow from ${money(r.currentCapital)} today to a projected ${money(r.projectedCapitalReal)} at age ${r.retirementAge}, in today's money, against ${money(r.requiredCapitalReal)} needed.${depleted ? ` Savings run out at about age ${Math.floor(r.depletionAge!)}.` : ''}`}
    >
      <GraphPaper x={m.l} y={m.t} w={pw} h={ph} cols={a1 - a0} rows={25} majorEvery={5} rowMajorEvery={5} />
      <rect x={m.l} y={m.t} width={pw} height={ph} fill="none" stroke={INK} strokeWidth={0.9} />
      {/* inward ticks */}
      {ageTicks.map((a) => (
        <g key={a}>
          <line x1={sx(a)} x2={sx(a)} y1={m.t + ph} y2={m.t + ph - 5} stroke={INK} strokeWidth={0.8} />
          <text x={sx(a)} y={m.t + ph + 13} fontSize={9.5} textAnchor="middle" fill={INK}>
            {a}
          </text>
        </g>
      ))}
      {yTicks.map((v) => (
        <g key={v}>
          <line x1={m.l} x2={m.l + 5} y1={sy(v)} y2={sy(v)} stroke={INK} strokeWidth={0.8} />
          <text x={m.l - 6} y={sy(v) + 3} fontSize={9.5} textAnchor="end" fill={INK}>
            {axisMoney(v)}
          </text>
        </g>
      ))}
      <text x={m.l + pw / 2} y={H - 8} fontSize={10} textAnchor="middle" fontStyle="italic" fill={INK_2}>
        Age, in years
      </text>
      <text transform={`translate(13 ${m.t + ph / 2}) rotate(-90)`} fontSize={10} textAnchor="middle" fontStyle="italic" fill={INK_2}>
        Savings, in today’s money
      </text>
      {/* capital needed */}
      <line x1={m.l} x2={m.l + pw} y1={sy(r.requiredCapitalReal)} y2={sy(r.requiredCapitalReal)} stroke={INK} strokeWidth={0.9} strokeDasharray="5 3" />
      <text x={m.l + 8} y={sy(r.requiredCapitalReal) + (labelAbove ? -5 : 12)} fontSize={9.6} fontStyle="italic" fill={INK}>
        capital needed by {r.retirementAge}: {moneyCompact(r.requiredCapitalReal)}
      </text>
      {/* retirement */}
      <line x1={rx} x2={rx} y1={m.t} y2={m.t + ph} stroke={INK_2} strokeWidth={0.8} strokeDasharray="2 2.4" />
      <text transform={`translate(${rx - 4} ${m.t + ph - 6}) rotate(-90)`} fontSize={9.4} fontStyle="italic" fill={INK_2}>
        retires at {r.retirementAge}
      </text>
      {/* projection */}
      <path d={curve} fill="none" stroke={INK} strokeWidth={1.5} strokeLinejoin="round" />
      {pts
        .filter((t) => (Math.round(t.age) - a0) % 5 === 0)
        .map((t) => (
          <circle key={t.age} cx={sx(t.age)} cy={sy(t.capitalReal)} r={2.7} fill={PAPER} stroke={INK} strokeWidth={0.9} />
        ))}
      {short > 0.5 && (
        <g>
          <line x1={rx + 9} x2={rx + 9} y1={sy(r.requiredCapitalReal)} y2={sy(r.projectedCapitalReal)} stroke={RED} strokeWidth={1.1} />
          <line x1={rx + 5} x2={rx + 13} y1={sy(r.requiredCapitalReal)} y2={sy(r.requiredCapitalReal)} stroke={RED} strokeWidth={1.1} />
          <line x1={rx + 5} x2={rx + 13} y1={sy(r.projectedCapitalReal)} y2={sy(r.projectedCapitalReal)} stroke={RED} strokeWidth={1.1} />
          <text x={rx + 17} y={(sy(r.requiredCapitalReal) + sy(r.projectedCapitalReal)) / 2 + 3.5} fontSize={10} fill={RED} fontWeight={600}>
            short {moneyCompact(short)}
          </text>
        </g>
      )}
      {depleted && (
        <g>
          <path
            d={`M${sx(r.depletionAge!) - 4},${sy(0) - 4} L${sx(r.depletionAge!) + 4},${sy(0) + 4} M${sx(r.depletionAge!) - 4},${sy(0) + 4} L${sx(r.depletionAge!) + 4},${sy(0) - 4}`}
            stroke={RED}
            strokeWidth={1.4}
          />
          {m.l + pw - 78 > sx(r.depletionAge!) + 10 && <line x1={m.l + pw - 78} y1={sy(0) - 13} x2={sx(r.depletionAge!) + 6} y2={sy(0) - 5} stroke={RED} strokeWidth={0.6} />}
          <text x={m.l + pw - 5} y={sy(0) - 21} fontSize={9.6} textAnchor="end" fontStyle="italic" fill={RED}>
            savings run out
          </text>
          <text x={m.l + pw - 5} y={sy(0) - 9} fontSize={9.6} textAnchor="end" fontStyle="italic" fill={RED}>
            at about age {Math.floor(r.depletionAge!)}
          </text>
        </g>
      )}
      {/* key */}
      <g transform={`translate(${m.l + pw - 168} ${m.t + 8})`}>
        <rect x={0} y={0} width={160} height={46} fill={PAPER} stroke={INK} strokeWidth={0.6} />
        <line x1={8} x2={30} y1={12} y2={12} stroke={INK} strokeWidth={1.5} />
        <circle cx={19} cy={12} r={2.7} fill={PAPER} stroke={INK} strokeWidth={0.9} />
        <text x={36} y={15} fontSize={9.3}>
          projected savings
        </text>
        <line x1={8} x2={30} y1={26} y2={26} stroke={INK} strokeWidth={0.9} strokeDasharray="5 3" />
        <text x={36} y={29} fontSize={9.3}>
          capital needed at retirement
        </text>
        <text x={8} y={41} fontSize={8.6} fontStyle="italic" fill={INK_2}>
          ○ marks every fifth year
        </text>
      </g>
    </svg>
  );
}

/* ================================================================== */
/* The dial — share of the wanted retirement income the savings pay    */
/* ================================================================== */

export function Dial({ id, ratio, caption }: { id: string; ratio: number; caption: string }) {
  const W = 300;
  const cx = 150;
  const cy = 130;
  const R = 106;
  const H = cy + 56;
  const top = 1.2;
  const ang = (v: number) => Math.PI - (clamp(v, 0, top) / top) * Math.PI;
  const pt = (v: number, r: number) => [cx + r * Math.cos(ang(v)), cy - r * Math.sin(ang(v))] as const;
  const band = (from: number, to: number, tone: Tone, key: string) => {
    const [ax, ay] = pt(from, R);
    const [bx, by] = pt(to, R);
    const [cx2, cy2] = pt(to, R - 13);
    const [dx, dy] = pt(from, R - 13);
    const d = `M${ax},${ay} A${R},${R} 0 0 1 ${bx},${by} L${cx2},${cy2} A${R - 13},${R - 13} 0 0 0 ${dx},${dy} Z`;
    const clip = `vr-dial-${id}-${key}`;
    return (
      <g key={key}>
        <clipPath id={clip}>
          <path d={d} />
        </clipPath>
        <g clipPath={`url(#${clip})`}>
          <ToneRect x={cx - R - 2} y={cy - R - 2} w={2 * R + 4} h={R + 4} tone={tone} />
        </g>
        <path d={d} fill="none" stroke={tone === 'red' ? RED : INK} strokeWidth={0.6} />
      </g>
    );
  };
  const [nx, ny] = pt(ratio, R - 19);
  const [tx, ty] = pt(ratio, -10);
  const state = ratio >= 0.95 ? 'on course' : ratio >= 0.6 ? 'partly on course' : 'well short';
  const tone = ratio >= 0.6 ? INK : RED;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="vr-svg vr-dial" role="img" aria-label={`${caption}: ${pct(ratio)} of the income wanted, ${state}.`}>
      {band(0, 0.6, 'red', 'a')}
      {band(0.6, 0.95, 'hatch', 'b')}
      {band(0.95, top, 'cross', 'c')}
      <path d={`M${pt(0, R)[0]},${cy} A${R},${R} 0 0 1 ${pt(top, R)[0]},${cy}`} fill="none" stroke={INK} strokeWidth={0.9} />
      {Array.from({ length: 25 }, (_, i) => {
        const v = (i / 24) * top;
        const major = i % 4 === 0;
        const [x1, y1] = pt(v, R);
        const [x2, y2] = pt(v, R + (major ? 7 : 4));
        const [lx, ly] = pt(v, R + 16);
        return (
          <g key={i}>
            <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={INK} strokeWidth={major ? 0.9 : 0.5} />
            {major && (
              <text x={lx} y={ly + 3} fontSize={8.6} textAnchor="middle" fill={INK_2}>
                {Math.round(v * 100)}
              </text>
            )}
          </g>
        );
      })}
      {(() => {
        const [a1, b1] = pt(1, R - 15);
        const [a2, b2] = pt(1, R + 9);
        return <line x1={a1} y1={b1} x2={a2} y2={b2} stroke={INK} strokeWidth={1.6} />;
      })()}
      <line x1={tx} y1={ty} x2={nx} y2={ny} stroke={tone} strokeWidth={2} strokeLinecap="round" />
      <circle cx={cx} cy={cy} r={6} fill={INK} />
      <circle cx={cx} cy={cy} r={2} fill={PAPER} />
      <line x1={cx - R - 6} x2={cx - R + 19} y1={cy} y2={cy} stroke={INK} strokeWidth={0.7} />
      <line x1={cx + R - 19} x2={cx + R + 6} y1={cy} y2={cy} stroke={INK} strokeWidth={0.7} />
      <text x={cx} y={cy + 33} fontSize={25} textAnchor="middle" fontWeight={600} fill={tone}>
        {pct(ratio)}
      </text>
      <text x={cx} y={cy + 48} fontSize={9.5} textAnchor="middle" fontStyle="italic" fill={INK_2}>
        of the income wanted · the bold tick is 100%
      </text>
    </svg>
  );
}

/* ================================================================== */
/* The almanac — the coming events on one line of years                */
/* ================================================================== */

export interface AlmanacEvent {
  year: number;
  label: string;
  red?: boolean;
}

export function Almanac({ start, events }: { start: number; events: AlmanacEvent[] }) {
  const W = 640;
  const lane = 25;
  const last = Math.max(start + 10, ...events.map((e) => e.year)) + 2;
  const x0 = 24;
  const x1 = W - 24;
  const sx = (y: number) => x0 + ((y - start) / (last - start)) * (x1 - x0);
  const taken: { lane: number; a: number; b: number }[] = [];
  const order = [-1, 1, -2, 2, -3, 3];
  const placed = [...events]
    .sort((a, b) => a.year - b.year)
    .map((e) => {
      const x = sx(e.year);
      const w = Math.max(e.label.length, 6) * 4.9 + 12;
      const a = clamp(x - w / 2, 0, W - w);
      const b = a + w;
      const ln = order.find((l) => !taken.some((t) => t.lane === l && !(b < t.a || a > t.b))) ?? order[order.length - 1];
      taken.push({ lane: ln, a, b });
      return { ...e, x, lane: ln, tx: a + w / 2 };
    });
  const ticks: number[] = [];
  for (let y = start; y <= last; y++) ticks.push(y);
  const upN = Math.max(0, ...placed.filter((p) => p.lane < 0).map((p) => -p.lane));
  const downN = Math.max(0, ...placed.filter((p) => p.lane > 0).map((p) => p.lane));
  const axis = 14 + upN * lane;
  const H = axis + 24 + downN * lane;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="vr-svg" role="img" aria-label={placed.map((e) => `${e.year}: ${e.label}`).join('; ')}>
      <line x1={x0 - 10} x2={x1 + 10} y1={axis} y2={axis} stroke={INK} strokeWidth={1.2} />
      <line x1={x0 - 10} x2={x1 + 10} y1={axis + 3} y2={axis + 3} stroke={INK} strokeWidth={0.5} />
      {ticks.map((y) => {
        const major = y % 5 === 0;
        return (
          <g key={y}>
            <line x1={sx(y)} x2={sx(y)} y1={axis - (major ? 5 : 2.5)} y2={axis} stroke={INK} strokeWidth={major ? 0.8 : 0.5} />
            {major && (
              <text x={sx(y)} y={axis + 15} fontSize={9} textAnchor="middle" fill={INK_2}>
                {y}
              </text>
            )}
          </g>
        );
      })}
      <text x={x0 - 10} y={axis + 15} fontSize={9} fill={INK_2} fontStyle="italic">
        today
      </text>
      {placed.map((e) => {
        const up = e.lane < 0;
        const L = Math.abs(e.lane);
        const labelY = up ? axis - L * lane - 2 : axis + 8 + L * lane;
        const yearY = labelY + 10;
        const c = e.red ? RED : INK;
        return (
          <g key={e.label + e.year}>
            <line x1={e.x} x2={e.x} y1={axis} y2={up ? yearY + 3 : labelY - 9} stroke={c} strokeWidth={0.6} />
            <circle cx={e.x} cy={axis + 1.5} r={3} fill={e.red ? RED : INK} />
            <text x={e.tx} y={labelY} fontSize={9.6} textAnchor="middle" fill={c}>
              {e.label}
            </text>
            <text x={e.tx} y={yearY} fontSize={8.6} textAnchor="middle" fontStyle="italic" fill={e.red ? RED : INK_2}>
              {e.year}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ================================================================== */
/* Cost of the advice on graph paper, year by year                     */
/* ================================================================== */

export function CostPaper({ series, years = 20 }: { series: { label: string; monthly: number; escalation: number }[]; years?: number }) {
  const W = 640;
  const H = 200;
  const m = { l: 62, r: 10, t: 12, b: 30 };
  const pw = W - m.l - m.r;
  const ph = H - m.t - m.b;
  const ys = Array.from({ length: years }, (_, i) => i + 1);
  const at = (s: (typeof series)[number], y: number) => s.monthly * Math.pow(1 + s.escalation, y - 1);
  const totals = ys.map((y) => sum(series.map((s) => at(s, y))));
  const yMax = niceCeil(Math.max(1, ...totals) * 1.1);
  const bw = pw / years;
  const sy = (v: number) => m.t + ph - (v / yMax) * ph;
  const labelled = new Set([1, 5, 10, 15, 20]);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="vr-svg" role="img" aria-label={`Total monthly cost rises from ${money(totals[0])} in year 1 to ${money(totals[years - 1])} in year ${years}.`}>
      <GraphPaper x={m.l} y={m.t} w={pw} h={ph} cols={years} rows={20} majorEvery={5} rowMajorEvery={5} />
      <rect x={m.l} y={m.t} width={pw} height={ph} fill="none" stroke={INK} strokeWidth={0.9} />
      {[0, 0.5, 1].map((f) => (
        <g key={f}>
          <line x1={m.l} x2={m.l + 5} y1={sy(f * yMax)} y2={sy(f * yMax)} stroke={INK} strokeWidth={0.8} />
          <text x={m.l - 6} y={sy(f * yMax) + 3} fontSize={9.4} textAnchor="end">
            {money(f * yMax)}
          </text>
        </g>
      ))}
      {ys.map((y, i) => {
        let b = 0;
        return (
          <g key={y}>
            {series.map((s, si) => {
              const v = at(s, y);
              const top = sy(b + v);
              const h = sy(b) - top;
              b += v;
              return (
                <g key={si}>
                  <rect x={m.l + i * bw + 3} y={top} width={bw - 6} height={Math.max(0, h)} fill={PAPER} />
                  <ToneRect x={m.l + i * bw + 3} y={top} w={bw - 6} h={Math.max(0, h)} tone={TONES[si % TONES.length]} />
                  <rect x={m.l + i * bw + 3} y={top} width={bw - 6} height={Math.max(0, h)} fill="none" stroke={INK} strokeWidth={0.7} />
                </g>
              );
            })}
            {labelled.has(y) && (
              <text x={m.l + i * bw + bw / 2} y={sy(totals[i]) - 4} fontSize={9} textAnchor="middle" fontWeight={600}>
                {axisMoney(totals[i])}
              </text>
            )}
            <text x={m.l + i * bw + bw / 2} y={H - m.b + 13} fontSize={8.8} textAnchor="middle" fill={labelled.has(y) ? INK : INK_3}>
              {y}
            </text>
          </g>
        );
      })}
      <text x={m.l + pw / 2} y={H - 2} fontSize={9.6} textAnchor="middle" fontStyle="italic" fill={INK_2}>
        Year of the plan
      </text>
    </svg>
  );
}

/* ================================================================== */
/* The family table                                                    */
/* ================================================================== */

export interface Kin {
  name: string;
  sub: string;
}

export function FamilyTree({ adults, children, others, union }: { adults: Kin[]; children: Kin[]; others: Kin[]; union?: string }) {
  const W = 640;
  const hasKids = children.length > 0;
  const H = hasKids ? 150 : 70;
  const ay = 28;
  const ky = 122;
  const ax = adults.length > 1 ? [190, 450] : [320];
  const mid = adults.length > 1 ? (ax[0] + ax[1]) / 2 : ax[0];
  const kx = children.map((_, i) => (children.length === 1 ? mid : mid - ((children.length - 1) * 130) / 2 + i * 130));
  const node = (k: Kin, x: number, y: number, key: string) => (
    <g key={key}>
      <text x={x} y={y} fontSize={12.5} textAnchor="middle" className="sc">
        {k.name}
      </text>
      <text x={x} y={y + 13} fontSize={9.3} textAnchor="middle" fontStyle="italic" fill={INK_2}>
        {k.sub}
      </text>
    </g>
  );
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="vr-svg" role="img" aria-label={`The household: ${[...adults, ...children, ...others].map((k) => `${k.name}, ${k.sub}`).join('; ')}.`}>
      {adults.map((a, i) => node(a, ax[i], ay, `a${i}`))}
      {adults.length > 1 && (
        <g>
          <line x1={ax[0] + 74} x2={ax[1] - 74} y1={ay - 6} y2={ay - 6} stroke={INK} strokeWidth={0.8} />
          <line x1={ax[0] + 74} x2={ax[1] - 74} y1={ay - 2.5} y2={ay - 2.5} stroke={INK} strokeWidth={0.8} />
          {union && (
            <text x={mid} y={ay - 12} fontSize={8.8} textAnchor="middle" fontStyle="italic" fill={INK_2}>
              {union}
            </text>
          )}
        </g>
      )}
      {hasKids && (
        <g fill="none" stroke={INK} strokeWidth={0.8}>
          <line x1={mid} x2={mid} y1={adults.length > 1 ? ay - 2.5 : ay + 18} y2={ky - 36} />
          {children.length > 1 && <line x1={kx[0]} x2={kx[kx.length - 1]} y1={ky - 36} y2={ky - 36} />}
          {kx.map((x, i) => (
            <line key={i} x1={x} x2={x} y1={ky - 36} y2={ky - 14} />
          ))}
        </g>
      )}
      {children.map((c, i) => node(c, kx[i], ky, `c${i}`))}
      {others.length > 0 && (
        <g>
          <text x={W - 4} y={hasKids ? ky - 40 : ay + 34} fontSize={9} textAnchor="end" className="sc" fill={INK_2}>
            also supported
          </text>
          {others.map((o, i) => (
            <text key={i} x={W - 4} y={(hasKids ? ky - 27 : ay + 47) + i * 13} fontSize={9.6} textAnchor="end">
              {o.name}, <tspan fontStyle="italic">{o.sub}</tspan>
            </text>
          ))}
        </g>
      )}
    </svg>
  );
}

/* ================================================================== */
/* Catalogue bars — living expenses by category                        */
/* ================================================================== */

export function CatalogueBars({ items }: { items: { label: string; value: number }[] }) {
  const W = 640;
  const x0 = 186;
  const span = 360;
  const rh = 19;
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <svg viewBox={`0 0 ${W} ${items.length * rh + 4}`} className="vr-svg" role="img" aria-label={items.map((i) => `${i.label} ${money(i.value)}`).join('; ')}>
      {items.map((it, i) => {
        const y = 4 + i * rh;
        const w = (it.value / max) * span;
        return (
          <g key={it.label}>
            <text x={0} y={y + 10} fontSize={10.3}>
              {it.label}
            </text>
            <ToneRect x={x0} y={y + 1} w={w} h={11} tone="hatch" />
            <path d={ruledRect(x0, y + 1, w, 11, seedOf(it.label), 0.3)} fill="none" stroke={INK} strokeWidth={0.8} />
            <text x={x0 + w + 7} y={y + 10} fontSize={9.8} fill={INK}>
              {money(it.value)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ================================================================== */
/* The scale of five — the investor risk profile                       */
/* ================================================================== */

export function RiskScale({ cats, profile, tolerance, capacity }: { cats: { label: string; equity: string }[]; profile: number; tolerance: number; capacity: number }) {
  const W = 640;
  const H = 120;
  const by = 18;
  const bw = W / cats.length;
  const marks = [
    { i: tolerance, label: 'your tolerance', solid: true },
    { i: capacity, label: 'your capacity', solid: false },
  ].filter((mk) => mk.i >= 0);
  const same = marks.length === 2 && marks[0].i === marks[1].i;
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="vr-svg"
      role="img"
      aria-label={`Recommended profile ${cats[profile]?.label ?? 'not set'}, on a scale from ${cats[0].label.toLowerCase()} to ${cats[cats.length - 1].label.toLowerCase()}${marks.map((mk) => `; ${mk.label} ${cats[mk.i].label}`).join('')}.`}
    >
      {cats.map((c, i) => {
        const x = i * bw;
        const on = i === profile;
        return (
          <g key={c.label}>
            {on && (
              <text x={x + bw / 2} y={11} fontSize={9.4} textAnchor="middle" fontStyle="italic">
                recommended
              </text>
            )}
            <rect x={x + 3} y={by} width={bw - 6} height={46} fill="none" stroke={INK} strokeWidth={on ? 1.5 : 0.6} />
            {on && <rect x={x + 6.5} y={by + 3.5} width={bw - 13} height={39} fill="none" stroke={INK} strokeWidth={0.6} />}
            <text x={x + bw / 2} y={by + 20} fontSize={11} textAnchor="middle" className="sc" fontWeight={on ? 600 : undefined}>
              {c.label.length > 16 ? c.label.replace('Moderately', 'Mod.') : c.label}
            </text>
            <text x={x + bw / 2} y={by + 34} fontSize={8.6} textAnchor="middle" fontStyle="italic" fill={INK_2}>
              {c.equity.replace(' growth assets', ' in growth')}
            </text>
          </g>
        );
      })}
      {marks.map((mk, j) => {
        const cx = mk.i * bw + bw / 2 + (same ? (j ? 34 : -34) : 0);
        const y = by + 52;
        return (
          <g key={mk.label}>
            <path d={`M${cx},${y} L${cx - 5},${y + 9} L${cx + 5},${y + 9} Z`} fill={mk.solid ? INK : PAPER} stroke={INK} strokeWidth={0.8} />
            <text x={cx} y={y + 21} fontSize={9.2} textAnchor="middle" fontStyle="italic">
              {mk.label}
            </text>
          </g>
        );
      })}
      <text x={2} y={H - 3} fontSize={8.8} fill={INK_2}>
        ← steadier, lower expected return
      </text>
      <text x={W - 2} y={H - 3} fontSize={8.8} textAnchor="end" fill={INK_2}>
        bolder, higher expected return →
      </text>
    </svg>
  );
}
