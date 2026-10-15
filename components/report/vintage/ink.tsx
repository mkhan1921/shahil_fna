/*
 * Ink, paper and engraving helpers for the vintage edition of the report.
 *
 * The visual treatment follows the ideas catalogued in
 * github.com/Foadsf/vintage-latex (no code from it is used): warm paper and
 * softened ink, hatching in place of grey fills, seeded line tremor so every
 * rebuild draws the same "hand-ruled" plate, and labels kept as real text.
 */

export const INK = '#2a1d14';
export const INK_2 = '#574333';
export const INK_3 = '#76604c';
export const RED = '#9a2b1d';
export const PAPER = '#f4ead8';
export const RULE_FAINT = '#c9b597';
export const GRID_MINOR = '#edd5b7';
export const GRID_MAJOR = '#d8a776';

/** Deterministic PRNG (mulberry32), so a plate never redraws differently. */
export function rng(seed: number) {
  let t = seed >>> 0;
  return () => {
    t += 0x6d2b79f5;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r ^= r + Math.imul(r ^ (r >>> 7), 61 | r);
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}

/** FNV-1a hash of a label, used as a seed. */
export const seedOf = (s: string) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
};

const r2 = (n: number) => Math.round(n * 100) / 100;

/** A hand-ruled line: straight on average, with a small seeded tremor. */
export function ruled(x1: number, y1: number, x2: number, y2: number, seed: number, amp = 0.45): string {
  const rand = rng(seed);
  const len = Math.hypot(x2 - x1, y2 - y1) || 1;
  const n = Math.max(2, Math.round(len / 14));
  const nx = -(y2 - y1) / len;
  const ny = (x2 - x1) / len;
  let d = `M${r2(x1)},${r2(y1)}`;
  for (let i = 1; i <= n; i++) {
    const t = i / n;
    const j = i === n ? 0 : (rand() - 0.5) * 2 * amp;
    d += ` L${r2(x1 + (x2 - x1) * t + nx * j)},${r2(y1 + (y2 - y1) * t + ny * j)}`;
  }
  return d;
}

/** Outline of a rectangle drawn as four hand-ruled strokes. */
export const ruledRect = (x: number, y: number, w: number, h: number, seed: number, amp = 0.45) =>
  [ruled(x, y, x + w, y, seed, amp), ruled(x + w, y, x + w, y + h, seed + 1, amp), ruled(x + w, y + h, x, y + h, seed + 2, amp), ruled(x, y + h, x, y, seed + 3, amp)].join(' ');

/*
 * Tones. Hatching and stippling are drawn as real vector strokes, computed for
 * each shape, rather than as SVG <pattern> fills: Chrome rasterises pattern
 * fills when printing, which makes a PDF large and soft. Lines sit on one
 * page-wide grid, so neighbouring shapes hatch in step.
 */
export type Tone = 'cross' | 'hatch' | 'back' | 'dense' | 'horiz' | 'vert' | 'dots' | 'sparse' | 'liquid' | 'red' | 'none';

export const PAT = {
  cross: 'cross',
  hatch: 'hatch',
  back: 'back',
  horiz: 'horiz',
  vert: 'vert',
  dots: 'dots',
  sparse: 'sparse',
  dense: 'dense',
  liquid: 'liquid',
  red: 'red',
} as const satisfies Record<string, Tone>;

/** Tones for categories, ordered so neighbours never share a texture. */
export const TONES: Tone[] = ['cross', 'hatch', 'dots', 'horiz', 'back', 'vert', 'sparse', 'dense'];

interface Family {
  /** Unit normal of the lines. */
  n: [number, number];
  gap: number;
  color: string;
  width: number;
}
const D = Math.SQRT1_2;
const FAMILIES: Record<Exclude<Tone, 'dots' | 'sparse' | 'none'>, Family[]> = {
  hatch: [{ n: [D, D], gap: 3.4, color: INK, width: 0.7 }],
  back: [{ n: [D, -D], gap: 3.4, color: INK, width: 0.7 }],
  cross: [
    { n: [D, D], gap: 3.4, color: INK, width: 0.7 },
    { n: [D, -D], gap: 3.4, color: INK, width: 0.7 },
  ],
  dense: [{ n: [D, D], gap: 2.2, color: INK, width: 0.75 }],
  horiz: [{ n: [0, 1], gap: 2.8, color: INK, width: 0.7 }],
  vert: [{ n: [1, 0], gap: 3.2, color: INK, width: 0.7 }],
  liquid: [{ n: [0, 1], gap: 2.4, color: INK_2, width: 0.6 }],
  red: [{ n: [D, D], gap: 3, color: RED, width: 0.85 }],
};
const STIPPLE = { dots: { gap: 3.2, r: 0.62 }, sparse: { gap: 5, r: 0.55 } };

export type Shape = { kind: 'rect'; x: number; y: number; w: number; h: number } | { kind: 'circle'; cx: number; cy: number; r: number };

