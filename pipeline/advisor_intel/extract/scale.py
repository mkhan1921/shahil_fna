"""Scale resolution for spoken amounts (spec §7.4).

The amount grammar (§5.3) gives each numeric span its well-formed readings, so "two fifty" yields
250, 250,000 and 2,500,000. Choosing between them needs context the grammar cannot see, so each
reading is scored as a product of likelihoods:

1. the grammar and ASR score (``Reading.weight``, normalised), which already includes explicit scale
   words ("thousand", "bar", "mil");
2. the second pass (§5.6), when it ran: a matching reading ×4, a contradicting one ÷4;
3. the field's plausible range from the population prior (its 1st–99th percentiles, e.g. a gross
   monthly income in roughly [5,000, 500,000]): 1 inside, ×0.05 per decade outside, ~0 beyond the
   slot's hard bounds;
4. consistency with other facts: a caller-supplied soft-constraint likelihood (a monthly expense
   above total monthly income, a fund 10× its prior FNA value) in (0, 1];
5. the prior FNA value: a reading within 20% of the client's existing figure gets a boost, because
   most review-call amounts are updates, not surprises.

One reading with ≥ 0.9 of the combined weight becomes the value; otherwise every reading with
≥ 0.1 becomes a hypothesis with that weight and ``scale_ambiguous = 1``. The LLM never chooses the
scale itself: it quotes the span, and ``resolve_amount`` / ``resolve_hypotheses`` here replace the
numbers in its AMOUNT_* form with the resolved readings.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace
from typing import Callable, Sequence

from pydantic import BaseModel

from .. import stats
from ..lexicon.normalize import MONEY_PERIOD, period_factor
from ..models.dists import Dist
from ..models.extraction import IntermediateForm, RawHypothesis
from ..models.profile import Profile
from ..models.transcript import NumericSpan, Reading
from ..schema.slots import SlotDef

SINGLE = 0.9  # one reading with at least this share is the value (§7.4)
KEEP = 0.1  # otherwise every reading with at least this share becomes a hypothesis
MARGIN_CAP = 1000.0  # grammar_top_margin when a span has a single reading
SCALE_FEATURES = ("grammar_readings", "grammar_top_margin", "scale_ambiguous", "second_pass_run", "second_pass_agreement")
NUMBER_FIELDS: dict[str, tuple[str, ...]] = {
    "AMOUNT_EXACT": ("value",),
    "AMOUNT_APPROX": ("value",),
    "AMOUNT_RANGE": ("low", "high"),
    "AMOUNT_BOUND": ("bound",),
}


@dataclass(frozen=True)
class ScaleContext:
    """What scale resolution knows beyond the grammar. Every field is optional."""

    population_prior: Dist | None = None  # default: the slot's population prior
    plausible_range: tuple[float, float] | None = None  # overrides the prior's range, in the slot's unit
    prior_value: float | None = None  # the client's existing FNA value, in the slot's unit
    consistency: Callable[[float], float] | None = None  # soft-constraint likelihood of a value in the slot's unit
    second_pass: Reading | None = None  # overrides NumericSpan.second_pass_reading
    period: str | None = None  # the spoken period when a reading carries none ('monthly', 'annual')
    range_quantiles: tuple[float, float] = (0.01, 0.99)
    out_of_range_decay: float = 0.05  # likelihood per decade outside the plausible range
    hard_floor: float = 1e-6  # likelihood beyond the slot's hard bounds
    prior_boost: float = 3.0
    prior_tolerance: float = 0.2  # "within 20% of the client's existing FNA value"
    second_pass_factor: float = 4.0

    @classmethod
    def for_fact(cls, slot: SlotDef, profile: Profile | None, fact_id: str | None, **kw) -> "ScaleContext":
        """A context with the prior FNA value of the fact being extracted, when the profile holds one."""
        prior_value = None
        f = profile.facts.get(fact_id) if profile is not None and fact_id else None
        if f is not None and f.prior_value is not None and f.prior_value.unit in ("ZAR", "count", "none", "years", "age", "pct"):
            d = f.prior_value.dist
            if not stats.is_categorical(d) and not stats.is_boolean(d):
                prior_value = stats.median(d)
        return cls(prior_value=prior_value, **kw)


class ResolvedReading(BaseModel):
    value: float  # as spoken, in the reading's own period
    slot_value: float  # in the slot's unit (what the context signals were computed on)
    weight: float  # posterior share (renormalised among the chosen in ``ScaleResolution.chosen``)
    signals: dict[str, float]  # grammar, second_pass, range, consistency, prior
    reading: Reading


class ScaleResolution(BaseModel):
    span_id: str | None
    readings: list[ResolvedReading]  # every distinct reading, by posterior weight
    chosen: list[ResolvedReading]  # the value (one) or the hypotheses (each >= 0.1)
    scale_ambiguous: bool
    features: dict[str, float | None]  # the §9.2 scale features for this span


def _numeric(r: Reading) -> bool:
    return isinstance(r.value, (int, float)) and not isinstance(r.value, bool) and math.isfinite(float(r.value))


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9)


def plausible_range(slot: SlotDef, context: ScaleContext | None = None) -> tuple[float, float] | None:
    """The slot's plausible range: an explicit override, else the population prior's 1st–99th percentiles, else its bounds."""
    ctx = context or ScaleContext()
    if ctx.plausible_range is not None:
        return ctx.plausible_range
    prior = ctx.population_prior or slot.population_prior
    if prior is not None and not stats.is_categorical(prior) and not stats.is_boolean(prior):
        lo, hi = (stats.quantile(prior, q) for q in ctx.range_quantiles)
        if math.isfinite(lo) and math.isfinite(hi) and hi > lo:
            return lo, hi
    return (float(slot.bounds[0]), float(slot.bounds[1])) if slot.bounds else None


def _range_likelihood(x: float, rng: tuple[float, float] | None, slot: SlotDef, ctx: ScaleContext) -> float:
    if slot.bounds and not (slot.bounds[0] <= x <= slot.bounds[1]):
        return ctx.hard_floor
    if rng is None:
        return 1.0
    lo, hi = rng
    if lo <= x <= hi:
        return 1.0
    if x < lo:
        if x <= 0 or lo <= 0:
            return ctx.out_of_range_decay
        decades = math.log10(lo / x)
    else:
        if hi <= 0:
            return ctx.out_of_range_decay
        decades = math.log10(x / hi)
    return max(ctx.hard_floor, ctx.out_of_range_decay**decades)


def _merge(readings: Sequence[Reading]) -> list[Reading]:
    """Readings with the same value (different lattice paths) merge, summing their weights."""
    out: list[Reading] = []
    for r in readings:
        if not _numeric(r):
            continue
        for i, o in enumerate(out):
            if _same(float(o.value), float(r.value)):
                keep = o if o.weight >= r.weight else r
                out[i] = keep.model_copy(update={"weight": o.weight + r.weight})
                break
        else:
            out.append(r)
    return out


def _span_features(n: int, margin: float | None, ambiguous: bool, sp: Reading | None, chosen: float | None) -> dict[str, float | None]:
    run = sp is not None and _numeric(sp)
    return {
        "grammar_readings": float(n),
        "grammar_top_margin": None if margin is None else min(MARGIN_CAP, margin),
        "scale_ambiguous": 1.0 if ambiguous else 0.0,
        "second_pass_run": 1.0 if run else 0.0,
        "second_pass_agreement": (1.0 if chosen is not None and _same(float(sp.value), chosen) else 0.0) if run else None,  # type: ignore[union-attr]
    }


def resolve_scale(span: NumericSpan, slot: SlotDef, *, context: ScaleContext | None = None) -> ScaleResolution:
    """Pick among a span's grammar readings with the five §7.4 signals."""
    ctx = context or ScaleContext()
    readings = _merge(span.readings)
    sp = ctx.second_pass or span.second_pass_reading
    if not readings:
        return ScaleResolution(span_id=span.span_id, readings=[], chosen=[], scale_ambiguous=False, features=_span_features(0, None, False, sp, None))
    total_w = sum(max(0.0, r.weight) for r in readings)
    grammar = [max(0.0, r.weight) / total_w if total_w > 0 else 1.0 / len(readings) for r in readings]
    ws = sorted(grammar, reverse=True)
    margin = MARGIN_CAP if len(ws) < 2 else (ws[0] / ws[1] if ws[1] > 0 else MARGIN_CAP)
    rng = plausible_range(slot, ctx)
    scored: list[tuple[Reading, float, float, dict[str, float]]] = []
    for r, g in zip(readings, grammar):
        x = float(r.value)
        sx = x * period_factor(r.period if r.period != "none" else (ctx.period or "none"), slot.fna_unit) if slot.fna_unit in MONEY_PERIOD else x
        sig = {"grammar": g, "second_pass": 1.0, "range": _range_likelihood(sx, rng, slot, ctx), "consistency": 1.0, "prior": 1.0}
        if sp is not None and _numeric(sp):
            sig["second_pass"] = ctx.second_pass_factor if _same(float(sp.value), x) else 1.0 / ctx.second_pass_factor
        if ctx.consistency is not None:
            sig["consistency"] = max(ctx.hard_floor, min(1.0, float(ctx.consistency(sx))))
        if ctx.prior_value is not None and ctx.prior_value != 0 and abs(sx - ctx.prior_value) <= ctx.prior_tolerance * abs(ctx.prior_value):
            sig["prior"] = ctx.prior_boost
        scored.append((r, sx, math.prod(sig.values()), sig))
    z = sum(s for _, _, s, _ in scored)
    post = [s / z for _, _, s, _ in scored] if z > 0 else grammar
    all_r = sorted(
        (ResolvedReading(value=float(r.value), slot_value=sx, weight=p, signals=sig, reading=r) for (r, sx, _, sig), p in zip(scored, post)),
        key=lambda rr: -rr.weight,
    )
    if all_r[0].weight >= SINGLE:
        chosen = [all_r[0].model_copy(update={"weight": 1.0})]
        ambiguous = False
    else:
        kept = [rr for rr in all_r if rr.weight >= KEEP] or all_r[:1]
        s = sum(rr.weight for rr in kept)
        chosen = [rr.model_copy(update={"weight": rr.weight / s}) for rr in kept]
        ambiguous = True
    feats = _span_features(len(readings), margin, ambiguous, sp, chosen[0].value)
    return ScaleResolution(span_id=span.span_id, readings=all_r, chosen=chosen, scale_ambiguous=ambiguous, features=feats)


