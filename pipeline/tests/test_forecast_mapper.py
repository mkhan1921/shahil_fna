"""Mapping the profile to forecast inputs (spec §4.3, §12.2): one rule per status, source and review case."""

from __future__ import annotations

import numpy as np
import pytest

from advisor_intel.forecast.config import load_cma, load_config
from advisor_intel.forecast.engine import _Simulation
from advisor_intel.forecast.mapper import Money, build_plan, plan_from_document
from advisor_intel.forecast.returns import MarketModel
from advisor_intel.models.dists import Bernoulli, LogNormal, Mixture, Point, Triangular
from tests.forecast_support import (
    ANCHOR,
    StubTaxModel,
    add_fact,
    empty_profile,
    mark_unknown,
    point,
    prior_profile,
    reject,
    sample_document,
)

CFG = load_config()
CMA_ = load_cma()
TAX = StubTaxModel()
N = 20_000


def plan_for(profile, doc=None, cfg=CFG):
    return build_plan(profile, base_document=doc, config=cfg, cma=CMA_)


def draws(plan, n=N, seed=3):
    return plan.draws(plan.uniforms(seed, n))


GROSS = "income.{p}.grossMonthly#client"
RET_AGE = "retirement.{p}.retirementAge#client"


def test_prior_fna_values_not_discussed_are_used_as_is():
    doc = sample_document()
    plan = plan_for(prior_profile(doc), doc)
    s = plan.samplers[GROSS]
    assert s.rule == "not_discussed_prior_value" and s.mode == "content" and s.content == Point(value=100000)
    assert not s.random
    assert np.all(draws(plan)["income.client.grossMonthly"].values == 100_000)
    assert plan.random_facts() == []  # a profile seeded from the prior FNA alone has no client uncertainty


def test_unreviewed_call_fact_is_the_extraction_risk_mixture():
    doc = sample_document()
    prof, fid = add_fact(
        prior_profile(doc),
        "retirement.{p}.retirementAge",
        "retirement.client.retirementAge",
        point(60),
        instance="client",
        unit="age",
        p_correct=0.7,
    )
    plan = plan_for(prof, doc)
    s = plan.samplers[fid]
    assert (s.rule, s.mode, s.q) == ("extraction_mixture", "mixture", 0.7)
    d = draws(plan)["retirement.client.retirementAge"]
    assert d.extracted.mean() == pytest.approx(0.7, abs=0.01)  # the use-extracted-or-prior indicator is explicit (group E)
    assert np.all(d.values[d.extracted] == 60)
    prior_draws = d.values[~d.extracted]
    assert prior_draws.min() >= 60 and prior_draws.max() <= 70 and prior_draws.std() > 1  # the population prior, triangular 60–70


@pytest.mark.parametrize(
    "kw",
    [{"review": "accepted"}, {"review": "edited"}, {"auto_accepted": True}, {"source": "reviewer"}],
    ids=["accepted", "edited", "auto_accepted", "reviewer"],
)
def test_trusted_facts_use_their_value(kw):
    prof, fid = add_fact(
        empty_profile(),
        "retirement.{p}.retirementAge",
        "retirement.client.retirementAge",
        Triangular(low=60, mode=62, high=65),
        instance="client",
        unit="age",
        p_correct=0.3,
        **kw,
    )
    plan = plan_for(prof)
    s = plan.samplers[fid]
    assert (s.rule, s.mode, s.q) == ("trusted", "content", 1.0)
    d = draws(plan)["retirement.client.retirementAge"]
    assert d.extracted is None and d.values.min() >= 60 and d.values.max() <= 65


def test_not_discussed_without_a_prior_uses_the_population_prior():
    prof, fid = add_fact(
        empty_profile(),
        "retirement.{p}.retirementAge",
        "retirement.client.retirementAge",
        point(65),
        instance="client",
        unit="age",
        status="not_discussed",
        source="fna_default",
    )
    plan = plan_for(prof)
    s = plan.samplers[fid]
    assert (s.rule, s.mode) == ("not_discussed_population_prior", "prior")
    v = draws(plan)["retirement.client.retirementAge"].values
    assert v.mean() == pytest.approx(65, abs=0.1) and v.min() >= 60 and v.max() <= 70 and v.std() > 1.5


