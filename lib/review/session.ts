/**
 * One adviser's review of one call (spec §11): the facts as the overlay shows them, the reviewer's
 * ops, the document the sheet shows, and the flags, follow-ups and terms the reviewer settles.
 *
 * Every action goes through applyAction, which builds the ops (./ops.ts), numbers them after the
 * profile's last op, applies them to the local facts the way the pipeline's reducer does, and
 * rewrites the affected fields of the working document the way binding.materialise does — so the
 * sheet, the needs analysis and the impact strip move the moment the reviewer decides, and the
 * document the app saves is the document the pipeline will produce from the same ops.
 *
 * The session is a plain object, updated immutably (immer), and saved on this device only.
 */
import { current, isDraft, produce, type Draft } from 'immer';
import { normaliseDocument } from '../fna/defaults';
import type { FnaDocument } from '../fna/types';
import { factIdFor, fieldLabel, listOf, pathFor, quantityFor, rowIdFor, slotAtPath, slotById, splitFactId, toFnaValue, toQuantity, type SlotDef } from './catalog';
import { mixture, phrase, phraseHypotheses, summarise } from './dist';
import {
  acceptOp,
  addFactOps,
  editOp,
  numberOps,
  reattributeOps,
  rejectOp,
  removeRowOp,
  reviewerHypothesis,
  reviewerProvenance,
  speakerOp,
  statusOp,
  type OpDraft,
  type RejectReason,
  type Stamp,
} from './ops';
import { DICT_CONTAINERS, IDENTITY_FIELD, defaultAt, diffDocuments, getPath, hasRow, isDocumentPath, isListContainer, parsePath, removeRow, setPath, type ListContainer } from './paths';
import type {
  Attribution,
  BlockingFlag,
  ChangeKind,
  Dist,
  Evidence,
  FactSource,
  FactStatus,
  FlagClearance,
  FlagResolution,
  FollowUp,
  FollowupEdit,
  Hypothesis,
  Quantity,
  ReviewBundle,
  ReviewResult,
  ReviewState,
  SidecarField,
  StateOp,
  TermConfirmation,
  TermResolution,
  Tier,
  ValueSummary,
} from './types';
import { REVIEW_RESULT_FORMAT } from './types';
import { rowIdForNewEntity } from './uuid';
import { sameValue, toDist, type EditorValue } from './values';

/* ------------------------------------------------------------------ */
/* State                                                               */
/* ------------------------------------------------------------------ */

/** A fact as the overlay holds it: the sidecar's view, kept current as the reviewer acts. */
export interface ReviewFact {
  factId: string;
  slotId: string;
  instanceKey: string | null;
  /** Concrete FnaDocument path (or forecast-only path). */
  path: string;
  label: string;
  group: string;
  tier: Tier;
  exported: boolean;
  source: FactSource;
  status: FactStatus;
  change: ChangeKind;
  /** The merged value over the hypotheses (null when unknown or not discussed). */
  quantity: Quantity | null;
  hypotheses: Hypothesis[];
  /** The value as the adviser reads it (dist.phraseHypotheses). */
  valueText: string;
  prior: ValueSummary | null;
  priorText: string | null;
  pCorrect: number;
  autoAccepted: boolean;
  priority: number;
  why: string | null;
  attribution: Attribution;
  said: string;
  interpretation: string;
  hedges: string[];
  evidence: Evidence[];
  entityRefs: string[];
  derivation: string | null;
  termResolution: TermResolution | null;
  review: ReviewState;
  /** Where the fact came from: the pipeline's sidecar, or the reviewer. */
  origin: 'pipeline' | 'reviewer';
  /** The reviewer set the value: the sheet shows its median as computed here, not the pipeline's. */
  edited: boolean;
  /** Re-attributed to another person or row: kept for the record, gone from the sheet and the queue. */
  moved: boolean;
}

/** A list row the reviewer added: its entity and the row id the pipeline will give it. */
export interface ReviewerEntity {
  entityId: string;
  container: ListContainer;
  rowId: string;
}

/** A field the client's file changed on this device after it was exported for the call, which the call also changed. */
export interface Conflict {
  path: string;
  local: unknown;
  call: unknown;
}

export interface ReviewSession {
  version: 1;
  callId: string;
  runId: string;
  /** The client file (IndexedDB) this review updates when it is submitted. */
  docId: string;
  reviewerId: string;
  createdAt: string;
  /** As received from the pipeline; never changed. */
  bundle: ReviewBundle;
  /** Where unusable facts fall back to: the prior FNA (null for a new client). */
  base: FnaDocument | null;
  /** What the sheet shows: the median document with the reviewer's decisions applied. */
  working: FnaDocument;
  facts: Record<string, ReviewFact>;
  /** Concrete path → id of the fact the sheet shows there. */
  pathIndex: Record<string, string>;
  reviewerEntities: ReviewerEntity[];
  ops: StateOp[];
  flags: BlockingFlag[];
  flagClearances: FlagClearance[];
  followups: FollowUp[];
  followupEdits: FollowupEdit[];
  termConfirmations: TermConfirmation[];
  conflicts: Conflict[];
  /** Sheet edits on extracted fields not yet turned into ops (they become ops when the field loses focus). */
  pending: string[];
  /** This review also puts every auto-accepted fact under "Needs you" (§11.3). */
  spotCheck: boolean;
  activeSeconds: number;
  submittedAt: string | null;
  /** API mode: ops up to this seq have been accepted by POST /calls/{id}/ops. */
  sentSeq: number;
  /** API mode: ops the pipeline refused (by the seq the app gave them). They stay in the review and its result file. */
  refused: RefusedOp[];
}

export interface RefusedOp {
  seq: number;
  reason: string;
  at: string;
}

/* ------------------------------------------------------------------ */
/* Facts                                                               */
/* ------------------------------------------------------------------ */

const SEED_SOURCES = new Set<FactSource>(['prior_fna', 'crm', 'fna_default']);