def scale_features(resolutions: Sequence[ScaleResolution]) -> dict[str, float | None]:
    """The §9.2 scale features for a fact: the riskiest value over its spans (a range has two)."""
    if not resolutions:
        return {"grammar_readings": 0.0, "grammar_top_margin": None, "scale_ambiguous": 0.0, "second_pass_run": 0.0, "second_pass_agreement": None}
    fs = [r.features for r in resolutions]
    margins = [f["grammar_top_margin"] for f in fs if f["grammar_top_margin"] is not None]
    agree = [f["second_pass_agreement"] for f in fs if f["second_pass_agreement"] is not None]
    return {
        "grammar_readings": max(float(f["grammar_readings"] or 0.0) for f in fs),
        "grammar_top_margin": min(margins) if margins else None,
        "scale_ambiguous": max(float(f["scale_ambiguous"] or 0.0) for f in fs),
        "second_pass_run": max(float(f["second_pass_run"] or 0.0) for f in fs),
        "second_pass_agreement": min(agree) if agree else None,  # type: ignore[type-var]
    }


# --------------------------------------------------------------------------- glue for the extractor


class ResolvedForm(BaseModel):
    form: IntermediateForm  # the extractor's AMOUNT_* form with the resolved numbers
    weight: float  # share of the extractor's hypothesis
    span_ids: list[str] = []
    values: dict[str, float] = {}  # field → resolved value