/** The parallel strokes of one family that fall inside a rectangle or circle. */
function strokes(shape: Shape, { n, gap }: Family): string {
  const t: [number, number] = [-n[1], n[0]];
  const pts: [number, number][] =
    shape.kind === 'rect'
      ? [
          [shape.x, shape.y],
          [shape.x + shape.w, shape.y],
          [shape.x, shape.y + shape.h],
          [shape.x + shape.w, shape.y + shape.h],
        ]
      : [
          [shape.cx - shape.r, shape.cy - shape.r],
          [shape.cx + shape.r, shape.cy - shape.r],
          [shape.cx - shape.r, shape.cy + shape.r],
          [shape.cx + shape.r, shape.cy + shape.r],
        ];
  const proj = pts.map(([x, y]) => x * n[0] + y * n[1]);
  const lo = Math.min(...proj);
  const hi = Math.max(...proj);
  let d = '';
  for (let k = Math.ceil(lo / gap); k * gap <= hi; k++) {
    const c = k * gap;
    let s0 = -Infinity;
    let s1 = Infinity;
    if (shape.kind === 'circle') {
      const off = shape.cx * n[0] + shape.cy * n[1] - c;
      if (Math.abs(off) >= shape.r) continue;
      const half = Math.sqrt(shape.r * shape.r - off * off);
      const mid = shape.cx * t[0] + shape.cy * t[1];
      s0 = mid - half;
      s1 = mid + half;
    } else {
      const axes: [number, number, number, number][] = [
        [c * n[0], t[0], shape.x, shape.x + shape.w],
        [c * n[1], t[1], shape.y, shape.y + shape.h],
      ];
      for (const [p, dir, a0, a1] of axes) {
        if (Math.abs(dir) < 1e-9) {
          if (p < a0 || p > a1) s1 = s0 - 1;
          continue;
        }
        const a = (a0 - p) / dir;
        const b = (a1 - p) / dir;
        s0 = Math.max(s0, Math.min(a, b));
        s1 = Math.min(s1, Math.max(a, b));
      }
    }
    if (!(s1 - s0 > 0.05)) continue;
    d += `M${r2(c * n[0] + s0 * t[0])},${r2(c * n[1] + s0 * t[1])}L${r2(c * n[0] + s1 * t[0])},${r2(c * n[1] + s1 * t[1])}`;
  }
  return d;
}

/** Staggered dots inside a rectangle or circle. */
function stipple(shape: Shape, gap: number, r: number): string {
  const [x0, y0, x1, y1] = shape.kind === 'rect' ? [shape.x, shape.y, shape.x + shape.w, shape.y + shape.h] : [shape.cx - shape.r, shape.cy - shape.r, shape.cx + shape.r, shape.cy + shape.r];
  let d = '';
  const dot = (x: number, y: number) => {
    const inside = shape.kind === 'rect' ? x - r >= x0 && x + r <= x1 && y - r >= y0 && y + r <= y1 : Math.hypot(x - shape.cx, y - shape.cy) <= shape.r - r;
    if (inside) d += `M${r2(x - r)},${r2(y)}a${r},${r} 0 1,0 ${2 * r},0a${r},${r} 0 1,0 ${-2 * r},0`;
  };
  for (let j = Math.floor(y0 / gap); j * gap <= y1; j++)
    for (let i = Math.floor(x0 / gap); i * gap <= x1; i++) {
      dot(i * gap + gap / 4, j * gap + gap / 4);
      dot(i * gap + (3 * gap) / 4, j * gap + (3 * gap) / 4);
    }
  return d;
}

/** Fill a rectangle or circle with a tone, as vector strokes. */
export function ToneFill({ shape, tone }: { shape: Shape; tone: Tone }) {
  if (tone === 'none') return null;
  if (tone === 'dots' || tone === 'sparse') return <path d={stipple(shape, STIPPLE[tone].gap, STIPPLE[tone].r)} fill={INK} />;
  return (
    <>
      {FAMILIES[tone].map((f, i) => (
        <path key={i} d={strokes(shape, f)} stroke={f.color} strokeWidth={f.width} fill="none" />
      ))}
    </>
  );
}

export const ToneRect = ({ x, y, w, h, tone }: { x: number; y: number; w: number; h: number; tone: Tone }) => (w > 0 && h > 0 ? <ToneFill shape={{ kind: 'rect', x, y, w, h }} tone={tone} /> : null);
export const ToneCircle = ({ cx, cy, r, tone }: { cx: number; cy: number; r: number; tone: Tone }) => <ToneFill shape={{ kind: 'circle', cx, cy, r }} tone={tone} />;

/** A small swatch of a tone, for keys printed in running text. */
export function Swatch({ tone, red = false, round = false }: { tone: Tone; red?: boolean; round?: boolean }) {
  return (
    <svg className="vr-swatch" viewBox="0 0 14 10" aria-hidden>
      {round ? (
        <>
          <ToneCircle cx={7} cy={5} r={4.4} tone={tone} />
          <circle cx="7" cy="5" r="4.4" fill="none" stroke={red ? RED : INK} strokeWidth="0.8" />
        </>
      ) : (
        <>
          <ToneRect x={0.6} y={0.6} w={12.8} h={8.8} tone={tone} />
          <rect x="0.6" y="0.6" width="12.8" height="8.8" fill="none" stroke={red ? RED : INK} strokeWidth="0.8" strokeDasharray={red ? '2 1.4' : undefined} />
        </>
      )}
    </svg>
  );
}