def test_explicitly_unknown_uses_the_prior_value_or_else_the_population_prior():
    doc = sample_document()
    prof = mark_unknown(prior_profile(doc), RET_AGE)  # the client said they don't know; the prior FNA said 65
    s = plan_for(prof, doc).samplers[RET_AGE]
    assert s.rule == "unknown_prior_value" and s.content == Point(value=65)
    fid = "retirement.{p}.retirementAge#client"
    prof = mark_unknown(
        empty_profile(), fid, slot_id="retirement.{p}.retirementAge", path="retirement.client.retirementAge", instance="client"
    )
    s = plan_for(prof).samplers[fid]
    assert s.rule == "unknown_population_prior" and s.mode == "prior"


def test_rejected_facts_are_absent():
    doc = sample_document()
    prof, fid = add_fact(
        prior_profile(doc),
        "income.{p}.grossMonthly",
        "income.client.grossMonthly",
        point(250_000),
        instance="client",
        period="monthly",
        p_correct=0.4,
    )
    prof = reject(prof, fid)
    s = plan_for(prof, doc).samplers[fid]
    assert s.rule == "rejected_prior_value" and s.content == Point(value=100000)  # back to the prior FNA value
    prof, fid = add_fact(
        empty_profile(),
        "income.{p}.grossMonthly",
        "income.client.grossMonthly",
        point(250_000),
        instance="client",
        period="monthly",
        p_correct=0.4,
    )
    plan = plan_for(reject(prof, fid))
    assert fid not in plan.samplers and plan.trace[fid] == "absent_median_document"
    inp = plan.realise(plan.uniforms(1, 10), TAX)
    assert np.all(inp.persons[0].gross_monthly.total() == 0)  # the app default, as in the median document


def test_contradicted_and_multi_hypothesis_facts_stay_bimodal():
    prof, fid = add_fact(
        empty_profile(),
        "retirement.{p}.retirementAge",
        "retirement.client.retirementAge",
        [(0.5, point(60)), (0.5, point(65))],
        instance="client",
        unit="age",
        status="contradicted",
        review="accepted",
    )
    plan = plan_for(prof)
    assert isinstance(plan.samplers[fid].content, Mixture)
    v = draws(plan)["retirement.client.retirementAge"].values
    assert set(np.unique(v)) == {60.0, 65.0} and np.mean(v == 60) == pytest.approx(0.5, abs=0.01)


def test_income_basis_unknown_is_gross_or_net_half_the_time():
    doc = sample_document()
    prof, _ = add_fact(
        prior_profile(doc),
        "income.{p}.grossMonthly",
        "income.client.grossMonthly",
        point(60_000),
        instance="client",
        period="monthly",
        basis="unknown",
        review="accepted",
    )
    plan = plan_for(prof, doc)
    inp = plan.realise(plan.uniforms(5, 4_000), TAX)
    p = inp.persons[0]
    gross = p.gross_monthly.total()
    as_gross = np.isclose(gross, 60_000)
    assert as_gross.mean() == pytest.approx(0.5, abs=0.03)
    up = gross[~as_gross]
    assert np.allclose(up, up[0]) and up[0] > 60_000
    # The grossed-up salary's take-home pay is the stated R60,000 (the payroll view, through the tax model).
    t = TAX.person_tax(
        age=p.age0[0],
        gross_monthly=up[0],
        retirement_contributions_annual=p.contrib_payroll_monthly[0] * 12,
        medical_scheme_members=p.medical_members[0],
        uif_applies=True,
    )
    take_home = (
        up[0] - t.annual_tax / 12 - t.monthly_uif - p.contrib_payroll_monthly[0] - p.medical_payroll_monthly[0] - p.other_payroll_monthly[0]
    )
    assert float(take_home) == pytest.approx(60_000, abs=0.01)
    # Stated as take-home: every path is grossed up. The FNA target follows the grossed-up income.
    prof2, _ = add_fact(
        prior_profile(doc),
        "income.{p}.grossMonthly",
        "income.client.grossMonthly",
        point(60_000),
        instance="client",
        period="monthly",
        basis="net",
        review="accepted",
    )
    p2 = plan_for(prof2, doc).realise(plan.uniforms(5, 100), TAX).persons[0]
    assert np.allclose(p2.gross_monthly.total(), up[0])
    assert np.allclose(p2.target_monthly_today, 0.75 * (up[0] + 100_000 / 12))


