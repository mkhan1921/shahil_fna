"""The reward for RL on the fine-tuned extractor, and the reward-hacking checks (spec §15.9).

One sampled slot-group output is scored against the reviewed facts for the same task:

=================== ====================================================================================
Term                Value
=================== ====================================================================================
status              +1 per slot whose status matches the review
value               +1 per slot at most: a proper scoring rule scaled to [0, 1] — the interval score on
                    p10–p90 for ranges, CRPS for other distributions, Brier for categories and booleans;
                    a very wide range cannot score well
grounded            +0.5 per fact whose every quote is found in its segment
unsupported         −2 per stated or implied fact with no matching reviewed fact
attribution         −1 per fact with the advisor's suggestion recorded as the client's, or the reverse
calibration         −Brier(self_score, correct), the mean over the output's valued facts
=================== ====================================================================================

The policy's raw text is turned into ``RolloutFact``s by the production post-processing (span alignment and
normalisation, §7.3, §8), injected into the training loop, so the reward sees the same distributions the pipeline
would store. The checks guard against reward hacking: the not_discussed rate per slot group must stay within 2
points of the M6 model's, mean range width per slot must not grow while coverage stays flat, and 50 sampled outputs
go to a person before promotion.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel
from rapidfuzz import fuzz

from .. import stats
from ..models.dists import Quantity
from ..models.facts import Attribution, FactStatus
from ..models.profile import Profile
from ..schema.slots import Catalog, SlotDef, load_catalog
from .gold import GoldFact, GoldValue, gold_value
from .records import FOUND_STATUSES

INTERVAL_ALPHA = 0.2  # p10–p90 is an 80% interval
VALUED = frozenset({"stated", "implied"})
CRPS_SAMPLES = 2000


class RolloutFact(BaseModel):
    """One slot of a sampled slot-group output after post-processing."""

    slot_id: str
    instance_key: str | None = None
    status: FactStatus
    value: Quantity | None = None
    quotes: list[tuple[str, str]] = []  # (segment_id, quote) the output cited
    attribution: Attribution = "client"
    self_score: float | None = None
    range_like: bool = False  # the value came from a spoken range (AMOUNT_RANGE, AGE range): scored with the interval score
    interpretation: str = ""

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.slot_id, self.instance_key)


class RolloutOutput(BaseModel):
    """One sampled output for one slot-group prompt."""

    prompt_id: str
    call_id: str = ""
    group: str = ""
    facts: list[RolloutFact] = []
    text: str = ""


class RewardBreakdown(BaseModel):
    status: float = 0.0
    value: float = 0.0
    grounded: float = 0.0
    unsupported: float = 0.0
    attribution: float = 0.0
    calibration: float = 0.0
    n_slots: int = 0

    @property
    def total(self) -> float:
        return self.status + self.value + self.grounded + self.unsupported + self.attribution + self.calibration


# --------------------------------------------------------------------------- proper scoring rules


def interval_score(lo: float, hi: float, y: float, alpha: float = INTERVAL_ALPHA) -> float:
    """Gneiting–Raftery interval score of a central (1 − α) interval: width plus 2/α times the miss distance."""
    return (hi - lo) + (2.0 / alpha) * max(0.0, lo - y) + (2.0 / alpha) * max(0.0, y - hi)


def crps(dist: Any, y: float, *, n: int = CRPS_SAMPLES, seed: int = 0) -> float:
    """Continuous ranked probability score E|X − y| − ½E|X − X'|, exact for points, by sampling otherwise."""
    if dist.kind == "point":
        return abs(stats.as_number(dist.value) - y)
    xs = np.sort(stats.sample(dist, np.random.default_rng(seed), n).astype(float))
    # E|X − X'| from sorted samples: (2 / n²) Σ (2i − n − 1) x_(i)
    i = np.arange(1, n + 1)
    e_xx = float(2.0 / (n * n) * np.sum((2 * i - n - 1) * xs))
    return float(np.mean(np.abs(xs - y)) - 0.5 * e_xx)


def value_scale(slot: SlotDef, y: float) -> float:
    """The error at which the value term reaches zero, per value type (half the value for amounts)."""
    vt = slot.value_type
    if vt in ("date", "year"):
        return 2.0
    if vt == "age":
        return 5.0
    if vt in ("years", "months"):
        return max(0.5 * abs(y), 1.0)
    if vt == "rate":
        return max(0.5 * abs(y), 0.01)
    if vt == "count":
        return 1.0
    return max(0.5 * abs(y), 100.0)


