'use client';

import { AlertTriangle, CheckCircle2, CircleAlert, Info, Minus, OctagonAlert } from 'lucide-react';
import { useId, useState, type ButtonHTMLAttributes, type ReactNode } from 'react';
import type { Status } from '@/lib/fna/analysis';

export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(' ');

/* ------------------------------------------------------------------ */
/* Layout                                                              */
/* ------------------------------------------------------------------ */

export function Card({ children, className, id }: { children: ReactNode; className?: string; id?: string }) {
  return (
    <section id={id} className={cx('rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(16,24,40,0.04)]', className)}>
      {children}
    </section>
  );
}

export function CardHeader({
  title,
  description,
  actions,
  icon,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
      <div className="flex min-w-0 items-start gap-3">
        {icon && <div className="mt-0.5 text-brand-2">{icon}</div>}
        <div className="min-w-0">
          <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
          {description && <p className="mt-0.5 text-[13px] leading-5 text-muted">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('px-5 py-5', className)}>{children}</div>;
}

export function Grid({ children, cols = 3, className }: { children: ReactNode; cols?: 1 | 2 | 3 | 4; className?: string }) {
  const map = {
    1: 'grid-cols-1',
    2: 'grid-cols-1 sm:grid-cols-2',
    3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
    4: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4',
  };
  return <div className={cx('grid gap-x-4 gap-y-4', map[cols], className)}>{children}</div>;
}

export function StepHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-3xl">
        {eyebrow && <div className="mb-1 text-xs font-semibold uppercase tracking-[0.08em] text-brand-2">{eyebrow}</div>}
        <h2 className="text-2xl font-semibold tracking-tight text-ink">{title}</h2>
        {description && <p className="mt-1.5 text-sm leading-6 text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Fields                                                              */
/* ------------------------------------------------------------------ */

export function Field({
  label,
  hint,
  error,
  children,
  className,
  htmlFor,
  optional,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  className?: string;
  htmlFor?: string;
  optional?: boolean;
}) {
  return (
    <div className={cx('min-w-0', className)}>
      <label htmlFor={htmlFor} className="mb-1.5 flex items-baseline justify-between gap-2 text-[13px] font-medium text-ink-2">
        <span>{label}</span>
        {optional && <span className="text-[11px] font-normal text-faint">Optional</span>}
      </label>
      {children}
      {error ? (
        <p className="mt-1 text-xs text-critical-ink">{error}</p>
      ) : hint ? (
        <p className="mt-1 text-xs leading-4 text-muted">{hint}</p>
      ) : null}
    </div>
  );
}

type Common = { label: ReactNode; hint?: ReactNode; error?: ReactNode; className?: string; optional?: boolean; placeholder?: string; disabled?: boolean };

export function TextField({ value, onChange, type = 'text', ...rest }: Common & { value: string; onChange: (v: string) => void; type?: string }) {
  const id = useId();
  return (
    <Field label={rest.label} hint={rest.hint} error={rest.error} className={rest.className} htmlFor={id} optional={rest.optional}>
      <input
        id={id}
        type={type}
        className="control"
        value={value ?? ''}
        placeholder={rest.placeholder}
        disabled={rest.disabled}
        aria-invalid={!!rest.error}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  );
}

export function TextArea({ value, onChange, rows = 3, ...rest }: Common & { value: string; onChange: (v: string) => void; rows?: number }) {
  const id = useId();
  return (
    <Field label={rest.label} hint={rest.hint} className={rest.className} htmlFor={id} optional={rest.optional}>
      <textarea id={id} rows={rows} className="control" value={value ?? ''} placeholder={rest.placeholder} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}

const groupDigits = (raw: string) => {
  const [int, dec] = raw.split('.');
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return dec !== undefined ? `${grouped}.${dec}` : grouped;
};

/** Numeric input that keeps a friendly text representation while editing. */
function NumericInput({
  value,
  onChange,
  prefix,
  suffix,
  scale = 1,
  decimals = 0,
  allowNull = false,
  id,
  placeholder,
  invalid,
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
  id?: string;
  placeholder?: string;
  invalid?: boolean;
  min?: number;
  max?: number;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const display = (() => {
    if (draft !== null) return draft;
    if (value === null || value === undefined || (allowNull && value === null)) return '';
    const scaled = value * scale;
    if (!scaled && !allowNull) return '';
    const fixed = Number(scaled.toFixed(decimals)).toString();
    return groupDigits(fixed);
  })();
  const commit = (text: string) => {
    const cleaned = text.replace(/[^\d.\-]/g, '');
    if (cleaned === '' || cleaned === '-' || cleaned === '.') {
      onChange(allowNull ? null : 0);
      return;
    }
    let n = Number(cleaned) / scale;
    if (!Number.isFinite(n)) return;
    if (min !== undefined) n = Math.max(min, n);
    if (max !== undefined) n = Math.min(max, n);
    onChange(n);
  };
  return (
    <div className="relative">
      {prefix && <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-sm text-muted">{prefix}</span>}
      <input
        id={id}
        inputMode="decimal"
        autoComplete="off"
        className={cx('control tabular', prefix && 'pl-7', suffix && 'pr-10')}
        value={display}
        placeholder={placeholder ?? (allowNull ? 'Auto' : '0')}
        aria-invalid={invalid}
        onFocus={(e) => {
          setDraft(display.replace(/\s/g, ''));
          requestAnimationFrame(() => e.target.select());
        }}
        onChange={(e) => {
          const t = e.target.value.replace(/,/g, '.').replace(/[^\d.\-\s]/g, '');
          setDraft(t);
          commit(t);
        }}
        onBlur={() => setDraft(null)}
      />
      {suffix && <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-sm text-muted">{suffix}</span>}
    </div>
  );
}

export function MoneyField({
  value,
  onChange,
  suffix,
  ...rest
}: Common & { value: number; onChange: (v: number) => void; suffix?: string }) {
  const id = useId();
  return (
    <Field label={rest.label} hint={rest.hint} error={rest.error} className={rest.className} htmlFor={id} optional={rest.optional}>
      <NumericInput id={id} value={value} onChange={(v) => onChange(v ?? 0)} prefix="R" suffix={suffix} placeholder={rest.placeholder} min={0} />
    </Field>
  );
}

export function NullableMoneyField({ value, onChange, ...rest }: Common & { value: number | null; onChange: (v: number | null) => void }) {
  const id = useId();
  return (
    <Field label={rest.label} hint={rest.hint} className={rest.className} htmlFor={id} optional={rest.optional}>
      <NumericInput id={id} value={value} onChange={onChange} prefix="R" allowNull placeholder={rest.placeholder} min={0} />
    </Field>
  );
}

export function PercentField({ value, onChange, decimals = 2, ...rest }: Common & { value: number; onChange: (v: number) => void; decimals?: number }) {
  const id = useId();
  return (
    <Field label={rest.label} hint={rest.hint} className={rest.className} htmlFor={id} optional={rest.optional}>
      <NumericInput id={id} value={value} onChange={(v) => onChange(v ?? 0)} suffix="%" scale={100} decimals={decimals} placeholder={rest.placeholder ?? '0'} />
    </Field>
  );
}

export function NumberField({
  value,
  onChange,
  suffix,
  min,
  max,
  ...rest
}: Common & { value: number; onChange: (v: number) => void; suffix?: string; min?: number; max?: number }) {
  const id = useId();
  return (
    <Field label={rest.label} hint={rest.hint} className={rest.className} htmlFor={id} optional={rest.optional}>
      <NumericInput id={id} value={value} onChange={(v) => onChange(v ?? 0)} suffix={suffix} decimals={1} min={min} max={max} placeholder={rest.placeholder} />
    </Field>
  );
}

export function DateField({ value, onChange, ...rest }: Common & { value: string; onChange: (v: string) => void }) {
  const id = useId();
  return (
    <Field label={rest.label} hint={rest.hint} error={rest.error} className={rest.className} htmlFor={id} optional={rest.optional}>
      <input id={id} type="date" className="control tabular" value={value ?? ''} onChange={(e) => onChange(e.target.value)} max="2100-12-31" />
    </Field>
  );
}

export function SelectField<T extends string>({
  value,
  onChange,
  options,
  ...rest
}: Common & { value: T; onChange: (v: T) => void; options: { value: T; label: string }[] | Record<T, string> }) {
  const id = useId();
  const list = Array.isArray(options) ? options : (Object.entries(options) as [T, string][]).map(([v, l]) => ({ value: v, label: l }));
  return (
    <Field label={rest.label} hint={rest.hint} className={rest.className} htmlFor={id} optional={rest.optional}>
      <select id={id} className="control" value={value} onChange={(e) => onChange(e.target.value as T)} disabled={rest.disabled}>
        {rest.placeholder !== undefined && <option value="">{rest.placeholder}</option>}
        {list.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </Field>
  );
}

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  size = 'md',
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode }[];
  size?: 'sm' | 'md';
}) {
  return (
    <div role="radiogroup" className="inline-flex flex-wrap rounded-lg border border-line-strong bg-wash p-0.5">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.value)}
            className={cx(
              'focus-ring rounded-md font-medium transition-colors',
              size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-3 py-1.5 text-[13px]',
              active ? 'bg-surface text-ink shadow-[0_1px_2px_rgba(16,24,40,0.12)]' : 'text-muted hover:text-ink',
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function SegmentedField<T extends string>({
  label,
  hint,
  className,
  ...rest
}: { label: ReactNode; hint?: ReactNode; className?: string; value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode }[] }) {
  return (
    <Field label={label} hint={hint} className={className}>
      <div>
        <Segmented {...rest} />
      </div>
    </Field>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
  className,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={cx('flex items-start gap-3', className)}>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cx(
          'focus-ring relative mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors',
          checked ? 'bg-brand-2' : 'bg-line-strong',
        )}
      >
        <span className={cx('inline-block h-4 w-4 rounded-full bg-white shadow transition-transform', checked ? 'translate-x-[18px]' : 'translate-x-0.5')} />
      </button>
      <label htmlFor={id} className="cursor-pointer select-none">
        <span className="block text-sm font-medium text-ink">{label}</span>
        {description && <span className="mt-0.5 block text-xs leading-4 text-muted">{description}</span>}
      </label>
    </div>
  );
}

export function Checkbox({ checked, onChange, label, description }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; description?: ReactNode }) {
  const id = useId();
  return (
    <div className="flex items-start gap-3">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 rounded border-line-strong accent-[var(--color-brand-2)]"
      />
      <label htmlFor={id} className="cursor-pointer select-none">
        <span className="block text-sm text-ink">{label}</span>
        {description && <span className="mt-0.5 block text-xs leading-4 text-muted">{description}</span>}
      </label>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Buttons & chips                                                     */
/* ------------------------------------------------------------------ */

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md';
  icon?: ReactNode;
};

export function Button({ variant = 'secondary', size = 'md', icon, className, children, type = 'button', ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(
        'focus-ring inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50',
        size === 'sm' ? 'h-8 px-2.5 text-[13px]' : 'h-9 px-3.5 text-sm',
        variant === 'primary' && 'bg-brand text-white hover:bg-brand-2',
        variant === 'secondary' && 'border border-line-strong bg-surface text-ink hover:bg-wash',
        variant === 'ghost' && 'text-ink-2 hover:bg-wash hover:text-ink',
        variant === 'danger' && 'border border-line-strong bg-surface text-critical-ink hover:bg-critical-soft',
        className,
      )}
      {...rest}
    >
      {icon}
      {children}
    </button>
  );
}

export function Badge({ children, tone = 'neutral', className }: { children: ReactNode; tone?: 'neutral' | 'brand' | 'good' | 'warning' | 'critical'; className?: string }) {
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium',
        tone === 'neutral' && 'bg-canvas text-ink-2',
        tone === 'brand' && 'bg-brand-soft text-brand',
        tone === 'good' && 'bg-good-soft text-good-ink',
        tone === 'warning' && 'bg-warning-soft text-warning-ink',
        tone === 'critical' && 'bg-critical-soft text-critical-ink',
        className,
      )}
    >
      {children}
    </span>
  );
}

