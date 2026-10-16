"""Forecast configuration, market model, block bootstrap, sampling and the Wilson interval (spec §12.1, §12.6)."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats as sps

from advisor_intel.forecast.config import ASSET_CLASSES, CMA, allocation_vector, glide_weights, load_cma, load_config
from advisor_intel.forecast.returns import HistoricalSeries, MarketModel, StationaryBlockBootstrap
from advisor_intel.forecast.run import paths_for_width, wilson_interval
from advisor_intel.forecast.sampling import derive_seed, fact_uniforms, ppf
from advisor_intel.models.dists import Bernoulli, Categorical, Discrete, LogNormal, Mixture, Normal, Point, Triangular

CFG = load_config()
CMA_ = load_cma()


# --------------------------------------------------------------------------- config and CMA


def test_config_and_cma_are_version_pinned():
    assert CFG.config_version == "config_v1" and CFG.cma_version == "2026Q4" == CMA_.cma_version
    assert CFG.asset_classes == ASSET_CLASSES
    assert CFG.inflation.phi == 0.6 and CFG.inflation.sd == 0.015 and CFG.inflation.mean_for(0.05) == 0.05
    assert CFG.fx_usdzar.drift_real == 0.01 and CFG.fx_usdzar.vol == 0.15
    assert CFG.fat_tails.df == 5 and CFG.success_definition.allow_property_sale is False
    assert CFG.mode("final").attribution_n == 2000 and CFG.mode("final").shapley_permutations == 200
    assert np.linalg.eigvalsh(CMA_.joint_correlation()).min() > 0
    with pytest.raises(KeyError):
        CFG.mode("nope")


def test_cma_rejects_a_correlation_that_is_not_positive_definite():
    data = CMA_.model_dump(mode="json")
    bad = [list(r) for r in data["correlation"]]
    bad[0][3] = bad[3][0] = 0.99
    bad[0][1] = bad[1][0] = -0.99
    bad[1][3] = bad[3][1] = 0.99
    data["correlation"] = bad
    with pytest.raises(ValueError, match="positive definite"):
        CMA.model_validate(data)
    data = CMA_.model_dump(mode="json")
    data["policies"]["cash"] = {"za_cash": 0.5}
    with pytest.raises(ValueError, match="sum to 1"):
        CMA.model_validate(data)


def test_glide_path_interpolates_by_age():
    w = glide_weights(CMA_.policies.glide_path, np.array([20.0, 37.5, 90.0]))
    assert np.allclose(w.sum(axis=-1), 1)
    first, second = (allocation_vector(g.allocation) for g in CMA_.policies.glide_path[:2])
    assert np.allclose(w[0], first) and np.allclose(w[1], (first + second) / 2)
    assert np.allclose(w[2], allocation_vector(CMA_.policies.glide_path[-1].allocation))


# --------------------------------------------------------------------------- the MVN-t market


def test_zero_volatility_market_follows_the_means_exactly():
    m = MarketModel(CFG.with_(zero_volatility=True), CMA_, 0.045)
    mk = m.realise(m.shocks(1, 3, 20))
    assert np.all(mk.infl == 0.045)
    assert np.allclose(mk.fx_log, np.log(1.01))
    ref = allocation_vector(CMA_.policies.reference)
    target = 1.085 / 1.045 - 1
    assert np.all(mk.portfolio_gross(ref, target) == pytest.approx(1 + target, abs=1e-15))
    assert np.allclose(mk.price[:, -1], 1.045**20)


def test_class_means_inflation_and_fx_match_the_model():
    m = MarketModel(CFG, CMA_, 0.045)
    mk = m.realise(m.shocks(3, 20_000, 40))
    assert np.allclose(mk.class_gross.mean(axis=(1, 2)), m.class_mean, rtol=4e-3)
    # Portfolio means are the document's, whatever the mix (the CMA supplies only the shape, §12.8).
    for w in (allocation_vector(CMA_.policies.reference), allocation_vector(CMA_.policies.offshore)):
        assert mk.portfolio_gross(w, 0.03).mean() == pytest.approx(1.03, rel=3e-3)
    # AR(1) inflation around the document CPI: phi 0.6, innovation sd 0.015.
    infl = mk.infl[:, 10:]
    assert infl.mean() == pytest.approx(0.045, abs=1e-3)
    assert infl.std() == pytest.approx(0.015 / np.sqrt(1 - 0.36), rel=0.03)
    lag = np.corrcoef(infl[:, 1:].ravel(), infl[:, :-1].ravel())[0, 1]
    assert lag == pytest.approx(0.6, abs=0.02)
    # GBM real USDZAR: 1% real drift, 15% vol, weaker rand when SA equity falls.
    assert np.exp(mk.fx_log).mean() == pytest.approx(1.01, abs=2e-3)
    assert mk.fx_log.std() == pytest.approx(0.15, rel=0.02)
    x = m.players(m.shocks(3, 20_000, 5))
    assert np.corrcoef(x[0].ravel(), x[5].ravel())[0, 1] == pytest.approx(-0.35, abs=0.02)
    assert np.corrcoef(x[0].ravel(), x[3].ravel())[0, 1] == pytest.approx(0.55, abs=0.02)


def test_fat_tails_keep_unit_variance_and_add_kurtosis():
    m = MarketModel(CFG, CMA_, 0.045)
    shocks = m.shocks(5, 20_000, 30)
    w = m._w(shocks.chi2)  # noqa: SLF001
    assert (w**2).mean() == pytest.approx(1.0, rel=0.02)
    assert w.max() <= CFG.fat_tails.max_multiplier / m.w_norm + 1e-12
    eq = m.realise(shocks).class_log[0].ravel()
    assert eq.std() == pytest.approx(CMA_.vols[0], rel=0.02)
    assert sps.kurtosis(eq) > 0.5
    normal = MarketModel(CFG.with_(fat_tails=CFG.fat_tails.model_copy(update={"enabled": False})), CMA_, 0.045)
    assert abs(sps.kurtosis(normal.realise(normal.shocks(5, 20_000, 30)).class_log[0].ravel())) < 0.1


def test_market_draws_are_common_random_numbers():
    """Path p in year t is the same whatever the path count or horizon (sensitivity runs reuse market draws)."""
    m = MarketModel(CFG, CMA_, 0.045)
    a, b = m.shocks(9, 200, 30), m.shocks(9, 50, 45)
    assert np.array_equal(a.z[:, :50, :30], b.z[:, :, :30])
    assert np.array_equal(a.chi2[:50, :30], b.chi2[:, :30]) and np.array_equal(a.e[:50, :30], b.e[:, :30])
    assert not np.array_equal(m.shocks(10, 50, 30).z, b.z[:, :, :30])
    u, v = fact_uniforms(4, "x#1", 100), fact_uniforms(4, "x#1", 30)
    assert all(np.array_equal(getattr(u, k)[:30], getattr(v, k)) for k in ("c1", "c2", "e1", "e2"))
    assert not np.array_equal(fact_uniforms(4, "x#2", 30).c1, v.c1)
    assert derive_seed("wf-1", 3) == derive_seed("wf-1", 3) != derive_seed("wf-1", 4)


# --------------------------------------------------------------------------- the block bootstrap (§12.6)


def _synthetic_series(months: int = 312, seed: int = 0, rho: float = 0.0) -> HistoricalSeries:
    """A synthetic stand-in for the investment team's monthly history (the real series must be supplied)."""
    rng = np.random.default_rng(seed)
    shock = rng.normal(0, 0.04, months)
    ar = np.empty(months)
    ar[0] = shock[0]
    for i in range(1, months):
        ar[i] = rho * ar[i - 1] + shock[i]
    returns = {k: rng.normal(0.006, 0.02, months) for k in ASSET_CLASSES}
    returns["za_equity"] = 0.008 + ar
    return HistoricalSeries(returns=returns, cpi=rng.normal(0.004, 0.002, months), usdzar_log_change=rng.normal(0.004, 0.03, months), start="2000-01")


