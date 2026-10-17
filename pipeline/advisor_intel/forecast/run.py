"""``run_forecast``: the Temporal activity's entry point (spec §12), and the evaluator it shares with §10.2.

- Seed: derived from the run id and the profile version unless given (``seed_policy: per_run_fixed``), so the
  same inputs, config and seed give identical result bytes (§12.6).
- Monte Carlo error: PoS carries a Wilson 95% interval; while it is wider than 2 points the path count is
  raised (to ``n_paths_max``).
- Side by side (§12.8): ``deterministic`` holds the FNA's own retirement projection from ``fna_analyse(doc, today)``;
  ``need_bands`` holds ``fna_bands(docs, today)`` over K documents from ``binding.materialise_draws`` (§12.7). Both
  default to the ported FNA engine when it is importable and can be injected (a stub, or ``False`` to leave the
  field empty); this package never computes FNA needs itself.
"""

from __future__ import annotations

import inspect
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import numpy as np

from .. import binding
from ..models import fna as F
from ..models.forecast import ForecastResult
from ..models.profile import Profile
from .config import CMA, ForecastConfig, load_cma, load_config
from .engine import EngineResult, simulate
from .mapper import ForecastInput, ForecastPlan, Override, build_plan, plan_from_document
from .returns import BootstrapShocks, HistoricalSeries, MarketModel, MarketPaths, MarketShocks, StationaryBlockBootstrap
from .sampling import FactUniforms, Uniforms, derive_seed
from .tax import TaxModel, default_tax_model

Z95 = 1.959963984540054
BAND_QUANTILES = (10, 25, 50, 75, 90)
MAX_BATCH_PATHS = 12_000  # paths per engine call when a batch is split

FnaAnalyse = Callable[..., Any]  # fna_analyse(doc) or fna_analyse(doc, today=...)
FnaBands = Callable[..., dict[str, dict[str, float]]]  # fna_bands(docs) or fna_bands(docs, today=...)


def _engine_hook(name: str) -> Callable[..., Any] | None:
    """The ported FNA engine's ``analyse`` or ``bands`` hook, or None when the engine is not installed."""
    try:
        from .. import fna_engine  # noqa: F401 - only to find out whether the port is importable
    except ImportError:
        return None
    return fna_engine_analyse if name == "analyse" else fna_engine_bands


def _call(fn: Callable[..., Any], arg: Any, today: date) -> Any:
    """Call an injected FNA hook, passing the profile's anchor date as ``today`` when it takes one."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(arg)
    if "today" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(arg, today=today)
    return fn(arg)


def fna_engine_analyse(doc: F.FnaDocument, *, today: date | None = None) -> Any:
    """The ported FNA ``analyse`` (§12.7), for ``run_forecast(fna_analyse=...)``."""
    from ..fna_engine import analyse

    return analyse(doc, today=today)


def fna_engine_bands(docs: list[F.FnaDocument], *, today: date | None = None) -> dict[str, dict[str, float]]:
    """p10/p50/p90 of every need's shortfall over materialised draws (§12.7), for ``run_forecast(fna_bands=...)``."""
    from ..fna_engine import analyse_batch, need_bands

    return need_bands(analyse_batch(docs, today=today))


