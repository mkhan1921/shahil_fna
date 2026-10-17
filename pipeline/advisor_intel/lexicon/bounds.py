"""Bounds check (spec §7.3 step 6): values outside ``SlotDef.bounds`` are dropped and logged.

A value whose median lies outside the slot's bounds (more than half its mass outside) is dropped:
it is almost always a scale or unit error, and the drop is logged and returned so the extraction
result can list it. A distribution that is plausible but has a tail beyond a bound (more than 1% of
its mass) is truncated to the bounds instead, so the forecast never samples an impossible value
(a negative age, a retirement age of 95), and the fact is flagged ``truncated_to_bounds``.
Categorical, boolean and text values, foreign-currency amounts (bounds are in rand) and slots
without bounds are not checked.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from .. import stats
from ..models.dists import Point, Quantity
from ..schema.slots import SlotDef
from .normalize import Normalized

log = logging.getLogger(__name__)

BoundsAction = Literal["keep", "truncated", "dropped", "skipped"]
DROP_ABOVE = 0.5  # share of mass outside the bounds that drops a value
TRUNCATE_ABOVE = 0.01  # share of mass outside the bounds that truncates it


class BoundsCheck(BaseModel):
    action: BoundsAction
    p_outside: float = 0.0
    low: float | None = None
    high: float | None = None
    reason: str = ""

    @property
    def keep(self) -> bool:
        return self.action != "dropped"


def check_bounds(q: Quantity | None, slot: SlotDef) -> BoundsCheck:
    """How much of a value's mass lies outside the slot's bounds, and what to do about it."""
    if q is None or slot.bounds is None:
        return BoundsCheck(action="skipped", reason="no value" if q is None else "slot has no bounds")
    lo, hi = float(slot.bounds[0]), float(slot.bounds[1])
    d = q.dist
    if stats.is_categorical(d) or stats.is_boolean(d):
        return BoundsCheck(action="skipped", low=lo, high=hi, reason="not numeric")
    if q.unit in ("USD", "GBP", "EUR"):
        return BoundsCheck(action="skipped", low=lo, high=hi, reason=f"bounds are in rand; value is in {q.unit}")
    eps = 1e-9 * max(1.0, abs(lo), abs(hi))
    p_out = stats.cdf(d, lo - eps) + (1.0 - stats.cdf(d, hi + eps))
    p_out = min(1.0, max(0.0, p_out))
    if p_out <= TRUNCATE_ABOVE:
        return BoundsCheck(action="keep", p_outside=p_out, low=lo, high=hi)
    med = stats.median(d)
    if p_out > DROP_ABOVE or not (lo - eps <= med <= hi + eps):
        return BoundsCheck(action="dropped", p_outside=p_out, low=lo, high=hi, reason=f"median {med:g} outside [{lo:g}, {hi:g}]")
    return BoundsCheck(action="truncated", p_outside=p_out, low=lo, high=hi, reason=f"{p_out:.1%} of the mass outside [{lo:g}, {hi:g}]")


def apply_bounds(n: Normalized, slot: SlotDef, *, fact_id: str | None = None) -> Normalized | None:
    """The normalised value after the bounds check: ``None`` when dropped (logged), truncated when it has a tail outside."""
    chk = check_bounds(n.quantity, slot)
    if chk.action == "dropped":
        log.warning("dropped out-of-bounds value for %s: %s", fact_id or slot.slot_id, chk.reason)
        return None
    if chk.action == "truncated" and n.quantity is not None:
        d = stats.truncate(n.quantity.dist, low=chk.low, high=chk.high)
        if isinstance(d, Point) and chk.low is not None and chk.high is not None:
            d = Point(value=min(chk.high, max(chk.low, stats.as_number(d.value))))
        out = n.model_copy(update={"quantity": n.quantity.model_copy(update={"dist": d})})
        return out.with_flag("truncated_to_bounds", bounds={"low": chk.low, "high": chk.high, "p_outside": round(chk.p_outside, 4)})
    return n


class BoundsResult(BaseModel):
    values: list[Normalized | None]  # aligned with the input; None where a value was dropped
    dropped: list[str] = []  # one log line per dropped value, for ExtractionResult.dropped
    rejected: list[Normalized] = []  # the dropped values themselves, flagged out_of_bounds, for the audit log


def filter_bounds(values: Sequence[Normalized | None], slot: SlotDef, *, fact_id: str | None = None) -> BoundsResult:
    """Apply the bounds check to every hypothesis of one fact (§7.3 step 6)."""
    out: list[Normalized | None] = []
    dropped: list[str] = []
    rejected: list[Normalized] = []
    for i, n in enumerate(values):
        if n is None:
            out.append(None)
            continue
        kept = apply_bounds(n, slot, fact_id=fact_id)
        if kept is None:
            chk = check_bounds(n.quantity, slot)
            dropped.append(f"{fact_id or slot.slot_id} hypothesis {i}: out of bounds ({chk.reason})")
            rejected.append(n.with_flag("out_of_bounds", bounds={"low": chk.low, "high": chk.high, "reason": chk.reason}))
        out.append(kept)
    return BoundsResult(values=out, dropped=dropped, rejected=rejected)


__all__ = ["BoundsCheck", "BoundsResult", "apply_bounds", "check_bounds", "filter_bounds"]
