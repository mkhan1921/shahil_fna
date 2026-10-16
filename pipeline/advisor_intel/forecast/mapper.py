"""Mapping the profile to forecast inputs (spec §12.2).

Every forecast-bound fact (``SlotDef.forecast_binding``) gets a ``FactSampler``; every other field comes from
``binding.materialise(profile, "median", base=base_document)``, the same document the adviser reviews.

Which value a fact contributes (§4.3, §12.2):

========================================================  =====================================================
Fact                                                      Per-path value
========================================================  =====================================================
reviewed (accepted / edited), auto-accepted, or source     the fact's value distribution (q = 1)
``prior_fna`` / ``reviewer``
stated / implied / contradicted, otherwise                 the extraction-risk mixture: the fact's value with
                                                           probability q = p_correct, else the slot's population
                                                           prior. The indicator is drawn explicitly (group E).
not_discussed with a prior FNA (or CRM) value              that value, trusted
not_discussed from a default, or explicitly_unknown        the slot's population prior (group C: unknown content)
without a prior
explicitly_unknown with a prior value                      the prior value
rejected                                                   as absent: its prior value if it had one, else the
                                                           median document's value
========================================================  =====================================================

A fact with several hypotheses samples the hypothesis first and then within it (``sampling.ppf``).

Special cases:

- **Income basis unknown**: per path a fair coin (group C) decides whether the stated amount was gross or
  take-home; take-home amounts are grossed up through the ``TaxModel`` (the FNA engine), never here.
- **Foreign currency**: USD/GBP/EUR amounts are converted at the CMA spot and flagged; the engine moves them
  with each path's real USDZAR at the time the money is spent, so a weaker rand makes offshore costs dearer.
- **Events and income changes** (forecast-only slots): occurrence ~ Bernoulli(p), timing and cash impact,
  each from its fact. An offshore-education event linked to a dependant shares its single occurrence draw
  with that dependant's tertiary plan: if it happens, the offshore cost (and its FX exposure) replaces the
  local cost, so location, cost and FX move together.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel

from .. import binding, stats
from ..models import fna as F
from ..models.dists import Dist, Point, Quantity
from ..models.facts import Fact
from ..models.profile import Profile
from ..schema.slots import Catalog, SlotDef, generic_slot_id, load_catalog, person_of
from .config import CMA, ForecastConfig
from .sampling import FactUniforms, Uniforms, ValueKind, as_float, fact_uniforms, ppf, value_kind
from .tax import TaxModel

TRUSTED_REVIEW = frozenset({"accepted", "edited"})
TRUSTED_SOURCES = frozenset({"prior_fna", "reviewer"})
RECORD_SOURCES = frozenset({"prior_fna", "crm"})  # systems of record: a value not discussed on the call stands
PRE_RETIREMENT_FUNDS = ("pension", "provident", "retirement-annuity", "preservation-pension", "preservation-provident")
FALLBACK_AGE = 40  # lib/fna/analysis.ts FALLBACK_AGE: exact age 40.5 when no date of birth is known
DAYS_PER_YEAR = 365.2425  # lib/fna/idNumber.ts exactAge

CASH_TYPES = ("cash", "money-market")
INVESTMENT_TYPES = ("unit-trust", "shares", "endowment", "crypto")
PROPERTY_TYPES = ("primary-residence", "residential-property", "commercial-property")
LIFESTYLE_TYPES = ("vehicle", "personal-effects", "other")
EVENT_DISCRETIONARY_KINDS = ("large-purchase",)

Override = Any  # a value (fixed on every path), a Dist or Quantity (sampled with the fact's own draws), or "prior"


# --------------------------------------------------------------------------- money


@dataclass
class Money:
    """A per-path amount in today's rand: a fixed part and a part that moves with the real USDZAR rate."""

    fixed: np.ndarray
    linked: np.ndarray

    def __post_init__(self) -> None:
        self.has_fx = bool(np.any(self.linked))

    @staticmethod
    def zeros(n: int) -> Money:
        return Money(np.zeros(n), np.zeros(n))

    @staticmethod
    def of(values: np.ndarray, fx: np.ndarray | None = None) -> Money:
        v = np.asarray(values, dtype=float)
        if fx is None or not np.any(fx):
            return Money(v.copy(), np.zeros_like(v))
        return Money(np.where(fx, 0.0, v), np.where(fx, v, 0.0))

    def total(self) -> np.ndarray:
        """The amount at today's exchange rate."""
        return self.fixed + self.linked if self.has_fx else self.fixed

    def at(self, fx_index: np.ndarray | float) -> np.ndarray:
        """The amount when the real USDZAR has moved by ``fx_index`` since today (read-only when no FX part)."""
        return self.fixed + self.linked * fx_index if self.has_fx else self.fixed

    def __add__(self, other: Money) -> Money:
        return Money(self.fixed + other.fixed, self.linked + other.linked)

    def scale(self, k: np.ndarray | float) -> Money:
        return Money(self.fixed * k, self.linked * k)

    def where(self, mask: np.ndarray) -> Money:
        return Money(np.where(mask, self.fixed, 0.0), np.where(mask, self.linked, 0.0))


# --------------------------------------------------------------------------- samplers


@dataclass(frozen=True)
class Draw:
    """One fact's values on every path."""

    values: np.ndarray  # floats (booleans 0/1, dates as decimal years) or object labels
    fx: np.ndarray | None = None  # paths whose value is a foreign amount converted at today's spot
    extracted: np.ndarray | None = None  # paths that used the extracted value (None: no extraction indicator)
    net: np.ndarray | None = None  # paths whose income amount is take-home pay, still to be grossed up


