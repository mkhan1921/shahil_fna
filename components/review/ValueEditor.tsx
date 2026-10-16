'use client';

/**
 * The typed editor (spec §11.2 "Edit value"): a point, a range (lowest, most likely, highest) or
 * options with weights, in the units the adviser thinks in — rand, percent, ages, dates — for the
 * field's own type. Annotate mode adds the p10 / p50 / p90 an annotator gives for a vague value
 * (§15.2). Enter saves, Escape cancels.
 */
import { Plus, X } from 'lucide-react';
import { useId, useState, type KeyboardEvent } from 'react';
import type { Quantiles } from '@/lib/review/annotate';
import { categoryLabel, type SlotDef } from '@/lib/review/catalog';
import { editorModes, editorProblems, inputKind, onlyWarnings, type EditorMode, type EditorValue, type Scalar } from '@/lib/review/values';
import { Button, Segmented, cx } from '../ui';

const MODE_LABEL: Record<EditorMode, string> = { point: 'One value', range: 'Range', options: 'Options' };

type Row = { value: Scalar; weight: string };

const str = (v: unknown) => (v === null || v === undefined ? '' : String(v));

function ScalarInput({ slot, value, onChange, label, autoFocus }: { slot: SlotDef; value: Scalar; onChange: (v: Scalar) => void; label: string; autoFocus?: boolean }) {
  const kind = inputKind(slot);
  const base = 'control h-8 min-h-0 px-2 py-1 text-[13px] tabular';
  if (kind === 'boolean')
    return (
      <Segmented
        size="sm"
        value={value === true ? 'yes' : value === false ? 'no' : ''}
        onChange={(v) => onChange(v === 'yes')}
        options={[
          { value: 'yes', label: 'Yes' },
          { value: 'no', label: 'No' },
        ]}
      />
    );
  if (kind === 'category')
    return (
      <select aria-label={label} autoFocus={autoFocus} className={cx(base, 'pr-7')} value={str(value)} onChange={(e) => onChange(e.target.value)}>
        <option value="" disabled>
          Choose…
        </option>
        {(slot.categories ?? []).map((c) => (
          <option key={c} value={c}>
            {categoryLabel(slot, c)}
          </option>
        ))}
      </select>
    );
  if (kind === 'text')
    return <textarea aria-label={label} autoFocus={autoFocus} rows={2} className="control min-h-0 px-2 py-1 text-[13px]" value={str(value)} onChange={(e) => onChange(e.target.value)} />;
  if (kind === 'date') return <input aria-label={label} autoFocus={autoFocus} type="date" max="2100-12-31" className={base} value={str(value)} onChange={(e) => onChange(e.target.value)} />;
  const affix = kind === 'amount' ? 'R' : kind === 'percent' ? '%' : '';
  return (
    <span className="relative block">
      {kind === 'amount' && <span className="pointer-events-none absolute inset-y-0 left-2 flex items-center text-[12px] text-muted">R</span>}
      <input
        aria-label={label}
        autoFocus={autoFocus}
        inputMode="decimal"
        autoComplete="off"
        className={cx(base, kind === 'amount' && 'pl-5', kind === 'percent' && 'pr-6')}
        value={str(value)}
        onChange={(e) => onChange(e.target.value.replace(/[^\d.,\-\s]/g, ''))}
      />
      {kind === 'percent' && <span className="pointer-events-none absolute inset-y-0 right-2 flex items-center text-[12px] text-muted">{affix}</span>}
    </span>
  );
}

const firstOptionValue = (slot: SlotDef): Scalar => (inputKind(slot) === 'boolean' ? true : inputKind(slot) === 'category' ? (slot.categories?.[0] ?? '') : '');

