"""``normalize_value`` (spec §8.3): one intermediate form, as spoken, → a distribution in the slot's unit.

The LLM returns the flat intermediate forms of §8.1; this module turns each into a ``Quantity`` in
the FnaDocument unit the slot declares (``SlotDef.fna_unit``), using the versioned lexicon for every
vague word. Nothing here guesses: a word the lexicon does not know falls back to the slot's
population prior with ``lexicon_miss`` (or to the nearest key with ``lexicon_near_miss``), a missing
reference falls back with ``reference_missing``, and everything applied is recorded in the returned
``normalization`` dict (intermediate form, lexicon version, lookups, conversions), which becomes
``Fact.normalization``.

FnaDocument rules (§3.6, §8.3):

- money is converted to the field's period (``rand_monthly``, ``rand_annual``, ``rand`` lump sum);
  percentages become decimals; durations become a calendar year, a date or an age as the field
  needs; ages come from the person's date of birth when the field is age-typed;
- goal costs, retirement targets and education costs are in today's money: an amount priced in a
  future year (``priced_year``) is deflated with ``assumptions.cpi`` (``educationInflation`` for
  education fields), as the FNA engine inflates them (``lib/fna/analysis.ts``);
- foreign amounts stay in their currency (converted with an FX distribution in the forecast);
- an income basis the client did not give stays ``"unknown"`` (a 50/50 gross/net mixture in §12.2);
- category labels outside the FNA union map to ``other``, with the spoken label kept.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Mapping

import numpy as np
from pydantic import BaseModel, TypeAdapter
from rapidfuzz import fuzz, process

from .. import stats
from ..models.dists import CURRENCIES, Bernoulli, Categorical, Dist, Point, Quantity, Triangular
from ..models.extraction import (
    AgeForm,
    AmountApprox,
    AmountBound,
    AmountExact,
    AmountRange,
    AmountRelative,
    BooleanForm,
    CategoryForm,
    DateAbs,
    DurationFromAnchor,
    IntermediateForm,
    ProbabilityEvent,
    RateForm,
    TextForm,
)
from ..models.facts import Fact
from ..models.fna import Assumptions
from ..models.profile import Profile
from ..schema.slots import SlotDef, fact_id_for, generic_slot_id, person_of
from .loader import Lexicon, Lookup
from .phrases import canon

NormalizationFlag = Literal[
    "lexicon_miss",  # a vague word missed the lexicon: population prior (or lexicon default for a stated value)
    "lexicon_near_miss",  # the nearest lexicon key (similarity >= 0.8) supplied a provisional prior
    "population_prior",  # the value is the slot's population prior
    "fna_default",  # no population prior: the FNA default stands in, shown as assumed
    "no_prior",  # neither a prior nor a default: no value
    "deflated",  # a future-dated amount was deflated to today's money
    "future_dated_foreign",  # a future-dated foreign amount stays nominal; the forecast deflates it with FX
    "foreign_currency",  # the amount is kept in its own currency
    "reference_missing",  # a reference slot, date of birth or anchor is not in the profile
    "period_mismatch",  # a monthly or annual amount for a lump-sum field (or a one-off for a monthly field)
    "form_slot_mismatch",  # the form type does not fit the slot's unit
    "form_not_allowed",  # the form type is not in the slot's allowed_forms
    "label_mapped_to_other",  # a category outside the FNA union became 'other'; the spoken label is kept
    "label_unmatched",  # a category outside the union, and the slot has no 'other'
    "out_of_bounds",  # set by the bounds check (§7.3 step 6)
    "truncated_to_bounds",  # set by the bounds check
]

MONEY_PERIOD: dict[str, Literal["once", "monthly", "annual"]] = {"rand": "once", "rand_monthly": "monthly", "rand_annual": "annual"}
QUANTITY_UNIT: dict[str, str] = {
    "years": "years",
    "months": "months",
    "age": "age",
    "year": "date",  # a calendar year, as a decimal-year date
    "decimal": "pct",  # rates as decimal fractions (0.115 = 11.5%)
    "date": "date",
    "count": "count",
    "category": "none",
    "boolean": "none",
    "text": "none",
    "probability": "none",
}
_PER_YEAR = {"monthly": 12.0, "annual": 1.0}
# FnaDocument fields held in today's money (§8.3) and the assumption that inflates them in the FNA engine.
_TODAYS_MONEY: dict[tuple[str, str], Literal["cpi", "educationInflation"]] = {
    ("goals", "cost"): "cpi",
    ("retirement", "targetMonthly"): "cpi",
    ("retirement", "otherIncomeMonthly"): "cpi",
    ("dependants", "tertiaryCostAnnual"): "educationInflation",
    ("dependants", "schoolFeesAnnual"): "educationInflation",
}
_DURATION_NOISE = {
    "in",
    "from",
    "now",
    "time",
    "s",
    "years",
    "year",
    "months",
    "month",
    "jaar",
    "maande",
    "maand",
    "oor",
    "the",
    "next",
    "ago",
    "gelede",
}
_YES = ("yes", "true", "y", "ja")
_NO = ("no", "false", "n", "nee")
_NEGATION = {"not", "no", "never", "nie", "dont", "isnt", "arent"}
_FORM = TypeAdapter(IntermediateForm)


class Normalized(BaseModel):
    """``normalize_value``'s result: the value, how it was reached, and calibration flags."""

    quantity: Quantity | None  # None only when nothing usable exists (no prior, no default)
    normalization: dict[str, Any]  # intermediate form, lexicon version, lookups, conversions (§8.3)
    flags: list[str] = []  # NormalizationFlag values

    @property
    def ok(self) -> bool:
        return self.quantity is not None

    def has(self, flag: str) -> bool:
        return flag in self.flags

    def with_flag(self, flag: str, **notes: Any) -> "Normalized":
        flags = self.flags if flag in self.flags else [*self.flags, flag]
        return self.model_copy(update={"flags": flags, "normalization": {**self.normalization, **notes}})