def test_foreign_amounts_convert_at_spot_and_move_with_the_rand():
    prof, _ = add_fact(
        empty_profile(),
        "expenses.items[]",
        "expenses.items[groceries]",
        point(500),
        instance="groceries",
        unit="USD",
        period="monthly",
        review="accepted",
    )
    plan = plan_for(prof)
    d = draws(plan, n=100)["expenses.items[groceries]"]
    assert np.all(d.fx) and np.allclose(d.values, 500 * 17.5)
    living = plan.realise(plan.uniforms(1, 100), TAX).household.living_essential
    assert np.allclose(living.linked, 8_750) and np.allclose(living.at(1.2), 8_750 * 1.2)
    # A foreign amount replaced by the (rand) population prior on some paths is linked to the rand only where extracted.
    prof, _ = add_fact(
        empty_profile(),
        "expenses.items[]",
        "expenses.items[groceries]",
        point(500),
        instance="groceries",
        unit="USD",
        period="monthly",
        p_correct=0.5,
    )
    d = draws(plan_for(prof), n=4_000)["expenses.items[groceries]"]
    assert np.array_equal(d.fx, d.extracted) and d.fx.mean() == pytest.approx(0.5, abs=0.03)
    m = Money.of(np.array([10.0, 20.0]), np.array([True, False]))
    assert np.allclose(m.at(2.0), [20.0, 20.0]) and np.allclose(m.total(), [10.0, 20.0])


def test_offshore_education_shares_one_occurrence_draw_for_location_cost_and_fx():
    doc = sample_document()
    prof = prior_profile(doc)
    neo = next(e for e in prof.entities.values() if e.canonical_name.startswith("Neo"))
    common = {"instance": "event.offshore_1", "entity_refs": [neo.entity_id], "review": "accepted"}
    prof, _ = add_fact(prof, "event[].occurs", "event[e1].occurs", Bernoulli(p=0.4), unit="none", **common)
    prof, _ = add_fact(prof, "event[].cashImpact", "event[e1].cashImpact", point(-40_000), unit="USD", period="annual", **common)
    prof, _ = add_fact(prof, "event[].kind", "event[e1].kind", point("education-offshore"), unit="none", **common)
    prof, _ = add_fact(prof, "event[].year", "event[e1].year", point(2034), unit="date", **common)
    plan = plan_for(prof, doc)
    u = plan.uniforms(7, 4_000)
    inp = plan.realise(u, TAX)
    dep = next(d for d in inp.dependants if d.key == neo.fna_id)
    o = dep.offshore
    assert o is not None and inp.events == []  # the event became the dependant's study plan, not a separate cost
    assert np.array_equal(o.occurs, plan.draws(u)["event[e1].occurs"].values >= 0.5)
    assert o.occurs.mean() == pytest.approx(0.4, abs=0.03)
    assert np.allclose(o.annual_cost.linked, 40_000 * 17.5) and np.all(o.start_step == 8)
    # In the engine the offshore cost (moving with the rand) replaces the local cost on the paths where it happens.
    zero = CFG.with_(zero_volatility=True)
    mm = MarketModel(zero, CMA_, 0.045)
    sim = _Simulation(inp, mm.realise(mm.shocks(1, inp.n, inp.n_years)), zero, CMA_, TAX)
    t = 9
    _, _, tertiary = sim._dependant_costs(t, sim.fx_mid[:, t])
    esc = (1.065 / 1.045) ** t
    kagiso = next(d for d in inp.dependants if d.key != neo.fna_id and d.is_child)
    local_other = 150_000 * esc * (t >= 19 - kagiso.age0[0]) * (t < 19 - kagiso.age0[0] + 3)
    away = 700_000 * sim.fx_mid[:, t] * esc + local_other
    local = 150_000 * esc + local_other
    assert np.allclose(tertiary[o.occurs], away[o.occurs]) and np.allclose(tertiary[~o.occurs], local)