@dataclass(frozen=True)
class FactSampler:
    """How one fact becomes per-path values (§12.2)."""

    fact_id: str
    slot_id: str
    path: str  # concrete FnaDocument path (with the row id), or the forecast-only path
    tier: str
    kind: ValueKind
    rule: str  # the §4.3/§12.2 rule that applied, for review and tests
    q: float = 1.0  # P(use the extracted value); 1 when trusted
    content: Dist | None = None  # the fact's value in the slot's unit (a foreign currency when ``currency``)
    prior: Dist | None = None  # the slot's population prior
    currency: str | None = None  # USD / GBP / EUR when the content is a foreign amount
    scale: float = 1.0  # period conversion of the content into the slot's unit
    basis: str = "na"  # gross / net / unknown for income amounts
    sign: float = 1.0  # applied to prior draws (an event's cost is negative)
    period: str = "none"  # the content's period (events: recurring or once)
    entity_refs: tuple[str, ...] = ()
    instance_key: str | None = None

    @property
    def mode(self) -> Literal["content", "prior", "mixture"]:
        if self.content is None:
            return "prior"
        if self.q >= 1 or self.prior is None:
            return "content"
        return "mixture"

    @property
    def random(self) -> bool:
        """Whether the fact's value varies by path (it can then drive the forecast's variance)."""
        if self.mode == "prior":
            return not isinstance(self.prior, Point)
        if self.mode == "mixture":
            return True
        return not isinstance(self.content, Point) or (self.basis == "unknown")

    def draw(self, u: FactUniforms, spot: Callable[[str], float]) -> Draw:
        n = len(u.c1)
        if self.content is None:
            vals = ppf(self.prior, u.c1) if self.prior is not None else np.zeros(n)  # type: ignore[arg-type]
            return Draw(self._signed(vals))
        vals = ppf(self.content, u.c1)
        if self.kind == "numeric":
            vals = vals * (self.scale * (spot(self.currency) if self.currency else 1.0))
        use = None
        if self.mode == "mixture":
            use = u.e1 < self.q
            vals = np.where(use, vals, self._signed(ppf(self.prior, u.e2)))  # type: ignore[arg-type]
        fx = None
        if self.currency:
            fx = np.ones(n, dtype=bool) if use is None else use
        net = None
        if self.basis in ("net", "unknown"):
            coin = np.ones(n, dtype=bool) if self.basis == "net" else (u.c2 < 0.5)
            net = coin if use is None else (coin & use)
        return Draw(vals, fx, use, net)

    def _signed(self, vals: np.ndarray) -> np.ndarray:
        return vals * self.sign if (self.sign != 1.0 and vals.dtype != object) else vals

    def fixed(self, value: Any, rule: str = "override") -> FactSampler:
        return dataclasses.replace(self, content=Point(value=value), q=1.0, currency=None, scale=1.0, basis="na", rule=rule)

    def with_content(self, dist: Dist, rule: str = "override") -> FactSampler:
        return dataclasses.replace(self, content=dist, q=1.0, currency=None, scale=1.0, basis="na", rule=rule)

    def with_prior(self, rule: str = "override_prior") -> FactSampler:
        return dataclasses.replace(self, content=None, q=1.0, rule=rule)


def _scale(slot: SlotDef, q: Quantity) -> float:
    """Convert a stated period into the slot's unit (the normaliser should have done it already)."""
    if slot.fna_unit == "rand_monthly" and q.period == "annual":
        return 1 / 12
    if slot.fna_unit == "rand_annual" and q.period == "monthly":
        return 12.0
    return 1.0


def _currency(q: Quantity | None) -> str | None:
    return q.unit if q is not None and q.unit in ("USD", "GBP", "EUR") else None


def build_sampler(fact: Fact, slot: SlotDef, *, path: str) -> FactSampler | None:
    """The §12.2 sampler for one fact; None when the fact is absent from the forecast (median document value)."""
    prior = slot.population_prior
    common: dict[str, Any] = {
        "fact_id": fact.fact_id,
        "slot_id": fact.slot_id,
        "path": path,
        "tier": slot.sensitivity_tier,
        "entity_refs": tuple(fact.entity_refs),
        "instance_key": fact.instance_key,
    }
    record = fact.prior_value
    if record is None and fact.source in RECORD_SOURCES and fact.value is not None:
        record = fact.value

    def content(q: Quantity, rule: str, p: float = 1.0) -> FactSampler:
        return FactSampler(
            **common,
            kind=value_kind(q.dist),
            rule=rule,
            q=p,
            content=q.dist,
            prior=prior,
            currency=_currency(q),
            scale=_scale(slot, q),
            basis=q.basis if slot.slot_id.endswith("grossMonthly") else "na",
            sign=_prior_sign(slot, q),
            period=q.period,
        )

    def population(rule: str) -> FactSampler | None:
        if prior is None:
            return None
        return FactSampler(**common, kind=value_kind(prior), rule=rule, prior=prior, sign=_prior_sign(slot, None))

    if fact.review.status == "rejected":
        return content(record, "rejected_prior_value") if fact.prior_value is not None else None
    if fact.status == "not_discussed":
        return content(record, "not_discussed_prior_value") if record is not None else population("not_discussed_population_prior")
    if fact.status == "explicitly_unknown" or fact.value is None:
        return content(record, "unknown_prior_value") if record is not None else population("unknown_population_prior")
    trusted = fact.review.status in TRUSTED_REVIEW or fact.auto_accepted or fact.source in TRUSTED_SOURCES
    q = 1.0 if trusted else float(min(1.0, max(0.0, fact.p_correct)))
    if trusted:
        return content(fact.value, "trusted")
    if prior is None:
        return content(fact.value, "no_population_prior")
    return content(fact.value, "extraction_mixture", q)


def _prior_sign(slot: SlotDef, q: Quantity | None) -> float:
    """Event cash impacts are signed (a cost is negative); the population prior holds magnitudes."""
    if slot.slot_id != "event[].cashImpact":
        return 1.0
    if q is not None:
        try:
            return -1.0 if stats.median(q.dist) < 0 else 1.0
        except TypeError:
            return 1.0
    return -1.0


# --------------------------------------------------------------------------- engine inputs


@dataclass
class IncomeChangeIn:
    occurs: np.ndarray  # (P,) bool
    step: np.ndarray  # (P,) int: the year index from which the new income applies
    new_gross_monthly: Money


@dataclass
class PersonIn:
    key: str
    age0: np.ndarray  # exact age today
    retired_now: np.ndarray  # bool: retired or past the retirement age (FNA computeRetirement)
    retirement_age: np.ndarray
    planning_age: np.ndarray
    self_employed: np.ndarray  # bool
    uif: np.ndarray  # bool
    gross_monthly: Money  # salary or business income, grossed up where stated as take-home
    bonus_annual: Money
    other_taxable_monthly: Money
    non_taxable_monthly: Money
    medical_payroll_monthly: np.ndarray
    medical_members: np.ndarray
    other_payroll_monthly: np.ndarray
    growth_real: np.ndarray  # real growth of earnings: salary escalation over CPI, or the business growth rate
    target_monthly_today: np.ndarray  # FNA retirement target (percent of gross equivalent, or amount)
    other_income_monthly: np.ndarray  # retirement.{p}.otherIncomeMonthly
    capital0: np.ndarray  # pre-retirement and living-annuity funds plus retirement-earmarked assets
    contrib_payroll_monthly: np.ndarray  # employee contributions through payroll
    contrib_own_monthly: np.ndarray  # employee contributions paid by the household (retirement annuities)
    contrib_employer_monthly: np.ndarray
    contrib_earmarked_monthly: np.ndarray  # contributions to retirement-earmarked assets (household-paid)
    share: np.ndarray  # share of household living expenses carried by this person's income before retirement
    rental_monthly: Money  # income from assets attributed to this person (taxable)
    changes: list[IncomeChangeIn] = field(default_factory=list)

    @property
    def net_target_today(self) -> np.ndarray:
        """FNA netTargetToday: the target less other retirement income, never negative."""
        return np.maximum(0.0, self.target_monthly_today - self.other_income_monthly)


