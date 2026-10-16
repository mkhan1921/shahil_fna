/**
 * Annotate mode (spec §15.1–15.2): an annotator fills the same fact structure as the system, in
 * the sheet, without seeing system output — a status per slot, the value as a point, a range or
 * weighted options (with the p10 / p50 / p90 they believe the client meant, for anything vague),
 * who said it, and the transcript spans it rests on. The export is one gold annotation file per
 * annotator per call, mirrored by pipeline/advisor_intel/evals/annotation.py (GoldAnnotation) and
 * checked against the JSON Schema exported from it.
 *
 * An annotation session only ever holds the transcript, the recording, the client's prior FNA and
 * the prior's rows: the pipeline's facts, values, flags and forecast are never loaded.
 */
import { produce } from 'immer';
import { normaliseDocument } from '../fna/defaults';
import type { FnaDocument } from '../fna/types';
import { factIdFor, pathFor, quantityUnit, rowIdFor, slotAtPath, slotById, toFnaValue, toQuantity, type SlotDef } from './catalog';
import { pyRound } from './dist';
import { IDENTITY_FIELD, defaultAt, diffDocuments, getPath, hasRow, isDocumentPath, isListContainer, parsePath, removeRow, setPath, type ListContainer } from './paths';
import type { Attribution, Dist, Entity, EvidenceRole, FactStatus, Period, PointDist, Quantity, ReviewBundle, Transcript, Unit } from './types';
import { rowIdForNewEntity } from './uuid';
import { fromInput, inputKind, toDist, toEditor, toInput, type EditorValue, type Scalar } from './values';

export const GOLD_FORMAT = 'advisor-intel-gold-annotation';

export type GoldValue =
  | { kind: 'point'; value: Scalar }
  /** A range or a vague amount: the bounds as said (if any) and the annotator's quantiles. */
  | { kind: 'range'; low: number | null; high: number | null; p10: number; p50: number; p90: number }
  | { kind: 'options'; options: { value: Scalar; weight: number }[] };

export interface GoldQuantity {
  value: GoldValue;
  unit: Unit;
  period: Period;
}

export interface GoldSpan {
  segment_id: string;
  char_start: number;
  char_end: number;
  quote: string;
  speaker_role: string;
  t_start: number;
  t_end: number;
  role: EvidenceRole;
}

export interface GoldFact {
  /** `${slot_id}#${instance}`, the system's convention, so scorers join gold to extracted facts directly. */
  fact_id: string;
  slot_id: string;
  instance_key: string | null;
  fna_path: string;
  /** Names an object the prior FNA does not have: "new group disability cover". */
  entity_label: string;
  status: FactStatus;
  value: GoldQuantity | null;
  speaker_attribution: Attribution;
  spans: GoldSpan[];
  note: string;
}

export interface GoldAnnotation {
  format: typeof GOLD_FORMAT;
  version: 1;
  call_id: string;
  annotator_id: string;
  role: 'annotator' | 'adjudicator';
  schema_version: string;
  anchor_date: string;
  transcript: { source: 'asr' | 'human'; asr_provider: string; asr_model_version: string };
  prior_document_id: string | null;
  started_at: string;
  completed_at: string | null;
  facts: GoldFact[];
}

/** A new list row the annotator adds (the prior FNA does not have it). */
export interface AnnotatorRow {
  entityId: string;
  container: ListContainer;
  rowId: string;
  label: string;
}

export interface AnnotationSession {
  version: 1;
  annotation: GoldAnnotation;
  transcript: Transcript;
  audioUrl: string | null;
  prior: FnaDocument | null;
  /** The prior's rows and the household's people: enough to say which row a field belongs to. */
  entities: Record<string, Entity>;
  rows: AnnotatorRow[];
  /** The prior file with each annotated value at its median: what the annotator sees in the sheet. */
  working: FnaDocument;
}

/* ------------------------------------------------------------------ */
/* Starting                                                            */
/* ------------------------------------------------------------------ */

