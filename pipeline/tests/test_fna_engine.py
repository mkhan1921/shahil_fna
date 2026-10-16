"""tests/engine.test.ts ported case for case (§12.7): the cases that pin the engine to SARS worked examples.

``toBeCloseTo(x, d)`` passes when |a - x| < 10**-d / 2, as :func:`close` does. The TypeScript uses
``sampleDocument()``; here it is the same household exported to ``fixtures/sample_fna.json``.
"""

from __future__ import annotations

import json
import math
from datetime import datetime

import pytest

from advisor_intel.fna_engine import analyse
from advisor_intel.fna_engine.analysis import compute_estate, compute_income
from advisor_intel.fna_engine.defaults import new_asset, new_dependant, new_document, new_liability, new_policy, new_retirement_fund
from advisor_intel.fna_engine.estate import conveyancing_guideline_fee, masters_fee
from advisor_intel.fna_engine.finance import (
    annuity_factor,
    fv_escalating_contributions,
    loan_repayment,
    months_to_repay,
    pv_growing_annuity,
    pv_growing_monthly_income,
    real_rate,
    required_escalating_saving,
)
from advisor_intel.fna_engine.id_number import age_on, luhn_valid, parse_sa_id
from advisor_intel.fna_engine.risk_profile import score_risk_profile
from advisor_intel.fna_engine.tax import (
    PersonTaxInput,
    apply_brackets,
    estate_duty,
    get_tax_table,
    income_tax,
    medical_tax_credit,
    person_tax,
    retirement_deduction,
    retirement_lump_sum_tax,
    withdrawal_lump_sum_tax,
)
from advisor_intel.models.fna import Dependant, FnaDocument, RetirementGoal

from .conftest import load_fixture

T27 = get_tax_table("2026/27")
T26 = get_tax_table("2025/26")
NOW = datetime(2026, 9, 29, 12, 0, 0)  # new Date('2026-09-29T12:00:00') in a UTC process


def close(actual: float, expected: float, digits: int = 2) -> bool:
    """``expect(actual).toBeCloseTo(expected, digits)``."""
    return abs(actual - expected) < 10**-digits / 2


def sample_document() -> FnaDocument:
    return FnaDocument.model_validate(load_fixture("sample_fna.json"))


def has_nan(x) -> bool:
    if isinstance(x, dict):
        return any(has_nan(v) for v in x.values())
    if isinstance(x, list):
        return any(has_nan(v) for v in x)
    return isinstance(x, float) and math.isnan(x)


def has_infinity(x) -> bool:
    if isinstance(x, dict):
        return any(has_infinity(v) for v in x.values())
    if isinstance(x, list):
        return any(has_infinity(v) for v in x)
    return isinstance(x, float) and math.isinf(x)


# --------------------------------------------------------------------------- SARS income tax


class TestSarsIncomeTax:
    def test_reproduces_the_sars_2026_27_worked_example(self):
        """R500,000, under 65, 2 medical members."""
        assert close(apply_brackets(500_000, T27.brackets), 116_237, 0)
        assert close(income_tax(500_000, 40, T27), 98_417, 0)
        assert close(income_tax(500_000, 40, T27) - medical_tax_credit(2, T27), 89_393, 0)

    def test_bracket_bases_reconcile_for_both_tax_years(self):
        for table in (T26, T27):
            for prev, cur in zip(table.brackets, table.brackets[1:], strict=False):
                assert close(prev.base + (cur.from_ - prev.from_) * prev.rate, cur.base, 0)

    def test_tax_thresholds_produce_zero_tax_after_rebates(self):
        assert income_tax(T27.thresholds.under65, 40, T27) < 1
        assert income_tax(T27.thresholds.age65to74, 70, T27) < 1
        assert income_tax(T27.thresholds.age75plus, 80, T27) < 1
        assert income_tax(T26.thresholds.under65, 40, T26) < 1
        assert income_tax(T27.thresholds.under65 + 1000, 40, T27) > 0

    def test_medical_tax_credits_follow_the_s6a_schedule(self):
        assert medical_tax_credit(0, T27) == 0
        assert medical_tax_credit(1, T27) == 376 * 12
        assert medical_tax_credit(2, T27) == 752 * 12
        assert medical_tax_credit(4, T27) == (752 + 2 * 254) * 12

    def test_caps_the_s11f_retirement_deduction_at_27_5_percent_and_the_annual_cap(self):
        assert close(retirement_deduction(100_000, 300_000, T27), 82_500)
        assert retirement_deduction(20_000, 300_000, T27) == 20_000
        assert retirement_deduction(900_000, 5_000_000, T27) == 430_000
        assert retirement_deduction(900_000, 5_000_000, T26) == 350_000

    def test_computes_paye_and_uif_for_an_employee(self):
        r = person_tax(
            PersonTaxInput(
                age=40,
                gross_monthly=50_000,
                annual_bonus=0,
                other_taxable_monthly=0,
                retirement_contributions_annual=45_000,
                employer_retirement_annual=0,
                medical_scheme_members=1,
                uif_applies=True,
            ),
            T27,
        )
        assert r.taxable == 555_000
        expected = 125_599 + (555_000 - 530_200) * 0.36 - 17_820 - 376 * 12
        assert close(r.annualTax, expected, 0)
        assert close(r.monthlyUif, 177.12, 2)
        assert r.marginalRate == 0.36


