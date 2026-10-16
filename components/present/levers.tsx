'use client';

import { Minus, Plus } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import { cx } from '../ui';

/*
 * The levers of the planning session, set as slips of paper. A slider shows
 * its value in words and can carry a marker — “closes the gap”, “in the
 * file” — that sets the lever to that value when chosen.
 */

export function LeverCard({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('pv-slip', className)}>{children}</div>;
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
  /** A reference value on the track; choosing it sets the lever to it. */
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
      <div className="pv-lever-head">
        <label htmlFor={id} className="pv-lever-label">
          {label}
        </label>
        <span className="pv-lever-value">{format(value)}</span>
      </div>
      <div className={cx('pv-range-wrap', mk !== null && 'has-marker')}>
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
          <button type="button" onClick={() => onChange(marker.value)} title={`Set to ${format(marker.value)}`} className="pv-marker" style={{ left: `calc(13px + (100% - 26px) * ${mk})` }}>
            {marker.label}
          </button>
        )}
      </div>
      {hint && <p className="pv-hint">{hint}</p>}
      {children}
    </div>
  );
}

/** A blank on the slip for a premium quoted by an insurer. */
export function RandInput({ label, value, onChange, placeholder = 'to be quoted' }: { label: string; value: number; onChange: (v: number) => void; placeholder?: string }) {
  const id = useId();
  const [text, setText] = useState<string | null>(null);
  return (
    <div className="pv-blank-row">
      <label htmlFor={id}>{label}</label>
      <span className="pv-blank">
        R
        <input
          id={id}
          inputMode="numeric"
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
    <div role="radiogroup" aria-label={label} className={cx('pv-tabs', size === 'sm' && 'is-sm')}>
      {options.map((o) => (
        <button key={String(o.value)} type="button" role="radio" aria-checked={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function CheckRow({ checked, onChange, label, detail }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; detail?: ReactNode }) {
  return (
    <label className={cx('pv-check', checked && 'is-on')}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="min-w-0">
        <span className="pv-check-label">{label}</span>
        {detail && <span className="pv-check-detail">{detail}</span>}
      </span>
    </label>
  );
}

/** A year, moved one at a time. */
export function YearStepper({ label, value, min, max, onChange, note }: { label: string; value: number; min: number; max: number; onChange: (v: number) => void; note?: string }) {
  const btn = (dir: -1 | 1, icon: ReactNode, text: string) => (
    <button type="button" onClick={() => onChange(Math.min(max, Math.max(min, value + dir)))} disabled={dir < 0 ? value <= min : value >= max} aria-label={text} title={text} className="pv-step">
      {icon}
    </button>
  );
  return (
    <div className="pv-stepper">
      <div className="min-w-0">
        <div className="pv-lever-label">{label}</div>
        {note && <p className="pv-hint !mt-0">{note}</p>}
      </div>
      <div className="pv-stepper-ctl">
        {btn(-1, <Minus size={18} />, `${label}: a year earlier`)}
        <span className="pv-lever-value w-[3.2em] text-center" aria-live="polite">
          {value}
        </span>
        {btn(1, <Plus size={18} />, `${label}: a year later`)}
      </div>
    </div>
  );
}
