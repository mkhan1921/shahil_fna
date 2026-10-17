"""Shared SARS tables and the tax module (§12.7, §12.3, §7.6): schema checks, the Budget 2026 values in the
spec, CPI projection, numpy versions equal to the scalar ones, and the gross-up from take-home pay."""

from __future__ import annotations

import json
import shutil
from datetime import date

import numpy as np
import pytest

from advisor_intel.config import REPO_DIR
from advisor_intel.fna_engine.analysis import compute_income
from advisor_intel.fna_engine.defaults import new_document, new_retirement_fund
from advisor_intel.fna_engine.tables import (
    TableError,
    available_tax_years,
    get_table,
    indexed_table,
    latest_tax_year,
    load_table_file,
    table_for,
    table_for_year,
)
from advisor_intel.fna_engine.tax import (
    PersonTaxInput,
    capital_gains_tax,
    donations_tax,
    gross_up_monthly,
    income_tax,
    income_tax_vec,
    incremental_tax,
    interest_exemption,
    lump_sum_tax_vec,
    marginal_rate,
    person_tax,
    person_tax_vec,
    rebates_for,
    retirement_lump_sum_tax,
    savings_withdrawal_tax,
    tax_threshold,
    tax_years,
    taxable_capital_gain,
    uif_monthly,
    withdrawal_lump_sum_tax,
)

T27 = get_table("2026/27")
T26 = get_table("2025/26")
TABLES = REPO_DIR / "fna_tables"


# --------------------------------------------------------------------------- the table files


def test_the_2026_27_table_holds_the_budget_2026_values_in_the_spec():
    """Spec §12.7, '2026/27 values'."""
    t = T27
    assert [(b.from_, b.base, b.rate) for b in t.brackets] == [
        (0, 0, 0.18),
        (245_100, 44_118, 0.26),
        (383_100, 79_998, 0.31),
        (530_200, 125_599, 0.36),
        (695_800, 185_215, 0.39),
        (887_000, 259_783, 0.41),
        (1_878_600, 666_339, 0.45),
    ]
    assert (t.rebates.primary, t.rebates.secondary, t.rebates.tertiary) == (17_820, 9_765, 3_249)
    assert (t.thresholds.under65, t.thresholds.age65to74, t.thresholds.age75plus) == (99_000, 153_250, 171_300)
    assert (t.retirementDeduction.rate, t.retirementDeduction.cap) == (0.275, 430_000)
    assert [(b.from_, b.rate) for b in t.retirementLumpSum] == [(0, 0), (550_000, 0.18), (770_000, 0.27), (1_155_000, 0.36)]
    assert (t.tfsa.annual, t.tfsa.lifetime) == (46_000, 500_000)
    assert (t.interestExemption.under65, t.interestExemption.over65) == (23_800, 34_500)
    assert (t.medicalCredits.main, t.medicalCredits.firstDependant, t.medicalCredits.additional) == (376, 376, 254)
    assert (t.cgt.annualExclusion, t.cgt.deathExclusion) == (50_000, 440_000)
    assert (t.donations.annualExemption, t.donations.rateLow, t.donations.threshold, t.donations.rateHigh) == (
        150_000,
        0.2,
        30_000_000,
        0.25,
    )
    assert (t.estateDuty.abatement, t.estateDuty.rateLow, t.estateDuty.threshold, t.estateDuty.rateHigh) == (
        3_500_000,
        0.2,
        30_000_000,
        0.25,
    )
    assert (t.savingsWithdrawal.perTaxYear, t.savingsWithdrawal.minimum) == (1, 2_000)
    assert (t.livingAnnuity.minDrawdown, t.livingAnnuity.maxDrawdown) == (0.025, 0.175)
    assert (t.startDate, t.endDate, t.status) == ("2026-03-01", "2027-02-28", "confirmed")


def test_every_table_file_loads_newest_first():
    assert available_tax_years() == tax_years() == ["2026/27", "2025/26"]
    assert latest_tax_year() == "2026/27"
    for year in available_tax_years():
        assert get_table(year).taxYear == year


