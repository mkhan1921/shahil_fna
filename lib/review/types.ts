/**
 * The review contracts shared with the Advisor Call Intelligence pipeline (spec §3, §10, §11.5, §12.4).
 *
 * Mirrors pipeline/advisor_intel/models/{dists,facts,profile,entities,transcript,forecast,runs,review,sidecar}.py
 * and the payload models in extract/reducer.py. The JSON Schemas in ./schema are exported from those
 * Pydantic models (pipeline/scripts/export_review_schemas.py); the tests validate fixtures and every
 * reviewer op against them, so a change on either side fails a test instead of drifting silently.
 */
import type { FnaDocument } from '../fna/types';

/* ------------------------------------------------------------------ */
/* Distributions (§3.3)                                                */
/* ------------------------------------------------------------------ */

export interface PointDist {
  kind: 'point';
  value: number | string | boolean;
}
export interface CategoricalDist {
  kind: 'categorical';
  probs: Record<string, number>;
}
export interface DiscreteDist {
  kind: 'discrete';
  support: number[];
  weights: number[];
}
export interface TriangularDist {
  kind: 'triangular';
  low: number;
  mode: number;
  high: number;
}
/** Stored as quantiles; the log-scale parameters are fitted when sampled. */
export interface LogNormalDist {
  kind: 'lognormal';
  median: number;
  p10: number;
  p90: number;
}
export interface NormalDist {
  kind: 'normal';
  mean: number;
  sd: number;
}
export interface BernoulliDist {
  kind: 'bernoulli';
  p: number;
}
export interface MixtureDist {
  kind: 'mixture';
  components: [number, Dist][];
}
export type Dist = PointDist | CategoricalDist | DiscreteDist | TriangularDist | LogNormalDist | NormalDist | BernoulliDist | MixtureDist;
export type DistKind = Dist['kind'];

/** "pct" holds a rate as a decimal; uncertain dates and calendar years are decimal years in "date". */
export type Unit = 'ZAR' | 'USD' | 'GBP' | 'EUR' | 'years' | 'months' | 'age' | 'pct' | 'count' | 'date' | 'none';
export type Period = 'once' | 'monthly' | 'annual' | 'none';
export type Basis = 'gross' | 'net' | 'unknown' | 'na';
export type RealOrNominal = 'real' | 'nominal' | 'na';

export interface Quantity {
  dist: Dist;
  unit: Unit;
  period: Period;
  basis: Basis;
  real_or_nominal: RealOrNominal;
}

/* ------------------------------------------------------------------ */
/* Facts (§3.4)                                                        */
/* ------------------------------------------------------------------ */

export type FactStatus = 'stated' | 'implied' | 'explicitly_unknown' | 'not_discussed' | 'contradicted';
export type FactSource = 'call' | 'prior_fna' | 'crm' | 'derived' | 'fna_default' | 'followup' | 'reviewer';
export type ChangeKind = 'new' | 'changed' | 'confirmed' | 'unchanged_not_discussed' | 'removed';
export type Attribution = 'client' | 'advisor_suggested' | 'advisor_confirmed_by_client' | 'mixed';
export type EvidenceRole = 'supports' | 'revises' | 'contradicts' | 'context';
export type ReviewStatus = 'pending' | 'accepted' | 'edited' | 'rejected' | 'auto_accepted';

export interface Evidence {
  segment_id: string;
  char_start: number;
  char_end: number;
  /** Exact 1-best text of the span. */
  quote: string;
  speaker_role: string;
  t_start: number;
  t_end: number;
  asr_mean_conf: number;
  role: EvidenceRole;
  aligner_score?: number | null;
}

export interface Hedge {
  term: string;
  strength: number;
  lexicon_key: string;
}

export interface Hypothesis {
  value: Quantity | null;
  weight: number;
  rationale: string;
  revises?: boolean;
}

export interface ReviewState {
  status: ReviewStatus;
  reviewer_id?: string | null;
  reviewed_at?: string | null;
  original_value?: Quantity | null;
  original_status?: FactStatus | null;
  note?: string | null;
  reason?: string | null;
}

export interface Provenance {
  run_id: string;
  trace_id?: string;
  extractor_model?: string;
  extractor_prompt_version?: string;
  calibrator_version?: string;
  lexicon_version?: string;
  schema_version?: string;
}

/** An unknown product or provider term and what the lookup found (§8.5). */
export interface TermCandidate {
  canonical_name: string;
  product_type?: string;
  provider?: string;
  source?: string;
  score?: number;
}
export interface TermResolution {
  term: string;
  status?: string;
  fallback?: string;
  candidates?: TermCandidate[];
  [key: string]: unknown;
}