export interface AnnotationSource {
  callId: string;
  anchorDate: string;
  transcript: Transcript;
  audioUrl: string | null;
  prior: FnaDocument | null;
  entities: Record<string, Entity>;
  schemaVersion: string;
}

/** What an annotator may see of a review bundle: never the sidecar, the median document, flags or forecast. */
export const annotationSource = (b: ReviewBundle): AnnotationSource => ({
  callId: b.call_id,
  anchorDate: b.transcript.anchor_date,
  transcript: b.transcript,
  audioUrl: b.audio_url,
  prior: b.prior_document,
  // Only entities that stand for people or rows of the prior file: the call's discoveries are system output.
  entities: Object.fromEntries(Object.entries(b.entities).filter(([, e]) => !!e.fna_id || ['person.client_primary', 'person.spouse', 'person.advisor'].includes(e.entity_id))),
  schemaVersion: b.sidecar.schema_version,
});

export const createAnnotation = (src: AnnotationSource, o: { annotatorId: string; now: string; role?: 'annotator' | 'adjudicator'; transcriptSource?: 'asr' | 'human' }): AnnotationSession => {
  const working = src.prior ? normaliseDocument(structuredClone(src.prior)) : normaliseDocument({});
  return {
    version: 1,
    annotation: {
      format: GOLD_FORMAT,
      version: 1,
      call_id: src.callId,
      annotator_id: o.annotatorId,
      role: o.role ?? 'annotator',
      schema_version: src.schemaVersion,
      anchor_date: src.anchorDate,
      transcript: { source: o.transcriptSource ?? 'asr', asr_provider: src.transcript.asr_provider, asr_model_version: src.transcript.asr_model_version },
      prior_document_id: src.prior?.id ?? null,
      started_at: o.now,
      completed_at: null,
      facts: [],
    },
    transcript: src.transcript,
    audioUrl: src.audioUrl,
    prior: src.prior ? normaliseDocument(structuredClone(src.prior)) : null,
    entities: src.entities,
    rows: [],
    working,
  };
};

/* ------------------------------------------------------------------ */
/* Values                                                              */
/* ------------------------------------------------------------------ */

/** The quantiles an annotator gives for a vague value (§15.2). */
export interface Quantiles {
  p10: number | string;
  p50: number | string;
  p90: number | string;
}

export class AnnotationError extends Error {}

/**
 * A gold value from the editor. A point stays a point; options keep their weights; a range needs the
 * annotator's p10 / p50 / p90, and keeps the bounds the speaker gave (the editor's low and high).
 */
export const goldValue = (slot: SlotDef, v: EditorValue, quantiles?: Quantiles): GoldQuantity => {
  const [unit, period] = quantityUnit(slot);
  if (v.mode === 'point') {
    const d = toDist(slot, v);
    return { value: { kind: 'point', value: (d as { value: Scalar }).value }, unit, period };
  }
  if (v.mode === 'options') {
    const d = toDist(slot, v);
    const options =
      d.kind === 'categorical'
        ? Object.entries(d.probs).map(([value, weight]) => ({ value, weight }))
        : d.kind === 'discrete'
          ? d.support.map((value, i) => ({ value, weight: d.weights[i] }))
          : d.kind === 'mixture'
            ? d.components.map(([weight, c]) => ({ value: (c as { value: Scalar }).value, weight }))
            : d.kind === 'bernoulli'
              ? [
                  { value: true, weight: d.p },
                  { value: false, weight: pyRound(1 - d.p, 6) },
                ]
              : [{ value: (d as { value: Scalar }).value, weight: 1 }];
    return { value: { kind: 'options', options }, unit, period };
  }
  if (!quantiles) throw new AnnotationError('Give the p10, p50 and p90 you believe the client meant.');
  const [p10, p50, p90] = [quantiles.p10, quantiles.p50, quantiles.p90].map((x) => fromInput(slot, x));
  if (![p10, p50, p90].every(Number.isFinite)) throw new AnnotationError('Give the p10, p50 and p90 as numbers.');
  if (!(p10 <= p50 && p50 <= p90)) throw new AnnotationError('The quantiles must rise: p10 ≤ p50 ≤ p90.');
  const said = (x: number | string) => (x === '' || x === null || x === undefined ? null : fromInput(slot, x));
  const low = said(v.low);
  const high = said(v.high);
  if (low !== null && high !== null && low > high) throw new AnnotationError('The lowest value said must not exceed the highest.');
  return { value: { kind: 'range', low, high, p10, p50, p90 }, unit, period };
};

