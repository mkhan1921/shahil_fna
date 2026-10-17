'use client';

/**
 * The review's dialogs (spec §11.2–11.4): the follow-up questions, confirming an unknown term,
 * adding a fact the call missed, the whole transcript, and submitting the review.
 */
import { CheckCircle2, Download, ExternalLink, MessageSquarePlus, Plus, Send, Trash2, X } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { EXPENSE_ITEMS } from '@/lib/fna/catalog';
import type { MeetingNote } from '@/lib/review/api';
import { CATALOG, categoryLabel, fieldLabel, groupLabel, slotById, type SlotDef } from '@/lib/review/catalog';
import { pyPercent } from '@/lib/review/dist';
import { factTitle, householdName, instanceChoices, type InstanceChoice } from '@/lib/review/labels';
import { getPath } from '@/lib/review/paths';
import { buildQueue } from '@/lib/review/queue';
import { followupFacts, isDecided, isReviewItem, type ReviewAction, type ReviewFact, type ReviewSession } from '@/lib/review/session';
import { clock, speakerName } from '@/lib/review/transcript';
import type { Attribution, Evidence, FollowUp } from '@/lib/review/types';
import { toEditor } from '@/lib/review/values';
import Modal from '../Modal';
import { Badge, Button, Callout, Segmented, cx } from '../ui';
import { ATTRIBUTION_LABEL } from './FieldPopover';
import TranscriptView from './TranscriptView';
import type { CallAudio } from './useCallAudio';
import ValueEditor from './ValueEditor';

type Dispatch = (a: ReviewAction) => boolean;

const STATUS_TONE: Record<FollowUp['status'], 'neutral' | 'brand' | 'good'> = { open: 'brand', asked: 'neutral', answered: 'good', dropped: 'neutral' };

/* ------------------------------------------------------------------ */
/* Follow-up questions (§10.4)                                         */
/* ------------------------------------------------------------------ */

