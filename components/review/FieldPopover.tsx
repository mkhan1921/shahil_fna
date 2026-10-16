'use client';

/**
 * The field popover (spec §11.1): the value as a short phrase with its shape, its status, who said
 * it and how sure the system is, the one-line reason it matters — then, kept apart, what was said
 * (the verbatim quotes, each playable from 2 s before) and what the system takes it to mean (its
 * reading and the normalised value). Below that, what the fact does to the needs analysis, and
 * the reviewer's actions (§11.2).
 */
import { AlertTriangle, ArrowRightLeft, Calculator, Check, CircleSlash, HelpCircle, Lightbulb, Pause, Pencil, Play, Trash2, UserRound, X } from 'lucide-react';
import { useMemo, useState, type CSSProperties, type KeyboardEvent, type ReactNode, type Ref } from 'react';
import { analyse } from '@/lib/fna/analysis';
import { money, moneyCompact } from '@/lib/format';
import { slotById, type SlotDef } from '@/lib/review/catalog';
import { explain, pyPercent } from '@/lib/review/dist';
import { compareNeeds, movedNeeds, type NeedDelta } from '@/lib/review/impact';
import { derivationText, factSubject, instanceChoices } from '@/lib/review/labels';
import { REJECT_REASONS, type RejectReason } from '@/lib/review/ops';
import { IDENTITY_FIELD, isListContainer, parsePath } from '@/lib/review/paths';
import { pBand } from '@/lib/review/queue';
import { applyAction, isDecided, isUsable, type ReviewAction, type ReviewFact, type ReviewSession } from '@/lib/review/session';
import { clock, readingsFor, speakerName } from '@/lib/review/transcript';
import type { Attribution, FactStatus } from '@/lib/review/types';
import { toEditor } from '@/lib/review/values';
import { Badge, Button, StatusPill, cx } from '../ui';
import Sparkline from './Sparkline';
import type { CallAudio } from './useCallAudio';
import ValueEditor from './ValueEditor';

export type Panel = 'view' | 'edit' | 'status' | 'reject' | 'reattribute' | 'speaker';

export const STATUS_LABEL: Record<FactStatus, string> = {
  stated: 'Stated',
  implied: 'Implied',
  explicitly_unknown: 'Unknown',
  not_discussed: 'Not discussed',
  contradicted: 'Contradicted',
};

export const ATTRIBUTION_LABEL: Record<Attribution, string> = {
  client: 'The client said it',
  advisor_suggested: 'The adviser’s suggestion',
  advisor_confirmed_by_client: 'Adviser’s figure, client agreed',
  mixed: 'Client and adviser',
};

const BAND_TONE = { high: 'good', medium: 'warning', low: 'critical' } as const;

export const bandBadge = (p: number) => {
  const band = pBand(p);
  return (
    <Badge tone={BAND_TONE[band]}>
      <span className="capitalize">{band}</span> confidence · {pyPercent(p)}
    </Badge>
  );
};

/** The needs this fact moves: the analysis without it against the analysis with it. */
const useFactImpact = (session: ReviewSession, fact: ReviewFact, asAt: Date): { deltas: NeedDelta[]; direction: 'keeps' | 'would' } | null =>
  useMemo(() => {
    if (!fact.exported || session.submittedAt) return null;
    const stamp = { reviewerId: session.reviewerId, at: asAt.toISOString() };
    try {
      if (isUsable(fact)) {
        const without = applyAction(session, { type: 'reject', factId: fact.factId, reason: 'other' }, stamp);
        return { deltas: movedNeeds(compareNeeds(analyse(without.working, asAt), analyse(session.working, asAt))), direction: 'keeps' };
      }
      if (fact.review.status === 'rejected' && fact.quantity) {
        const withIt = applyAction(session, { type: 'accept', factId: fact.factId }, stamp);
        return { deltas: movedNeeds(compareNeeds(analyse(session.working, asAt), analyse(withIt.working, asAt))), direction: 'would' };
      }
    } catch {
      return null;
    }
    return null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.working, fact.factId, fact.review.status, fact.quantity]);

