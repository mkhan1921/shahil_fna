import type { ReactNode } from 'react';
import { cx } from '../ui';

export function ReportSection({
  id,
  number,
  title,
  intro,
  children,
  breakBefore = true,
}: {
  id?: string;
  number?: string;
  title: string;
  intro?: ReactNode;
  children: ReactNode;
  breakBefore?: boolean;
}) {
  return (
    <section id={id} className={cx('report-section', breakBefore && 'print-break')}>
      <header className="mb-5 border-b-2 pb-2" style={{ borderColor: 'var(--report-brand)', breakAfter: 'avoid' }}>
        {number && <div className="text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--report-brand)' }}>Section {number}</div>}
        <h2 className="font-serif text-[22px] font-semibold leading-tight text-ink">{title}</h2>
      </header>
      {intro && <p className="mb-5 max-w-[46rem] text-[12.5px] leading-[1.6] text-ink-2">{intro}</p>}
      <div className="space-y-6">{children}</div>
    </section>
  );
}

export function SubHeading({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-2.5 flex items-baseline justify-between gap-4 border-b border-line pb-1.5" style={{ breakAfter: 'avoid' }}>
      <h3 className="font-serif text-[15px] font-semibold text-ink">{children}</h3>
      {aside && <div className="text-[11px] text-muted">{aside}</div>}
    </div>
  );
}

export function KV({ rows, cols = 2 }: { rows: [ReactNode, ReactNode][]; cols?: 1 | 2 | 3 }) {
  return (
    <dl className={cx('grid gap-x-8 text-[12px]', cols === 1 ? 'grid-cols-1' : cols === 2 ? 'grid-cols-2' : 'grid-cols-3')}>
      {rows.map(([k, v], i) => (
        <div key={i} className="flex justify-between gap-3 border-b border-line/70 py-1.5">
          <dt className="text-muted">{k}</dt>
          <dd className="text-right font-medium text-ink tabular">{v || '—'}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Table({
  head,
  rows,
  align,
  foot,
  className,
}: {
  head: ReactNode[];
  rows: ReactNode[][];
  align?: ('l' | 'r' | 'c')[];
  foot?: ReactNode[];
  className?: string;
}) {
  const a = (i: number) => (align?.[i] === 'r' ? 'text-right' : align?.[i] === 'c' ? 'text-center' : 'text-left');
  return (
    <table className={cx('w-full border-collapse text-[11.5px]', className)}>
      <thead>
        <tr className="border-b border-line-strong text-[10px] uppercase tracking-wide text-muted">
          {head.map((h, i) => (
            <th key={i} className={cx('px-2 py-1.5 font-semibold first:pl-0 last:pr-0', a(i))}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="tabular">
        {rows.map((r, ri) => (
          <tr key={ri} className="avoid-break border-b border-line/70">
            {r.map((c, ci) => (
              <td key={ci} className={cx('px-2 py-1.5 align-top first:pl-0 last:pr-0', a(ci), ci === 0 ? 'text-ink-2' : 'text-ink')}>
                {c}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
      {foot && (
        <tfoot>
          <tr className="border-t-2 border-line-strong font-semibold tabular">
            {foot.map((c, i) => (
              <td key={i} className={cx('px-2 py-1.5 first:pl-0 last:pr-0', a(i))}>
                {c}
              </td>
            ))}
          </tr>
        </tfoot>
      )}
    </table>
  );
}

export function Note({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'warning' | 'critical' }) {
  return (
    <div
      className={cx(
        'avoid-break rounded-md border-l-[3px] px-3 py-2 text-[11px] leading-[1.55]',
        tone === 'neutral' && 'border-line-strong bg-wash text-ink-2',
        tone === 'warning' && 'border-warning bg-warning-soft text-ink-2',
        tone === 'critical' && 'border-critical bg-critical-soft text-ink-2',
      )}
    >
      {children}
    </div>
  );
}

export function Signature({ label, name, date }: { label: string; name?: string; date?: string }) {
  return (
    <div className="avoid-break">
      <div className="h-14 border-b border-ink/60" />
      <div className="mt-1.5 flex justify-between text-[11px]">
        <span className="font-medium text-ink">{label}</span>
        <span className="text-muted">Date: {date || '____________'}</span>
      </div>
      {name && <div className="text-[11px] text-muted">{name}</div>}
    </div>
  );
}