# --------------------------------------------------------------------------- lump sum tax and estate duty


class TestLumpSumTaxAndEstateDuty:
    def test_taxes_a_r1m_retirement_lump_sum_at_r101_700(self):
        assert close(retirement_lump_sum_tax(1_000_000, T27), 101_700, 0)
        assert retirement_lump_sum_tax(550_000, T27) == 0

    def test_aggregates_prior_lump_sums(self):
        assert close(retirement_lump_sum_tax(500_000, T27, 500_000), apply_brackets(1_000_000, T27.retirementLumpSum), 0)

    def test_applies_the_withdrawal_table(self):
        assert withdrawal_lump_sum_tax(27_500, T27) == 0
        assert close(withdrawal_lump_sum_tax(726_000, T27), 125_730, 0)

    def test_charges_estate_duty_at_20_then_25_percent(self):
        assert estate_duty(6_000_000 - 3_500_000, T27) == 500_000
        assert estate_duty(40_000_000, T27) == 30_000_000 * 0.2 + 10_000_000 * 0.25

    def test_uses_the_2018_masters_fee_tariff(self):
        assert masters_fee(200_000) == 0
        assert masters_fee(350_000) == 600
        assert masters_fee(1_000_000) == 600 + 6 * 200
        assert masters_fee(10_000_000) == 7_000

    def test_matches_the_lssa_2026_conveyancing_guideline_points(self):
        assert conveyancing_guideline_fee(1_000_000) == 26_275
        assert conveyancing_guideline_fee(2_000_000) == 36_875
        assert conveyancing_guideline_fee(3_000_000) == 47_475
        assert conveyancing_guideline_fee(5_000_000) == 68_675
        assert conveyancing_guideline_fee(10_000_000) == 95_375


# --------------------------------------------------------------------------- time value of money


class TestTimeValueOfMoney:
    def test_matches_the_growing_annuity_due_test_vector(self):
        """R240k p.a., 15 yrs, 9% / 5%."""
        assert pv_growing_annuity(240_000, 15, 0.09, 0.05, True) > 2_800_000
        assert pv_growing_annuity(240_000, 15, 0.09, 0.05, True) < 2_815_000

    def test_monthly_escalating_income_pv_equals_the_level_annuity_at_the_real_rate(self):
        r = (1 + real_rate(0.09, 0.05)) ** (1 / 12) - 1
        expected = 10_000 * annuity_factor(r, 120) * (1 + r)
        assert close(pv_growing_monthly_income(10_000, 10, 0.09, 0.05), expected, 4)

    def test_inverts_escalating_contributions_exactly(self):
        monthly = required_escalating_saving(1_000_000, 15, 0.085, 0.055)
        assert close(fv_escalating_contributions(monthly, 15, 0.085, 0.055), 1_000_000, 2)

    def test_amortises_loans_consistently(self):
        pmt = loan_repayment(1_000_000, 0.1075, 240)
        assert months_to_repay(1_000_000, 0.1075, pmt) == 240
        assert months_to_repay(100_000, 0.2, 1_000) == math.inf


# --------------------------------------------------------------------------- SA ID numbers


