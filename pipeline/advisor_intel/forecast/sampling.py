"""Uniform-driven sampling over the §3.3 distributions, and the random streams behind it (spec §12.2, §12.5).

Every random input of a forecast is a uniform (or, for markets, a normal) drawn from a stream named by the
run seed and a tag, so:

- the same seed gives the same draws (determinism, §12.6);
- a fact's draws depend only on the seed and its fact id, never on which other facts exist, so sensitivity
  runs that change one fact reuse every other draw (common random numbers, §10.2);
- the variance attribution can swap one group of inputs between two independent input matrices (§12.5).

``ppf`` maps uniforms to values by inverse CDF. A ``Mixture`` picks the hypothesis first and then samples
within it (the rescaled remainder of the same uniform), so "retire at 60 or at 65" stays bimodal, exactly
as ``stats.sample`` does with a generator.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Literal

import numpy as np
from scipy.special import ndtri

from .. import stats
from ..models.dists import Bernoulli, Categorical, Discrete, Dist, LogNormal, Mixture, Normal, Point, Triangular

ValueKind = Literal["numeric", "categorical", "boolean"]
_EPS = 1e-12


def _tag(s: str) -> int:
    """A stable 63-bit integer for a string (Python's hash() is salted per process)."""
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "little") >> 1


def stream(seed: int, *tags: str) -> np.random.Generator:
    """An independent generator for (seed, tags...)."""
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence([int(seed) & ((1 << 63) - 1), *(_tag(t) for t in tags)])))


def derive_seed(*parts: object) -> int:
    """A 63-bit seed from labels, e.g. (run_id, profile version) (§12.6 deterministic seed per run)."""
    return _tag("|".join(str(p) for p in parts))


@dataclass(frozen=True)
class FactUniforms:
    """The uniforms behind one fact on every path.

    c1, c2 belong to group C (client content: the draw within the content distribution, the gross/net coin,
    event occurrence and timing); e1, e2 to group E (extraction: the use-extracted-or-prior indicator and the
    replacement draw from the population prior).
    """

    c1: np.ndarray
    c2: np.ndarray
    e1: np.ndarray
    e2: np.ndarray

    def take(self, rows: np.ndarray) -> "FactUniforms":
        return FactUniforms(self.c1[rows], self.c2[rows], self.e1[rows], self.e2[rows])

    def mix(self, content_from: "FactUniforms", extraction_from: "FactUniforms") -> "FactUniforms":
        return FactUniforms(content_from.c1, content_from.c2, extraction_from.e1, extraction_from.e2)


@dataclass(frozen=True)
class Uniforms:
    """The C and E uniforms of every fact in a plan, for ``n`` paths (one input matrix of §12.5)."""

    n: int
    facts: dict[str, FactUniforms]

    def take(self, rows: np.ndarray) -> "Uniforms":
        return Uniforms(len(rows), {k: v.take(rows) for k, v in self.facts.items()})

    def swap(self, other: "Uniforms", *, content: Iterable[str] = (), extraction: Iterable[str] = ()) -> "Uniforms":
        """These uniforms with the named facts' content (C) and/or extraction (E) draws taken from ``other``."""
        content, extraction = set(content), set(extraction)
        out = {}
        for k, v in self.facts.items():
            c = other.facts[k] if k in content else v
            e = other.facts[k] if k in extraction else v
            out[k] = v if (c is v and e is v) else v.mix(c, e)
        return Uniforms(self.n, out)


def fact_uniforms(seed: int, fact_id: str, n: int, *, matrix: str = "") -> FactUniforms:
    """The uniforms of one fact for ``n`` paths; ``matrix`` names an independent input matrix (attribution)."""
    gc = stream(seed, "content", matrix, fact_id)
    ge = stream(seed, "extraction", matrix, fact_id)
    return FactUniforms(gc.random(n), gc.random(n), ge.random(n), ge.random(n))


# --------------------------------------------------------------------------- inverse CDF


def value_kind(dist: Dist) -> ValueKind:
    if stats.is_boolean(dist):
        return "boolean"
    if stats.is_categorical(dist):
        return "categorical"
    return "numeric"


def ppf(dist: Dist, u: np.ndarray) -> np.ndarray:
    """Values at uniforms ``u`` (vectorised inverse CDF).

    Numeric and boolean distributions give float arrays (booleans as 0/1, ISO dates as decimal years);
    categorical ones give object arrays of labels. Monotone in ``u`` within each component, so a fact's p10
    and p90 come from the same uniforms on every run.
    """
    u = np.clip(np.asarray(u, dtype=float), _EPS, 1 - _EPS)
    if isinstance(dist, Point):
        v = dist.value
        if isinstance(v, str) and not stats._looks_like_date(v):  # noqa: SLF001
            return np.full(u.shape, v, dtype=object)
        return np.full(u.shape, stats.as_number(v), dtype=float)
    if isinstance(dist, Bernoulli):
        return (u >= 1 - dist.p).astype(float)
    if isinstance(dist, Categorical):
        labels = np.asarray(list(dist.probs), dtype=object)
        cum = np.cumsum([dist.probs[k] for k in dist.probs], dtype=float)
        cum /= cum[-1]
        idx = np.minimum(np.searchsorted(cum, u, side="right"), len(labels) - 1)
        return labels[idx]
    if isinstance(dist, Discrete):
        order = np.argsort(dist.support, kind="stable")
        support = np.asarray(dist.support, dtype=float)[order]
        cum = np.cumsum(np.asarray(dist.weights, dtype=float)[order])
        cum /= cum[-1]
        idx = np.minimum(np.searchsorted(cum, u, side="left"), len(support) - 1)
        return support[idx]
    if isinstance(dist, Triangular):
        a, c, b = dist.low, dist.mode, dist.high
        if b - a <= 0:
            return np.full(u.shape, c, dtype=float)
        fc = (c - a) / (b - a)
        left = a + np.sqrt(u * (b - a) * (c - a))
        right = b - np.sqrt((1 - u) * (b - a) * (b - c))
        return np.where(u <= fc, left, right)
    if isinstance(dist, LogNormal):
        return np.exp(dist.mu + dist.sigma * ndtri(u))
    if isinstance(dist, Normal):
        return dist.mean + dist.sd * ndtri(u)
    if isinstance(dist, Mixture):
        weights = np.asarray([w for w, _ in dist.components], dtype=float)
        cum = np.cumsum(weights)
        cum /= cum[-1]
        k = np.minimum(np.searchsorted(cum, u, side="right"), len(weights) - 1)
        lower = np.where(k > 0, cum[np.maximum(k - 1, 0)], 0.0)
        within = (u - lower) / np.maximum(cum[k] - lower, _EPS)  # the remainder of u, uniform within the hypothesis
        categorical = stats.is_categorical(dist)
        out = np.empty(u.shape, dtype=object if categorical else float)
        for i, (_, comp) in enumerate(dist.components):
            mask = k == i
            if mask.any():
                out[mask] = ppf(comp, within[mask])
        return out
    raise TypeError(f"unknown distribution {type(dist).__name__}")


def as_float(values: np.ndarray) -> np.ndarray:
    """Values as floats: numeric labels ('3' for a count) and booleans convert; other labels raise."""
    if values.dtype != object:
        return values.astype(float)
    return np.array([float(v) if not isinstance(v, bool) else float(v) for v in values], dtype=float)
