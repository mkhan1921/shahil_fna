"""Calibration labels from advisor review (spec §9.3, §11.2).

``correct ∈ {0, 1}`` per reviewed fact, comparing the fact as it stood before review with the fact after
the reviewer's ops (``actor="reviewer"``, applied by the same reducer):

- accepted unchanged → 1 (one CONFIRM per fact also covers "accept all in group");
- edited, and the edited value's median falls inside the original p10–p90 (numeric), or has the same
  argmax (categorical, boolean), or the same text → 1; edited outside that → 0;
- rejected → 0, with the reason code (wrong person, misheard, advisor said it, not what they meant, other);
- status changed (e.g. a ``not_discussed`` slot marked stated, or a value marked unknown) → 0 for the original;
- re-attributed to another entity or speaker → 0 with reason ``wrong_entity``;
- facts the reviewer added are not labels: they are logged as recall misses.

So ``p_correct`` answers "would the advisor accept this fact's distribution as covering the truth?". Each
label carries the pre-review score, features and the facts needed by the conformal threshold (§9.5).
Removing a prior FNA row the call never discussed is not an extraction outcome and gives no label.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from rapidfuzz import fuzz

from .. import stats
from ..extract.reducer import apply_state_ops
from ..models.dists import Quantity
from ..models.facts import Fact
from ..models.profile import Profile, StateOp
from ..models.review import ReviewResult
from ..schema.slots import Catalog, load_catalog

SEED_SOURCES = frozenset({"prior_fna", "crm", "fna_default"})
Action = Literal["accepted", "edited", "status_changed", "rejected", "reattributed"]


class Label(BaseModel):
    call_id: str
    run_id: str
    fact_id: str
    slot_id: str
    slot_group: str
    family: str
    tier: str
    correct: int  # 0 or 1
    action: Action
    reason: str
    p_raw: float
    p_correct: float
    auto_accepted: bool
    eligible: bool  # passed every auto-accept condition except the threshold and the tier rule (§9.5)
    features: dict[str, float | None] = {}
    reviewer_id: str | None = None
    reviewed_at: datetime | None = None
    spot_check: bool = False
    calibrator_version: str = ""


class RecallMiss(BaseModel):
    """A fact the reviewer added: the pipeline missed it (§11.2 'Add fact')."""

    call_id: str
    run_id: str
    fact_id: str
    slot_id: str
    slot_group: str
    family: str
    tier: str
    reviewer_id: str | None = None
    segment_id: str | None = None


class LabelBatch(BaseModel):
    labels: list[Label] = []
    recall_misses: list[RecallMiss] = []
    skipped: list[str] = []  # "fact_id: why" for reviewed facts that give no label


# --------------------------------------------------------------------------- value comparison


def within_original(original: Quantity, edited: Quantity) -> bool:
    """§9.3: the edited value's median inside the original p10–p90, or the same argmax / text."""
    a, b = original.dist, edited.dist
    try:
        if stats.is_boolean(a) or stats.is_boolean(b):
            return (stats.p_true(a) >= 0.5) == (stats.p_true(b) >= 0.5)
        if stats.is_categorical(a) or stats.is_categorical(b):
            la, lb = stats.mode_label(a).strip().lower(), stats.mode_label(b).strip().lower()
            return la == lb or fuzz.ratio(la, lb) >= 90
        m = stats.median(b)
        if original.period != edited.period and {original.period, edited.period} == {"monthly", "annual"}:
            m = m * 12 if edited.period == "monthly" else m / 12
        lo, hi = stats.quantile(a, 0.1), stats.quantile(a, 0.9)
    except (TypeError, ValueError):
        return original == edited
    tol = 1e-9 * max(1.0, abs(lo), abs(hi))
    return math.isfinite(m) and lo - tol <= m <= hi + tol


def eligible_for_auto_accept(fact: Fact) -> bool:
    """Every §9.5 auto-accept condition except ``p_correct > τ`` and the tier rule."""
    if fact.status != "stated":
        return False
    if fact.verification.get("constraint") == "fail" or fact.verification.get("contradicted"):
        return False
    return not any(e.role == "contradicts" for e in fact.evidence)


# --------------------------------------------------------------------------- building labels


def _label(
    before: Profile,
    fact: Fact,
    catalog: Catalog,
    correct: int,
    action: Action,
    reason: str,
    op: StateOp | None,
    after: Fact | None,
    spot_check: bool,
) -> Label:
    slot = catalog.slot(fact.slot_id)
    review = after.review if after is not None else fact.review
    return Label(
        call_id=before.call_id,
        run_id=before.run_id,
        fact_id=fact.fact_id,
        slot_id=fact.slot_id,
        slot_group=slot.group,
        family=catalog.family_of(fact.slot_id),
        tier=slot.sensitivity_tier,
        correct=correct,
        action=action,
        reason=reason,
        p_raw=fact.p_raw,
        p_correct=fact.p_correct,
        auto_accepted=fact.auto_accepted,
        eligible=eligible_for_auto_accept(fact),
        features=dict(fact.features),
        reviewer_id=review.reviewer_id,
        reviewed_at=review.reviewed_at,
        spot_check=spot_check,
        calibrator_version=fact.provenance.calibrator_version,
    )