function Section({ title, children, note }: { title: string; children: ReactNode; note?: ReactNode }) {
  return (
    <section className="border-t border-line px-3 py-2">
      <h3 className="mb-1 flex items-baseline justify-between text-[10.5px] font-semibold uppercase tracking-[0.07em] text-ink-2">
        {title}
        {note && <span className="font-normal normal-case tracking-normal text-faint">{note}</span>}
      </h3>
      {children}
    </section>
  );
}

const need = (d: NeedDelta['after']) => (d.monthly ? `${money(d.shortfall)} p.m.` : moneyCompact(d.shortfall));

export function ImpactList({ deltas, names }: { deltas: NeedDelta[]; names: Record<string, string> }) {
  return (
    <ul className="space-y-1 text-[11.5px]">
      {deltas.map((d) => (
        <li key={d.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
          <span className="truncate text-ink-2">
            {d.after.title}
            {d.after.person && <span className="text-muted"> · {names[d.after.person]}</span>}
          </span>
          <span className="flex items-center gap-1 tabular">
            {d.statusChanged ? <StatusPill status={d.before.status} /> : <span className="text-muted">{d.before.shortfall > 0.5 ? need(d.before) : 'met'}</span>}
            <span className="text-faint" aria-hidden>
              →
            </span>
            {d.statusChanged ? <StatusPill status={d.after.status} /> : <span className="font-medium text-ink">{d.after.shortfall > 0.5 ? `short ${need(d.after)}` : 'met'}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function FieldPopover({
  session,
  fact,
  asAt,
  audio,
  panel,
  setPanel,
  dispatch,
  onClose,
  onDecided,
  onConfirmTerm,
  onKeyDown,
  rootRef,
  style,
}: {
  session: ReviewSession;
  fact: ReviewFact;
  asAt: Date;
  audio: CallAudio;
  panel: Panel;
  setPanel: (p: Panel) => void;
  dispatch: (a: ReviewAction) => boolean;
  onClose: () => void;
  /** After a decision on this fact: move on in the queue. */
  onDecided: () => void;
  onConfirmTerm: () => void;
  /** The queue's keys work in the popover too (Enter accepts, Tab moves on …). */
  onKeyDown?: (e: KeyboardEvent<HTMLDivElement>) => void;
  rootRef?: Ref<HTMLDivElement>;
  style?: CSSProperties;
}) {
  const slot = slotById(fact.slotId) as SlotDef;
  const subject = factSubject(session, fact);
  const flags = session.flags.filter((f) => f.fact_id === fact.factId && !f.cleared);
  const conflict = session.conflicts.find((c) => c.path === fact.path);
  const impact = useFactImpact(session, fact, asAt);
  const names = { client: session.working.client.firstName || 'Client', spouse: session.working.spouse.firstName || 'Spouse' };
  const pp = parsePath(fact.path);
  const identity = pp.key !== null && isListContainer(pp.container) && pp.field === IDENTITY_FIELD[pp.container];
  const done = !!session.submittedAt;
  const [reason, setReason] = useState<RejectReason>('misheard');
  const [note, setNote] = useState('');
  const [status, setStatus] = useState<'stated' | 'implied' | 'explicitly_unknown' | 'not_discussed'>(fact.status === 'implied' ? 'implied' : 'stated');
  const act = (a: ReviewAction, next = true) => {
    if (dispatch(a)) {
      setPanel('view');
      setNote('');
      if (next) onDecided();
    }
  };
  const evidence = [...fact.evidence].sort((a, b) => (a.role === 'context' ? 1 : 0) - (b.role === 'context' ? 1 : 0) || a.t_start - b.t_start);
  const valued = fact.hypotheses.filter((h) => h.value && h.weight > 0);
  const priorNumber = fact.prior && typeof fact.prior.point === 'number' ? fact.prior.point : typeof fact.prior?.p50 === 'number' ? fact.prior.p50 : null;

  return (
    <div
      ref={rootRef}
      role="dialog"
      tabIndex={-1}
      aria-label={`Review ${subject ? `${subject}, ` : ''}${fact.label}`}
      onKeyDown={onKeyDown}
      className="no-print fixed z-40 flex max-h-[70vh] w-[min(27rem,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-xl border border-line-strong bg-surface shadow-[0_12px_40px_rgba(15,30,50,0.22)] outline-none"
      style={style}
    >
      <header className="flex items-start justify-between gap-2 border-b border-line bg-wash px-3 py-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-[0.07em] text-muted">
            <span className={cx('rounded px-1 text-white', fact.tier === 'A' ? 'bg-brand' : fact.tier === 'B' ? 'bg-brand-2' : 'bg-faint')}>Tier {fact.tier}</span>
            {!fact.exported && <span>Forecast only</span>}
            {fact.autoAccepted && <span>Auto-accepted</span>}
          </div>
          <h2 className="truncate text-[14px] font-semibold text-ink">
            {subject && <span className="text-ink-2">{subject} · </span>}
            {fact.label}
          </h2>
        </div>
        <button type="button" onClick={onClose} className="focus-ring rounded-md p-1 text-muted hover:bg-surface hover:text-ink" aria-label="Close (Esc)">
          <X size={16} />
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="space-y-2 px-3 py-2.5">
          <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
            <div className="min-w-0">
              <div className={cx('text-[17px] font-semibold leading-6 tracking-tight text-ink tabular', fact.review.status === 'rejected' && 'line-through decoration-critical/60')}>
                {fact.quantity ? fact.valueText : fact.status === 'explicitly_unknown' ? 'Not known' : 'Not discussed'}
              </div>
              {fact.priorText && fact.change !== 'confirmed' && <div className="text-[11.5px] text-muted">Before the call: {fact.priorText}</div>}
              {fact.change === 'confirmed' && <div className="text-[11.5px] text-good-ink">Confirms the file: {fact.priorText}</div>}
              {fact.derivation && (
                <div className="flex items-center gap-1 text-[11.5px] font-medium text-brand-2">
                  <Calculator size={12} aria-hidden /> {derivationText(fact.derivation)}
                </div>
              )}
            </div>
            {fact.quantity && <Sparkline value={fact.quantity} prior={priorNumber} label={fact.label} />}
          </div>
          <div className="flex flex-wrap items-center gap-1">
            <Badge tone={fact.status === 'contradicted' ? 'critical' : fact.status === 'stated' ? 'brand' : 'neutral'}>{STATUS_LABEL[fact.status]}</Badge>
            <Badge tone={fact.attribution === 'advisor_suggested' ? 'warning' : 'neutral'}>
              <UserRound size={11} aria-hidden /> {ATTRIBUTION_LABEL[fact.attribution]}
            </Badge>
            {fact.origin === 'pipeline' && bandBadge(fact.pCorrect)}
            {isDecided(fact) && (
              <Badge tone={fact.review.status === 'rejected' ? 'critical' : 'good'}>
                {fact.review.status === 'rejected' ? <X size={11} aria-hidden /> : <Check size={11} aria-hidden />}
                {fact.review.status === 'accepted' ? 'Accepted' : fact.review.status === 'edited' ? 'Edited' : `Rejected${fact.review.reason ? `: ${REJECT_REASONS[fact.review.reason as RejectReason] ?? fact.review.reason.replace(/_/g, ' ')}` : ''}`}
              </Badge>
            )}
          </div>
          {fact.why && (
            <p className="flex gap-1.5 text-[12px] leading-4 text-ink-2">
              <Lightbulb size={13} className="mt-px shrink-0 text-warning-ink" aria-hidden />
              {fact.why}
            </p>
          )}
          {flags.map((f) => (
            <p key={f.flag_id} className="flex gap-1.5 rounded-md bg-critical-soft px-2 py-1 text-[11.5px] leading-4 text-ink-2">
              <AlertTriangle size={13} className="mt-px shrink-0 text-critical" aria-hidden />
              <span>
                <strong className="font-semibold">Blocks submission:</strong> {f.message}
              </span>
            </p>
          ))}
          {conflict && (
            <p className="rounded-md bg-warning-soft px-2 py-1 text-[11.5px] leading-4 text-ink-2">
              After the file was exported for the call it was changed on this device to <strong>{String(conflict.local)}</strong>. The call proposes <strong>{String(conflict.call)}</strong>: keep one by accepting or editing.
            </p>
          )}
        </div>

        <Section title="What was said" note={audio.available ? 'click a quote to play it' : evidence.length ? 'no recording attached' : undefined}>
          {evidence.length === 0 ? (
            <p className="text-[12px] text-muted">{fact.source === 'fna_default' ? 'Nobody discussed this: the FNA default is assumed.' : fact.origin === 'reviewer' ? 'Added by the reviewer.' : 'No quote.'}</p>
          ) : (
            <ul className="space-y-1.5">
              {evidence.map((e) => {
                const key = `${e.segment_id}:${e.char_start}`;
                const readings = readingsFor(session.bundle.transcript, e).filter((n) => n.readings.length > 1);
                return (
                  <li key={key + e.role}>
                    <button
                      type="button"
                      disabled={!audio.available}
                      onClick={() => audio.play(e)}
                      className={cx('group block w-full rounded-md px-1.5 py-1 text-left', audio.available ? 'hover:bg-wash' : 'cursor-default', e.role === 'context' && 'opacity-75')}
                      title={audio.available ? `Play from ${clock(Math.max(0, e.t_start - 2))}` : undefined}
                    >
                      <span className="flex items-center gap-1.5 text-[10.5px] text-muted">
                        {audio.available && (audio.playing === key ? <Pause size={11} className="text-brand-2" /> : <Play size={11} className="text-brand-2" />)}
                        <span className="font-semibold text-ink-2">{speakerName(e.speaker_role, session.working)}</span>
                        <span className="tabular">{clock(e.t_start)}</span>
                        {e.role !== 'supports' && <span className={cx('rounded px-1', e.role === 'contradicts' ? 'bg-critical-soft text-critical-ink' : 'bg-canvas')}>{e.role}</span>}
                        {e.asr_mean_conf < 0.6 && <span className="text-warning-ink">hard to hear</span>}
                      </span>
                      <q className="block font-serif text-[13px] leading-5 text-ink">{e.quote}</q>
                    </button>
                    {readings.map((n) => (
                      <p key={n.span_id} className="ml-1.5 text-[11px] text-muted">
                        “{n.surface}” could be {n.readings.map((r) => `${typeof r.value === 'number' ? money(r.value) : r.value} (${pyPercent(r.weight)})`).join(' or ')}
                        {n.second_pass_reading && `; re-listening heard ${typeof n.second_pass_reading.value === 'number' ? money(n.second_pass_reading.value) : n.second_pass_reading.value}`}
                      </p>
                    ))}
                  </li>
                );
              })}
            </ul>
          )}
          {fact.hedges.length > 0 && <p className="mt-1 text-[11px] text-muted">Hedged: {fact.hedges.map((h) => `“${h}”`).join(', ')}</p>}
        </Section>

        <Section title="What the system takes it to mean">
          {fact.interpretation && <p className="text-[12px] leading-[17px] text-ink-2">{fact.interpretation}</p>}
          {fact.quantity && <p className="mt-1 text-[11.5px] text-muted">Normalised: {explain(fact.quantity)}.</p>}
          {valued.length > 1 && (
            <ul className="mt-1 space-y-0.5 text-[11.5px] text-ink-2">
              {valued.map((h, i) => (
                <li key={i} className="flex gap-1.5">
                  <span className="w-9 shrink-0 text-right font-medium tabular">{pyPercent(h.weight)}</span>
                  <span>
                    {h.value && explain(h.value)}
                    {h.rationale && <span className="text-muted"> — {h.rationale}</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {fact.derivation && (
            <p className="mt-1 text-[11.5px] text-ink-2">
              {derivationText(fact.derivation)}
              {valued.length === 1 && valued[0].rationale && !valued[0].rationale.startsWith('derived:') ? `: ${valued[0].rationale}` : ''}.
            </p>
          )}
          {fact.termResolution && (
            <div className="mt-1.5 rounded-md bg-wash px-2 py-1.5 text-[11.5px] text-ink-2">
              <div className="flex items-center gap-1 font-medium text-ink">
                <HelpCircle size={12} aria-hidden /> Term “{fact.termResolution.term}”: {fact.termResolution.status === 'confirmed' || fact.termResolution.status === 'corrected' ? `${fact.termResolution.status} as ${String(fact.termResolution.confirmed_name ?? '')}` : 'not resolved'}
              </div>
              {fact.termResolution.candidates?.length ? <div className="text-muted">Candidates: {fact.termResolution.candidates.map((c) => `${c.canonical_name}${c.score !== undefined ? ` (${pyPercent(c.score)})` : ''}`).join('; ')}</div> : null}
              {!done && (
                <button type="button" onClick={onConfirmTerm} className="mt-0.5 font-semibold text-brand-2 hover:underline">
                  Confirm or correct the term…
                </button>
              )}
            </div>
          )}
        </Section>

        {fact.exported ? (
          impact && impact.deltas.length > 0 ? (
            <Section title={impact.direction === 'keeps' ? 'What it does to the needs' : 'Accepting it would move'} note="without → with it">
              <ImpactList deltas={impact.deltas} names={names} />
            </Section>
          ) : impact ? (
            <Section title="What it does to the needs">
              <p className="text-[12px] text-muted">No need’s shortfall or status depends on it.</p>
            </Section>
          ) : null
        ) : (
          <Section title="What it does to the plan">
            <p className="text-[12px] text-muted">Forecast only: the FNA has no field for it, so it moves the Monte Carlo forecast, not the needs analysis.</p>
          </Section>
        )}
      </div>

      {!done && (
        <footer className="border-t border-line-strong bg-wash px-3 py-2">
          {panel === 'view' && (
            <div className="flex flex-wrap items-center gap-1.5">
              <Button size="sm" variant="primary" icon={<Check size={14} />} onClick={() => act({ type: 'accept', factId: fact.factId })} title="Accept and move on (Enter)">
                {fact.review.status === 'accepted' || fact.review.status === 'edited' ? 'Keep' : 'Accept'}
              </Button>
              <Button size="sm" icon={<Pencil size={13} />} onClick={() => setPanel('edit')} title="Edit the value (E)">
                Edit
              </Button>
              <Button size="sm" onClick={() => setPanel('status')} title="Change the status (U marks it unknown)">
                Status
              </Button>
              <Button size="sm" variant="danger" icon={<CircleSlash size={13} />} onClick={() => setPanel('reject')} title="Reject (R)">
                Reject
              </Button>
              <Button size="sm" variant="ghost" icon={<ArrowRightLeft size={13} />} onClick={() => setPanel(instanceChoices(session, slot).length ? 'reattribute' : 'speaker')} title="Re-attribute to another person, row or speaker">
                Re-attribute
              </Button>
              {identity && (
                <Button size="sm" variant="ghost" icon={<Trash2 size={13} />} onClick={() => act({ type: 'removeRow', factId: fact.factId, note: 'removed in review' })} title="Remove this whole row from the FNA">
                  Remove row
                </Button>
              )}
            </div>
          )}
          {panel === 'edit' && (
            <ValueEditor slot={slot} initial={toEditor(slot, fact.quantity)} onCancel={() => setPanel('view')} onSave={(value) => act({ type: 'edit', factId: fact.factId, value })} saveLabel="Save edit" />
          )}
          {panel === 'status' && (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-1">
                {(['stated', 'implied', 'explicitly_unknown', 'not_discussed'] as const).map((st) => (
                  <button
                    key={st}
                    type="button"
                    onClick={() => setStatus(st)}
                    className={cx('rounded-md border px-2 py-1 text-[12px] font-medium', status === st ? 'border-brand-2 bg-brand-soft text-brand' : 'border-line-strong bg-surface text-ink-2 hover:bg-wash')}
                  >
                    {STATUS_LABEL[st]}
                  </button>
                ))}
              </div>
              {status === 'stated' || status === 'implied' ? (
                <ValueEditor slot={slot} initial={toEditor(slot, fact.quantity)} onCancel={() => setPanel('view')} onSave={(value) => act({ type: 'setStatus', factId: fact.factId, status, value })} saveLabel={`Save as ${STATUS_LABEL[status].toLowerCase()}`} autoFocus={false} />
              ) : (
                <div className="space-y-1.5">
                  <input className="control h-8 min-h-0 px-2 py-1 text-[13px]" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
                  <div className="flex justify-end gap-1.5">
                    <Button size="sm" variant="ghost" onClick={() => setPanel('view')}>
                      Cancel
                    </Button>
                    <Button size="sm" variant="primary" onClick={() => act({ type: 'setStatus', factId: fact.factId, status, note: note || undefined })}>
                      Mark {STATUS_LABEL[status].toLowerCase()}
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}
          {panel === 'reject' && (
            <div className="space-y-2">
              <div className="text-[11.5px] font-medium text-ink-2">Why is it wrong?</div>
              <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Reason">
                {(Object.keys(REJECT_REASONS) as RejectReason[]).map((r) => (
                  <button
                    key={r}
                    type="button"
                    role="radio"
                    aria-checked={reason === r}
                    onClick={() => setReason(r)}
                    className={cx('rounded-md border px-2 py-1 text-[12px] font-medium', reason === r ? 'border-critical bg-critical-soft text-critical-ink' : 'border-line-strong bg-surface text-ink-2 hover:bg-wash')}
                  >
                    {REJECT_REASONS[r]}
                  </button>
                ))}
              </div>
              <input
                className="control h-8 min-h-0 px-2 py-1 text-[13px]"
                placeholder={reason === 'other' ? 'What was wrong (needed)' : 'Note (optional)'}
                value={note}
                autoFocus
                onChange={(e) => setNote(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (reason !== 'other' || note.trim())) act({ type: 'reject', factId: fact.factId, reason, note: note || undefined });
                }}
              />
              <p className="text-[11px] text-muted">A rejected fact leaves the forecast; the field goes back to {fact.priorText ? `the file’s ${fact.priorText}` : 'the FNA default'}.</p>
              <div className="flex justify-end gap-1.5">
                <Button size="sm" variant="ghost" onClick={() => setPanel('view')}>
                  Cancel
                </Button>
                <Button size="sm" variant="danger" disabled={reason === 'other' && !note.trim()} onClick={() => act({ type: 'reject', factId: fact.factId, reason, note: note || undefined })}>
                  Reject
                </Button>
              </div>
            </div>
          )}
          {panel === 'reattribute' && (
            <div className="space-y-1.5">
              <div className="text-[11.5px] font-medium text-ink-2">It is about someone or something else:</div>
              <div className="flex max-h-32 flex-wrap gap-1 overflow-y-auto">
                {instanceChoices(session, slot)
                  .filter((c) => c.instance !== fact.instanceKey)
                  .map((c) => (
                    <button
                      key={c.instance ?? 'new'}
                      type="button"
                      onClick={() => act({ type: 'reattribute', factId: fact.factId, to: c.newRow ? { kind: 'new-row' } : { kind: 'instance', instance: c.instance } })}
                      className="rounded-md border border-line-strong bg-surface px-2 py-1 text-[12px] font-medium text-ink-2 hover:bg-brand-soft hover:text-brand"
                    >
                      {c.label}
                    </button>
                  ))}
              </div>
              <div className="flex items-center justify-between">
                <button type="button" onClick={() => setPanel('speaker')} className="text-[11.5px] font-semibold text-brand-2 hover:underline">
                  Wrong speaker instead…
                </button>
                <Button size="sm" variant="ghost" onClick={() => setPanel('view')}>
                  Cancel
                </Button>
              </div>
            </div>
          )}
          {panel === 'speaker' && (
            <div className="space-y-1.5">
              <div className="text-[11.5px] font-medium text-ink-2">Who said it?</div>
              <div className="flex flex-wrap gap-1">
                {(Object.keys(ATTRIBUTION_LABEL) as Attribution[]).map((a) => (
                  <button
                    key={a}
                    type="button"
                    onClick={() => act({ type: 'reattributeSpeaker', factId: fact.factId, attribution: a }, false)}
                    className={cx('rounded-md border px-2 py-1 text-[12px] font-medium', fact.attribution === a ? 'border-brand-2 bg-brand-soft text-brand' : 'border-line-strong bg-surface text-ink-2 hover:bg-wash')}
                  >
                    {ATTRIBUTION_LABEL[a]}
                  </button>
                ))}
              </div>
              <div className="flex justify-end">
                <Button size="sm" variant="ghost" onClick={() => setPanel('view')}>
                  Done
                </Button>
              </div>
            </div>
          )}
        </footer>
      )}
    </div>
  );
}

/** The fact's value as the adviser reads it, for compact lists. */
export const shortValue = (f: ReviewFact) => (f.quantity ? f.valueText : f.status === 'explicitly_unknown' ? 'not known' : 'not discussed');