export function FollowupDialog({
  session,
  open,
  onClose,
  dispatch,
  onAnswer,
}: {
  session: ReviewSession;
  open: boolean;
  onClose: () => void;
  dispatch: Dispatch;
  /** API mode: send the client's answer to the pipeline. */
  onAnswer?: (followupId: string, text: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [text, setText] = useState('');
  const [answering, setAnswering] = useState<string | null>(null);
  const [answer, setAnswer] = useState('');
  const [answerState, setAnswerState] = useState<Record<string, 'sending' | 'sent' | string>>({});
  const [adding, setAdding] = useState('');
  const [about, setAbout] = useState('');
  const done = !!session.submittedAt;
  const facts = useMemo(() => Object.values(session.facts).filter((f) => isReviewItem(f) && f.tier !== 'C'), [session.facts]);
  const list = [...session.followups].sort((a, b) => Number(a.status === 'dropped') - Number(b.status === 'dropped'));

  const send = async (id: string) => {
    if (!onAnswer || !answer.trim()) return;
    setAnswerState((s) => ({ ...s, [id]: 'sending' }));
    try {
      await onAnswer(id, answer.trim());
      setAnswerState((s) => ({ ...s, [id]: 'sent' }));
      setAnswering(null);
      setAnswer('');
    } catch (e) {
      setAnswerState((s) => ({ ...s, [id]: e instanceof Error ? e.message : String(e) }));
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Follow-up questions for the client" width="max-w-2xl">
      <div className="-mx-1 max-h-[65vh] space-y-2 overflow-y-auto px-1">
        <p className="text-[12.5px] text-muted">The questions whose answers would move the plan most. Reword them in your voice, drop what you will not ask, or add your own.</p>
        {list.map((q) => {
          const linked = followupFacts(session, q);
          const pipeline = !q.followup_id.startsWith('added-');
          const state = answerState[q.followup_id];
          return (
            <div key={q.followup_id} className={cx('rounded-lg border border-line px-3 py-2', q.status === 'dropped' && 'opacity-60')}>
              <div className="flex items-start justify-between gap-2">
                {editing === q.followup_id ? (
                  <textarea autoFocus rows={3} className="control text-[13px]" value={text} onChange={(e) => setText(e.target.value)} aria-label="Question" />
                ) : (
                  <p className={cx('text-[13.5px] leading-5 text-ink', q.status === 'dropped' && 'line-through')}>{q.question}</p>
                )}
                <Badge tone={STATUS_TONE[q.status]}>{q.status}</Badge>
              </div>
              <div className="mt-1 text-[11.5px] leading-4 text-muted">
                {q.why}
                {q.expected_pos_change > 0 && pipeline && <span> · could move the success odds by about {pyPercent(q.expected_pos_change)}</span>}
              </div>
              {linked.length > 0 && <div className="mt-0.5 text-[11.5px] text-ink-2">About: {linked.map((f) => factTitle(session, f)).join('; ')}</div>}
              {state && state !== 'sending' && state !== 'sent' && <p className="mt-1 text-[11.5px] text-critical-ink">{state}</p>}
              {state === 'sent' && <p className="mt-1 text-[11.5px] text-good-ink">Answer sent to the pipeline: it re-runs the facts it touches.</p>}
              {!done && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {editing === q.followup_id ? (
                    <>
                      <Button
                        size="sm"
                        variant="primary"
                        onClick={() => {
                          if (dispatch({ type: 'followup', change: { action: 'reword', followupId: q.followup_id, question: text } })) setEditing(null);
                        }}
                      >
                        Save wording
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                        Cancel
                      </Button>
                    </>
                  ) : (
                    q.status !== 'dropped' && (
                      <>
                        <Button
                          size="sm"
                          onClick={() => {
                            setEditing(q.followup_id);
                            setText(q.question);
                          }}
                        >
                          Reword
                        </Button>
                        <Button size="sm" variant="ghost" icon={<Trash2 size={13} />} onClick={() => dispatch({ type: 'followup', change: { action: 'drop', followupId: q.followup_id } })}>
                          Drop
                        </Button>
                        {onAnswer && pipeline && q.status !== 'answered' && answering !== q.followup_id && (
                          <Button size="sm" variant="ghost" icon={<MessageSquarePlus size={13} />} onClick={() => setAnswering(q.followup_id)}>
                            Record the answer
                          </Button>
                        )}
                      </>
                    )
                  )}
                </div>
              )}
              {answering === q.followup_id && (
                <div className="mt-1.5 space-y-1">
                  <textarea autoFocus rows={2} className="control text-[13px]" placeholder="What the client answered" value={answer} onChange={(e) => setAnswer(e.target.value)} aria-label="Answer" />
                  <div className="flex justify-end gap-1.5">
                    <Button size="sm" variant="ghost" onClick={() => setAnswering(null)}>
                      Cancel
                    </Button>
                    <Button size="sm" variant="primary" icon={<Send size={13} />} disabled={!answer.trim() || state === 'sending'} onClick={() => void send(q.followup_id)}>
                      {state === 'sending' ? 'Sending…' : 'Send answer'}
                    </Button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {!done && (
          <form
            className="space-y-1.5 rounded-lg border border-dashed border-line-strong px-3 py-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (dispatch({ type: 'followup', change: { action: 'add', question: adding, factIds: about ? [about] : [] } })) {
                setAdding('');
                setAbout('');
              }
            }}
          >
            <label className="block text-[12px] font-medium text-ink-2" htmlFor="followup-new">
              Add a question
            </label>
            <textarea id="followup-new" rows={2} className="control text-[13px]" placeholder="In the words you would use with the client" value={adding} onChange={(e) => setAdding(e.target.value)} />
            <div className="flex flex-wrap items-center gap-2">
              <select className="control h-8 min-h-0 max-w-full flex-1 px-2 py-1 text-[12.5px]" value={about} onChange={(e) => setAbout(e.target.value)} aria-label="About which fact">
                <option value="">About no particular fact</option>
                {facts.map((f) => (
                  <option key={f.factId} value={f.factId}>
                    {factTitle(session, f)}
                  </option>
                ))}
              </select>
              <Button size="sm" type="submit" icon={<Plus size={13} />} disabled={!adding.trim()}>
                Add question
              </Button>
            </div>
          </form>
        )}
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ */
/* Unknown terms (§5.4 → §11.2)                                        */
/* ------------------------------------------------------------------ */

export function TermDialog({ session, fact, onClose, dispatch }: { session: ReviewSession; fact: ReviewFact | null; onClose: () => void; dispatch: Dispatch }) {
  const term = fact?.termResolution ?? null;
  const slot = fact ? slotById(fact.slotId) : undefined;
  const candidates = term?.candidates ?? [];
  const [pick, setPick] = useState<number>(candidates.length ? 0 : -1);
  const [name, setName] = useState(candidates[0]?.canonical_name ?? '');
  const [type, setType] = useState(candidates[0]?.product_type ?? (typeof term?.fallback === 'string' ? term.fallback : ''));
  const [provider, setProvider] = useState(candidates[0]?.provider ?? '');
  if (!fact || !term || !slot) return null;
  const types = slot.categories ?? [];
  const choose = (i: number) => {
    setPick(i);
    const c = candidates[i];
    if (c) {
      setName(c.canonical_name);
      setType(c.product_type ?? type);
      setProvider(c.provider ?? '');
    } else setName('');
  };
  const top = candidates[0];
  const confirmed = pick === 0 && !!top && name.trim() === top.canonical_name && (top.product_type ?? '') === type;
  const quote = fact.evidence[0];

  return (
    <Modal
      open
      onClose={onClose}
      title={`What is “${term.term}”?`}
      width="max-w-lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!name.trim()}
            onClick={() => {
              if (dispatch({ type: 'confirmTerm', factId: fact.factId, canonicalName: name, productType: type, provider, confirmed })) onClose();
            }}
          >
            {confirmed ? 'Confirm' : 'Save correction'}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-[13px]">
        {quote && (
          <p className="rounded-md bg-wash px-2.5 py-1.5 text-ink-2">
            <span className="text-[11px] text-muted">
              {speakerName(quote.speaker_role, session.working)} at {clock(quote.t_start)}:
            </span>{' '}
            <q className="font-serif">{quote.quote}</q>
          </p>
        )}
        <p className="text-[12.5px] text-muted">The system could not place this term. Your answer is kept for the practice’s term list, so the next call recognises it.</p>
        <div role="radiogroup" aria-label="What the term means" className="space-y-1">
          {candidates.map((c, i) => (
            <label key={c.canonical_name} className={cx('flex cursor-pointer items-start gap-2 rounded-md border px-2.5 py-1.5', pick === i ? 'border-brand-2 bg-brand-soft' : 'border-line hover:bg-wash')}>
              <input type="radio" name="term" className="mt-1" checked={pick === i} onChange={() => choose(i)} />
              <span className="min-w-0">
                <span className="block font-medium text-ink">{c.canonical_name}</span>
                <span className="block text-[11.5px] text-muted">
                  {[c.product_type && (types.includes(c.product_type) ? categoryLabel(slot, c.product_type) : c.product_type), c.provider, c.source, c.score !== undefined && `${pyPercent(c.score)} match`].filter(Boolean).join(' · ')}
                </span>
              </span>
            </label>
          ))}
          <label className={cx('flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-1.5', pick === -1 ? 'border-brand-2 bg-brand-soft' : 'border-line hover:bg-wash')}>
            <input type="radio" name="term" checked={pick === -1} onChange={() => choose(-1)} />
            <span className="font-medium text-ink">Something else</span>
          </label>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <label className="block text-[12px] font-medium text-ink-2 sm:col-span-2">
            Name
            <input className="control mt-0.5 h-8 min-h-0 px-2 py-1 text-[13px]" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="block text-[12px] font-medium text-ink-2">
            {slot.label}
            {types.length ? (
              <select className="control mt-0.5 h-8 min-h-0 px-2 py-1 text-[13px]" value={type} onChange={(e) => setType(e.target.value)}>
                {types.map((t) => (
                  <option key={t} value={t}>
                    {categoryLabel(slot, t)}
                  </option>
                ))}
              </select>
            ) : (
              <input className="control mt-0.5 h-8 min-h-0 px-2 py-1 text-[13px]" value={type} onChange={(e) => setType(e.target.value)} />
            )}
          </label>
          <label className="block text-[12px] font-medium text-ink-2">
            Provider (optional)
            <input className="control mt-0.5 h-8 min-h-0 px-2 py-1 text-[13px]" value={provider} onChange={(e) => setProvider(e.target.value)} />
          </label>
        </div>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ */
/* Adding a fact the call missed (§11.2 "Add fact")                    */
/* ------------------------------------------------------------------ */

const slotTitle = (slot: SlotDef) => `${groupLabel(slot.group)} · ${fieldLabel(slot, null)}`;

interface Pick {
  slot: SlotDef;
  /** Budget lines are picked by name: the instance comes with the pick. */
  choice: InstanceChoice | null;
  title: string;
}

/**
 * What a reviewer can add: every FNA field, and forecast-only fields that belong to a person.
 * Each budget line is its own pick ("Budget · Fuel"), so it is found by its name.
 */
const PICKS: Pick[] = CATALOG.slots
  .filter((s) => s.exported || s.cardinality === 'per_person')
  .flatMap((slot): Pick[] =>
    slot.slot_id === 'expenses.items[]'
      ? EXPENSE_ITEMS.map((e) => ({ slot, choice: { instance: e.key, label: e.label }, title: `Budget · ${e.label}` }))
      : [{ slot, choice: null, title: slotTitle(slot) }],
  );

export function AddFactDialog({
  session,
  open,
  onClose,
  dispatch,
  audio,
  error,
}: {
  session: ReviewSession;
  open: boolean;
  onClose: () => void;
  dispatch: Dispatch;
  audio: CallAudio;
  error: string | null;
}) {
  const [query, setQuery] = useState('');
  const [slotId, setSlotId] = useState<string | null>(null);
  const [choice, setChoice] = useState<InstanceChoice | null>(null);
  const [status, setStatus] = useState<'stated' | 'implied'>('stated');
  const [attribution, setAttribution] = useState<Attribution>('client');
  const [quotes, setQuotes] = useState<Evidence[]>([]);
  const slot = slotId ? slotById(slotId) : undefined;
  const choices = slot ? instanceChoices(session, slot) : [];
  const needsInstance = !!slot && slot.cardinality !== 'one';
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return PICKS.filter((p) => !q || p.title.toLowerCase().includes(q) || (!p.choice && p.slot.question.toLowerCase().includes(q))).slice(0, 60);
  }, [query]);

  const reset = () => {
    setSlotId(null);
    setChoice(null);
    setQuotes([]);
    setQuery('');
  };

  return (
    <Modal
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="Add a fact the call missed"
      width="max-w-4xl"
    >
      <div className="grid max-h-[70vh] grid-cols-1 gap-4 overflow-y-auto md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-3">
          {!slot ? (
            <div className="space-y-1.5">
              <label className="block text-[12px] font-medium text-ink-2" htmlFor="add-fact-search">
                Which field?
              </label>
              <input id="add-fact-search" autoFocus className="control h-8 min-h-0 px-2 py-1 text-[13px]" placeholder="Search: retirement age, bond, school fees…" value={query} onChange={(e) => setQuery(e.target.value)} />
              <ul className="max-h-72 overflow-y-auto rounded-md border border-line">
                {matches.map((p) => (
                  <li key={`${p.slot.slot_id}#${p.choice?.instance ?? ''}`}>
                    <button
                      type="button"
                      onClick={() => {
                        setSlotId(p.slot.slot_id);
                        setChoice(p.choice);
                      }}
                      className="block w-full px-2.5 py-1 text-left text-[12.5px] hover:bg-brand-soft"
                    >
                      <span className="text-ink">{p.title}</span>
                      {!p.slot.exported && <span className="ml-1 text-[11px] text-muted">(forecast only)</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-[13.5px] font-semibold text-ink">{choice && slot.slot_id === 'expenses.items[]' ? `Budget · ${choice.label}` : slotTitle(slot)}</div>
                  <div className="text-[11.5px] text-muted">{slot.question}</div>
                </div>
                <button type="button" onClick={reset} className="text-[12px] font-semibold text-brand-2 hover:underline">
                  Change
                </button>
              </div>
              {needsInstance && (
                <div>
                  <div className="mb-1 text-[12px] font-medium text-ink-2">For</div>
                  <div className="flex max-h-28 flex-wrap gap-1 overflow-y-auto">
                    {choices.map((c) => (
                      <button
                        key={c.instance ?? 'new'}
                        type="button"
                        onClick={() => setChoice(c)}
                        className={cx('rounded-md border px-2 py-1 text-[12px] font-medium', choice?.instance === c.instance && choice?.newRow === c.newRow ? 'border-brand-2 bg-brand-soft text-brand' : 'border-line-strong bg-surface text-ink-2 hover:bg-wash')}
                      >
                        {c.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Segmented
                  size="sm"
                  value={status}
                  onChange={setStatus}
                  options={[
                    { value: 'stated', label: 'Stated' },
                    { value: 'implied', label: 'Implied' },
                  ]}
                />
                <select className="control h-8 min-h-0 w-auto px-2 py-1 text-[12.5px]" value={attribution} onChange={(e) => setAttribution(e.target.value as Attribution)} aria-label="Who said it">
                  {(Object.keys(ATTRIBUTION_LABEL) as Attribution[]).map((a) => (
                    <option key={a} value={a}>
                      {ATTRIBUTION_LABEL[a]}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <div className="mb-1 text-[12px] font-medium text-ink-2">Quotes</div>
                {quotes.length ? (
                  <ul className="space-y-1">
                    {quotes.map((q, i) => (
                      <li key={`${q.segment_id}:${q.char_start}`} className="flex items-start justify-between gap-2 rounded-md bg-wash px-2 py-1 text-[12px]">
                        <span>
                          <span className="text-[11px] text-muted">
                            {speakerName(q.speaker_role, session.working)} {clock(q.t_start)}:{' '}
                          </span>
                          <q className="font-serif">{q.quote}</q>
                        </span>
                        <button type="button" onClick={() => setQuotes((qs) => qs.filter((_, j) => j !== i))} className="rounded p-0.5 text-faint hover:text-critical-ink" aria-label="Remove quote">
                          <X size={13} />
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-[11.5px] text-muted">Select words in the transcript to quote what was said.</p>
                )}
              </div>
              {error && <Callout tone="critical">{error}</Callout>}
              <ValueEditor
                key={slot.slot_id}
                slot={slot}
                initial={toEditor(slot, null)}
                autoFocus={!needsInstance}
                saveLabel="Add fact"
                onCancel={reset}
                onSave={(value) => {
                  if (needsInstance && !choice) return;
                  const ok = dispatch({
                    type: 'addFact',
                    slotId: slot.slot_id,
                    to: choice?.newRow ? { kind: 'new-row' } : { kind: 'instance', instance: choice?.instance ?? null },
                    value,
                    status,
                    evidence: quotes,
                    attribution,
                  });
                  if (ok) {
                    reset();
                    onClose();
                  }
                }}
              />
              {needsInstance && !choice && <p className="text-[11.5px] text-warning-ink">Choose who or what it is for.</p>}
            </>
          )}
        </div>
        <div className="min-h-0 rounded-lg border border-line md:max-h-[64vh] md:overflow-y-auto">
          <TranscriptView transcript={session.bundle.transcript} doc={session.working} audio={audio} highlight={quotes} onSelect={(e) => setQuotes((qs) => (qs.some((q) => q.segment_id === e.segment_id && q.char_start === e.char_start) ? qs : [...qs, e]))} className="p-1" />
        </div>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ */
/* The transcript                                                      */
/* ------------------------------------------------------------------ */

export function TranscriptDialog({ session, open, onClose, audio, fact }: { session: ReviewSession; open: boolean; onClose: () => void; audio: CallAudio; fact: ReviewFact | null }) {
  return (
    <Modal open={open} onClose={onClose} title="Call transcript" width="max-w-3xl">
      <div className="max-h-[70vh] overflow-y-auto">
        {fact && (
          <p className="mb-2 text-[12px] text-muted">
            Highlighted: what was said about <strong className="text-ink-2">{factTitle(session, fact)}</strong>.
          </p>
        )}
        <TranscriptView transcript={session.bundle.transcript} doc={session.working} audio={audio} highlight={fact?.evidence ?? []} focus={fact?.evidence[0]?.segment_id ?? null} />
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ */
/* The meeting note (§11.6)                                            */
/* ------------------------------------------------------------------ */

const NOTE_SECTION: Record<string, string> = {
  reason_for_advice: 'Reason for advice',
  objectives: 'Client objectives',
  concerns: 'Client concerns',
  summary: 'Summary (draft)',
};

export const NOTE_FIELD_LABEL: Record<string, string> = {
  'engagement.reasonForAdvice': 'Reason for advice',
  'engagement.clientObjectives': 'Client objectives',
  'engagement.clientConcerns': 'Client concerns',
  'advice.summary': 'Advice summary (draft)',
};

/**
 * The meeting note the pipeline renders from the reviewed facts after the review is submitted
 * (§11.6): every sentence names the facts it rests on. Applying it writes the narrative fields
 * into the client's file; it never changes a fact.
 */
export function NoteDialog({
  session,
  open,
  onClose,
  load,
  onApply,
}: {
  session: ReviewSession;
  open: boolean;
  onClose: () => void;
  load: () => Promise<MeetingNote>;
  onApply: (fields: Record<string, string>) => Promise<void>;
}) {
  const [state, setState] = useState<{ kind: 'loading' } | { kind: 'ready'; note: MeetingNote } | { kind: 'error'; message: string }>({ kind: 'loading' });
  const [applied, setApplied] = useState<'no' | 'busy' | 'done' | string>('no');
  useEffect(() => {
    if (!open) return;
    let alive = true;
    setState({ kind: 'loading' });
    setApplied('no');
    load()
      .then((note) => alive && setState({ kind: 'ready', note }))
      .catch((e) => alive && setState({ kind: 'error', message: e instanceof Error ? e.message : String(e) }));
    return () => {
      alive = false;
    };
  }, [open, load]);
  const note = state.kind === 'ready' ? state.note : null;
  const fields = note?.narrative_fields ?? {};
  const same = note ? Object.entries(fields).every(([path, text]) => getPath(session.working, path) === text) : false;
  const sections = note ? [...new Set(note.sentences.map((s) => s.section))] : [];
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Meeting note"
      width="max-w-2xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          {note && Object.keys(fields).length > 0 && (
            <Button
              variant="primary"
              icon={<CheckCircle2 size={14} />}
              disabled={applied === 'busy' || applied === 'done' || same}
              onClick={() => {
                setApplied('busy');
                onApply(fields)
                  .then(() => setApplied('done'))
                  .catch((e) => setApplied(e instanceof Error ? e.message : String(e)));
              }}
            >
              {same || applied === 'done' ? 'In the client file' : applied === 'busy' ? 'Applying…' : 'Apply to the client file'}
            </Button>
          )}
        </>
      }
    >
      <div className="max-h-[65vh] space-y-3 overflow-y-auto text-[13px] text-ink-2">
        <p className="text-[12.5px] text-muted">
          Rendered from the reviewed facts, never from the transcript: each sentence rests on the facts named under it. Applying it fills the narrative fields of the client file; the facts themselves do not change.
        </p>
        {state.kind === 'loading' && <p className="text-muted">Fetching the note from the pipeline…</p>}
        {state.kind === 'error' && (
          <Callout tone={/404|no meeting note/i.test(state.message) ? 'info' : 'warning'}>
            {/404|no meeting note/i.test(state.message) ? 'The pipeline has not written the meeting note yet: it follows the review once the run resumes. Try again in a moment.' : state.message}
          </Callout>
        )}
        {note && note.sentences.length === 0 && <Callout tone="info">The note has no sentences: nothing in the reviewed facts could be written up.</Callout>}
        {sections.map((sec) => (
          <section key={sec}>
            <h3 className="mb-1 text-[10.5px] font-semibold uppercase tracking-[0.07em] text-muted">{NOTE_SECTION[sec] ?? sec}</h3>
            <ul className="space-y-1">
              {note!.sentences
                .filter((s) => s.section === sec)
                .map((s, i) => {
                  const about = s.fact_ids.map((id) => session.facts[id]).filter((f): f is ReviewFact => !!f);
                  return (
                    <li key={i} className="rounded-md border border-line px-2.5 py-1.5">
                      <p className="leading-5 text-ink">{s.text}</p>
                      {about.length > 0 && <p className="mt-0.5 text-[11px] text-muted">From: {about.map((f) => factTitle(session, f)).join('; ')}</p>}
                    </li>
                  );
                })}
            </ul>
          </section>
        ))}
        {applied !== 'no' && applied !== 'busy' && applied !== 'done' && <Callout tone="critical">{applied}</Callout>}
        {applied === 'done' && <Callout tone="good">The narrative fields are in {householdName(session.working)}’s file.</Callout>}
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ */
/* Submitting (§11.2 "Submit")                                         */
/* ------------------------------------------------------------------ */

export interface SubmitNotice {
  tone: 'good' | 'warning' | 'info';
  text: string;
}

export interface SubmitChoices {
  downloadResult: boolean;
  downloadFile: boolean;
  sendToApi: boolean;
}

const duration = (seconds: number) => {
  const m = Math.round(seconds / 60);
  return m < 1 ? 'under a minute' : m === 1 ? '1 minute' : `${m} minutes`;
};

export function SubmitDialog({
  session,
  open,
  onClose,
  onSubmit,
  apiAvailable,
  busy,
  error,
  household,
  localChanged,
  refused = 0,
  notice,
  onDownloadResult,
  onDownloadFile,
}: {
  session: ReviewSession;
  open: boolean;
  onClose: () => void;
  onSubmit: (c: SubmitChoices) => void;
  apiAvailable: boolean;
  busy: boolean;
  error: string | null;
  household: string;
  /** The client's file was edited on this device while the review was open. */
  localChanged: boolean;
  /** API mode: changes the pipeline refused (they stay in this review and its result file). */
  refused?: number;
  /** After an API submission: how the pipeline's reviewed file compares with the one saved here. */
  notice: SubmitNotice | null;
  onDownloadResult: () => void;
  onDownloadFile: () => void;
}) {
  const [choices, setChoices] = useState<SubmitChoices>({ downloadResult: true, downloadFile: false, sendToApi: apiAvailable });
  const q = useMemo(() => buildQueue(session), [session]);
  const facts = Object.values(session.facts).filter(isReviewItem);
  const count = (st: string) => facts.filter((f) => f.review.status === st).length;
  const undecidedNeeds = q.needs.filter((f) => !isDecided(f)).length;
  const undecided = [...q.needs, ...q.quick].filter((f) => !isDecided(f)).length;
  const openFollowups = session.followups.filter((f) => f.status === 'open').length;

  if (session.submittedAt)
    return (
      <Modal open={open} onClose={onClose} title="Review submitted" width="max-w-lg">
        <div className="space-y-3 text-[13px] text-ink-2">
          <p className="flex items-start gap-2">
            <CheckCircle2 size={18} className="mt-px shrink-0 text-good-ink" aria-hidden />
            <span>
              {household}’s file on this device now holds the reviewed values. The review result records every decision for the pipeline{apiAvailable ? ', which has received it' : ''}.
            </span>
          </p>
          {notice && <Callout tone={notice.tone}>{notice.text}</Callout>}
          <div className="flex flex-wrap gap-2">
            <Button icon={<Download size={14} />} onClick={onDownloadResult}>
              Review result
            </Button>
            <Button icon={<Download size={14} />} onClick={onDownloadFile}>
              Reviewed client file
            </Button>
            <Link href={`/client?id=${encodeURIComponent(session.docId)}`} className="focus-ring inline-flex h-9 items-center gap-1.5 rounded-lg bg-brand px-3.5 text-sm font-medium text-white hover:bg-brand-2">
              <ExternalLink size={14} /> Open the client file
            </Link>
          </div>
        </div>
      </Modal>
    );

  const toggle = (k: keyof SubmitChoices) => setChoices((c) => ({ ...c, [k]: !c[k] }));
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Submit the review"
      width="max-w-lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Keep reviewing
          </Button>
          <Button variant="primary" icon={<Send size={14} />} onClick={() => onSubmit(choices)} disabled={busy}>
            {busy ? 'Submitting…' : 'Submit review'}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-[13px] text-ink-2">
        <dl className="grid grid-cols-3 gap-2 text-center">
          {[
            ['Accepted', count('accepted')],
            ['Edited', count('edited')],
            ['Rejected', count('rejected')],
          ].map(([label, n]) => (
            <div key={label} className="rounded-lg bg-wash px-2 py-1.5">
              <dt className="text-[11px] text-muted">{label}</dt>
              <dd className="text-[18px] font-semibold text-ink tabular">{n}</dd>
            </div>
          ))}
        </dl>
        {undecided > 0 && (
          <Callout tone={undecidedNeeds ? 'warning' : 'info'}>
            {undecided} fact{undecided === 1 ? '' : 's'} you did not decide keep{undecided === 1 ? 's' : ''} the call’s proposal{undecidedNeeds ? `, ${undecidedNeeds} of them under “Needs you”` : ''}.
          </Callout>
        )}
        {openFollowups > 0 && (
          <p className="text-[12.5px]">
            {openFollowups} follow-up question{openFollowups === 1 ? '' : 's'} will go to the client.
          </p>
        )}
        <p className="text-[12.5px] text-muted">Reviewed for {duration(session.activeSeconds)}. Submitting writes the reviewed values into {household}’s file on this device.</p>
        {localChanged && (
          <Callout tone="warning" title="The client file changed while you reviewed">
            It was edited on this device after this review started. Submitting replaces those edits with the reviewed file: make them again in the sheet here first if they matter.
          </Callout>
        )}
        {refused > 0 && (
          <Callout tone="warning" title={`The pipeline refused ${refused} of your change${refused === 1 ? '' : 's'}`}>
            The file saved here keeps {refused === 1 ? 'it' : 'them'}; the pipeline’s reviewed file will differ there. The banner above the sheet names {refused === 1 ? 'it' : 'them'}.
          </Callout>
        )}
        <fieldset className="space-y-1.5">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={choices.downloadResult} onChange={() => toggle('downloadResult')} /> Download the review result for the pipeline
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={choices.downloadFile} onChange={() => toggle('downloadFile')} /> Download the reviewed client file
          </label>
          {apiAvailable && (
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={choices.sendToApi} onChange={() => toggle('sendToApi')} /> Send the review to the pipeline’s API
            </label>
          )}
        </fieldset>
        {error && <Callout tone="critical">{error}</Callout>}
      </div>
    </Modal>
  );
}