class AmountResolution(BaseModel):
    hypotheses: list[ResolvedForm]
    resolutions: list[ScaleResolution] = []
    features: dict[str, float | None]
    flags: list[str] = []  # "no_matching_span" when a quoted number matches no grammar span

    @property
    def scale_ambiguous(self) -> bool:
        return bool(self.features.get("scale_ambiguous"))


def _power_of_ten(a: float, b: float) -> bool:
    if a <= 0 or b <= 0:
        return False
    k = math.log10(a / b)
    return abs(k - round(k)) < 1e-6 and abs(round(k)) <= 9


def match_span(value: float, spans: Sequence[NumericSpan], used: set[str] | None = None) -> NumericSpan | None:
    """The amount span a quoted number came from: one with a reading equal to it, else one that differs only in scale."""
    used = used or set()
    best, best_score = None, 0
    for s in spans:
        if s.kind != "amount" or s.span_id in used:
            continue
        score = 0
        for r in s.readings:
            if not _numeric(r):
                continue
            if _same(float(r.value), float(value)):
                score = max(score, 3)
            elif _power_of_ten(float(r.value), float(value)):
                score = max(score, 2)
        if score > best_score:
            best, best_score = s, score
    return best if best_score >= 2 else None


def resolve_amount(form: IntermediateForm, spans: Sequence[NumericSpan], slot: SlotDef, *, context: ScaleContext | None = None) -> AmountResolution:
    """Resolve the scale of every number the LLM quoted in an AMOUNT_* form from the spans overlapping its evidence.

    The LLM's own magnitude is ignored: each number is matched to its grammar span (same digits,
    any scale) and replaced by the span's resolved readings. A range resolves both ends and keeps
    only combinations with low <= high. Forms without numbers (vague, relative) and non-amount forms
    pass through unchanged with weight 1.
    """
    fields = NUMBER_FIELDS.get(form.type, ())
    if not fields:
        return AmountResolution(hypotheses=[ResolvedForm(form=form, weight=1.0)], features=scale_features([]))
    ctx = context or ScaleContext()
    if ctx.period is None and getattr(form, "period", None) in ("monthly", "annual", "once"):
        ctx = replace(ctx, period=form.period)  # type: ignore[union-attr]
    used: set[str] = set()
    options: list[list[tuple[float, float, str | None]]] = []
    resolutions: list[ScaleResolution] = []
    flags: list[str] = []
    for name in fields:
        quoted = float(getattr(form, name))
        span = match_span(quoted, spans, used)
        if span is None:
            if "no_matching_span" not in flags:
                flags.append("no_matching_span")
            options.append([(quoted, 1.0, None)])
            continue
        used.add(span.span_id)
        res = resolve_scale(span, slot, context=ctx)
        resolutions.append(res)
        options.append([(rr.value, rr.weight, span.span_id) for rr in res.chosen] or [(quoted, 1.0, span.span_id)])
    combos: list[tuple[dict[str, float], float, list[str]]] = []
    for combo in itertools.product(*options):
        values = {name: v for name, (v, _, _) in zip(fields, combo)}
        w = math.prod(cw for _, cw, _ in combo)
        if form.type == "AMOUNT_RANGE" and values["low"] > values["high"]:
            w = 0.0
        if w > 0:
            combos.append((values, w, [sid for _, _, sid in combo if sid]))
    if not combos:  # every combination inverted: keep the best-weighted one, swapped back by the normaliser
        combo = [opt[0] for opt in options]
        combos = [({name: v for name, (v, _, _) in zip(fields, combo)}, 1.0, [sid for _, _, sid in combo if sid])]
    z = sum(w for _, w, _ in combos)
    combos = sorted(((v, w / z, s) for v, w, s in combos), key=lambda c: -c[1])
    if combos[0][1] >= SINGLE:
        combos = [(combos[0][0], 1.0, combos[0][2])]
    elif len(combos) > 1:
        kept = [c for c in combos if c[1] >= KEEP] or combos[:1]
        s = sum(c[1] for c in kept)
        combos = [(v, w / s, sids) for v, w, sids in kept]
    hyps = [ResolvedForm(form=form.model_copy(update=values), weight=w, span_ids=sids, values=values) for values, w, sids in combos]
    feats = scale_features(resolutions)
    if len(hyps) > 1:
        feats["scale_ambiguous"] = 1.0
    return AmountResolution(hypotheses=hyps, resolutions=resolutions, features=feats, flags=flags)