def test_an_unknown_year_raises_unless_the_app_fallback_is_asked_for():
    with pytest.raises(KeyError, match="available: 2026/27, 2025/26"):
        get_table("2031/32")
    assert table_for("2031/32") is T27  # getTaxTable falls back to the default year


def _broken(tmp_path, change, name="za_2026_27.json"):
    d = tmp_path / "tables"
    d.mkdir()
    shutil.copy(TABLES / "schema.json", d / "schema.json")
    raw = json.loads((TABLES / "za_2026_27.json").read_text())
    change(raw)
    (d / name).write_text(json.dumps(raw))
    return d / name


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda t: t.pop("rebates"), "'rebates' is a required property"),
        (lambda t: t["brackets"][1].update(rate=1.26), "greater than the maximum"),
        (lambda t: t["brackets"][0].update({"from": 10}), "0 was expected"),
        (lambda t: t.update(status="draft"), "is not one of"),
        (lambda t: t.update(endDate="2027-02-30"), "is not a 'date'"),
        (lambda t: t["brackets"][2].update(base=80_998), "does not reconcile"),
        (lambda t: t["brackets"].reverse() or t["brackets"].insert(0, t["brackets"].pop()), "is not above"),
        (lambda t: t["thresholds"].update(under65=95_000), "thresholds.under65"),
        (lambda t: t.update(endDate="2027-03-31"), "endDate should be 2027-02-28"),
        (lambda t: t["livingAnnuity"].update(minDrawdown=0.2), "minDrawdown must be below"),
        (lambda t: t.update(extra=1), "Additional properties"),
    ],
)
def test_a_broken_table_file_is_rejected(tmp_path, change, message):
    with pytest.raises(TableError, match=message):
        load_table_file(_broken(tmp_path, change))


def test_a_table_file_must_be_named_after_its_tax_year(tmp_path):
    with pytest.raises(TableError, match=r"file name should be za_2026_27\.json"):
        load_table_file(_broken(tmp_path, lambda t: None, name="za_2027_28.json"))


# --------------------------------------------------------------------------- projection beyond the latest table (§12.3)


def test_indexed_table_grows_brackets_rebates_thresholds_and_medical_credits_with_cpi():
    f = 1.05**3
    t = indexed_table(T27, 3, 0.05)
    assert indexed_table(T27, 0, 0.05) is T27
    assert (t.taxYear, t.startDate, t.endDate, t.status) == ("2029/30", "2029-03-01", "2030-02-28", "provisional")
    assert [b.from_ for b in t.brackets] == pytest.approx([b.from_ * f for b in T27.brackets])
    assert [b.base for b in t.brackets] == pytest.approx([b.base * f for b in T27.brackets])
    assert [b.rate for b in t.brackets] == [b.rate for b in T27.brackets]
    assert t.rebates.primary == pytest.approx(T27.rebates.primary * f)
    assert t.thresholds.age75plus == pytest.approx(T27.thresholds.age75plus * f)
    assert t.medicalCredits.additional == pytest.approx(T27.medicalCredits.additional * f)
    # Legislated limits stay nominal unless asked for.
    assert t.retirementDeduction.cap == 430_000 and t.retirementLumpSum == T27.retirementLumpSum
    all_limits = indexed_table(T27, 3, 0.05, index_limits=True)
    assert all_limits.retirementDeduction.cap == pytest.approx(430_000 * f)
    assert all_limits.retirementLumpSum[1].from_ == pytest.approx(550_000 * f)
    assert all_limits.estateDuty.abatement == pytest.approx(3_500_000 * f)
    with pytest.raises(ValueError):
        indexed_table(T27, 1.5, 0.05)  # type: ignore[arg-type]


def test_tax_on_an_indexed_table_scales_with_the_cpi_factor():
    f = 1.045**7
    t = indexed_table(T27, 7, 0.045)
    for income in (50_000, 99_000, 250_000, 600_000, 2_500_000):
        for age in (40, 66, 80):
            expected = f * income_tax(income, age, T27)
            assert income_tax(income * f, age, t) == pytest.approx(expected, rel=1e-12, abs=1e-6)
            assert float(income_tax_vec(income * f, age, T27, index=f)) == pytest.approx(expected, rel=1e-12, abs=1e-6)


