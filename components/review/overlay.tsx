'use client';

/**
 * The proposed-changes overlay on the adviser's own sheet (spec §11.1). Sheet cells name the
 * FnaDocument path they edit; inside a review (or annotate) workspace this context tells each cell
 * whether a fact stands behind it and how to badge it — new, changed (prior value struck through),
 * confirmed or assumed — and opens the field's popover when the badge is chosen. Outside a
 * workspace there is no provider and cells render exactly as before.
 */
import { AlertTriangle, Check, Pencil, Plus, X } from 'lucide-react';
import { createContext, useContext, type ReactNode } from 'react';
import type { BadgeKind } from '@/lib/review/queue';
import { cx } from '../ui';

/** A review badge (lib/review/queue.ts), or annotate mode's: annotated, or a slot to annotate. */
export type MarkKind = BadgeKind | 'annotated' | 'annotate';

export interface FieldMarkData {
  kind: MarkKind;
  /** Short badge text: "New", "Changed", "Confirmed", "Assumed" … */
  label: string;
  /** Longer description for the tooltip and screen readers. */
  title: string;
  /** The prior value, struck through beside a change. */
  prior?: string | null;
  decision?: 'accepted' | 'edited' | 'rejected' | 'auto' | null;
  flagged?: boolean;
}

export interface OverlayApi {
  mode: 'review' | 'annotate';
  markAt: (path: string) => FieldMarkData | null;
  currentPath: string | null;
  openAt: (path: string) => void;
}

export const OverlayContext = createContext<OverlayApi | null>(null);

export function OverlayProvider({ value, children }: { value: OverlayApi; children: ReactNode }) {
  return <OverlayContext.Provider value={value}>{children}</OverlayContext.Provider>;
}

/** What the overlay shows on the cell at `path` (null outside a workspace or where no fact stands). */
export const useFieldMark = (path: string | undefined) => {
  const o = useContext(OverlayContext);
  if (!o || !path) return { mark: null, current: false, open: undefined };
  return { mark: o.markAt(path), current: o.currentPath === path, open: () => o.openAt(path) };
};

const TONE: Record<MarkKind, string> = {
  new: 'bg-brand-soft text-brand ring-brand/25',
  changed: 'bg-warning-soft text-warning-ink ring-warning/40',
  confirmed: 'bg-good-soft text-good-ink ring-good/30',
  assumed: 'bg-canvas text-ink-2 ring-line-strong',
  unknown: 'bg-warning-soft text-warning-ink ring-warning/40',
  removed: 'bg-critical-soft text-critical-ink ring-critical/30',
  annotated: 'bg-brand-soft text-brand ring-brand/25',
  annotate: 'bg-transparent text-faint ring-transparent opacity-0 group-hover:opacity-100 group-focus-within:opacity-100',
};

const DECISION: Record<NonNullable<FieldMarkData['decision']>, { icon: ReactNode; label: string; className: string }> = {
  accepted: { icon: <Check size={9} strokeWidth={3.5} />, label: 'accepted', className: 'text-good-ink' },
  edited: { icon: <Pencil size={8} strokeWidth={3} />, label: 'edited', className: 'text-brand-2' },
  rejected: { icon: <X size={9} strokeWidth={3.5} />, label: 'rejected', className: 'text-critical-ink' },
  auto: { icon: <Check size={9} strokeWidth={2.5} />, label: 'auto-accepted', className: 'text-faint' },
};

/** A badge pinned to the corner of a compact cell that has no label row (custom budget lines). */
export function CornerMark({ path }: { path: string }) {
  const { mark, current, open } = useFieldMark(path);
  if (!mark) return null;
  return (
    <span className={cx('absolute left-0.5 top-0 z-10 leading-none', current && 'rounded-sm ring-2 ring-brand-2')}>
      <FieldMark mark={mark} onOpen={open} />
    </span>
  );
}

/** The value before the call, struck through beside a changed field. */
export function PriorValue({ text }: { text: string }) {
  return (
    <s className="min-w-0 shrink truncate font-normal text-faint decoration-faint/70" title={`Before the call: ${text}`}>
      {text}
    </s>
  );
}

/** The badge in a cell's label row. Not a tab stop: the sheet's keyboard flow stays field to field. */
export function FieldMark({ mark, onOpen }: { mark: FieldMarkData; onOpen?: () => void }) {
  const d = mark.decision ? DECISION[mark.decision] : null;
  return (
    <span className="inline-flex shrink-0 items-baseline">
      <button
        type="button"
        tabIndex={-1}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onOpen?.();
        }}
        title={mark.title}
        aria-label={`${mark.title}${d ? `, ${d.label}` : ''}. Open the review popover.`}
        className={cx(
          'inline-flex h-[12px] items-center gap-0.5 rounded-[3px] px-1 text-[9px] font-semibold uppercase leading-none tracking-[0.03em] ring-1 ring-inset transition-opacity',
          TONE[mark.kind],
          mark.decision === 'rejected' && 'line-through',
        )}
      >
        {mark.flagged && <AlertTriangle size={8} strokeWidth={3} className="text-critical" aria-hidden />}
        {mark.kind === 'annotate' ? <Plus size={8} strokeWidth={3} aria-hidden /> : null}
        {mark.label}
        {d && (
          <span className={d.className} aria-hidden>
            {d.icon}
          </span>
        )}
      </button>
    </span>
  );
}
