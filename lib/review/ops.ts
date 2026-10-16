/**
 * Reviewer actions as state ops (spec §11.2), following the table in
 * pipeline/advisor_intel/models/review.py. Every action is a StateOp with actor "reviewer", stamped
 * with the reviewer and the time, so the review is replayable through the same reducer as
 * extraction:
 *
 *   Accept               CONFIRM {review: accepted}
 *   Edit value           UPDATE {replace_hypotheses, review: edited}         (status → stated if it was not)
 *   Change status        UPDATE {status, replace_hypotheses, review: edited}; to unknown: MARK_UNKNOWN
 *   Reject               RETRACT {reason, note, review: rejected}
 *   Remove a prior row   RETRACT {reason: removed} on the row's identity fact
 *   Re-attribute         RETRACT {reason: wrong_entity} + ADD the same hypotheses and evidence under
 *                        the right instance (source reviewer); a speaker fix is an UPDATE
 *   Add fact             ADD (source reviewer, optional evidence span) + CONFIRM: the reviewer's own value
 *   Accept all in group  one CONFIRM per fact
 *
 * Builders return ops without seq numbers; the session numbers them after the profile's last op.
 */
import type {
  Attribution,
  Evidence,
  FactStatus,
  Hypothesis,
  OpKind,
  OpPayloads,
  Provenance,
  RetractReason,
  ReviewBundle,
  ReviewState,
  ReviewStatus,
  StateOp,
} from './types';

export type OpDraft = { [K in OpKind]: Omit<Extract<StateOp, { op: K }>, 'seq'> }[OpKind];

export interface Stamp {
  reviewerId: string;
  /** ISO time of the action. */
  at: string;
}

/** The reasons a reviewer can give for rejecting a fact (§11.2), as the reducer's RETRACT reasons. */
export const REJECT_REASONS = {
  wrong_person: 'Wrong person',
  misheard: 'Misheard',
  advisor_said_it: 'The adviser said it',
  not_what_they_meant: 'Not what they meant',
  other: 'Other',
} as const satisfies Partial<Record<RetractReason, string>>;
export type RejectReason = keyof typeof REJECT_REASONS;

/** The minimal facts a builder needs about the fact it acts on. */
export interface OpTarget {
  factId: string;
  slotId: string;
  status: FactStatus;
  hypotheses: Hypothesis[];
  evidence: Evidence[];
  attribution: Attribution;
  interpretation: string;
  said: string;
}

const op = <K extends OpKind>(kind: K, factId: string, payload: OpPayloads[K], segmentId: string | null = null) =>
  ({ op: kind, fact_id: factId, segment_id: segmentId, payload, actor: 'reviewer' }) as unknown as OpDraft;

export const reviewState = (status: ReviewStatus, s: Stamp, extra: Partial<ReviewState> = {}): ReviewState => ({
  status,
  reviewer_id: s.reviewerId,
  reviewed_at: s.at,
  ...extra,
});

/** Provenance for facts the reviewer adds: the run they belong to, with its version pins. */
export const reviewerProvenance = (b: Pick<ReviewBundle, 'run_id' | 'versions' | 'sidecar'>): Provenance => ({
  run_id: b.run_id,
  trace_id: '',
  extractor_model: '',
  extractor_prompt_version: '',
  calibrator_version: b.versions.calibrator_version,
  lexicon_version: b.versions.lexicon_version,
  schema_version: b.sidecar.schema_version,
});

export const reviewerHypothesis = (value: Hypothesis['value'], rationale = 'reviewer edit'): Hypothesis => ({ value, weight: 1, rationale, revises: false });

export const acceptOp = (t: Pick<OpTarget, 'factId'>, s: Stamp, note?: string): OpDraft => op('CONFIRM', t.factId, { evidence: [], review: reviewState('accepted', s, note ? { note } : {}) });

/** Edit value: the reviewer's distribution replaces the hypotheses; a fact not yet stated becomes stated. */
export const editOp = (t: Pick<OpTarget, 'factId' | 'status'>, hypotheses: Hypothesis[], s: Stamp, note?: string): OpDraft =>
  op('UPDATE', t.factId, {
    replace_hypotheses: hypotheses,
    ...(t.status === 'stated' || t.status === 'implied' ? {} : { status: 'stated' as FactStatus }),
    review: reviewState('edited', s, note ? { note } : {}),
  });