@dataclass
class LiabilityIn:
    key: str
    balance: np.ndarray  # nominal rand today
    rate: np.ndarray  # nominal annual, compounded monthly
    repayment: np.ndarray  # nominal monthly instalment


@dataclass
class PremiumIn:
    person: int  # index into ForecastInput.persons
    monthly: np.ndarray
    escalation: float  # nominal annual premium escalation


@dataclass
class OffshoreStudyIn:
    occurs: np.ndarray  # bool: the single occurrence draw shared by location, cost and FX
    start_step: np.ndarray  # int, or -1 for the dependant's own tertiary start
    annual_cost: Money


@dataclass
class DependantIn:
    key: str
    age0: np.ndarray  # completed years today (FNA ageOn)
    is_child: bool
    school: bool  # school type is not "none"
    school_fees_annual: Money
    tertiary: np.ndarray  # bool
    tertiary_start_age: np.ndarray
    tertiary_years: np.ndarray
    tertiary_cost_annual: Money
    support_monthly: Money
    support_years: np.ndarray
    offshore: OffshoreStudyIn | None = None


@dataclass
class GoalIn:
    key: str  # the goal's result key (its cost fact id, else goals[<row id>])
    cost: Money  # today's money
    step: np.ndarray  # int: years from today to the target year
    savings0: np.ndarray
    monthly_nominal: np.ndarray  # contributions do not escalate (FNA computeGoals)


@dataclass
class EventIn:
    key: str
    occurs: np.ndarray  # bool
    step: np.ndarray  # int
    amount: Money  # signed, today's money: a lump sum, or a yearly amount when recurring
    recurring: bool
    essential: bool


@dataclass
class HouseholdIn:
    cash0: np.ndarray
    discretionary0: np.ndarray
    offshore0: np.ndarray
    tfsa0: np.ndarray
    education0: np.ndarray
    property0: np.ndarray
    business0: np.ndarray
    loan_account0: np.ndarray
    lifestyle0: dict[str, np.ndarray]  # by asset type
    contrib_cash: np.ndarray  # monthly, today's money
    contrib_discretionary: np.ndarray
    contrib_offshore: np.ndarray
    contrib_tfsa: np.ndarray
    contrib_education: np.ndarray  # pooled education savings (dependants and education-earmarked assets)
    living_essential: Money  # monthly: lines that continue
    living_discretionary: Money
    children_essential: Money  # monthly: child-related lines, until the youngest child turns children_end_age
    children_discretionary: Money
    support_essential: Money  # monthly: family-support lines, until the supported dependants' years end
    children_end_step: np.ndarray  # int, -1 when the lines never end
    support_end_step: np.ndarray  # int, -1 when the lines never end
    support_from_dependants: bool  # no family-support expense line: dependants' monthly support is paid instead
    school_from_dependants: bool  # no child-related expense lines: dependants' school fees are paid instead


@dataclass
class Assumptions:
    cpi: float
    salary_escalation: float
    pre_retirement_return: float
    post_retirement_return: float
    risk_capital_return: float
    education_inflation: float

    def real(self, nominal: float) -> float:
        return (1 + nominal) / (1 + self.cpi) - 1


@dataclass
class ForecastInput:
    """Everything the engine reads, per path (P,): today's money unless a field says otherwise."""

    n: int
    anchor: date
    persons: list[PersonIn]
    household: HouseholdIn
    liabilities: list[LiabilityIn]
    premiums: list[PremiumIn]
    dependants: list[DependantIn]
    goals: list[GoalIn]
    events: list[EventIn]
    assumptions: Assumptions
    accumulation_mix: str | None  # the risk-profile category whose mix is used, or None for the glide path
    n_years: int
    primary_age_int: int


# --------------------------------------------------------------------------- the plan


@dataclass
class EventSpec:
    key: str
    occurs: str | None
    year: str | None
    amount: str | None
    kind: str
    dependant_row: str | None


@dataclass
class ChangeSpec:
    person: str
    key: str
    occurs: str | None
    year: str | None
    amount: str | None


def _js_round(x: np.ndarray) -> np.ndarray:
    """Math.round: halves round up (Python's round() is banker's rounding)."""
    return np.floor(np.asarray(x, dtype=float) + 0.5)


def exact_age(dob: str | float | None, anchor: date) -> float | None:
    """FNA exactAge: days since birth / 365.2425 on the anchor date; decimal-year dates convert to days first."""
    if dob is None or dob == "":
        return None
    if isinstance(dob, str):
        try:
            d = date.fromisoformat(dob[:10])
        except ValueError:
            return None
        return (anchor.toordinal() - d.toordinal()) / DAYS_PER_YEAR
    return float(_ages_from_years(np.array([float(dob)]), anchor)[0])


def _ordinal_jan1(year: np.ndarray) -> np.ndarray:
    y = year - 1
    return 365 * y + y // 4 - y // 100 + y // 400 + 1


def _ages_from_years(years: np.ndarray, anchor: date) -> np.ndarray:
    """Exact ages from decimal-year dates of birth (stats.iso_to_year's convention)."""
    y = np.floor(years).astype(np.int64)
    start = _ordinal_jan1(y)
    days = _ordinal_jan1(y + 1) - start
    ordinal = start + (years - y) * days
    return (anchor.toordinal() - ordinal) / DAYS_PER_YEAR


def _completed_years(dob: str | None, anchor: date) -> float | None:
    """FNA ageOn: completed years on the anchor date."""
    if not dob:
        return None
    try:
        d = date.fromisoformat(dob[:10])
    except ValueError:
        return None
    age = anchor.year - d.year - ((anchor.month, anchor.day) < (d.month, d.day))
    return float(age)


