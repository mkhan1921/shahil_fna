"""Distribution maths over the §3.3 types: sampling, CDFs, quantiles and mixtures.

Composite distributions (products, sums for derivations) are not closed-form, so they are
stored as a ``Mixture`` of 50 equally weighted quantile points fitted from 10 000 samples
(spec §8.3). Downstream code therefore only ever handles the base types.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from typing import Any

import numpy as np
from numpy.random import Generator
from scipy.special import ndtri

from .models.dists import Bernoulli, Categorical, Discrete, Dist, LogNormal, Mixture, Normal, Point, Triangular

N_QUANTILE_POINTS = 50
N_COMPOSITE_SAMPLES = 10_000

# --------------------------------------------------------------------------- dates as decimal years


def iso_to_year(s: str) -> float:
    """'1985-07-01' → 1985.496; a bare year '1985' → 1985.5."""
    s = s.strip()
    if len(s) == 4 and s.isdigit():
        return int(s) + 0.5
    d = date.fromisoformat(s[:10])
    start = date(d.year, 1, 1).toordinal()
    days = date(d.year + 1, 1, 1).toordinal() - start
    return d.year + (d.toordinal() - start) / days


def year_to_iso(y: float) -> str:
    year = math.floor(y)
    start = date(year, 1, 1).toordinal()
    days = date(year + 1, 1, 1).toordinal() - start
    return date.fromordinal(start + min(days - 1, max(0, round((y - year) * days)))).isoformat()


def as_number(v: bool | float | str) -> float:
    """A point value as a number: booleans as 0/1, ISO dates as decimal years."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except ValueError:
        return iso_to_year(v)


# --------------------------------------------------------------------------- type tests


def is_categorical(dist: Dist) -> bool:
    if isinstance(dist, Categorical):
        return True
    if isinstance(dist, Point):
        return isinstance(dist.value, str) and not _looks_like_date(dist.value)
    if isinstance(dist, Mixture):
        return all(is_categorical(d) for _, d in dist.components)
    return False


def _looks_like_date(s: str) -> bool:
    try:
        iso_to_year(s)
        return True
    except ValueError:
        return False


def is_boolean(dist: Dist) -> bool:
    if isinstance(dist, Bernoulli):
        return True
    if isinstance(dist, Point):
        return isinstance(dist.value, bool)
    if isinstance(dist, Mixture):
        return all(is_boolean(d) for _, d in dist.components)
    return False


# --------------------------------------------------------------------------- sampling


def _weights(ws: Sequence[float]) -> np.ndarray:
    """Weights as a probability vector: the models tolerate rounding noise (a weight of -1e-7, a sum of
    1 ± 1e-4), which ``Generator.choice`` does not."""
    w = np.clip(np.asarray(ws, dtype=float), 0.0, None)
    total = w.sum()
    return w / total if total > 0 else np.full(len(w), 1.0 / len(w))


def sample(dist: Dist, rng: Generator, n: int) -> np.ndarray:
    """n draws. Numeric dists give float arrays; categorical ones give object arrays of labels."""
    if isinstance(dist, Point):
        if isinstance(dist.value, str) and not _looks_like_date(dist.value):
            return np.full(n, dist.value, dtype=object)
        return np.full(n, as_number(dist.value), dtype=float)
    if isinstance(dist, Categorical):
        labels = list(dist.probs)
        p = _weights([dist.probs[k] for k in labels])
        return np.asarray(labels, dtype=object)[rng.choice(len(labels), size=n, p=p)]
    if isinstance(dist, Discrete):
        w = _weights(dist.weights)
        return np.asarray(dist.support, dtype=float)[rng.choice(len(w), size=n, p=w)]
    if isinstance(dist, Triangular):
        if dist.high - dist.low <= 0:
            return np.full(n, dist.mode, dtype=float)
        return rng.triangular(dist.low, dist.mode, dist.high, size=n)
    if isinstance(dist, LogNormal):
        return np.exp(dist.mu + dist.sigma * rng.standard_normal(n))
    if isinstance(dist, Normal):
        return dist.mean + dist.sd * rng.standard_normal(n)
    if isinstance(dist, Bernoulli):
        return (rng.random(n) < dist.p).astype(float)
    if isinstance(dist, Mixture):
        w = _weights([c[0] for c in dist.components])
        idx = rng.choice(len(w), size=n, p=w)
        categorical = is_categorical(dist)
        out = np.empty(n, dtype=object if categorical else float)
        for k, (_, comp) in enumerate(dist.components):
            mask = idx == k
            m = int(mask.sum())
            if m:
                out[mask] = sample(comp, rng, m)
        return out
    raise TypeError(f"unknown distribution {type(dist).__name__}")


