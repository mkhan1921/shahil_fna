'use client';

/**
 * Dense, keyboard-first "sheet" primitives.
 *
 * Every field is a bordered cell with a tiny label above a borderless input,
 * laid out in a 12-column flex grid separated by 1px hairlines. Rows always
 * fill the full width (cells grow to absorb leftover space), so conditional
 * cells never leave gaps.
 */
import { Plus, X } from 'lucide-react';
import { useState, type CSSProperties, type ReactNode } from 'react';
import { cx } from '../ui';

const cellStyle = (span: number): CSSProperties =>
  ({ ['--b' as string]: `${(span / 12) * 100}%`, ['--g' as string]: span }) as CSSProperties;

const input = 'w-full min-w-0 bg-transparent text-[13px] leading-5 text-ink outline-none placeholder:text-faint';

/* ------------------------------------------------------------------ */
/* Layout                                                              */
/* ------------------------------------------------------------------ */

export function Section({
  id,
  title,
  right,
  children,
  note,
}: {
  id: string;
  title: string;
  right?: ReactNode;
  note?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={id} data-section className="scroll-mt-14 border-b border-line-strong last:border-b-0">
      <header className="flex min-h-9 flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-line bg-[#eef2f6] px-3 py-1.5">
        <h2 className="text-[11.5px] font-bold uppercase tracking-[0.08em] text-ink">{title}</h2>
        {right && <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] text-ink-2">{right}</div>}
      </header>
      {note && <div className="border-b border-line bg-wash px-3 py-1.5 text-[11.5px] leading-4 text-muted">{note}</div>}
      {children}
    </section>
  );
}

/** A thin sub-heading row inside a section. */
export function Sub({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line bg-wash px-3 py-1">
      <div className="text-[10.5px] font-semibold uppercase tracking-[0.07em] text-ink-2">{children}</div>
      {right && <div className="flex items-center gap-3 text-[11px] text-muted">{right}</div>}
    </div>
  );
}

/** Row container: cells wrap onto new lines and always fill the width. */
export function Cells({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('sheet-cells flex flex-wrap gap-px border-b border-line bg-line', className)}>{children}</div>;
}

/** Two blocks side by side (e.g. client | spouse), stacking on small screens. */
export function Pair({ children }: { children: ReactNode }) {
  const count = (Array.isArray(children) ? children : [children]).filter(Boolean).length;
  return <div className={cx('grid gap-px bg-line-strong', count > 1 && 'lg:grid-cols-2')}>{children}</div>;
}