/* ------------------------------------------------------------------ */
/* State ops and their payloads (§3.5, extract/reducer.py)             */
/* ------------------------------------------------------------------ */

export type OpKind = 'ADD' | 'UPDATE' | 'RETRACT' | 'CONFIRM' | 'MARK_UNKNOWN';
export type Actor = 'extractor' | 'verifier' | 'reviewer' | 'deriver' | 'loader';

export interface AddPayload {
  slot_id: string;
  instance_key?: string | null;
  fna_path: string;
  source?: FactSource;
  status?: FactStatus;
  hypotheses?: Hypothesis[];
  evidence?: Evidence[];
  hedges?: Hedge[];
  speaker_attribution?: Attribution;
  interpretation?: string;
  said?: string;
  entity_refs?: string[];
  normalization?: Record<string, unknown> | null;
  derivation?: string | null;
  term_resolution?: TermResolution | null;
  self_score?: number | null;
  field_retries?: number;
  reopened_by_coverage?: boolean;
  prior_value?: Quantity | null;
  provenance: Provenance;
}

export interface UpdatePayload {
  add_hypotheses?: Hypothesis[];
  weights?: number[] | null;
  /** A reviewer edit: the new value. */
  replace_hypotheses?: Hypothesis[] | null;
  evidence?: Evidence[];
  status?: FactStatus | null;
  speaker_attribution?: Attribution | null;
  instance_key?: string | null;
  fna_path?: string | null;
  entity_refs?: string[] | null;
  interpretation?: string | null;
  normalization?: Record<string, unknown> | null;
  review?: ReviewState | null;
  reason?: string | null;
}

export type RetractReason =
  | 'change_of_mind'
  | 'extraction_error'
  | 'entailment_contradicted'
  | 'wrong_person'
  | 'misheard'
  | 'advisor_said_it'
  | 'not_what_they_meant'
  | 'wrong_entity'
  | 'removed'
  | 'other';

export interface RetractPayload {
  reason?: RetractReason;
  hypothesis_index?: number | null;
  evidence?: Evidence[];
  review?: ReviewState | null;
  note?: string | null;
}

export interface ConfirmPayload {
  evidence?: Evidence[];
  review?: ReviewState | null;
}

export interface MarkUnknownPayload {
  reason?: string;
  evidence?: Evidence[];
  slot_id?: string | null;
  instance_key?: string | null;
  fna_path?: string | null;
  source?: FactSource;
  speaker_attribution?: Attribution;
  provenance?: Provenance | null;
  review?: ReviewState | null;
}

export interface OpPayloads {
  ADD: AddPayload;
  UPDATE: UpdatePayload;
  RETRACT: RetractPayload;
  CONFIRM: ConfirmPayload;
  MARK_UNKNOWN: MarkUnknownPayload;
}

export type StateOp = {
  [K in OpKind]: {
    op: K;
    fact_id: string;
    /** The segment that caused the op; null for reviewer ops without a transcript span. */
    segment_id: string | null;
    payload: OpPayloads[K];
    actor: Actor;
    /** Monotonic within the run: reviewer ops are numbered after the profile's last op. */
    seq: number;
  };
}[OpKind];

/* ------------------------------------------------------------------ */
/* Follow-ups and blocking flags (§10.3, §10.5)                        */
/* ------------------------------------------------------------------ */

export interface FollowUp {
  followup_id: string;
  fact_ids: string[];
  /** Plain-language, client-facing. */
  question: string;
  /** One line for the adviser: "moves success odds by ~9 points". */
  why: string;
  expected_pos_change: number;
  answer_type: string;
  status: 'open' | 'asked' | 'answered' | 'dropped';
}

export type FlagKind =
  | 'tier_a_missing'
  | 'tier_a_contradicted'
  | 'status_flip'
  | 'scale_ambiguous'
  | 'second_pass_disagrees'
  | 'hard_constraint'
  | 'unresolved_term'
  | 'role_review';

export type FlagResolution = 'accepted' | 'edited' | 'marked_unknown' | 'dismissed';

/** One rule, one fact: any open flag blocks review submission and export. */
export interface BlockingFlag {
  flag_id: string;
  kind: FlagKind;
  fact_id: string | null;
  message: string;
  cleared: boolean;
  cleared_by: string | null;
  resolution: FlagResolution | null;
}

/* ------------------------------------------------------------------ */
/* Entities and the transcript (§3.1, §3.2, §5)                        */
/* ------------------------------------------------------------------ */

export type EntityType = 'person' | 'account' | 'policy' | 'property' | 'business' | 'liability' | 'goal' | 'institution';

