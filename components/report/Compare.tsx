import type { Line, NeedResult, Status } from '@/lib/fna/analysis';
import { money, moneyCompact } from '@/lib/format';
import { cx } from '../ui';

const FILL: Record<Status, string> = {
  covered: 'var(--color-good)',
  partial: 'var(--color-warning)',
  shortfall: 'var(--color-critical)',
  na: 'var(--color-line-strong)',
};
const TRACK: Record<Status, string> = {
  covered: 'var(--color-good-soft)',
  partial: 'var(--color-warning-soft)',
  shortfall: 'var(--color-critical-soft)',
  na: 'var(--color-canvas)',
};
const WORD: Record<Status, string> = { covered: 'Covered', partial: 'Partial', shortfall: 'Shortfall', na: 'No need' };

export interface GlanceCell {
  need: number;
  provision: number;
  shortfall: number;
  status: Status;
  monthly?: boolean;
}

/** One compact cell: meter + "provided of need" + shortfall. */
function Cell({ c }: { c?: GlanceCell }) {
  if (!c) return <td className="px-2 py-1.5 text-muted">—</td>;
  const ratio = c.need > 0 ? Math.min(1, c.provision / c.need) : 1;
  const f = (n: number) => (c.monthly ? money(n) : moneyCompact(n));
  return (
    <td className="px-2 py-1.5 align-middle last:pr-0">
      <div className="flex items-center gap-2">
        <div className="h-[5px] w-16 shrink-0 overflow-hidden rounded-full" style={{ background: TRACK[c.status] }} aria-hidden>
          <div className="h-full rounded-full" style={{ width: `${c.status === 'na' ? 0 : ratio * 100}%`, background: FILL[c.status] }} />
        </div>
        <span className="text-[10.5px] text-muted tabular">
          {c.status === 'na' ? 'No need' : `${f(c.provision)} of ${f(c.need)}${c.monthly ? ' p.m.' : ''}`}
        </span>
      </div>
    </td>
  );
}

function Gap({ c }: { c?: GlanceCell }) {
  if (!c) return <td className="px-2 py-1.5" />;
  return (
    <td className="px-2 py-1.5 text-right align-middle tabular last:pr-0">
      <span className={cx('text-[11px] font-semibold', c.shortfall > 0.5 ? 'text-critical-ink' : 'text-good-ink')}>
        {c.shortfall > 0.5 ? `−${c.monthly ? money(c.shortfall) : moneyCompact(c.shortfall)}` : WORD[c.status]}
      </span>
    </td>
  );
}

