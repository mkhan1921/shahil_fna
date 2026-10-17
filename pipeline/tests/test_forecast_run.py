"""run_forecast (spec §12.4, §12.6): determinism, the seed policy, Monte Carlo error and every result field."""

from __future__ import annotations

import numpy as np
import pytest

from advisor_intel.forecast import fna_engine_analyse, fna_engine_bands, return_model_check, run_forecast
from advisor_intel.forecast.config import ModeSettings, load_cma, load_config
from advisor_intel.forecast.mapper import build_plan
from advisor_intel.forecast.run import wilson_interval
from advisor_intel.forecast.sampling import derive_seed
from advisor_intel.forecast.tax import FnaEngineTaxModel, default_tax_model
from advisor_intel.models.dists import Bernoulli, LogNormal, Triangular
from tests.forecast_support import StubTaxModel, add_fact, point, prior_profile, sample_document
from tests.test_forecast_returns import _synthetic_series

SMALL = ModeSettings(n_paths=1_000, attribution_n=300, shapley_paths=120, shapley_contexts=12, shapley_permutations=14,
                     shapley_inner=3, top_drivers=5, need_bands_k=12)
CFG = load_config().with_(modes={"preview": SMALL, "final": SMALL.model_copy(update={"attribution_n": 400})}, pos_ci_max_width=1.0)
TAX = StubTaxModel()


@pytest.fixture(scope="module")
def household():
    doc = sample_document()
    prof = prior_profile(doc)
    prof, _ = add_fact(prof, "retirement.{p}.retirementAge", "retirement.client.retirementAge", Triangular(low=60, mode=63, high=66),
                       instance="client", unit="age", p_correct=0.7)
    prof, _ = add_fact(prof, "expenses.items[]", "expenses.items[groceries]", LogNormal(median=9_000, p10=7_000, p90=13_000),
                       instance="groceries", period="monthly", p_correct=0.6)
    neo = next(e for e in prof.entities.values() if e.canonical_name.startswith("Neo"))
    common = {"instance": "event.offshore_1", "entity_refs": [neo.entity_id], "p_correct": 0.8}
    prof, _ = add_fact(prof, "event[].occurs", "event[e1].occurs", Bernoulli(p=0.4), unit="none", **common)
    prof, _ = add_fact(prof, "event[].cashImpact", "event[e1].cashImpact", point(-30_000), unit="USD", period="annual", **common)
    prof, _ = add_fact(prof, "event[].kind", "event[e1].kind", point("education-offshore"), unit="none", **common)
    return prof, doc


def test_same_inputs_config_and_seed_give_identical_bytes(household):
    prof, doc = household
    a = run_forecast(prof, base_document=doc, config=CFG, tax_model=TAX, seed=7)
    b = run_forecast(prof, base_document=doc, config=CFG, tax_model=TAX, seed=7)
    assert a.model_dump_json() == b.model_dump_json()
    c = run_forecast(prof, base_document=doc, config=CFG, tax_model=TAX, seed=8)
    assert c.model_dump_json() != a.model_dump_json()


def test_seed_is_derived_from_the_run_id_and_profile_version(household):
    prof, doc = household
    a = run_forecast(prof, base_document=doc, config=CFG, tax_model=TAX)
    assert a.seed == derive_seed(prof.run_id, prof.version)
    assert run_forecast(prof, base_document=doc, config=CFG, tax_model=TAX).model_dump_json() == a.model_dump_json()
    bumped = prof.model_copy(update={"version": prof.version + 1})
    assert run_forecast(bumped, base_document=doc, config=CFG, tax_model=TAX).seed != a.seed


def test_every_result_field_is_filled(household):
    prof, doc = household
    r = run_forecast(prof, base_document=doc, mode="final", config=CFG, tax_model=TAX, seed=3)
    assert (r.run_id, r.profile_version, r.config_version, r.cma_version) == (prof.run_id, str(prof.version), "config_v1", "2026Q4")
    assert r.n_paths == 1_000 and 0 < r.pos < 1 and r.pos_ci[0] <= r.pos <= r.pos_ci[1]
    assert (r.pos_ci[0], r.pos_ci[1]) == pytest.approx(wilson_interval(round(r.pos * r.n_paths), r.n_paths), abs=1e-6)
    assert len(r.goal_funding) == 2 and all(0 <= v <= 1 for v in r.goal_funding.values())
    assert len(r.ages) == 61 and r.ages[0] == 40
    assert set(r.net_worth_bands) == {"p10", "p25", "p50", "p75", "p90"} and all(len(v) == 61 for v in r.net_worth_bands.values())
    bands = np.array([r.net_worth_bands[k] for k in ("p10", "p25", "p50", "p75", "p90")])
    assert np.all(np.diff(bands, axis=0) >= 0)
    assert r.net_worth_bands["p50"][0] == pytest.approx(4_920_000, rel=1e-6)
    assert all(len(v) == 60 for v in r.retirement_income_bands.values())
    assert r.shortfall["p_any"] == pytest.approx(1 - r.pos) and r.shortfall["p90_total_real_shortfall"] >= 0
    assert set(r.shortfall) == {"p_any", "median_first_shortfall_age", "p90_total_real_shortfall"}  # §12.4, as the review fixture names them
    assert 0 <= r.liquidity_breach_prob <= 1
    assert set(r.variance_attribution) == {"market", "client_content", "extraction", "interaction"}
    assert r.variance_attribution["market"] > 0.3 and r.variance_attribution["extraction"] > 0
    assert set(r.market_attribution) == {"za_equity", "za_bonds", "za_cash", "global_equity", "global_bonds", "fx_usdzar", "inflation"}
    assert sum(r.market_attribution.values()) == pytest.approx(r.variance_attribution["market"], abs=2e-3)
    assert r.top_drivers and r.top_drivers[0][0] in prof.facts and r.top_drivers == sorted(r.top_drivers, key=lambda kv: -kv[1])
    off = run_forecast(prof, base_document=doc, mode="final", config=CFG, tax_model=TAX, seed=3, fna_analyse=False, fna_bands=False)
    assert off.deterministic == {} and off.need_bands == {}  # the FNA side switched off
    assert off.model_copy(update={"deterministic": r.deterministic, "need_bands": r.need_bands}) == r  # and nothing else changes