/** A distribution for a gold value, for showing it and writing its median into the sheet. */
export const goldDist = (g: GoldQuantity): Dist => {
  const v = g.value;
  if (v.kind === 'point') return { kind: 'point', value: v.value };
  if (v.kind === 'range') return v.p10 === v.p90 ? { kind: 'point', value: v.p50 } : { kind: 'triangular', low: v.p10, mode: v.p50, high: v.p90 };
  if (v.options.every((o) => typeof o.value === 'boolean')) return { kind: 'bernoulli', p: v.options.reduce((s, o) => (o.value === true ? s + o.weight : s), 0) };
  if (v.options.every((o) => typeof o.value === 'string')) return { kind: 'categorical', probs: Object.fromEntries(v.options.map((o) => [String(o.value), o.weight])) };
  return { kind: 'discrete', support: v.options.map((o) => Number(o.value)), weights: v.options.map((o) => o.weight) };
};

/** A gold value as a quantity, so it is phrased and drawn like the system's values. */
export const goldQuantity = (g: GoldQuantity): Quantity => ({ dist: goldDist(g), unit: g.unit, period: g.period, basis: 'na', real_or_nominal: 'na' });

/** Where the editor starts for a gold value: its mode, and for a range the bounds as said and the quantiles. */
export const goldToEditor = (slot: SlotDef, g: GoldQuantity | null): { value: EditorValue; quantiles?: Quantiles } => {
  if (!g) return { value: toEditor(slot, null) };
  const v = g.value;
  if (v.kind !== 'range') return { value: toEditor(slot, goldQuantity(g)) };
  const said = (x: number | null) => (x === null ? '' : toInput(slot, x));
  return {
    value: { mode: 'range', low: said(v.low), likely: toInput(slot, v.p50), high: said(v.high) },
    quantiles: { p10: toInput(slot, v.p10), p50: toInput(slot, v.p50), p90: toInput(slot, v.p90) },
  };
};

/** The sheet's value for a gold value: the annotator's median (the p50 of a range). */
const goldDocValue = (slot: SlotDef, g: GoldQuantity): unknown =>
  toFnaValue(slot, g.value.kind === 'range' ? { kind: 'point', value: g.value.p50 } : goldDist(g));

/** What the Python model would refuse (empty when the fact can be exported). */
export const goldProblems = (f: GoldFact): string[] => {
  const out: string[] = [];
  if (f.fact_id.split('#', 1)[0] !== f.slot_id) out.push(`fact id ${f.fact_id} does not match slot ${f.slot_id}`);
  if (f.status === 'stated' || f.status === 'implied' || f.status === 'contradicted') {
    if (!f.value) out.push(`a ${f.status.replace('_', ' ')} fact needs a value`);
    if (!f.spans.length) out.push(`a ${f.status.replace('_', ' ')} fact needs at least one transcript span`);
  }
  if ((f.status === 'not_discussed' || f.status === 'explicitly_unknown') && f.value) out.push(`a ${f.status.replace('_', ' ')} fact has no value`);
  const v = f.value?.value;
  if (v?.kind === 'range' && !(v.p10 <= v.p50 && v.p50 <= v.p90)) out.push('the quantiles must rise');
  if (v?.kind === 'options' && Math.abs(v.options.reduce((s, o) => s + o.weight, 0) - 1) > 1e-4) out.push('option weights must add up to 100%');
  return out;
};

/* ------------------------------------------------------------------ */
/* Editing                                                             */
/* ------------------------------------------------------------------ */

const ENTITY_TYPE: Record<ListContainer, string> = {
  dependants: 'person',
  retirementFunds: 'account',
  assets: 'property',
  liabilities: 'liability',
  policies: 'policy',
  goals: 'goal',
  'expenses.custom': 'account',
};