export interface Entity {
  entity_id: string;
  type: EntityType;
  canonical_name: string;
  aliases: string[];
  attributes: Record<string, unknown>;
  crm_match: [string, number][];
  first_mention: string | null;
  /** "dependants", "policies", … when the entity is an FnaDocument list row. */
  fna_list: string | null;
  fna_id: string | null;
}

export type SpeakerRole = 'advisor' | 'client_primary' | 'client_secondary' | 'other' | 'unknown';
export type SegmentFlag = 'self_correction' | 'interrupted' | 'crosstalk' | 'low_asr';

export interface Word {
  text: string;
  t_start: number;
  t_end: number;
  conf: number;
  alts: [string, number][];
  raw_spoken: string | null;
  numeric_candidates: number[];
}

export interface Segment {
  segment_id: string;
  speaker_label: string;
  speaker_role: SpeakerRole;
  role_conf: number;
  diarization_conf: number;
  t_start: number;
  t_end: number;
  words: Word[];
  text: string;
  rewritten_text: string | null;
  flags: SegmentFlag[];
}

export interface Reading {
  value: number | string;
  currency: string | null;
  period: Period;
  scale_source: string | null;
  lexicon_key: string | null;
  weight: number;
  is_rate: boolean;
}

/** An amount, age or product mention with its well-formed readings (§5.3, §5.6). */
export interface NumericSpan {
  span_id: string;
  segment_id: string;
  kind: 'amount' | 'age' | 'product';
  word_start: number;
  word_end: number;
  surface: string;
  readings: Reading[];
  provider_itn: string | null;
  accepted: boolean;
  mean_conf: number;
  second_pass_reading: Reading | null;
  second_pass_agreement: boolean | null;
  second_pass_method: string | null;
}

/** Call-level quality metrics and the acceptance checks of §5.5. */
export interface QualityReport {
  mean_word_conf: number;
  low_asr_share: number;
  crosstalk_share: number;
  n_speakers: number;
  role_conf_min: number;
  scale_ambiguous_spans: number;
  banners: string[];
  role_review_needed: boolean;
  failed: boolean;
  cap_p_correct: boolean;
}

export interface Transcript {
  call_id: string;
  anchor_date: string;
  language: string;
  segments: Segment[];
  asr_provider: string;
  asr_model_version: string;
  asr_alternatives: boolean;
  numeric_spans: NumericSpan[];
  quality: QualityReport | null;
  channel_layout: 'stereo_split' | 'mono';
}

/* ------------------------------------------------------------------ */
/* Forecast and run (§12.4, §13)                                       */
/* ------------------------------------------------------------------ */

export interface ForecastResult {
  run_id: string;
  profile_version: string;
  config_version: string;
  cma_version: string;
  n_paths: number;
  seed: number;
  /** P(essential spend met to the planning age). */
  pos: number;
  pos_ci: [number, number];
  goal_funding: Record<string, number>;
  ages: number[];
  net_worth_bands: Record<string, number[]>;
  shortfall: Record<string, number | null>;
  liquidity_breach_prob: number;
  retirement_income_bands: Record<string, number[]>;
  /** {"market", "client_content", "extraction", "interaction"}: shares of the forecast's variance. */
  variance_attribution: Record<string, number>;
  market_attribution: Record<string, number>;
  top_drivers: [string, number][];
  /** The FNA's own deterministic retirement projection, for side-by-side display (§12.8). */
  deterministic: Record<string, number | null>;
  /** need → {p10, p50, p90} of the shortfall over materialised draws (§12.7). */
  need_bands: Record<string, Record<string, number>>;
}

export type RunState =
  | 'STARTED'
  | 'INGESTED'
  | 'DISCOURSE_DONE'
  | 'AGENT_RUNNING'
  | 'AGENT_DONE'
  | 'AGENT_PARTIAL'
  | 'AWAITING_REVIEW'
  | 'REVIEWED'
  | 'COMPLETE'
  | 'FAILED'
  | 'MANUAL';

export interface VersionPins {
  schema_version: string;
  lexicon_version: string;
  grammar_version: string;
  calibrator_version: string;
  asr_provider: string;
  asr_model_version: string;
  diarization_model: string;
  frontier_model: string;
  reasoning_model: string;
  verifier_model: string;
  small_model: string;
  embedding_model: string;
  prompt_versions: Record<string, string>;
  forecast_config_version: string;
  cma_version: string;
  fna_tables_version: string;
}

/* ------------------------------------------------------------------ */
/* The sidecar (binding.sidecar(), models/sidecar.py)                  */
/* ------------------------------------------------------------------ */