class TestSaIdNumbers:
    def test_validates_the_luhn_check_and_extracts_details(self):
        assert luhn_valid("8001015009087")
        r = parse_sa_id("8001015009087", NOW)
        assert (r.valid, r.dateOfBirth, r.gender, r.citizenship) == (True, "1980-01-01", "male", "SA citizen")

    def test_rejects_typos_and_impossible_dates(self):
        assert not parse_sa_id("8001015009088", NOW).valid
        assert not parse_sa_id("8002305009087", NOW).valid
        assert not parse_sa_id("12345", NOW).valid

    def test_resolves_the_century_and_gender(self):
        r = parse_sa_id("8909030456085", NOW)
        assert (r.valid, r.dateOfBirth, r.gender) == (True, "1989-09-03", "female")
        assert age_on("1986-04-12", NOW) == 40
        assert age_on("1986-10-12", NOW) == 39


# --------------------------------------------------------------------------- estate calculation


class TestEstateCalculation:
    def test_applies_executor_fees_abatement_and_duty_for_a_single_person(self):
        doc = new_document()
        doc.client.dateOfBirth = "1960-01-01"
        house = new_asset("client").model_copy(update={"type": "residential-property", "value": 3_000_000, "bequeathToSpouse": False})
        cash = new_asset("client").model_copy(update={"type": "cash", "value": 1_000_000, "bequeathToSpouse": False})
        doc.assets = [house, cash]
        income = compute_income(doc, "client", T27, NOW)
        e = compute_estate(doc, "client", income, T27, False)
        assert e.grossEstate == 4_000_000
        assert close(e.executorFees, 4_000_000 * 0.035 * 1.15, 2)
        assert e.cgt == 0
        expected_dutiable = e.netEstate - 3_500_000
        assert close(e.dutiable, expected_dutiable, 2)
        assert close(e.estateDuty, expected_dutiable * 0.2, 2)

    def test_excludes_nominated_beneficiary_policies_from_the_gross_estate_but_includes_them_as_deemed_property(self):
        doc = new_document()
        p = new_policy("client").model_copy(update={"type": "life", "cover": 2_000_000, "beneficiary": "children"})
        q = new_policy("client").model_copy(update={"type": "life", "cover": 500_000, "beneficiary": "estate"})
        doc.policies = [p, q]
        income = compute_income(doc, "client", T27, NOW)
        e = compute_estate(doc, "client", income, T27, False)
        assert e.grossEstate == 500_000
        assert e.deemedProperty == 2_000_000
        assert e.cashAvailable == 500_000

    def test_attributes_half_the_joint_estate_when_married_in_community_of_property(self):
        doc = new_document()
        doc.household = doc.household.model_copy(update={"maritalStatus": "married", "maritalRegime": "icop", "includeSpouse": True})
        doc.assets = [new_asset("client").model_copy(update={"type": "unit-trust", "value": 1_000_000})]
        income = compute_income(doc, "client", T27, NOW)
        e = compute_estate(doc, "client", income, T27, True)
        assert e.grossEstate == 500_000

    def test_charges_cgt_on_death_above_the_r440_000_exclusion_and_rolls_over_spousal_bequests(self):
        doc = new_document()
        doc.client.dateOfBirth = "1970-01-01"
        shares = new_asset("client").model_copy(
            update={"type": "shares", "value": 2_000_000, "baseCost": 500_000, "bequeathToSpouse": False}
        )
        doc.assets = [shares]
        income = compute_income(doc, "client", T27, NOW)
        e = compute_estate(doc, "client", income, T27, False)
        assert e.capitalGains == 1_500_000
        # taxable gain = (1.5m - 440k) * 40% = 424,000 taxed at the margin from zero income.
        assert close(e.cgt, income_tax(424_000, 56, T27), 0)

        doc.household = doc.household.model_copy(
            update={"maritalStatus": "married", "maritalRegime": "anc-no-accrual", "includeSpouse": True}
        )
        shares.bequeathToSpouse = True
        e2 = compute_estate(doc, "client", income, T27, True)
        assert e2.cgt == 0
        assert e2.estateDuty == 0

    def test_excludes_the_primary_residence_gain_up_to_the_exclusion(self):
        doc = new_document()
        home = new_asset("client").model_copy(
            update={"type": "primary-residence", "value": 4_000_000, "baseCost": 1_500_000, "bequeathToSpouse": False}
        )
        doc.assets = [home]
        income = compute_income(doc, "client", T27, NOW)
        e = compute_estate(doc, "client", income, T27, False)
        assert e.capitalGains == 0  # gain 2.5m < 3m exclusion


# --------------------------------------------------------------------------- full analysis of the sample household