def todays_money_rate(slot: SlotDef) -> Literal["cpi", "educationInflation"] | None:
    """The FNA assumption that inflates this field when it is held in today's money, else None (§8.3)."""
    head = slot.fna_path.split(".", 1)[0].split("[", 1)[0]
    return _TODAYS_MONEY.get((head, slot.field))


def quantity_unit(slot: SlotDef, currency: str = "ZAR") -> tuple[str, str]:
    """The (Quantity.unit, Quantity.period) a slot's values are stored in."""
    if slot.fna_unit in MONEY_PERIOD:
        return currency, MONEY_PERIOD[slot.fna_unit]
    return QUANTITY_UNIT[slot.fna_unit], "none"


# --------------------------------------------------------------------------- per-call context


@dataclass
class _Ctx:
    form: Any
    slot: SlotDef
    profile: Profile | None
    lex: Lexicon
    anchor: date | None
    person: str | None
    instance: str | None
    assumptions: Assumptions
    priced_year: float | None
    seed: int
    flags: list[str] = field(default_factory=list)
    conversions: list[str] = field(default_factory=list)
    lookups: list[Lookup] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)
    real_or_nominal: str = "na"

    def flag(self, *names: str) -> None:
        for n in names:
            if n not in self.flags:
                self.flags.append(n)

    def use(self, lk: Lookup) -> Lookup:
        self.lookups.append(lk)
        if lk.flag:
            self.flag(lk.flag)
        return lk

    def need_anchor(self) -> date:
        if self.anchor is None:
            raise ValueError(f"{self.form.type} for {self.slot.slot_id} needs anchor_date (or a profile with one)")
        return self.anchor

    def quantity(self, dist: Dist, *, currency: str = "ZAR", basis: str | None = None) -> Quantity:
        unit, period = quantity_unit(self.slot, currency)
        if basis is None:
            basis = getattr(self.form, "basis", "na") if self.slot.fna_unit in MONEY_PERIOD else "na"
        if basis == "na" and self.slot.fna_unit in MONEY_PERIOD and _person_list(self.slot) == "income":
            basis = "unknown"  # §8.3: an income basis the client did not give stays unknown
        return Quantity(dist=dist, unit=unit, period=period, basis=basis, real_or_nominal=self.real_or_nominal)  # type: ignore[arg-type]


def _point(v: Any) -> Point:
    return Point(value=v if isinstance(v, (bool, int, float, str)) else str(v))


def _prior(ctx: _Ctx, reason: str) -> Dist | None:
    """The slot's population prior, else its FNA default (assumed), else nothing (§4.3, §8.3)."""
    ctx.notes.setdefault("prior_reason", reason)
    if ctx.slot.population_prior is not None:
        ctx.flag("population_prior")
        return ctx.slot.population_prior
    if ctx.slot.fna_default is not None and isinstance(ctx.slot.fna_default, (bool, int, float, str)):
        ctx.flag("fna_default")
        return _point(ctx.slot.fna_default)
    ctx.flag("no_prior")
    return None


def _fallback(ctx: _Ctx, reason: str, *flags: str) -> Quantity | None:
    ctx.flag(*flags)
    dist = _prior(ctx, reason)
    return None if dist is None else ctx.quantity(dist)


# --------------------------------------------------------------------------- references and dates of birth


def _person_list(slot: SlotDef) -> str:
    return slot.fna_path.split(".", 1)[0].split("[", 1)[0]


def resolve_reference(ref: str, profile: Profile | None, *, person: str | None = None, instance: str | None = None) -> Fact | None:
    """A reference slot resolved against the profile's facts: by fact id first, then by slot id.

    ``{p}`` takes the current person and ``[]`` slots the current instance, so 'client.dateOfBirth',
    '{p}.dateOfBirth' and 'dependants[].dateOfBirth' (for the dependant being normalised) all resolve.
    """
    if profile is None or not ref or ref == "call":
        return None
    facts = profile.facts
    if ref in facts:
        return facts[ref]
    concrete = ref.replace("{p}", person) if person else ref
    for fid in (concrete, fact_id_for(concrete, instance) if instance else None):
        if fid and fid in facts:
            return facts[fid]
    want = generic_slot_id(concrete)
    want_person = person_of(concrete) or (person if "{p}" in ref else None)
    matches = [f for f in facts.values() if generic_slot_id(f.fact_id) == want or f.slot_id in (ref, concrete)]
    if want_person:
        matches = [f for f in matches if person_of(f.fact_id) in (want_person, None)]
    if "[]" in concrete and instance:
        matches = [f for f in matches if f.instance_key == instance or f.fact_id.endswith(f"#{instance}")]
    if len(matches) == 1:
        return matches[0]
    with_value = [f for f in matches if f.value is not None]
    return with_value[0] if len(with_value) == 1 else None


def _fact_quantity(f: Fact | None) -> tuple[Quantity | None, str | None]:
    if f is None:
        return None, None
    if f.value is not None:
        return f.value, "value"
    if f.prior_value is not None:
        return f.prior_value, "prior_value"
    return None, None


