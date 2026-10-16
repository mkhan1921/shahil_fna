'use client';

/**
 * Annotate mode (spec §15.1–15.2): an annotator fills the same fact structure as the system, in the
 * adviser's sheet, without seeing anything the system extracted. Each field takes a status, a value
 * (a point, a range with the p10 / p50 / p90 the annotator believes the client meant, or weighted
 * options), who said it, and the transcript spans it rests on. Forecast-only facts (events, income
 * changes) are annotated from the rail. The export is one gold annotation file per annotator per call.
 */
import { produce } from 'immer';
import { CheckCircle2, Download, NotebookPen, Pause, Play, Plus, Trash2, X } from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type Ref } from 'react';
import { analyse } from '@/lib/fna/analysis';
import {
  addAnnotatorRow,
  annotationEntityOfRow,
  annotationPathOf,
  annotationProblems,
  editAnnotationDocument,
  forecastInstance,
  goldFactAt,
  goldFile,
  goldProblems,
  goldQuantity,
  goldToEditor,
  goldValue,
  removeGoldFact,
  setGoldFact,
  type AnnotationSession,
  type GoldFact,
  type GoldQuantity,
  type GoldSpan,
} from '@/lib/review/annotate';
import { CATALOG, factIdFor, fieldLabel, groupLabel, isPerPerson, slotAtPath, slotById, type SlotDef } from '@/lib/review/catalog';
import { phrase } from '@/lib/review/dist';
import { callDate } from '@/lib/review/impact';
import { describeErrors, validate, type JsonSchema } from '@/lib/review/jsonSchema';
import { firstName } from '@/lib/review/labels';
import { IDENTITY_FIELD, LIST_CONTAINERS, type ListContainer } from '@/lib/review/paths';
import { loadAnnotation, saveAnnotation } from '@/lib/review/store';
import { clock, speakerName } from '@/lib/review/transcript';
import type { Attribution, FactStatus } from '@/lib/review/types';
import { usePractice } from '@/lib/store/hooks';
import { downloadBlob, exportJsonFile, safeFilename } from '@/lib/store/transfer';
import type { SaveState } from '@/lib/store/hooks';
import AppShell from '../AppShell';
import SheetFrame, { SaveIndicator } from '../sheet/SheetFrame';
import { Button, Callout, EmptyState, Segmented, cx } from '../ui';
import { ATTRIBUTION_LABEL, STATUS_LABEL } from './FieldPopover';
import { OverlayProvider, type FieldMarkData, type OverlayApi } from './overlay';
import TranscriptView from './TranscriptView';
import { useCallAudio, type CallAudio } from './useCallAudio';
import { usePlacement } from './usePlacement';
import ValueEditor from './ValueEditor';

const STATUSES: FactStatus[] = ['stated', 'implied', 'explicitly_unknown', 'not_discussed', 'contradicted'];
const VALUED = new Set<FactStatus>(['stated', 'implied', 'contradicted']);

/** A slot instance being annotated. */
interface Target {
  slotId: string;
  instance: string | null;
  path: string;
  /** For a forecast-only object the annotator named. */
  entityLabel?: string;
}

/* ------------------------------------------------------------------ */
/* Keeping the annotation                                              */
/* ------------------------------------------------------------------ */

function useAnnotation(key: string | null) {
  const [a, setA] = useState<AnnotationSession | null | undefined>(undefined);
  const [saveState, setSaveState] = useState<SaveState>('idle');
  const latest = useRef<AnnotationSession | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    if (!latest.current) return;
    setSaveState('saving');
    try {
      await saveAnnotation(latest.current);
      setSaveState('saved');
    } catch {
      setSaveState('error');
    }
  }, []);

  useEffect(() => {
    let alive = true;
    if (!key) {
      setA(null);
      return;
    }
    loadAnnotation(key).then((s) => {
      if (!alive) return;
      latest.current = s ?? null;
      setA(s ?? null);
    });
    return () => {
      alive = false;
    };
  }, [key]);

  useEffect(() => {
    const handler = () => {
      if (timer.current) void flush();
    };
    window.addEventListener('beforeunload', handler);
    return () => {
      window.removeEventListener('beforeunload', handler);
      handler();
    };
  }, [flush]);

  const set = useCallback(
    (next: AnnotationSession) => {
      latest.current = next;
      setA(next);
      setSaveState('pending');
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void flush(), 400);
    },
    [flush],
  );

  return { a, set, saveState, latest };
}

