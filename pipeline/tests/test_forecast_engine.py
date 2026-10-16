"""The cash-flow engine (spec §12.3) against hand-computed tables and closed forms (§12.6)."""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pytest

from advisor_intel.forecast.config import FxConfig, load_cma, load_config
from advisor_intel.forecast.engine import EngineResult, _amortise, simulate
from advisor_intel.forecast.mapper import build_plan, plan_from_document
from advisor_intel.forecast.returns import MarketModel
from advisor_intel.forecast.run import Forecaster
from advisor_intel.models import fna as F
from tests.forecast_support import ANCHOR, StubTaxModel, ZeroTaxModel, prior_profile, sample_document

CFG = load_config()
CMA_ = load_cma()
ZERO = CFG.with_(zero_volatility=True, fx_usdzar=FxConfig(drift_real=0.0))  # no drift: global classes earn the same in rand


def flat_cma():
    """The CMA with every class placed alike, so each household portfolio earns exactly the document's mean."""
    data = CMA_.model_dump(mode="json")
    for a in data["asset_classes"]:
        a["relative_real_return"] = 0.03
    return type(CMA_).model_validate(data)


def doc_with(**kw) -> F.FnaDocument:
    """A single-person document built field by field (no prior FNA)."""
    d = F.FnaDocument()
    d.client.dateOfBirth = kw.get("dob", "1966-10-15")
    d.client.employmentType = kw.get("employment", "salaried")
    d.income["client"] = F.Income(grossMonthly=kw.get("gross", 30_000), uifApplies=False)
    d.expenses.items = kw.get("items", {"groceries": 10_000, "dining": 2_000})
    d.assets = kw.get("assets", [F.Asset(type="cash", value=100_000)])
    d.retirementFunds = kw.get("funds", [F.RetirementFund(type="retirement-annuity", value=500_000, employeeMonthly=1_000, viaPayroll=False)])
    d.liabilities = kw.get("liabilities", [])
    d.goals = kw.get("goals", [])
    d.retirement["client"] = F.RetirementGoal(retirementAge=kw.get("retire", 62), planningAge=kw.get("plan", 64),
                                              targetMode="amount", targetMonthly=kw.get("target", 5_000))
    a = kw.get("assumptions", {"cpi": 0.0, "salaryEscalation": 0.0, "preRetirementReturn": 0.0, "postRetirementReturn": 0.0,
                               "riskCapitalReturn": 0.0, "educationInflation": 0.0})
    d.assumptions = d.assumptions.model_copy(update=a)
    return d


def run_doc(doc: F.FnaDocument, *, config=ZERO, cma=None, tax=None, n=4, seed=1) -> EngineResult:
    plan = plan_from_document(doc, anchor=ANCHOR, config=config, cma=cma or flat_cma())
    return Forecaster(plan, tax or ZeroTaxModel()).run(seed, n).result


def test_zero_volatility_reproduces_a_hand_computed_cash_flow_table():
    """Cash 100k; RA 500k plus R1,000 a month; salary R30k; groceries R10k (essential) and dining R2k a month;
    retire at 62 (two years) with R5,000 a month to age 64; every rate zero and no tax, so each year is arithmetic."""
    r = run_doc(doc_with())
    # Year 1 and 2: salary 360,000 − living 144,000 − RA 12,000 = 204,000 surplus to discretionary; the RA grows by 12,000.
    # Year 3 and 4: the RA (524,000 at retirement) pays R60,000 a year inside the 2.5%–17.5% band; spending equals it.
    # Year 5 on: past the planning age the target ends and the 2.5% minimum drawdown moves money to discretionary.
    expected_nw = [600_000, 816_000, 1_032_000, 972_000, 912_000, 912_000, 912_000]
    assert np.allclose(r.net_worth_real[:, :7], expected_nw)
    assert np.allclose(r.capital_at_retirement["client"], 524_000) and np.allclose(r.capital_at_retirement_real["client"], 524_000)
    assert np.allclose(r.retirement_income_real[:, :6], [0, 0, 60_000, 60_000, 0.025 * 404_000, 0.025 * 393_900])
    assert np.all(r.success) and not r.liquidity_breach.any() and np.all(np.isnan(r.depletion_age["client"]))
    assert np.all(r.total_shortfall_real == 0)
    # Funded ratio: (assets 600,000 + income 2 x 360,000) / (essential 2 x 120,000 + target 2 x 60,000).
    assert np.allclose(r.funded_ratio, 1_320_000 / 360_000)
    assert r.ages[:3] == [60, 61, 62] and r.net_worth_real.shape == (4, 41)


