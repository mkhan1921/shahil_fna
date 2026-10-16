"""Reviewer actions as state ops, review events and side records (spec §11.2).

Every reviewer action is a ``StateOp`` with ``actor="reviewer"`` applied by the same reducer as
extraction, and written to Postgres immediately, so a review is replayable. This module prepares
reviewer ops (numbering after the profile's last op, stamping the review state and provenance),
applies them, and turns each applied op, flag clearance, follow-up edit and term confirmation
into a ``ReviewEvent``: the source of calibration labels (§9.3) and of the nightly datasets.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from .extract.reducer import ReducerResult, RejectedOp, apply_state_ops
from .models.facts import Provenance
from .models.profile import Profile, StateOp
from .models.review import FlagClearance, FollowupEdit, TermConfirmation
from .storage.glossary import GlossaryEntry
from .storage.records import ReviewAction, ReviewEvent

_REVIEW_STATUS = {"CONFIRM": "accepted", "UPDATE": "edited", "RETRACT": "rejected", "MARK_UNKNOWN": "edited", "ADD": "edited"}


def classify(op: StateOp) -> ReviewAction:
    """The §11.2 action a reviewer op records."""
    if op.op == "CONFIRM":
        return "accept"
    if op.op == "ADD":
        return "add"
    if op.op == "MARK_UNKNOWN":
        return "mark_unknown"
    if op.op == "RETRACT":
        reason = op.payload.get("reason")
        return "reattribute" if reason == "wrong_entity" else "remove" if reason == "removed" else "reject"
    return "change_status" if op.payload.get("status") else "edit"


def prepare(profile: Profile, ops: list[StateOp], *, reviewer_id: str, at: datetime) -> tuple[list[StateOp], list[RejectedOp]]:
    """Number reviewer ops after the profile's last op and stamp review state and provenance.

    Ops not from a reviewer are refused: this path is for reviewer actions only (§11.2).
    """
    ready: list[StateOp] = []
    refused: list[RejectedOp] = []
    seq = profile.next_seq()
    for op in ops:
        if op.actor != "reviewer":
            refused.append(RejectedOp(op=op, reason=f"only reviewer ops are accepted here, not {op.actor}"))
            continue
        payload: dict[str, Any] = dict(op.payload)
        if op.op in ("ADD", "MARK_UNKNOWN"):
            if op.op == "ADD":
                payload["source"] = "reviewer"  # a reviewer's own value needs no transcript evidence
            if "provenance" not in payload:
                payload["provenance"] = Provenance(run_id=profile.run_id, schema_version=profile.schema_version).model_dump(mode="json")
        if op.op != "ADD":
            review = dict(payload.get("review") or {})
            review.setdefault("status", _REVIEW_STATUS[op.op])
            review.setdefault("reviewer_id", reviewer_id)
            review.setdefault("reviewed_at", at.isoformat())
            payload["review"] = review
        ready.append(op.model_copy(update={"payload": payload, "seq": seq}))
        seq += 1
    return ready, refused


def apply_reviewer_ops(profile: Profile, ops: list[StateOp], *, reviewer_id: str, at: datetime) -> tuple[ReducerResult, list[ReviewEvent]]:
    """Apply reviewer ops through the reducer and describe each applied op as a review event."""
    ready, refused = prepare(profile, ops, reviewer_id=reviewer_id, at=at)
    result = apply_state_ops(profile, ready)
    result = result.model_copy(update={"rejected": refused + list(result.rejected)})
    before = profile.facts
    after = result.profile.facts
    events = []
    for op in result.applied:
        when = op.payload.get("review", {}).get("reviewed_at")
        events.append(
            ReviewEvent(
                workflow_id=profile.run_id,
                fact_id=op.fact_id,
                action=classify(op),
                reason=op.payload.get("reason"),
                reviewer_id=op.payload.get("review", {}).get("reviewer_id") or reviewer_id,
                at=datetime.fromisoformat(when) if isinstance(when, str) else at,
                op_seq=op.seq,
                profile_version=result.profile.version,
                detail={
                    "op": op.model_dump(mode="json"),
                    "fact_before": before[op.fact_id].model_dump(mode="json") if op.fact_id in before else None,
                    "fact_after": after[op.fact_id].model_dump(mode="json") if op.fact_id in after else None,
                    "note": op.payload.get("note") or op.payload.get("review", {}).get("note"),
                },
            )
        )
    return result, events


def apply_flag_clearances(profile: Profile, clearances: list[FlagClearance]) -> tuple[Profile, list[ReviewEvent], list[str]]:
    """Clear blocking flags (§10.3). Returns the profile, the events and any unknown flag ids."""
    by_id = {c.flag_id: c for c in clearances}
    flags = []
    events: list[ReviewEvent] = []
    for f in profile.blocking_flags:
        c = by_id.pop(f.flag_id, None)
        if c is None or f.cleared:
            flags.append(f)
            continue
        flags.append(f.model_copy(update={"cleared": True, "cleared_by": c.reviewer_id, "resolution": c.resolution}))
        events.append(
            ReviewEvent(
                workflow_id=profile.run_id,
                fact_id=f.fact_id,
                action="clear_flag",
                reason=c.resolution,
                reviewer_id=c.reviewer_id,
                at=c.at,
                profile_version=profile.version,
                detail={"flag": f.model_dump(mode="json"), "note": c.note},
            )
        )
    return profile.model_copy(update={"blocking_flags": flags}), events, sorted(by_id)


def followup_edit_events(run_id: str, edits: list[FollowupEdit]) -> list[ReviewEvent]:
    """Edited follow-up questions, kept for prompt tuning (§11.2, §15.6)."""
    return [
        ReviewEvent(
            workflow_id=run_id,
            action="edit_followup",
            reason=e.action,
            reviewer_id=e.reviewer_id,
            at=e.at,
            detail={"edit": e.model_dump(mode="json")},
        )
        for e in edits
    ]


def term_confirmation_events(run_id: str, confirmations: list[TermConfirmation]) -> list[ReviewEvent]:
    return [
        ReviewEvent(
            workflow_id=run_id,
            action="confirm_term",
            reason="confirmed" if c.confirmed else "corrected",
            reviewer_id=c.reviewer_id,
            at=c.at,
            detail={"confirmation": c.model_dump(mode="json")},
        )
        for c in confirmations
    ]


def glossary_entry(c: TermConfirmation) -> GlossaryEntry:
    """A confirmed or corrected term resolution becomes a practice glossary entry (§8.5)."""
    return GlossaryEntry(
        term=c.term,
        canonical_name=c.canonical_name,
        product_type=c.product_type,
        provider=c.provider,
        source="adviser",
        confirmed_by=c.reviewer_id,
        confirmed_at=c.at,
    )


def change_summary(profile: Profile) -> dict[str, int]:
    """How many facts are new, changed, confirmed, removed or unchanged against the prior FNA (§3.6)."""
    return dict(sorted(Counter(f.change for f in profile.facts.values()).items()))