class TestFullAnalysisOfTheSampleHousehold:
    @pytest.fixture(scope="class")
    @classmethod
    def a(cls):
        return analyse(sample_document(), today=NOW)

    def test_analyses_both_spouses(self, a):
        assert a.people == ["client", "spouse"]
        assert a.persons["client"].income.age == 40
        assert a.persons["spouse"].income.age == 37

    def test_produces_a_sane_cash_flow(self, a):
        assert 80_000 < a.cashflow.takeHome < 110_000
        assert math.isfinite(a.cashflow.surplus)

    def test_identifies_life_cover_and_income_protection_needs(self, a):
        c = a.persons["client"]
        assert c.death.need > c.death.provision * 0.5
        assert c.incomeProtection.need > 40_000
        assert c.incomeProtection.provision == 50_000
        assert c.deathIncomeYears > 10

    def test_flags_the_missing_will_and_guardian(self, a):
        assert any("no confirmed valid will" in f.title for f in a.findings)
        assert any("guardian" in f.title for f in a.findings)

    def test_projects_retirement_with_a_coherent_timeline(self, a):
        r = a.persons["client"].retirement
        assert r.projectedCapital > r.currentCapital
        assert r.requiredCapital > 0
        assert close(r.timeline[0].capital, r.currentCapital, 0)
        assert any(t.phase == "retirement" for t in r.timeline)
        if r.shortfall > 0:
            assert r.additionalMonthly > 0

    def test_computes_education_funding_per_child(self, a):
        assert len(a.education) == 2
        neo = next(e for e in a.education if e.name.startswith("Neo"))
        assert neo.yearsToTertiary == 8
        assert len(neo.schedule) == 4
        assert neo.capitalAtStart > 4 * 150_000

    def test_scores_the_risk_profile(self, a):
        assert a.risk.complete
        assert a.risk.profile is not None

    def test_is_complete_except_for_advice(self, a):
        assert a.completenessScore > 0.8


# --------------------------------------------------------------------------- edge cases


class TestEdgeCases:
    def test_handles_an_empty_document_without_nan(self):
        a = analyse(new_document(), today=NOW).model_dump(mode="json")
        assert not has_nan(a)
        assert "null" in json.dumps(a)

    def test_handles_a_retiree_drawing_a_living_annuity(self):
        doc = new_document()
        doc.client.dateOfBirth = "1955-01-01"
        doc.client.employmentType = "retired"
        doc.retirementFunds = [
            new_retirement_fund("client").model_copy(update={"type": "living-annuity", "value": 3_000_000, "drawdownRate": 0.05})
        ]
        doc.retirement["client"] = doc.retirement["client"].model_copy(update={"targetMode": "amount", "targetMonthly": 20_000})
        r = analyse(doc, today=NOW).persons["client"].retirement
        assert r.retired
        assert r.yearsToRetirement == 0
        assert r.currentCapital == 3_000_000
        assert math.isfinite(r.requiredCapital)

    def test_does_not_count_credit_life_debts_in_the_death_need(self):
        doc = new_document()
        doc.liabilities = [new_liability("client").model_copy(update={"balance": 500_000, "creditLifeCover": True})]
        assert analyse(doc, today=NOW).persons["client"].estate.liabilities == 0

    def test_keeps_the_risk_profile_at_the_lower_of_tolerance_and_capacity(self):
        r = score_risk_profile(
            {
                "horizon": 0,
                "income-stability": 0,
                "emergency": 0,
                "proportion": 0,
                "knowledge": 4,
                "objective": 4,
                "drawdown": 4,
                "tradeoff": 4,
            }
        )
        assert r.profile is not None and r.profile.key == "conservative"
        assert r.mismatch


# --------------------------------------------------------------------------- regression tests from the calculation audit


def base_document() -> FnaDocument:
    doc = new_document()
    doc.client.dateOfBirth = "1980-06-15"
    doc.income["client"].grossMonthly = 100_000
    return doc


