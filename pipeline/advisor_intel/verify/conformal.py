"""Conformal auto-accept (spec §9.5) and the spot-check monitor (§11.3, §14.4).

Goal: with probability ≥ 1 − α, a new call has *no* incorrect fact among its auto-accepted facts.

Calibration set: the last N reviewed calls processed with the current pipeline versions. For each call i
the score is its most confident wrong fact, ``s_i = max{p_correct(f) : correct(f) = 0}`` (0 if none), and
``τ`` is the ⌈(N+1)(1−α)⌉-th smallest ``s_i``. With N < 50 auto-accept is off. Only facts that could ever
be auto-accepted enter the scores (stated, no hard constraint failure, no contradiction, and not tier A
unless ``auto_accept_tier_a``), and calibration calls must have every such fact labelled: calls reviewed in
full, which spot-checked calls (§11.3) and calls reviewed while auto-accept was off are.

Mondrian groups: τ is set per slot family (household, income and expenses, assets and liabilities,
policies and cover, goals) on that family's facts at α/5, so the per-call guarantee stays at α by the
union bound. A family with fewer than 50 calibration calls uses the pooled τ, computed at the same α/5
over every family's facts, which is never smaller than the family's own score would be, so the fallback
keeps the guarantee.

At run time a fact is auto-accepted when ``p_correct > τ`` for its family, its status is ``stated``, it has
no hard constraint failure and no contradicting evidence, and it is not tier A unless the deployment sets
``auto_accept_tier_a`` (default false). One review in ten, chosen by call id, is a spot check that shows
every auto-accepted fact; the realised share of calls with a wrong auto-accepted fact feeds a monitor that
switches auto-accept off when it exceeds α over the last 100 spot-checked calls.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..models.facts import Fact
from ..schema.slots import Catalog, load_catalog
from .labels import Label

FAMILIES: tuple[str, ...] = ("household", "income_expenses", "assets_liabilities", "policies_cover", "goals")
MONITOR_WINDOW = 100


class CalibrationCall(BaseModel):
    """One reviewed call's eligible facts: (p_correct, correct, family)."""

    call_id: str
    facts: list[tuple[float, int, str]] = []


def calibration_calls(labels: Iterable[Label], *, auto_accept_tier_a: bool = False, calls: set[str] | None = None) -> list[CalibrationCall]:
    """Group labels by call, keeping the facts auto-accept could ever accept (in first-seen call order)."""
    out: dict[str, CalibrationCall] = {}
    for lb in labels:
        if calls is not None and lb.call_id not in calls:
            continue
        cc = out.setdefault(lb.call_id, CalibrationCall(call_id=lb.call_id))
        if lb.eligible and (auto_accept_tier_a or lb.tier != "A"):
            cc.facts.append((float(lb.p_correct), int(lb.correct), lb.family))
    return list(out.values())


def call_score(call: CalibrationCall, family: str | None = None) -> float:
    """The call's most confident wrong fact (within ``family`` when given); 0 when there is none."""
    wrong = [p for p, correct, fam in call.facts if correct == 0 and (family is None or fam == family)]
    return max(wrong) if wrong else 0.0


def conformal_quantile(scores: Sequence[float], alpha: float) -> float | None:
    """The ⌈(N+1)(1−α)⌉-th smallest score, or None when that rank exceeds N (no finite threshold)."""
    n = len(scores)
    if n == 0:
        return None
    k = math.ceil((n + 1) * (1 - alpha) - 1e-12)
    if k > n:
        return None
    return sorted(scores)[max(k, 1) - 1]


class ConformalThresholds(BaseModel):
    """τ per family (Mondrian) or pooled, with what each was computed from."""

    version: str = "off"
    alpha: float = 0.1
    n_calls: int = 0
    enabled: bool = False
    reason: str = "not fitted"
    mondrian: bool = True
    family_alpha: float = 0.02  # α / number of families
    tau_pooled: float | None = None  # at α, for a non-Mondrian deployment
    tau_pooled_family: float | None = None  # at α/5: the fallback for a thin family
    tau_by_family: dict[str, float | None] = {}
    n_by_family: dict[str, int] = {}
    source_by_family: dict[str, Literal["family", "pooled"]] = {}
    auto_accept_tier_a: bool = False
    calibrator_version: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    def tau_for(self, family: str) -> float | None:
        if not self.enabled:
            return None
        if not self.mondrian:
            return self.tau_pooled
        if family in self.tau_by_family:
            return self.tau_by_family[family]
        return self.tau_pooled_family

    def disabled(self, reason: str) -> ConformalThresholds:
        return self.model_copy(update={"enabled": False, "reason": reason})