def draw_one(dist: Dist) -> Callable[[Generator], Any]:
    """A function of an rng that returns ``sample(dist, rng, 1)[0]``: the same value from the same random stream,
    with the set-up ``sample`` repeats on every call (weight vectors, label arrays, the type tests) done once.
    ``binding.materialise_draws`` calls one of these per fact per draw, so each generator call below is the one
    ``sample`` makes for n = 1, in the same order."""
    if isinstance(dist, Point):
        fixed = sample(dist, None, 1)[0]  # type: ignore[arg-type]  # a point consumes no randomness
        return lambda rng: fixed
    if isinstance(dist, Categorical):
        labels = list(dist.probs)
        p = _weights([dist.probs[k] for k in labels])
        n_labels = len(labels)
        return lambda rng: labels[rng.choice(n_labels, size=1, p=p)[0]]
    if isinstance(dist, Discrete):
        w = _weights(dist.weights)
        support = np.asarray(dist.support, dtype=float)
        n_points = len(w)
        return lambda rng: support[rng.choice(n_points, size=1, p=w)][0]
    if isinstance(dist, Triangular):
        if dist.high - dist.low <= 0:
            flat = np.full(1, dist.mode, dtype=float)[0]
            return lambda rng: flat
        low, mode, high = dist.low, dist.mode, dist.high
        return lambda rng: rng.triangular(low, mode, high, size=1)[0]
    if isinstance(dist, LogNormal):
        mu, sigma = dist.mu, dist.sigma
        return lambda rng: np.exp(mu + sigma * rng.standard_normal(1))[0]
    if isinstance(dist, Normal):
        mean, sd = dist.mean, dist.sd
        return lambda rng: (mean + sd * rng.standard_normal(1))[0]
    if isinstance(dist, Bernoulli):
        p_event = dist.p
        return lambda rng: (rng.random(1) < p_event).astype(float)[0]
    if isinstance(dist, Mixture):
        w = _weights([c[0] for c in dist.components])
        n_components = len(w)
        parts = [draw_one(comp) for _, comp in dist.components]
        if is_categorical(dist):
            return lambda rng: parts[rng.choice(n_components, size=1, p=w)[0]](rng)
        # ``sample`` writes the chosen component's draw into a float array: the same float64.
        return lambda rng: np.float64(parts[rng.choice(n_components, size=1, p=w)[0]](rng))
    raise TypeError(f"unknown distribution {type(dist).__name__}")


# --------------------------------------------------------------------------- CDF and quantiles (numeric)