const plain = <T>(x: T): T => (isDraft(x) ? (current(x as Draft<T>) as T) : x);

/** Whether the call (or the reviewer) touched this fact, so it is reviewed and badged. Untouched prior values are not. */
export const isReviewItem = (f: ReviewFact): boolean => !f.moved && (f.origin === 'reviewer' || f.source !== 'prior_fna' || f.status !== 'not_discussed');

export const isUsable = (f: ReviewFact): boolean => !f.moved && f.review.status !== 'rejected' && f.quantity !== null;

export const isDecided = (f: ReviewFact): boolean => ['accepted', 'edited', 'rejected'].includes(f.review.status);

const factFromSidecar = (path: string, s: SidecarField): ReviewFact => {
  const slot = slotById(s.slot_id);
  const instance = splitFactId(s.fact_id).instance;
  return {
    factId: s.fact_id,
    slotId: s.slot_id,
    instanceKey: instance,
    path,
    label: slot ? fieldLabel(slot, instance) : s.label,
    group: s.group,
    tier: s.tier,
    exported: s.exported,
    source: s.source,
    status: s.status,
    change: s.change,
    quantity: s.quantity,
    hypotheses: s.hypotheses.map((h) => ({ value: h.quantity, weight: h.weight, rationale: h.rationale, revises: h.revises })),
    valueText: phraseHypotheses(s.hypotheses.map((h) => ({ value: h.quantity, weight: h.weight, rationale: h.rationale })), s.quantity),
    prior: s.prior_value,
    priorText: s.prior_text,
    pCorrect: s.p_correct,
    autoAccepted: s.auto_accepted,
    priority: s.review_priority,
    why: s.why,
    attribution: s.speaker_attribution,
    said: s.said,
    interpretation: s.interpretation,
    hedges: s.hedges,
    evidence: s.evidence,
    entityRefs: s.entity_refs,
    derivation: s.derivation,
    termResolution: s.term_resolution,
    review: s.review,
    origin: 'pipeline',
    edited: false,
    moved: false,
  };
};

/** The weighted mixture of the hypotheses' values (reducer.merged_value). */
export const mergedValue = (hyps: Hypothesis[]): Quantity | null => {
  const valued = hyps.filter((h) => h.value && h.weight > 0);
  if (!valued.length) return null;
  const top = valued.reduce((a, b) => (b.weight > a.weight ? b : a)).value as Quantity;
  const dist = mixture(valued.map((h) => [h.weight, (h.value as Quantity).dist] as [number, Dist]));
  const bases = new Set(valued.map((h) => (h.value as Quantity).basis));
  return { dist, unit: top.unit, period: top.period, basis: bases.size === 1 ? top.basis : 'unknown', real_or_nominal: top.real_or_nominal };
};

const renormalise = (hyps: Hypothesis[]): Hypothesis[] => {
  if (!hyps.length) return [];
  const total = hyps.reduce((s, h) => s + h.weight, 0);
  return hyps.map((h) => ({ ...h, weight: total > 0 ? h.weight / total : 1 / hyps.length }));
};

/** Change status against the prior value (reducer.compute_change). */
const computeChange = (f: ReviewFact): ChangeKind => {
  if (f.change === 'removed') return 'removed';
  if (SEED_SOURCES.has(f.source) && f.status === 'not_discussed') return f.prior ? 'unchanged_not_discussed' : 'new';
  if (!f.prior) return 'new';
  if (!f.quantity) return f.status === 'not_discussed' || f.status === 'explicitly_unknown' ? 'unchanged_not_discussed' : 'changed';
  const now = summarise(f.quantity);
  return now && sameValue(now, f.prior) ? 'confirmed' : 'changed';
};

/** Recompute the merged value, the change and the phrase after an op (reducer._finalise). */
const finalise = (f: ReviewFact): ReviewFact => {
  const hypotheses = renormalise(f.hypotheses);
  const quantity = mergedValue(hypotheses);
  const next = { ...f, hypotheses, quantity };
  return { ...next, change: computeChange(next), valueText: phraseHypotheses(hypotheses, quantity) };
};

const keepOriginal = (prev: ReviewState, next: ReviewState): ReviewState => ({
  ...next,
  ...(prev.original_value ? { original_value: prev.original_value } : {}),
  ...(prev.original_status ? { original_status: prev.original_status } : {}),
});

