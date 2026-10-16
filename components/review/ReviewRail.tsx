'use client';

/**
 * The rail beside the sheet while reviewing (spec §11.1, §11.3), in place of the live summary:
 *
 * - the impact strip — what the last decision did to each need (the app's own analyse(), before and
 *   after), the Monte Carlo success probability from the pipeline's forecast run with its variance
 *   attribution, and the FNA's deterministic retirement projection, labelled, next to it;
 * - the review queue — blocking flags first, then "Needs you", "Check quickly" and the collapsed
 *   auto-accepted facts, each sorted by review priority. The queue is one keyboard stop: Enter
 *   accepts and moves on, Tab moves on without deciding, Shift goes back.
 */
import { AlertTriangle, Check, ChevronDown, ChevronRight, Circle, CircleSlash, Pencil, ShieldCheck } from 'lucide-react';
import { useEffect, useState, type KeyboardEvent, type ReactNode, type Ref } from 'react';
import type { Analysis } from '@/lib/fna/analysis';
import { money, moneyCompact, pct } from '@/lib/format';
import { pyPercent } from '@/lib/review/dist';
import { attributionSentence, ciText, forecastDeterministic, needBand, needLines, retirementViews, type NeedDelta } from '@/lib/review/impact';
import { factTitle } from '@/lib/review/labels';
import { FLAG_LABEL, GROUP_LABEL, badgeOf, pBand, progress, type Queue, type QueueGroup } from '@/lib/review/queue';
import { isDecided, type ReviewFact, type ReviewSession } from '@/lib/review/session';
import { Button, StatusPill, cx } from '../ui';
import { ImpactList, shortValue } from './FieldPopover';

export interface LastChange {
  label: string;
  deltas: NeedDelta[];
}

const BAND_DOT = { high: 'bg-good', medium: 'bg-warning', low: 'bg-critical' } as const;