class ScaledHypothesis(BaseModel):
    raw: RawHypothesis  # the extractor's hypothesis with its numbers resolved
    parent: int  # index of the extractor hypothesis it came from (``assign_weights`` groups)
    scale_weight: float  # share of the parent's weight (``assign_weights`` scale_weights)
    span_ids: list[str] = []


class ScaledHypotheses(BaseModel):
    items: list[ScaledHypothesis]
    features: dict[str, float | None]
    flags: list[str] = []

    @property
    def groups(self) -> list[int]:
        return [h.parent for h in self.items]

    @property
    def scale_weights(self) -> list[float]:
        return [h.scale_weight for h in self.items]


def resolve_hypotheses(
    raw_hypotheses: Sequence[RawHypothesis], spans: Sequence[NumericSpan], slot: SlotDef, *, context: ScaleContext | None = None
) -> ScaledHypotheses:
    """Scale-resolve every AMOUNT_* hypothesis of one field (§7.4) before normalisation (§7.3 step 4).

    Pass ``groups`` and ``scale_weights`` of the result to ``assign_weights`` so readings of one
    spoken amount share their parent's weight.
    """
    items: list[ScaledHypothesis] = []
    resolutions: list[ScaleResolution] = []
    flags: list[str] = []
    ambiguous = False
    for i, h in enumerate(raw_hypotheses):
        if h.intermediate.type not in NUMBER_FIELDS:
            items.append(ScaledHypothesis(raw=h, parent=i, scale_weight=1.0))
            continue
        res = resolve_amount(h.intermediate, spans, slot, context=context)
        resolutions.extend(res.resolutions)
        ambiguous = ambiguous or len(res.hypotheses) > 1
        flags.extend(f for f in res.flags if f not in flags)
        for rf in res.hypotheses:
            items.append(ScaledHypothesis(raw=h.model_copy(update={"intermediate": rf.form}), parent=i, scale_weight=rf.weight, span_ids=rf.span_ids))
    feats = scale_features(resolutions)
    if ambiguous:
        feats["scale_ambiguous"] = 1.0
    return ScaledHypotheses(items=items, features=feats, flags=flags)


__all__ = [
    "AmountResolution",
    "KEEP",
    "NUMBER_FIELDS",
    "ResolvedForm",
    "ResolvedReading",
    "SCALE_FEATURES",
    "SINGLE",
    "ScaleContext",
    "ScaleResolution",
    "ScaledHypotheses",
    "ScaledHypothesis",
    "match_span",
    "plausible_range",
    "resolve_amount",
    "resolve_hypotheses",
    "resolve_scale",
    "scale_features",
]