/** The entity behind a list row: one of the prior's rows, or a row the annotator added. */
export const annotationEntityOfRow = (a: AnnotationSession, container: string, rowId: string): string | null =>
  Object.values(a.entities).find((e) => e.fna_list === container && e.fna_id === rowId)?.entity_id ?? a.rows.find((r) => r.container === container && r.rowId === rowId)?.entityId ?? null;

export const annotationPathOf = (a: AnnotationSession, slot: SlotDef, instance: string | null) =>
  pathFor(slot, instance, (entityId) => a.rows.find((r) => r.entityId === entityId)?.rowId ?? rowIdFor(a.annotation.call_id, a.entities, entityId));

export const goldFactAt = (a: AnnotationSession, path: string): GoldFact | undefined => a.annotation.facts.find((f) => f.fna_path === path);

const fallback = (a: AnnotationSession, path: string): unknown => {
  const pp = parsePath(path);
  if (pp.key !== null && isListContainer(pp.container)) return a.prior && hasRow(a.prior, pp.container, pp.key) ? getPath(a.prior, path) : defaultAt(path);
  return a.prior ? getPath(a.prior, path) : defaultAt(path);
};

const writeGold = (a: AnnotationSession, f: GoldFact) => {
  if (!isDocumentPath(f.fna_path)) return;
  const slot = slotById(f.slot_id);
  if (!slot?.exported) return;
  const v = f.value ? goldDocValue(slot, f.value) : null;
  setPath(a.working, f.fna_path, v !== null && v !== undefined ? v : fallback(a, f.fna_path));
};

export interface GoldInput {
  slotId: string;
  /** A person, a prior row's entity, an expense key, a row the annotator added, or null for single fields. */
  instance: string | null;
  status: FactStatus;
  value: GoldQuantity | null;
  attribution: Attribution;
  spans: GoldSpan[];
  note?: string;
  entityLabel?: string;
}

/** Add or replace the annotation of one slot instance. */
export const setGoldFact = (a: AnnotationSession, g: GoldInput): AnnotationSession => {
  const slot = slotById(g.slotId);
  if (!slot) throw new AnnotationError(`Unknown slot ${g.slotId}.`);
  const fact: GoldFact = {
    fact_id: factIdFor(slot.slot_id, g.instance),
    slot_id: slot.slot_id,
    instance_key: g.instance,
    fna_path: annotationPathOf(a, slot, g.instance),
    entity_label: g.entityLabel ?? a.rows.find((r) => r.entityId === g.instance)?.label ?? '',
    status: g.status,
    value: g.status === 'not_discussed' || g.status === 'explicitly_unknown' ? null : g.value,
    speaker_attribution: g.attribution,
    spans: g.spans,
    note: g.note ?? '',
  };
  return produce(a, (d) => {
    const i = d.annotation.facts.findIndex((f) => f.fact_id === fact.fact_id);
    if (i >= 0) d.annotation.facts[i] = fact;
    else d.annotation.facts.push(fact);
    writeGold(d as AnnotationSession, fact);
  });
};

export const removeGoldFact = (a: AnnotationSession, factId: string): AnnotationSession =>
  produce(a, (d) => {
    const f = d.annotation.facts.find((x) => x.fact_id === factId);
    if (!f) return;
    d.annotation.facts = d.annotation.facts.filter((x) => x.fact_id !== factId);
    if (isDocumentPath(f.fna_path)) setPath(d.working as FnaDocument, f.fna_path, fallback(d as AnnotationSession, f.fna_path));
  });

/** A new row for an object the prior file does not have. */
export const addAnnotatorRow = (a: AnnotationSession, container: ListContainer, label: string): [AnnotationSession, AnnotatorRow] => {
  const n = a.rows.filter((r) => r.container === container).length + 1;
  const entityId = `${ENTITY_TYPE[container]}.annotator_${container.replace('.', '_')}_${n}`;
  const row: AnnotatorRow = { entityId, container, rowId: rowIdForNewEntity(a.annotation.call_id, entityId), label: label.trim() };
  const identity = `${container}[${row.rowId}].${IDENTITY_FIELD[container]}`;
  const next = produce(a, (d) => {
    d.rows.push(row);
    setPath(d.working as FnaDocument, identity, defaultAt(identity)); // creates the row with the FNA defaults
  });
  return [next, row];
};

