from __future__ import annotations

import math

import numpy as np
import pytest

from advisor_intel import stats
from advisor_intel.models.dists import Bernoulli, Categorical, Discrete, LogNormal, Mixture, Normal, Point, Triangular


def test_dates_as_decimal_years_round_trip():
    for iso in ("1986-04-12", "2000-01-01", "2026-12-31", "2024-02-29"):
        assert stats.year_to_iso(stats.iso_to_year(iso)) == iso
    assert stats.iso_to_year("1985") == 1985.5


@pytest.mark.parametrize(
    "dist",
    [
        Triangular(low=60, mode=62, high=65),
        Normal(mean=10, sd=2),
        LogNormal(median=100, p10=80, p90=125),
        Discrete(support=[2, 3, 5], weights=[0.5, 0.3, 0.2]),
    ],
)
def test_quantile_inverts_cdf(dist):
    for q in (0.1, 0.25, 0.5, 0.75, 0.9):
        x = stats.quantile(dist, q)
        assert stats.cdf(dist, x) >= q - 1e-9
        if not isinstance(dist, Discrete):
            assert math.isclose(stats.cdf(dist, x), q, abs_tol=1e-9)


def test_lognormal_recovers_symmetric_quantiles():
    d = LogNormal(median=100, p10=80, p90=125)
    assert math.isclose(stats.quantile(d, 0.1), 80, rel_tol=1e-6)
    assert math.isclose(stats.quantile(d, 0.9), 125, rel_tol=1e-6)


def test_sampling_matches_mean():
    rng = np.random.default_rng(1)
    for d in (Triangular(low=0, mode=1, high=5), Normal(mean=3, sd=1), Bernoulli(p=0.3), LogNormal(median=1, p10=0.8, p90=1.25)):
        xs = stats.sample(d, rng, 200_000)
        assert math.isclose(xs.mean(), stats.mean(d), rel_tol=0.02, abs_tol=0.01)


def test_sampling_tolerates_rounding_noise_in_weights():
    # The models accept weights off by the validation tolerance; sampling must not raise on a -5e-7.
    rng = np.random.default_rng(0)
    assert set(stats.sample(Categorical(probs={"a": 1.0000005, "b": -5e-7}), rng, 50)) == {"a"}
    assert set(stats.sample(Discrete(support=[1, 2], weights=[1.0000005, -5e-7]), rng, 50)) == {1.0}
    assert set(stats.sample(Mixture(components=[(1.0000005, Point(value=1)), (-5e-7, Point(value=2))]), rng, 50)) == {1.0}


def test_mixture_quantile_is_bimodal():
    m = Mixture(components=[(0.5, Point(value=60)), (0.5, Point(value=65))])
    assert stats.quantile(m, 0.25) == pytest.approx(60)
    assert stats.quantile(m, 0.75) == pytest.approx(65)
    assert stats.median(m) == pytest.approx(60)


def test_mixture_helper_merges_and_collapses():
    assert stats.mixture([(1.0, Point(value=3))]) == Point(value=3)
    assert stats.mixture([(0.3, Point(value=3)), (0.7, Point(value=3))]) == Point(value=3)
    m = stats.mixture([(2, Point(value=1)), (2, Point(value=2))])
    assert isinstance(m, Mixture) and [w for w, _ in m.components] == [0.5, 0.5]


def test_categorical_helpers():
    m = Mixture(components=[(0.5, Categorical(probs={"a": 1.0})), (0.5, Categorical(probs={"a": 0.2, "b": 0.8}))])
    assert stats.is_categorical(m)
    assert stats.categorical_probs(m) == pytest.approx({"a": 0.6, "b": 0.4})
    assert stats.mode_label(m) == "a"
    rng = np.random.default_rng(0)
    draws = stats.sample(m, rng, 10_000)
    assert abs((draws == "a").mean() - 0.6) < 0.02


def test_product_of_distributions_is_quantile_mixture():
    ref = Point(value=30_000)
    rel = Triangular(low=0.75, mode=0.88, high=0.97)
    d = stats.product(ref, rel)
    assert isinstance(d, Mixture) and len(d.components) == stats.N_QUANTILE_POINTS
    assert stats.median(d) == pytest.approx(30_000 * stats.median(rel), rel=0.01)
    assert stats.quantile(d, 0.1) == pytest.approx(30_000 * stats.quantile(rel, 0.1), rel=0.02)


def test_truncate_for_bounds():
    prior = Triangular(low=0, mode=500, high=2000)
    d = stats.truncate(prior, low=1000)
    assert stats.quantile(d, 0.0) >= 1000 - 1e-6
    far = stats.truncate(Normal(mean=0, sd=1), low=50)
    assert far == Point(value=50.0)


def test_scaled_and_shifted_keep_types():
    t = Triangular(low=1, mode=2, high=3)
    assert stats.scaled(t, 12) == Triangular(low=12, mode=24, high=36)
    assert stats.shifted(t, 1) == Triangular(low=2, mode=3, high=4)
    assert stats.scaled(LogNormal(median=2, p10=1, p90=4), 2) == LogNormal(median=4, p10=2, p90=8)


def test_uniform_discrete_for_dates():
    d = stats.uniform_discrete(1985.0, 1986.0)
    assert len(d.support) == 13 and stats.mean(d) == pytest.approx(1985.5)


def test_interval_contains():
    d = Triangular(low=60, mode=62, high=65)
    assert stats.interval_contains(d, 62)
    assert not stats.interval_contains(d, 65)