def test_fna_side_by_side_defaults_to_the_ported_engine(household):
    """§12.8 "report both": the pipeline passes no hooks, so the engine's own view must be there by default."""
    pytest.importorskip("advisor_intel.fna_engine", reason="the deterministic projection and need bands come from the ported FNA engine")
    prof, doc = household
    r = run_forecast(prof, base_document=doc, config=CFG, tax_model=TAX, seed=3)
    det = r.deterministic
    assert det["client.retirement_age"] == 63  # the median document: retirement age at the fact's median
    assert det["client.projected_capital"] > 0 and "spouse.depletion_age" in det and det["client.replacement_ratio"] > 0
    # §12.8 consistency figure beside the FNA's own: zero volatility, every fact at its median, within 0.5%.
    for p in ("client", "spouse"):
        assert abs(det[f"{p}.mc_zero_vol_capital"] / det[f"{p}.projected_capital"] - 1) < 0.005, p
    # Need bands are keyed as the app's review overlay keys its need lines (lib/review/impact.ts).
    assert {"life.client", "retirement.client", "retirement.spouse", "education.household", "emergency.household"} <= set(r.need_bands)
    assert all(b["p10"] <= b["p50"] <= b["p90"] for b in r.need_bands.values())
    assert r.need_bands["retirement.client"]["p50"] == pytest.approx(det["client.shortfall"], rel=0.25)  # the band sits around the median document's shortfall
    injected = run_forecast(prof, base_document=doc, config=CFG, tax_model=TAX, seed=3, fna_analyse=fna_engine_analyse, fna_bands=fna_engine_bands)
    assert injected == r
    called = {}
    run_forecast(prof, base_document=doc, config=CFG, tax_model=TAX, seed=3, fna_analyse=lambda d: called.setdefault("doc", d) and {}, fna_bands=False)
    assert called["doc"].retirement["client"].retirementAge == 63  # one-argument hooks are called without today


def test_path_count_rises_until_the_interval_is_two_points_wide(household):
    prof, doc = household
    cfg = CFG.with_(pos_ci_max_width=0.02, n_paths_max=20_000)
    r = run_forecast(prof, base_document=doc, config=cfg, tax_model=TAX, seed=5, n_paths=300)
    assert r.n_paths > 300 and r.pos_ci[1] - r.pos_ci[0] <= 0.02
    capped = run_forecast(prof, base_document=doc, config=cfg.with_(n_paths_max=600), tax_model=TAX, seed=5, n_paths=300)
    assert capped.n_paths == 600 and capped.pos_ci[1] - capped.pos_ci[0] > 0.02


def test_the_market_covers_the_longest_lived_path(household):
    """An uncertain date of birth: the youngest of all the paths sets the horizon, not a sample of them."""
    prof, doc = household
    prof, _ = add_fact(prof, "{p}.dateOfBirth", "client.dateOfBirth", Triangular(low=1955.0, mode=1986.0, high=2001.0), instance="client",
                       unit="date", p_correct=0.5)
    plan = build_plan(prof, base_document=doc, config=CFG, cma=load_cma())
    u = plan.uniforms(4, 1_000, matrix="main")
    years = plan.realise(u, TAX).n_years
    assert years > plan.realise(u.take(np.arange(64)), TAX).n_years  # the first paths alone would fall short
    r = run_forecast(prof, base_document=doc, config=CFG, tax_model=TAX, seed=4)  # attribution mixes draws across matrices too
    assert r.n_paths == 1_000 and len(r.ages) == years + 1 and all(len(v) == years for v in r.retirement_income_bands.values())


def test_overrides_move_a_fact_for_what_if_runs(household):
    prof, doc = household
    base = run_forecast(prof, base_document=doc, config=CFG, tax_model=TAX, seed=2)
    early = run_forecast(prof, base_document=doc, config=CFG, tax_model=TAX, seed=2, overrides={"retirement.{p}.retirementAge#client": 55})
    late = run_forecast(prof, base_document=doc, config=CFG, tax_model=TAX, seed=2, overrides={"retirement.client.retirementAge": 68})
    assert early.pos < base.pos < late.pos


def test_default_tax_model_is_the_fna_engine():
    pytest.importorskip("advisor_intel.fna_engine", reason="the default tax model is the ported FNA engine")
    m = default_tax_model("2026/27")
    assert isinstance(m, FnaEngineTaxModel) and m.version == "fna_engine:2026/27"
    assert default_tax_model("1999/00").version == "fna_engine:2026/27"  # unknown years fall back as the app does


def test_return_model_check_compares_the_bootstrap_with_mvn(household):
    prof, doc = household
    out = return_model_check(prof, _synthetic_series(), base_document=doc, config=CFG, tax_model=TAX, seed=1, n_paths=500)
    assert set(out) == {"pos_mvn", "pos_bootstrap", "gap_points"}
    assert out["gap_points"] == pytest.approx(100 * abs(out["pos_mvn"] - out["pos_bootstrap"]), abs=1e-3)