def _dob(ctx: _Ctx, explicit: str | None = None) -> tuple[Dist | None, str | None]:
    """The date of birth of the person this fact is about: the form's anchor, the person, the dependant or the life assured."""
    refs: list[tuple[str, str | None, str | None]] = []
    if explicit and explicit != "call":
        refs.append((explicit, ctx.person, ctx.instance))
    if ctx.person:
        refs.append((f"{ctx.person}.dateOfBirth", ctx.person, None))
    if ctx.instance:
        lst = _person_list(ctx.slot)
        if lst == "dependants":
            refs.append(("dependants[].dateOfBirth", None, ctx.instance))
        if lst == "policies":
            la = resolve_reference("policies[].lifeAssured", ctx.profile, instance=ctx.instance)
            q, _ = _fact_quantity(la)
            if q is not None and stats.is_categorical(q.dist):
                refs.append((f"{stats.mode_label(q.dist)}.dateOfBirth", None, None))
    for ref, person, instance in refs:
        f = resolve_reference(ref, ctx.profile, person=person, instance=instance)
        q, source = _fact_quantity(f)
        if q is not None:
            ctx.notes["date_of_birth"] = {"fact_id": f.fact_id, "source": source}  # type: ignore[union-attr]
            return q.dist, f.fact_id  # type: ignore[union-attr]
    return None, None


def _decimal_year(d: date) -> float:
    return stats.iso_to_year(d.isoformat())


def _exact_date(dist: Dist) -> date | None:
    if isinstance(dist, Point) and isinstance(dist.value, str):
        try:
            return date.fromisoformat(dist.value[:10])
        except ValueError:
            return None
    return None


def _age_on(dob: Dist, at: date) -> Dist:
    """Age at a date: completed years for an exact date of birth (FNA ``ageOn``), else decimal years."""
    exact = _exact_date(dob)
    if exact is not None:
        return Point(value=float(at.year - exact.year - ((at.month, at.day) < (exact.month, exact.day))))
    return stats.shifted(stats.scaled(dob, -1.0), _decimal_year(at))


def _plus(a: Dist, b: Dist, seed: int) -> Dist:
    if isinstance(a, Point):
        return stats.shifted(b, stats.as_number(a.value))
    if isinstance(b, Point):
        return stats.shifted(a, stats.as_number(b.value))
    return stats.total([a, b], seed=seed)


def _minus(a: Dist, b: Dist, seed: int) -> Dist:
    return _plus(a, stats.scaled(b, -1.0), seed)


def _times(a: Dist, b: Dist, seed: int) -> Dist:
    if isinstance(a, Point):
        return stats.scaled(b, stats.as_number(a.value))
    if isinstance(b, Point):
        return stats.scaled(a, stats.as_number(b.value))
    return stats.product(a, b, seed=seed)


# --------------------------------------------------------------------------- amounts


def _period_factor(spoken: str, fna_unit: str) -> tuple[float, str | None, bool]:
    """(multiplier, conversion note, mismatch) from the spoken period to a money field's period."""
    target = MONEY_PERIOD[fna_unit]
    if target == "once":
        if spoken in ("once", "none"):
            return 1.0, None, False
        return 1.0, f"{spoken} amount for a lump-sum field: not converted", True
    if spoken == "none":
        return 1.0, f"no period said: {target} assumed", False
    if spoken == "once":
        if target == "annual":
            return 1.0, "once-a-year amount read as annual", False
        return 1.0, "one-off amount for a monthly field: not converted", True
    if spoken == target:
        return 1.0, None, False
    f = _PER_YEAR[spoken] / _PER_YEAR[target]
    return f, f"{spoken} → {target}: ×{f:g}" if f >= 1 else f"{spoken} → {target}: ÷{1 / f:g}", False


def period_factor(spoken: str | None, fna_unit: str) -> float:
    """Multiplier from a spoken period ('monthly', 'annual', 'once', 'none') to a money field's period; 1 otherwise."""
    if fna_unit not in MONEY_PERIOD or spoken not in ("once", "monthly", "annual", "none"):
        return 1.0
    return _period_factor(spoken, fna_unit)[0]


def _deflation(ctx: _Ctx, currency: str) -> float:
    """Today's-money factor for an amount priced in a future year (§8.3)."""
    py = ctx.priced_year
    if py is None:
        return 1.0
    anchor = ctx.need_anchor()
    rate_name = todays_money_rate(ctx.slot)
    years = float(py) - anchor.year
    if rate_name is None:
        ctx.conversions.append(f"priced in {py:g}: {ctx.slot.field} is not held in today's money, kept as said")
        return 1.0
    if years <= 0:
        return 1.0
    if currency != "ZAR":
        ctx.flag("future_dated_foreign")
        ctx.real_or_nominal = "nominal"
        ctx.notes["deflation"] = {"applied": False, "priced_year": py, "reason": "foreign currency: the forecast deflates it with FX"}
        return 1.0
    rate = float(getattr(ctx.assumptions, rate_name))
    f = (1.0 + rate) ** -years
    ctx.flag("deflated")
    ctx.real_or_nominal = "real"
    ctx.notes["deflation"] = {
        "applied": True,
        "priced_year": py,
        "anchor_year": anchor.year,
        "years": years,
        "rate_name": rate_name,
        "rate": rate,
        "factor": f,
    }
    ctx.conversions.append(f"deflated to today's money: ÷(1 + {rate_name} {rate:g})^{years:g}")
    return f


def _range(lo: float, hi: float, widen: float) -> Triangular:
    lo, hi = sorted((float(lo), float(hi)))
    return Triangular(low=lo - abs(lo) * widen, mode=(lo + hi) / 2, high=hi + abs(hi) * widen)


