import type { ReportStyle } from '@/lib/fna/types';

/** A thumbnail of the first page of each report style, for the style picker in Settings. */
export default function StylePreview({ style, accent }: { style: ReportStyle; accent: string }) {
  return (
    <svg viewBox="0 0 96 128" className="h-32 w-24 shrink-0 rounded-[3px] shadow-[0_1px_4px_rgba(16,24,40,0.22)]" aria-hidden>
      {style === 'vintage' ? <Vintage /> : <Standard accent={accent} />}
    </svg>
  );
}

function Standard({ accent }: { accent: string }) {
  const bars: [number, string][] = [
    [0.64, '#fab219'],
    [0.4, '#d03b3b'],
    [1, '#0ca30c'],
    [0.83, '#fab219'],
  ];
  return (
    <>
      <rect width="96" height="128" fill="#fff" />
      <rect x="10" y="10" width="30" height="2.2" rx="1" fill={accent} />
      <rect x="10" y="15" width="62" height="4" rx="1" fill="#0b1b2b" />
      <rect x="10" y="21.5" width="40" height="2" rx="1" fill="#98a4b3" />
      <rect x="10" y="27" width="76" height="0.8" fill={accent} />
      {[0, 1, 2, 3].map((i) => (
        <g key={i}>
          <rect x={10 + i * 19.5} y="32" width="17" height="11" rx="1.5" fill="none" stroke="#cbd4de" strokeWidth="0.6" />
          <rect x={12.5 + i * 19.5} y="35" width="8" height="1.4" rx="0.7" fill="#98a4b3" />
          <rect x={12.5 + i * 19.5} y="38.2" width="11" height="2.2" rx="0.7" fill="#0b1b2b" />
        </g>
      ))}
      {bars.map(([f, c], i) => (
        <g key={i}>
          <rect x="10" y={50 + i * 7} width="20" height="1.8" rx="0.9" fill="#98a4b3" />
          <rect x="34" y={49.5 + i * 7} width="52" height="3" rx="1.5" fill="#f4f6f8" />
          <rect x="34" y={49.5 + i * 7} width={52 * f} height="3" rx="1.5" fill={c} />
        </g>
      ))}
      {[82, 86, 90, 94, 98, 102].map((y, i) => (
        <rect key={y} x="10" y={y} width={i % 3 === 2 ? 50 : 76} height="1.6" rx="0.8" fill="#cbd4de" />
      ))}
      <rect x="10" y="110" width="76" height="8" rx="1.5" fill="#f8fafb" stroke="#e3e8ee" strokeWidth="0.6" />
    </>
  );
}

function Vintage() {
  const ink = '#2a1d14';
  const faint = '#8a735c';
  const red = '#9a2b1d';
  const beakers = [0.64, 0.63, 0.19, 1];
  return (
    <>
      <rect width="96" height="128" fill="#f4ead8" />
      <rect width="96" height="128" fill="none" stroke="#d9c6a6" strokeWidth="2" />
      <text x="48" y="13" textAnchor="middle" fontSize="3.4" letterSpacing="0.8" fill={faint} fontFamily="Georgia, 'Times New Roman', serif">
        CHAPTER THE FIRST
      </text>
      <text x="48" y="21" textAnchor="middle" fontSize="7" fill={ink} fontFamily="Georgia, 'Times New Roman', serif">
        The Verdict
      </text>
      <path d="M30 25.5h13M45 25.5h6M53 25.5h13" stroke={ink} strokeWidth="0.5" />
      <text x="10" y="37.5" fontSize="11" fill={ink} fontFamily="Georgia, 'Times New Roman', serif">
        O
      </text>
      {[31, 35, 39, 43].map((y, i) => (
        <line key={y} x1={i < 2 ? 19 : 10} x2={i === 3 ? 62 : 86} y1={y} y2={y} stroke={faint} strokeWidth="0.7" />
      ))}
      <path d="M10 48h76M10 57h76" stroke={ink} strokeWidth="0.6" />
      {[0, 1, 2].map((i) => (
        <line key={i} x1={29 + i * 19} x2={29 + i * 19} y1="48" y2="57" stroke={ink} strokeWidth="0.4" />
      ))}
      {beakers.map((f, i) => {
        const x = 12 + i * 19;
        const top = 64;
        const h = 16;
        const level = top + 3 + (h - 4) * (1 - f);
        return (
          <g key={i}>
            {Array.from({ length: Math.max(0, Math.floor((top + h - 1 - level) / 1.3)) }, (_, k) => (
              <line key={k} x1={x + 0.8} x2={x + 10.2} y1={top + h - 1.2 - k * 1.3} y2={top + h - 1.2 - k * 1.3} stroke={faint} strokeWidth="0.4" />
            ))}
            <path d={`M${x - 1.2} ${top}l1.2 1.2v${h - 2.4}q0 1.2 1.2 1.2h8.6q1.2 0 1.2-1.2v-${h - 2.4}l1.2-1.2`} fill="none" stroke={ink} strokeWidth="0.6" />
            <rect x={x + 1.5} y={top + h + 3} width="8" height="1.6" fill={f < 0.6 ? red : ink} />
          </g>
        );
      })}
      {[94, 101, 108].map((y, i) => (
        <g key={y}>
          <path d={`M10 ${y - 1.4}h3.2l0.8 0.5h4.4M10 ${y + 0.4}h4`} stroke={i < 2 ? red : ink} strokeWidth="0.6" fill="none" />
          <line x1="21" x2={i === 1 ? 70 : 86} y1={y} y2={y} stroke={faint} strokeWidth="0.7" />
        </g>
      ))}
      <text x="48" y="121" textAnchor="middle" fontSize="3.4" fill={faint} fontFamily="Georgia, 'Times New Roman', serif">
        — 2 —
      </text>
    </>
  );
}