@dataclass
class ForecastPlan:
    """The static part of a forecast: the median document, one sampler per bound fact, and the structure."""

    profile_id: str
    anchor: date
    catalog: Catalog
    config: ForecastConfig
    cma: CMA
    doc: dict[str, Any]
    samplers: dict[str, FactSampler]
    events: list[EventSpec]
    changes: list[ChangeSpec]
    persons: list[str]
    goal_keys: dict[str, str]
    accumulation_mix: str | None
    trace: dict[str, str] = field(default_factory=dict)  # fact id → rule, including facts left to the document

    # ------------------------------------------------------------------ randomness

    @property
    def fact_ids(self) -> list[str]:
        return sorted(self.samplers)

    def random_facts(self) -> list[str]:
        return [fid for fid in self.fact_ids if self.samplers[fid].random]

    def tier_a_random(self) -> list[str]:
        return [fid for fid in self.random_facts() if self.samplers[fid].tier == "A"]

    def uniforms(self, seed: int, n: int, *, matrix: str = "") -> Uniforms:
        """Every fact's C and E uniforms for ``n`` paths, from (seed, matrix, fact id) streams (§12.5, §10.2)."""
        return Uniforms(n, {fid: fact_uniforms(seed, fid, n, matrix=matrix) for fid in self.fact_ids})

    def spot(self, currency: str | None) -> float:
        return self.cma.fx.spot(currency) if currency else 1.0

    def with_overrides(self, overrides: dict[str, Override] | None) -> ForecastPlan:
        """A copy with facts (by fact id) or document fields (by concrete path) fixed, resampled or reset to the prior."""
        if not overrides:
            return self
        samplers = dict(self.samplers)
        by_path = {s.path: fid for fid, s in samplers.items()}
        for key, value in overrides.items():
            fid = key if key in samplers else by_path.get(key)
            if fid is None:
                slot = self.catalog.slot(_generic(key))
                samplers[key] = _override(FactSampler(fact_id=key, slot_id=slot.slot_id, path=key, tier=slot.sensitivity_tier,
                                                      kind="numeric", rule="override", prior=slot.population_prior), value)
                continue
            samplers[fid] = _override(samplers[fid], value)
        return dataclasses.replace(self, samplers=samplers)

    def with_samplers(self, samplers: dict[str, FactSampler]) -> ForecastPlan:
        """A copy with some samplers replaced (sensitivity runs move one fact at a time)."""
        return dataclasses.replace(self, samplers={**self.samplers, **samplers})

    # ------------------------------------------------------------------ realisation

    def draws(self, uniforms: Uniforms) -> dict[str, Draw]:
        return {s.path: s.draw(uniforms.facts[fid], self.spot) for fid, s in self.samplers.items()}

    def realise(self, uniforms: Uniforms, tax_model: TaxModel) -> ForecastInput:
        """Per-path engine inputs from one set of draws."""
        return _Builder(self, self.draws(uniforms), uniforms.n, tax_model).build()


def _generic(path: str) -> str:
    """'dependants[abc].tertiaryCostAnnual' → 'dependants[].tertiaryCostAnnual'; 'income.client.x' → 'income.{p}.x'."""
    pp = binding.parse_path(path)
    if pp.key is not None and pp.container in binding.DICT_CONTAINERS:
        return f"{pp.container}[]" if pp.container == "expenses.items" else path
    if pp.key is not None:
        return f"{pp.container}[].{pp.field}"
    return generic_slot_id(path)


def _override(s: FactSampler, value: Override) -> FactSampler:
    """``"prior"`` resets to the population prior; a Quantity or Dist is sampled with the fact's own draws; any other
    value is fixed on every path (in the slot's unit, rand)."""
    if isinstance(value, str) and value == "prior":
        return s.with_prior()
    if isinstance(value, Quantity):
        return dataclasses.replace(s, content=value.dist, q=1.0, currency=_currency(value), scale=1.0, basis=value.basis,
                                   kind=value_kind(value.dist), rule="override")
    if isinstance(value, BaseModel):  # a Dist
        return dataclasses.replace(s.with_content(value), kind=value_kind(value))  # type: ignore[arg-type]
    return dataclasses.replace(s.fixed(value), kind=value_kind(Point(value=value)))


def _concrete_path(profile: Profile, f: Fact, slot: SlotDef) -> str:
    """The fact's path with the entity's FnaDocument row id (as binding.materialise writes it)."""
    pp = binding.parse_path(f.fna_path)
    if not slot.exported or pp.key is None or pp.container not in binding.LIST_ROW_MODEL or not f.instance_key:
        return f.fna_path
    return f"{pp.container}[{binding.row_id_for(profile, f.instance_key)}].{pp.field}"


def _risk_category(answers: dict[str, float]) -> str | None:
    """The FNA risk profile category (lower of tolerance and capacity), from the ported engine when available."""
    if not answers:
        return None
    try:
        from ..fna_engine.risk_profile import score_risk_profile
    except ImportError:
        return None
    result = score_risk_profile(answers)
    if not getattr(result, "complete", False) or getattr(result, "profile", None) is None:
        return None
    return str(result.profile.key)


