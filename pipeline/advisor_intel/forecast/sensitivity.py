"""Sensitivity runs for the value-of-information ranking, engine side (spec §10.2).

A reduced forecast (1,000 paths, annual steps) with common random numbers: every run in one call shares the
same market draws and the same draws for every other fact, so a PoS difference reflects only the fact moved.
For each candidate fact:

- content impact: PoS with the fact fixed at its p10 and at its p90 (for Bernoulli and boolean facts, the event
  happening or not; for categorical facts the two outcomes, among the likely ones, whose PoS differ most);
- extraction impact: PoS with the fact replaced by its slot's population prior, weighted by 1 − trust, where
  trust is the probability the forecast gives the extracted value (1 once reviewed, auto-accepted or from the
  prior FNA; p_correct otherwise).

All variants of a call run as one batched engine evaluation; about 30 candidates take seconds.
``fact_extremes`` gives the same two ends for the FNA-side runs (§10.2, I_fna), which move the fact in the
median document instead.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable, Iterable, Literal

import numpy as np
from pydantic import BaseModel

from .. import stats
from ..models import fna as F
from ..models.dists import Dist, Point
from ..models.facts import Fact
from ..models.profile import Profile
from ..schema.slots import SlotDef, load_catalog
from .config import CMA, ForecastConfig, load_cma, load_config
from .engine import simulate
from .mapper import FactSampler, ForecastPlan, build_plan, build_sampler, concat_inputs
from .run import MAX_BATCH_PATHS, Forecaster
from .tax import TaxModel, default_tax_model

CATEGORY_MIN_P = 0.05  # categories below this probability are not candidate outcomes
CATEGORY_MAX = 4  # at most this many outcomes are evaluated per categorical fact


class SensitivityRow(BaseModel):
    fact_id: str
    slot_id: str
    tier: str
    kind: Literal["numeric", "boolean", "categorical"]
    bound: bool  # the forecast uses this fact; when False every PoS equals the base (the FNA side may still move)
    low: float | str | bool | None = None  # the value at the low end (p10, False, or the worse outcome)
    high: float | str | bool | None = None
    pos_base: float
    pos_low: float
    pos_high: float
    pos_prior: float | None = None  # with the fact replaced by its slot's population prior
    trust: float = 1.0  # P(the forecast uses the extracted value)
    i_content: float = 0.0  # |PoS(high) − PoS(low)|
    i_extract: float = 0.0  # (1 − trust) · |PoS(base) − PoS(prior)|
    n_paths: int
    seed: int
    rule: str = ""  # the §12.2 rule the fact's sampler follows


# --------------------------------------------------------------------------- the two ends of a fact


def _numeric_ends(dist: Dist) -> tuple[float, float]:
    return float(stats.quantile(dist, 0.1)), float(stats.quantile(dist, 0.9))


def _categories(dist: Dist) -> list[str]:
    """Likely outcomes, most probable first (at least the top two)."""
    probs = sorted(stats.categorical_probs(dist).items(), key=lambda kv: (-kv[1], kv[0]))
    keep = [k for i, (k, p) in enumerate(probs) if i < 2 or p >= CATEGORY_MIN_P]
    return [k for k, p in probs if k in keep and p > 0][:CATEGORY_MAX] or [probs[0][0]]


def _sampled_dist(s: FactSampler) -> Dist | None:
    return s.content if s.content is not None else s.prior


def _ends(s: FactSampler) -> tuple[Any, Any]:
    """The two ends of a sampler's distribution in its own units (before currency and period conversion)."""
    dist = _sampled_dist(s)
    if dist is None:
        return None, None
    if s.kind == "boolean":
        p = stats.p_true(dist)
        return (0.0, 1.0) if 0 < p < 1 else (float(p >= 0.5),) * 2
    if s.kind == "categorical":
        cats = _categories(dist)
        return cats[0], cats[1] if len(cats) > 1 else cats[0]
    return _numeric_ends(dist)


def _document_value(slot: SlotDef, x: Any) -> Any:
    """A value in the FnaDocument's own form: ISO dates, whole ages and years, rounded money and rates."""
    if x is None or isinstance(x, str):
        return x
    if slot.value_type in ("boolean", "probability"):
        return bool(x >= 0.5)
    if slot.value_type == "date":
        return stats.year_to_iso(float(x))
    if slot.value_type in ("age", "years", "months", "count", "year"):
        return int(round(float(x)))
    if slot.value_type == "amount":
        return round(float(x), 2)
    if slot.value_type == "rate":
        return round(float(x), 6)
    return float(x)


def fact_extremes(fact: Fact, slot: SlotDef, *, spot: Callable[[str], float] | None = None) -> tuple[Any, Any]:
    """The two ends a fact is moved to (§10.2), as FnaDocument values.

    Numeric facts: the p10 and p90 of the distribution the forecast samples (the fact's value; its prior value
    or the slot's population prior when not discussed or unknown, §4.3), converted to the slot's period. Foreign
    amounts are converted to rand when ``spot`` (rand per unit) is given, else left in their currency. Boolean
    and Bernoulli facts: False and True. Categorical facts: the two most probable outcomes. (None, None) when
    the fact contributes no value (rejected without a prior value).
    """
    s = build_sampler(fact, slot, path=fact.fna_path)
    if s is None:
        return None, None
    lo, hi = _ends(s)
    if s.kind == "numeric" and lo is not None:
        k = s.scale * (spot(s.currency) if (spot and s.currency) else 1.0) if s.content is not None else 1.0
        lo, hi = lo * k, hi * k
    return _document_value(slot, lo), _document_value(slot, hi)