def value_score(slot: SlotDef, value: Quantity | None, truth: GoldValue | None, *, range_like: bool = False) -> float:
    """The value term in [0, 1] (§15.9): a proper scoring rule, linearly scaled and clipped."""
    if value is None or truth is None:
        return 0.0
    d = value.dist
    try:
        if truth.kind == "numeric":
            y = float(truth.p50)  # type: ignore[arg-type]
            if slot.value_type == "probability":
                p = stats.p_true(d) if stats.is_boolean(d) else stats.mean(d)
                return max(0.0, 1.0 - abs(p - y) / 0.5)
            if range_like:
                s = interval_score(stats.quantile(d, 0.1), stats.quantile(d, 0.9), y)
            else:
                s = crps(d, y)
            return float(np.clip(1.0 - s / value_scale(slot, y), 0.0, 1.0))
        if truth.kind == "boolean":
            p = stats.p_true(d)
            return 1.0 - (p - (1.0 if truth.truth else 0.0)) ** 2
        if truth.kind == "categorical":
            probs = stats.categorical_probs(d)
            labels = set(probs) | {str(truth.label)}
            return 1.0 - 0.5 * sum((probs.get(k, 0.0) - (1.0 if k == truth.label else 0.0)) ** 2 for k in labels)
        return fuzz.token_set_ratio(str(getattr(d, "value", "")), truth.label or "") / 100.0
    except (TypeError, ValueError):
        return 0.0


def _coarse(a: str) -> str:
    return "advisor" if a == "advisor_suggested" else ("mixed" if a == "mixed" else "client")


def _correct(slot: SlotDef, f: RolloutFact, truth: GoldFact | None) -> int:
    """§9.3's label: right status and, for a valued fact, a distribution that covers the reviewed value."""
    ts = truth.status if truth else "not_discussed"
    if f.status != ts:
        return 0
    if f.status in FOUND_STATUSES and f.value is not None and truth is not None and truth.value is not None:
        from .records import summarise_quantity
        from .scorers import covers

        return int(covers(summarise_quantity(f.value, slot), truth.value))
    return 1


def reward(
    rollout: Sequence[RolloutFact],
    reviewed: Sequence[GoldFact],
    segments: Mapping[str, str],
    *,
    slots: Iterable[tuple[str, str | None]] | None = None,
    catalog: Catalog | None = None,
) -> RewardBreakdown:
    """The §15.9 reward of one slot-group output against the reviewed facts of the same task.

    ``slots`` lists the task's (slot, instance) keys; by default the union of both sides. ``segments`` maps segment
    ids to their 1-best text, for the grounding term."""
    catalog = catalog or load_catalog()
    pred = {f.key: f for f in rollout}
    truth = {g.key: g for g in reviewed}
    keys = list(dict.fromkeys(slots)) if slots is not None else sorted(set(pred) | set(truth), key=lambda k: (k[0], k[1] or ""))
    out = RewardBreakdown(n_slots=len(keys))
    briers = []
    for key in keys:
        slot = catalog.slot(key[0])
        f, t = pred.get(key), truth.get(key)
        ps = f.status if f else "not_discussed"
        ts = t.status if t else "not_discussed"
        out.status += 1.0 if ps == ts else 0.0
        if f is not None and t is not None and ps in FOUND_STATUSES and ts in FOUND_STATUSES:
            out.value += value_score(slot, f.value, t.value, range_like=f.range_like)
        if f is None or ps not in FOUND_STATUSES:
            continue
        if f.quotes and all(q and q in segments.get(seg, "") for seg, q in f.quotes):
            out.grounded += 0.5
        if ps in VALUED and (t is None or ts not in FOUND_STATUSES):
            out.unsupported -= 2.0
        elif t is not None and ts in FOUND_STATUSES and _coarse(f.attribution) != _coarse(t.attribution):
            out.attribution -= 1.0
        if f.self_score is not None:
            briers.append((float(f.self_score) - _correct(slot, f, t)) ** 2)
    out.calibration = -float(np.mean(briers)) if briers else 0.0
    return out