def _bound(ctx: _Ctx, bound: float, direction: str, prior: Dist | None) -> Dist:
    """§8.3 AMOUNT_BOUND: the population prior truncated at the bound; a plain spread beyond it without a prior."""
    if prior is not None and not stats.is_categorical(prior) and not stats.is_boolean(prior):
        ctx.conversions.append(f"population prior truncated {'below' if direction == 'at_least' else 'above'} {bound:g}")
        return (
            stats.truncate(prior, low=bound, seed=ctx.seed) if direction == "at_least" else stats.truncate(prior, high=bound, seed=ctx.seed)
        )
    ctx.flag("no_prior")
    ctx.conversions.append("no population prior: a spread beyond the bound")
    b = abs(bound)
    if bound == 0 and direction == "at_most":
        return Point(value=0.0)
    if direction == "at_least":
        return Triangular(low=bound, mode=bound + 0.15 * b, high=bound + 0.6 * b) if b else Point(value=0.0)
    return Triangular(low=bound - 0.5 * b, mode=bound - 0.15 * b, high=bound)


def _money(ctx: _Ctx) -> Quantity | None:
    f, lex = ctx.form, ctx.lex
    if isinstance(f, AmountRelative):
        return _relative(ctx)
    currency, basis = f.currency, f.basis
    if _person_list(ctx.slot) == "income" and basis in ("na", "unknown"):
        basis = "unknown"
        ctx.conversions.append("income basis not said: kept unknown")
    factor, note, mismatch = _period_factor(f.period, ctx.slot.fna_unit)
    if note:
        ctx.conversions.append(note)
    if mismatch:
        ctx.flag("period_mismatch")
    if currency != "ZAR":
        ctx.flag("foreign_currency")
    factor *= _deflation(ctx, currency)
    ctx.notes["value_factor"] = factor
    if isinstance(f, AmountExact):
        dist: Dist = Point(value=float(f.value) * factor)
    elif isinstance(f, AmountApprox):
        dist = stats.scaled(ctx.use(lex.approx(f.approx_word, f.value)).dist, factor)  # type: ignore[arg-type]
    elif isinstance(f, AmountRange):
        dist = stats.scaled(_range(f.low, f.high, lex.params.range_widen_rel), factor)
        ctx.conversions.append(f"range widened {lex.params.range_widen_rel:.0%} each side")
    elif isinstance(f, AmountBound):
        prior = ctx.slot.population_prior if currency == "ZAR" else None
        dist = _bound(ctx, float(f.bound) * factor, f.direction, prior)
    else:  # AmountVague
        lk = ctx.use(lex.vague_amount(f.vague_word))
        if lk.dist is None:
            return _fallback(ctx, f"vague amount {f.vague_word!r} not in the lexicon")
        dist = stats.scaled(lk.dist, factor)
    return ctx.quantity(dist, currency=currency, basis=basis)


def _relative(ctx: _Ctx) -> Quantity | None:
    """§8.3 AMOUNT_RELATIVE: the reference fact's value × the lexicon's multiplier (a sampled product)."""
    f: AmountRelative = ctx.form
    lk = ctx.use(ctx.lex.relative(f.relation_word))
    if lk.dist is None:
        return _fallback(ctx, f"relation word {f.relation_word!r} not in the lexicon")
    ref = resolve_reference(f.reference_slot, ctx.profile, person=ctx.person, instance=ctx.instance)
    q, source = _fact_quantity(ref)
    if q is None or stats.is_categorical(q.dist) or stats.is_boolean(q.dist):
        ctx.notes["reference"] = {"slot": f.reference_slot, "found": ref is not None}
        return _fallback(ctx, f"reference {f.reference_slot!r} has no numeric value in the profile", "reference_missing")
    ref_dist, currency = q.dist, (q.unit if q.unit in CURRENCIES else "ZAR")
    if ctx.slot.fna_unit in MONEY_PERIOD and q.period in ("once", "monthly", "annual"):
        factor, note, mismatch = _period_factor(q.period, ctx.slot.fna_unit)
        if note and factor != 1.0:
            ctx.conversions.append(f"reference {note}")
            ref_dist = stats.scaled(ref_dist, factor)
        if mismatch:
            ctx.flag("period_mismatch")
    if currency != "ZAR":
        ctx.flag("foreign_currency")
    dist = _times(ref_dist, lk.dist, ctx.seed)
    ctx.notes["reference"] = {"slot": f.reference_slot, "fact_id": ref.fact_id, "source": source, "median": stats.median(ref_dist)}  # type: ignore[union-attr]
    ctx.conversions.append("reference value × relative multiplier")
    basis = q.basis if ctx.slot.fna_unit in MONEY_PERIOD else "na"
    return ctx.quantity(dist, currency=currency, basis=basis)


def _as_decimal(ctx: _Ctx, v: float) -> float:
    """Percentages become decimals (§8.3): 11.5 → 0.115, using the slot's bounds to tell them apart."""
    if ctx.slot.bounds:
        lo, hi = ctx.slot.bounds
        if lo <= v <= hi:
            return float(v)
        if lo <= v / 100.0 <= hi:
            ctx.conversions.append("percent → decimal: ÷100")
            return v / 100.0
        return float(v)
    if abs(v) > 1.0:
        ctx.conversions.append("percent → decimal: ÷100")
        return v / 100.0
    return float(v)


