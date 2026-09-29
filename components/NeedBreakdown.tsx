import type { NeedResult } from '@/lib/fna/analysis';
import { money } from '@/lib/format';
import { cx } from './ui';

export default function NeedBreakdown({ need, dense = false }: { need: NeedResult; dense?: boolean }) {
  const suffix = need.monthly ? ' p.m.' : '';
  const cell = dense ? 'py-1' : 'py-1.5';
  return (
    <div className={cx('grid gap-x-8 gap-y-4', dense ? 'grid-cols-2 text-[11.5px]' : 'text-sm md:grid-cols-2')}>
      <table className="w-full">
        <thead>
          <tr className="border-b border-line text-left text-[11px] uppercase tracking-wide text-muted">
            <th className="pb-1.5 font-medium">Required</th>
            <th className="pb-1.5 text-right font-medium">Amount</th>
          </tr>
        </thead>
        <tbody className="tabular">
          {need.needLines.map((l) => (
            <tr key={l.label} className="border-b border-line/70 last:border-0">
              <td className={cx(cell, 'pr-3 text-ink-2')}>{l.label}</td>
              <td className={cx(cell, 'text-right text-ink')}>{money(l.amount)}</td>
            </tr>
          ))}
          <tr className="border-t border-line-strong font-semibold">
            <td className={cell}>Total required</td>
            <td className={cx(cell, 'text-right')}>
              {money(need.need)}
              {suffix}
            </td>
          </tr>
        </tbody>
      </table>
      <table className="w-full">
        <thead>
          <tr className="border-b border-line text-left text-[11px] uppercase tracking-wide text-muted">
            <th className="pb-1.5 font-medium">Existing provision</th>
            <th className="pb-1.5 text-right font-medium">Amount</th>
          </tr>
        </thead>
        <tbody className="tabular">
          {need.provisionLines.length === 0 && (
            <tr>
              <td className={cx(cell, 'text-muted')} colSpan={2}>
                None
              </td>
            </tr>
          )}
          {need.provisionLines.map((l) => (
            <tr key={l.label} className="border-b border-line/70 last:border-0">
              <td className={cx(cell, 'pr-3 text-ink-2')}>{l.label}</td>
              <td className={cx(cell, 'text-right text-ink')}>{money(l.amount)}</td>
            </tr>
          ))}
          <tr className="border-t border-line-strong font-semibold">
            <td className={cell}>Total provision</td>
            <td className={cx(cell, 'text-right')}>
              {money(need.provision)}
              {suffix}
            </td>
          </tr>
          <tr className={cx('font-semibold', need.shortfall > 0.5 ? 'text-critical-ink' : 'text-good-ink')}>
            <td className={cell}>{need.shortfall > 0.5 ? 'Shortfall' : 'Surplus'}</td>
            <td className={cx(cell, 'text-right')}>
              {money(need.shortfall > 0.5 ? need.shortfall : need.provision - need.need)}
              {suffix}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