/** One reviewer op applied to a fact, as the pipeline's reducer applies it to the profile. */
const reduce = (f: ReviewFact | undefined, op: StateOp): ReviewFact => {
  if (op.op === 'ADD') {
    const p = op.payload;
    const slot = slotById(p.slot_id);
    const seeded = f && SEED_SOURCES.has(f.source) && !f.moved;
    // A statement about a slot the reviewer rejected or moved away replaces that fact, which keeps its prior.
    const replaced = f && !seeded && (f.moved || f.review.status === 'rejected');
    const added: ReviewFact = {
      factId: op.fact_id,
      slotId: p.slot_id,
      instanceKey: p.instance_key ?? null,
      path: p.fna_path,
      label: slot ? fieldLabel(slot, p.instance_key ?? null) : p.slot_id,
      group: slot?.group ?? '',
      tier: slot?.sensitivity_tier ?? 'C',
      exported: slot?.exported ?? true,
      source: p.source ?? 'reviewer',
      status: p.status ?? 'stated',
      change: 'new',
      quantity: null,
      hypotheses: p.hypotheses ?? [],
      valueText: '',
      // A statement about a seeded field: the seed becomes the prior.
      prior: seeded ? (f.prior ?? summarise(f.quantity)) : replaced ? f.prior : null,
      priorText: seeded ? (f.priorText ?? (f.quantity ? phrase(f.quantity) : null)) : replaced ? f.priorText : null,
      pCorrect: 1,
      autoAccepted: false,
      priority: f?.priority ?? 0,
      why: null,
      attribution: p.speaker_attribution ?? 'client',
      said: p.said ?? '',
      interpretation: p.interpretation ?? '',
      hedges: [],
      evidence: p.evidence ?? [],
      entityRefs: p.entity_refs ?? [],
      derivation: null,
      termResolution: null,
      review: seeded ? f.review : { status: 'pending' },
      origin: 'reviewer',
      edited: true,
      moved: false,
    };
    if (!f || seeded || replaced) return finalise(added);
    // An ADD on a call fact merges hypotheses: identical values pool their weight.
    const merged = [...f.hypotheses];
    for (const h of added.hypotheses) {
      const i = merged.findIndex((e) => JSON.stringify(e.value) === JSON.stringify(h.value));
      if (i >= 0) merged[i] = { ...merged[i], weight: merged[i].weight + h.weight };
      else merged.push(h);
    }
    const seen = new Set(f.evidence.map((e) => `${e.segment_id}:${e.char_start}:${e.char_end}:${e.role}`));
    const evidence = [...f.evidence, ...added.evidence.filter((e) => !seen.has(`${e.segment_id}:${e.char_start}:${e.char_end}:${e.role}`))];
    const status = (f.status === 'not_discussed' || f.status === 'explicitly_unknown') && (added.status === 'stated' || added.status === 'implied') ? added.status : f.status;
    return finalise({ ...f, status, hypotheses: merged, evidence, entityRefs: [...new Set([...f.entityRefs, ...added.entityRefs])].sort(), edited: true });
  }
  if (!f) throw new ReviewError(`${op.op} names unknown fact ${op.fact_id}`);
  switch (op.op) {
    case 'CONFIRM': {
      const r = op.payload.review ?? { status: 'accepted' };
      return finalise({ ...f, review: keepOriginal(f.review, { ...r, status: r.status === 'edited' ? 'edited' : 'accepted' }) });
    }
    case 'UPDATE': {
      const p = op.payload;
      let next: ReviewFact = { ...f };
      let review = f.review;
      if (p.replace_hypotheses) {
        if (f.quantity && !review.original_value) review = { ...review, original_value: f.quantity };
        next = { ...next, hypotheses: p.replace_hypotheses, edited: true };
      }
      if (p.status) {
        if (p.status !== f.status && !review.original_status) review = { ...review, original_status: f.status };
        next = { ...next, status: p.status };
        if ((p.status === 'stated' || p.status === 'implied') && SEED_SOURCES.has(f.source)) next.source = 'reviewer';
      }
      if (p.speaker_attribution) next.attribution = p.speaker_attribution;
      if (p.source) next.source = p.source;
      next.review = p.review ? keepOriginal(review, p.review) : review;
      return finalise(next);
    }
    case 'RETRACT': {
      const p = op.payload;
      const r = p.review ?? { status: 'rejected' };
      return finalise({
        ...f,
        review: keepOriginal(f.review, { ...r, status: 'rejected', reason: p.reason ?? null, note: p.note || r.note || null }),
        change: p.reason === 'removed' ? 'removed' : f.change,
        moved: f.moved || p.reason === 'wrong_entity',
      });
    }
    case 'MARK_UNKNOWN': {
      // What was there is kept, for the label of §9.3 ("status changed → 0 for the original").
      const originals: ReviewState = {
        ...f.review,
        ...(f.review.original_value || f.quantity ? { original_value: f.review.original_value ?? f.quantity } : {}),
        ...(f.review.original_status || f.status !== 'explicitly_unknown' ? { original_status: f.review.original_status ?? f.status } : {}),
      };
      return finalise({ ...f, status: 'explicitly_unknown', hypotheses: [], review: keepOriginal(originals, op.payload.review ?? originals), edited: true });
    }
  }
};

/* ------------------------------------------------------------------ */
/* The working document                                                */
/* ------------------------------------------------------------------ */

const sameJson = (a: unknown, b: unknown) => a === b || JSON.stringify(a) === JSON.stringify(b);

/** What the sheet shows at a fact's path while the fact is usable: the pipeline's median, or the reviewer's. */
const factDocValue = (s: ReviewSession, f: ReviewFact): unknown => {
  const slot = slotById(f.slotId);
  if (!slot || !isUsable(f)) return null;
  if (!f.edited) {
    const v = getPath(s.bundle.median_document, f.path);
    if (v !== undefined) return v;
  }
  return toFnaValue(slot, (f.quantity as Quantity).dist);
};

/** Where an unusable fact falls back to: the prior FNA's value, else the FNA default (binding.materialise). */
const fallbackValue = (s: ReviewSession, path: string): unknown => {
  const pp = parsePath(path);
  const base = s.base;
  if (pp.key !== null && DICT_CONTAINERS.has(pp.container)) return base ? getPath(base, path) : undefined;
  if (pp.key !== null && isListContainer(pp.container)) return base && hasRow(base, pp.container, pp.key) ? getPath(base, path) : defaultAt(path);
  return base ? getPath(base, path) : defaultAt(path);
};

/** The value the sheet shows at a path when nothing was typed there. */
export const expectedValue = (s: ReviewSession, path: string): unknown => {
  const id = s.pathIndex[path];
  const v = id ? factDocValue(s, s.facts[id]) : null;
  return v !== null && v !== undefined ? v : fallbackValue(s, path);
};

const rowFacts = (s: ReviewSession, container: string, key: string): ReviewFact[] =>
  Object.values(s.facts).filter((f) => {
    if (!f.exported) return false;
    const pp = parsePath(f.path);
    return pp.container === container && pp.key === key;
  });

/** A row the call or the reviewer created goes when nothing usable is left in it (binding._ensure_rows). */
const pruneRow = (s: Draft<ReviewSession>, container: string, key: string) => {
  const base = s.base as FnaDocument | null;
  if (base && hasRow(base, container, key)) return;
  if (!rowFacts(s as ReviewSession, container, key).some(isUsable)) removeRow(s.working as FnaDocument, container, key);
};

