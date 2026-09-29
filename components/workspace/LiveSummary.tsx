'use client';

import type { Analysis, NeedResult } from '@/lib/fna/analysis';
import { money, moneyCompact, pct } from '@/lib/format';
import { Meter } from '../charts';
import { StatusPill, cx } from '../ui';
import type { StepKey } from './types';

function NeedRow({ need, onClick }: { need: NeedResult; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="focus-ring block w-full rounded-md px-2 py-2 text-left hover:bg-wash">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="truncate text-[13px] font-medium text-ink">{need.title}</span>
        <StatusPill status={need.status} />
      </div>
      <Meter value={need.provision} total={need.need} status={need.status} />
      <div className="mt-1 flex justify-between text-[11px] text-muted tabular">
        <span>
          Need {need.monthly ? `${money(need.need)} p.m.` : moneyCompact(need.need)}
        </span>
        <span>{need.shortfall > 0 ? `Short ${need.monthly ? money(need.shortfall) : moneyCompact(need.shortfall)}` : 'Fully provided'}</span>
      </div>
    </button>
  );
}

export default function LiveSummary({ analysis, go }: { analysis: Analysis; go: (s: StepKey) => void }) {
  const cf = analysis.cashflow;
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-line bg-surface p-4">
        <div className="text-xs font-medium text-muted">Monthly cash flow</div>
        <div className={cx('mt-1 text-2xl font-semibold tracking-tight', cf.surplus < 0 ? 'text-critical-ink' : 'text-ink')}>
          {money(cf.surplus, { sign: true })}
        </div>
        <div className="mt-0.5 text-xs text-muted">
          {cf.takeHome > 0 ? `${cf.surplus < 0 ? 'Deficit' : 'Surplus'} on ${money(cf.takeHome)} take-home` : 'Capture income and expenses'}
        </div>
      </div>

      {analysis.people.map((k) => {
        const p = analysis.persons[k];
        if (!p) return null;
        return (
          <div key={k} className="rounded-xl border border-line bg-surface p-2">
            <div className="px-2 pb-1 pt-1.5 text-xs font-semibold uppercase tracking-wide text-muted">{p.name}</div>
            <NeedRow need={p.death} onClick={() => go('analysis')} />
            <NeedRow need={p.incomeProtection} onClick={() => go('analysis')} />
            <NeedRow need={p.disability} onClick={() => go('analysis')} />
            <NeedRow need={p.severeIllness} onClick={() => go('analysis')} />
            <button type="button" onClick={() => go('analysis')} className="focus-ring block w-full rounded-md px-2 py-2 text-left hover:bg-wash">
              <div className="mb-1.5 flex items-center justify-between gap-2">
                <span className="text-[13px] font-medium text-ink">Retirement</span>
                <StatusPill status={p.retirement.status} />
              </div>
              <Meter value={p.retirement.funded} total={1} status={p.retirement.status} />
              <div className="mt-1 flex justify-between text-[11px] text-muted tabular">
                <span>{pct(p.retirement.replacementRatio)} of income</span>
                <span>{p.retirement.additionalMonthly > 0 ? `+${money(p.retirement.additionalMonthly)} p.m.` : 'On track'}</span>
              </div>
            </button>
          </div>
        );
      })}

      <div className="rounded-xl border border-line bg-surface p-4">
        <div className="mb-2 flex justify-between text-xs text-muted">
          <span className="font-medium">FNA completeness</span>
          <span className="tabular">{pct(analysis.completenessScore)}</span>
        </div>
        <ul className="space-y-1">
          {analysis.completeness.map((c) => (
            <li key={c.section} className="flex items-center gap-2 text-xs">
              <span className={cx('h-1.5 w-1.5 shrink-0 rounded-full', c.complete ? 'bg-good' : 'bg-line-strong')} aria-hidden />
              <span className={c.complete ? 'text-ink-2' : 'text-muted'}>{c.section}</span>
              <span className="sr-only">{c.complete ? 'complete' : 'incomplete'}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