def _amount_as_number(ctx: _Ctx) -> Quantity | None:
    """Amount forms for a non-money numeric field (counts, or a mismatched age/years/rate): the number itself."""
    f, lex, unit = ctx.form, ctx.lex, ctx.slot.fna_unit
    if unit != "count":
        ctx.flag("form_slot_mismatch")
    if isinstance(f, AmountRelative):
        return _relative(ctx)
    if isinstance(f, AmountExact):
        dist: Dist = Point(value=float(f.value))
    elif isinstance(f, AmountApprox):
        dist = ctx.use(lex.approx(f.approx_word, f.value)).dist  # type: ignore[assignment]
    elif isinstance(f, AmountRange):
        dist = _range(f.low, f.high, lex.params.range_widen_rel)
    elif isinstance(f, AmountBound):
        dist = _bound(ctx, float(f.bound), f.direction, ctx.slot.population_prior)
    else:
        lk = ctx.use(lex.vague_count(f.vague_word))
        if lk.dist is None:
            return _fallback(ctx, f"vague count {f.vague_word!r} not in the lexicon")
        dist = lk.dist
    if unit == "decimal" and isinstance(dist, Point):
        dist = Point(value=_as_decimal(ctx, float(dist.value)))
    return ctx.quantity(dist)


def _amount(ctx: _Ctx) -> Quantity | None:
    unit = ctx.slot.fna_unit
    if unit in MONEY_PERIOD:
        return _money(ctx)
    if unit in ("count", "years", "months", "age", "year", "decimal", "date"):
        return _amount_as_number(ctx)
    return _fallback(ctx, f"an amount does not fit a {unit} field", "form_slot_mismatch")


# --------------------------------------------------------------------------- time: durations, ages, dates


def _anchor_base(ctx: _Ctx, anchor: str, *, as_year: bool) -> tuple[Dist | None, str]:
    """The time a duration counts from: the call (calendar year or decimal date) or a reference date."""
    if not anchor or anchor == "call":
        a = ctx.need_anchor()
        return (Point(value=float(a.year)) if as_year else Point(value=_decimal_year(a))), "call"
    ref = resolve_reference(anchor, ctx.profile, person=ctx.person, instance=ctx.instance)
    q, _ = _fact_quantity(ref)
    if q is None or q.unit not in ("date", "none") or stats.is_categorical(q.dist):
        return None, anchor
    exact = _exact_date(q.dist)
    if exact is not None:
        return Point(value=float(exact.year) if as_year else _decimal_year(exact)), ref.fact_id  # type: ignore[union-attr]
    return q.dist, ref.fact_id  # type: ignore[union-attr]


def _duration(ctx: _Ctx) -> Quantity | None:
    f: DurationFromAnchor = ctx.form
    unit = ctx.slot.fna_unit
    words = canon(f.vague_word).split()
    sign = -1.0 if ("ago" in words or "gelede" in words) else 1.0
    if f.form == "value":
        count: Dist = Point(value=float(f.value) * (sign if f.value > 0 else 1.0))
    else:
        cleaned = " ".join(w for w in words if w not in _DURATION_NOISE) or f.vague_word
        lk = ctx.use(ctx.lex.vague_count(cleaned))
        if lk.dist is None:
            return _fallback(ctx, f"vague duration {f.vague_word!r} not in the lexicon")
        count = stats.scaled(lk.dist, sign) if sign < 0 else lk.dist
    to_years = 1.0 if f.unit == "years" else 1.0 / 12.0
    years = stats.scaled(count, to_years) if to_years != 1.0 else count
    if f.unit == "months" and unit != "months":
        ctx.conversions.append("months → years: ÷12")
    ctx.notes["value_factor"] = to_years
    if unit == "years":
        return ctx.quantity(years)
    if unit == "months":
        if f.unit == "years":
            ctx.conversions.append("years → months: ×12")
        ctx.notes["value_factor"] = 1.0 if f.unit == "months" else 12.0
        return ctx.quantity(count if f.unit == "months" else stats.scaled(count, 12.0))
    if unit in ("year", "date"):
        base, src = _anchor_base(ctx, f.anchor, as_year=unit == "year")
        if base is None:
            return _fallback(ctx, f"anchor {f.anchor!r} has no date in the profile", "reference_missing")
        ctx.notes["anchor_offset"] = stats.median(base)
        ctx.conversions.append(f"duration from {src} → {'calendar year' if unit == 'year' else 'date'}")
        return ctx.quantity(_plus(base, years, ctx.seed))
    if unit == "age":
        anchor_ref = f.anchor if f.anchor and f.anchor != "call" else None
        ref = resolve_reference(anchor_ref, ctx.profile, person=ctx.person, instance=ctx.instance) if anchor_ref else None
        q, _ = _fact_quantity(ref)
        if q is not None and q.unit == "age":  # 'five years after I retire': an age plus years
            ctx.notes["anchor_offset"] = stats.median(q.dist)
            ctx.conversions.append(f"duration after the age in {ref.fact_id}")  # type: ignore[union-attr]
            return ctx.quantity(_plus(q.dist, years, ctx.seed))
        dob, _ = _dob(ctx, anchor_ref)
        if dob is None:
            return _fallback(ctx, "age from a duration needs the person's date of birth", "reference_missing")
        now = _age_on(dob, ctx.need_anchor())
        ctx.notes["anchor_offset"] = stats.median(now)
        ctx.conversions.append("age at the call (from date of birth) + duration")
        return ctx.quantity(_plus(now, years, ctx.seed))
    return _fallback(ctx, f"a duration does not fit a {unit} field", "form_slot_mismatch")


