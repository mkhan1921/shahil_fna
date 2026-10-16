"""Review contracts shared by the API, the file exchange and the FNA app's review overlay (spec §11).

``ReviewBundle`` is what ``GET /calls/{call_id}/review`` returns and what a ``review package`` file
carries when the adviser works by file exchange (§11.5, §17 default for M3). Reviewer actions come
back as ``StateOp``s with ``actor="reviewer"``, applied by the same reducer as extraction, so a
review is replayable. How each action of §11.2 maps to an op:

=====================  ==========================================================================
Action                 Op and payload (see ``extract/reducer.py`` payload models)
=====================  ==========================================================================
Accept                 ``CONFIRM`` ``{"review": {"status": "accepted", "reviewer_id", "reviewed_at"}}``
Edit value             ``UPDATE`` ``{"replace_hypotheses": [Hypothesis], "review": {"status": "edited", …}}``
                       (point → ``point``; range → ``triangular`` low/mode/high; options with weights →
                       ``discrete`` for numbers or ``categorical`` for labels)
Change status          ``UPDATE`` ``{"status": "stated" | "implied" | "not_discussed", "replace_hypotheses": […],
                       "review": {"status": "edited", …}}``; to explicitly unknown: ``MARK_UNKNOWN`` with ``review``
Reject                 ``RETRACT`` ``{"reason": "wrong_person" | "misheard" | "advisor_said_it" |
                       "not_what_they_meant" | "other", "note", "review": {…}}``
Remove a prior row     ``RETRACT`` ``{"reason": "removed", "review": {…}}`` on the row's identity fact
Re-attribute           ``RETRACT`` ``{"reason": "wrong_entity"}`` on the old fact, then ``ADD`` the same
                       hypotheses and evidence under the right instance with ``source="reviewer"``
Add fact               ``ADD`` with ``source="reviewer"``, optional evidence span (logged as a recall miss)
Accept all in group    one ``CONFIRM`` per fact
Clear flag             the accept / edit / mark-unknown op above, plus ``FlagClearance``
Edit follow-up         ``FollowupEdit`` (not a fact op; stored for prompt tuning)
Confirm term           ``TermConfirmation`` (writes the practice glossary, §8.5)
=====================  ==========================================================================
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .entities import Entity
from .fna import FnaDocument
from .forecast import ForecastResult
from .profile import BlockingFlag, FollowUp, StateOp
from .runs import RunState, VersionPins
from .transcript import QualityReport, Transcript


class ReviewBundle(BaseModel):
    """Everything the review overlay needs for one call (§11.5 GET /calls/{call_id}/review)."""

    call_id: str
    run_id: str
    profile_version: int
    state: RunState
    median_document: FnaDocument  # the profile materialised at its median (§3.6)
    prior_document: FnaDocument | None = None  # the client's FNA before the call
    sidecar: dict[str, Any]  # binding.sidecar(): facts keyed by concrete fna_path
    transcript: Transcript
    audio_url: str | None = None
    entities: dict[str, Entity] = {}
    followups: list[FollowUp] = []
    blocking_flags: list[BlockingFlag] = []
    preview_forecast: ForecastResult | None = None
    deterministic_analysis: dict[str, Any] | None = None  # the FNA engine's analyse() of the median document
    quality: QualityReport | None = None
    spot_check: bool = False  # this review also shows every auto-accepted fact under "Needs you" (§11.3)
    versions: VersionPins = Field(default_factory=VersionPins)
    attribution_text: str | None = None  # "Of the uncertainty in this plan, about X% is markets, …" (§12.5)


class FlagClearance(BaseModel):
    flag_id: str
    resolution: Literal["accepted", "edited", "marked_unknown", "dismissed"]
    reviewer_id: str
    at: datetime
    note: str = ""


class FollowupEdit(BaseModel):
    followup_id: str | None  # None for a question the adviser added
    action: Literal["reword", "drop", "add"]
    question: str = ""
    reviewer_id: str
    at: datetime


class TermConfirmation(BaseModel):
    term: str
    canonical_name: str
    product_type: str = ""
    provider: str = ""
    confirmed: bool = True  # False: the adviser corrected the proposed resolution
    reviewer_id: str
    at: datetime


class ReviewPackage(BaseModel):
    """A review handed to the adviser as a file (file exchange, §11.5)."""

    format: Literal["advisor-intel-review"] = "advisor-intel-review"
    version: Literal[1] = 1
    bundle: ReviewBundle


class ReviewResult(BaseModel):
    """What the app sends back: reviewer ops and side records, by API or as a file."""

    format: Literal["advisor-intel-review-result"] = "advisor-intel-review-result"
    version: Literal[1] = 1
    call_id: str
    run_id: str
    base_profile_version: int
    reviewer_id: str
    ops: list[StateOp] = []
    flag_clearances: list[FlagClearance] = []
    followup_edits: list[FollowupEdit] = []
    term_confirmations: list[TermConfirmation] = []
    submitted: bool = False
    submitted_at: datetime | None = None
    review_seconds: float | None = None  # time spent reviewing, for the operations metric (§15.3)