def build_plan(
    profile: Profile,
    *,
    base_document: F.FnaDocument | None = None,
    config: ForecastConfig,
    cma: CMA,
    catalog: Catalog | None = None,
    overrides: dict[str, Override] | None = None,
) -> ForecastPlan:
    """Samplers for every forecast-bound fact plus the median document for everything else (§12.2)."""
    catalog = catalog or load_catalog(profile.schema_version)
    median = binding.materialise(profile, "median", base=base_document, catalog=catalog)
    doc = median.model_dump(mode="json")
    samplers: dict[str, FactSampler] = {}
    trace: dict[str, str] = {}
    event_facts: dict[str, dict[str, Fact]] = {}
    change_facts: dict[tuple[str, str], dict[str, Fact]] = {}
    for f in sorted(profile.facts.values(), key=lambda x: x.fact_id):
        slot = catalog.slot(f.slot_id)
        if slot.slot_id.startswith("event[]."):
            event_facts.setdefault(f.instance_key or f.fact_id, {})[slot.field] = f
        elif ".expectedChange[]." in slot.slot_id:
            person = person_of(f.fna_path) or "client"
            change_facts.setdefault((person, f.instance_key or f.fact_id), {})[slot.field] = f
        if slot.forecast_binding is None:
            trace[f.fact_id] = "not_forecast_bound"
            continue
        s = build_sampler(f, slot, path=_concrete_path(profile, f, slot))
        if s is None:
            trace[f.fact_id] = "absent_median_document"
            continue
        samplers[f.fact_id] = s
        trace[f.fact_id] = s.rule

    events: list[EventSpec] = []
    dependant_rows = {r["id"] for r in doc["dependants"]}
    for key, parts in sorted(event_facts.items()):
        if "cashImpact" not in parts or parts["cashImpact"].fact_id not in samplers:
            continue
        kind_fact = parts.get("kind")
        kind = "other"
        if kind_fact is not None and kind_fact.value is not None and kind_fact.review.status != "rejected":
            kind = stats.mode_label(kind_fact.value.dist)
        refs = {r for f in parts.values() for r in f.entity_refs}
        rows = sorted({binding.row_id_for(profile, r) for r in refs} & dependant_rows)
        spec = EventSpec(key=key, occurs=None, year=None, amount=parts["cashImpact"].fact_id, kind=kind, dependant_row=rows[0] if rows else None)
        for name, attr in (("occurs", "occurs"), ("year", "year")):
            fid = parts[name].fact_id if name in parts and parts[name].fact_id in samplers else None
            if fid is None and name == "year":
                fid = _virtual(samplers, catalog, f"event[].year#{key}", f"event[{key}].year")
            setattr(spec, attr, fid)
        events.append(spec)

    changes: list[ChangeSpec] = []
    for (person, key), parts in sorted(change_facts.items()):
        if "newGrossMonthly" not in parts or parts["newGrossMonthly"].fact_id not in samplers:
            continue
        occurs = parts["occurs"].fact_id if "occurs" in parts and parts["occurs"].fact_id in samplers else None
        year = parts["year"].fact_id if "year" in parts and parts["year"].fact_id in samplers else None
        if year is None:
            year = _virtual(samplers, catalog, f"income.{{p}}.expectedChange[].year#{key}", f"income.{person}.expectedChange[{key}].year")
        changes.append(ChangeSpec(person=person, key=key, occurs=occurs, year=year, amount=parts["newGrossMonthly"].fact_id))

    hh = doc["household"]
    persons = ["client"] + (["spouse"] if hh.get("includeSpouse") and hh.get("maritalStatus") in ("married", "life-partner") else [])
    goal_keys: dict[str, str] = {}
    cost_paths = {s.path: fid for fid, s in samplers.items() if s.slot_id == "goals[].cost"}
    for f in profile.facts.values():
        if f.slot_id == "goals[].name" and f.instance_key:
            rid = binding.row_id_for(profile, f.instance_key)
            goal_keys.setdefault(rid, f"goals[].cost#{f.instance_key}")
    for row in doc["goals"]:
        rid = row["id"]
        goal_keys[rid] = cost_paths.get(f"goals[{rid}].cost") or goal_keys.get(rid) or f"goals[{rid}]"
    mix = _risk_category(doc["riskProfile"]["answers"]) if config.portfolio_policy == "from_profile_or_default" else None
    if mix is not None and mix not in cma.policies.risk_profiles:
        mix = None
    plan = ForecastPlan(
        profile_id=profile.call_id,
        anchor=profile.anchor_date,
        catalog=catalog,
        config=config,
        cma=cma,
        doc=doc,
        samplers=samplers,
        events=events,
        changes=changes,
        persons=persons,
        goal_keys=goal_keys,
        accumulation_mix=mix,
        trace=trace,
    )
    return plan.with_overrides(overrides)


def _virtual(samplers: dict[str, FactSampler], catalog: Catalog, fact_id: str, path: str) -> str:
    """A prior-only sampler for a forecast-only component the call did not cover (an event's year)."""
    slot = catalog.slot(fact_id.split("#", 1)[0])
    samplers[fact_id] = FactSampler(fact_id=fact_id, slot_id=slot.slot_id, path=path, tier=slot.sensitivity_tier, kind="numeric",
                                    rule="absent_component_population_prior", prior=slot.population_prior)
    return fact_id


def plan_from_document(
    document: F.FnaDocument, *, anchor: date, config: ForecastConfig, cma: CMA, call_id: str = "document", run_id: str = "document"
) -> ForecastPlan:
    """A plan with every field fixed at the document's value (§12.8: every fact at its median)."""
    empty = Profile(call_id=call_id, run_id=run_id, schema_version=load_catalog().version, anchor_date=anchor)
    return build_plan(empty, base_document=document, config=config, cma=cma)


# --------------------------------------------------------------------------- building the engine input