/**
 * The instance of a forecast-only object the annotator names (an event, an income change): the FNA
 * has no row for it, so it is keyed by its name, "event.annotator_kagiso_studies_overseas".
 */
export const forecastInstance = (slot: SlotDef, label: string): string => {
  const slug = label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40);
  if (!slug) throw new AnnotationError('Name what this is about.');
  return `${slot.entity_type ?? 'event'}.annotator_${slug}`;
};

/**
 * The annotator typed in the sheet: each extracted field they change becomes (or updates) its
 * annotation as a point, stated, keeping any spans already given. Rows they add become annotator rows.
 */
export const editAnnotationDocument = (a: AnnotationSession, next: FnaDocument, attribution: Attribution = 'client'): AnnotationSession => {
  const changes = diffDocuments(a.working, next);
  let s = produce(a, (d) => {
    d.working = structuredClone(next);
    for (const row of changes.addedRows) {
      const pp = parsePath(row);
      if (!pp.key || !isListContainer(pp.container) || annotationEntityOfRow(d as AnnotationSession, pp.container, pp.key)) continue;
      const n = d.rows.filter((r) => r.container === pp.container).length + 1;
      const entityId = `${ENTITY_TYPE[pp.container]}.annotator_${pp.container.replace('.', '_')}_${n}`;
      const rowId = rowIdForNewEntity(d.annotation.call_id, entityId);
      d.rows.push({ entityId, container: pp.container, rowId, label: '' });
      const obj = getPath(d.working as FnaDocument, row) as Record<string, unknown> | undefined;
      if (obj) obj.id = rowId;
    }
    for (const row of changes.removedRows) {
      const pp = parsePath(row);
      if (!pp.key) continue;
      d.annotation.facts = d.annotation.facts.filter((f) => !f.fna_path.startsWith(`${row}.`));
      removeRow(d.working as FnaDocument, pp.container, pp.key);
    }
  });
  for (const path of changes.fields) {
    const at = slotAtPath(path, (c, k) => annotationEntityOfRow(s, c, k));
    if (!at) continue;
    const raw = getPath(s.working, path);
    if (raw === undefined || raw === null) continue;
    const existing = goldFactAt(s, path);
    const q = toQuantity(at.slot, raw);
    s = setGoldFact(s, {
      slotId: at.slot.slot_id,
      instance: at.instance,
      status: existing && existing.status !== 'not_discussed' && existing.status !== 'explicitly_unknown' ? existing.status : 'stated',
      value: { value: { kind: 'point', value: (q.dist as PointDist).value }, unit: q.unit, period: q.period },
      attribution: existing?.speaker_attribution ?? attribution,
      spans: existing?.spans ?? [],
      note: existing?.note,
      entityLabel: existing?.entity_label,
    });
  }
  return s;
};

/* ------------------------------------------------------------------ */
/* Finishing                                                           */
/* ------------------------------------------------------------------ */

/** Every annotated fact that the evals model would refuse, by fact id. */
export const annotationProblems = (a: AnnotationSession): { factId: string; problems: string[] }[] =>
  a.annotation.facts.map((f) => ({ factId: f.fact_id, problems: goldProblems(f) })).filter((p) => p.problems.length);

/** The gold annotation file: facts in a stable order, the completion time set. */
export const goldFile = (a: AnnotationSession, completedAt: string | null): GoldAnnotation => ({
  ...a.annotation,
  completed_at: completedAt,
  facts: [...a.annotation.facts].sort((x, y) => x.fact_id.localeCompare(y.fact_id)),
});

/** The input kind of a gold fact's slot (for the editor). */
export const goldInputKind = (slotId: string) => {
  const slot = slotById(slotId);
  return slot ? inputKind(slot) : 'text';
};