/* ------------------------------------------------------------------ */
/* The annotation popover                                              */
/* ------------------------------------------------------------------ */

const spanKey = (s: Pick<GoldSpan, 'segment_id' | 'char_start'>) => `${s.segment_id}:${s.char_start}`;

function AnnotatePopover({
  a,
  target,
  spans,
  setSpans,
  audio,
  onSave,
  onRemove,
  onClose,
  style,
  rootRef,
}: {
  a: AnnotationSession;
  target: Target;
  spans: GoldSpan[];
  setSpans: (s: GoldSpan[]) => void;
  audio: CallAudio;
  onSave: (f: { status: FactStatus; value: GoldQuantity | null; attribution: Attribution; note: string }) => string | null;
  onRemove: () => void;
  onClose: () => void;
  style: CSSProperties;
  rootRef: Ref<HTMLDivElement>;
}) {
  const slot = slotById(target.slotId) as SlotDef;
  const existing = a.annotation.facts.find((f) => f.fact_id === factIdFor(slot.slot_id, target.instance));
  const [status, setStatus] = useState<FactStatus>(existing?.status ?? 'stated');
  const [attribution, setAttribution] = useState<Attribution>(existing?.speaker_attribution ?? 'client');
  const [note, setNote] = useState(existing?.note ?? '');
  const [error, setError] = useState<string | null>(null);
  const start = useMemo(() => goldToEditor(slot, existing?.value ?? null), [slot, existing?.value]);
  const subject = isPerPerson(slot) ? firstName(a.working, target.instance === 'spouse' ? 'spouse' : 'client') : (target.entityLabel ?? existing?.entity_label ?? '');
  const problems = existing ? goldProblems(existing) : [];

  const save = (value: GoldQuantity | null) => setError(onSave({ status, value, attribution, note }));

  return (
    <div
      ref={rootRef}
      role="dialog"
      tabIndex={-1}
      aria-label={`Annotate ${fieldLabel(slot, target.instance)}`}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          onClose();
        }
      }}
      className="no-print fixed z-40 flex w-[min(27rem,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-xl border border-line-strong bg-surface shadow-[0_12px_40px_rgba(15,30,50,0.22)] outline-none"
      style={style}
    >
      <header className="flex items-start justify-between gap-2 border-b border-line bg-wash px-3 py-2">
        <div className="min-w-0">
          <div className="text-[10.5px] font-semibold uppercase tracking-[0.07em] text-muted">
            Tier {slot.sensitivity_tier} · {CATALOG.groups[slot.group]?.name ?? groupLabel(slot.group)}
            {!slot.exported && ' · forecast only'}
          </div>
          <h2 className="truncate text-[14px] font-semibold text-ink">
            {subject && <span className="text-ink-2">{subject} · </span>}
            {fieldLabel(slot, target.instance)}
          </h2>
          <p className="text-[11.5px] leading-4 text-muted">{slot.question}</p>
        </div>
        <button type="button" onClick={onClose} className="focus-ring rounded-md p-1 text-muted hover:bg-surface hover:text-ink" aria-label="Close (Esc)">
          <X size={16} />
        </button>
      </header>
      <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-3 py-2.5">
        <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Status">
          {STATUSES.map((st) => (
            <button
              key={st}
              type="button"
              role="radio"
              aria-checked={status === st}
              onClick={() => setStatus(st)}
              className={cx('rounded-md border px-2 py-1 text-[12px] font-medium', status === st ? 'border-brand-2 bg-brand-soft text-brand' : 'border-line-strong bg-surface text-ink-2 hover:bg-wash')}
            >
              {STATUS_LABEL[st]}
            </button>
          ))}
        </div>
        <label className="block text-[12px] font-medium text-ink-2">
          Who said it
          <select className="control mt-0.5 h-8 min-h-0 px-2 py-1 text-[12.5px]" value={attribution} onChange={(e) => setAttribution(e.target.value as Attribution)}>
            {(Object.keys(ATTRIBUTION_LABEL) as Attribution[]).map((x) => (
              <option key={x} value={x}>
                {ATTRIBUTION_LABEL[x]}
              </option>
            ))}
          </select>
        </label>
        <div>
          <div className="mb-1 text-[12px] font-medium text-ink-2">What it rests on</div>
          {spans.length ? (
            <ul className="space-y-1">
              {spans.map((s) => (
                <li key={spanKey(s)} className="flex items-start justify-between gap-2 rounded-md bg-wash px-2 py-1 text-[12px]">
                  <button type="button" disabled={!audio.available} onClick={() => audio.play(s)} className="text-left disabled:cursor-default">
                    <span className="flex items-center gap-1 text-[10.5px] text-muted">
                      {audio.available && (audio.playing === spanKey(s) ? <Pause size={10} /> : <Play size={10} />)}
                      {speakerName(s.speaker_role, a.working)} {clock(s.t_start)}
                    </span>
                    <q className="font-serif">{s.quote}</q>
                  </button>
                  <button type="button" onClick={() => setSpans(spans.filter((x) => spanKey(x) !== spanKey(s)))} className="rounded p-0.5 text-faint hover:text-critical-ink" aria-label="Remove span">
                    <X size={13} />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[11.5px] text-muted">Select the words in the transcript beside the sheet to add a span.</p>
          )}
        </div>
        <input className="control h-8 min-h-0 px-2 py-1 text-[12.5px]" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
        {problems.length > 0 && (
          <ul className="space-y-0.5 text-[11.5px] text-warning-ink">
            {problems.map((p) => (
              <li key={p}>Saved annotation: {p}.</li>
            ))}
          </ul>
        )}
        {error && <p className="text-[11.5px] text-critical-ink">{error}</p>}
        {VALUED.has(status) ? (
          <ValueEditor
            key={`${slot.slot_id}:${target.instance}`}
            slot={slot}
            initial={start.value}
            initialQuantiles={start.quantiles}
            withQuantiles
            autoFocus={false}
            saveLabel="Save annotation"
            onCancel={onClose}
            onSave={(v, q) => {
              try {
                save(goldValue(slot, v, q));
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
              }
            }}
          />
        ) : (
          <div className="flex justify-end gap-1.5">
            <Button size="sm" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={() => save(null)}>
              Save annotation
            </Button>
          </div>
        )}
        {existing && (
          <div className="border-t border-line pt-2">
            <button type="button" onClick={onRemove} className="inline-flex items-center gap-1 text-[12px] font-semibold text-critical-ink hover:underline">
              <Trash2 size={12} /> Remove this annotation
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* The workspace                                                       */
/* ------------------------------------------------------------------ */

const goldText = (f: GoldFact) => (f.value ? phrase(goldQuantity(f.value)) : f.status === 'explicitly_unknown' ? 'not known' : 'not discussed');

/** Forecast-only slots: annotated from the rail, since the sheet has no field for them. */
const FORECAST_SLOTS = CATALOG.slots.filter((s) => !s.exported);

export default function AnnotateWorkspace({ annotationKey }: { annotationKey: string | null }) {
  const { a, set, saveState, latest } = useAnnotation(annotationKey);
  if (a === undefined)
    return (
      <AppShell wide>
        <div className="p-16 text-center text-sm text-muted">Loading the annotation…</div>
      </AppShell>
    );
  if (!a)
    return (
      <AppShell>
        <div className="mx-auto max-w-lg p-10">
          <EmptyState
            title="Annotation not found"
            description="This annotation is not stored in this browser. Start one from the client list with the call's review package."
            action={
              <Link href="/" className="text-sm font-medium text-brand underline">
                Back to clients
              </Link>
            }
          />
        </div>
      </AppShell>
    );
  return <Annotating a={a} set={set} saveState={saveState} get={() => latest.current ?? a} />;
}

function Annotating({ a, set, saveState, get }: { a: AnnotationSession; set: (a: AnnotationSession) => void; saveState: SaveState; get: () => AnnotationSession }) {
  const { practice } = usePractice();
  const audio = useCallAudio(a.audioUrl);
  const asAt = useMemo(() => callDate(a.transcript.anchor_date || a.annotation.anchor_date), [a.transcript.anchor_date, a.annotation.anchor_date]);
  const analysis = useMemo(() => analyse(a.working, asAt), [a.working, asAt]);
  const [target, setTarget] = useState<Target | null>(null);
  const [spans, setSpans] = useState<GoldSpan[]>([]);
  const [message, setMessage] = useState<{ tone: 'good' | 'critical'; text: string } | null>(null);
  const [forecastSlot, setForecastSlot] = useState('');
  const [forecastName, setForecastName] = useState('');
  const [forecastPerson, setForecastPerson] = useState<'client' | 'spouse'>('client');
  const [rowContainer, setRowContainer] = useState<ListContainer>('policies');
  const [rowLabel, setRowLabel] = useState('');
  const popRef = useRef<HTMLDivElement>(null);
  const audioInput = useRef<HTMLInputElement>(null);
  const placement = usePlacement(target?.path ?? null, !!target);
  const entityOf = useCallback((c: string, k: string) => annotationEntityOfRow(a, c, k), [a]);
  const problems = annotationProblems(a);
  const forecastSlotDef = forecastSlot ? slotById(forecastSlot) : undefined;

  const open = useCallback((t: Target) => {
    setTarget(t);
    const g = get().annotation.facts.find((f) => f.fact_id === factIdFor(t.slotId, t.instance));
    setSpans(g?.spans ?? []);
    requestAnimationFrame(() => popRef.current?.focus({ preventScroll: true }));
  }, [get]);

  const overlay = useMemo<OverlayApi>(
    () => ({
      mode: 'annotate',
      markAt: (path): FieldMarkData | null => {
        const g = goldFactAt(a, path);
        if (g) {
          const issues = goldProblems(g);
          return { kind: 'annotated', label: STATUS_LABEL[g.status], title: `Annotated ${STATUS_LABEL[g.status].toLowerCase()}: ${goldText(g)}${issues.length ? ` · ${issues.join('; ')}` : ''}`, flagged: issues.length > 0 };
        }
        const at = slotAtPath(path, entityOf);
        if (!at || (at.instance === null && at.slot.cardinality !== 'one' && !isPerPerson(at.slot))) return null;
        return { kind: 'annotate', label: 'Annotate', title: `Annotate ${fieldLabel(at.slot, at.instance)}` };
      },
      currentPath: target?.path ?? null,
      openAt: (path) => {
        const at = slotAtPath(path, entityOf);
        if (at) open({ slotId: at.slot.slot_id, instance: at.instance, path });
      },
    }),
    [a, entityOf, target?.path, open],
  );

  const save = (f: { status: FactStatus; value: GoldQuantity | null; attribution: Attribution; note: string }): string | null => {
    if (!target) return null;
    try {
      set(setGoldFact(get(), { slotId: target.slotId, instance: target.instance, status: f.status, value: f.value, attribution: f.attribution, spans, note: f.note, entityLabel: target.entityLabel }));
      setTarget(null);
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : String(e);
    }
  };

  const exportGold = async (complete: boolean) => {
    const now = get();
    if (complete && annotationProblems(now).length) {
      setMessage({ tone: 'critical', text: 'Fix the annotations marked with a warning before completing: each needs what the evaluation set requires.' });
      return;
    }
    const at = new Date().toISOString();
    const file = goldFile(now, complete ? at : null);
    const schema = (await import('@/lib/review/schema/gold_annotation.json')).default as unknown as JsonSchema;
    const errors = validate(schema, file);
    if (errors.length) {
      setMessage({ tone: 'critical', text: `The gold file does not match the annotation contract: ${describeErrors(errors, 3)}.` });
      return;
    }
    if (complete) set(produce(now, (d) => void (d.annotation.completed_at = at)));
    downloadBlob(exportJsonFile(file), `${safeFilename(now.annotation.call_id)}_${safeFilename(now.annotation.annotator_id)}_gold${complete ? '' : '_draft'}.json`);
    setMessage({ tone: 'good', text: complete ? 'Completed: the gold file is downloaded.' : 'Draft downloaded.' });
  };

  const rail = (
    <div data-review-rail className="space-y-2 pb-4">
      <section className="rounded-lg border border-line bg-surface px-2.5 py-2">
        <div className="flex items-center gap-1.5 text-[12.5px] font-semibold text-ink">
          <NotebookPen size={14} className="text-brand-2" aria-hidden /> Gold annotation
        </div>
        <div className="mt-0.5 text-[11.5px] text-muted">
          {a.annotation.call_id} · {a.annotation.annotator_id} ({a.annotation.role})
        </div>
        <div className="mt-1 text-[11.5px] text-ink-2 tabular">
          {a.annotation.facts.length} fact{a.annotation.facts.length === 1 ? '' : 's'} annotated
          {problems.length > 0 && <span className="text-warning-ink"> · {problems.length} need attention</span>}
          {a.annotation.completed_at && <span className="text-good-ink"> · completed</span>}
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          <Button size="sm" variant="primary" icon={<CheckCircle2 size={13} />} onClick={() => void exportGold(true)}>
            Complete & export
          </Button>
          <Button size="sm" variant="ghost" icon={<Download size={13} />} onClick={() => void exportGold(false)}>
            Draft
          </Button>
        </div>
        {message && <p className={cx('mt-1 text-[11.5px]', message.tone === 'good' ? 'text-good-ink' : 'text-critical-ink')}>{message.text}</p>}
      </section>

      <section className="overflow-hidden rounded-lg border border-line bg-surface">
        <h2 className="flex items-center justify-between border-b border-line bg-wash px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-wide text-muted">
          <span>Transcript</span>
          <span className="font-normal normal-case tracking-normal text-faint">{target ? 'select words to add a span' : 'open a field to add spans'}</span>
        </h2>
        <div className="max-h-[45vh] overflow-y-auto p-1">
          <TranscriptView
            transcript={a.transcript}
            doc={a.working}
            audio={audio}
            highlight={spans}
            onSelect={(e) => {
              if (!target) {
                setMessage({ tone: 'critical', text: 'Open the field the words are about first, then select them.' });
                return;
              }
              const span: GoldSpan = { segment_id: e.segment_id, char_start: e.char_start, char_end: e.char_end, quote: e.quote, speaker_role: e.speaker_role, t_start: e.t_start, t_end: e.t_end, role: e.role };
              setSpans((s) => (s.some((x) => spanKey(x) === spanKey(span)) ? s : [...s, span]));
            }}
          />
        </div>
      </section>

      <section className="rounded-lg border border-line bg-surface">
        <h2 className="border-b border-line bg-wash px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-wide text-muted">Forecast-only facts</h2>
        <div className="space-y-1.5 px-2.5 py-2">
          <select className="control h-8 min-h-0 px-2 py-1 text-[12.5px]" value={forecastSlot} onChange={(e) => setForecastSlot(e.target.value)} aria-label="Forecast-only field">
            <option value="">An event or income change…</option>
            {FORECAST_SLOTS.map((s) => (
              <option key={s.slot_id} value={s.slot_id}>
                {CATALOG.groups[s.group]?.name ?? groupLabel(s.group)} · {s.label}
              </option>
            ))}
          </select>
          {forecastSlotDef &&
            (isPerPerson(forecastSlotDef) && forecastSlotDef.cardinality === 'per_person' ? (
              <Segmented
                size="sm"
                value={forecastPerson}
                onChange={setForecastPerson}
                options={[
                  { value: 'client', label: firstName(a.working, 'client') },
                  { value: 'spouse', label: firstName(a.working, 'spouse') },
                ]}
              />
            ) : (
              <input className="control h-8 min-h-0 px-2 py-1 text-[12.5px]" placeholder="What it is: “Kagiso studies overseas”" value={forecastName} onChange={(e) => setForecastName(e.target.value)} />
            ))}
          {forecastSlotDef && (
            <Button
              size="sm"
              onClick={() => {
                try {
                  const perPerson = isPerPerson(forecastSlotDef) && forecastSlotDef.cardinality === 'per_person';
                  const instance = perPerson ? forecastPerson : forecastInstance(forecastSlotDef, forecastName);
                  open({ slotId: forecastSlotDef.slot_id, instance, path: annotationPathOf(a, forecastSlotDef, instance), entityLabel: perPerson ? undefined : forecastName.trim() });
                  setMessage(null);
                } catch (e) {
                  setMessage({ tone: 'critical', text: e instanceof Error ? e.message : String(e) });
                }
              }}
            >
              Annotate
            </Button>
          )}
        </div>
      </section>

      <section className="rounded-lg border border-line bg-surface">
        <h2 className="border-b border-line bg-wash px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-wide text-muted">Something new in the call</h2>
        <div className="space-y-1.5 px-2.5 py-2">
          <p className="text-[11px] leading-[15px] text-muted">A policy, fund, asset, debt, goal or person the file does not have: add its row, then annotate its fields in the sheet.</p>
          <div className="flex gap-1.5">
            <select className="control h-8 min-h-0 w-auto px-2 py-1 text-[12.5px]" value={rowContainer} onChange={(e) => setRowContainer(e.target.value as ListContainer)} aria-label="Kind of row">
              {LIST_CONTAINERS.map((c) => (
                <option key={c} value={c}>
                  {c === 'expenses.custom' ? 'Other expense' : c.replace(/([A-Z])/g, ' $1').replace(/^\w/, (x) => x.toUpperCase())}
                </option>
              ))}
            </select>
            <input className="control h-8 min-h-0 min-w-0 flex-1 px-2 py-1 text-[12.5px]" placeholder="Name it" value={rowLabel} onChange={(e) => setRowLabel(e.target.value)} />
          </div>
          <Button
            size="sm"
            icon={<Plus size={13} />}
            disabled={!rowLabel.trim()}
            onClick={() => {
              const [next, row] = addAnnotatorRow(get(), rowContainer, rowLabel);
              set(next);
              setRowLabel('');
              const identity = slotById(`${rowContainer}[].${IDENTITY_FIELD[rowContainer]}`);
              if (identity) open({ slotId: identity.slot_id, instance: row.entityId, path: `${rowContainer}[${row.rowId}].${IDENTITY_FIELD[rowContainer]}`, entityLabel: row.label });
            }}
          >
            Add the row
          </Button>
        </div>
      </section>

      {a.annotation.facts.length > 0 && (
        <section className="overflow-hidden rounded-lg border border-line bg-surface">
          <h2 className="border-b border-line bg-wash px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-wide text-muted">Annotated</h2>
          <ul className="divide-y divide-line">
            {[...a.annotation.facts]
              .sort((x, y) => x.fact_id.localeCompare(y.fact_id))
              .map((f) => {
                const slot = slotById(f.slot_id);
                const issues = goldProblems(f);
                return (
                  <li key={f.fact_id}>
                    <button
                      type="button"
                      tabIndex={-1}
                      onClick={() => {
                        open({ slotId: f.slot_id, instance: f.instance_key, path: f.fna_path, entityLabel: f.entity_label || undefined });
                        document.querySelector(`[data-path="${CSS.escape(f.fna_path)}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
                      }}
                      className="block w-full px-2.5 py-1 text-left hover:bg-wash"
                    >
                      <div className="flex items-center justify-between gap-2 text-[12px]">
                        <span className="truncate font-medium text-ink">
                          {f.entity_label || (f.instance_key && isPerPerson(slot as SlotDef) ? firstName(a.working, f.instance_key === 'spouse' ? 'spouse' : 'client') : '')} {slot ? fieldLabel(slot, f.instance_key) : f.slot_id}
                        </span>
                        <span className={cx('shrink-0 text-[10.5px]', issues.length ? 'text-warning-ink' : 'text-muted')}>{STATUS_LABEL[f.status]}</span>
                      </div>
                      <div className="truncate text-[11px] text-muted tabular">{goldText(f)}</div>
                      {issues.length > 0 && <div className="text-[10.5px] text-warning-ink">{issues.join('; ')}</div>}
                    </button>
                  </li>
                );
              })}
          </ul>
        </section>
      )}
    </div>
  );

  const actions = (
    <div className="flex items-center gap-2">
      <SaveIndicator state={saveState} />
      <input
        ref={audioInput}
        type="file"
        accept="audio/*"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file) audio.openFile(file);
        }}
      />
      <button type="button" tabIndex={-1} onClick={() => audioInput.current?.click()} className="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-[13px] font-medium text-ink-2 hover:bg-wash">
        <Play size={15} /> <span className="hidden sm:inline">{audio.available ? 'Recording ready' : 'Open the recording'}</span>
      </button>
    </div>
  );

  const banner = (
    <div className="no-print mx-auto max-w-[1760px] space-y-2 px-3 pt-3 sm:px-4">
      <Callout tone="info" title="Annotate mode">
        You see the client’s file as it was before the call and the transcript, never what the system extracted. Annotate every field the call touched: its status, the value you believe the client
        meant (for a range, your p10, p50 and p90), who said it, and the words it rests on.
      </Callout>
      {audio.error && <Callout tone="warning">{audio.error}</Callout>}
    </div>
  );

  return (
    <OverlayProvider value={overlay}>
      <SheetFrame
        doc={a.working}
        analysis={analysis}
        update={(recipe) => set(editAnnotationDocument(get(), produce(get().working, recipe)))}
        practice={practice}
        actions={actions}
        banner={banner}
        back={{ href: '/', label: 'All clients' }}
        railClassName="hidden w-[22rem] lg:block"
        navClassName="hidden 2xl:flex"
        rail={() => rail}
      />
      {target && (
        <AnnotatePopover
          key={`${target.slotId}#${target.instance}`}
          a={a}
          target={target}
          spans={spans}
          setSpans={setSpans}
          audio={audio}
          onSave={save}
          onRemove={() => {
            set(removeGoldFact(get(), factIdFor(target.slotId, target.instance)));
            setTarget(null);
          }}
          onClose={() => setTarget(null)}
          style={placement}
          rootRef={popRef}
        />
      )}
    </OverlayProvider>
  );
}