/** The printer's manicule — a pointing hand that marks a matter for attention. */
export function Manicule({ red = false }: { red?: boolean }) {
  const c = red ? RED : INK;
  return (
    <svg className="vr-manicule" viewBox="0 0 24 12" aria-hidden>
      <path d="M0.6,1.4 L4.2,1.9 L4.2,10.1 L0.6,10.6 Z" fill="none" stroke={c} strokeWidth="0.8" />
      <path d="M1.5,2.2 L1.5,9.8 M2.6,2.3 L2.6,9.7 M3.6,2.4 L3.6,9.6" stroke={c} strokeWidth="0.45" />
      <path
        d="M4.2,2.3 C6.4,1.2 9.6,1 12,2.5 L21.7,2.5 C23.5,2.5 23.5,5 21.7,5 L13.8,5 C15,5.3 15,7.2 13.4,7.3 C14.3,7.8 14,9.2 12.7,9.3 C13.4,9.9 12.9,10.9 11.6,10.9 L7.6,10.9 C6.1,10.9 5.1,10.4 4.2,9.8 Z"
        fill={PAPER}
        stroke={c}
        strokeWidth="0.8"
        strokeLinejoin="round"
      />
      <path d="M8.6,5 C10.4,5.6 12.3,5.3 13.8,5 M10.6,7.3 L13.4,7.3 M10.4,9.3 L12.7,9.3" fill="none" stroke={c} strokeWidth="0.55" />
    </svg>
  );
}

/** Rule — loops — rule: the small vector ornament of a period title page. */
export function Ornament({ width = 180 }: { width?: number }) {
  const m = width / 2;
  return (
    <svg className="vr-ornament" viewBox={`0 0 ${width} 14`} style={{ width }} aria-hidden>
      <line x1="0" y1="7" x2={m - 15} y2="7" stroke={INK} strokeWidth="0.9" />
      <line x1={m + 15} y1="7" x2={width} y2="7" stroke={INK} strokeWidth="0.9" />
      <path
        d={`M${m - 15},7 C${m - 12},1.5 ${m - 4},1.5 ${m},7 C${m + 4},12.5 ${m + 12},12.5 ${m + 15},7 C${m + 12},1.5 ${m + 4},1.5 ${m},7 C${m - 4},12.5 ${m - 12},12.5 ${m - 15},7 Z`}
        fill="none"
        stroke={INK}
        strokeWidth="0.9"
      />
      <circle cx={m} cy="7" r="1.1" fill={INK} />
    </svg>
  );
}

/** The three-segment printer's rule. */
export function Rule3() {
  return (
    <div className="vr-rule3" aria-hidden>
      <span />
      <span />
      <span />
    </div>
  );
}

const ROMAN: [number, string][] = [
  [1000, 'M'],
  [900, 'CM'],
  [500, 'D'],
  [400, 'CD'],
  [100, 'C'],
  [90, 'XC'],
  [50, 'L'],
  [40, 'XL'],
  [10, 'X'],
  [9, 'IX'],
  [5, 'V'],
  [4, 'IV'],
  [1, 'I'],
];
export const roman = (n: number) => {
  let s = '';
  for (const [v, r] of ROMAN) while (n >= v) ((s += r), (n -= v));
  return s;
};

const ORDINALS = ['First', 'Second', 'Third', 'Fourth', 'Fifth', 'Sixth', 'Seventh', 'Eighth', 'Ninth', 'Tenth'];
export const ordinal = (n: number) => ORDINALS[n - 1] ?? String(n);

const WORDS = [
  'no',
  'one',
  'two',
  'three',
  'four',
  'five',
  'six',
  'seven',
  'eight',
  'nine',
  'ten',
  'eleven',
  'twelve',
  'thirteen',
  'fourteen',
  'fifteen',
  'sixteen',
  'seventeen',
  'eighteen',
  'nineteen',
  'twenty',
];
/** Small counts in words, as a period compositor would set them. */
export const inWords = (n: number) => WORDS[n] ?? String(n);

/** A four-petalled fleuron, marking a matter in good order. */
export function Fleuron() {
  return (
    <svg className="vr-fleuron" viewBox="0 0 12 12" aria-hidden>
      {[0, 90, 180, 270].map((r) => (
        <path key={r} d="M6,6 C4.6,4.6 4.8,1.6 6,0.8 C7.2,1.6 7.4,4.6 6,6 Z" transform={`rotate(${r} 6 6)`} fill={INK} />
      ))}
      <circle cx="6" cy="6" r="1.3" fill={PAPER} stroke={INK} strokeWidth="0.6" />
    </svg>
  );
}