/** Needs-at-a-glance matrix: one row per need, one column group per person. */
export function GlanceTable({ names, rows }: { names: string[]; rows: { label: string; cells: (GlanceCell | undefined)[]; household?: boolean }[] }) {
  return (
    <table className="w-full border-collapse text-[11px]">
      <thead>
        <tr className="border-b border-line-strong text-[9.5px] uppercase tracking-wide text-muted">
          <th className="py-1.5 pr-2 text-left font-semibold">Need</th>
          {names.map((n) => (
            <th key={n} colSpan={2} className="px-2 py-1.5 text-left font-semibold">
              {n}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label} className="border-b border-line/70">
            <td className="py-1.5 pr-2 font-medium text-ink">{r.label}</td>
            {r.household ? (
              <>
                <Cell c={r.cells[0]} />
                <Gap c={r.cells[0]} />
                {names.length > 1 && <td colSpan={2 * (names.length - 1)} className="px-2 text-[10px] text-muted">Household</td>}
              </>
            ) : (
              r.cells.map((c, i) => [<Cell key={`c${i}`} c={c} />, <Gap key={`g${i}`} c={c} />])
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const baseLabel = (l: string) => l.replace(/\s*\([^)]*\)\s*$/, '').trim();

/**
 * Side-by-side need calculation for several lives. Lines are merged by label
 * (parenthetical detail such as "(16 yrs)" is dropped for alignment).
 */
export function NeedCompare({ names, needs, monthly = false }: { names: string[]; needs: NeedResult[]; monthly?: boolean }) {
  const merge = (pick: (n: NeedResult) => Line[]) => {
    const order: string[] = [];
    const map = new Map<string, (number | undefined)[]>();
    needs.forEach((n, i) => {
      for (const l of pick(n)) {
        const key = baseLabel(l.label);
        if (!map.has(key)) {
          map.set(key, needs.map(() => undefined));
          order.push(key);
        }
        map.get(key)![i] = (map.get(key)![i] ?? 0) + l.amount;
      }
    });
    return order.map((k) => ({ label: k, values: map.get(k)! }));
  };
  const req = merge((n) => n.needLines);
  const prov = merge((n) => n.provisionLines);
  const f = (v?: number) => (v === undefined ? '—' : money(v));
  const cols = names.length;
  return (
    <table className="w-full border-collapse text-[11px]">
      <thead>
        <tr className="border-b border-line-strong text-[9.5px] uppercase tracking-wide text-muted">
          <th className="py-1.5 pr-2 text-left font-semibold">{monthly ? 'Per month' : 'Capital'}</th>
          {names.map((n) => (
            <th key={n} className="w-[24%] px-2 py-1.5 text-right font-semibold last:pr-0">
              {n}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="tabular">
        {req.map((r) => (
          <tr key={`r-${r.label}`} className="border-b border-line/60">
            <td className="py-1 pr-2 text-ink-2">{r.label}</td>
            {r.values.map((v, i) => (
              <td key={i} className="px-2 py-1 text-right text-ink last:pr-0">
                {f(v)}
              </td>
            ))}
          </tr>
        ))}
        <tr className="border-b border-line-strong font-semibold">
          <td className="py-1 pr-2">Total required</td>
          {needs.map((n, i) => (
            <td key={i} className="px-2 py-1 text-right last:pr-0">
              {money(n.need)}
            </td>
          ))}
        </tr>
        {prov.length === 0 ? (
          <tr className="border-b border-line/60">
            <td className="py-1 pr-2 text-ink-2">Existing provision</td>
            {needs.map((_, i) => (
              <td key={i} className="px-2 py-1 text-right text-muted last:pr-0">
                None
              </td>
            ))}
          </tr>
        ) : (
          prov.map((r) => (
            <tr key={`p-${r.label}`} className="border-b border-line/60">
              <td className="py-1 pr-2 text-ink-2">Less: {r.label.charAt(0).toLowerCase() + r.label.slice(1)}</td>
              {r.values.map((v, i) => (
                <td key={i} className="px-2 py-1 text-right text-ink last:pr-0">
                  {v === undefined ? '—' : money(-v)}
                </td>
              ))}
            </tr>
          ))
        )}
        <tr className="font-semibold">
          <td className="py-1.5 pr-2">{cols > 1 ? 'Shortfall / (surplus)' : 'Shortfall / (surplus)'}</td>
          {needs.map((n, i) => (
            <td key={i} className={cx('px-2 py-1.5 text-right last:pr-0', n.shortfall > 0.5 ? 'text-critical-ink' : 'text-good-ink')}>
              {n.shortfall > 0.5 ? money(n.shortfall) : `(${money(n.provision - n.need)})`}
            </td>
          ))}
        </tr>
      </tbody>
    </table>
  );
}

/** Generic label × person table. */
export function CompareRows({
  names,
  rows,
  strongRows = [],
}: {
  names: string[];
  rows: { label: string; values: React.ReactNode[]; tone?: 'critical' | 'good' }[];
  strongRows?: string[];
}) {
  return (
    <table className="w-full border-collapse text-[11px]">
      <thead>
        <tr className="border-b border-line-strong text-[9.5px] uppercase tracking-wide text-muted">
          <th className="py-1.5 pr-2 text-left font-semibold" />
          {names.map((n) => (
            <th key={n} className="w-[26%] px-2 py-1.5 text-right font-semibold last:pr-0">
              {n}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="tabular">
        {rows.map((r) => (
          <tr key={r.label} className={cx('border-b border-line/60', strongRows.includes(r.label) && 'font-semibold')}>
            <td className={cx('py-1 pr-2', strongRows.includes(r.label) ? 'text-ink' : 'text-ink-2')}>{r.label}</td>
            {r.values.map((v, i) => (
              <td key={i} className={cx('px-2 py-1 text-right last:pr-0', r.tone === 'critical' ? 'text-critical-ink' : r.tone === 'good' ? 'text-good-ink' : 'text-ink')}>
                {v}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