def test_table_for_year_uses_the_file_when_there_is_one_and_indexes_after_the_latest():
    assert table_for_year(2026, 0.05) is T27
    assert table_for_year(2025, 0.05) is T26
    later = table_for_year(2030, 0.05)
    assert later.taxYear == "2030/31" and later.rebates.primary == pytest.approx(17_820 * 1.05**4)
    with pytest.raises(KeyError):
        table_for_year(2019, 0.05)


# --------------------------------------------------------------------------- scalar helpers


def test_rebates_thresholds_exemptions_and_uif_by_age():
    assert rebates_for(64, T27) == 17_820 and rebates_for(65, T27) == 27_585 and rebates_for(75, T27) == 30_834
    assert [tax_threshold(a, T27) for a in (40, 70, 80)] == [99_000, 153_250, 171_300]
    assert [interest_exemption(a, T27) for a in (64, 65)] == [23_800, 34_500]
    assert uif_monthly(50_000, T27) == pytest.approx(177.12) and uif_monthly(10_000, T27) == 100 and uif_monthly(50_000, T27, False) == 0


def test_marginal_rate_changes_only_above_a_bracket_bound():
    assert marginal_rate(245_100, T27) == 0.18
    assert marginal_rate(245_101, T27) == 0.26
    assert marginal_rate(0, T27) == 0.18
    assert marginal_rate(5_000_000, T27) == 0.45


def test_cgt_on_death_is_the_income_tax_on_the_included_gain():
    assert taxable_capital_gain(1_500_000, T27, death=True) == pytest.approx(424_000)
    assert taxable_capital_gain(1_500_000, T27) == pytest.approx((1_500_000 - 50_000) * 0.4)
    assert capital_gains_tax(1_500_000, 0, 56, T27, death=True) == pytest.approx(income_tax(424_000, 56, T27))


def test_donations_tax_after_the_annual_exemption_and_above_the_cumulative_threshold():
    assert donations_tax(150_000, T27) == 0
    assert donations_tax(250_000, T27) == pytest.approx(20_000)
    assert donations_tax(250_000, T27, exemption_used=150_000) == pytest.approx(50_000)
    assert donations_tax(1_150_000, T27, prior_taxable=29_500_000) == pytest.approx(500_000 * 0.2 + 500_000 * 0.25)
    assert donations_tax(250_000, T26) == pytest.approx(30_000)  # R100,000 exemption in 2025/26


def test_a_two_pot_savings_withdrawal_is_taxed_at_the_marginal_rate():
    assert savings_withdrawal_tax(10_000, 500_000, 40, T27) == pytest.approx(3_100)
    assert savings_withdrawal_tax(10_000, 500_000, 40, T27) == pytest.approx(incremental_tax(500_000, 10_000, 40, T27))
    with pytest.raises(ValueError, match="at least"):
        savings_withdrawal_tax(1_999, 500_000, 40, T27)


# --------------------------------------------------------------------------- numpy versions give the scalar results


def test_income_tax_vec_equals_income_tax_element_by_element():
    rng = np.random.default_rng(7)
    taxable = np.concatenate([rng.uniform(-10_000, 3_000_000, 2_000), [0, 245_100, 245_100.01, 99_000, 1_878_600, 1e9]])
    age = rng.choice([20, 64, 65, 74, 75, 90], size=taxable.size).astype(float)
    for table in (T26, T27):
        vec = income_tax_vec(taxable, age, table)
        assert np.array_equal(vec, [income_tax(x, a, table) for x, a in zip(taxable, age, strict=True)])
    assert float(income_tax_vec(500_000.0, 40.0, T27)) == income_tax(500_000, 40, T27)


