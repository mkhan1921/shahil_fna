"""Gold annotations made in the FNA app's annotate mode (spec §15.1–15.2).

Annotators fill the same fact structure as the system, in the sheet, without seeing system output:
a status per slot, the value as a point, a range or weighted options, who said it, and the transcript
spans it rests on. For a vague statement the annotator gives the p10 / p50 / p90 they believe the
client meant, and adjudication averages two annotators' quantiles. The app exports one file per
annotator per call (``format: "advisor-intel-gold-annotation"``); these models read it, and the
JSON Schema exported from them (``lib/review/schema/gold_annotation.json``) checks the app's output.

Facts use the system's conventions, so scorers can join them to extracted facts directly:
``fact_id`` is ``f"{slot_id}#{instance}"`` and ``fna_path`` is the concrete FnaDocument path.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Union

import numpy as np
from pydantic import BaseModel, Field, model_validator

from ..models.dists import Period, Unit
from ..models.facts import Attribution, EvidenceRole, FactStatus

Scalar = bool | int | float | str


class GoldPoint(BaseModel):
    kind: Literal["point"] = "point"
    value: Scalar


class GoldRange(BaseModel):
    """A range or a vague amount: the bounds as said (if any) and the annotator's quantiles."""

    kind: Literal["range"] = "range"
    low: float | None = None  # "between 60 and 65": the bounds the speaker gave
    high: float | None = None
    p10: float
    p50: float
    p90: float

    @model_validator(mode="after")
    def _ordered(self) -> "GoldRange":
        if not (self.p10 <= self.p50 <= self.p90):
            raise ValueError(f"quantiles must be ordered, got {self.p10}, {self.p50}, {self.p90}")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("low must not exceed high")
        return self


class GoldOption(BaseModel):
    value: Scalar
    weight: float = Field(ge=0)


class GoldOptions(BaseModel):
    """Several values the client gave ('60 or 65'), labels ('fairly conservative'), or an event's yes / no."""

    kind: Literal["options"] = "options"
    options: list[GoldOption] = Field(min_length=1)

    @model_validator(mode="after")
    def _weights(self) -> "GoldOptions":
        total = sum(o.weight for o in self.options)
        if abs(total - 1) > 1e-4:
            raise ValueError(f"option weights must sum to 1, got {total}")
        return self


GoldValue = Annotated[Union[GoldPoint, GoldRange, GoldOptions], Field(discriminator="kind")]


class GoldQuantity(BaseModel):
    value: GoldValue
    unit: Unit
    period: Period = "none"

    def quantiles(self) -> tuple[float, float, float] | None:
        """(p10, p50, p90) of a numeric value; None for labels, yes / no and text."""
        v = self.value
        if isinstance(v, GoldRange):
            return (v.p10, v.p50, v.p90)
        if isinstance(v, GoldPoint):
            if isinstance(v.value, (int, float)) and not isinstance(v.value, bool):
                x = float(v.value)
                return (x, x, x)
            return None
        values = [o.value for o in v.options]
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in values):
            return None
        order = np.argsort([float(x) for x in values])  # type: ignore[arg-type]
        xs = [float(values[i]) for i in order]  # type: ignore[arg-type]
        cum = np.cumsum([v.options[i].weight for i in order])
        return tuple(xs[int(np.searchsorted(cum, q - 1e-12))] for q in (0.1, 0.5, 0.9))  # type: ignore[return-value]


class GoldSpan(BaseModel):
    segment_id: str
    char_start: int
    char_end: int
    quote: str
    speaker_role: str
    t_start: float
    t_end: float
    role: EvidenceRole = "supports"


class GoldFact(BaseModel):
    fact_id: str
    slot_id: str
    instance_key: str | None = None
    fna_path: str
    entity_label: str = ""  # names an object the prior FNA does not have: "new group disability cover"
    status: FactStatus
    value: GoldQuantity | None = None  # None when not discussed or explicitly unknown
    speaker_attribution: Attribution = "client"
    spans: list[GoldSpan] = []
    note: str = ""

    @model_validator(mode="after")
    def _status_rules(self) -> "GoldFact":
        if self.fact_id.split("#", 1)[0] != self.slot_id:
            raise ValueError(f"fact id {self.fact_id} does not match slot {self.slot_id}")
        if self.status in ("stated", "implied", "contradicted"):
            if self.value is None:
                raise ValueError(f"{self.fact_id}: a {self.status} fact needs a value")
            if not self.spans:
                raise ValueError(f"{self.fact_id}: a {self.status} fact needs at least one transcript span")
        if self.status in ("not_discussed", "explicitly_unknown") and self.value is not None:
            raise ValueError(f"{self.fact_id}: a {self.status} fact has no value")
        return self


class TranscriptRef(BaseModel):
    """The transcript the spans point into: the ASR output or the human verbatim transcript (§15.1)."""

    source: Literal["asr", "human"] = "asr"
    asr_provider: str = ""
    asr_model_version: str = ""


class GoldAnnotation(BaseModel):
    format: Literal["advisor-intel-gold-annotation"] = "advisor-intel-gold-annotation"
    version: Literal[1] = 1
    call_id: str
    annotator_id: str
    role: Literal["annotator", "adjudicator"] = "annotator"
    schema_version: str = "profile_v1"
    anchor_date: date
    transcript: TranscriptRef = Field(default_factory=TranscriptRef)
    prior_document_id: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    facts: list[GoldFact] = []

    @model_validator(mode="after")
    def _unique(self) -> "GoldAnnotation":
        ids = [f.fact_id for f in self.facts]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"facts annotated twice: {dupes}")
        return self
