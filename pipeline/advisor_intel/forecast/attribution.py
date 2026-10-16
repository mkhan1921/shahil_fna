"""Variance attribution (spec §12.5).

Every path's random inputs fall into three independent groups:

- **M (market)**: asset-class returns, inflation and the rand (``MarketShocks``);
- **C (client content)**: draws within each fact's content distribution, the gross/net coin, event
  occurrences and timing (each fact's c1, c2 uniforms);
- **E (extraction)**: each fact's use-extracted-or-prior indicator and its replacement draw from the population
  prior (e1, e2) — the uncertainty that exists only because the fact may have been misheard.

The output is the path's funded ratio (assets plus PV of future income over PV of essential spending), which
is continuous and better behaved than the 0/1 success flag.

First-order Sobol indices per group use the pick-freeze (Saltelli 2010) estimator with two independent input
matrices A and B of N paths: S_g = mean((Y_A − mean Y)(Y_B^(g←A) − Y_B)) / Var(Y), where Y_B^(g←A) uses B's
inputs except group g, taken from A. Cost (2 + 3)·N path evaluations; ``interaction = 1 − ΣS``.
``top_drivers`` reuses A and B with one tier A fact's C and E draws taken from A.

Asset-class returns are correlated, so first-order indices would misattribute variance between them; within M
the split uses Shapley effects (Song, Nelson & Staum 2016, random permutations, one outer and three inner
samples), estimated on a reduced run whose client inputs are held at a context drawn per permutation.
Their raw values sum to the market variance of that run; they are reported as shares of it times S_M, so the
market split adds up to the market share the review sentence shows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import ModeSettings
from .returns import PLAYERS, MarketModel, MarketShocks
from .run import Forecaster
from .sampling import Uniforms, stream

GROUPS: dict[str, str] = {"M": "market", "C": "client_content", "E": "extraction"}
DRIVER_SCREEN = 3  # evaluate at most this many times the reported number of drivers, screened by rank correlation


@dataclass
class Attribution:
    groups: dict[str, float]  # {"market", "client_content", "extraction", "interaction"}
    market: dict[str, float]  # Shapley effects within M, summing to the market share
    drivers: list[tuple[str, float]]  # tier A fact id, first-order index
    variance: float = 0.0  # Var(funded ratio)
    diagnostics: dict[str, float] = field(default_factory=dict)


def pick_freeze(y_a: np.ndarray, y_b: np.ndarray, y_mix: np.ndarray, mean: float, variance: float) -> float:
    """First-order index from Y_A, Y_B and Y_B^(g←A) (Saltelli 2010 estimator, centred)."""
    if variance <= 0:
        return 0.0
    return float(np.mean((y_a - mean) * (y_mix - y_b)) / variance)


@dataclass
class SobolDesign:
    """The two input matrices and their outputs, kept so the per-fact drivers can reuse them."""

    u_a: Uniforms
    u_b: Uniforms
    m_a: MarketShocks
    m_b: MarketShocks
    y_a: np.ndarray
    y_b: np.ndarray
    mean: float
    variance: float
    n_years: int


def _years(fc: Forecaster, *uniforms: Uniforms) -> int:
    return max(fc.plan.realise(u, fc.tax).n_years for u in uniforms)


def sobol_groups(fc: Forecaster, seed: int, n: int) -> tuple[dict[str, float], SobolDesign]:
    """First-order Sobol indices of M, C and E on the funded ratio (§12.5), and the design for the drivers."""
    u_a = fc.uniforms(seed, n, matrix="A")
    u_b = fc.uniforms(seed, n, matrix="B")
    years = _years(fc, u_a, u_b)
    m_a = fc.shocks(seed, n, years, matrix="A")
    m_b = fc.shocks(seed, n, years, matrix="B")
    facts = list(u_a.facts)
    y_a, y_b, y_m, y_c, y_e = fc.funded_batch(
        [
            (u_a, m_a),
            (u_b, m_b),
            (u_b, m_a),  # market from A
            (u_b.swap(u_a, content=facts), m_b),  # client content from A
            (u_b.swap(u_a, extraction=facts), m_b),  # extraction indicators from A
        ],
        years,
    )
    pooled = np.concatenate([y_a, y_b])
    mean = float(pooled.mean())
    var = float(pooled.var(ddof=1)) if pooled.size > 1 else 0.0
    raw = {"market": pick_freeze(y_a, y_b, y_m, mean, var), "client_content": pick_freeze(y_a, y_b, y_c, mean, var),
           "extraction": pick_freeze(y_a, y_b, y_e, mean, var)}
    return raw, SobolDesign(u_a, u_b, m_a, m_b, y_a, y_b, mean, var, years)  # type: ignore[arg-type]


def top_drivers(fc: Forecaster, design: SobolDesign, *, limit: int) -> list[tuple[str, float]]:
    """First-order index of each tier A fact (its C and E draws from A, everything else from B)."""
    candidates = [f for f in fc.plan.tier_a_random() if f in design.u_a.facts]
    if not candidates or design.variance <= 0 or limit <= 0:
        return []
    budget = max(limit * DRIVER_SCREEN, limit)
    if len(candidates) > budget:
        # Screen by rank correlation of the funded ratio with the fact's own uniforms (a lower bound on S_f).
        ranks = np.argsort(np.argsort(design.y_a)) / max(len(design.y_a) - 1, 1)
        def score(fid: str) -> float:
            u = design.u_a.facts[fid]
            return max(abs(np.corrcoef(ranks, u.c1)[0, 1]), abs(np.corrcoef(ranks, u.e1)[0, 1]), abs(np.corrcoef(ranks, u.c2)[0, 1]))
        candidates = sorted(candidates, key=lambda f: (-np.nan_to_num(score(f)), f))[:budget]
    ys = fc.funded_batch([(design.u_b.swap(design.u_a, content=[f], extraction=[f]), design.m_b) for f in candidates], design.n_years)
    out = [(f, max(0.0, round(pick_freeze(design.y_a, design.y_b, y, design.mean, design.variance), 6))) for f, y in zip(candidates, ys)]
    out.sort(key=lambda kv: (-kv[1], kv[0]))
    return out[:limit]


def shapley_market(fc: Forecaster, seed: int, settings: ModeSettings) -> tuple[dict[str, float], dict[str, float]]:
    """Shapley effects of the market inputs (each asset class, FX, inflation) as shares of the market variance.

    Random permutations with one outer and ``shapley_inner`` inner samples per position (Song, Nelson & Staum
    2016): c(J) = E[Var(Y | inputs outside J)] with J's inputs drawn from their Gaussian conditional given the
    others. Client inputs are held at one context per permutation; the full-set value is the mean
    within-context market variance from ``shapley_paths`` paths. The run uses the Gaussian version of the market
    (same covariance, unit t multiplier): the multiplier scales every class together, so it does not change the
    split, only the noise of its estimate. Returns (shares, diagnostics).
    """
    model = fc.gaussian
    if model is None or settings.shapley_permutations <= 0:
        return {}, {}
    d = len(PLAYERS)
    n_ctx = max(2, settings.shapley_contexts)
    reps = max(2, settings.shapley_paths // n_ctx)
    inner = max(2, settings.shapley_inner)
    n_perm = settings.shapley_permutations
    u_ctx = fc.uniforms(seed, n_ctx, matrix="shapley-context")
    years = _years(fc, u_ctx)

    # Full-set value: the market variance within each context, averaged.
    rows_v = np.repeat(np.arange(n_ctx), reps)
    xv = model.players(model.shocks(seed, len(rows_v), years, matrix="shapley-total"))
    y_v = _funded(fc, u_ctx.take(rows_v), model, xv)
    v_total = float(np.mean(y_v.reshape(n_ctx, reps).var(axis=1, ddof=1)))
    if v_total <= 0:
        return {p: 0.0 for p in PLAYERS}, {"shapley_variance": 0.0}

    perms = _permutations(seed, d, n_perm)
    n_perm = len(perms)
    positions = d - 1  # the last position is the full set, known from v_total
    # One outer sample and one set of inner noises per permutation, shared by its positions: every c(J) estimate
    # stays unbiased, and the differences between neighbouring positions are much less noisy.
    outer = model.players(model.shocks(seed, n_perm, years, matrix="shapley-outer"))  # (7, n_perm, T)
    noise = model.shocks(seed, n_perm * inner, years, matrix="shapley-inner")
    rows = n_perm * positions * inner
    perm_of = np.repeat(np.arange(n_perm), positions * inner)
    pos_of = np.tile(np.repeat(np.arange(1, d), inner), n_perm)  # j = 1..d-1 players varied
    noise_of = perm_of * inner + np.tile(np.arange(inner), n_perm * positions)
    x = outer[:, perm_of].copy()
    keys: dict[frozenset[int], list[int]] = {}
    for r in range(rows):
        keys.setdefault(frozenset(int(i) for i in perms[perm_of[r], : pos_of[r]]), []).append(r)
    for varied, idx_list in keys.items():
        idx = np.asarray(idx_list)
        v, f, a, low = model.conditional(varied)
        if len(v):
            xi = noise.z[: len(v)][:, noise_of[idx]]  # independent normals for the varied correlated players
            cond = np.einsum("vk,krt->vrt", a, x[f][:, idx]) if len(f) else 0.0
            x[v[:, None], idx[None, :]] = cond + np.einsum("vw,wrt->vrt", low, xi)
        if 6 in varied:
            x[6, idx] = noise.e[noise_of[idx]]  # inflation innovations are independent of the rest
    ctx = perm_of % n_ctx
    y = _funded(fc, u_ctx.take(ctx), model, x).reshape(n_perm, positions, inner)
    c = np.zeros((n_perm, d + 1))
    c[:, 1:d] = y.var(axis=2, ddof=1)
    c[:, d] = v_total
    shapley = np.zeros(d)
    for k in range(n_perm):
        for j in range(1, d + 1):
            shapley[perms[k, j - 1]] += c[k, j] - c[k, j - 1]
    shapley /= n_perm
    raw = shapley / v_total
    # Shapley effects are non-negative; a small negative estimate is noise. Project onto that constraint while
    # keeping the total (the market's whole variance) unchanged.
    clipped = np.maximum(raw, 0.0)
    shares = clipped * (raw.sum() / clipped.sum()) if clipped.sum() > 0 else clipped
    diag = {"shapley_variance": v_total, "shapley_sum": float(shapley.sum())} | {f"shapley_raw_{n}": float(raw[i]) for i, n in enumerate(PLAYERS)}
    return {name: float(shares[i]) for i, name in enumerate(PLAYERS)}, diag


def _permutations(seed: int, d: int, n: int) -> np.ndarray:
    """``n`` permutations, position-balanced: the d cyclic shifts of each random base permutation, so every input
    appears in every position equally often (removes the position-sampling part of the estimator's noise)."""
    g = stream(seed, "shapley-permutations")
    bases = max(1, round(n / d))
    out = [np.roll(base, k) for base in (g.permutation(d) for _ in range(bases)) for k in range(d)]
    return np.stack(out)


def _funded(fc: Forecaster, uniforms: Uniforms, model: MarketModel, players: np.ndarray) -> np.ndarray:
    """Funded ratios on the Gaussian version of the market built from ``players``."""
    market = model.realise_players(players, None)
    out = []
    step = 6_000
    for i in range(0, uniforms.n, step):
        rows = np.arange(i, min(uniforms.n, i + step))
        out.append(fc.evaluate_market_paths(uniforms.take(rows), market.take(rows)).result.funded_ratio)
    return np.concatenate(out) if out else np.zeros(0)


def attribute(fc: Forecaster, seed: int, settings: ModeSettings) -> Attribution:
    """The §12.5 attribution of one forecast: group indices, drivers and the market split."""
    raw, design = sobol_groups(fc, seed, settings.attribution_n)
    groups = {k: round(min(1.0, max(0.0, v)), 4) for k, v in raw.items()}
    groups["interaction"] = round(max(0.0, 1.0 - sum(groups.values())), 4) if design.variance > 0 else 0.0
    drivers = top_drivers(fc, design, limit=settings.top_drivers)
    shares, diag = shapley_market(fc, seed, settings) if design.variance > 0 else ({}, {})
    market = {k: round(v * groups["market"], 4) for k, v in shares.items()}
    diagnostics = {f"raw_{k}": v for k, v in raw.items()} | {"variance": design.variance} | diag
    return Attribution(groups=groups, market=market, drivers=drivers, variance=design.variance, diagnostics=diagnostics)


def attribution_sentence(variance_attribution: dict[str, float]) -> str:
    """The review UI's line (§12.5): markets, what the client hasn't decided, facts we may have misheard."""
    parts = [max(0.0, float(variance_attribution.get(k, 0.0))) for k in ("market", "client_content", "extraction")]
    total = sum(parts)
    if total <= 0:
        return "There is no measurable uncertainty in this plan's outcome."
    pct = _largest_remainder([100 * p / total for p in parts])
    return (
        f"Of the uncertainty in this plan, about {pct[0]}% is markets, {pct[1]}% is what the client hasn't decided, "
        f"{pct[2]}% is facts we may have misheard."
    )


def _largest_remainder(values: list[float]) -> list[int]:
    """Whole percentages that add up to 100."""
    floors = [int(np.floor(v)) for v in values]
    short = 100 - sum(floors)
    order = sorted(range(len(values)), key=lambda i: (-(values[i] - floors[i]), i))
    for i in order[: max(0, short)]:
        floors[i] += 1
    return floors