def test_a_single_lump_sum_at_a_fixed_real_return_compounds_exactly():
    a = {"cpi": 0.05, "salaryEscalation": 0.05, "preRetirementReturn": 0.08, "postRetirementReturn": 0.08, "riskCapitalReturn": 0.08,
         "educationInflation": 0.05}
    doc = doc_with(dob="1986-10-15", gross=0, items={}, assets=[F.Asset(type="unit-trust", value=1_000_000)],
                   funds=[F.RetirementFund(type="pension", value=1_000_000)], retire=65, plan=92, target=0, assumptions=a)
    r = run_doc(doc)
    real = 1.08 / 1.05
    years = np.arange(26)
    assert np.allclose(r.net_worth_real[0, :26], 2_000_000 * real**years, rtol=1e-12)
    assert r.capital_at_retirement["client"][0] == pytest.approx(1_000_000 * 1.08**25, rel=1e-12)  # 300 months of FNA monthly compounding
    assert r.capital_at_retirement_real["client"][0] == pytest.approx(1_000_000 * real**25, rel=1e-12)


def test_doubling_all_expenses_lowers_the_probability_of_success():
    doc = sample_document()
    pos = []
    for factor in (1.0, 1.5, 2.0):
        d = doc.model_copy(deep=True)
        d.expenses.items = {k: v * factor for k, v in doc.expenses.items.items()}
        pos.append(run_doc(d, config=CFG, cma=CMA_, tax=StubTaxModel(), n=2_000, seed=4).pos)
    assert pos[0] >= pos[1] >= pos[2] and pos[0] - pos[2] > 0.2


def test_drawdown_is_clamped_to_the_living_annuity_band():
    retired = {"employment": "retired", "dob": "1961-10-15", "retire": 60, "plan": 90, "gross": 0, "items": {}}
    fund = [F.RetirementFund(type="living-annuity", value=1_000_000)]
    # R20,000 a month is 24% of R1m: only 17.5% can be drawn; with no other assets the gap is unmet at once.
    r = run_doc(doc_with(target=20_000, funds=fund, assets=[], **retired))
    assert np.allclose(r.retirement_income_real[:, 0], 175_000) and not r.success.any()
    assert np.allclose(r.first_shortfall_age, (ANCHOR.toordinal() - date(1961, 10, 15).toordinal()) / 365.2425)
    # The same gap funded from cash for a while: success until the cash runs out, then a shortfall.
    r = run_doc(doc_with(target=20_000, funds=fund, assets=[F.Asset(type="cash", value=10_000_000)], **retired))
    assert r.success.all()
    # A need below 2.5% still draws the minimum; the excess income is invested.
    r = run_doc(doc_with(target=1_000, funds=fund, assets=[], **retired))
    assert np.allclose(r.retirement_income_real[:, 0], 25_000) and r.success.all()
    assert r.net_worth_real[0, 1] == pytest.approx(1_000_000 - 12_000)  # only the R12,000 spent leaves; the rest is invested


def test_liquidity_breach_and_essential_shortfall():
    # Essential spending above income with no liquid assets: a breach, and a shortfall once the RA debit order is cut too.
    r = run_doc(doc_with(gross=10_000, items={"groceries": 15_000}, assets=[], retire=62, plan=63))
    assert r.liquidity_breach.all() and not r.success.any()
    assert np.allclose(r.total_shortfall_real, 2 * (180_000 - 120_000))
    assert np.allclose(r.capital_at_retirement["client"], 500_000)  # the R1,000 a month was not paid after all
    # The excess is discretionary: a breach, but dining is cut and essential spend is met.
    r = run_doc(doc_with(gross=10_000, items={"groceries": 5_000, "dining": 10_000}, assets=[], retire=62, plan=63))
    assert r.liquidity_breach.all() and r.success.all()
    assert np.allclose(r.capital_at_retirement["client"], 524_000)