def test_block_bootstrap_resamples_blocks_of_mean_length_24_and_keeps_the_model_means():
    model = MarketModel(CFG, CMA_, 0.045)
    series = _synthetic_series()
    boot = StationaryBlockBootstrap(series, model)
    shocks = boot.shocks(1, 2_000, 30)
    d = np.diff(shocks.idx, axis=1)
    jumps = (d != 1) & (d != -(series.n_months - 1))
    assert 1 / jumps.mean() == pytest.approx(24, rel=0.08)
    mk = boot.realise(shocks)
    assert mk.class_log.shape == (5, 2_000, 30)
    assert mk.class_log[0].mean() == pytest.approx(model.class_mu[0], abs=0.01)  # recentred onto the model's drift
    assert np.expm1(np.log1p(mk.infl).mean()) == pytest.approx(0.045, abs=0.003)
    raw = StationaryBlockBootstrap(series, model, recentre=False).realise(shocks)
    expected = 12 * np.log1p(series.returns["za_equity"]).mean() - 12 * np.log1p(series.cpi).mean()
    assert raw.class_log[0].mean() == pytest.approx(expected, abs=0.01)


def test_block_bootstrap_keeps_autocorrelation_that_iid_resampling_loses():
    model = MarketModel(CFG, CMA_, 0.045)
    series = _synthetic_series(months=600, seed=2, rho=0.6)
    blocks = StationaryBlockBootstrap(series, model).realise(StationaryBlockBootstrap(series, model).shocks(2, 3_000, 10))
    iid = StationaryBlockBootstrap(series, model, mean_block_months=1).realise(StationaryBlockBootstrap(series, model, mean_block_months=1).shocks(2, 3_000, 10))
    # Positive monthly autocorrelation makes annual sums more variable; blocks keep it, single months do not.
    assert blocks.class_log[0].std() > 1.4 * iid.class_log[0].std()