def fit_conformal(
    labels: Iterable[Label] | None = None,
    *,
    calls: Sequence[CalibrationCall] | None = None,
    alpha: float | None = None,
    min_calls: int | None = None,
    mondrian: bool = True,
    families: Sequence[str] = FAMILIES,
    auto_accept_tier_a: bool | None = None,
    calibrator_version: str = "",
    last_n: int | None = None,
    settings: Settings | None = None,
) -> ConformalThresholds:
    """The nightly ``refit_conformal`` step: τ from the calibration calls (§9.5).

    ``labels`` (or ``calls``) are in review order and come from calls processed with the current pipeline
    versions; ``last_n`` keeps only the most recent calls.
    """
    s = settings or get_settings()
    alpha = s.conformal_alpha if alpha is None else alpha
    min_calls = s.min_conformal_calls if min_calls is None else min_calls
    tier_a = s.auto_accept_tier_a if auto_accept_tier_a is None else auto_accept_tier_a
    if calls is None:
        calls = calibration_calls(labels or [], auto_accept_tier_a=tier_a)
    if last_n is not None:
        calls = list(calls)[-last_n:]
    n = len(calls)
    fam_alpha = alpha / max(1, len(families))
    th = ConformalThresholds(
        alpha=alpha, n_calls=n, mondrian=mondrian, family_alpha=fam_alpha, auto_accept_tier_a=tier_a, calibrator_version=calibrator_version
    )
    if n < min_calls:
        return th.model_copy(update={"reason": f"{n} calibration calls; auto-accept needs at least {min_calls}"})
    pooled = [call_score(c) for c in calls]
    th.tau_pooled = conformal_quantile(pooled, alpha)
    th.tau_pooled_family = conformal_quantile(pooled, fam_alpha)
    for fam in families:
        fam_calls = [c for c in calls if any(f == fam for _, _, f in c.facts)]
        th.n_by_family[fam] = len(fam_calls)
        if len(fam_calls) >= min_calls:
            th.tau_by_family[fam] = conformal_quantile([call_score(c, fam) for c in fam_calls], fam_alpha)
            th.source_by_family[fam] = "family"
        else:
            th.tau_by_family[fam] = th.tau_pooled_family
            th.source_by_family[fam] = "pooled"
    digest = hashlib.sha256(repr((alpha, mondrian, [(c.call_id, c.facts) for c in calls])).encode()).hexdigest()[:10]
    th.version = f"conformal-{n}-{digest}"
    th.enabled = True
    th.reason = f"{n} calibration calls at alpha {alpha:g}" + (f" (Mondrian, {fam_alpha:g} per family)" if mondrian else "")
    return th


# --------------------------------------------------------------------------- the auto-accept predicate


def contradicted(fact: Fact) -> bool:
    return (
        bool(fact.verification.get("contradicted")) or fact.status == "contradicted" or any(e.role == "contradicts" for e in fact.evidence)
    )


def auto_accept_decision(
    fact: Fact, thresholds: ConformalThresholds | None, *, catalog: Catalog | None = None, settings: Settings | None = None
) -> tuple[bool, str]:
    """§9.5: p_correct > τ, status stated, no hard constraint failure or contradiction, not tier A by default."""
    s = settings or get_settings()
    catalog = catalog or load_catalog()
    if thresholds is None or not thresholds.enabled:
        return False, f"auto-accept is off: {thresholds.reason if thresholds else 'no threshold'}"
    slot = catalog.slot(fact.slot_id)
    if slot.sensitivity_tier == "A" and not s.auto_accept_tier_a:
        return False, "tier A facts are never auto-accepted (auto_accept_tier_a is off)"
    if fact.status != "stated":
        return False, f"status is {fact.status}"
    if fact.verification.get("constraint") == "fail":
        return False, "a hard constraint fails"
    if contradicted(fact):
        return False, "contradicting evidence"
    tau = thresholds.tau_for(catalog.family_of(fact.slot_id))
    if tau is None:
        return False, "no finite threshold for this family"
    if not fact.p_correct > tau:
        return False, f"p_correct {fact.p_correct:.3f} is not above τ {tau:.3f}"
    return True, f"p_correct {fact.p_correct:.3f} > τ {tau:.3f}"


# --------------------------------------------------------------------------- spot checks and the monitor


def is_spot_check(call_id: str, rate: float | None = None, *, salt: str = "spot-check") -> bool:
    """One review in ten (``settings.spot_check_rate``), chosen deterministically from the call id (§11.3)."""
    rate = get_settings().spot_check_rate if rate is None else rate
    u = int.from_bytes(hashlib.sha256(f"{salt}:{call_id}".encode()).digest()[:8], "big") / 2**64
    return u < rate


class SpotCheckOutcome(BaseModel):
    call_id: str
    n_auto_accepted: int
    n_wrong: int
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WrongAutoAcceptMonitor(BaseModel):
    """The realised rate of calls with a wrong auto-accepted fact over the last 100 spot checks (§11.3, §14.4)."""

    alpha: float = 0.1
    window: int = MONITOR_WINDOW
    outcomes: list[SpotCheckOutcome] = []

    def record(self, call_id: str, n_auto_accepted: int, n_wrong: int) -> WrongAutoAcceptMonitor:
        kept = [o for o in self.outcomes if o.call_id != call_id] + [
            SpotCheckOutcome(call_id=call_id, n_auto_accepted=n_auto_accepted, n_wrong=n_wrong)
        ]
        return self.model_copy(update={"outcomes": kept[-self.window :]})

    def record_labels(self, call_id: str, labels: Sequence[Label]) -> WrongAutoAcceptMonitor:
        auto = [lb for lb in labels if lb.call_id == call_id and lb.auto_accepted]
        return self.record(call_id, len(auto), sum(1 for lb in auto if lb.correct == 0))

    def rate(self) -> float | None:
        if not self.outcomes:
            return None
        return sum(1 for o in self.outcomes if o.n_wrong > 0) / len(self.outcomes)

    def alert(self) -> bool:
        """True once a full window of spot checks shows a rate above α: switch auto-accept off."""
        r = self.rate()
        return len(self.outcomes) >= self.window and r is not None and r > self.alpha

    def apply(self, thresholds: ConformalThresholds) -> ConformalThresholds:
        if self.alert():
            return thresholds.disabled(
                f"wrong-auto-accept rate {self.rate():.1%} over the last {len(self.outcomes)} spot checks exceeds {self.alpha:g}"
            )
        return thresholds
