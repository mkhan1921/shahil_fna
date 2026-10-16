"""Deciding the action — ``should_clarify(f)`` (spec §10.4, §4.3).

| Condition                                                        | Action                                   |
| ---------------------------------------------------------------- | ---------------------------------------- |
| I_extract dominates (I_extract > I_content) and p_correct < 0.7 | review item: the advisor can answer from memory or the recording |
| I_content dominates and status is stated or implied             | client follow-up: only the client can narrow it |
| status not_discussed or explicitly_unknown, and tier A          | client follow-up                          |
| priority below threshold                                         | no action; the fact stays as it is       |

Two refinements keep the table consistent with §4.3 and §10.3: a tier A field that was not discussed but
has a prior FNA value uses that value, so it becomes a follow-up only when it matters (priority at or
above the threshold); and a fact forced into review by a hard constraint failure, a contradiction or an
advisor suggestion is a review item, because the advisor has to settle it before the client is asked
anything. Anything else above the threshold is a review item.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..models.profile import Profile
from .priority import COLLAPSE_BELOW, PriorityItem

Action = Literal["followup", "review", "none"]
P_REVIEW = 0.7
UNKNOWN = ("not_discussed", "explicitly_unknown")


class ClarifyDecision(BaseModel):
    fact_id: str
    action: Action
    rule: str
    expected_pos_change: float  # I_content for a follow-up, I_extract for a review item (§10.5)


def should_clarify(item: PriorityItem, *, has_prior: bool, threshold: float = COLLAPSE_BELOW) -> ClarifyDecision:
    """The action for one fact (see the module docstring for the table and its order)."""

    def d(action: Action, rule: str, change: float) -> ClarifyDecision:
        return ClarifyDecision(fact_id=item.fact_id, action=action, rule=rule, expected_pos_change=change)

    if item.status in UNKNOWN and item.tier == "A":
        if item.status == "explicitly_unknown" or item.virtual or not has_prior:
            return d("followup", f"tier A {item.status.replace('_', ' ')}", item.i_content)
        if item.priority >= threshold:
            return d("followup", "tier A not discussed; the prior value matters", item.i_content)
    if item.priority < threshold:
        return d("none", "priority below threshold", 0.0)
    if any(not r.startswith("flips") for r in item.forced_reasons):
        return d("review", "forced: " + next(r for r in item.forced_reasons if not r.startswith("flips")), item.i_extract)
    if item.i_extract > item.i_content and item.p_correct < P_REVIEW:
        return d("review", "extraction risk dominates and p_correct < 0.7", item.i_extract)
    if item.i_content >= item.i_extract and item.status in ("stated", "implied"):
        return d("followup", "content spread dominates", item.i_content)
    return d("review", "above threshold", max(item.i_extract, item.i_content))


def clarify_all(profile: Profile, items: list[PriorityItem], *, threshold: float = COLLAPSE_BELOW) -> dict[str, ClarifyDecision]:
    """The action for every prioritised fact."""
    out = {}
    for it in items:
        f = profile.facts.get(it.fact_id)
        has_prior = f is not None and (f.prior_value is not None or f.value is not None)
        out[it.fact_id] = should_clarify(it, has_prior=has_prior, threshold=threshold)
    return out
