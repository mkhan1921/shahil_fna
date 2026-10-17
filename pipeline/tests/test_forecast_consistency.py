"""§12.8: with volatility switched off and every fact at its median, the Monte Carlo layer agrees with the FNA.

The median capital at retirement must equal the FNA's projectedCapital within 0.5% and the depletion age must
match. The engine-backed tests skip when ``advisor_intel.fna_engine`` is not importable; the comparison with the
TypeScript engine's own output (tests/fixtures/sample_analysis.json) runs regardless.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from advisor_intel import binding
from advisor_intel.forecast.config import load_cma, load_config
from advisor_intel.forecast.mapper import plan_from_document
from advisor_intel.forecast.run import Forecaster
from advisor_intel.models import fna as F
from advisor_intel.models.dists import LogNormal, Triangular
from tests.forecast_support import ANCHOR, StubTaxModel, add_fact, prior_profile, sample_analysis, sample_document

ZERO = load_config().with_(zero_volatility=True)
CMA_ = load_cma()
ENGINE_REASON = "§12.8 compares with the ported FNA engine (advisor_intel.fna_engine), which is not importable here"


def _median_forecast(doc: F.FnaDocument, tax):
    plan = plan_from_document(doc, anchor=ANCHOR, config=ZERO, cma=CMA_)
    return Forecaster(plan, tax).run(1, 4).result


def _assert_matches(result, retirement_results: dict) -> None:
    for person, rr in retirement_results.items():
        projected = rr["projectedCapital"]
        capital = result.capital_at_retirement[person]
        assert np.ptp(capital) == 0  # zero volatility: every path is the same
        assert capital[0] == pytest.approx(projected, rel=0.005)
        assert capital[0] == pytest.approx(projected, rel=1e-9)  # in fact the same arithmetic
        depletion = result.depletion_age[person][0]
        if rr["depletionAge"] is None:
            assert math.isnan(depletion), person
        else:
            assert depletion == pytest.approx(rr["depletionAge"], abs=1e-9), person


def _profile_with_uncertain_facts():
    doc = sample_document()
    prof = prior_profile(doc)
    prof, _ = add_fact(
        prof,
        "retirement.{p}.retirementAge",
        "retirement.client.retirementAge",
        Triangular(low=61, mode=64, high=67),
        instance="client",
        unit="age",
        p_correct=0.7,
    )
    prof, _ = add_fact(
        prof,
        "income.{p}.grossMonthly",
        "income.spouse.grossMonthly",
        LogNormal(median=58_000, p10=50_000, p90=70_000),
        instance="spouse",
        period="monthly",
        p_correct=0.8,
    )
    fund = next(f for f in prof.facts.values() if f.slot_id == "retirementFunds[].employeeMonthly" and f.value.dist.value == 2500)
    prof, _ = add_fact(
        prof,
        "retirementFunds[].employeeMonthly",
        fund.fna_path,
        Triangular(low=2_000, mode=3_000, high=5_000),
        instance=fund.instance_key,
        period="monthly",
        p_correct=0.6,
        entity_refs=fund.entity_refs,
    )
    return prof, doc


def test_zero_volatility_medians_match_the_fna_engine():
    engine = pytest.importorskip("advisor_intel.fna_engine", reason=ENGINE_REASON)
    from advisor_intel.forecast.tax import default_tax_model

    prof, doc = _profile_with_uncertain_facts()
    median = binding.materialise(prof, "median", base=doc)  # every fact at its median
    analysis = engine.analyse(median, today=ANCHOR)
    result = _median_forecast(median, default_tax_model(median.assumptions.taxYear))
    _assert_matches(result, {k: p.retirement.model_dump() for k, p in analysis.persons.items()})


def test_zero_volatility_matches_the_fna_engine_for_retirees_and_other_assumptions():
    engine = pytest.importorskip("advisor_intel.fna_engine", reason=ENGINE_REASON)
    from advisor_intel.forecast.tax import default_tax_model

    doc = sample_document()
    doc.client.employmentType = "retired"  # drawing income from today
    doc.client.dateOfBirth = "1958-03-02"
    doc.retirementFunds[0].type = "living-annuity"
    doc.retirement["client"] = F.RetirementGoal(
        retirementAge=65, planningAge=95, targetMode="amount", targetMonthly=60_000, otherIncomeMonthly=5_000
    )
    doc.assets[6].earmark = "retirement"  # a unit trust counted towards retirement, as the FNA does
    doc.assumptions = doc.assumptions.model_copy(
        update={"cpi": 0.06, "salaryEscalation": 0.07, "preRetirementReturn": 0.10, "postRetirementReturn": 0.065}
    )
    analysis = engine.analyse(doc, today=ANCHOR)
    assert analysis.persons["client"].retirement.retired
    result = _median_forecast(doc, default_tax_model(doc.assumptions.taxYear))
    _assert_matches(result, {k: p.retirement.model_dump() for k, p in analysis.persons.items()})


def test_zero_volatility_matches_the_typescript_engine_output():
    """The sample household against lib/fna's own analyse() output, exported to the fixture."""
    expected = sample_analysis()["persons"]
    result = _median_forecast(sample_document(), StubTaxModel())  # tax does not enter the retirement projection
    _assert_matches(result, {k: v["retirement"] for k, v in expected.items()})


def test_default_tax_model_is_the_fna_engines_tax():
    engine = pytest.importorskip("advisor_intel.fna_engine", reason=ENGINE_REASON)
    from advisor_intel.fna_engine import tables, tax
    from advisor_intel.forecast.tax import default_tax_model

    m = default_tax_model("2026/27")
    t = m.person_tax(
        age=40.0,
        gross_monthly=100_000.0,
        annual_bonus=100_000.0,
        retirement_contributions_annual=240_000.0,
        employer_retirement_annual=120_000.0,
        medical_scheme_members=4,
        uif_applies=True,
    )
    income = sample_analysis()["persons"]["client"]["income"]
    assert float(t.annual_tax) == pytest.approx(income["tax"]["annualTax"]) and float(t.monthly_uif) == pytest.approx(income["uifMonthly"])
    nets = np.array([15_000.0, 42_000.0, 79_000.0])
    ours = m.gross_up_monthly(nets, age=40.0, retirement_monthly=7_500.0, medical_members=4, medical_payroll_monthly=9_850.0)
    table = tables.table_for("2026/27")
    theirs = [
        tax.gross_up_monthly(
            float(n), age=40.0, table=table, retirement_monthly=7_500.0, medical_members=4, medical_payroll_monthly=9_850.0
        )
        for n in nets
    ]
    assert np.allclose(ours, theirs, atol=1e-4)
    assert engine is not None