def wilson_interval(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (§12.4 pos_ci)."""
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def paths_for_width(pos: float, width: float, z: float = Z95, *, step: int = 1_000) -> int:
    """Paths needed for a 95% interval of ``width`` around ``pos`` (10% margin, rounded up to ``step``)."""
    p = min(max(pos, 0.01), 0.99)
    n = (2 * z) ** 2 * p * (1 - p) / (width * width) * 1.1
    return int(math.ceil(n / step) * step)


# --------------------------------------------------------------------------- evaluation


@dataclass
class Evaluation:
    """One evaluated batch of paths: the engine input, the market and the per-path results."""

    inp: ForecastInput
    market: MarketPaths
    result: EngineResult


def concat_uniforms(parts: Sequence[Uniforms]) -> Uniforms:
    keys = parts[0].facts.keys()
    facts = {
        k: FactUniforms(*(np.concatenate([getattr(p.facts[k], a) for p in parts]) for a in ("c1", "c2", "e1", "e2")))
        for k in keys
    }
    return Uniforms(sum(p.n for p in parts), facts)


def concat_shocks(parts: Sequence[MarketShocks | BootstrapShocks], n_years: int) -> MarketShocks | BootstrapShocks:
    """The first ``n_years`` years of several shock blocks, stacked along the path axis."""
    short = min(p.n_years for p in parts)
    if short < n_years:
        raise ValueError(f"market shocks cover {short} years; the plan needs {n_years}")
    if isinstance(parts[0], BootstrapShocks):
        return BootstrapShocks(np.concatenate([p.idx[:, : 12 * n_years] for p in parts]))  # type: ignore[union-attr]
    return MarketShocks(
        np.concatenate([p.z[:, :, :n_years] for p in parts], axis=1),  # type: ignore[union-attr]
        np.concatenate([p.chi2[:, :n_years] for p in parts]),  # type: ignore[union-attr]
        np.concatenate([p.e[:, :n_years] for p in parts]),  # type: ignore[union-attr]
    )


class Forecaster:
    """A plan, a market model and a tax model: evaluates any uniforms against any market shocks.

    ``run_forecast``, the variance attribution and the sensitivity runs all go through ``evaluate``, so they
    share one engine and one random-number layout (common random numbers, §10.2).
    """

    def __init__(self, plan: ForecastPlan, tax_model: TaxModel, *, market: MarketModel | StationaryBlockBootstrap | None = None) -> None:
        self.plan = plan
        self.config = plan.config
        self.cma = plan.cma
        self.tax = tax_model
        cpi = float(plan.doc["assumptions"]["cpi"])
        self.market_model = market or MarketModel(plan.config, plan.cma, cpi)

    @property
    def gaussian(self) -> MarketModel | None:
        """The MVN-t model when it is in use (Shapley effects need its conditional distributions)."""
        return self.market_model if isinstance(self.market_model, MarketModel) else None

    def uniforms(self, seed: int, n: int, *, matrix: str = "") -> Uniforms:
        return self.plan.uniforms(seed, n, matrix=matrix)

    def shocks(self, seed: int, n: int, n_years: int, *, matrix: str = "") -> MarketShocks | BootstrapShocks:
        return self.market_model.shocks(seed, n, n_years, matrix=matrix)

    def realise_market(self, shocks: MarketShocks | BootstrapShocks) -> MarketPaths:
        return self.market_model.realise(shocks)  # type: ignore[arg-type]

    def evaluate(self, uniforms: Uniforms, shocks: MarketShocks | BootstrapShocks, *, plan: ForecastPlan | None = None) -> Evaluation:
        return self.evaluate_inputs((plan or self.plan).realise(uniforms, self.tax), shocks)

    def evaluate_inputs(self, inp: ForecastInput, shocks: MarketShocks | BootstrapShocks) -> Evaluation:
        """Simulate realised inputs on the first ``inp.n_years`` years of ``shocks`` (which may cover more)."""
        if shocks.n_years < inp.n_years:
            raise ValueError(f"market shocks cover {shocks.n_years} years; the plan needs {inp.n_years}")
        market = self.realise_market(shocks.head(inp.n_years))
        return Evaluation(inp, market, simulate(inp, market, config=self.config, cma=self.cma, tax_model=self.tax))

    def evaluate_market_paths(self, uniforms: Uniforms, market: MarketPaths, *, plan: ForecastPlan | None = None) -> Evaluation:
        inp = (plan or self.plan).realise(uniforms, self.tax)
        return Evaluation(inp, market, simulate(inp, market, config=self.config, cma=self.cma, tax_model=self.tax))

    def funded_batch(self, items: Sequence[tuple[Uniforms, MarketShocks | BootstrapShocks]]) -> list[np.ndarray]:
        """Funded ratios for several (uniforms, shocks) pairs, evaluated in as few engine calls as possible.

        Each batch uses the years its realised inputs need, so every block of shocks must cover the longest
        horizon any of its draws can give (``ForecastConfig.max_years`` always does).
        """
        out: list[np.ndarray] = []
        group: list[tuple[Uniforms, MarketShocks | BootstrapShocks]] = []
        size = 0

        def flush() -> None:
            nonlocal group, size
            if not group:
                return
            inp = self.plan.realise(concat_uniforms([u for u, _ in group]), self.tax)
            ev = self.evaluate_inputs(inp, concat_shocks([s for _, s in group], inp.n_years))
            y = ev.result.funded_ratio
            i = 0
            for u, _ in group:
                out.append(y[i : i + u.n])
                i += u.n
            group, size = [], 0

        for u, s in items:
            if size and size + u.n > MAX_BATCH_PATHS:
                flush()
            group.append((u, s))
            size += u.n
        flush()
        return out

    def run(self, seed: int, n_paths: int, *, plan: ForecastPlan | None = None) -> Evaluation:
        """The main simulation: ``n_paths`` paths from the run seed (matrix ``main``), the market drawn for as many
        years as the longest-lived path needs."""
        plan = plan or self.plan
        inp = plan.realise(plan.uniforms(seed, n_paths, matrix="main"), self.tax)
        return self.evaluate_inputs(inp, self.shocks(seed, n_paths, inp.n_years, matrix="main"))


# --------------------------------------------------------------------------- results


def _bands(values: np.ndarray, decimals: int = 0) -> dict[str, list[float]]:
    q = np.percentile(values, BAND_QUANTILES, axis=0)
    return {f"p{k}": [round(float(v), decimals) for v in row] for k, row in zip(BAND_QUANTILES, q)}


def _finite(x: float | None, decimals: int = 6) -> float | None:
    if x is None or not np.isfinite(x):
        return None
    return round(float(x), decimals)


DETERMINISTIC_KEYS = ("currentAge", "retirementAge", "planningAge", "yearsToRetirement", "currentCapital", "monthlyContributions",
                      "projectedCapital", "projectedCapitalReal", "targetMonthlyToday", "targetMonthlyAtRetirement", "requiredCapital",
                      "requiredCapitalReal", "shortfall", "shortfallReal", "funded", "initialDrawdown", "sustainableMonthlyToday",
                      "replacementRatio", "targetReplacementRatio", "depletionAge")


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def deterministic_projection(analysis: Any) -> dict[str, float | None]:
    """The FNA's retirement projection per person, flattened for side-by-side display (§12.8).

    Keys are ``"<person>.<field>"`` in snake case (``client.projected_capital``, ``spouse.depletion_age``), as the
    app's review overlay reads them (``lib/review/impact.ts`` ``forecastDeterministic``).
    """
    data = analysis.model_dump(mode="json") if hasattr(analysis, "model_dump") else analysis
    persons = (data or {}).get("persons") or {}
    out: dict[str, float | None] = {}
    for person in sorted(persons):
        ret = (persons[person] or {}).get("retirement") or {}
        for k in DETERMINISTIC_KEYS:
            v = ret.get(k)
            out[f"{person}.{_snake(k)}"] = _finite(float(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    return out


def zero_volatility_capital(plan: ForecastPlan, tax_model: TaxModel) -> dict[str, float | None]:
    """§12.8's consistency figure, ``"<person>.mc_zero_vol_capital"``: the Monte Carlo capital at retirement with every
    volatility switched off and every fact at its median (the plan's median document), which must agree with the
    FNA's ``projectedCapital`` within 0.5%."""
    median = F.FnaDocument.model_validate(plan.doc)
    zero = plan_from_document(median, anchor=plan.anchor, config=plan.config.with_(zero_volatility=True), cma=plan.cma, call_id=plan.profile_id)
    result = Forecaster(zero, tax_model).run(0, 2).result
    return {f"{k}.mc_zero_vol_capital": _finite(float(v[0])) for k, v in sorted(result.capital_at_retirement.items())}


def summarise(ev: Evaluation) -> dict[str, Any]:
    """The headline numbers of one evaluation, as ForecastResult fields (rounded so the bytes are stable)."""
    r = ev.result
    n = r.n_paths
    successes = int(r.success.sum())
    lo, hi = wilson_interval(successes, n)
    failing = r.first_shortfall_age[~r.success]
    failing = failing[np.isfinite(failing)]
    return {
        "n_paths": n,
        "pos": round(successes / n, 6),
        "pos_ci": (round(lo, 6), round(hi, 6)),
        "goal_funding": {k: round(float(v.mean()), 6) for k, v in sorted(r.goal_funded.items())},
        "ages": [int(a) for a in r.ages],
        "net_worth_bands": _bands(r.net_worth_real),
        "shortfall": {  # §12.4: P(any), median first shortfall age, p90 total real shortfall
            "p_any": round(1 - successes / n, 6),
            "median_first_shortfall_age": _finite(float(np.median(failing)), 2) if failing.size else None,
            "p90_total_real_shortfall": round(float(np.percentile(r.total_shortfall_real, 90)), 0),
        },
        "liquidity_breach_prob": round(float(r.liquidity_breach.mean()), 6),
        "retirement_income_bands": _bands(r.retirement_income_real),
    }


def run_forecast(
    profile: Profile,
    *,
    base_document: F.FnaDocument | None = None,
    mode: Literal["preview", "final"] = "preview",
    config: ForecastConfig | None = None,
    cma: CMA | None = None,
    seed: int | None = None,
    n_paths: int | None = None,
    tax_model: TaxModel | None = None,
    overrides: dict[str, Override] | None = None,
    fna_analyse: FnaAnalyse | Literal[False] | None = None,
    fna_bands: FnaBands | Literal[False] | None = None,
) -> ForecastResult:
    """The Monte Carlo forecast of one profile version (§12): PoS with its Wilson interval, goal funding, bands,
    shortfall, liquidity breach, variance attribution and drivers, and the FNA's deterministic view beside it.

    ``overrides`` fixes facts (by fact id) or document fields (by concrete path) for what-if runs: a value fixes
    it on every path, a ``Dist``/``Quantity`` resamples it with the fact's own draws, ``"prior"`` replaces it with
    the slot's population prior.

    ``fna_analyse`` and ``fna_bands`` default to the ported FNA engine (§12.7, §12.8 "report both"); pass a
    hook to replace it or ``False`` to leave ``deterministic`` / ``need_bands`` empty.
    """
    from .attribution import attribute

    config = config or load_config()
    cma = cma or load_cma(config.cma_version)
    settings = config.mode(mode)
    seed = derive_seed(profile.run_id, profile.version) if seed is None else int(seed)
    plan = build_plan(profile, base_document=base_document, config=config, cma=cma, overrides=overrides)
    tax_model = tax_model or default_tax_model(str(plan.doc["assumptions"]["taxYear"]))
    fc = Forecaster(plan, tax_model)
    if fna_analyse is None:
        fna_analyse = _engine_hook("analyse")
    if fna_bands is None:
        fna_bands = _engine_hook("bands")

    n = int(n_paths or settings.n_paths)
    ev = fc.run(seed, n)
    while True:
        successes = int(ev.result.success.sum())
        lo, hi = wilson_interval(successes, n)
        if hi - lo <= config.pos_ci_max_width or n >= config.n_paths_max:
            break
        n = min(config.n_paths_max, max(n + 1_000, paths_for_width(successes / n, config.pos_ci_max_width)))
        ev = fc.run(seed, n)
    head = summarise(ev)
    attribution = attribute(fc, seed, settings)

    median_doc = F.FnaDocument.model_validate(plan.doc)
    deterministic: dict[str, float | None] = {}
    if fna_analyse:
        deterministic = deterministic_projection(_call(fna_analyse, median_doc, profile.anchor_date))
        deterministic.update(zero_volatility_capital(plan, tax_model))
    need_bands: dict[str, dict[str, float]] = {}
    if fna_bands:
        docs = binding.materialise_draws(profile, settings.need_bands_k, seed=seed, base=base_document)
        bands = _call(fna_bands, docs, profile.anchor_date)
        need_bands = {k: {q: round(float(v), 2) for q, v in sorted(d.items())} for k, d in sorted(bands.items())}

    return ForecastResult(
        run_id=profile.run_id,
        profile_version=str(profile.version),
        config_version=config.config_version,
        cma_version=cma.cma_version,
        seed=seed,
        variance_attribution=attribution.groups,
        market_attribution=attribution.market,
        top_drivers=attribution.drivers,
        deterministic=deterministic,
        need_bands=need_bands,
        **head,
    )


def return_model_check(
    profile: Profile,
    series: HistoricalSeries,
    *,
    base_document: F.FnaDocument | None = None,
    config: ForecastConfig | None = None,
    cma: CMA | None = None,
    seed: int | None = None,
    n_paths: int = 10_000,
    tax_model: TaxModel | None = None,
) -> dict[str, float]:
    """The quarterly return-model check (§12.6): PoS under MVN-t and under a block bootstrap of ``series``.

    The investment team supplies the real monthly series (SA returns, CPI and USDZAR since 2000); a gap above
    5 points for any gold household goes to them.
    """
    config = config or load_config()
    cma = cma or load_cma(config.cma_version)
    seed = derive_seed(profile.run_id, profile.version) if seed is None else int(seed)
    plan = build_plan(profile, base_document=base_document, config=config, cma=cma)
    tax_model = tax_model or default_tax_model(str(plan.doc["assumptions"]["taxYear"]))
    mvn = Forecaster(plan, tax_model)
    boot = Forecaster(plan, tax_model, market=StationaryBlockBootstrap(series, mvn.market_model))  # type: ignore[arg-type]
    pos_mvn = mvn.run(seed, n_paths).result.pos
    pos_boot = boot.run(seed, n_paths).result.pos
    return {"pos_mvn": round(pos_mvn, 6), "pos_bootstrap": round(pos_boot, 6), "gap_points": round(100 * abs(pos_mvn - pos_boot), 3)}