def reviewed_facts(profile: Profile, *, catalog: Catalog | None = None, include_pending: bool = False) -> list[GoldFact]:
    """The reviewed truth of a profile as gold facts: accepted, edited and auto-accepted facts keep their value;
    rejected facts count as not discussed; unreviewed facts are left out unless asked for."""
    catalog = catalog or load_catalog()
    out = []
    for f in profile.facts.values():
        if f.source in ("prior_fna", "fna_default", "crm") and f.review.status == "pending":
            continue
        rs = f.review.status
        if rs == "rejected":
            out.append(GoldFact(slot_id=f.slot_id, instance_key=f.instance_key, fna_path=f.fna_path, status="not_discussed"))
            continue
        if rs == "pending" and not (include_pending or f.auto_accepted):
            continue
        slot = catalog.slot(f.slot_id)
        gv = None
        if f.value is not None and f.status in FOUND_STATUSES:
            d = f.value.dist
            if slot.value_type in ("boolean",):
                gv = GoldValue(kind="boolean", label="true" if stats.p_true(d) >= 0.5 else "false")
            elif slot.value_type == "probability":
                p = stats.p_true(d) if stats.is_boolean(d) else stats.mean(d)
                gv = GoldValue(kind="numeric", p50=p)
            elif slot.value_type in ("category", "count") or stats.is_categorical(d):
                gv = GoldValue(kind="categorical", label=stats.mode_label(d))
            elif slot.value_type == "text":
                gv = gold_value(slot, value=getattr(d, "value", ""))
            else:
                s = stats.summary(d)
                gv = GoldValue(kind="numeric", p10=s["p10"], p50=s["p50"], p90=s["p90"])
        out.append(
            GoldFact(
                slot_id=f.slot_id,
                instance_key=f.instance_key,
                fna_path=f.fna_path,
                status=f.status,
                attribution=f.speaker_attribution,
                value=gv,
                spans=[],
            )
        )
    return out


# --------------------------------------------------------------------------- reward-hacking checks


class CheckRow(BaseModel):
    key: str
    candidate: float | None
    reference: float | None
    passed: bool
    note: str = ""


class HackingReport(BaseModel):
    passed: bool
    not_discussed: list[CheckRow]
    range_width: list[CheckRow]
    review_sheet: str | None = None
    flagged_samples: int = 0

    def failures(self) -> list[CheckRow]:
        return [r for r in self.not_discussed + self.range_width if not r.passed]