def _age(ctx: _Ctx) -> Quantity | None:
    f: AgeForm = ctx.form
    unit = ctx.slot.fna_unit
    if f.form == "value":
        age: Dist = Point(value=float(f.value))
    elif f.form == "range":
        w = ctx.lex.params.age_range_widen_years
        lo, hi = sorted((float(f.low), float(f.high)))
        age = Triangular(low=lo - w, mode=(lo + hi) / 2, high=hi + w)
        ctx.conversions.append(f"age range widened {w:g} years each side")
    else:
        lk = ctx.use(ctx.lex.age_band(f.vague_word))
        if lk.dist is None:
            return _fallback(ctx, f"age phrase {f.vague_word!r} not in the lexicon")
        age = lk.dist
    if unit == "age":
        return ctx.quantity(age)
    if unit == "date":  # §7.6: a date of birth from a stated age is 1 July of (anchor year − age), ±6 months
        centre = ctx.need_anchor().year + 0.5
        ctx.conversions.append("age → date of birth: 1 July of (anchor year − age), ±6 months")
        if isinstance(age, Point):
            dob = centre - float(age.value)
            return ctx.quantity(stats.uniform_discrete(dob - 0.5, dob + 0.5))
        return ctx.quantity(_minus(Point(value=centre), _plus(age, stats.uniform_discrete(-0.5, 0.5), ctx.seed), ctx.seed))
    if unit in ("year", "years", "months"):
        dob, _ = _dob(ctx)
        if dob is None:
            return _fallback(ctx, f"an age for a {unit} field needs the date of birth", "reference_missing")
        if unit == "year":
            exact = _exact_date(dob)
            born = Point(value=float(exact.year)) if exact else stats.composite(np.floor, dob, seed=ctx.seed)
            ctx.conversions.append("age → calendar year: birth year + age")
            return ctx.quantity(_plus(born, age, ctx.seed))
        now = _age_on(dob, ctx.need_anchor())
        years = _minus(age, now, ctx.seed)
        ctx.notes["anchor_offset"] = stats.median(now)
        ctx.conversions.append("age → years from now: age − current age")
        if unit == "months":
            ctx.conversions.append("years → months: ×12")
            return ctx.quantity(stats.scaled(years, 12.0))
        return ctx.quantity(years)
    return _fallback(ctx, f"an age does not fit a {unit} field", "form_slot_mismatch")


def _month_days(year: int, month: int) -> int:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - date(year, month, 1)).days


def _date_dist(f: DateAbs) -> tuple[Dist, date | None]:
    """(decimal-year distribution, exact date or None). month/day 0 = not said (§8.1)."""
    month = f.month if 1 <= f.month <= 12 else 0
    day = f.day if month and 1 <= f.day <= _month_days(f.year, month) else 0
    if month and day:
        d = date(f.year, month, day)
        return Point(value=_decimal_year(d)), d
    if month:
        start = _decimal_year(date(f.year, month, 1))
        end = _decimal_year(date(f.year, month, _month_days(f.year, month)))
        return stats.uniform_discrete(start, end, n=5), None
    return stats.uniform_discrete(f.year + 1 / 24, f.year + 23 / 24, n=12), None


def _date(ctx: _Ctx) -> Quantity | None:
    f: DateAbs = ctx.form
    unit = ctx.slot.fna_unit
    dec, exact = _date_dist(f)
    if unit == "date":
        return ctx.quantity(Point(value=exact.isoformat()) if exact else dec)
    if unit == "year":
        return ctx.quantity(Point(value=float(f.year)))
    if unit == "age":
        dob, _ = _dob(ctx)
        if dob is None:
            return _fallback(ctx, "an age from a date needs the date of birth", "reference_missing")
        ctx.conversions.append("date → age: age on that date")
        if exact is not None and _exact_date(dob) is not None:
            return ctx.quantity(_age_on(dob, exact))
        born = Point(value=_decimal_year(_exact_date(dob))) if _exact_date(dob) else dob  # type: ignore[arg-type]
        return ctx.quantity(_minus(dec, born, ctx.seed))
    if unit in ("years", "months"):
        years = stats.shifted(dec, -_decimal_year(ctx.need_anchor()))
        ctx.conversions.append("date → years from the call")
        if unit == "months":
            return ctx.quantity(stats.scaled(years, 12.0))
        return ctx.quantity(years)
    return _fallback(ctx, f"a date does not fit a {unit} field", "form_slot_mismatch")


# --------------------------------------------------------------------------- rates and probabilities


def _rate(ctx: _Ctx) -> Quantity | None:
    f: RateForm = ctx.form
    unit = ctx.slot.fna_unit
    if unit == "decimal":
        if f.form == "value":
            ctx.real_or_nominal = f.real_or_nominal
            return ctx.quantity(Point(value=_as_decimal(ctx, f.value)))
        lk = ctx.use(ctx.lex.rate(f.vague_word))
        if lk.dist is None:
            return _fallback(ctx, f"rate phrase {f.vague_word!r} not in the lexicon")
        if lk.section in ("rate_words", "rate_templates"):
            ctx.real_or_nominal = "real"  # §8.2: the lexicon's rate words are annual real rates
            if f.real_or_nominal == "nominal":
                ctx.conversions.append("lexicon rate words are real; the form said nominal")
        else:
            ctx.real_or_nominal = f.real_or_nominal
        return ctx.quantity(lk.dist)
    if unit in ("probability", "boolean"):
        if f.form == "value":
            return ctx.quantity(Bernoulli(p=min(1.0, max(0.0, _as_decimal(ctx, f.value) if f.value > 1 else f.value))))
        lk = ctx.use(ctx.lex.likelihood(f.vague_word))
        if lk.likelihood is None:
            return _fallback(ctx, f"likelihood {f.vague_word!r} not in the lexicon")
        ctx.notes["likelihood"] = lk.likelihood.model_dump()
        return ctx.quantity(Bernoulli(p=lk.likelihood.mean))
    return _fallback(ctx, f"a rate does not fit a {unit} field", "form_slot_mismatch")


