"""Review priority (spec §10.3).

``priority(f) = w_c · I_content(f) + w_e · I_extract(f) + forced(f)`` with ``w_c = 1`` and ``w_e = 2`` (a wrong
fact is worse than a vague one, because nobody knows to look). ``forced(f) = 1.0`` for a fact that flips an
FNA need's status (§10.2), a hard constraint failure, a ``contradicted`` status, or an ``advisor_suggested``
attribution on a tier A slot. The review UI sorts by priority; an auto-accepted fact below 0.005 (half a
PoS point) is collapsed by default.

Each fact also gets a one-line ``why`` for the advisor ("moves success odds by ~9 points"), stored with the
VOI terms in ``fact.voi`` (``why_text``), which the sidecar shows in the field popover (§11.1).
"""

from __future__ import annotations

import math

from pydantic import BaseModel

from ..extract.reducer import annotate
from ..models.facts import Fact, Provenance
from ..models.profile import Profile
from ..schema.slots import Catalog, load_catalog
from .sensitivity import SensitivityRow, need_label

W_CONTENT = 1.0
W_EXTRACT = 2.0
FORCED = 1.0
COLLAPSE_BELOW = 0.005  # half a PoS point
REVIEW_TOP = 15  # the review list is sorted so the top 15 cover the decisions that matter (§10)


class PriorityItem(BaseModel):
    fact_id: str
    slot_id: str
    tier: str
    status: str
    p_correct: float
    i_content: float
    i_extract: float
    i_fna: float
    i_pos: float
    forced: float
    forced_reasons: list[str] = []
    priority: float
    collapsed: bool = False
    auto_accepted: bool = False
    virtual: bool = False  # an expected field the call left without a fact
    why: str = ""


def _points(x: float) -> str:
    return f"~{max(1, round(abs(x) * 100))} point" + ("" if max(1, round(abs(x) * 100)) == 1 else "s")


def _money(x: float) -> str:
    x = abs(x)
    if x >= 1_000_000:
        return f"R{x / 1_000_000:.1f}m"
    if x >= 10_000:
        return f"R{x / 1000:,.0f}k"
    return f"R{x:,.0f}"


def forced_reasons(fact: Fact, row: SensitivityRow | None, tier: str) -> list[str]:
    reasons = []
    if row is not None and row.status_flip and not row.virtual:
        first = row.flips[0] if row.flips else ""
        key, _, change = first.partition(" ")
        reasons.append(f"flips {need_label(key)} from {change.replace('→', ' to ')}" if change else "flips an FNA need's status")
    if fact.verification.get("constraint") == "fail":
        msgs = fact.verification.get("constraint_messages") or []
        reasons.append(f"fails a check: {msgs[0]}" if msgs else "fails a hard consistency check")
    if fact.status == "contradicted":
        reasons.append("the call holds two incompatible statements")
    if fact.speaker_attribution == "advisor_suggested" and tier == "A":
        reasons.append("the adviser proposed this and the client did not clearly agree")
    return reasons


def why_line(item: PriorityItem, row: SensitivityRow | None) -> str:
    """One line for the advisor: the forced reason first, then the size of the effect."""
    parts = list(item.forced_reasons[:1])
    if row is not None:
        if item.i_content >= item.i_extract and item.i_content > 0:
            if row.i_pos >= row.i_fna and row.i_pos > 0:
                parts.append(f"moves success odds by {_points(row.i_pos)}")
            elif row.top_need:
                need = need_label(row.top_need)
                share = "more than the whole need" if row.i_fna > 1.0 else f"~{row.i_fna:.0%} of the need"
                parts.append(f"moves {need} shortfall by {_money(row.top_need_change)} ({share})")
        elif item.i_extract > 0 and row.pos_base is not None and row.pos_prior is not None:
            parts.append(f"if misheard, success odds could be off by {_points(row.pos_base - row.pos_prior)}")
        if item.virtual:
            parts.append("not discussed in the call")
    if not parts:
        parts.append("little effect on the plan")
    return "; ".join(parts)


def prioritise(
    profile: Profile,
    rows: list[SensitivityRow],
    *,
    catalog: Catalog | None = None,
    w_content: float = W_CONTENT,
    w_extract: float = W_EXTRACT,
    collapse_below: float = COLLAPSE_BELOW,
) -> list[PriorityItem]:
    """One item per sensitivity row, sorted by priority (highest first)."""
    catalog = catalog or load_catalog(profile.schema_version)
    items = []
    for row in rows:
        fact = profile.facts.get(row.fact_id)
        tier = catalog.slot(row.slot_id).sensitivity_tier
        if fact is None:  # a missing expected field: only its content impact and the status-flip rule apply
            fact = Fact(
                fact_id=row.fact_id,
                slot_id=row.slot_id,
                fna_path="",
                status="not_discussed",
                p_correct=1.0,
                provenance=Provenance(run_id=profile.run_id),
            )
        reasons = forced_reasons(fact, row, tier)
        forced = FORCED if reasons else 0.0
        i_content = row.i_content if math.isfinite(row.i_content) else 0.0
        i_extract = row.i_extract if math.isfinite(row.i_extract) else 0.0
        prio = w_content * i_content + w_extract * i_extract + forced
        item = PriorityItem(
            fact_id=row.fact_id,
            slot_id=row.slot_id,
            tier=tier,
            status=fact.status,
            p_correct=fact.p_correct,
            i_content=i_content,
            i_extract=i_extract,
            i_fna=row.i_fna,
            i_pos=row.i_pos,
            forced=forced,
            forced_reasons=reasons,
            priority=prio,
            auto_accepted=fact.auto_accepted,
            virtual=row.virtual,
        )
        item.collapsed = item.auto_accepted and prio < collapse_below
        item.why = why_line(item, row)
        items.append(item)
    items.sort(key=lambda it: (-it.priority, it.fact_id))
    return items


def annotate_priorities(profile: Profile, items: list[PriorityItem]) -> Profile:
    """Write ``review_priority`` and the VOI terms (with ``why_text``) onto each fact (``reducer.annotate``)."""
    for it in items:
        if it.fact_id not in profile.facts:
            continue
        voi: dict[str, float | str] = {
            "I_content": it.i_content,
            "I_extract": it.i_extract,
            "I_fna": it.i_fna,
            "I_pos": it.i_pos,
            "forced": it.forced,
            "priority": it.priority,
            "collapsed": 1.0 if it.collapsed else 0.0,
            "why_text": it.why,
        }
        profile = annotate(profile, it.fact_id, review_priority=it.priority, voi=voi)
    return profile


def review_list(items: list[PriorityItem], top: int = REVIEW_TOP) -> list[str]:
    """The review rail's head: the highest-priority facts that are not collapsed."""
    return [it.fact_id for it in items if not it.collapsed and not it.virtual][:top]