/** Rewrite one fact's field in the working document (binding.materialise, one field at a time). */
const writeFact = (s: Draft<ReviewSession>, f: ReviewFact) => {
  if (!f.exported || !isDocumentPath(f.path)) return;
  const doc = s.working as FnaDocument;
  const pp = parsePath(f.path);
  const list = pp.key !== null && isListContainer(pp.container) ? pp.container : null;
  if (list && pp.key && f.change === 'removed' && pp.field === IDENTITY_FIELD[list]) {
    removeRow(doc, list, pp.key);
    return;
  }
  const v = factDocValue(s as ReviewSession, f);
  if (v !== null && v !== undefined) {
    setPath(doc, f.path, v);
    return;
  }
  if (list && pp.key && !hasRow(doc, list, pp.key)) return;
  setPath(doc, f.path, fallbackValue(s as ReviewSession, f.path));
  if (list && pp.key) pruneRow(s, list, pp.key);
};

const reindex = (s: Draft<ReviewSession>) => {
  const index: Record<string, string> = {};
  for (const f of Object.values(s.facts)) if (!f.moved) index[f.path] = f.factId;
  s.pathIndex = index;
};

/* ------------------------------------------------------------------ */
/* Entities and instances                                              */
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

type EntityContext = Pick<ReviewSession, 'bundle' | 'reviewerEntities' | 'callId'>;

/** The entity behind a list row: from the registry, a row the call created, or a row the reviewer added. */
export const entityOfRow = (s: EntityContext, container: string, rowId: string): string | null => {
  const entities = Object.values(s.bundle.entities);
  const own = entities.find((e) => e.fna_list === container && e.fna_id === rowId);
  if (own) return own.entity_id;
  const created = entities.find((e) => e.fna_list === container && !e.fna_id && rowIdForNewEntity(s.callId, e.entity_id) === rowId);
  if (created) return created.entity_id;
  return s.reviewerEntities.find((r) => r.container === container && r.rowId === rowId)?.entityId ?? null;
};

export const rowIdOf = (s: EntityContext, entityId: string): string => s.reviewerEntities.find((r) => r.entityId === entityId)?.rowId ?? rowIdFor(s.callId, s.bundle.entities, entityId);

/** The concrete path of a slot for an instance in this review. */
export const pathOf = (s: EntityContext, slot: SlotDef, instance: string | null): string => pathFor(slot, instance, (entityId) => rowIdOf(s, entityId));

const newReviewerEntity = (s: Draft<ReviewSession>, container: ListContainer): ReviewerEntity => {
  const n = s.reviewerEntities.filter((r) => r.container === container).length + 1;
  const entityId = `${ENTITY_TYPE[container]}.reviewer_${container.replace('.', '_')}_${n}`;
  const r: ReviewerEntity = { entityId, container, rowId: rowIdForNewEntity(s.callId, entityId) };
  s.reviewerEntities.push(r);
  return r;
};

/** Registry entities a fact about this instance refers to (a row the reviewer adds is not in the registry). */
const refsFor = (s: ReviewSession, slot: SlotDef, instance: string | null): string[] => {
  if (slot.slot_id.includes('{p}')) return [instance === 'spouse' ? 'person.spouse' : 'person.client_primary'].filter((e) => e in s.bundle.entities);
  return instance && instance in s.bundle.entities ? [instance] : [];
};

/* ------------------------------------------------------------------ */
/* Creating a session                                                  */
/* ------------------------------------------------------------------ */

export interface CreateOptions {
  docId: string;
  reviewerId: string;
  now: string;
  /** The client's file as it is on this device, when it may have changed since it was exported for the call. */
  local?: FnaDocument | null;
}

export const createSession = (bundle: ReviewBundle, o: CreateOptions): ReviewSession => {
  const facts: Record<string, ReviewFact> = {};
  const pathIndex: Record<string, string> = {};
  for (const [path, field] of Object.entries(bundle.sidecar.fields)) {
    facts[field.fact_id] = factFromSidecar(path, field);
    pathIndex[path] = field.fact_id;
  }
  const session: ReviewSession = {
    version: 1,
    callId: bundle.call_id,
    runId: bundle.run_id,
    docId: o.docId,
    reviewerId: o.reviewerId,
    createdAt: o.now,
    bundle,
    base: bundle.prior_document ? normaliseDocument(structuredClone(bundle.prior_document)) : null,
    working: normaliseDocument(structuredClone(bundle.median_document)),
    facts,
    pathIndex,
    reviewerEntities: [],
    ops: [],
    flags: structuredClone(bundle.blocking_flags),
    flagClearances: [],
    followups: structuredClone(bundle.followups),
    followupEdits: [],
    termConfirmations: [],
    conflicts: [],
    pending: [],
    spotCheck: bundle.spot_check,
    activeSeconds: 0,
    submittedAt: null,
    sentSeq: 0,
    refused: [],
  };
  return o.local && bundle.prior_document ? rebase(session, o.local, { reviewerId: o.reviewerId, at: o.now }) : session;
};

/** A session saved by an earlier build of the app: fields added since take their defaults. */
export const normaliseSession = (s: ReviewSession): ReviewSession => ({ ...s, refused: s.refused ?? [], conflicts: s.conflicts ?? [], pending: s.pending ?? [], sentSeq: s.sentSeq ?? 0 });

const listOfDoc = (doc: FnaDocument, container: ListContainer): unknown[] =>
  container === 'expenses.custom' ? doc.expenses.custom : (doc as unknown as Record<string, unknown[]>)[container];

/**
 * The client's file changed on this device after it was exported for the call: carry those changes
 * into the review. Fields the call did not touch take the device's value (as reviewer edits, so the
 * pipeline's document agrees); fields both changed keep the call's proposal and are listed as conflicts.
 */