def test_income_changes_and_business_growth():
    doc = sample_document()
    prof = prior_profile(doc)
    common = {"instance": "change.part_time_1", "review": "accepted"}
    prof, _ = add_fact(
        prof, "income.{p}.expectedChange[].occurs", "income.spouse.expectedChange[c1].occurs", Bernoulli(p=0.3), unit="none", **common
    )
    prof, _ = add_fact(
        prof, "income.{p}.expectedChange[].year", "income.spouse.expectedChange[c1].year", point(2030), unit="date", **common
    )
    prof, _ = add_fact(
        prof,
        "income.{p}.expectedChange[].newGrossMonthly",
        "income.spouse.expectedChange[c1].newGrossMonthly",
        point(30_000),
        period="monthly",
        **common,
    )
    prof, _ = add_fact(
        prof,
        "income.{p}.businessGrowthRate",
        "income.client.businessGrowthRate",
        point(0.03),
        instance="client",
        unit="pct",
        review="accepted",
    )
    inp = plan_for(prof, doc).realise(plan_for(prof, doc).uniforms(2, 4_000), TAX)
    spouse = inp.persons[1]
    (change,) = spouse.changes
    assert change.occurs.mean() == pytest.approx(0.3, abs=0.03) and np.all(change.step == 4)
    assert np.allclose(change.new_gross_monthly.total(), 30_000)
    client = inp.persons[0]
    assert np.allclose(client.growth_real, 1.055 / 1.045 - 1)  # salaried: salary escalation; business growth applies to the self-employed


def test_fields_without_facts_come_from_the_median_document():
    doc = sample_document()
    plan = plan_from_document(doc, anchor=ANCHOR, config=CFG, cma=CMA_)
    assert plan.samplers == {}
    inp = plan.realise(plan.uniforms(1, 3), TAX)
    client, spouse = inp.persons
    assert client.age0[0] == pytest.approx((ANCHOR.toordinal() - 725108) / 365.2425)  # 1986-04-12, FNA exactAge
    assert client.capital0[0] == 1_320_000 + 315_000 and spouse.capital0[0] == 560_000 + 184_000
    assert (client.contrib_payroll_monthly[0], client.contrib_own_monthly[0], client.contrib_employer_monthly[0]) == (7_500, 2_500, 10_000)
    assert client.target_monthly_today[0] == pytest.approx(0.75 * (100_000 + 100_000 / 12))
    assert client.share[0] + spouse.share[0] == pytest.approx(1)
    hh = inp.household
    assert hh.cash0[0] == 48_000 + 95_000 and hh.discretionary0[0] == 410_000 and hh.tfsa0[0] == 168_000 and hh.property0[0] == 2_850_000
    assert hh.education0[0] == 85_000 and [g.savings0[0] for g in inp.goals] == [15_000, 20_000]
    assert hh.children_end_step[0] == 11 and not hh.school_from_dependants  # school fees come from the expense lines
    assert inp.n_years == 60 and inp.primary_age_int == 40
    assert len(inp.liabilities) == 4 and len(inp.premiums) == sum(1 for p in doc.policies if not p.isGroup)


def test_portfolio_policy_follows_the_risk_profile_or_the_glide_path():
    pytest.importorskip("advisor_intel.fna_engine.risk_profile", reason="the risk profile score comes from the ported FNA engine")
    doc = sample_document()
    assert plan_from_document(doc, anchor=ANCHOR, config=CFG, cma=CMA_).accumulation_mix == "moderate"
    assert plan_from_document(doc, anchor=ANCHOR, config=CFG.with_(portfolio_policy="default"), cma=CMA_).accumulation_mix is None


def test_overrides_fix_resample_or_reset_a_fact():
    doc = sample_document()
    prof, fid = add_fact(
        prior_profile(doc),
        "expenses.items[]",
        "expenses.items[groceries]",
        LogNormal(median=9_000, p10=7_000, p90=13_000),
        instance="groceries",
        period="monthly",
        p_correct=0.6,
    )
    plan = plan_for(prof, doc)
    fixed = plan.with_overrides({fid: 12_000})
    assert np.all(draws(fixed, 50)["expenses.items[groceries]"].values == 12_000)
    reset = plan.with_overrides({fid: "prior"})
    assert reset.samplers[fid].mode == "prior"
    by_path = plan.with_overrides({"expenses.items[groceries]": 5_000, "retirement.client.planningAge": 95})
    assert np.all(draws(by_path, 10)["expenses.items[groceries]"].values == 5_000)
    assert np.all(by_path.realise(by_path.uniforms(1, 10), TAX).persons[0].planning_age == 95)