/** Change status: to stated, implied or not discussed with an UPDATE; to explicitly unknown with MARK_UNKNOWN. */
export const statusOp = (t: Pick<OpTarget, 'factId'>, status: Exclude<FactStatus, 'contradicted'>, hypotheses: Hypothesis[] | null, s: Stamp, note?: string): OpDraft => {
  if (status === 'explicitly_unknown') return op('MARK_UNKNOWN', t.factId, { reason: note || 'the reviewer marked it unknown', evidence: [], review: reviewState('edited', s, note ? { note } : {}) });
  if ((status === 'stated' || status === 'implied') && !hypotheses?.length) throw new RangeError(`a ${status} fact needs a value`);
  return op('UPDATE', t.factId, { status, replace_hypotheses: status === 'not_discussed' ? [] : hypotheses, review: reviewState('edited', s, note ? { note } : {}) });
};

export const rejectOp = (t: Pick<OpTarget, 'factId'>, reason: RejectReason, s: Stamp, note?: string): OpDraft =>
  op('RETRACT', t.factId, { reason, note: note || null, evidence: [], review: reviewState('rejected', s) });

/** Remove a row the prior FNA had (or the call added): RETRACT "removed" on the row's identity fact. */
export const removeRowOp = (t: Pick<OpTarget, 'factId'>, s: Stamp, note?: string): OpDraft =>
  op('RETRACT', t.factId, { reason: 'removed', note: note || null, evidence: [], review: reviewState('rejected', s) });

export interface ReattributeTarget {
  instance: string;
  /** The concrete path under the right instance. */
  path: string;
  /** Registry entities the fact is now about (empty for a row the reviewer adds). */
  entityRefs: string[];
}

/** Re-attribute to another person or row: RETRACT wrong_entity, then ADD the same hypotheses and evidence there. */
export const reattributeOps = (t: OpTarget, to: ReattributeTarget, s: Stamp, provenance: Provenance, note?: string): OpDraft[] => [
  op('RETRACT', t.factId, { reason: 'wrong_entity', note: note || null, evidence: [], review: reviewState('rejected', s) }),
  op(
    'ADD',
    `${t.slotId}#${to.instance}`,
    {
      slot_id: t.slotId,
      instance_key: to.instance,
      fna_path: to.path,
      source: 'reviewer',
      status: t.status,
      hypotheses: t.hypotheses,
      evidence: t.evidence,
      speaker_attribution: t.attribution,
      interpretation: t.interpretation,
      said: t.said,
      entity_refs: to.entityRefs,
      provenance,
    },
    t.evidence.find((e) => e.role === 'supports' || e.role === 'revises')?.segment_id ?? null,
  ),
];

/** Re-attribute the speaker: the value stays, who said it changes. */
export const speakerOp = (t: Pick<OpTarget, 'factId'>, attribution: Attribution, s: Stamp): OpDraft =>
  op('UPDATE', t.factId, { speaker_attribution: attribution, review: reviewState('edited', s, { reason: 'wrong_speaker' }) });

export interface NewFact {
  slotId: string;
  instance: string | null;
  path: string;
  status: 'stated' | 'implied';
  hypotheses: Hypothesis[];
  evidence: Evidence[];
  attribution: Attribution;
  interpretation: string;
  entityRefs: string[];
}

/** Add a fact the pipeline missed (a recall miss): ADD with source reviewer, then CONFIRM it as the reviewer's own. */
export const addFactOps = (f: NewFact, s: Stamp, provenance: Provenance): OpDraft[] => {
  const factId = f.instance ? `${f.slotId}#${f.instance}` : f.slotId;
  return [
    op(
      'ADD',
      factId,
      {
        slot_id: f.slotId,
        instance_key: f.instance,
        fna_path: f.path,
        source: 'reviewer',
        status: f.status,
        hypotheses: f.hypotheses,
        evidence: f.evidence,
        speaker_attribution: f.attribution,
        interpretation: f.interpretation,
        said: f.evidence[0]?.quote ?? '',
        entity_refs: f.entityRefs,
        provenance,
      },
      f.evidence[0]?.segment_id ?? null,
    ),
    acceptOp({ factId }, s),
  ];
};

/** Number a batch after the last seq: the reducer refuses a seq that is not after its log's last. */
export const numberOps = (drafts: OpDraft[], lastSeq: number): StateOp[] => drafts.map((d, i) => ({ ...d, seq: lastSeq + 1 + i }) as StateOp);
