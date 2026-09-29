'use client';

import type { Analysis, NeedResult, Status } from '@/lib/fna/analysis';
import { money, moneyCompact, pct } from '@/lib/format';
import { Meter } from '../charts';
import { StatusPill, cx } from '../ui';

function Row({ title, status, value, total, left, right, onClick }: { title: string; status: Status; value: number; total: number; left: string; right: string; onClick: () => void }) {
  return (
    <button type="button" tabIndex={-1} onClick={onClick} className="block w-full px-2.5 py-1.5 text-left hover:bg-wash">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="truncate text-[12px] font-medium text-ink">{title}</span>
        <StatusPill status={status} />
      </div>
      <Meter value={value} total={total} status={status} height={4} />
      <div className="mt-0.5 flex justify-between text-[10.5px] text-muted tabular">
        <span>{left}</span>
        <span>{right}</span>
      </div>
    </button>
  );
}

const needRow = (n: NeedResult, go: () => void) => (
  <Row
    key={n.area}
    title={n.title}
    status={n.status}
    value={n.provision}
    total={n.need}
    left={`Need ${n.monthly ? money(n.need) : moneyCompact(n.need)}`}
    right={n.shortfall > 0 ? `Short ${n.monthly ? money(n.shortfall) : moneyCompact(n.shortfall)}` : n.need > 0 ? 'Covered' : '—'}
    onClick={go}
  />
);

export default function LiveSummary({ analysis, go }: { analysis: Analysis; go: (section: string) => void }) {
  const cf = analysis.cashflow;
  const toAnalysis = () => go('analysis');
  return (
    <div className="space-y-2 text-ink">
      <div className="rounded-lg border border-line bg-surface px-3 py-2">
        <div className="text-[10.5px] font-semibold uppercase tracking-wide text-muted">Monthly cash flow</div>
        <div className={cx('text-[20px] font-semibold tracking-tight tabular', cf.surplus < 0 ? 'text-critical-ink' : 'text-ink')}>{money(cf.surplus, { sign: true })}</div>
        <div className="text-[10.5px] text-muted">{cf.takeHome > 0 ? `${cf.surplus < 0 ? 'deficit' : 'surplus'} on ${money(cf.takeHome)} take-home` : 'capture income & budget'}</div>
      </div>
      {analysis.people.map((k) => {
        const p = analysis.persons[k];
        if (!p) return null;
        return (
          <div key={k} className="overflow-hidden rounded-lg border border-line bg-surface">
            <div className="border-b border-line bg-wash px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-wide text-muted">{p.name}</div>
            {needRow(p.death, toAnalysis)}
            {needRow(p.incomeProtection, toAnalysis)}
            {needRow(p.disability, toAnalysis)}
            {needRow(p.severeIllness, toAnalysis)}
            <Row
              title="Retirement"
              status={p.retirement.status}
              value={p.retirement.funded}
              total={1}
              left={`${pct(p.retirement.replacementRatio)} of income`}
              right={p.retirement.additionalMonthly > 0 ? `+${money(p.retirement.additionalMonthly)} p.m.` : 'On track'}
              onClick={toAnalysis}
            />
          </div>
        );
      })}
      <div className="rounded-lg border border-line bg-surface px-3 py-2">
        <div className="mb-1 flex justify-between text-[10.5px] font-semibold uppercase tracking-wide text-muted">
          <span>Completeness</span>
          <span className="tabular">{pct(analysis.completenessScore)}</span>
        </div>
        <div className="grid grid-cols-2 gap-x-2 gap-y-0.5">
          {analysis.completeness.map((c) => (
            <div key={c.section} className="flex items-center gap-1.5 text-[10.5px]">
              <span className={cx('h-1.5 w-1.5 shrink-0 rounded-full', c.complete ? 'bg-good' : 'bg-line-strong')} aria-hidden />
              <span className={cx('truncate', c.complete ? 'text-ink-2' : 'text-muted')}>{c.section}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