def not_discussed_rates(outputs: Sequence[RolloutOutput]) -> dict[str, float]:
    """Share of slots marked not_discussed, per slot group."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for o in outputs:
        for f in o.facts:
            g = o.group or load_catalog().slot(f.slot_id).group
            counts[g][0] += f.status == "not_discussed"
            counts[g][1] += 1
    return {g: n / t for g, (n, t) in counts.items() if t}


def check_not_discussed(candidate: Sequence[RolloutOutput], reference: Sequence[RolloutOutput], *, tol: float = 0.02) -> list[CheckRow]:
    """The candidate's not_discussed rate per group must stay within ``tol`` (2 points) of the M6 model's."""
    c, r = not_discussed_rates(candidate), not_discussed_rates(reference)
    rows = []
    for g in sorted(set(c) | set(r)):
        cv, rv = c.get(g), r.get(g)
        ok = cv is not None and rv is not None and abs(cv - rv) <= tol + 1e-12
        rows.append(
            CheckRow(
                key=g,
                candidate=cv,
                reference=rv,
                passed=ok or cv is None or rv is None,
                note="" if ok else ("dodging penalties" if cv is not None and rv is not None and cv > rv else ""),
            )
        )
    return rows


def width_and_coverage(outputs: Sequence[RolloutOutput], reviewed: Mapping[str, Sequence[GoldFact]]) -> dict[str, tuple[float, float, int]]:
    """Per slot: (mean relative p10–p90 width, share of reviewed medians covered, n) over numeric valued facts.
    ``reviewed`` maps prompt id → the reviewed facts of that prompt."""
    acc: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for o in outputs:
        truth = {g.key: g for g in reviewed.get(o.prompt_id, [])}
        for f in o.facts:
            t = truth.get(f.key)
            if f.value is None or f.status not in FOUND_STATUSES or t is None or t.value is None or t.value.kind != "numeric":
                continue
            try:
                lo, hi, mid = stats.quantile(f.value.dist, 0.1), stats.quantile(f.value.dist, 0.9), stats.median(f.value.dist)
            except TypeError:
                continue
            width = (hi - lo) / max(abs(mid), 1e-9)
            acc[f.slot_id].append((width, int(lo - 1e-9 <= float(t.value.p50) <= hi + 1e-9)))  # type: ignore[arg-type]
    return {s: (float(np.mean([w for w, _ in v])), float(np.mean([c for _, c in v])), len(v)) for s, v in acc.items()}


def check_range_width(
    candidate: Sequence[RolloutOutput],
    reference: Sequence[RolloutOutput],
    reviewed: Mapping[str, Sequence[GoldFact]],
    *,
    width_tol: float = 0.05,
    coverage_tol: float = 0.01,
) -> list[CheckRow]:
    """Mean range width per slot must not grow (beyond ``width_tol``, relative) while coverage stays flat (gains less
    than ``coverage_tol``)."""
    c, r = width_and_coverage(candidate, reviewed), width_and_coverage(reference, reviewed)
    rows = []
    for s in sorted(set(c) & set(r)):
        (cw, cc, _), (rw, rc, _) = c[s], r[s]
        wider = cw > rw * (1 + width_tol) + 1e-12
        flat = cc - rc < coverage_tol
        rows.append(
            CheckRow(
                key=s,
                candidate=cw,
                reference=rw,
                passed=not (wider and flat),
                note=f"coverage {rc:.2f} → {cc:.2f}" + (" while ranges widened" if wider and flat else ""),
            )
        )
    return rows


def flag_output(o: RolloutOutput, quote_counts: Counter[str], interp_counts: Counter[str], n_outputs: int) -> list[str]:
    """Automatic hints for the human reader: generic quotes and boilerplate rationales."""
    flags = []
    for f in o.facts:
        for _, q in f.quotes:
            if len(q.split()) < 3:
                flags.append(f"short quote for {f.slot_id}: {q!r}")
            elif quote_counts[q] > max(2, 0.2 * n_outputs):
                flags.append(f"generic quote reused across outputs: {q!r}")
        if f.interpretation and interp_counts[f.interpretation] > max(2, 0.2 * n_outputs):
            flags.append(f"boilerplate rationale: {f.interpretation!r}")
    return flags


def sample_for_review(outputs: Sequence[RolloutOutput], *, n: int = 50, seed: int = 0) -> list[dict[str, Any]]:
    """``n`` outputs for a person to read before promotion, with automatic flags (§15.9)."""
    quote_counts = Counter(q for o in outputs for f in o.facts for _, q in f.quotes)
    interp_counts = Counter(f.interpretation for o in outputs for f in o.facts if f.interpretation)
    idx = np.random.default_rng(seed).permutation(len(outputs))[:n]
    return [
        {
            "prompt_id": outputs[i].prompt_id,
            "call_id": outputs[i].call_id,
            "group": outputs[i].group,
            "text": outputs[i].text,
            "facts": [f.model_dump(mode="json") for f in outputs[i].facts],
            "flags": flag_output(outputs[i], quote_counts, interp_counts, len(outputs)),
            "reviewer_verdict": None,  # filled in by the person: "ok" or a note
        }
        for i in sorted(idx.tolist())
    ]


def write_review_sheet(samples: Sequence[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(s, sort_keys=True) + "\n" for s in samples))
    return path


def review_signed_off(path: Path) -> bool:
    """True once every sampled output has a reviewer verdict and none is a rejection."""
    if not path.exists():
        return False
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return bool(rows) and all(r.get("reviewer_verdict") not in (None, "", "reject") for r in rows)


def hacking_checks(
    candidate: Sequence[RolloutOutput],
    reference: Sequence[RolloutOutput],
    reviewed: Mapping[str, Sequence[GoldFact]],
    *,
    review_path: Path | None = None,
    n_review: int = 50,
    seed: int = 0,
) -> HackingReport:
    """Every training run's reward-hacking checks against the M6 model's outputs on the same prompts."""
    nd = check_not_discussed(candidate, reference)
    rw = check_range_width(candidate, reference, reviewed)
    samples = sample_for_review(candidate, n=n_review, seed=seed)
    sheet = str(write_review_sheet(samples, review_path)) if review_path else None
    return HackingReport(
        passed=all(r.passed for r in nd + rw),
        not_discussed=nd,
        range_width=rw,
        review_sheet=sheet,
        flagged_samples=sum(1 for s in samples if s["flags"]),
    )


def mean_reward(breakdowns: Iterable[RewardBreakdown]) -> float:
    xs = [b.total for b in breakdowns]
    return float(np.mean(xs)) if xs else math.nan