const rebase = (session: ReviewSession, local: FnaDocument, stamp: Stamp): ReviewSession => {
  const prior = normaliseDocument(structuredClone(session.bundle.prior_document as FnaDocument));
  const mine = normaliseDocument(structuredClone(local));
  const changes = diffDocuments(prior, mine);
  const conflicts: Conflict[] = [];
  const next = produce(session.working, (w) => {
    const doc = w as FnaDocument;
    for (const path of changes.fields) {
      const id = session.pathIndex[path];
      const f = id ? session.facts[id] : undefined;
      if (f && isReviewItem(f)) {
        conflicts.push({ path, local: getPath(mine, path), call: getPath(session.working, path) });
        continue;
      }
      const pp = parsePath(path);
      if (pp.key !== null && isListContainer(pp.container) && !hasRow(doc, pp.container, pp.key)) continue;
      setPath(doc, path, getPath(mine, path));
    }
    for (const row of changes.addedRows) {
      const pp = parsePath(row);
      const r = getPath(mine, row);
      // A row the call proposes too (the device holds an earlier review of this call): the call's version is under review.
      if (isListContainer(pp.container) && r && pp.key !== null && !hasRow(doc, pp.container, pp.key)) listOfDoc(doc, pp.container).push(structuredClone(r));
    }
    for (const row of changes.removedRows) {
      const pp = parsePath(row);
      if (pp.key) removeRow(doc, pp.container, pp.key);
    }
    // What the pipeline never extracts comes from the device: the engagement, advice record and assumptions.
    doc.engagement = structuredClone(mine.engagement);
    doc.advice = structuredClone(mine.advice);
    doc.assumptions = structuredClone(mine.assumptions);
    doc.notes = mine.notes;
    doc.status = mine.status;
    doc.riskProfile.notes = mine.riskProfile.notes;
  });
  const edited = commitPending(editDocument(session, next, stamp), stamp);
  return produce(edited, (s) => {
    s.conflicts = conflicts;
  });
};

/* ------------------------------------------------------------------ */
/* Actions                                                             */
/* ------------------------------------------------------------------ */

/** Who a re-attributed or added fact is about: a person, an existing row's entity, an expense key, or a new row. */
export type InstanceTarget = { kind: 'instance'; instance: string | null } | { kind: 'new-row' };

export type FollowupChange =
  | { action: 'reword'; followupId: string; question: string }
  | { action: 'drop'; followupId: string }
  | { action: 'add'; question: string; factIds: string[] };

export type ReviewAction =
  | { type: 'accept'; factId: string; note?: string }
  | { type: 'acceptMany'; factIds: string[] }
  | { type: 'edit'; factId: string; value: EditorValue; note?: string }
  | { type: 'setStatus'; factId: string; status: 'stated' | 'implied' | 'explicitly_unknown' | 'not_discussed'; value?: EditorValue; note?: string }
  | { type: 'reject'; factId: string; reason: RejectReason; note?: string }
  | { type: 'removeRow'; factId: string; note?: string }
  | { type: 'reattribute'; factId: string; to: InstanceTarget; note?: string }
  | { type: 'reattributeSpeaker'; factId: string; attribution: Attribution }
  | { type: 'addFact'; slotId: string; to: InstanceTarget; value: EditorValue; status?: 'stated' | 'implied'; evidence?: Evidence[]; attribution?: Attribution; note?: string }
  | { type: 'clearFlag'; flagId: string; resolution: FlagResolution; value?: EditorValue; note?: string }
  | { type: 'followup'; change: FollowupChange }
  | { type: 'confirmTerm'; factId: string; canonicalName: string; productType: string; provider: string; confirmed: boolean };

export class ReviewError extends Error {}

const lastSeq = (s: Draft<ReviewSession>) => (s.ops.length ? s.ops[s.ops.length - 1].seq : s.bundle.sidecar.last_seq);

/** Number ops after the log, apply them to the local facts, and rewrite the fields they touch. */
const emit = (s: Draft<ReviewSession>, drafts: OpDraft[]): StateOp[] => {
  const ops = numberOps(drafts.map(plain), lastSeq(s));
  const touched = new Set<string>();
  for (const op of ops) {
    s.facts[op.fact_id] = reduce(s.facts[op.fact_id] ? plain(s.facts[op.fact_id] as ReviewFact) : undefined, op) as Draft<ReviewFact>;
    s.ops.push(op as Draft<StateOp>);
    touched.add(op.fact_id);
  }
  reindex(s);
  for (const id of touched) writeFact(s, plain(s.facts[id] as ReviewFact));
  return ops;
};

/** A snapshot of a fact (never a draft), for building ops. */
const factOf = (s: Draft<ReviewSession>, factId: string): ReviewFact => {
  const f = s.facts[factId];
  if (!f) throw new ReviewError(`No fact ${factId} in this review.`);
  return plain(f as ReviewFact);
};

const slotOfFact = (f: ReviewFact): SlotDef => {
  const slot = slotById(f.slotId);
  if (!slot) throw new ReviewError(`Unknown slot ${f.slotId}.`);
  return slot;
};

const hypothesesOf = (slot: SlotDef, dist: Dist, basis: Quantity['basis'] = 'na'): Hypothesis[] => [reviewerHypothesis(quantityFor(slot, dist, basis))];

/** Clear the open flags on a fact once the reviewer has decided it (§10.3: accept, edit or mark unknown). */
const settleFlags = (s: Draft<ReviewSession>, factId: string, resolution: FlagResolution, stamp: Stamp, note = '') => {
  for (const flag of s.flags) {
    if (flag.cleared || flag.fact_id !== factId) continue;
    flag.cleared = true;
    flag.cleared_by = stamp.reviewerId;
    flag.resolution = resolution;
    s.flagClearances.push({ flag_id: flag.flag_id, resolution, reviewer_id: stamp.reviewerId, at: stamp.at, note });
  }
};

const resolveTarget = (s: Draft<ReviewSession>, slot: SlotDef, to: InstanceTarget): { instance: string | null; path: string; refs: string[] } => {
  if (to.kind === 'new-row') {
    const list = listOf(slot);
    if (!list || !isListContainer(list)) throw new ReviewError(`${slot.label} is not a list field.`);
    const r = newReviewerEntity(s, list);
    return { instance: r.entityId, path: pathOf(s as ReviewSession, slot, r.entityId), refs: [] };
  }
  return { instance: to.instance, path: pathOf(s as ReviewSession, slot, to.instance), refs: refsFor(s as ReviewSession, slot, to.instance) };
};