def test_historical_series_is_validated():
    with pytest.raises(ValueError, match="lacks"):
        HistoricalSeries(returns={"za_equity": np.zeros(30)}, cpi=np.zeros(30), usdzar_log_change=np.zeros(30))
    with pytest.raises(ValueError, match="24 months"):
        HistoricalSeries(returns={k: np.zeros(12) for k in ASSET_CLASSES}, cpi=np.zeros(12), usdzar_log_change=np.zeros(12))


# --------------------------------------------------------------------------- sampling


@pytest.mark.parametrize(
    "dist",
    [
        Triangular(low=60, mode=62, high=67),
        LogNormal(median=45_000, p10=18_000, p90=130_000),
        Normal(mean=0.02, sd=0.05),
        Discrete(support=[5.0, 3.0, 4.0], weights=[0.2, 0.5, 0.3]),
    ],
)
def test_ppf_is_the_inverse_cdf(dist):
    from advisor_intel import stats

    for q in (0.1, 0.5, 0.9):
        assert ppf(dist, np.array([q]))[0] == pytest.approx(stats.quantile(dist, q), rel=1e-9, abs=1e-9)


def test_ppf_keeps_hypothesis_structure_and_frequencies():
    u = np.random.default_rng(0).random(40_000)
    bimodal = Mixture(components=[(0.5, Triangular(low=59, mode=60, high=61)), (0.5, Triangular(low=64, mode=65, high=66))])
    x = ppf(bimodal, u)
    assert np.mean((x > 61) & (x < 64)) == 0  # "retire at 60 or at 65" stays bimodal
    assert np.mean(x < 62) == pytest.approx(0.5, abs=0.01)
    assert ppf(Bernoulli(p=0.3), u).mean() == pytest.approx(0.3, abs=0.01)
    labels = ppf(Categorical(probs={"a": 0.2, "b": 0.8}), u)
    assert labels.dtype == object and np.mean(labels == "b") == pytest.approx(0.8, abs=0.01)
    assert ppf(Point(value="1986-04-12"), u[:2])[0] == pytest.approx(1986 + 101 / 365)  # ISO dates as decimal years


# --------------------------------------------------------------------------- the Wilson interval


def test_wilson_interval():
    lo, hi = wilson_interval(50, 100)
    assert (round(lo, 4), round(hi, 4)) == (0.4038, 0.5962)
    lo, hi = wilson_interval(0, 1_000)
    assert lo == pytest.approx(0.0, abs=1e-12) and hi == pytest.approx(0.00383, abs=1e-5)
    lo, hi = wilson_interval(9_500, 10_000)
    assert lo < 0.95 < hi and hi - lo < 0.01
    assert wilson_interval(0, 0) == (0.0, 1.0)
    n = paths_for_width(0.5, 0.02)
    lo, hi = wilson_interval(n // 2, n)
    assert hi - lo <= 0.02 and n % 1_000 == 0