def _probability_event(ctx: _Ctx) -> Quantity | None:
    """§8.3: P(event) from the likelihood word, sampled as Bernoulli(mean); mean/low/high kept for review."""
    f: ProbabilityEvent = ctx.form
    if ctx.slot.fna_unit not in ("boolean", "probability"):
        return _fallback(ctx, f"an event probability does not fit a {ctx.slot.fna_unit} field", "form_slot_mismatch")
    ctx.notes["event"] = f.event
    lk = ctx.use(ctx.lex.likelihood(f.likelihood_word))
    if lk.likelihood is None:
        return _fallback(ctx, f"likelihood word {f.likelihood_word!r} not in the lexicon")
    ctx.notes["likelihood"] = lk.likelihood.model_dump()
    return ctx.quantity(Bernoulli(p=lk.likelihood.mean))


# --------------------------------------------------------------------------- categories, booleans, text


def match_category(label: str, categories: list[str], lex: Lexicon) -> tuple[str, str] | None:
    """(category, how) for a spoken label: exact, lexicon synonym, fuzzy (typos) or contained; None if outside the union."""
    c = canon(label)
    by = {canon(x): x for x in categories}
    if c in by:
        return by[c], "exact"
    syn = lex.category_synonym(label, categories)
    if syn is not None:
        return syn, "synonym"
    if c and by:
        best = process.extractOne(c, list(by), scorer=fuzz.ratio)
        if best and best[1] >= 90:
            return by[best[0]], "fuzzy"
        if set(c.split()) & _NEGATION:  # 'not conservative at all' never matches 'conservative'
            return None
        inside = [k for k in by if k and f" {k} " in f" {c} " and k not in ("other",)]
        if inside:
            longest = max(inside, key=len)
            if sum(1 for k in inside if len(k) == len(longest)) == 1:
                return by[longest], "contained"
    return None


def _category_probs(ctx: _Ctx, label: str, word: str, *, kind: Literal["category", "boolean"] = "category") -> dict[str, float] | None:
    """§8.3 categorical with a certainty word: the named level gets p; on an ordered scale the rest goes
    to the adjacent levels ('fairly conservative' → 0.6 / 0.4), otherwise it follows the population prior."""
    cats = list(ctx.slot.categories or [])
    if not cats:
        ctx.conversions.append("slot lists no categories: the label stands")
        return {label: 1.0}
    m = match_category(label, cats, ctx.lex)
    if m is None:
        ctx.notes["spoken_label"] = label
        if "other" in cats:
            ctx.flag("label_mapped_to_other")
            named = "other"
        else:
            ctx.flag("label_unmatched")
            return None
    else:
        named, how = m
        if how != "exact":
            ctx.conversions.append(f"label {label!r} → {named!r} ({how})")
    ctx.notes["category"] = named
    p = float(ctx.use(ctx.lex.certainty(word, kind=kind)).value)  # type: ignore[arg-type]
    if p >= 1.0:
        return {named: 1.0}
    scale = ctx.lex.ordered_scale(cats)
    if scale and named in scale:
        i = scale.index(named)
        nbrs = [scale[j] for j in (i - 1, i + 1) if 0 <= j < len(scale)]
        ctx.conversions.append("certainty smoothing over adjacent levels")
        return {named: p, **{n: (1.0 - p) / len(nbrs) for n in nbrs}}
    others = [x for x in cats if x != named]
    prior = ctx.slot.population_prior
    w = {x: 1.0 for x in others}
    if prior is not None and stats.is_categorical(prior):
        pp = stats.categorical_probs(prior)
        if sum(pp.get(x, 0.0) for x in others) > 0:
            w = {x: pp.get(x, 0.0) for x in others}
    total = sum(w.values())
    ctx.conversions.append(
        "certainty remainder spread over the other categories" + (" by the population prior" if w != {x: 1.0 for x in others} else "")
    )
    out = {named: p, **{x: (1.0 - p) * v / total for x, v in w.items() if v > 0}}
    return out


def _categorical(ctx: _Ctx, probs: dict[str, float]) -> Quantity:
    probs = {k: v for k, v in probs.items() if v > 1e-12}
    total = sum(probs.values())
    return ctx.quantity(Categorical(probs={k: v / total for k, v in probs.items()}))


def _yes_no(label: str) -> bool | None:
    c = canon(label)
    return True if c in _YES else (False if c in _NO else None)


def _category(ctx: _Ctx) -> Quantity | None:
    f: CategoryForm = ctx.form
    unit = ctx.slot.fna_unit
    if unit == "category":
        probs = _category_probs(ctx, f.label, f.certainty_word)
        if probs is None:
            return _unmatched(ctx)
        return _categorical(ctx, probs)
    if unit in ("boolean", "probability"):
        v = _yes_no(f.label)
        if v is None:
            return _fallback(ctx, f"label {f.label!r} is not a yes/no answer", "form_slot_mismatch")
        p = float(ctx.use(ctx.lex.certainty(f.certainty_word, kind="boolean")).value)  # type: ignore[arg-type]
        return ctx.quantity(Bernoulli(p=p if v else 1.0 - p))
    if unit == "text":
        return ctx.quantity(Point(value=f.label))
    return _fallback(ctx, f"a category does not fit a {unit} field", "form_slot_mismatch")