const run = (s: Draft<ReviewSession>, a: ReviewAction, stamp: Stamp): void => {
  switch (a.type) {
    case 'accept': {
      const f = factOf(s, a.factId);
      if (f.review.status !== 'accepted' && f.review.status !== 'edited') emit(s, [acceptOp(f, stamp, a.note)]);
      settleFlags(s, f.factId, 'accepted', stamp, a.note);
      return;
    }
    case 'acceptMany': {
      const facts = a.factIds.map((id) => factOf(s, id));
      emit(s, facts.filter((f) => !isDecided(f)).map((f) => acceptOp(f, stamp)));
      for (const f of facts) settleFlags(s, f.factId, 'accepted', stamp);
      return;
    }
    case 'edit': {
      const f = factOf(s, a.factId);
      const slot = slotOfFact(f);
      emit(s, [editOp(f, hypothesesOf(slot, toDist(slot, a.value), f.quantity?.basis), stamp, a.note)]);
      settleFlags(s, f.factId, 'edited', stamp, a.note);
      return;
    }
    case 'setStatus': {
      const f = factOf(s, a.factId);
      const slot = slotOfFact(f);
      const hyps = a.value ? hypothesesOf(slot, toDist(slot, a.value), f.quantity?.basis) : a.status === 'stated' || a.status === 'implied' ? f.hypotheses : null;
      emit(s, [statusOp(f, a.status, hyps, stamp, a.note)]);
      settleFlags(s, f.factId, a.status === 'explicitly_unknown' ? 'marked_unknown' : 'edited', stamp, a.note);
      return;
    }
    case 'reject': {
      const f = factOf(s, a.factId);
      emit(s, [rejectOp(f, a.reason, stamp, a.note)]);
      settleFlags(s, f.factId, 'edited', stamp, `rejected: ${a.reason}${a.note ? ` (${a.note})` : ''}`);
      return;
    }
    case 'removeRow': {
      const f = factOf(s, a.factId);
      const pp = parsePath(f.path);
      if (!pp.key || !isListContainer(pp.container) || pp.field !== IDENTITY_FIELD[pp.container]) throw new ReviewError('Only a row’s identifying field can remove the row.');
      const others = rowFacts(s as ReviewSession, pp.container, pp.key);
      emit(s, [removeRowOp(f, stamp, a.note)]);
      for (const g of others) settleFlags(s, g.factId, 'edited', stamp, 'row removed');
      return;
    }
    case 'reattribute': {
      const f = factOf(s, a.factId);
      const slot = slotOfFact(f);
      if (slot.cardinality === 'one') throw new ReviewError(`${f.label} belongs to the household as a whole.`);
      if (a.to.kind === 'instance' && (a.to.instance === f.instanceKey || a.to.instance === null)) return;
      // The target must be free: merging two live statements about one field would make a value nobody said.
      const target = a.to.kind === 'instance' ? s.facts[factIdFor(slot.slot_id, a.to.instance)] : undefined;
      if (target && isReviewItem(plain(target as ReviewFact)) && target.review.status !== 'rejected') {
        throw new ReviewError(`${target.label} already has a value there: reject or edit that one first.`);
      }
      const to = resolveTarget(s, slot, a.to);
      if (to.instance === f.instanceKey || to.instance === null) return;
      emit(s, reattributeOps(f, { instance: to.instance, path: to.path, entityRefs: to.refs }, stamp, reviewerProvenance(s.bundle), a.note));
      settleFlags(s, f.factId, 'edited', stamp, `re-attributed to ${to.instance}`);
      return;
    }
    case 'reattributeSpeaker': {
      const f = factOf(s, a.factId);
      if (f.attribution !== a.attribution) emit(s, [speakerOp(f, a.attribution, stamp)]);
      return;
    }
    case 'addFact': {
      const slot = slotById(a.slotId);
      if (!slot) throw new ReviewError(`Unknown slot ${a.slotId}.`);
      const dist = toDist(slot, a.value);
      const to = resolveTarget(s, slot, a.to);
      const existing = s.facts[factIdFor(slot.slot_id, to.instance)];
      if (existing && isReviewItem(plain(existing as ReviewFact))) throw new ReviewError(`${existing.label} is already in this review: edit it instead.`);
      emit(
        s,
        addFactOps(
          {
            slotId: slot.slot_id,
            instance: to.instance,
            path: to.path,
            status: a.status ?? 'stated',
            hypotheses: hypothesesOf(slot, dist),
            evidence: a.evidence ?? [],
            attribution: a.attribution ?? 'client',
            interpretation: a.note || 'Added by the reviewer.',
            entityRefs: to.refs,
          },
          stamp,
          reviewerProvenance(s.bundle),
        ),
      );
      return;
    }
    case 'clearFlag': {
      const flag = s.flags.find((x) => x.flag_id === a.flagId);
      if (!flag) throw new ReviewError(`No flag ${a.flagId}.`);
      if (flag.cleared) return;
      if (a.resolution === 'dismissed' && !a.note?.trim()) throw new ReviewError('Say why the flag does not apply.');
      if (a.resolution !== 'dismissed' && flag.fact_id) {
        if (a.resolution === 'accepted') run(s, { type: 'accept', factId: flag.fact_id, note: a.note }, stamp);
        else if (a.resolution === 'edited') {
          if (!a.value) throw new ReviewError('Give the corrected value.');
          run(s, { type: 'edit', factId: flag.fact_id, value: a.value, note: a.note }, stamp);
        } else run(s, { type: 'setStatus', factId: flag.fact_id, status: 'explicitly_unknown', note: a.note }, stamp);
      }
      if (!flag.cleared) {
        flag.cleared = true;
        flag.cleared_by = stamp.reviewerId;
        flag.resolution = a.resolution;
        s.flagClearances.push({ flag_id: flag.flag_id, resolution: a.resolution, reviewer_id: stamp.reviewerId, at: stamp.at, note: a.note ?? '' });
      }
      return;
    }
    case 'followup': {
      const c = a.change;
      if (c.action === 'add') {
        const question = c.question.trim();
        if (!question) throw new ReviewError('Write the question.');
        const n = s.followups.filter((q) => q.followup_id.startsWith('added-')).length + 1;
        s.followups.push({ followup_id: `added-${n}`, fact_ids: c.factIds, question, why: 'Added by the adviser.', expected_pos_change: 0, answer_type: 'TEXT', status: 'open' });
        s.followupEdits.push({ followup_id: null, action: 'add', question, reviewer_id: stamp.reviewerId, at: stamp.at });
        return;
      }
      const q = s.followups.find((x) => x.followup_id === c.followupId);
      if (!q) throw new ReviewError(`No follow-up ${c.followupId}.`);
      const pipelineId = q.followup_id.startsWith('added-') ? null : q.followup_id;
      if (c.action === 'drop') {
        if (q.status === 'dropped') return;
        q.status = 'dropped';
        s.followupEdits.push({ followup_id: pipelineId, action: 'drop', question: q.question, reviewer_id: stamp.reviewerId, at: stamp.at });
        return;
      }
      const question = c.question.trim();
      if (!question) throw new ReviewError('Write the question.');
      if (question === q.question) return;
      q.question = question;
      s.followupEdits.push({ followup_id: pipelineId, action: pipelineId ? 'reword' : 'add', question, reviewer_id: stamp.reviewerId, at: stamp.at });
      return;
    }
    case 'confirmTerm': {
      const f = factOf(s, a.factId);
      if (!f.termResolution) throw new ReviewError(`${f.label} has no term to confirm.`);
      const name = a.canonicalName.trim();
      if (!name) throw new ReviewError('Name what the term means.');
      s.termConfirmations.push({ term: f.termResolution.term, canonical_name: name, product_type: a.productType, provider: a.provider.trim(), confirmed: a.confirmed, reviewer_id: stamp.reviewerId, at: stamp.at });
      const note = `${f.termResolution.term} confirmed as ${name}`;
      const slot = slotOfFact(f);
      const shown = f.quantity ? toFnaValue(slot, f.quantity.dist) : null;
      if (slot.categories?.includes(a.productType) && shown !== a.productType) {
        emit(s, [editOp(f, hypothesesOf(slot, { kind: 'point', value: a.productType }, f.quantity?.basis), stamp, note)]);
        settleFlags(s, f.factId, 'edited', stamp, note);
      } else {
        if (!isDecided(f)) emit(s, [acceptOp(f, stamp, note)]);
        settleFlags(s, f.factId, a.confirmed ? 'accepted' : 'edited', stamp, note);
      }
      s.facts[f.factId].termResolution = { ...f.termResolution, status: a.confirmed ? 'confirmed' : 'corrected', confirmed_name: name };
      return;
    }
  }
};

