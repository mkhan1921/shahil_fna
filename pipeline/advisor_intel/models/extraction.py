"""Planner and extraction contracts (spec §7.1, §7.2, §8.1).

These models are sent to providers as strict structured-output schemas (§4.4), so every
field is required: absence is a sentinel (``0`` for an unsaid month, ``""`` for an unsaid
word) or a ``form`` tag, never an optional or nullable field. The LLM returns the flat
intermediate form *as spoken*; deterministic code turns it into a distribution (§8.3).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from .entities import EntityType
from .facts import Attribution, FactStatus

Currency = Literal["ZAR", "USD", "GBP", "EUR"]
PeriodWord = Literal["once", "monthly", "annual", "none"]
BasisWord = Literal["gross", "net", "unknown", "na"]


class AmountExact(BaseModel):
    """'I take home forty-two thousand a month'."""

    type: Literal["AMOUNT_EXACT"] = "AMOUNT_EXACT"
    value: float
    currency: Currency
    period: PeriodWord
    basis: BasisWord


class AmountApprox(BaseModel):
    """'about two and a half million'."""

    type: Literal["AMOUNT_APPROX"] = "AMOUNT_APPROX"
    value: float
    approx_word: str
    currency: Currency
    period: PeriodWord
    basis: BasisWord


class AmountRange(BaseModel):
    """'somewhere between 30 and 40k'."""

    type: Literal["AMOUNT_RANGE"] = "AMOUNT_RANGE"
    low: float
    high: float
    currency: Currency
    period: PeriodWord
    basis: BasisWord


class AmountBound(BaseModel):
    """'at least a bar a month'."""

    type: Literal["AMOUNT_BOUND"] = "AMOUNT_BOUND"
    bound: float
    direction: Literal["at_least", "at_most"]
    currency: Currency
    period: PeriodWord
    basis: BasisWord


class AmountRelative(BaseModel):
    """'a bit less than now' — a multiplier on another slot's value."""

    type: Literal["AMOUNT_RELATIVE"] = "AMOUNT_RELATIVE"
    reference_slot: str
    relation_word: str


class AmountVague(BaseModel):
    """'a few hundred K'."""

    type: Literal["AMOUNT_VAGUE"] = "AMOUNT_VAGUE"
    vague_word: str
    currency: Currency
    period: PeriodWord
    basis: BasisWord


class DateAbs(BaseModel):
    """'in March 2029'. month and day are 0 when not said."""

    type: Literal["DATE_ABS"] = "DATE_ABS"
    year: int
    month: int
    day: int


class DurationFromAnchor(BaseModel):
    """'in a few years', 'in eight years'. ``form`` says which of value / vague_word was said."""

    type: Literal["DURATION_FROM_ANCHOR"] = "DURATION_FROM_ANCHOR"
    form: Literal["value", "vague"]
    value: float  # 0 when form is vague
    vague_word: str  # "" when form is value
    unit: Literal["years", "months"]
    anchor: str  # "call", or a reference slot id ("client.dateOfBirth")


class AgeForm(BaseModel):
    """'early sixties', 'between 60 and 65', '62'."""

    type: Literal["AGE"] = "AGE"
    form: Literal["value", "range", "vague"]
    value: float  # 0 unless form is value
    low: float  # 0 unless form is range
    high: float  # 0 unless form is range
    vague_word: str  # "" unless form is vague


class RateForm(BaseModel):
    """'eleven and a half percent', 'growing a bit'. value is a decimal (0.115)."""

    type: Literal["RATE"] = "RATE"
    form: Literal["value", "vague"]
    value: float  # 0 when form is vague
    vague_word: str  # "" when form is value
    real_or_nominal: Literal["real", "nominal", "na"]


class ProbabilityEvent(BaseModel):
    """'she might go overseas'."""

    type: Literal["PROBABILITY_EVENT"] = "PROBABILITY_EVENT"
    event: str
    likelihood_word: str


