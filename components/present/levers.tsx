'use client';

import { Minus, Plus } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import { cx } from '../ui';

/*
 * Lever controls for presentation mode. A slider shows its value in words and
 * can carry a marker — “closes the gap”, “in the file” — that snaps the lever
 * to that value when clicked.
 */

export function LeverCard({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('pv-card p-5', className)}>{children}</div>;
}

export function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  format,
  marker,
  hint,
  children,
}: {
  label: ReactNode;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  format: (v: number) => string;
  /** A reference value on the track; clicking it sets the lever to it. */
  marker?: { value: number; label: string };
  hint?: ReactNode;
  children?: ReactNode;
}) {
  const id = useId();
  const span = Math.max(step, max - min);
  const frac = Math.min(1, Math.max(0, (value - min) / span));
  const mk = marker && marker.value >= min && marker.value <= max ? (marker.value - min) / span : null;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-[15px] font-semibold">
          {label}
        </label>
        <span className="text-[22px] font-semibold tracking-tight pv-tabular">{format(value)}</span>
      </div>
      <div className="relative mt-1">
        <input
          id={id}
          type="range"
          className="pv-range"
          min={min}
          max={max}
          step={step}
          value={Math.min(max, Math.max(min, value))}
          aria-valuetext={format(value)}
          onChange={(e) => onChange(Number(e.target.value))}
          style={{ ['--fill' as string]: `${frac * 100}%` }}
        />
        {mk !== null && marker && (
          <button
            type="button"
            onClick={() => onChange(marker.value)}
            title={`Set to ${format(marker.value)}`}
            className="absolute top-[24px] flex -translate-x-1/2 flex-col items-center text-[12px] leading-none text-[var(--pv-ink-2)] hover:text-[var(--pv-ink)]"
            style={{ left: `calc(13px + (100% - 26px) * ${mk})` }}
          >
            <span className="h-2 w-[2px] bg-[var(--pv-ink-2)]" aria-hidden />
            <span className="mt-1 whitespace-nowrap">{marker.label}</span>
          </button>
        )}
      </div>
      <div className={cx(mk !== null ? 'mt-7' : 'mt-1')}>
        {hint && <p className="text-[13px] leading-snug text-[var(--pv-ink-2)]">{hint}</p>}
        {children}
      </div>
    </div>
  );
}

/** A small rand input, for premiums quoted by insurers. */
export function RandInput({ label, value, onChange, placeholder = 'to be quoted' }: { label: string; value: number; onChange: (v: number) => void; placeholder?: string }) {
  const id = useId();
  const [text, setText] = useState<string | null>(null);
  return (
    <div className="mt-3 flex items-center gap-2 text-[13.5px] text-[var(--pv-ink-2)]">
      <label htmlFor={id}>{label}</label>
      <span className="relative">
        <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--pv-muted)]">R</span>
        <input
          id={id}
          inputMode="numeric"
          className="h-9 w-32 rounded-lg border border-[var(--pv-border)] bg-[var(--pv-raised)] pl-6 pr-2.5 text-right text-[14px] text-[var(--pv-ink)] pv-tabular outline-none focus:border-[var(--pv-plan)]"
          placeholder={placeholder}
          value={text ?? (value ? Math.round(value).toLocaleString('en-ZA') : '')}
          onFocus={() => setText(value ? String(Math.round(value)) : '')}
          onChange={(e) => {
            const raw = e.target.value.replace(/[^\d]/g, '');
            setText(raw);
            onChange(raw ? Number(raw) : 0);
          }}
          onBlur={() => setText(null)}
        />
      </span>
      <span>a month</span>
    </div>
  );
}

export function Tabs<T extends string | number>({
  value,
  onChange,
  options,
  label,
  size = 'md',
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  label: string;
  size?: 'md' | 'sm';
}) {
  if (options.length < 2) return null;
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-xl border border-[var(--pv-border)] bg-[var(--pv-surface)] p-1">
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(o.value)}
            className={cx(
              'rounded-lg font-medium transition-colors',
              size === 'md' ? 'px-4 py-1.5 text-[15px]' : 'px-3 py-1 text-[13.5px]',
              on ? 'bg-[var(--pv-plan)] text-white' : 'text-[var(--pv-ink-2)] hover:text-[var(--pv-ink)]',
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function CheckRow({ checked, onChange, label, detail }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; detail?: ReactNode }) {
  return (
    <label
      className={cx(
        'flex cursor-pointer items-start gap-3 rounded-xl border px-4 py-3 transition-colors',
        checked ? 'border-[var(--pv-plan)] bg-[var(--pv-plan-wash)]' : 'border-[var(--pv-border)] hover:bg-[var(--pv-plan-wash)]',
      )}
    >
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-1 h-4 w-4 accent-[var(--pv-plan)]" />
      <span className="min-w-0 flex-1">
        <span className="block text-[15px] font-medium">{label}</span>
        {detail && <span className="mt-0.5 block text-[13px] text-[var(--pv-ink-2)]">{detail}</span>}
      </span>
    </label>
  );
}

/** A year, moved one at a time. */
export function YearStepper({ label, value, min, max, onChange, note }: { label: string; value: number; min: number; max: number; onChange: (v: number) => void; note?: string }) {
  const btn = (dir: -1 | 1, icon: ReactNode, text: string) => (
    <button
      type="button"
      onClick={() => onChange(Math.min(max, Math.max(min, value + dir)))}
      disabled={dir < 0 ? value <= min : value >= max}
      aria-label={text}
      className="flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--pv-border)] text-[var(--pv-ink-2)] hover:border-[var(--pv-plan)] hover:text-[var(--pv-ink)] disabled:opacity-30"
    >
      {icon}
    </button>
  );
  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <div className="text-[15px] font-semibold">{label}</div>
        {note && <div className="text-[13px] text-[var(--pv-ink-2)]">{note}</div>}
      </div>
      <div className="flex items-center gap-2">
        {btn(-1, <Minus size={16} />, `${label}: a year earlier`)}
        <span className="w-14 text-center text-[22px] font-semibold tracking-tight pv-tabular" aria-live="polite">
          {value}
        </span>
        {btn(1, <Plus size={16} />, `${label}: a year later`)}
      </div>
    </div>
  );
}