/** Apply one reviewer action and return the new session. Throws ReviewError when the action cannot apply. */
export const applyAction = (session: ReviewSession, action: ReviewAction, stamp: Stamp): ReviewSession => {
  if (session.submittedAt) throw new ReviewError('This review has been submitted.');
  return produce(session, (s) => run(s, action, stamp));
};

/* ------------------------------------------------------------------ */
/* Editing the sheet directly                                          */
/* ------------------------------------------------------------------ */

/**
 * The adviser changed the sheet. The change shows at once; fields the pipeline extracts become
 * reviewer ops when the field loses focus (commitPending): an edit of the fact there, or a new fact
 * where there was none (a recall miss). Rows added in the sheet take the id the pipeline will give
 * them; rows removed retract their identifying fact. Other fields (contact details, consents, the
 * advice record) simply change in the document.
 */
export const editDocument = (session: ReviewSession, next: FnaDocument, stamp: Stamp): ReviewSession => {
  if (session.submittedAt) throw new ReviewError('This review has been submitted.');
  const changes = diffDocuments(session.working, next);
  return produce(session, (s) => {
    s.working = structuredClone(next) as Draft<FnaDocument>;
    const w = s.working as FnaDocument;
    for (const row of changes.addedRows) {
      const pp = parsePath(row);
      if (!pp.key || !isListContainer(pp.container) || entityOfRow(s as ReviewSession, pp.container, pp.key)) continue;
      const r = newReviewerEntity(s, pp.container);
      const obj = getPath(w, row) as Record<string, unknown> | undefined;
      if (obj) obj.id = r.rowId;
      if (pp.container === 'assets') for (const l of w.liabilities) if (l.linkedAssetId === pp.key) l.linkedAssetId = r.rowId;
      s.pending.push(`${pp.container}[${r.rowId}].${IDENTITY_FIELD[pp.container]}`);
    }
    for (const row of changes.removedRows) {
      const pp = parsePath(row);
      if (!pp.key || !isListContainer(pp.container)) continue;
      s.pending = s.pending.filter((p) => !p.startsWith(`${row}.`));
      const live = rowFacts(s as ReviewSession, pp.container, pp.key).filter((f) => !f.moved && f.review.status !== 'rejected');
      const reviewerRow = s.reviewerEntities.some((r) => r.container === pp.container && r.rowId === pp.key);
      const identity = s.pathIndex[`${row}.${IDENTITY_FIELD[pp.container]}`];
      if (!reviewerRow && identity) {
        emit(s, [removeRowOp({ factId: identity }, stamp, 'row removed in the sheet')]);
        for (const f of live) settleFlags(s, f.factId, 'edited', stamp, 'row removed');
      } else if (live.length) {
        emit(s, live.map((f) => rejectOp(f, 'other', stamp, 'row removed in the sheet')));
        for (const f of live) settleFlags(s, f.factId, 'edited', stamp, 'row removed');
      }
      removeRow(w, pp.container, pp.key);
    }
    for (const path of changes.fields) {
      if (s.pending.includes(path)) continue;
      if (slotAtPath(path, (c, k) => entityOfRow(s as ReviewSession, c, k))) s.pending.push(path);
    }
  });
};

