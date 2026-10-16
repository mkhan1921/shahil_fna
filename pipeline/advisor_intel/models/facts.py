"""Facts, evidence and review state (spec §3.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from .dists import Quantity

FactStatus = Literal["stated", "implied", "explicitly_unknown", "not_discussed", "contradicted"]
FactSource = Literal["call", "prior_fna", "crm", "derived", "fna_default", "followup", "reviewer"]
ChangeKind = Literal["new", "changed", "confirmed", "unchanged_not_discussed", "removed"]
Attribution = Literal["client", "advisor_suggested", "advisor_confirmed_by_client", "mixed"]
EvidenceRole = Literal["supports", "revises", "contradicts", "context"]
ReviewStatus = Literal["pending", "accepted", "edited", "rejected", "auto_accepted"]


class Evidence(BaseModel):
    segment_id: str
    char_start: int
    char_end: int
    quote: str  # exact 1-best text of the span
    speaker_role: str
    t_start: float
    t_end: float
    asr_mean_conf: float
    role: EvidenceRole
    aligner_score: float | None = None


class Hedge(BaseModel):
    term: str  # "thinking of", "maybe"
    strength: float  # 0 (certain) .. 1 (pure speculation), from lexicon
    lexicon_key: str


class Hypothesis(BaseModel):
    value: Quantity | None
    weight: float  # within-slot weight, sums to 1 across hypotheses
    rationale: str  # one line, for the reviewer
    revises: bool = False  # this hypothesis came from a later, revising statement


class ReviewState(BaseModel):
    status: ReviewStatus = "pending"
    reviewer_id: str | None = None
    reviewed_at: datetime | None = None
    original_value: Quantity | None = None  # kept when edited
    original_status: FactStatus | None = None  # kept when the reviewer changed the status
    note: str | None = None
    reason: str | None = None  # reject / re-attribute reason code


class Provenance(BaseModel):
    run_id: str  # Temporal workflow id
    trace_id: str = ""  # OpenTelemetry trace id; opens the run in Braintrust
    extractor_model: str = ""
    extractor_prompt_version: str = ""
    calibrator_version: str = ""
    lexicon_version: str = ""
    schema_version: str = ""


class Fact(BaseModel):
    fact_id: str  # f"{slot_id}#{instance}" e.g. "dependants[].tertiaryStartAge#person.daughter_1"
    slot_id: str
    instance_key: str | None = None  # entity the fact is about, for list slots
    fna_path: str  # resolved FnaDocument path, e.g. "dependants[3f2a…].tertiaryStartAge"
    source: FactSource = "call"
    prior_value: Quantity | None = None  # value in the client's existing FnaDocument, if any
    change: ChangeKind = "new"
    status: FactStatus
    hypotheses: list[Hypothesis] = []  # ≥1 unless status is not_discussed / explicitly_unknown
    value: Quantity | None = None  # merged distribution over hypotheses (Mixture if >1); recomputed, never hand-set
    p_correct: float = 0.5  # calibrated extraction confidence
    p_raw: float = 0.5  # pre-calibration score, kept for retraining
    hedges: list[Hedge] = []
    interpretation: str = ""  # the model's reading of the evidence, kept apart from the verbatim spans
    said: str = ""  # the speaker's words as the extractor quoted them (replaced by the aligned span)
    speaker_attribution: Attribution = "client"
    evidence: list[Evidence] = []
    entity_refs: list[str] = []
    normalization: dict[str, Any] | None = None  # intermediate form + lexicon version (§8)
    verification: dict[str, Any] = {}  # entailment, consistency, constraint checks (§9)
    features: dict[str, float | None] = {}  # calibration feature vector (§9.2), kept for retraining
    derivation: str | None = None  # name of the derivation rule for implied facts (§7.6)
    term_resolution: dict[str, Any] | None = None  # unknown product or provider term (§8.5)
    self_score: float | None = None  # extractor's own confidence: a feature, never p_correct
    field_retries: int = 0
    reopened_by_coverage: bool = False
    review_priority: float = 0.0  # VOI score (§10)
    voi: dict[str, float | str] = {}  # I_content, I_extract, I_fna, forced, and the advisor's "why_text" line (§10.3)
    auto_accepted: bool = False  # passed conformal filter
    review: ReviewState = ReviewState()
    provenance: Provenance
