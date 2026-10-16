"""Transcript layer (spec §3.1, §5)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

SpeakerRole = Literal["advisor", "client_primary", "client_secondary", "other", "unknown"]
SegmentFlag = Literal["self_correction", "interrupted", "crosstalk", "low_asr"]
SpanKind = Literal["amount", "age", "product"]
SpanFlag = Literal["low_asr", "crosstalk", "scale_ambiguous"]


class Word(BaseModel):
    text: str
    t_start: float  # seconds from call start
    t_end: float
    conf: float  # ASR word confidence 0..1
    alts: list[tuple[str, float]] = []  # n-best alternatives with scores
    raw_spoken: str | None = None  # e.g. "two fifty" before inverse text normalisation
    numeric_candidates: list[float] = []  # scale readings for numeric tokens (§5.3)


class Segment(BaseModel):
    segment_id: str  # "s0001"…, stable within a call
    speaker_label: str  # raw diarization label "spk_0"
    speaker_role: SpeakerRole = "unknown"
    role_conf: float = 0.0
    diarization_conf: float = 1.0
    t_start: float
    t_end: float
    words: list[Word]
    text: str  # 1-best text
    rewritten_text: str | None = None  # self-contained rewrite (Stage 2)
    flags: set[SegmentFlag] = set()

    @property
    def mean_conf(self) -> float:
        return sum(w.conf for w in self.words) / len(self.words) if self.words else 0.0


class Reading(BaseModel):
    """One well-formed reading of a spoken amount, age or product span (§5.3)."""

    value: float | str  # number for amounts and ages; canonical name for products
    currency: str | None = None  # ZAR, USD, GBP, EUR for amounts
    period: Literal["once", "monthly", "annual", "none"] = "none"
    scale_source: str | None = None  # the scale word or digit pattern that set the magnitude
    lexicon_key: str | None = None  # for age bands and product names
    weight: float  # normalised score (ASR × grammar)
    is_rate: bool = False  # value is the spoken percentage ("eleven and a half percent" → 11.5)
    provider: str | None = None  # product readings: the canonical provider ("Allan Gray")


class NumericSpan(BaseModel):
    """An amount, age or product mention found by the grammars, with its readings (§5.3, §5.6)."""

    span_id: str
    segment_id: str
    kind: SpanKind
    word_start: int  # index into Segment.words
    word_end: int  # exclusive
    surface: str
    readings: list[Reading] = []
    provider_itn: str | None = None  # the ASR provider's own inverse text normalisation, kept as a candidate
    accepted: bool = True  # False when no grammar path accepted the span
    mean_conf: float = 1.0
    flags: set[SpanFlag] = set()  # low_asr (no grammar path accepted), crosstalk over the span, scale_ambiguous
    second_pass_reading: Reading | None = None
    second_pass_agreement: bool | None = None
    second_pass_method: str | None = None

    @property
    def top_margin(self) -> float:
        """Score ratio between the top two readings (inf with one reading)."""
        ws = sorted((r.weight for r in self.readings), reverse=True)
        if len(ws) < 2 or ws[1] <= 0:
            return float("inf")
        return ws[0] / ws[1]


class QualityReport(BaseModel):
    """Call-level quality metrics and the acceptance checks of §5.5."""

    mean_word_conf: float
    low_asr_share: float  # share of speech time in low-ASR segments
    crosstalk_share: float
    n_speakers: int
    role_conf_min: float
    scale_ambiguous_spans: int = 0
    banners: list[str] = []  # shown in the review UI
    role_review_needed: bool = False
    failed: bool = False  # outside 2–4 speakers: route to manual
    cap_p_correct: bool = False  # mean conf or low-ASR checks failed


class Transcript(BaseModel):
    call_id: str
    anchor_date: date  # call date, used for all relative time
    language: str = "en-ZA"
    segments: list[Segment]
    asr_provider: str
    asr_model_version: str
    asr_alternatives: bool = True  # False when the provider gave no n-best or word alternatives
    numeric_spans: list[NumericSpan] = []
    quality: QualityReport | None = None
    channel_layout: Literal["stereo_split", "mono"] = "mono"

    def segment(self, segment_id: str) -> Segment:
        for s in self.segments:
            if s.segment_id == segment_id:
                return s
        raise KeyError(segment_id)

    def index(self) -> dict[str, Segment]:
        return {s.segment_id: s for s in self.segments}

    @property
    def duration(self) -> float:
        return max((s.t_end for s in self.segments), default=0.0)


class RoleAssignment(BaseModel):
    """Output of the role-assignment agent (§5.2 step 6)."""

    roles: dict[str, SpeakerRole] = Field(description="speaker label → role")
    confidence: dict[str, float] = Field(description="speaker label → confidence 0..1")
    rationale: str = ""
