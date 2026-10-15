import type { ReactNode } from 'react';
import { cx } from '../../ui';
import { ordinal, roman, Rule3 } from './ink';

/** One sheet of the report. On screen it is a leaf of paper; in print it begins a new page. */
export function Leaf({ children, running, className }: { children: ReactNode; running?: { left: string; right: string }; className?: string }) {
  return (
    <section className={cx('vr-leaf', className)}>
      {running && (
        <div className="vr-runhead" aria-hidden>
          <span>{running.left}</span>
          <span>{running.right}</span>
        </div>
      )}
      {children}
    </section>
  );
}

/** “Chapter the Second” — title — italic sub-title — printer’s rule. */
export function ChapterHead({ number, kicker, title, sub }: { number?: number; kicker?: string; title: string; sub?: string }) {
  return (
    <header className="vr-chapter">
      <div className="vr-chapter-no">{kicker ?? `Chapter the ${ordinal(number ?? 1)}`}</div>
      <h2>{title}</h2>
      {sub && <p className="vr-chapter-sub">{sub}</p>}
      <Rule3 />
    </header>
  );
}

/** A small-capital section heading with an optional italic gloss in plain words. */
export function Heading({ children, gloss, id }: { children: ReactNode; gloss?: ReactNode; id?: string }) {
  return (
    <div className="vr-heading" id={id}>
      <h3>{children}</h3>
      {gloss && <p>{gloss}</p>}
    </div>
  );
}

/**
 * A paragraph of the narrow text measure with a note in the outer margin —
 * used to explain a term in plain words where it first appears.
 */
export function Para({ children, note, drop = false, className }: { children: ReactNode; note?: ReactNode; drop?: boolean; className?: string }) {
  return (
    <div className={cx('vr-para', !note && 'vr-para-solo', className)}>
      <div className={cx('vr-text', drop && 'vr-drop')}>{children}</div>
      {note && <aside className="vr-margin">{note}</aside>}
    </div>
  );
}

export function Gloss({ term, children }: { term: string; children: ReactNode }) {
  return (
    <p>
      <i>{term}</i> — {children}
    </p>
  );
}

/** “The short of it”: a boxed abstract at the head of a chapter. */
export function Abstract({ children }: { children: ReactNode }) {
  return (
    <div className="vr-abstract">
      <span className="vr-abstract-label">The short of it.</span> {children}
    </div>
  );
}

export interface LeaderRow {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: 'red' | 'strong';
  key?: string;
}

/** Catalogue entries joined to their figures by dot leaders. */
export function Leaders({ rows, className }: { rows: LeaderRow[]; className?: string }) {
  return (
    <dl className={cx('vr-leaders', className)}>
      {rows.map((r, i) => (
        <div key={r.key ?? i} className={cx('vr-leader', r.tone === 'red' && 'is-red', r.tone === 'strong' && 'is-strong')}>
          <div className="vr-leader-line">
            <dt>{r.label}</dt>
            <span className="vr-dots" aria-hidden />
            <dd>{r.value}</dd>
          </div>
          {r.sub && <div className="vr-leader-sub">{r.sub}</div>}
        </div>
      ))}
    </dl>
  );
}

type Align = 'l' | 'r' | 'c';

/** A table ruled in the manner of a nineteenth-century table of figures. */
export function VTable({
  head,
  rows,
  align,
  foot,
  strong = [],
  red = [],
  className,
  caption,
}: {
  head: ReactNode[];
  rows: ReactNode[][];
  align?: Align[];
  foot?: ReactNode[];
  /** Row indexes set in bold. */
  strong?: number[];
  /** Row indexes printed in red ink. */
  red?: number[];
  className?: string;
  caption?: ReactNode;
}) {
  const a = (i: number) => (align?.[i] === 'r' ? 'is-r' : align?.[i] === 'c' ? 'is-c' : undefined);
  return (
    <table className={cx('vr-table', className)}>
      {caption && <caption>{caption}</caption>}
      <thead>
        <tr>
          {head.map((h, i) => (
            <th key={i} className={a(i)}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, ri) => (
          <tr key={ri} className={cx(strong.includes(ri) && 'is-strong', red.includes(ri) && 'is-red')}>
            {r.map((c, ci) => (
              <td key={ci} className={a(ci)}>
                {c}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
      {foot && (
        <tfoot>
          <tr>
            {foot.map((c, i) => (
              <td key={i} className={a(i)}>
                {c}
              </td>
            ))}
          </tr>
        </tfoot>
      )}
    </table>
  );
}

/** “Plate IV” — title — the engraving — “How to read it”. */
export function Plate({ number, title, children, read, className, label }: { number: number; title: ReactNode; children: ReactNode; read?: ReactNode; className?: string; label?: string }) {
  return (
    <figure className={cx('vr-plate', className)} aria-label={label}>
      <div className="vr-plate-head">
        <span className="vr-plate-no">Plate {roman(number)}</span>
        <span className="vr-plate-title">{title}</span>
      </div>
      <div className="vr-plate-body">{children}</div>
      {read && (
        <figcaption className="vr-plate-read">
          <i>How to read it.</i> {read}
        </figcaption>
      )}
    </figure>
  );
}

export function Signature({ role, name, date }: { role: string; name: string; date?: string }) {
  return (
    <div className="vr-sign">
      <div className="vr-sign-role">{role}</div>
      <div className="vr-sign-line" />
      <div className="vr-sign-name">{name}</div>
      <div className="vr-sign-date">{date ? `Dated ${date}` : 'Dated ____________________'}</div>
    </div>
  );
}

/** Keeps a heading with what follows it, so no heading is left at the foot of a printed page. */
export function Keep({ children }: { children: ReactNode }) {
  return <div className="vr-keep">{children}</div>;
}
