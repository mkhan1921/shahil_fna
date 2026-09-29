import { AlertTriangle, CheckCircle2, Info, OctagonAlert } from 'lucide-react';
import type { Finding } from '@/lib/fna/analysis';
import { cx } from './ui';

const META = {
  critical: { Icon: OctagonAlert, color: 'var(--color-critical)', label: 'Critical' },
  warning: { Icon: AlertTriangle, color: 'var(--color-warning-ink)', label: 'Attention' },
  info: { Icon: Info, color: 'var(--color-brand-2)', label: 'Consider' },
  positive: { Icon: CheckCircle2, color: 'var(--color-good-ink)', label: 'Strength' },
};

export default function Findings({ findings, limit, dense = false }: { findings: Finding[]; limit?: number; dense?: boolean }) {
  const list = limit ? findings.slice(0, limit) : findings;
  if (!list.length) return <p className="text-sm text-muted">No findings — capture more information to generate insights.</p>;
  return (
    <ul className={cx('divide-y divide-line', dense ? 'text-[11.5px]' : 'text-sm')}>
      {list.map((f, i) => {
        const m = META[f.severity];
        return (
          <li key={i} className={cx('flex gap-3', dense ? 'py-1.5' : 'py-2.5')}>
            <m.Icon size={dense ? 14 : 16} className="mt-0.5 shrink-0" style={{ color: m.color }} aria-label={m.label} />
            <div className="min-w-0">
              <div className="font-medium text-ink">{f.title}</div>
              <div className={cx('text-muted', dense ? 'leading-4' : 'leading-5')}>{f.detail}</div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