class CategoryForm(BaseModel):
    """'I'm fairly conservative'. label must be one of the slot's categories."""

    type: Literal["CATEGORY"] = "CATEGORY"
    label: str
    certainty_word: str  # "" when none


class BooleanForm(BaseModel):
    """'we do have wills, I think'."""

    type: Literal["BOOLEAN"] = "BOOLEAN"
    value: bool
    certainty_word: str  # "" when none


class TextForm(BaseModel):
    """Free text slots (occupation, objectives in the client's words)."""

    type: Literal["TEXT"] = "TEXT"
    text: str


IntermediateForm = Annotated[
    Union[
        AmountExact,
        AmountApprox,
        AmountRange,
        AmountBound,
        AmountRelative,
        AmountVague,
        DateAbs,
        DurationFromAnchor,
        AgeForm,
        RateForm,
        ProbabilityEvent,
        CategoryForm,
        BooleanForm,
        TextForm,
    ],
    Field(discriminator="type"),
]

FORM_TYPES: dict[str, type[BaseModel]] = {
    "AMOUNT_EXACT": AmountExact,
    "AMOUNT_APPROX": AmountApprox,
    "AMOUNT_RANGE": AmountRange,
    "AMOUNT_BOUND": AmountBound,
    "AMOUNT_RELATIVE": AmountRelative,
    "AMOUNT_VAGUE": AmountVague,
    "DATE_ABS": DateAbs,
    "DURATION_FROM_ANCHOR": DurationFromAnchor,
    "AGE": AgeForm,
    "RATE": RateForm,
    "PROBABILITY_EVENT": ProbabilityEvent,
    "CATEGORY": CategoryForm,
    "BOOLEAN": BooleanForm,
    "TEXT": TextForm,
}


class RawHypothesis(BaseModel):
    intermediate: IntermediateForm
    weight: float = Field(description="client-given weight 0..1, or 0 when the client gave none (code splits equally)")
    rationale: str = Field(description="one line for the reviewer")
    revises: bool = Field(description="true when this value comes from a later statement that changes an earlier one")


# --------------------------------------------------------------------------- planner (§7.1)


class ExtractionTask(BaseModel):
    task_id: str
    slot_group: str  # "income", "dependants", ...
    slot_ids: list[str]
    instance_keys: list[str | None] = []  # entities for per_entity slots
    segment_ids: list[str] = []  # retrieved evidence window
    depends_on: list[str] = []  # task_ids that must finish first
    priority: int = 1  # tier A slots first (lower runs first)
    status: Literal["todo", "running", "done", "failed", "skipped"] = "todo"
    skip_reason: str | None = None
    attempts: int = 0


class Plan(BaseModel):
    tasks: list[ExtractionTask]
    rationale: str


# --------------------------------------------------------------------------- extraction stages (§7.2)


class ObjectRef(BaseModel):
    entity_id: str  # registry id, or "new:<tmp>" for a new object
    entity_type: EntityType
    name: str  # short canonical name for a new object ("Thandi", "Discovery life policy")
    segment_ids: list[str]


class FieldStatus(BaseModel):
    fna_path: str
    status: FactStatus
    segment_ids: list[str]  # empty when not_discussed


class FieldValue(BaseModel):
    fna_path: str
    hypotheses: list[RawHypothesis]  # intermediate form as spoken, never a distribution
    hedges: list[str]
    speaker_attribution: Attribution
    said: str  # the speaker's words, verbatim and short
    interpretation: str  # one line: what the model takes them to mean, and why
    self_score: float  # feature only, never used as p_correct


class ExtractionResult(BaseModel):
    task_id: str
    ops_applied: int = 0
    new_fact_ids: list[str] = []
    gaps: list[str] = []  # tier A slots still not_discussed
    warnings: list[str] = []
    dropped: list[str] = []  # extractions without a located source, or out of bounds
    usage: dict[str, int] = {}