class _Builder:
    """Reads per-path fields (a draw where a fact is bound, else the median document) into ForecastInput."""

    def __init__(self, plan: ForecastPlan, draws: dict[str, Draw], n: int, tax_model: TaxModel) -> None:
        self.plan = plan
        self.doc = plan.doc
        self.draws = draws
        self.n = n
        self.tax = tax_model
        self.anchor = plan.anchor
        a = self.doc["assumptions"]
        self.assumptions = Assumptions(
            cpi=float(a["cpi"]),
            salary_escalation=float(a["salaryEscalation"]),
            pre_retirement_return=float(a["preRetirementReturn"]),
            post_retirement_return=float(a["postRetirementReturn"]),
            risk_capital_return=float(a["riskCapitalReturn"]),
            education_inflation=float(a["educationInflation"]),
        )

    # ------------------------------------------------------------------ field access

    def raw(self, path: str) -> Any:
        try:
            return binding.get_path(self.doc, path)
        except (KeyError, TypeError):
            return None

    def num(self, path: str, default: float = 0.0) -> np.ndarray:
        d = self.draws.get(path)
        if d is not None:
            return as_float(d.values) if d.values.dtype == object else d.values.astype(float)
        v = self.raw(path)
        if isinstance(v, bool):
            v = float(v)
        try:
            return np.full(self.n, float(v) if v is not None and v != "" else default)
        except (TypeError, ValueError):
            return np.full(self.n, default)

    def money(self, path: str, default: float = 0.0) -> Money:
        d = self.draws.get(path)
        if d is not None:
            return Money.of(d.values.astype(float), d.fx)
        return Money.of(self.num(path, default))

    def label(self, path: str, default: str) -> np.ndarray:
        d = self.draws.get(path)
        if d is not None:
            return d.values.astype(object) if d.values.dtype == object else np.array([str(v) for v in d.values], dtype=object)
        v = self.raw(path)
        return np.full(self.n, default if v is None or v == "" else str(v), dtype=object)

    def flag(self, path: str, default: bool) -> np.ndarray:
        d = self.draws.get(path)
        if d is not None:
            return as_float(d.values) >= 0.5
        v = self.raw(path)
        return np.full(self.n, default if v is None else bool(v), dtype=bool)

    def age(self, path: str) -> np.ndarray:
        """Exact age today from a date-of-birth field (FNA exactAge; FALLBACK_AGE + 0.5 when unknown)."""
        d = self.draws.get(path)
        if d is not None:
            return _ages_from_years(as_float(d.values), self.anchor)
        a = exact_age(self.raw(path), self.anchor)
        return np.full(self.n, FALLBACK_AGE + 0.5 if a is None else a)

    def completed_age(self, path: str) -> np.ndarray:
        """Completed years today (FNA ageOn; 0 when unknown, as computeEducation does)."""
        d = self.draws.get(path)
        if d is not None:
            return np.floor(_ages_from_years(as_float(d.values), self.anchor))
        a = _completed_years(self.raw(path), self.anchor)
        return np.full(self.n, 0.0 if a is None else a)

    def step_of_year(self, path: str, default_year: float) -> np.ndarray:
        """Years from today to a calendar year (FNA: targetYear − current year, never negative)."""
        years = np.floor(self.num(path, default_year))
        return np.maximum(0, years - self.anchor.year).astype(np.int64)

    # ------------------------------------------------------------------ build

    def build(self) -> ForecastInput:
        n = self.n
        persons = self.plan.persons
        funds = self._funds(persons)
        assets = self._assets(persons)
        people = [self._person(p, funds, assets) for p in persons]
        self._shares(people)
        dependants = self._dependants()
        hh = self._household(assets, dependants)
        liabilities = self._liabilities()
        premiums = self._premiums(persons)
        goals = self._goals(assets)
        events = self._events(dependants)
        primary_age = float(np.median(people[0].age0))
        horizon = self.plan.config.horizon_age - float(np.min(people[0].age0))
        for p in people:
            plan_eff = np.maximum(p.planning_age, np.ceil(np.where(p.retired_now, p.age0, p.retirement_age)) + 1)
            horizon = max(horizon, float(np.max(plan_eff - p.age0)))
        n_years = int(min(self.plan.config.max_years, max(1, math.ceil(horizon - 1e-9))))
        return ForecastInput(
            n=n,
            anchor=self.anchor,
            persons=people,
            household=hh,
            liabilities=liabilities,
            premiums=premiums,
            dependants=dependants,
            goals=goals,
            events=events,
            assumptions=self.assumptions,
            accumulation_mix=self.plan.accumulation_mix,
            n_years=n_years,
            primary_age_int=math.floor(primary_age),
        )

    def _funds(self, persons: list[str]) -> dict[str, dict[str, np.ndarray]]:
        n = self.n
        out = {p: {k: np.zeros(n) for k in ("capital", "payroll", "own", "employer")} for p in persons}
        for row in self.doc["retirementFunds"]:
            base = f"retirementFunds[{row['id']}]"
            owner = self.label(base + ".owner", row.get("owner") or "client")
            ftype = self.label(base + ".type", row.get("type") or "pension")
            value = self.money(base + ".value").total()
            employee = self.money(base + ".employeeMonthly").total()
            employer = self.money(base + ".employerMonthly").total()
            via = bool(row.get("viaPayroll", True))
            pre = np.isin(ftype, PRE_RETIREMENT_FUNDS)
            annuity = ftype == "living-annuity"
            for p in persons:
                mine = owner == p
                out[p]["capital"] += value * (mine & (pre | annuity))
                out[p]["employer"] += employer * (mine & pre)
                out[p]["payroll" if via else "own"] += employee * (mine & pre)
        return out

    def _assets(self, persons: list[str]) -> dict[str, Any]:
        n = self.n
        z = lambda: np.zeros(n)
        acc: dict[str, Any] = {
            "cash": z(), "discretionary": z(), "offshore": z(), "tfsa": z(), "education": z(), "property": z(), "business": z(),
            "loan": z(), "goal_pool": z(), "goal_pool_monthly": z(),
            "c_cash": z(), "c_discretionary": z(), "c_offshore": z(), "c_tfsa": z(), "c_education": z(),
            "lifestyle": {t: z() for t in LIFESTYLE_TYPES},
            "ret_capital": {p: z() for p in persons}, "ret_monthly": {p: z() for p in persons},
            "rental": {p: Money.zeros(n) for p in persons},
        }
        for row in self.doc["assets"]:
            base = f"assets[{row['id']}]"
            atype = self.label(base + ".type", row.get("type") or "cash")
            owner = self.label(base + ".owner", row.get("owner") or "client")
            earmark = self.label(base + ".earmark", row.get("earmark") or "general")
            value = self.money(base + ".value").total()
            monthly = self.money(base + ".monthlyContribution").total()
            income = self.money(base + ".monthlyIncome")
            # FNA computeRetirement: retirement-earmarked assets count for their owner, half each when joint.
            ret = earmark == "retirement"
            claimed = np.zeros(n)
            for p in persons:
                share = np.where(owner == p, 1.0, np.where(owner == "joint", 0.5, 0.0))
                acc["ret_capital"][p] += value * share * ret
                acc["ret_monthly"][p] += monthly * share * ret
                claimed += share
            # The part of a retirement-earmarked asset no person in the plan claims stays a household investment.
            spare = np.where(ret, np.clip(1 - claimed, 0, 1), 0.0)
            acc["discretionary"] += value * spare
            acc["c_discretionary"] += monthly * spare
            edu = earmark == "education"
            goal = earmark == "goal"
            acc["education"] += value * edu
            acc["c_education"] += monthly * edu
            acc["goal_pool"] += value * goal
            acc["goal_pool_monthly"] += monthly * goal
            free = ~(ret | edu | goal)
            cash = free & np.isin(atype, CASH_TYPES)
            inv = free & np.isin(atype, INVESTMENT_TYPES)
            tfsa = free & (atype == "tfsa")
            off = free & (atype == "offshore")
            prop = free & np.isin(atype, PROPERTY_TYPES)
            biz = free & (atype == "business-interest")
            loan = free & (atype == "loan-account")
            acc["cash"] += value * cash
            acc["discretionary"] += value * inv
            acc["tfsa"] += value * tfsa
            acc["offshore"] += value * off
            acc["property"] += value * prop
            acc["business"] += value * biz
            acc["loan"] += value * loan
            for t in LIFESTYLE_TYPES:
                acc["lifestyle"][t] += value * (free & (atype == t))
            acc["c_cash"] += monthly * cash
            acc["c_tfsa"] += monthly * tfsa
            acc["c_offshore"] += monthly * off
            acc["c_discretionary"] += monthly * (inv | (free & ~(cash | tfsa | off | inv)))
            # Income from an asset is taxable for its owner (half each when joint; the client when single).
            for p in persons:
                if len(persons) == 1:
                    share = np.ones(n)
                else:
                    share = np.where(owner == p, 1.0, np.where(owner == "joint", 0.5, 0.0))
                acc["rental"][p] = acc["rental"][p] + income.scale(share)
        return acc

    def _person(self, p: str, funds: dict[str, dict[str, np.ndarray]], assets: dict[str, Any]) -> PersonIn:
        a = self.assumptions
        age0 = self.age(f"{p}.dateOfBirth")
        emp = self.label(f"{p}.employmentType", "salaried")
        ret_age = self.num(f"retirement.{p}.retirementAge", 65)
        plan_age = self.num(f"retirement.{p}.planningAge", 92)
        retired_now = (emp == "retired") | (age0 >= ret_age)
        self_employed = emp == "self-employed"
        not_employed = np.isin(emp, ("self-employed", "retired", "unemployed", "student"))
        uif = self.flag(f"income.{p}.uifApplies", True) & ~not_employed
        members = self.num(f"income.{p}.medicalSchemeMembers")
        med_payroll = self.num(f"income.{p}.medicalAidPayrollMonthly")
        other_payroll = self.num(f"income.{p}.otherPayrollDeductionsMonthly")
        f = funds[p]
        path = f"income.{p}.grossMonthly"
        gross = self.money(path)
        d = self.draws.get(path)
        if d is not None and d.net is not None and d.net.any():
            gross = self._gross_up(gross, d.net, age0, f["payroll"], members, med_payroll, other_payroll, uif)
        bonus = self.money(f"income.{p}.annualBonus")
        other_taxable = self.money(f"income.{p}.otherTaxableMonthly")
        non_taxable = self.money(f"income.{p}.nonTaxableMonthly")
        growth = np.full(self.n, a.real(a.salary_escalation))
        bg = self.draws.get(f"income.{p}.businessGrowthRate")
        if bg is not None:
            growth = np.where(self_employed, bg.values.astype(float), growth)
        mode = self.label(f"retirement.{p}.targetMode", "percent")
        gross_eq = gross.total() + other_taxable.total() + bonus.total() / 12  # FNA grossMonthlyEquivalent
        target = np.where(mode == "amount", self.money(f"retirement.{p}.targetMonthly").total(), self.num(f"retirement.{p}.targetPercent", 0.75) * gross_eq)
        changes = [self._change(c) for c in self.plan.changes if c.person == p]
        return PersonIn(
            key=p,
            age0=age0,
            retired_now=retired_now,
            retirement_age=ret_age,
            planning_age=plan_age,
            self_employed=self_employed,
            uif=uif,
            gross_monthly=gross,
            bonus_annual=bonus,
            other_taxable_monthly=other_taxable,
            non_taxable_monthly=non_taxable,
            medical_payroll_monthly=med_payroll,
            medical_members=members,
            other_payroll_monthly=other_payroll,
            growth_real=growth,
            target_monthly_today=np.maximum(0.0, target),
            other_income_monthly=self.money(f"retirement.{p}.otherIncomeMonthly").total(),
            capital0=f["capital"] + assets["ret_capital"][p],
            contrib_payroll_monthly=f["payroll"],
            contrib_own_monthly=f["own"],
            contrib_employer_monthly=f["employer"],
            contrib_earmarked_monthly=assets["ret_monthly"][p],
            share=np.zeros(self.n),
            rental_monthly=assets["rental"][p],
            changes=changes,
        )

    def _gross_up(self, gross: Money, net: np.ndarray, age, payroll, members, med_payroll, other_payroll, uif) -> Money:
        """Take-home amounts → gross through the tax model (§7.6); paths stated as gross are unchanged."""
        idx = np.flatnonzero(net)
        total = gross.total()
        up = self.tax.gross_up_monthly(
            total[idx], age=age[idx], retirement_monthly=payroll[idx], medical_members=members[idx],
            medical_payroll_monthly=med_payroll[idx], other_payroll_monthly=other_payroll[idx], uif=uif[idx],
        )
        factor = np.ones(self.n)
        factor[idx] = np.where(total[idx] > 0, up / np.where(total[idx] > 0, total[idx], 1.0), 0.0)
        return gross.scale(factor)

    def _change(self, c: ChangeSpec) -> IncomeChangeIn:
        s = self.plan.samplers
        occurs = self.flag(s[c.occurs].path, True) if c.occurs else np.ones(self.n, dtype=bool)
        step = self.step_of_year(s[c.year].path, self.anchor.year) if c.year else np.zeros(self.n, dtype=np.int64)
        return IncomeChangeIn(occurs=occurs, step=step, new_gross_monthly=self.money(s[c.amount].path))  # type: ignore[index]

    def _shares(self, people: list[PersonIn]) -> None:
        """Each person's share of household living expenses before retirement: their share of gross income."""
        eq = [p.gross_monthly.total() + p.other_taxable_monthly.total() + p.bonus_annual.total() / 12 for p in people]
        total = np.sum(eq, axis=0)
        for p, e in zip(people, eq):
            p.share = np.where(total > 0, e / np.where(total > 0, total, 1.0), 1.0 / len(people))

    def _dependants(self) -> list[DependantIn]:
        out = []
        for row in self.doc["dependants"]:
            base = f"dependants[{row['id']}]"
            school_type = self.raw(base + ".schoolType") or "public"
            out.append(DependantIn(
                key=row["id"],
                age0=self.completed_age(base + ".dateOfBirth"),
                is_child=(row.get("relationship") or "child") == "child",
                school=school_type != "none",
                school_fees_annual=self.money(base + ".schoolFeesAnnual"),
                tertiary=self.flag(base + ".tertiary", True),
                tertiary_start_age=self.num(base + ".tertiaryStartAge", 19),
                tertiary_years=self.num(base + ".tertiaryYears", 4),
                tertiary_cost_annual=self.money(base + ".tertiaryCostAnnual", 150_000),
                support_monthly=self.money(base + ".monthlySupport"),
                support_years=self.num(base + ".supportYears"),
            ))
        return out

    def _household(self, assets: dict[str, Any], dependants: list[DependantIn]) -> HouseholdIn:
        n = self.n
        cfg = self.plan.config.expenses
        catalog = self.plan.catalog
        lines = {k: Money.zeros(n) for k in ("ess", "disc", "child_ess", "child_disc", "support")}
        keys = set(self.doc["expenses"]["items"]) | {
            binding.parse_path(s.path).key for s in self.plan.samplers.values() if s.slot_id == "expenses.items[]"
        }
        for key in sorted(k for k in keys if k):
            item = catalog.expense_item(key)
            amount = self.money(f"expenses.items[{key}]")
            essential = bool(item.essential) if item else False
            category = item.category if item else "other"
            self._add_line(lines, amount, np.full(n, essential), category, key in cfg.support_keys)
        for row in self.doc["expenses"]["custom"]:
            base = f"expenses.custom[{row['id']}]"
            amount = self.money(base + ".amount")
            essential = self.flag(base + ".essential", bool(row.get("essential")))
            self._add_line(lines, amount, essential, row.get("category") or "other", False)
        children = [d for d in dependants if d.is_child]
        if children:
            youngest = np.min(np.stack([d.age0 for d in children]), axis=0)
            children_end = np.maximum(0, np.ceil(cfg.children_end_age - youngest)).astype(np.int64)
        else:
            children_end = np.full(n, -1, dtype=np.int64)
        supported = [d for d in dependants if not d.is_child]
        if supported:
            years = np.max(np.stack([np.where(d.support_monthly.total() > 0, d.support_years, 0) for d in supported]), axis=0)
            support_end = np.where(years > 0, np.ceil(years), -1).astype(np.int64)
        else:
            support_end = np.full(n, -1, dtype=np.int64)
        has_support_line = bool(np.any(lines["support"].total() > 0))
        has_child_lines = bool(np.any((lines["child_ess"] + lines["child_disc"]).total() > 0))
        return HouseholdIn(
            cash0=assets["cash"],
            discretionary0=assets["discretionary"],
            offshore0=assets["offshore"],
            tfsa0=assets["tfsa"],
            education0=assets["education"] + sum((self.money(f"dependants[{d.key}].educationSavings").total() for d in dependants), np.zeros(n)),
            property0=assets["property"],
            business0=assets["business"],
            loan_account0=assets["loan"],
            lifestyle0=assets["lifestyle"],
            contrib_cash=assets["c_cash"],
            contrib_discretionary=assets["c_discretionary"],
            contrib_offshore=assets["c_offshore"],
            contrib_tfsa=assets["c_tfsa"],
            contrib_education=assets["c_education"] + sum((self.money(f"dependants[{d.key}].educationSavingsMonthly").total() for d in dependants), np.zeros(n)),
            living_essential=lines["ess"],
            living_discretionary=lines["disc"],
            children_essential=lines["child_ess"],
            children_discretionary=lines["child_disc"],
            support_essential=lines["support"],
            children_end_step=np.where(has_child_lines, children_end, -1) if children else children_end,
            support_end_step=support_end if has_support_line else np.full(n, -1, dtype=np.int64),
            support_from_dependants=not has_support_line,
            school_from_dependants=not has_child_lines,
        )

    @staticmethod
    def _add_line(lines: dict[str, Money], amount: Money, essential: np.ndarray, category: str, support: bool) -> None:
        if support:
            lines["support"] = lines["support"] + amount.where(essential)
            lines["disc"] = lines["disc"] + amount.where(~essential)
        elif category == "children":
            lines["child_ess"] = lines["child_ess"] + amount.where(essential)
            lines["child_disc"] = lines["child_disc"] + amount.where(~essential)
        else:
            lines["ess"] = lines["ess"] + amount.where(essential)
            lines["disc"] = lines["disc"] + amount.where(~essential)

    def _liabilities(self) -> list[LiabilityIn]:
        out = []
        for row in self.doc["liabilities"]:
            base = f"liabilities[{row['id']}]"
            out.append(LiabilityIn(
                key=row["id"],
                balance=self.money(base + ".balance").total(),
                rate=self.num(base + ".interestRate", 0.1075),
                repayment=self.money(base + ".monthlyRepayment").total(),
            ))
        return out

    def _premiums(self, persons: list[str]) -> list[PremiumIn]:
        out = []
        for row in self.doc["policies"]:
            if row.get("isGroup") or row.get("lifeAssured", "client") not in persons:
                continue
            monthly = self.money(f"policies[{row['id']}].premiumMonthly").total()
            out.append(PremiumIn(person=persons.index(row.get("lifeAssured", "client")), monthly=monthly, escalation=float(row.get("premiumEscalation") or 0.0)))
        return out

    def _goals(self, assets: dict[str, Any]) -> list[GoalIn]:
        rows = self.doc["goals"]
        costs = [self.money(f"goals[{r['id']}].cost") for r in rows]
        total = np.sum([c.total() for c in costs], axis=0) if rows else np.zeros(self.n)
        out = []
        for row, cost in zip(rows, costs):
            base = f"goals[{row['id']}]"
            share = np.where(total > 0, cost.total() / np.where(total > 0, total, 1.0), 0.0)  # FNA: pool shared by cost
            out.append(GoalIn(
                key=self.plan.goal_keys.get(row["id"], f"goals[{row['id']}]"),
                cost=cost,
                step=self.step_of_year(base + ".targetYear", self.anchor.year + 5),
                savings0=self.money(base + ".existingSavings").total() + assets["goal_pool"] * share,
                monthly_nominal=self.money(base + ".monthlyContribution").total() + assets["goal_pool_monthly"] * share,
            ))
        return out

    def _events(self, dependants: list[DependantIn]) -> list[EventIn]:
        s = self.plan.samplers
        out = []
        by_row = {d.key: d for d in dependants}
        for e in self.plan.events:
            amount_sampler = s[e.amount]  # type: ignore[index]
            amount = self.money(amount_sampler.path)
            occurs = self.flag(s[e.occurs].path, True) if e.occurs else np.ones(self.n, dtype=bool)
            step = self.step_of_year(s[e.year].path, self.anchor.year) if e.year else np.zeros(self.n, dtype=np.int64)
            period = amount_sampler.period
            if e.kind == "education-offshore" and e.dependant_row in by_row:
                # One occurrence draw decides location, cost and FX exposure together (§12.2).
                d = by_row[e.dependant_row]
                yearly = amount.scale(12.0) if period == "monthly" else amount
                if period not in ("monthly", "annual"):
                    yearly = amount.scale(1.0 / np.maximum(d.tertiary_years, 1.0))
                start = step if e.year and not s[e.year].rule.startswith("absent") else np.full(self.n, -1, dtype=np.int64)
                d.offshore = OffshoreStudyIn(occurs=occurs, start_step=start, annual_cost=Money(np.abs(yearly.fixed), np.abs(yearly.linked)))
                continue
            recurring = period in ("monthly", "annual")
            if period == "monthly":
                amount = amount.scale(12.0)
            out.append(EventIn(key=e.key, occurs=occurs, step=step, amount=amount, recurring=recurring,
                               essential=e.kind not in EVENT_DISCRETIONARY_KINDS))
        return out


def concat_inputs(parts: list[ForecastInput]) -> ForecastInput:
    """Several inputs with the same structure as one batch of paths (sensitivity variants run in one engine call)."""

    def cat(values: list[Any], name: str = "") -> Any:
        v0 = values[0]
        if isinstance(v0, np.ndarray):
            return np.concatenate(values)
        if isinstance(v0, Money):
            return Money(np.concatenate([v.fixed for v in values]), np.concatenate([v.linked for v in values]))
        if dataclasses.is_dataclass(v0):
            return type(v0)(**{f.name: cat([getattr(v, f.name) for v in values], f.name) for f in dataclasses.fields(v0)})
        if isinstance(v0, list):
            if any(len(v) != len(v0) for v in values):
                raise ValueError(f"inputs differ in structure ({name})")
            return [cat([v[i] for v in values], name) for i in range(len(v0))]
        if isinstance(v0, dict):
            return {k: cat([v[k] for v in values], k) for k in v0}
        if name == "n":
            return sum(values)
        if name == "n_years":
            return max(values)
        if name == "primary_age_int":  # only labels the recorded ages
            return v0
        if any(v != v0 for v in values):
            raise ValueError(f"inputs differ in {name}: {values[:3]}")
        return v0

    return cat(list(parts))