def _phi(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def cdf(dist: Dist, x: float) -> float:
    if isinstance(dist, Point):
        return 1.0 if x >= as_number(dist.value) else 0.0
    if isinstance(dist, Discrete):
        return float(sum(w for s, w in zip(dist.support, dist.weights) if s <= x))
    if isinstance(dist, Triangular):
        a, c, b = dist.low, dist.mode, dist.high
        if x <= a:
            return 0.0
        if x >= b:
            return 1.0
        if b == a:
            return 1.0
        if x <= c:
            return (x - a) ** 2 / ((b - a) * (c - a)) if c > a else 0.0
        return 1 - (b - x) ** 2 / ((b - a) * (b - c)) if b > c else 1.0
    if isinstance(dist, LogNormal):
        if x <= 0:
            return 0.0
        return _phi((math.log(x) - dist.mu) / dist.sigma) if dist.sigma > 0 else float(x >= dist.median)
    if isinstance(dist, Normal):
        return _phi((x - dist.mean) / dist.sd) if dist.sd > 0 else float(x >= dist.mean)
    if isinstance(dist, Bernoulli):
        return 0.0 if x < 0 else (1 - dist.p if x < 1 else 1.0)
    if isinstance(dist, Mixture):
        return sum(w * cdf(d, x) for w, d in dist.components)
    raise TypeError(f"no numeric CDF for {type(dist).__name__}")


def quantile(dist: Dist, q: float) -> float:
    q = min(1.0, max(0.0, q))
    if isinstance(dist, Point):
        return as_number(dist.value)
    if isinstance(dist, Discrete):
        order = np.argsort(dist.support)
        acc = 0.0
        for i in order:
            acc += dist.weights[i]
            if acc >= q - 1e-12:
                return float(dist.support[i])
        return float(dist.support[order[-1]])
    if isinstance(dist, Triangular):
        a, c, b = dist.low, dist.mode, dist.high
        if b == a:
            return a
        fc = (c - a) / (b - a)
        if q <= fc:
            return a + math.sqrt(q * (b - a) * (c - a))
        return b - math.sqrt((1 - q) * (b - a) * (b - c))
    if isinstance(dist, LogNormal):
        if q <= 0:
            return 0.0
        if q >= 1:
            return math.inf
        return math.exp(dist.mu + dist.sigma * float(ndtri(q)))
    if isinstance(dist, Normal):
        if q <= 0:
            return -math.inf
        if q >= 1:
            return math.inf
        return dist.mean + dist.sd * float(ndtri(q))
    if isinstance(dist, Bernoulli):
        return 1.0 if q > 1 - dist.p else 0.0
    if isinstance(dist, Mixture):
        return _mixture_quantile(dist, q)
    raise TypeError(f"no numeric quantile for {type(dist).__name__}")


def _mixture_quantile(dist: Mixture, q: float) -> float:
    eps = 1e-9
    lo = min(quantile(d, max(eps, 1e-6)) for _, d in dist.components)
    hi = max(quantile(d, min(1 - eps, 1 - 1e-6)) for _, d in dist.components)
    if not math.isfinite(lo) or not math.isfinite(hi):
        # Unbounded components: fall back to sampling.
        xs = sample(dist, np.random.default_rng(0), 20_000).astype(float)
        return float(np.quantile(xs, q))
    if hi <= lo:
        return lo
    # Bisection on the mixture CDF; step functions (points, discrete) need the smallest x with F(x) >= q.
    for _ in range(80):
        mid = (lo + hi) / 2
        if cdf(dist, mid) >= q - 1e-12:
            hi = mid
        else:
            lo = mid
    return hi


def median(dist: Dist) -> float:
    return quantile(dist, 0.5)


def mean(dist: Dist) -> float:
    if isinstance(dist, Point):
        return as_number(dist.value)
    if isinstance(dist, Discrete):
        return float(np.dot(dist.support, dist.weights))
    if isinstance(dist, Triangular):
        return (dist.low + dist.mode + dist.high) / 3
    if isinstance(dist, LogNormal):
        return math.exp(dist.mu + dist.sigma**2 / 2)
    if isinstance(dist, Normal):
        return dist.mean
    if isinstance(dist, Bernoulli):
        return dist.p
    if isinstance(dist, Mixture):
        return sum(w * mean(d) for w, d in dist.components)
    raise TypeError(f"no numeric mean for {type(dist).__name__}")


def summary(dist: Dist) -> dict[str, float]:
    """p10 / p50 / p90 of a numeric distribution."""
    return {"p10": quantile(dist, 0.1), "p50": quantile(dist, 0.5), "p90": quantile(dist, 0.9)}


# --------------------------------------------------------------------------- categorical and boolean


def categorical_probs(dist: Dist) -> dict[str, float]:
    if isinstance(dist, Categorical):
        return dict(dist.probs)
    if isinstance(dist, Point):
        return {str(dist.value): 1.0}
    if isinstance(dist, Mixture):
        out: dict[str, float] = {}
        for w, d in dist.components:
            for k, p in categorical_probs(d).items():
                out[k] = out.get(k, 0.0) + w * p
        return out
    raise TypeError(f"{type(dist).__name__} is not categorical")


def mode_label(dist: Dist) -> str:
    probs = categorical_probs(dist)
    return max(probs, key=lambda k: probs[k])


def p_true(dist: Dist) -> float:
    if isinstance(dist, Bernoulli):
        return dist.p
    if isinstance(dist, Point):
        return 1.0 if bool(dist.value) else 0.0
    if isinstance(dist, Mixture):
        return sum(w * p_true(d) for w, d in dist.components)
    raise TypeError(f"{type(dist).__name__} is not boolean")


# --------------------------------------------------------------------------- building distributions


def mixture(components: Sequence[tuple[float, Dist]]) -> Dist:
    """Weighted mixture with weights renormalised; a single component is returned as itself."""
    comps = [(float(w), d) for w, d in components if w > 0]
    if not comps:
        raise ValueError("mixture needs a positive weight")
    total = sum(w for w, _ in comps)
    comps = [(w / total, d) for w, d in comps]
    if len(comps) == 1:
        return comps[0][1]
    # Merge identical point components so a confirmed value does not look like two hypotheses.
    merged: list[tuple[float, Dist]] = []
    for w, d in comps:
        for i, (w2, d2) in enumerate(merged):
            if d2 == d:
                merged[i] = (w2 + w, d2)
                break
        else:
            merged.append((w, d))
    if len(merged) == 1:
        return merged[0][1]
    return Mixture(components=merged)


def fit_quantile_mixture(samples: Iterable[float], n_points: int = N_QUANTILE_POINTS) -> Dist:
    """A ``Mixture`` of ``n_points`` equally weighted points at the samples' quantiles."""
    xs = np.asarray(list(samples) if not isinstance(samples, np.ndarray) else samples, dtype=float)
    xs = xs[np.isfinite(xs)]
    if xs.size == 0:
        raise ValueError("no finite samples")
    if np.ptp(xs) == 0:
        return Point(value=float(xs[0]))
    qs = (np.arange(n_points) + 0.5) / n_points
    pts = np.quantile(xs, qs)
    w = 1.0 / n_points
    comps: list[tuple[float, Dist]] = []
    for p in pts:
        if comps and math.isclose(comps[-1][1].value, float(p), rel_tol=0, abs_tol=1e-9):  # type: ignore[union-attr]
            comps[-1] = (comps[-1][0] + w, comps[-1][1])
        else:
            comps.append((w, Point(value=float(p))))
    total = sum(c[0] for c in comps)
    comps = [(c[0] / total, c[1]) for c in comps]
    return comps[0][1] if len(comps) == 1 else Mixture(components=comps)


def composite(fn, *dists: Dist, seed: int = 0, n: int = N_COMPOSITE_SAMPLES) -> Dist:
    """Push samples of ``dists`` through ``fn`` and store the result as a quantile mixture."""
    rng = np.random.default_rng(seed)
    draws = [sample(d, rng, n).astype(float) for d in dists]
    return fit_quantile_mixture(fn(*draws))


def product(a: Dist, b: Dist, seed: int = 0) -> Dist:
    return composite(lambda x, y: x * y, a, b, seed=seed)


def total(dists: Sequence[Dist], seed: int = 0) -> Dist:
    return composite(lambda *xs: np.sum(xs, axis=0), *dists, seed=seed)


def scaled(dist: Dist, factor: float) -> Dist:
    """Multiply a numeric distribution by a constant, keeping its type where possible."""
    if isinstance(dist, Point):
        return Point(value=as_number(dist.value) * factor)
    if isinstance(dist, Discrete):
        return Discrete(support=[s * factor for s in dist.support], weights=list(dist.weights))
    if isinstance(dist, Triangular) and factor >= 0:
        return Triangular(low=dist.low * factor, mode=dist.mode * factor, high=dist.high * factor)
    if isinstance(dist, LogNormal) and factor > 0:
        return LogNormal(median=dist.median * factor, p10=dist.p10 * factor, p90=dist.p90 * factor)
    if isinstance(dist, Normal):
        return Normal(mean=dist.mean * factor, sd=abs(dist.sd * factor))
    if isinstance(dist, Mixture):
        return Mixture(components=[(w, scaled(d, factor)) for w, d in dist.components])
    return composite(lambda x: x * factor, dist)


def shifted(dist: Dist, offset: float) -> Dist:
    if isinstance(dist, Point):
        return Point(value=as_number(dist.value) + offset)
    if isinstance(dist, Discrete):
        return Discrete(support=[s + offset for s in dist.support], weights=list(dist.weights))
    if isinstance(dist, Triangular):
        return Triangular(low=dist.low + offset, mode=dist.mode + offset, high=dist.high + offset)
    if isinstance(dist, Normal):
        return Normal(mean=dist.mean + offset, sd=dist.sd)
    if isinstance(dist, Mixture):
        return Mixture(components=[(w, shifted(d, offset)) for w, d in dist.components])
    return composite(lambda x: x + offset, dist)


def uniform_discrete(low: float, high: float, n: int = 13) -> Discrete:
    """A uniform spread as an equally weighted discrete grid (used for ±6-month dates)."""
    support = list(np.linspace(low, high, n))
    return Discrete(support=[float(s) for s in support], weights=[1.0 / n] * n)


def truncate(dist: Dist, low: float | None = None, high: float | None = None, seed: int = 0) -> Dist:
    """Condition a numeric distribution on low <= x <= high (used for 'at least' bounds)."""
    rng = np.random.default_rng(seed)
    xs = sample(dist, rng, N_COMPOSITE_SAMPLES * 4).astype(float)
    mask = np.ones_like(xs, dtype=bool)
    if low is not None:
        mask &= xs >= low
    if high is not None:
        mask &= xs <= high
    kept = xs[mask]
    if kept.size < 50:
        # The bound sits far in the tail: the bound itself is the best single reading.
        edge = low if low is not None else high
        return Point(value=float(edge))
    return fit_quantile_mixture(kept)


def interval_contains(dist: Dist, x: float, lo_q: float = 0.1, hi_q: float = 0.9) -> bool:
    return quantile(dist, lo_q) - 1e-9 <= x <= quantile(dist, hi_q) + 1e-9