function Block({ title, note, children, className }: { title: ReactNode; note?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx('rounded-lg border border-line bg-surface', className)}>
      <h2 className="flex items-baseline justify-between gap-2 border-b border-line bg-wash px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-wide text-muted">
        <span>{title}</span>
        {note && <span className="truncate font-normal normal-case tracking-normal text-faint">{note}</span>}
      </h2>
      {children}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Impact strip                                                        */
/* ------------------------------------------------------------------ */

export function ImpactStrip({
  session,
  analysis,
  last,
  names,
  onOpenFact,
}: {
  session: ReviewSession;
  analysis: Analysis;
  last: LastChange | null;
  names: Record<string, string>;
  onOpenFact: (factId: string) => void;
}) {
  const f = session.bundle.preview_forecast;
  const sentence = attributionSentence(f, session.bundle.attribution_text);
  const retirement = retirementViews(analysis);
  const lines = needLines(analysis);
  const drivers = (f?.top_drivers ?? []).filter(([id]) => session.facts[id] && !session.facts[id].moved).slice(0, 3);
  const planningAge = f?.ages.length ? f.ages[f.ages.length - 1] : null;

  return (
    <div className="space-y-2">
      <Block title="Last change" note={last ? undefined : 'decide a fact to see its effect'}>
        <div className="px-2.5 py-1.5">
          {last ? (
            <>
              <div className="mb-1 truncate text-[11.5px] font-medium text-ink" title={last.label}>
                {last.label}
              </div>
              {last.deltas.length ? <ImpactList deltas={last.deltas} names={names} /> : <p className="text-[11.5px] text-muted">No need’s shortfall or status moved.</p>}
            </>
          ) : (
            <p className="text-[11.5px] text-muted">Each accept, edit or rejection re-runs the needs analysis; what moved shows here.</p>
          )}
        </div>
      </Block>

      <Block title="Retirement" note="two views of one plan">
        <div className="grid grid-cols-2 divide-x divide-line">
          <div className="px-2.5 py-1.5">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-muted">Monte Carlo</div>
            {f ? (
              <>
                <div className="text-[19px] font-semibold leading-6 tracking-tight text-ink tabular">{pyPercent(f.pos)}</div>
                <div className="text-[10.5px] leading-[14px] text-muted">
                  chance essential spending is met{planningAge ? ` to age ${planningAge}` : ''} ({ciText(f)})
                </div>
                <div className="mt-0.5 text-[10px] leading-[13px] text-faint">Pipeline forecast of the call as heard, before your edits</div>
              </>
            ) : (
              <p className="text-[11px] leading-[15px] text-muted">No forecast came with this review.</p>
            )}
          </div>
          <div className="px-2.5 py-1.5">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-muted">FNA projection</div>
            {retirement.map((r) => (
              <div key={r.person} className="mt-0.5">
                <div className="flex items-center justify-between gap-1 text-[11px]">
                  <span className="truncate font-medium text-ink">{r.name}</span>
                  <StatusPill status={r.status} />
                </div>
                <div className="text-[10.5px] leading-[14px] text-muted tabular" title={`Deterministic, live: ${money(r.projectedCapital)} projected against ${money(r.requiredCapital)} needed`}>
                  {moneyCompact(r.projectedCapital)} of {moneyCompact(r.requiredCapital)}
                  {r.depletionAge ? ` · lasts to ${Math.floor(r.depletionAge)}` : ''}
                </div>
                {forecastDeterministic(f, r.person, 'projected_capital') !== null && (
                  <div className="text-[10px] text-faint tabular">at forecast run {moneyCompact(forecastDeterministic(f, r.person, 'projected_capital') ?? 0)}</div>
                )}
              </div>
            ))}
            <div className="mt-0.5 text-[10px] leading-[13px] text-faint">Deterministic, live with your decisions</div>
          </div>
        </div>
        {sentence && <p className="border-t border-line px-2.5 py-1.5 text-[11px] leading-[15px] text-ink-2">{sentence}</p>}
        {drivers.length > 0 && (
          <div className="border-t border-line px-2.5 py-1.5">
            <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted">Moves the forecast most</div>
            <ul className="space-y-0.5">
              {drivers.map(([id, share]) => (
                <li key={id}>
                  <button type="button" tabIndex={-1} onClick={() => onOpenFact(id)} className="flex w-full items-baseline justify-between gap-2 rounded px-0.5 text-left text-[11px] hover:bg-wash">
                    <span className="truncate text-ink-2">{factTitle(session, session.facts[id])}</span>
                    <span className="shrink-0 text-muted tabular">{pyPercent(share)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Block>

      <details className="group rounded-lg border border-line bg-surface">
        <summary className="flex cursor-pointer list-none items-center justify-between px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-wide text-muted">
          <span>All needs now</span>
          <ChevronRight size={12} className="transition-transform group-open:rotate-90" aria-hidden />
        </summary>
        <ul className="space-y-1 border-t border-line px-2.5 py-1.5">
          {lines
            .filter((n) => n.status !== 'na')
            .map((n) => {
              const band = needBand(f, n.key);
              return (
                <li key={n.key} className="text-[11px]">
                  <div className="flex items-center justify-between gap-1">
                    <span className="truncate text-ink-2">
                      {n.title}
                      {n.person && <span className="text-muted"> · {names[n.person]}</span>}
                    </span>
                    <StatusPill status={n.status} />
                  </div>
                  <div className="flex justify-between text-[10.5px] text-muted tabular">
                    <span>{n.area === 'retirement' ? `${pct(n.need ? n.provision / n.need : 0)} funded` : n.shortfall > 0.5 ? `short ${n.monthly ? `${money(n.shortfall)} p.m.` : moneyCompact(n.shortfall)}` : 'met'}</span>
                    {band && (
                      <span title="The forecast's spread of this shortfall over its draws (p10–p90)">
                        forecast {moneyCompact(band.p10)}–{moneyCompact(band.p90)}
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
        </ul>
      </details>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Queue                                                               */
/* ------------------------------------------------------------------ */

function DecisionIcon({ f }: { f: ReviewFact | null }) {
  if (!f) return <Circle size={13} className="text-faint" aria-hidden />;
  if (f.review.status === 'accepted') return <Check size={13} strokeWidth={3} className="text-good-ink" aria-label="accepted" />;
  if (f.review.status === 'edited') return <Pencil size={12} strokeWidth={2.5} className="text-brand-2" aria-label="edited" />;
  if (f.review.status === 'rejected') return <CircleSlash size={13} className="text-critical-ink" aria-label="rejected" />;
  if (f.autoAccepted) return <Check size={13} className="text-faint" aria-label="auto-accepted" />;
  return <Circle size={13} className="text-line-strong" aria-label="not decided" />;
}

export const entryId = (entry: string) => `queue-${entry}`;

function Item({ session, f, entry, current, onSelect }: { session: ReviewSession; f: ReviewFact; entry: string; current: boolean; onSelect: (entry: string) => void }) {
  const badge = badgeOf(session, f);
  const band = pBand(f.pCorrect);
  return (
    <li
      id={entryId(entry)}
      role="option"
      aria-selected={current}
      onClick={() => onSelect(entry)}
      className={cx('cursor-pointer border-l-2 px-2 py-1', current ? 'border-brand-2 bg-brand-soft' : 'border-transparent hover:bg-wash', f.review.status === 'rejected' && 'opacity-70')}
    >
      <div className="flex items-center gap-1.5">
        <DecisionIcon f={f} />
        <span className={cx('min-w-0 flex-1 truncate text-[12px] font-medium text-ink', f.review.status === 'rejected' && 'line-through')}>{factTitle(session, f)}</span>
        {f.origin === 'pipeline' && f.source !== 'fna_default' && <span className={cx('h-1.5 w-1.5 shrink-0 rounded-full', BAND_DOT[band])} title={`${band} confidence (${pyPercent(f.pCorrect)})`} aria-label={`${band} confidence`} />}
      </div>
      <div className="flex items-center gap-1 pl-[19px] text-[11px] text-muted">
        {badge && <span className="shrink-0 font-semibold uppercase tracking-[0.03em] text-[9.5px] text-ink-2">{badge.label}</span>}
        <span className="truncate tabular">{shortValue(f)}</span>
      </div>
    </li>
  );
}

function GroupHeader({ label, count, decided, open, onToggle, action }: { label: string; count: number; decided: number; open?: boolean; onToggle?: () => void; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-line bg-wash px-2.5 py-1">
      <button type="button" tabIndex={-1} onClick={onToggle} disabled={!onToggle} className="flex min-w-0 items-center gap-1 text-[10.5px] font-semibold uppercase tracking-wide text-muted disabled:cursor-default">
        {onToggle && (open ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />)}
        <span className="truncate">{label}</span>
        <span className="font-normal normal-case tracking-normal tabular">
          {decided}/{count}
        </span>
      </button>
      {action}
    </div>
  );
}

export function ReviewQueue({
  session,
  queue,
  current,
  showCollapsed,
  setShowCollapsed,
  onSelect,
  onKeyDown,
  onAcceptGroup,
  onClearFlag,
  listRef,
}: {
  session: ReviewSession;
  queue: Queue;
  current: string | null;
  showCollapsed: boolean;
  setShowCollapsed: (v: boolean) => void;
  onSelect: (entry: string) => void;
  onKeyDown: (e: KeyboardEvent<HTMLElement>) => void;
  onAcceptGroup: (group: QueueGroup) => void;
  onClearFlag: (flagId: string, resolution: 'accepted' | 'dismissed', note?: string) => boolean;
  listRef?: Ref<HTMLDivElement>;
}) {
  const [dismissing, setDismissing] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const p = progress(session, queue);
  const done = !!session.submittedAt;

  useEffect(() => {
    if (current) document.getElementById(entryId(current))?.scrollIntoView({ block: 'nearest' });
  }, [current]);

  const group = (g: QueueGroup, facts: ReviewFact[]) => {
    if (!facts.length) return null;
    const open = g !== 'collapsed' || showCollapsed;
    const undecided = facts.filter((f) => !isDecided(f)).length;
    return (
      <div key={g}>
        <GroupHeader
          label={g === 'collapsed' ? `${GROUP_LABEL[g]} · barely move the plan` : GROUP_LABEL[g]}
          count={facts.length}
          decided={facts.length - undecided}
          open={open}
          onToggle={g === 'collapsed' ? () => setShowCollapsed(!showCollapsed) : undefined}
          action={
            g === 'quick' && undecided > 0 && !done ? (
              <button type="button" tabIndex={-1} onClick={() => onAcceptGroup(g)} className="shrink-0 rounded px-1 text-[11px] font-semibold text-brand-2 hover:bg-brand-soft">
                Accept all {undecided}
              </button>
            ) : null
          }
        />
        {open && (
          <ul role="group" aria-label={GROUP_LABEL[g]}>
            {facts.map((f) => (
              <Item key={f.factId} session={session} f={f} entry={f.factId} current={current === f.factId} onSelect={onSelect} />
            ))}
          </ul>
        )}
      </div>
    );
  };

  return (
    <section className="overflow-hidden rounded-lg border border-line bg-surface">
      <div className="px-2.5 py-2">
        <div className="flex items-baseline justify-between">
          <h2 className="text-[12.5px] font-semibold text-ink">Review queue</h2>
          <span className="text-[11px] text-muted tabular">
            {p.decided} of {p.total} decided
          </span>
        </div>
        <div className="mt-1 h-1 overflow-hidden rounded-full bg-brand-soft" aria-hidden>
          <div className="h-full rounded-full bg-brand-2" style={{ width: `${p.total ? (p.decided / p.total) * 100 : 100}%` }} />
        </div>
        {session.spotCheck && (
          <p className="mt-1.5 flex gap-1 text-[11px] leading-[15px] text-ink-2">
            <ShieldCheck size={13} className="mt-px shrink-0 text-brand-2" aria-hidden />
            Spot check: every auto-accepted fact is under “Needs you” in this review, so the confidence rules are checked against your decisions.
          </p>
        )}
        <p className="mt-1 text-[10.5px] leading-[14px] text-muted">
          <kbd className="font-sans font-semibold">Enter</kbd> accept · <kbd className="font-sans font-semibold">Tab</kbd> skip · <kbd className="font-sans font-semibold">E</kbd> edit · <kbd className="font-sans font-semibold">R</kbd> reject ·{' '}
          <kbd className="font-sans font-semibold">U</kbd> unknown · <kbd className="font-sans font-semibold">P</kbd> play
        </p>
      </div>
      <div
        ref={listRef}
        role="listbox"
        tabIndex={0}
        aria-label="Facts to review"
        aria-activedescendant={current ? entryId(current) : undefined}
        onKeyDown={onKeyDown}
        className="border-t border-line outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-2/50"
      >
        {queue.flags.length > 0 && (
          <div>
            <GroupHeader label="Blocking — clear before submitting" count={queue.flags.length} decided={0} />
            <ul role="group" aria-label="Blocking flags">
              {queue.flags.map(({ flag, fact }) => {
                const entry = `flag:${flag.flag_id}`;
                return (
                  <li
                    key={flag.flag_id}
                    id={entryId(entry)}
                    role="option"
                    aria-selected={current === entry}
                    onClick={() => onSelect(entry)}
                    className={cx('cursor-pointer border-l-2 px-2 py-1.5', current === entry ? 'border-critical bg-critical-soft' : 'border-transparent hover:bg-wash')}
                  >
                    <div className="flex items-center gap-1.5">
                      <AlertTriangle size={13} className="shrink-0 text-critical" aria-hidden />
                      <span className="shrink-0 rounded bg-critical-soft px-1 text-[9.5px] font-semibold uppercase tracking-[0.03em] text-critical-ink">{FLAG_LABEL[flag.kind]}</span>
                      <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">{fact ? factTitle(session, fact) : 'The whole call'}</span>
                    </div>
                    <p className="mt-0.5 line-clamp-2 pl-[19px] text-[11px] leading-[15px] text-ink-2">{flag.message}</p>
                    {!done && dismissing !== flag.flag_id && (
                      <div className="mt-0.5 flex gap-2 pl-[19px]">
                        {!fact && (
                          <button
                            type="button"
                            tabIndex={-1}
                            onClick={(e) => {
                              e.stopPropagation();
                              onClearFlag(flag.flag_id, 'accepted');
                            }}
                            className="text-[11px] font-semibold text-brand-2 hover:underline"
                          >
                            Mark checked
                          </button>
                        )}
                        <button
                          type="button"
                          tabIndex={-1}
                          onClick={(e) => {
                            e.stopPropagation();
                            setDismissing(flag.flag_id);
                            setNote('');
                          }}
                          className="text-[11px] font-semibold text-muted hover:text-ink hover:underline"
                        >
                          Dismiss…
                        </button>
                      </div>
                    )}
                    {dismissing === flag.flag_id && (
                      <form
                        className="mt-1 space-y-1 pl-[19px]"
                        onClick={(e) => e.stopPropagation()}
                        onKeyDown={(e) => e.stopPropagation()}
                        onSubmit={(e) => {
                          e.preventDefault();
                          if (onClearFlag(flag.flag_id, 'dismissed', note)) setDismissing(null);
                        }}
                      >
                        <input autoFocus className="control h-7 min-h-0 px-2 py-0.5 text-[12px]" placeholder="Why it does not apply (needed)" value={note} onChange={(e) => setNote(e.target.value)} />
                        <div className="flex justify-end gap-1">
                          <Button size="sm" variant="ghost" className="h-7" onClick={() => setDismissing(null)}>
                            Cancel
                          </Button>
                          <Button size="sm" type="submit" className="h-7" disabled={!note.trim()}>
                            Dismiss flag
                          </Button>
                        </div>
                      </form>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        {group('needs', queue.needs)}
        {group('quick', queue.quick)}
        {group('collapsed', queue.collapsed)}
        {!queue.flags.length && !queue.needs.length && !queue.quick.length && !queue.collapsed.length && <p className="px-2.5 py-3 text-[12px] text-muted">The call changed nothing that needs review.</p>}
      </div>
    </section>
  );
}