const STATUS_META: Record<Status, { label: string; tone: 'good' | 'warning' | 'critical' | 'neutral'; Icon: typeof CheckCircle2 }> = {
  covered: { label: 'Covered', tone: 'good', Icon: CheckCircle2 },
  partial: { label: 'Partial', tone: 'warning', Icon: AlertTriangle },
  shortfall: { label: 'Shortfall', tone: 'critical', Icon: OctagonAlert },
  na: { label: 'No need', tone: 'neutral', Icon: Minus },
};

export function StatusPill({ status, label }: { status: Status; label?: string }) {
  const m = STATUS_META[status];
  return (
    <Badge tone={m.tone}>
      <m.Icon size={12} strokeWidth={2.5} aria-hidden />
      {label ?? m.label}
    </Badge>
  );
}

export const statusColor = (status: Status) =>
  status === 'covered' ? 'var(--color-good)' : status === 'partial' ? 'var(--color-warning)' : status === 'shortfall' ? 'var(--color-critical)' : 'var(--color-line-strong)';

export function Callout({ tone = 'info', title, children, className }: { tone?: 'info' | 'warning' | 'critical' | 'good'; title?: ReactNode; children?: ReactNode; className?: string }) {
  const Icon = tone === 'good' ? CheckCircle2 : tone === 'warning' ? AlertTriangle : tone === 'critical' ? CircleAlert : Info;
  return (
    <div
      className={cx(
        'flex gap-3 rounded-lg border px-4 py-3 text-sm',
        tone === 'info' && 'border-brand-soft bg-brand-soft/60 text-ink-2',
        tone === 'warning' && 'border-warning/40 bg-warning-soft text-ink-2',
        tone === 'critical' && 'border-critical/30 bg-critical-soft text-ink-2',
        tone === 'good' && 'border-good/30 bg-good-soft text-ink-2',
        className,
      )}
    >
      <Icon
        size={18}
        className={cx(
          'mt-px shrink-0',
          tone === 'info' && 'text-brand-2',
          tone === 'warning' && 'text-warning-ink',
          tone === 'critical' && 'text-critical',
          tone === 'good' && 'text-good-ink',
        )}
        aria-hidden
      />
      <div className="min-w-0 leading-5">
        {title && <div className="font-medium text-ink">{title}</div>}
        {children && <div className={title ? 'mt-0.5' : undefined}>{children}</div>}
      </div>
    </div>
  );
}