export interface ValueSummary {
  unit: Unit;
  period: Period;
  basis: Basis;
  kind: string;
  point?: number | string | boolean | null;
  p_true?: number | null;
  probs?: Record<string, number> | null;
  p10?: number | null;
  p50?: number | null;
  p90?: number | null;
}

export interface SidecarHypothesis {
  value: ValueSummary | null;
  quantity: Quantity | null;
  text: string;
  weight: number;
  rationale: string;
  revises: boolean;
}

export type Tier = 'A' | 'B' | 'C';

export interface SidecarField {
  fact_id: string;
  slot_id: string;
  label: string;
  group: string;
  tier: Tier;
  /** False for forecast-only slots (events, income changes): they have no place in the FnaDocument. */
  exported: boolean;
  status: FactStatus;
  change: ChangeKind;
  source: FactSource;
  value: ValueSummary | null;
  quantity: Quantity | null;
  value_text: string;
  prior_value: ValueSummary | null;
  prior_text: string | null;
  hypotheses: SidecarHypothesis[];
  p_correct: number;
  auto_accepted: boolean;
  review_priority: number;
  why: string | null;
  speaker_attribution: Attribution;
  said: string;
  interpretation: string;
  hedges: string[];
  evidence: Evidence[];
  entity_refs: string[];
  review: ReviewState;
  derivation: string | null;
  term_resolution: TermResolution | null;
}

export interface Sidecar {
  format: 'advisor-intel-sidecar';
  version: 1;
  call_id: string;
  run_id: string;
  profile_version: number;
  /** The profile's last op: reviewer ops are numbered after it. */
  last_seq: number;
  schema_version: string;
  anchor_date: string;
  /** Keyed by concrete FnaDocument path: "income.client.grossMonthly", "dependants[<row id>].tertiaryStartAge". */
  fields: Record<string, SidecarField>;
  blocking_flags: BlockingFlag[];
  followups: FollowUp[];
}

/* ------------------------------------------------------------------ */
/* Review exchange (§11.5, models/review.py)                           */
/* ------------------------------------------------------------------ */

/** Everything the review overlay needs for one call: GET /calls/{call_id}/review, or a review package file. */
export interface ReviewBundle {
  call_id: string;
  run_id: string;
  profile_version: number;
  state: RunState;
  /** The profile materialised at its median (§3.6): what the sheet shows before any review. */
  median_document: FnaDocument;
  /** The client's FNA before the call. */
  prior_document: FnaDocument | null;
  sidecar: Sidecar;
  transcript: Transcript;
  audio_url: string | null;
  entities: Record<string, Entity>;
  followups: FollowUp[];
  blocking_flags: BlockingFlag[];
  preview_forecast: ForecastResult | null;
  deterministic_analysis: Record<string, unknown> | null;
  quality: QualityReport | null;
  /** This review also shows every auto-accepted fact under "Needs you" (§11.3). */
  spot_check: boolean;
  versions: VersionPins;
  attribution_text: string | null;
}

export const REVIEW_PACKAGE_FORMAT = 'advisor-intel-review';
export const REVIEW_RESULT_FORMAT = 'advisor-intel-review-result';
export const SIDECAR_FORMAT = 'advisor-intel-sidecar';

export interface ReviewPackage {
  format: typeof REVIEW_PACKAGE_FORMAT;
  version: 1;
  bundle: ReviewBundle;
}

export interface FlagClearance {
  flag_id: string;
  resolution: FlagResolution;
  reviewer_id: string;
  at: string;
  note: string;
}

export interface FollowupEdit {
  /** null for a question the adviser added. */
  followup_id: string | null;
  action: 'reword' | 'drop' | 'add';
  question: string;
  reviewer_id: string;
  at: string;
}

export interface TermConfirmation {
  term: string;
  canonical_name: string;
  product_type: string;
  provider: string;
  /** False: the adviser corrected the proposed resolution. */
  confirmed: boolean;
  reviewer_id: string;
  at: string;
}

/** What the app sends back: reviewer ops and side records, by API or as a file. */
export interface ReviewResult {
  format: typeof REVIEW_RESULT_FORMAT;
  version: 1;
  call_id: string;
  run_id: string;
  base_profile_version: number;
  reviewer_id: string;
  ops: StateOp[];
  flag_clearances: FlagClearance[];
  followup_edits: FollowupEdit[];
  term_confirmations: TermConfirmation[];
  submitted: boolean;
  submitted_at: string | null;
  /** Time spent reviewing, for the operations metric (§15.3). */
  review_seconds: number | null;
}