const reviewerRowIdentity = (s: ReviewSession, path: string): boolean => {
  const pp = parsePath(path);
  return !!pp.key && isListContainer(pp.container) && pp.field === IDENTITY_FIELD[pp.container] && s.reviewerEntities.some((r) => r.container === pp.container && r.rowId === pp.key);
};

/** Turn the sheet's pending edits on extracted fields into reviewer ops. */
export const commitPending = (session: ReviewSession, stamp: Stamp): ReviewSession => {
  if (!session.pending.length) return session;
  return produce(session, (s) => {
    const paths = [...s.pending];
    s.pending = [];
    for (const path of paths) {
      const snapshot = s as ReviewSession;
      const w = s.working as FnaDocument;
      const pp = parsePath(path);
      if (pp.key !== null && isListContainer(pp.container) && !hasRow(w, pp.container, pp.key)) continue;
      const at = slotAtPath(path, (c, k) => entityOfRow(snapshot, c, k));
      if (!at) continue;
      const amounts = pp.container === 'expenses.items';
      const raw = getPath(w, path) ?? (amounts ? 0 : undefined);
      const expected = expectedValue(snapshot, path) ?? (amounts ? 0 : undefined);
      const id = s.pathIndex[path];
      const f = id ? plain(s.facts[id] as ReviewFact) : undefined;
      const anchor = !f && reviewerRowIdentity(snapshot, path);
      if (!anchor && sameJson(raw, expected)) continue;
      if (raw === undefined || raw === null) {
        // A cleared answer: the sheet keeps showing what the review holds there.
        setPath(w, path, expected);
        continue;
      }
      const dist = toQuantity(at.slot, raw).dist;
      if (f && !f.moved) {
        emit(s, [editOp(f, hypothesesOf(at.slot, dist, f.quantity?.basis), stamp, 'edited in the sheet')]);
        settleFlags(s, f.factId, 'edited', stamp, 'edited in the sheet');
      } else {
        emit(
          s,
          addFactOps(
            {
              slotId: at.slot.slot_id,
              instance: at.instance,
              path,
              status: 'stated',
              hypotheses: hypothesesOf(at.slot, dist),
              evidence: [],
              attribution: 'client',
              interpretation: 'Entered by the adviser in the sheet.',
              entityRefs: refsFor(snapshot, at.slot, at.instance),
            },
            stamp,
            reviewerProvenance(s.bundle),
          ),
        );
      }
    }
  });
};

/* ------------------------------------------------------------------ */
/* Reading and finishing a session                                     */
/* ------------------------------------------------------------------ */

export const openFlags = (s: ReviewSession): BlockingFlag[] => s.flags.filter((f) => !f.cleared);

export const canSubmit = (s: ReviewSession): boolean => !s.submittedAt && openFlags(s).length === 0 && s.pending.length === 0;

export const factAt = (s: ReviewSession, path: string): ReviewFact | undefined => {
  const id = s.pathIndex[path];
  return id ? s.facts[id] : undefined;
};

/** The reviewer's result: ops and side records, as a file or for the API (§11.5). */
export const toResult = (s: ReviewSession, o: { submitted: boolean; at: string }): ReviewResult => ({
  format: REVIEW_RESULT_FORMAT,
  version: 1,
  call_id: s.callId,
  run_id: s.runId,
  base_profile_version: s.bundle.profile_version,
  reviewer_id: s.reviewerId,
  ops: s.ops,
  flag_clearances: s.flagClearances,
  followup_edits: s.followupEdits,
  term_confirmations: s.termConfirmations,
  submitted: o.submitted,
  submitted_at: o.submitted ? o.at : null,
  review_seconds: Math.round(s.activeSeconds),
});

/** Freeze the review: no more actions. The caller writes the working document to the client file. */
export const submit = (session: ReviewSession, at: string): ReviewSession => {
  if (session.submittedAt) throw new ReviewError('This review has been submitted.');
  if (session.pending.length) throw new ReviewError('Finish the field you are editing first.');
  if (openFlags(session).length) throw new ReviewError('Clear every blocking flag before submitting.');
  return produce(session, (s) => {
    s.submittedAt = at;
  });
};

/** Time spent reviewing, for the operations metric (§15.3). */
export const addActiveTime = (session: ReviewSession, seconds: number): ReviewSession =>
  produce(session, (s) => {
    s.activeSeconds += seconds;
  });

/**
 * The API answered a batch: everything up to `seq` has been seen by the pipeline. Ops it refused
 * are recorded (once each) so the adviser is told, and are not sent again: the pipeline's reducer
 * is deterministic, so the answer would be the same.
 */
export const markSent = (session: ReviewSession, seq: number, refused: { seq: number; reason: string }[] = [], at = new Date().toISOString()): ReviewSession =>
  produce(session, (s) => {
    s.sentSeq = Math.max(s.sentSeq, seq);
    for (const r of refused) if (!s.refused.some((x) => x.seq === r.seq)) s.refused.push({ seq: r.seq, reason: r.reason, at });
  });

/** Ops not yet accepted by the API. */
export const unsentOps = (s: ReviewSession): StateOp[] => s.ops.filter((o) => o.seq > s.sentSeq);

/** The facts the pipeline's refusals concern, with the reason, for the adviser. */
export const refusedFacts = (s: ReviewSession): { op: StateOp; fact: ReviewFact | undefined; reason: string }[] =>
  s.refused.flatMap((r) => {
    const op = s.ops.find((o) => o.seq === r.seq);
    return op ? [{ op, fact: s.facts[op.fact_id], reason: r.reason }] : [];
  });

/** The ids of the facts each follow-up is about, that are still in this review. */
export const followupFacts = (s: ReviewSession, q: FollowUp): ReviewFact[] => q.fact_ids.map((id) => s.facts[id]).filter((f): f is ReviewFact => !!f && !f.moved);
