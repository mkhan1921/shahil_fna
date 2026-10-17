"""Variance attribution (spec §12.5): Sobol group indices, per-fact drivers, Shapley effects within the market."""

from __future__ import annotations

import re

import numpy as np
import pytest

from advisor_intel.forecast import attribution, attribution_sentence
from advisor_intel.forecast.attribution import attribute, correlation_ratio, shapley_market, sobol_groups, top_drivers
from advisor_intel.forecast.config import ModeSettings, load_cma, load_config
from advisor_intel.forecast.mapper import build_plan
from advisor_intel.forecast.run import Forecaster
from advisor_intel.models.dists import Triangular
from tests.forecast_support import StubTaxModel, add_fact, point, prior_profile, sample_document

CFG = load_config()
ZERO = CFG.with_(zero_volatility=True)
CMA_ = load_cma()
TAX = StubTaxModel()
SETTINGS = ModeSettings(n_paths=1_000, attribution_n=1_000, shapley_paths=400, shapley_contexts=20, shapley_permutations=70,
                        shapley_inner=3, top_drivers=5, need_bands_k=10)


def forecaster(profile, doc, config=CFG):
    return Forecaster(build_plan(profile, base_document=doc, config=config, cma=CMA_), TAX)


def test_only_market_randomness_gives_a_market_share_of_one():
    doc = sample_document()
    fc = forecaster(prior_profile(doc), doc)  # every fact is the trusted prior FNA value
    raw, design = sobol_groups(fc, 11, 1_000)
    assert design.variance > 0
    assert raw["market"] == pytest.approx(1.0, abs=0.12)
    assert raw["client_content"] == 0.0 and raw["extraction"] == 0.0  # swapping draws that nothing uses changes nothing


def test_only_extraction_risk_gives_an_extraction_share_of_one():
    doc = sample_document()
    prof, _ = add_fact(prior_profile(doc), "retirement.{p}.retirementAge", "retirement.client.retirementAge", point(58), instance="client",
                       unit="age", p_correct=0.5)
    prof, _ = add_fact(prof, "income.{p}.grossMonthly", "income.client.grossMonthly", point(100_000), instance="client", period="monthly",
                       p_correct=0.5)
    fc = forecaster(prof, doc, ZERO)  # no market risk; the facts are certain if heard right
    raw, design = sobol_groups(fc, 12, 1_000)
    assert design.variance > 0
    assert raw["extraction"] == pytest.approx(1.0, abs=0.12)
    assert raw["market"] == 0.0 and raw["client_content"] == 0.0


def test_only_client_content_gives_a_content_share_of_one():
    doc = sample_document()
    prof, _ = add_fact(prior_profile(doc), "retirement.{p}.retirementAge", "retirement.client.retirementAge", Triangular(low=55, mode=60, high=70),
                       instance="client", unit="age", review="accepted")
    fc = forecaster(prof, doc, ZERO)
    raw, _ = sobol_groups(fc, 13, 1_000)
    assert raw["client_content"] == pytest.approx(1.0, abs=0.12)
    assert raw["market"] == 0.0 and raw["extraction"] == 0.0


def test_shapley_effects_sum_to_the_market_share():
    doc = sample_document()
    fc = forecaster(prior_profile(doc), doc)
    shares, diag = shapley_market(fc, 21, SETTINGS)
    assert set(shares) == {"za_equity", "za_bonds", "za_cash", "global_equity", "global_bonds", "fx_usdzar", "inflation"}
    assert sum(shares.values()) == pytest.approx(1.0, abs=1e-9) and min(shares.values()) >= 0
    assert diag["shapley_sum"] == pytest.approx(diag["shapley_variance"], rel=1e-9)  # efficiency: the effects add up
    # With only market randomness the market variance is the whole variance of Y (estimated independently).
    _, design = sobol_groups(fc, 21, 1_000)
    assert diag["shapley_variance"] / design.variance == pytest.approx(1.0, abs=0.35)
    assert max(shares, key=shares.get) == "za_equity"  # the largest, most volatile holding of a moderate mix
    result = attribute(fc, 21, SETTINGS)
    assert sum(result.market.values()) == pytest.approx(result.groups["market"], abs=2e-3)


def test_top_drivers_rank_a_wide_fact_above_a_narrow_one():
    doc = sample_document()
    prof, wide = add_fact(prior_profile(doc), "retirement.{p}.retirementAge", "retirement.client.retirementAge", Triangular(low=55, mode=62, high=70),
                          instance="client", unit="age", review="accepted")
    prof, narrow = add_fact(prof, "income.{p}.grossMonthly", "income.client.grossMonthly", Triangular(low=99_000, mode=100_000, high=101_000),
                            instance="client", period="monthly", review="accepted")
    fc = forecaster(prof, doc, ZERO)
    _, design = sobol_groups(fc, 14, 1_000)
    drivers = dict(top_drivers(fc, design, limit=5))
    assert set(drivers) == {wide, narrow}
    assert drivers[wide] > 0.5 and drivers[narrow] < 0.05


def test_the_driver_screen_keeps_the_dominant_fact(monkeypatch):
    doc = sample_document()
    prof, wide = add_fact(prior_profile(doc), "retirement.{p}.retirementAge", "retirement.client.retirementAge", Triangular(low=55, mode=62, high=70),
                          instance="client", unit="age", review="accepted")
    prof, _ = add_fact(prof, "income.{p}.grossMonthly", "income.client.grossMonthly", Triangular(low=99_000, mode=100_000, high=101_000),
                       instance="client", period="monthly", review="accepted")
    fc = forecaster(prof, doc, ZERO)
    _, design = sobol_groups(fc, 15, 1_000)
    monkeypatch.setattr(attribution, "DRIVER_MARGIN", 0)  # one pick-freeze run for two candidates: the screen decides
    drivers = top_drivers(fc, design, limit=1)
    assert [f for f, _ in drivers] == [wide] and drivers[0][1] > 0.5


def test_correlation_ratio_is_the_share_explained_by_the_groups():
    rng = np.random.default_rng(0)
    g = rng.integers(0, 10, 5_000)
    assert abs(correlation_ratio(g, rng.standard_normal(5_000))) < 0.01  # no effect: about zero after the adjustment
    assert correlation_ratio(g, g.astype(float)) == pytest.approx(1.0)
    half = g + rng.standard_normal(5_000) * np.sqrt(8.25)  # Var(g) = (10² − 1) / 12 = 8.25: half the variance
    assert correlation_ratio(g, half) == pytest.approx(0.5, abs=0.03)


def test_attribution_sentence():
    s = attribution_sentence({"market": 0.6, "client_content": 0.25, "extraction": 0.1, "interaction": 0.05})
    assert s.startswith("Of the uncertainty in this plan, about ")
    pct = [int(x) for x in re.findall(r"(\d+)%", s)]
    assert pct == [63, 26, 11] and sum(pct) == 100
    assert "markets" in s and "hasn't decided" in s and "misheard" in s
    assert attribution_sentence({"market": 0.0, "client_content": 0.0, "extraction": 0.0}).startswith("There is no measurable")
    assert np.isclose(sum(int(x) for x in re.findall(r"(\d+)%", attribution_sentence({"market": 1 / 3, "client_content": 1 / 3, "extraction": 1 / 3}))), 100)