export function Stat({ label, value, sub, tone }: { label: ReactNode; value: ReactNode; sub?: ReactNode; tone?: 'good' | 'critical' }) {
  return (
    <div className="min-w-0">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className={cx('mt-1 truncate text-xl font-semibold tracking-tight', tone === 'good' ? 'text-good-ink' : tone === 'critical' ? 'text-critical-ink' : 'text-ink')}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function EmptyState({ icon, title, description, action }: { icon?: ReactNode; title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-line-strong bg-wash px-6 py-10 text-center">
      {icon && <div className="mb-3 text-faint">{icon}</div>}
      <div className="text-sm font-medium text-ink">{title}</div>
      {description && <p className="mt-1 max-w-sm text-[13px] leading-5 text-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Two-column label/value row used in summaries. */
export function Row({ label, value, strong, muted, className }: { label: ReactNode; value: ReactNode; strong?: boolean; muted?: boolean; className?: string }) {
  return (
    <div className={cx('flex items-baseline justify-between gap-4 py-1.5 text-sm', className)}>
      <span className={cx(muted ? 'text-muted' : 'text-ink-2', strong && 'font-semibold text-ink')}>{label}</span>
      <span className={cx('tabular text-right', strong ? 'font-semibold text-ink' : 'text-ink')}>{value}</span>
    </div>
  );
}

/** Repeatable record card with a header and remove action. */
export function ItemCard({ title, subtitle, onRemove, children, badge }: { title: ReactNode; subtitle?: ReactNode; onRemove?: () => void; children: ReactNode; badge?: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-surface">
      <div className="flex items-center justify-between gap-3 border-b border-line bg-wash/70 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <div className="truncate text-sm font-medium text-ink">{title}</div>
          {badge}
          {subtitle && <div className="truncate text-xs text-muted">{subtitle}</div>}
        </div>
        {onRemove && (
          <Button variant="ghost" size="sm" onClick={onRemove} className="text-muted hover:text-critical-ink">
            Remove
          </Button>
        )}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}