def test_loans_are_repaid_until_paid_off():
    loans = _amortise(np.array([100_000.0]), np.array([0.12]), np.array([10_000.0]), 3)
    b, paid = 100_000.0, 0.0
    for _ in range(11):  # FNA monthsToRepay: ceil(-ln(1 - B r / M) / ln(1 + r)) = 11 months
        pay = min(10_000.0, b * 1.01)
        b = b * 1.01 - pay
        paid += pay
    assert loans.payments[0, 0] == pytest.approx(paid) and b == pytest.approx(0, abs=1e-6)
    assert np.allclose(loans.payments[0, 1:], 0) and np.allclose(loans.balance_end[0], 0)
    # An instalment below the interest never repays: payments continue and the balance grows.
    forever = _amortise(np.array([100_000.0]), np.array([0.24]), np.array([1_000.0]), 2)
    assert np.allclose(forever.payments, 12_000) and forever.balance_end[0, 1] > forever.balance_end[0, 0] > 100_000


def test_goals_are_funded_on_time_from_savings_then_liquid_assets():
    goals = [F.OtherGoal(name="Car", cost=50_000, targetYear=2028, existingSavings=60_000),
             F.OtherGoal(name="Holiday", cost=40_000, targetYear=2027),
             F.OtherGoal(name="Yacht", cost=50_000_000, targetYear=2029)]
    doc = doc_with(goals=goals)
    plan = plan_from_document(doc, anchor=ANCHOR, config=ZERO, cma=flat_cma())
    r = Forecaster(plan, ZeroTaxModel()).run(1, 4).result
    keys = {g.id: plan.goal_keys[g.id] for g in doc.goals}
    funded = {name: r.goal_funded[keys[g.id]].all() for name, g in zip(("car", "holiday", "yacht"), doc.goals)}
    assert funded == {"car": True, "holiday": True, "yacht": False}


def test_two_people_retire_on_their_own_dates():
    doc = sample_document()
    plan = plan_from_document(doc, anchor=ANCHOR, config=ZERO, cma=CMA_)
    r = Forecaster(plan, StubTaxModel()).run(1, 4).result
    assert set(r.capital_at_retirement) == {"client", "spouse"}
    assert r.capital_at_retirement["client"][0] > r.capital_at_retirement["spouse"][0] > 0
    assert r.retirement_income_real[0, 24] > 0 and r.retirement_income_real[0, 23] == 0  # the spouse retires first (age 63 in year 25)


def test_engine_is_deterministic_for_the_same_draws():
    doc = sample_document()
    plan = build_plan(prior_profile(doc), base_document=doc, config=CFG, cma=CMA_)
    fc = Forecaster(plan, StubTaxModel())
    a, b = fc.run(8, 500).result, fc.run(8, 500).result
    assert np.array_equal(a.net_worth_real, b.net_worth_real) and np.array_equal(a.funded_ratio, b.funded_ratio)
    c = fc.run(9, 500).result
    assert not np.array_equal(a.net_worth_real, c.net_worth_real)


def test_market_must_cover_the_horizon():
    doc = sample_document()
    plan = plan_from_document(doc, anchor=ANCHOR, config=ZERO, cma=CMA_)
    inp = plan.realise(plan.uniforms(1, 3), StubTaxModel())
    mm = MarketModel(ZERO, CMA_, 0.045)
    with pytest.raises(ValueError, match="years"):
        simulate(inp, mm.realise(mm.shocks(1, 3, 10)), config=ZERO, cma=CMA_, tax_model=StubTaxModel())
    assert math.isclose(inp.assumptions.real(0.085), 1.085 / 1.045 - 1)