export default function ValueEditor({
  slot,
  initial,
  onSave,
  onCancel,
  saveLabel = 'Save',
  withQuantiles = false,
  initialQuantiles,
  autoFocus = true,
}: {
  slot: SlotDef;
  initial: EditorValue;
  onSave: (value: EditorValue, quantiles?: Quantiles) => void;
  onCancel: () => void;
  saveLabel?: string;
  /** Annotate mode: a range needs the annotator's p10 / p50 / p90. */
  withQuantiles?: boolean;
  initialQuantiles?: Quantiles;
  autoFocus?: boolean;
}) {
  const id = useId();
  const modes = editorModes(slot);
  const [mode, setMode] = useState<EditorMode>(modes.includes(initial.mode) ? initial.mode : modes[0]);
  const [point, setPoint] = useState<Scalar>(initial.mode === 'point' ? initial.value : firstOptionValue(slot));
  const [range, setRange] = useState({
    low: initial.mode === 'range' ? str(initial.low) : '',
    likely: initial.mode === 'range' ? str(initial.likely) : '',
    high: initial.mode === 'range' ? str(initial.high) : '',
  });
  const [q, setQ] = useState<Quantiles>(initialQuantiles ?? { p10: '', p50: '', p90: '' });
  const [rows, setRows] = useState<Row[]>(
    initial.mode === 'options' ? initial.options.map((o) => ({ value: o.value, weight: str(o.weight) })) : [{ value: initial.mode === 'point' ? initial.value : firstOptionValue(slot), weight: '100' }],
  );

  const value: EditorValue =
    mode === 'point'
      ? { mode, value: point }
      : mode === 'range'
        ? withQuantiles
          ? { mode, low: range.low === '' ? q.p10 : range.low, likely: q.p50, high: range.high === '' ? q.p90 : range.high }
          : { mode, ...range }
        : { mode, options: rows.map((r) => ({ value: r.value, weight: Number(r.weight) || 0 })) };
  const problems = editorProblems(slot, value);
  const quantileProblem =
    withQuantiles && mode === 'range' && [q.p10, q.p50, q.p90].some((x) => String(x).trim() === '') ? 'Give the p10, p50 and p90 you believe the client meant.' : null;
  const blocking = !!quantileProblem || (problems.length > 0 && !onlyWarnings(problems));

  const save = () => {
    if (blocking) return;
    const v: EditorValue = withQuantiles && mode === 'range' ? { mode, low: range.low, likely: q.p50, high: range.high } : value;
    onSave(v, withQuantiles && mode === 'range' ? q : undefined);
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      onCancel();
    } else if (e.key === 'Enter' && !(e.target instanceof HTMLTextAreaElement && !e.ctrlKey) && !(e.target instanceof HTMLButtonElement)) {
      e.preventDefault();
      e.stopPropagation();
      save();
    }
  };
  const total = rows.reduce((s, r) => s + (Number(r.weight) || 0), 0);
  const numeric = !['category', 'boolean', 'text'].includes(inputKind(slot));

  return (
    <div className="space-y-2" onKeyDown={onKey}>
      {modes.length > 1 && <Segmented size="sm" value={mode} onChange={setMode} options={modes.map((m) => ({ value: m, label: MODE_LABEL[m] }))} />}
      {mode === 'point' && <ScalarInput slot={slot} value={point} onChange={setPoint} label="Value" autoFocus={autoFocus} />}
      {mode === 'range' && !withQuantiles && (
        <div className="grid grid-cols-3 gap-1.5">
          {(['low', 'likely', 'high'] as const).map((k, i) => (
            <label key={k} className="block text-[10.5px] font-medium text-muted" htmlFor={`${id}-${k}`}>
              {k === 'low' ? 'Lowest' : k === 'likely' ? 'Most likely' : 'Highest'}
              <ScalarInput slot={slot} value={range[k]} onChange={(v) => setRange((r) => ({ ...r, [k]: String(v) }))} label={k} autoFocus={autoFocus && i === 0} />
            </label>
          ))}
        </div>
      )}
      {mode === 'range' && withQuantiles && (
        <div className="space-y-1.5">
          <div className="grid grid-cols-3 gap-1.5">
            {(['p10', 'p50', 'p90'] as const).map((k, i) => (
              <label key={k} className="block text-[10.5px] font-medium text-muted">
                {k} {k === 'p50' ? '(most likely)' : k === 'p10' ? '(low)' : '(high)'}
                <ScalarInput slot={slot} value={String(q[k])} onChange={(v) => setQ((x) => ({ ...x, [k]: String(v) }))} label={k} autoFocus={autoFocus && i === 0} />
              </label>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            {(['low', 'high'] as const).map((k) => (
              <label key={k} className="block text-[10.5px] font-medium text-muted">
                {k === 'low' ? 'Lowest as said (optional)' : 'Highest as said (optional)'}
                <ScalarInput slot={slot} value={range[k]} onChange={(v) => setRange((r) => ({ ...r, [k]: String(v) }))} label={`${k} as said`} />
              </label>
            ))}
          </div>
        </div>
      )}
      {mode === 'options' && (
        <div className="space-y-1">
          <div className="grid grid-cols-[1fr_5rem_1.5rem] gap-1.5 text-[10.5px] font-medium text-muted">
            <span>{numeric ? 'Value' : 'Option'}</span>
            <span>Weight %</span>
            <span />
          </div>
          {rows.map((r, i) => (
            <div key={i} className="grid grid-cols-[1fr_5rem_1.5rem] items-center gap-1.5">
              <ScalarInput slot={slot} value={r.value} onChange={(v) => setRows((rs) => rs.map((x, j) => (j === i ? { ...x, value: v } : x)))} label={`Option ${i + 1}`} autoFocus={autoFocus && i === 0} />
              <input
                aria-label={`Weight of option ${i + 1}`}
                inputMode="decimal"
                className="control h-8 min-h-0 px-2 py-1 text-[13px] tabular"
                value={r.weight}
                onChange={(e) => setRows((rs) => rs.map((x, j) => (j === i ? { ...x, weight: e.target.value.replace(/[^\d.]/g, '') } : x)))}
              />
              <button type="button" onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))} disabled={rows.length === 1} className="rounded p-1 text-faint hover:bg-critical-soft hover:text-critical-ink disabled:opacity-30" aria-label={`Remove option ${i + 1}`}>
                <X size={13} />
              </button>
            </div>
          ))}
          <div className="flex items-center justify-between">
            <button type="button" onClick={() => setRows((rs) => [...rs, { value: firstOptionValue(slot), weight: '' }])} className="inline-flex items-center gap-1 rounded px-1 text-[11.5px] font-semibold text-brand-2 hover:bg-brand-soft">
              <Plus size={12} /> Option
            </button>
            <span className={cx('text-[10.5px] tabular', Math.abs(total - 100) > 0.5 ? 'text-warning-ink' : 'text-muted')}>{total ? `${total}% in all${Math.abs(total - 100) > 0.5 ? ' — scaled to 100%' : ''}` : ''}</span>
          </div>
        </div>
      )}
      {(quantileProblem || problems.length > 0) && (
        <ul className="space-y-0.5 text-[11.5px] leading-4">
          {quantileProblem && <li className="text-critical-ink">{quantileProblem}</li>}
          {problems.map((p) => (
            <li key={p} className={onlyWarnings([p]) ? 'text-warning-ink' : 'text-critical-ink'}>
              {p}
            </li>
          ))}
        </ul>
      )}
      <div className="flex justify-end gap-1.5">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" onClick={save} disabled={blocking}>
          {saveLabel}
        </Button>
      </div>
    </div>
  );
}