def _unmatched(ctx: _Ctx) -> Quantity | None:
    dist = _prior(ctx, "spoken label outside the slot's categories")
    if dist is None and ctx.slot.categories:
        ctx.conversions.append("no prior: uniform over the slot's categories")
        cats = ctx.slot.categories
        dist = Categorical(probs={c: 1.0 / len(cats) for c in cats})
    return None if dist is None else ctx.quantity(dist)


def _boolean(ctx: _Ctx) -> Quantity | None:
    """§8.3: Bernoulli(0.97) for a plain yes/no; with a certainty word, the lexicon's certainty."""
    f: BooleanForm = ctx.form
    unit = ctx.slot.fna_unit
    if unit in ("boolean", "probability"):
        p = float(ctx.use(ctx.lex.certainty(f.certainty_word, kind="boolean")).value)  # type: ignore[arg-type]
        return ctx.quantity(Bernoulli(p=p if f.value else 1.0 - p))
    if unit == "category" and ctx.slot.categories:
        want = _YES if f.value else _NO
        label = next((c for c in ctx.slot.categories if canon(c) in want), None)
        if label is None:
            return _fallback(ctx, "a yes/no answer for a category field without yes/no", "form_slot_mismatch")
        ctx.conversions.append(f"yes/no → {label!r}")
        probs = _category_probs(ctx, label, f.certainty_word, kind="boolean")
        return _categorical(ctx, probs) if probs else _unmatched(ctx)
    return _fallback(ctx, f"a yes/no answer does not fit a {unit} field", "form_slot_mismatch")


def _text(ctx: _Ctx) -> Quantity | None:
    f: TextForm = ctx.form
    unit = ctx.slot.fna_unit
    if unit == "text":
        return ctx.quantity(Point(value=f.text))
    if unit == "category":
        probs = _category_probs(ctx, f.text, "")
        return _categorical(ctx, probs) if probs else _unmatched(ctx)
    return _fallback(ctx, f"free text does not fit a {unit} field", "form_slot_mismatch")


_HANDLERS = {
    "AMOUNT_EXACT": _amount,
    "AMOUNT_APPROX": _amount,
    "AMOUNT_RANGE": _amount,
    "AMOUNT_BOUND": _amount,
    "AMOUNT_RELATIVE": _amount,
    "AMOUNT_VAGUE": _amount,
    "DATE_ABS": _date,
    "DURATION_FROM_ANCHOR": _duration,
    "AGE": _age,
    "RATE": _rate,
    "PROBABILITY_EVENT": _probability_event,
    "CATEGORY": _category,
    "BOOLEAN": _boolean,
    "TEXT": _text,
}


def normalize_value(
    form: IntermediateForm | Mapping[str, Any],
    slot: SlotDef,
    profile: Profile | None,
    lex: Lexicon,
    *,
    anchor_date: date | None = None,
    fact_id: str | None = None,
    person: str | None = None,
    assumptions: Assumptions | None = None,
    priced_year: float | None = None,
    seed: int = 0,
) -> Normalized:
    """Turn one intermediate form into a ``Quantity`` in the slot's FnaDocument unit (§8.3).

    ``fact_id`` is the concrete fact being filled ('retirement.client.retirementAge',
    'dependants[].tertiaryStartAge#person.daughter_1'): it names the person or dependant whose date
    of birth, references and instance apply. ``anchor_date`` defaults to the profile's (the call
    date). ``assumptions`` default to the FNA defaults (cpi 4.5%, educationInflation 6.5%).
    ``priced_year`` is the year a spoken amount is priced in ('R400,000 in 2030'); for fields held in
    today's money it is deflated to the call year. A raw dict form may carry it as 'priced_year'.
    """
    if isinstance(form, Mapping):
        data = dict(form)
        if priced_year is None and data.get("priced_year"):
            priced_year = float(data["priced_year"])
        data.pop("priced_year", None)
        form = _FORM.validate_python(data)
    anchor = anchor_date or (profile.anchor_date if profile is not None else None)
    ctx = _Ctx(
        form=form,
        slot=slot,
        profile=profile,
        lex=lex,
        anchor=anchor,
        person=person or (person_of(fact_id) if fact_id else None),
        instance=fact_id.split("#", 1)[1] if fact_id and "#" in fact_id else None,
        assumptions=assumptions or Assumptions(),
        priced_year=priced_year,
        seed=seed,
    )
    if slot.allowed_forms and form.type not in slot.allowed_forms:
        ctx.flag("form_not_allowed")
    quantity = _HANDLERS[form.type](ctx)
    if quantity is not None and slot.fna_unit in MONEY_PERIOD and quantity.unit not in CURRENCIES:
        quantity = quantity.model_copy(update={"unit": "ZAR"})
    normalization: dict[str, Any] = {
        "form_type": form.type,
        "form": form.model_dump(mode="json"),
        "lexicon_version": lex.version,
        "slot_id": slot.slot_id,
        "fna_unit": slot.fna_unit,
        "lexicon": [lk.record() for lk in ctx.lookups],
        "conversions": ctx.conversions,
    }
    if priced_year is not None:
        normalization["priced_year"] = priced_year
    normalization.update(ctx.notes)
    for k, v in list(normalization.items()):
        if isinstance(v, float) and not math.isfinite(v):
            normalization[k] = None
    return Normalized(quantity=quantity, normalization=normalization, flags=ctx.flags)


__all__ = [
    "MONEY_PERIOD",
    "NormalizationFlag",
    "Normalized",
    "QUANTITY_UNIT",
    "match_category",
    "normalize_value",
    "period_factor",
    "quantity_unit",
    "resolve_reference",
    "todays_money_rate",
]