def test_person_tax_vec_equals_person_tax_element_by_element():
    rng = np.random.default_rng(11)
    n = 1_000
    cols = {
        "age": rng.choice([30, 64, 65, 75], n).astype(float),
        "gross_monthly": rng.uniform(0, 250_000, n),
        "annual_bonus": rng.choice([0, 50_000, 300_000], n).astype(float),
        "other_taxable_monthly": rng.choice([0, 5_000], n).astype(float),
        "retirement_contributions_annual": rng.uniform(0, 600_000, n),
        "employer_retirement_annual": rng.choice([0, 60_000], n).astype(float),
        "medical_scheme_members": rng.integers(0, 6, n).astype(float),
        "uif_applies": rng.random(n) < 0.8,
    }
    vec = person_tax_vec(
        cols["age"],
        cols["gross_monthly"],
        T27,
        **{k: v for k, v in cols.items() if k not in ("age", "gross_monthly")},
    )
    for i in range(n):
        scalar = person_tax(PersonTaxInput(**{k: v[i].item() for k, v in cols.items()}), T27).model_dump()
        for k, v in scalar.items():
            assert vec[k][i] == pytest.approx(v, rel=1e-15, abs=1e-9), (i, k)


def test_lump_sum_tax_vec_equals_the_scalar_tables():
    amounts = np.array([0, 27_500, 300_000, 550_000, 700_000, 1_000_000, 2_000_000], dtype=float)
    prior = np.array([0, 0, 100_000, 0, 600_000, 0, 1_000_000], dtype=float)
    ret = lump_sum_tax_vec(amounts, T27, prior_lump_sums=prior)
    wd = lump_sum_tax_vec(amounts, T27, kind="withdrawal", prior_lump_sums=prior)
    assert np.array_equal(ret, [retirement_lump_sum_tax(a, T27, p) for a, p in zip(amounts, prior, strict=True)])
    assert np.array_equal(wd, [withdrawal_lump_sum_tax(a, T27, p) for a, p in zip(amounts, prior, strict=True)])


# --------------------------------------------------------------------------- net take-home → gross (§7.6)


def _take_home(gross, age, table, retirement=0.0, members=0, medical=0.0, other=0.0, uif=True) -> float:
    t = person_tax(PersonTaxInput(age, gross, 0, 0, retirement * 12, 0, members, uif), table)
    return gross - t.monthlyPaye - t.monthlyUif - retirement - medical - other


@pytest.mark.parametrize("age", [30, 66, 77])
@pytest.mark.parametrize("deductions", [(0, 0, 0, 0, True), (4_500, 3, 6_200, 350, True), (12_000, 1, 0, 0, False)])
def test_gross_up_monthly_inverts_paye_uif_and_payroll_deductions(age, deductions):
    retirement, members, medical, other, uif = deductions
    for gross in (6_000, 9_000, 17_712, 25_000, 55_000, 120_000, 400_000):
        net = _take_home(gross, age, T27, retirement, members, medical, other, uif)
        if net <= 0:
            continue
        got = gross_up_monthly(
            net,
            age=age,
            table=T27,
            retirement_monthly=retirement,
            medical_members=members,
            medical_payroll_monthly=medical,
            other_payroll_monthly=other,
            uif=uif,
        )
        assert got == pytest.approx(gross, abs=1e-4)


def test_gross_up_recovers_the_salary_behind_the_apps_take_home_pay():
    doc = new_document()
    doc.client.dateOfBirth = "1984-05-20"
    doc.income["client"] = doc.income["client"].model_copy(
        update={"grossMonthly": 82_500, "medicalSchemeMembers": 3, "medicalAidPayrollMonthly": 7_400, "otherPayrollDeductionsMonthly": 250}
    )
    doc.retirementFunds = [new_retirement_fund("client").model_copy(update={"employeeMonthly": 6_187.5, "viaPayroll": True})]
    inc = compute_income(doc, "client", T27, date(2026, 10, 16))
    gross = gross_up_monthly(
        inc.takeHomeMonthly,
        age=inc.taxAge,
        table=T27,
        retirement_monthly=6_187.5,
        medical_members=3,
        medical_payroll_monthly=7_400,
        other_payroll_monthly=250,
    )
    assert gross == pytest.approx(82_500, abs=1e-4)


def test_gross_up_of_nothing_is_nothing():
    assert gross_up_monthly(0, age=40, table=T27) == 0
    assert gross_up_monthly(-500, age=40, table=T27, other_payroll_monthly=100) == 0