def _fixed(s: FactSampler, value: Any, rule: str) -> FactSampler:
    """The fact fixed at ``value`` (in its own units: currency, period and income basis are kept)."""
    return dataclasses.replace(s, content=Point(value=value), q=1.0, rule=rule)


# --------------------------------------------------------------------------- runs


def _pos_of_variants(fc: Forecaster, plans: list[ForecastPlan], seed: int, n_paths: int) -> list[float]:
    """PoS of each plan variant on the same market and the same draws (one batched engine evaluation)."""
    u = fc.plan.uniforms(seed, n_paths, matrix="sensitivity")
    inputs = [p.realise(u, fc.tax) for p in plans]
    years = max(i.n_years for i in inputs)
    market = fc.realise_market(fc.shocks(seed, n_paths, years, matrix="sensitivity"))
    per_batch = max(1, MAX_BATCH_PATHS // n_paths)
    out: list[float] = []
    for i in range(0, len(inputs), per_batch):
        group = inputs[i : i + per_batch]
        batch = concat_inputs(group)
        tiled = market.take(np.tile(np.arange(n_paths), len(group)))
        success = simulate(batch, tiled, config=fc.config, cma=fc.cma, tax_model=fc.tax).success.reshape(len(group), n_paths)
        out.extend(float(x) for x in success.mean(axis=1))
    return out


def sensitivity_runs(
    profile: Profile,
    fact_ids: Iterable[str],
    *,
    n_paths: int = 1_000,
    seed: int,
    base_document: F.FnaDocument | None = None,
    config: ForecastConfig | None = None,
    cma: CMA | None = None,
    tax_model: TaxModel | None = None,
) -> dict[str, "SensitivityRow"]:
    """PoS at each candidate fact's two ends and with its population prior, with common random numbers (§10.2)."""
    config = config or load_config()
    cma = cma or load_cma(config.cma_version)
    plan = build_plan(profile, base_document=base_document, config=config, cma=cma)
    tax_model = tax_model or default_tax_model(str(plan.doc["assumptions"]["taxYear"]))
    fc = Forecaster(plan, tax_model)
    catalog = load_catalog(profile.schema_version)

    variants: list[ForecastPlan] = [plan]
    index: dict[tuple[str, str], int] = {}
    specs: dict[str, dict[str, Any]] = {}

    def add(fid: str, label: str, sampler: FactSampler) -> None:
        index[(fid, label)] = len(variants)
        variants.append(plan.with_samplers({fid: sampler}))

    for fid in dict.fromkeys(fact_ids):
        fact = profile.facts.get(fid)
        if fact is None:
            raise KeyError(f"no fact {fid!r} in the profile")
        slot = catalog.slot(fact.slot_id)
        s = plan.samplers.get(fid)
        if s is None:
            specs[fid] = {"slot": slot, "sampler": None}
            continue
        lo, hi = _ends(s)
        spec: dict[str, Any] = {"slot": slot, "sampler": s, "lo": lo, "hi": hi, "categories": []}
        if s.kind == "categorical":
            spec["categories"] = _categories(_sampled_dist(s))  # type: ignore[arg-type]
            for cat in spec["categories"]:
                add(fid, f"cat:{cat}", _fixed(s, cat, "sensitivity_outcome"))
        else:
            add(fid, "low", _fixed(s, lo, "sensitivity_low"))
            if hi != lo:
                add(fid, "high", _fixed(s, hi, "sensitivity_high"))
        if s.prior is not None and s.mode != "prior":
            add(fid, "prior", s.with_prior("sensitivity_prior"))
        specs[fid] = spec

    pos = _pos_of_variants(fc, variants, seed, n_paths)
    base = pos[0]
    rows: dict[str, SensitivityRow] = {}
    for fid, spec in specs.items():
        slot: SlotDef = spec["slot"]
        s: FactSampler | None = spec["sampler"]
        common = {"fact_id": fid, "slot_id": slot.slot_id, "tier": slot.sensitivity_tier, "pos_base": round(base, 6), "n_paths": n_paths, "seed": seed}
        if s is None:
            kind = "categorical" if slot.value_type == "category" else "boolean" if slot.value_type in ("boolean", "probability") else "numeric"
            rows[fid] = SensitivityRow(**common, kind=kind, bound=False, pos_low=round(base, 6), pos_high=round(base, 6), rule="not_in_forecast")
            continue
        if s.kind == "categorical":
            outcomes = [(pos[index[(fid, f"cat:{c}")]], c) for c in spec["categories"]]
            (p_lo, lo), (p_hi, hi) = min(outcomes), max(outcomes)
        else:
            lo, hi = spec["lo"], spec["hi"]
            p_lo = pos[index[(fid, "low")]]
            p_hi = pos[index[(fid, "high")]] if (fid, "high") in index else p_lo
            if s.kind == "boolean":
                lo, hi = bool(lo), bool(hi)
        p_prior = pos[index[(fid, "prior")]] if (fid, "prior") in index else (base if s.mode == "prior" else None)
        trust = s.q if s.mode == "mixture" else 1.0
        rows[fid] = SensitivityRow(
            **common,
            kind=s.kind,
            bound=True,
            low=lo,
            high=hi,
            pos_low=round(p_lo, 6),
            pos_high=round(p_hi, 6),
            pos_prior=None if p_prior is None else round(p_prior, 6),
            trust=round(trust, 6),
            i_content=round(abs(p_hi - p_lo), 6),
            i_extract=round((1 - trust) * abs(base - p_prior), 6) if p_prior is not None else 0.0,
            rule=s.rule,
        )
    return rows