export function PairHead({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-line bg-brand-soft/60 px-3 py-1">
      <span className="text-[11px] font-semibold text-brand">{children}</span>
      {right && <span className="text-[11px] text-ink-2">{right}</span>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Cells                                                               */
/* ------------------------------------------------------------------ */

function Cell({ label, span = 3, children, hint, tone, className }: { label: ReactNode; span?: number; children: ReactNode; hint?: ReactNode; tone?: 'error'; className?: string }) {
  return (
    <label
      className={cx(
        'sheet-cell relative flex min-h-[40px] min-w-0 flex-col justify-center bg-white px-2 pb-1 pt-[3px] focus-within:bg-[#eaf3fb] focus-within:shadow-[inset_2px_0_0_var(--color-brand-2)]',
        tone === 'error' && 'bg-critical-soft',
        className,
      )}
      style={cellStyle(span)}
    >
      <span className="flex items-baseline justify-between gap-2 truncate text-[9.5px] font-semibold uppercase leading-3 tracking-[0.05em] text-muted">
        <span className="truncate">{label}</span>
        {hint && <span className="shrink-0 font-normal normal-case tracking-normal text-faint">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

export function TextC({
  label,
  value,
  onChange,
  span,
  placeholder,
  type = 'text',
  hint,
  error,
  maxLength,
  inputMode,
  id,
}: {
  label: ReactNode;
  value: string;
  onChange: (v: string) => void;
  span?: number;
  placeholder?: string;
  type?: string;
  hint?: ReactNode;
  error?: boolean;
  maxLength?: number;
  inputMode?: 'numeric' | 'text' | 'email' | 'tel';
  id?: string;
}) {
  return (
    <Cell label={label} span={span} hint={hint} tone={error ? 'error' : undefined}>
      <input id={id} type={type} className={input} value={value ?? ''} placeholder={placeholder} maxLength={maxLength} inputMode={inputMode} onChange={(e) => onChange(e.target.value)} />
    </Cell>
  );
}

export function AreaC({ label, value, onChange, span = 12, placeholder, rows = 1 }: { label: ReactNode; value: string; onChange: (v: string) => void; span?: number; placeholder?: string; rows?: number }) {
  return (
    <Cell label={label} span={span}>
      <textarea
        className={cx(input, 'resize-y py-0')}
        rows={Math.max(rows, (value?.split('\n').length ?? 1) || 1)}
        value={value ?? ''}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </Cell>
  );
}

export function DateC({ label, value, onChange, span = 2, hint, error }: { label: ReactNode; value: string; onChange: (v: string) => void; span?: number; hint?: ReactNode; error?: boolean }) {
  return (
    <Cell label={label} span={span} hint={hint} tone={error ? 'error' : undefined}>
      <input type="date" max="2100-12-31" className={cx(input, 'tabular')} value={value ?? ''} onChange={(e) => onChange(e.target.value)} />
    </Cell>
  );
}

export function SelectC<T extends string>({
  label,
  value,
  onChange,
  options,
  span = 2,
  placeholder,
  hint,
}: {
  label: ReactNode;
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[] | Record<T, string>;
  span?: number;
  placeholder?: string;
  hint?: ReactNode;
}) {
  const list = Array.isArray(options) ? options : (Object.entries(options) as [T, string][]).map(([v, l]) => ({ value: v, label: l }));
  return (
    <Cell label={label} span={span} hint={hint}>
      <select className={cx(input, '-ml-1 cursor-pointer appearance-none bg-no-repeat pr-4')} value={value} onChange={(e) => onChange(e.target.value as T)} style={{ backgroundImage: CARET, backgroundPosition: 'right 0 center' }}>
        {placeholder !== undefined && <option value="">{placeholder}</option>}
        {list.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </Cell>
  );
}

const CARET = `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%2398a4b3' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E")`;

/** Yes/No as a select — faster than a toggle from the keyboard (type Y or N). */
export function YesNoC({ label, value, onChange, span = 1, yes = 'Yes', no = 'No' }: { label: ReactNode; value: boolean; onChange: (v: boolean) => void; span?: number; yes?: string; no?: string }) {
  return (
    <SelectC
      label={label}
      span={span}
      value={value ? 'y' : 'n'}
      onChange={(v) => onChange(v === 'y')}
      options={[
        { value: 'y', label: yes },
        { value: 'n', label: no },
      ]}
    />
  );
}

export function CheckC({ label, checked, onChange, span = 3, detail }: { label: ReactNode; checked: boolean; onChange: (v: boolean) => void; span?: number; detail?: ReactNode }) {
  return (
    <label
      className="sheet-cell flex min-h-[40px] min-w-0 cursor-pointer items-center gap-2.5 bg-white px-2 py-1 focus-within:bg-[#eaf3fb] focus-within:shadow-[inset_2px_0_0_var(--color-brand-2)]"
      style={cellStyle(span)}
    >
      <input type="checkbox" className="h-[15px] w-[15px] shrink-0 accent-[var(--color-brand-2)]" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="min-w-0 text-[12.5px] leading-4 text-ink">
        {label}
        {detail && <span className="block text-[10.5px] leading-[14px] text-muted">{detail}</span>}
      </span>
    </label>
  );
}

/** Computed, read-only value (not a tab stop). */
export function ReadC({ label, value, span = 2, tone, sub }: { label: ReactNode; value: ReactNode; span?: number; tone?: 'good' | 'bad' | 'strong'; sub?: ReactNode }) {
  return (
    <div className="sheet-cell flex min-h-[40px] min-w-0 flex-col justify-center bg-[#f7f9fb] px-2 pb-1 pt-[3px]" style={cellStyle(span)}>
      <span className="truncate text-[9.5px] font-semibold uppercase leading-3 tracking-[0.05em] text-faint">{label}</span>
      <span
        className={cx(
          'truncate text-[13px] leading-5 tabular',
          tone === 'good' && 'font-semibold text-good-ink',
          tone === 'bad' && 'font-semibold text-critical-ink',
          tone === 'strong' && 'font-semibold text-ink',
          !tone && 'text-ink-2',
        )}
      >
        {value}
        {sub && <span className="ml-1.5 text-[11px] text-muted">{sub}</span>}
      </span>
    </div>
  );
}

/** Empty filler cell to finish a row. */
export function Filler({ span = 1 }: { span?: number }) {
  return <div className="sheet-cell min-w-0 bg-white" style={cellStyle(span)} aria-hidden />;
}

/* ------------------------------------------------------------------ */
/* Numbers                                                             */
/* ------------------------------------------------------------------ */

const group = (raw: string) => {
  const [int, dec] = raw.split('.');
  const g = int.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return dec !== undefined ? `${g}.${dec}` : g;
};

function NumberInput({
  value,
  onChange,
  prefix,
  suffix,
  scale = 1,
  decimals = 0,
  allowNull = false,
  placeholder,
  min,
  max,
}: {
  value: number | null;
  onChange: (v: number | null) => void;
  prefix?: string;
  suffix?: string;
  scale?: number;
  decimals?: number;
  allowNull?: boolean;
  placeholder?: string;
  min?: number;
  max?: number;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = (() => {
    if (draft !== null) return draft;
    if (value === null || value === undefined) return '';
    const v = value * scale;
    if (!v) return '';
    return group(Number(v.toFixed(decimals)).toString());
  })();
  const commit = (text: string) => {
    const cleaned = text.replace(/[^\d.\-]/g, '');
    if (cleaned === '' || cleaned === '-' || cleaned === '.') return onChange(allowNull ? null : 0);
    let n = Number(cleaned) / scale;
    if (!Number.isFinite(n)) return;
    if (min !== undefined) n = Math.max(min, n);
    if (max !== undefined) n = Math.min(max, n);
    onChange(n);
  };
  return (
    <span className="flex items-baseline gap-1">
      {prefix && <span className="text-[11px] text-faint">{prefix}</span>}
      <input
        inputMode="decimal"
        autoComplete="off"
        className={cx(input, 'tabular')}
        value={shown}
        placeholder={placeholder ?? (allowNull ? 'auto' : '0')}
        onFocus={(e) => {
          setDraft(shown.replace(/\s/g, ''));
          const el = e.target;
          requestAnimationFrame(() => el.select());
        }}
        onChange={(e) => {
          const t = e.target.value.replace(/,/g, '.').replace(/[^\d.\-\s]/g, '');
          setDraft(t);
          commit(t);
        }}
        onBlur={() => setDraft(null)}
      />
      {suffix && <span className="shrink-0 text-[11px] text-faint">{suffix}</span>}
    </span>
  );
}

export function MoneyC({ label, value, onChange, span = 2, hint, suffix }: { label: ReactNode; value: number; onChange: (v: number) => void; span?: number; hint?: ReactNode; suffix?: string }) {
  return (
    <Cell label={label} span={span} hint={hint}>
      <NumberInput value={value} onChange={(v) => onChange(v ?? 0)} prefix="R" suffix={suffix} min={0} />
    </Cell>
  );
}

export function NullMoneyC({ label, value, onChange, span = 2, hint }: { label: ReactNode; value: number | null; onChange: (v: number | null) => void; span?: number; hint?: ReactNode }) {
  return (
    <Cell label={label} span={span} hint={hint}>
      <NumberInput value={value} onChange={onChange} prefix="R" allowNull min={0} />
    </Cell>
  );
}

export function PctC({ label, value, onChange, span = 1, decimals = 1, hint }: { label: ReactNode; value: number; onChange: (v: number) => void; span?: number; decimals?: number; hint?: ReactNode }) {
  return (
    <Cell label={label} span={span} hint={hint}>
      <NumberInput value={value} onChange={(v) => onChange(v ?? 0)} suffix="%" scale={100} decimals={decimals} />
    </Cell>
  );
}

export function NumC({ label, value, onChange, span = 1, suffix, min, max, hint }: { label: ReactNode; value: number; onChange: (v: number) => void; span?: number; suffix?: string; min?: number; max?: number; hint?: ReactNode }) {
  return (
    <Cell label={label} span={span} hint={hint}>
      <NumberInput value={value} onChange={(v) => onChange(v ?? 0)} suffix={suffix} decimals={1} min={min} max={max} />
    </Cell>
  );
}

/* ------------------------------------------------------------------ */
/* Repeating rows                                                      */
/* ------------------------------------------------------------------ */

/** One repeating record: a numbered gutter with a remove button, then its cells. */
export function Item({ n, id, onRemove, children, tone }: { n: number; id: string; onRemove: () => void; children: ReactNode; tone?: 'muted' }) {
  return (
    <div data-row={id} className={cx('flex border-b border-line', tone === 'muted' && 'opacity-80')}>
      <div className="flex w-7 shrink-0 flex-col items-center justify-between border-r border-line bg-wash py-1">
        <span className="text-[10px] font-semibold text-muted tabular">{n}</span>
        <button
          type="button"
          tabIndex={-1}
          onClick={onRemove}
          className="rounded p-0.5 text-faint hover:bg-critical-soft hover:text-critical-ink"
          title="Remove"
          aria-label={`Remove row ${n}`}
        >
          <X size={12} strokeWidth={2.5} />
        </button>
      </div>
      <div className="sheet-cells flex min-w-0 flex-1 flex-wrap gap-px bg-line">{children}</div>
    </div>
  );
}

export function AddRow({ label, onAdd, empty }: { label: string; onAdd: () => void; empty?: string }) {
  return (
    <div className="flex items-center gap-3 border-b border-line bg-white px-2 py-1">
      <button
        type="button"
        onClick={onAdd}
        className="focus-ring inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11.5px] font-semibold text-brand-2 hover:bg-brand-soft"
      >
        <Plus size={13} strokeWidth={2.5} /> {label}
      </button>
      {empty && <span className="text-[11px] text-faint">{empty}</span>}
    </div>
  );
}
