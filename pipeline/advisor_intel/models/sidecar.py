"""The provenance sidecar that travels beside the FnaDocument (spec §3.6, §11.1, §17 default).

Schema v2 of the FnaDocument has no place for probabilities, evidence, status or change, so
``binding.sidecar(profile)`` carries them keyed by concrete FnaDocument path. A ``ReviewBundle``
holds it as a plain dict; these models describe exactly what ``sidecar()`` emits, so the JSON
Schema exported from them (``lib/review/schema/sidecar.json``) keeps the review overlay's
TypeScript types in step, and ``tests/test_review_contracts.py`` validates ``sidecar()`` output
against them. Unknown keys are refused, so a new key in ``sidecar()`` must be added here too.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .dists import Basis, Period, Quantity, Unit
from .facts import Attribution, ChangeKind, Evidence, FactSource, FactStatus, ReviewState
from .profile import BlockingFlag, FollowUp


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValueSummary(_Strict):
    """``binding.summarise_value``: the unit, then one of a point, P(true), category probabilities or p10/p50/p90."""

    unit: Unit
    period: Period
    basis: Basis
    kind: str  # the distribution kind (point, triangular, mixture …)
    point: bool | int | float | str | None = None
    p_true: float | None = None
    probs: dict[str, float] | None = None
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None


class SidecarHypothesis(_Strict):
    value: ValueSummary | None
    quantity: Quantity | None  # the full distribution
    text: str  # binding.describe_value: "60–65, most likely 62"
    weight: float
    rationale: str
    revises: bool


class SidecarField(_Strict):
    fact_id: str
    slot_id: str
    label: str
    group: str
    tier: Literal["A", "B", "C"]
    exported: bool  # False for forecast-only slots (events, income changes)
    status: FactStatus
    change: ChangeKind
    source: FactSource
    value: ValueSummary | None
    quantity: Quantity | None  # the merged distribution over hypotheses
    value_text: str
    prior_value: ValueSummary | None
    prior_text: str | None
    hypotheses: list[SidecarHypothesis]
    p_correct: float
    auto_accepted: bool
    review_priority: float
    why: str | None  # the one-line reason from VOI (§10): "moves success odds by ~9 points"
    speaker_attribution: Attribution
    said: str
    interpretation: str
    hedges: list[str]  # hedge terms as spoken
    evidence: list[Evidence]
    entity_refs: list[str]
    review: ReviewState
    derivation: str | None
    term_resolution: dict[str, Any] | None  # an unknown product or provider term and its candidates (§8.5)


class Sidecar(_Strict):
    format: Literal["advisor-intel-sidecar"] = "advisor-intel-sidecar"
    version: Literal[1] = 1
    call_id: str
    run_id: str
    profile_version: int
    last_seq: int  # the last op in the profile's log: reviewer ops are numbered after it
    schema_version: str
    anchor_date: date
    fields: dict[str, SidecarField]  # keyed by concrete FnaDocument path
    blocking_flags: list[BlockingFlag]
    followups: list[FollowUp]
