"""State operations, follow-ups, blocking flags and the profile (spec §3.5, §10.3, §10.5)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel

from .entities import Entity
from .facts import Fact

OpKind = Literal["ADD", "UPDATE", "RETRACT", "CONFIRM", "MARK_UNKNOWN"]
Actor = Literal["extractor", "verifier", "reviewer", "deriver", "loader"]


class StateOp(BaseModel):
    op: OpKind
    fact_id: str
    segment_id: str | None  # the segment that caused the op (None for loader/deriver ops)
    payload: dict[str, Any]  # new hypotheses, or reason
    actor: Actor
    seq: int  # monotonic within run


class FollowUp(BaseModel):
    followup_id: str
    fact_ids: list[str]
    question: str  # plain-language, client-facing
    why: str  # one line for the advisor: "moves success odds by ~9 points"
    expected_pos_change: float  # I_content or I_extract
    answer_type: str  # matches the slot's intermediate form type
    status: Literal["open", "asked", "answered", "dropped"] = "open"


FlagKind = Literal[
    "tier_a_missing",
    "tier_a_contradicted",
    "status_flip",
    "scale_ambiguous",
    "second_pass_disagrees",
    "hard_constraint",
    "unresolved_term",
    "role_review",
]


class BlockingFlag(BaseModel):
    """One rule, one fact: any open flag blocks review submission and export (§10.3)."""

    flag_id: str
    kind: FlagKind
    fact_id: str | None
    message: str
    cleared: bool = False
    cleared_by: str | None = None
    resolution: Literal["accepted", "edited", "marked_unknown", "dismissed"] | None = None


class Profile(BaseModel):
    call_id: str
    run_id: str
    schema_version: str
    anchor_date: date
    entities: dict[str, Entity] = {}
    facts: dict[str, Fact] = {}  # keyed by fact_id
    ops_log: list["StateOp"] = []  # full, replayable
    open_questions: list[FollowUp] = []
    blocking_flags: list[BlockingFlag] = []
    version: int = 0  # bumped on every applied batch; frozen at review submission

    def next_seq(self) -> int:
        return (self.ops_log[-1].seq + 1) if self.ops_log else 1

    def validate_invariants(self, replay: bool = True) -> list[str]:
        """The invariants of §3.5. Returns the violations (empty when the profile is sound)."""
        from ..extract.reducer import check_profile  # local import: the reducer depends on these models

        return check_profile(self, replay=replay)

    def validate(self, replay: bool = True) -> None:  # type: ignore[override]
        problems = self.validate_invariants(replay=replay)
        if problems:
            raise ValueError("profile invariants violated:\n- " + "\n- ".join(problems))

    def open_flags(self) -> list[BlockingFlag]:
        return [f for f in self.blocking_flags if not f.cleared]