def build_labels(
    before: Profile,
    ops: list[StateOp],
    *,
    catalog: Catalog | None = None,
    after: Profile | None = None,
    spot_check: bool = False,
) -> LabelBatch:
    """Labels for every fact the reviewer acted on, and recall misses for facts the reviewer added."""
    catalog = catalog or load_catalog(before.schema_version)
    if after is None:
        after = apply_state_ops(before, list(ops)).profile
    reviewer_ops = sorted((op for op in ops if op.actor == "reviewer"), key=lambda o: o.seq)
    by_fact: dict[str, list[StateOp]] = {}
    for op in reviewer_ops:
        by_fact.setdefault(op.fact_id, []).append(op)
    batch = LabelBatch()

    # Re-attribution: a wrong_entity RETRACT, then the same slot added under the right entity.
    reattributed_slots: list[str] = []
    for op in reviewer_ops:
        if op.op == "RETRACT" and op.payload.get("reason") == "wrong_entity" and op.fact_id in before.facts:
            reattributed_slots.append(before.facts[op.fact_id].slot_id)

    for fid, fops in by_fact.items():
        orig = before.facts.get(fid)
        new = after.facts.get(fid)
        if orig is None:
            add = next((o for o in fops if o.op == "ADD"), None)
            if add is None:
                batch.skipped.append(f"{fid}: no such fact before review")
                continue
            slot_id = add.payload.get("slot_id") or fid.split("#", 1)[0]
            if slot_id in reattributed_slots:
                reattributed_slots.remove(slot_id)  # the second half of a re-attribution, not a miss
                continue
            slot = catalog.slot(slot_id)
            batch.recall_misses.append(
                RecallMiss(
                    call_id=before.call_id,
                    run_id=before.run_id,
                    fact_id=fid,
                    slot_id=slot.slot_id,
                    slot_group=slot.group,
                    family=catalog.family_of(slot.slot_id),
                    tier=slot.sensitivity_tier,
                    reviewer_id=(add.payload.get("review") or {}).get("reviewer_id"),
                    segment_id=add.segment_id,
                )
            )
            continue
        last = fops[-1]
        if all(o.op == "ADD" for o in fops) and orig.slot_id in reattributed_slots:
            reattributed_slots.remove(orig.slot_id)  # a re-attributed value merged into an existing fact
            batch.skipped.append(f"{fid}: target of a re-attribution")
            continue
        retract = next((o for o in fops if o.op == "RETRACT" and o.payload.get("hypothesis_index") is None), None)
        moved = any(
            o.op == "UPDATE"
            and (
                o.payload.get("instance_key") not in (None, orig.instance_key)
                or o.payload.get("speaker_attribution") not in (None, orig.speaker_attribution)
            )
            for o in fops
        )
        if (retract is not None and retract.payload.get("reason") == "wrong_entity") or moved:
            batch.labels.append(_label(before, orig, catalog, 0, "reattributed", "wrong_entity", last, new, spot_check))
            continue
        if new is None:
            batch.labels.append(_label(before, orig, catalog, 0, "rejected", "removed", last, None, spot_check))
            continue
        if new.review.status == "rejected":
            reason = new.review.reason or "other"
            if reason == "removed" and orig.source in SEED_SOURCES and orig.status == "not_discussed":
                batch.skipped.append(f"{fid}: prior row removed, not discussed in the call")
                continue
            batch.labels.append(_label(before, orig, catalog, 0, "rejected", reason, last, new, spot_check))
            continue
        if new.status != orig.status:
            batch.labels.append(_label(before, orig, catalog, 0, "status_changed", f"{orig.status}->{new.status}", last, new, spot_check))
            continue
        if new.value != orig.value:
            if orig.value is None or new.value is None:
                batch.labels.append(_label(before, orig, catalog, 0, "edited", "edited_outside", last, new, spot_check))
                continue
            ok = within_original(orig.value, new.value)
            batch.labels.append(
                _label(before, orig, catalog, int(ok), "edited", "edited_within" if ok else "edited_outside", last, new, spot_check)
            )
            continue
        if new.review.status in ("accepted", "edited", "auto_accepted"):
            batch.labels.append(_label(before, orig, catalog, 1, "accepted", "accepted", last, new, spot_check))
            continue
        batch.skipped.append(f"{fid}: reviewer ops left the fact unchanged and unaccepted")
    return batch


def labels_from_review(before: Profile, result: ReviewResult, *, catalog: Catalog | None = None, spot_check: bool = False) -> LabelBatch:
    """Labels from a review submitted by API or by file (``ReviewResult``)."""
    return build_labels(before, list(result.ops), catalog=catalog, spot_check=spot_check)


def wrong_auto_accepts(labels: list[Label]) -> tuple[int, int]:
    """(auto-accepted facts reviewed, of which wrong) — the spot-check monitor's input (§11.3)."""
    auto = [lb for lb in labels if lb.auto_accepted]
    return len(auto), sum(1 for lb in auto if lb.correct == 0)


def summarise(batch: LabelBatch) -> dict[str, Any]:
    by_action: dict[str, int] = {}
    for lb in batch.labels:
        by_action[lb.action] = by_action.get(lb.action, 0) + 1
    return {
        "labels": len(batch.labels),
        "positive": sum(lb.correct for lb in batch.labels),
        "by_action": by_action,
        "recall_misses": len(batch.recall_misses),
        "skipped": len(batch.skipped),
    }