class TestRegressionsFromTheCalculationAudit:
    def test_capitalises_the_actual_support_for_parents_instead_of_extending_full_income_replacement(self):
        doc = base_document()
        doc.dependants = [
            new_dependant().model_copy(
                update={
                    "relationship": "parent",
                    "dateOfBirth": "1955-01-01",
                    "monthlySupport": 3_500,
                    "supportYears": 15,
                    "tertiary": False,
                    "schoolType": "none",
                }
            )
        ]
        a = analyse(doc, today=NOW)
        death = a.persons["client"].death
        support = next(line for line in death.needLines if line.label.startswith("Support for parents"))
        assert 400_000 < support.amount < 700_000
        assert a.persons["client"].deathIncomeYears == 0

    def test_starts_drawdown_today_for_someone_who_retired_early(self):
        doc = new_document()
        doc.client.dateOfBirth = "1971-09-01"
        doc.client.employmentType = "retired"
        doc.retirementFunds = [new_retirement_fund("client").model_copy(update={"type": "living-annuity", "value": 5_000_000})]
        doc.retirement["client"] = RetirementGoal(
            **{**doc.retirement["client"].model_dump(), "targetMode": "amount", "targetMonthly": 30_000, "planningAge": 92}
        )
        r = analyse(doc, today=NOW).persons["client"].retirement
        assert r.retired
        assert r.yearsInRetirement > 36
        assert r.retirementAge == 55
        assert r.depletionAge is not None and r.depletionAge < 80

    def test_gives_retirees_no_income_protection_disability_or_severe_illness_need(self):
        doc = new_document()
        doc.client.dateOfBirth = "1954-01-01"
        doc.client.employmentType = "retired"
        doc.income["client"].otherTaxableMonthly = 30_000
        a = analyse(doc, today=NOW)
        p = a.persons["client"]
        assert p.incomeProtection.need == 0
        assert p.disability.need == 0
        assert p.severeIllness.need == 0
        assert not any(f.area == "income-protection" for f in a.findings)

    def test_respects_settle_on_death_for_debts(self):
        doc = base_document()
        doc.liabilities = [new_liability("client").model_copy(update={"balance": 1_000_000, "settleOnDeath": False})]
        assert analyse(doc, today=NOW).persons["client"].estate.liabilities == 0

    def test_never_produces_an_infinite_saving_requirement_just_before_retirement(self):
        doc = new_document()
        doc.client.dateOfBirth = "1961-10-05"
        doc.income["client"].grossMonthly = 50_000
        a = analyse(doc, today=NOW)
        assert math.isfinite(a.persons["client"].retirement.additionalMonthly)
        assert "Infinity" not in a.model_dump_json()
        assert not has_infinity(a.model_dump())

    def test_applies_the_secondary_rebate_based_on_age_at_the_end_of_the_tax_year(self):
        doc = new_document()
        doc.client.dateOfBirth = "1961-12-01"
        doc.income["client"].grossMonthly = 40_000
        inc = compute_income(doc, "client", T27, NOW)
        assert inc.age == 64
        assert inc.taxAge == 65
        assert inc.tax.rebates == T27.rebates.primary + T27.rebates.secondary

    def test_counts_funeral_cover_as_provision_against_the_funeral_cost_in_the_death_need(self):
        doc = base_document()
        doc.policies = [new_policy("client").model_copy(update={"type": "funeral", "cover": 50_000})]
        death = analyse(doc, today=NOW).persons["client"].death
        assert next(line for line in death.provisionLines if line.label == "Funeral cover").amount == 50_000

    def test_reports_a_lump_sum_for_a_child_already_studying_and_uses_education_earmarked_assets(self):
        doc = base_document()
        doc.dependants = [
            Dependant(
                **{
                    **new_dependant().model_dump(),
                    "name": "Student",
                    "dateOfBirth": "2006-03-01",
                    "tertiaryStartAge": 19,
                    "tertiaryYears": 4,
                    "tertiaryCostAnnual": 150_000,
                }
            )
        ]
        a1 = analyse(doc, today=NOW).education[0]
        assert a1.yearsToTertiary == 0
        assert a1.lumpSumRequired > 0
        doc.assets = [new_asset("client").model_copy(update={"type": "unit-trust", "value": 1_000_000, "earmark": "education"})]
        a2 = analyse(doc, today=NOW).education[0]
        assert a2.lumpSumRequired == 0
        assert a2.status == "covered"

    def test_excludes_credit_life_debt_from_the_deceased_side_of_the_accrual_calculation(self):
        doc = sample_document()
        with_cl = analyse(doc, today=NOW).persons["client"].estate.accrualPayable
        doc.liabilities = [x.model_copy(update={"creditLifeCover": False}) for x in doc.liabilities]
        without = analyse(doc, today=NOW).persons["client"].estate.accrualPayable
        assert with_cl > without
