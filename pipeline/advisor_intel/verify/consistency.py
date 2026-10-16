"""Consistency probes (spec §9.1b).

The same slot is re-extracted with k = 3 paraphrased prompts (different question wording and segment
order, on the small model) through the injected ``reextract(fact, variant)`` callable, which the
extraction stage provides and which returns the probe's status and normalised value. Each probe is
compared with the main extraction:

- numeric: the probe's median lies inside the main fact's p10–p90;
- categorical (and boolean): the probe has the same argmax;
- status: the probe has the same status.

``consistency`` is the share of agreeing probes: on the value for a fact that has one, on the status
for a fact that does not (not discussed, explicitly unknown). It runs on tier A and B facts only, to cap
cost; tier C facts get NaN (``None``). A probe that fails is left out; if all fail the score is NaN.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel
from rapidfuzz import fuzz

from .. import stats
from ..models.dists import Quantity
from ..models.facts import Fact, FactStatus
from ..models.profile import Profile
from ..schema.slots import Catalog, SlotDef, load_catalog

log = logging.getLogger(__name__)

K_PROBES = 3
TEXT_MATCH = 85  # rapidfuzz token_set_ratio for free-text slots
VALUED = ("stated", "implied", "contradicted")


class ProbeResult(BaseModel):
    """One paraphrased re-extraction: its status and, when it found one, its normalised value."""

    status: FactStatus
    value: Quantity | None = None


Reextract = Callable[[Fact, int], Awaitable[ProbeResult]]
Kind = Literal["numeric", "categorical", "boolean", "text", "status"]


class ConsistencyResult(BaseModel):
    fact_id: str
    kind: Kind
    k: int
    answered: int = 0
    failures: int = 0  # probes that raised (left out of the score, counted so a dead small model is visible)
    consistency: float | None = None  # None is NaN: tier C, or no probe answered
    value_agreement: float | None = None
    status_agreement: float | None = None
    probes: list[dict[str, Any]] = []
    skipped: str | None = None
    error: str | None = None  # the last probe failure, when any

    def verification(self) -> dict[str, Any]:
        return {
            "consistency": self.consistency,
            "consistency_detail": {
                "kind": self.kind,
                "k": self.k,
                "answered": self.answered,
                "failures": self.failures,
                "value_agreement": self.value_agreement,
                "status_agreement": self.status_agreement,
                "probes": self.probes,
                "skipped": self.skipped,
                "error": self.error,
            },
        }


def value_kind(fact: Fact, slot: SlotDef) -> Kind:
    if fact.status not in VALUED or fact.value is None:
        return "status"
    if slot.value_type == "text":
        return "text"
    if slot.value_type in ("boolean", "probability") or stats.is_boolean(fact.value.dist):
        return "boolean"
    if slot.value_type in ("category", "count") or stats.is_categorical(fact.value.dist):
        return "categorical"
    return "numeric"


def _comparable(main: Quantity, probe: Quantity) -> float | None:
    """The probe's median in the main fact's unit (monthly ↔ annual amounts converted), or None."""
    try:
        m = stats.median(probe.dist)
    except (TypeError, ValueError):
        return None
    if main.unit != probe.unit and not (main.unit == "date" and probe.unit == "date"):
        return None
    if main.period != probe.period and {main.period, probe.period} == {"monthly", "annual"}:
        m = m * 12 if probe.period == "monthly" else m / 12
    return m


def value_agrees(kind: Kind, main: Quantity, probe: Quantity | None) -> bool:
    if probe is None:
        return False
    try:
        if kind == "numeric":
            m = _comparable(main, probe)
            if m is None or not math.isfinite(m):
                return False
            lo, hi = stats.quantile(main.dist, 0.1), stats.quantile(main.dist, 0.9)
            tol = 1e-9 * max(1.0, abs(lo), abs(hi))
            return lo - tol <= m <= hi + tol
        if kind == "boolean":
            return (stats.p_true(main.dist) >= 0.5) == (stats.p_true(probe.dist) >= 0.5)
        if kind == "categorical":
            return stats.mode_label(main.dist) == stats.mode_label(probe.dist)
        if kind == "text":
            a, b = stats.mode_label(main.dist), stats.mode_label(probe.dist)
            return fuzz.token_set_ratio(a.lower(), b.lower()) >= TEXT_MATCH
    except (TypeError, ValueError):
        return False
    return False


def _describe(q: Quantity | None) -> Any:
    if q is None:
        return None
    try:
        if stats.is_categorical(q.dist):
            return stats.mode_label(q.dist)
        if stats.is_boolean(q.dist):
            return round(stats.p_true(q.dist), 4)
        return round(stats.median(q.dist), 6)
    except (TypeError, ValueError):
        return None


def score_probes(fact: Fact, slot: SlotDef, probes: Sequence[ProbeResult], k: int = K_PROBES) -> ConsistencyResult:
    """Agreement of answered probes with the main extraction (pure; used by ``consistency_probe``)."""
    kind = value_kind(fact, slot)
    res = ConsistencyResult(fact_id=fact.fact_id, kind=kind, k=k, answered=len(probes))
    if not probes:
        res.skipped = "no probe answered"
        return res
    same_status = [p.status == fact.status for p in probes]
    res.status_agreement = sum(same_status) / len(probes)
    if kind == "status":
        res.consistency = res.status_agreement
        res.probes = [{"status": p.status, "value": _describe(p.value), "agrees": s} for p, s in zip(probes, same_status)]
        return res
    assert fact.value is not None
    agree = [value_agrees(kind, fact.value, p.value) for p in probes]
    res.value_agreement = sum(agree) / len(probes)
    res.consistency = res.value_agreement
    res.probes = [{"status": p.status, "value": _describe(p.value), "agrees": a} for p, a in zip(probes, agree)]
    return res


async def consistency_probe(
    fact: Fact, *, catalog: Catalog, reextract: Reextract, k: int = K_PROBES, sem: asyncio.Semaphore | None = None
) -> ConsistencyResult:
    """k paraphrased re-extractions of one fact; tier C facts are skipped (NaN)."""
    slot = catalog.slot(fact.slot_id)
    if slot.sensitivity_tier == "C":
        return ConsistencyResult(fact_id=fact.fact_id, kind=value_kind(fact, slot), k=k, skipped="tier C")
    sem = sem or asyncio.Semaphore(k)
    errors: list[str] = []

    async def one(variant: int) -> ProbeResult | None:
        async with sem:
            try:
                return await reextract(fact, variant)
            except Exception as e:  # noqa: BLE001 - a failed probe is left out, not fatal; the failure is counted
                log.warning("consistency probe %s/%d failed: %s", fact.fact_id, variant, e)
                errors.append(f"{type(e).__name__}: {e}")
                return None

    answered = [p for p in await asyncio.gather(*(one(v) for v in range(k))) if p is not None]
    res = score_probes(fact, slot, answered, k)
    res.failures = len(errors)
    res.error = errors[-1] if errors else None
    if errors and not answered:
        res.skipped = f"no probe answered ({len(errors)} failed)"
    return res


async def run_consistency(
    profile: Profile,
    fact_ids: Sequence[str],
    *,
    catalog: Catalog | None = None,
    reextract: Reextract,
    k: int = K_PROBES,
    concurrency: int = 8,
) -> dict[str, ConsistencyResult]:
    """Consistency for each fact (tier A and B probed; tier C NaN)."""
    catalog = catalog or load_catalog(profile.schema_version)
    sem = asyncio.Semaphore(concurrency)
    facts = [profile.facts[f] for f in dict.fromkeys(fact_ids) if f in profile.facts]
    results = await asyncio.gather(*(consistency_probe(f, catalog=catalog, reextract=reextract, k=k, sem=sem) for f in facts))
    return {r.fact_id: r for r in results}
