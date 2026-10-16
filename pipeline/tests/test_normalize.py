"""normalize_value (spec §8.3): every intermediate form into every FnaDocument unit it can fill."""

from __future__ import annotations

import numpy as np
import pytest

from advisor_intel import stats
from advisor_intel.lexicon import Lexicon, load_lexicon, normalize_value, quantity_unit, todays_money_rate
from advisor_intel.models.dists import Bernoulli, Categorical, Discrete, LogNormal, Mixture, Point, Quantity, Triangular
from advisor_intel.models.extraction import AmountExact, AmountRelative
from advisor_intel.models.fna import Assumptions

from .lexicon_support import ANCHOR, FakeEmbedder, fact, make_slot, profile

LEX = load_lexicon()
GROSS_PRIOR = LogNormal(median=35_000, p10=12_000, p90=90_000)

GROSS = make_slot("income.{p}.grossMonthly", "rand_monthly", population_prior=GROSS_PRIOR, bounds=(0, 2_000_000))
BONUS = make_slot("income.{p}.annualBonus", "rand_annual", bounds=(0, 20_000_000))
ASSET_VALUE = make_slot("assets[].value", "rand", population_prior=LogNormal(median=1_500_000, p10=600_000, p90=4_000_000), bounds=(0, 2e8))
GOAL_COST = make_slot("goals[].cost", "rand", cardinality="list", entity_type="goal")
TERTIARY_COST = make_slot("dependants[].tertiaryCostAnnual", "rand_annual", entity_type="dependant")
SCHOOL_FEES = make_slot("dependants[].schoolFeesAnnual", "rand_annual", entity_type="dependant")
TARGET = make_slot("retirement.{p}.targetMonthly", "rand_monthly")
RET_AGE = make_slot(
    "retirement.{p}.retirementAge", "age", bounds=(40, 80), population_prior=Triangular(low=60, mode=65, high=70), fna_default=65
)
TERT_START = make_slot("dependants[].tertiaryStartAge", "age", entity_type="dependant")
TERT_YEARS = make_slot("dependants[].tertiaryYears", "years", entity_type="dependant")
SUPPORT_YEARS = make_slot("dependants[].supportYears", "years", entity_type="dependant")
WAITING = make_slot("policies[].waitingPeriodMonths", "months", entity_type="policy")
TERM_YEARS = make_slot("policies[].benefitTermYears", "years", entity_type="policy")
TARGET_YEAR = make_slot("goals[].targetYear", "year", cardinality="list", entity_type="goal")
DOB = make_slot("{p}.dateOfBirth", "date")
DEP_DOB = make_slot("dependants[].dateOfBirth", "date", entity_type="dependant")
RATE = make_slot("liabilities[].interestRate", "decimal", entity_type="liability", bounds=(0.0, 0.4))
GROWTH = make_slot("event[].businessGrowth", "decimal", entity_type="event")
TARGET_PCT = make_slot("retirement.{p}.targetPercent", "decimal")
MEMBERS = make_slot("income.{p}.medicalSchemeMembers", "count")
EMPLOYMENT_PRIOR = {
    "salaried": 0.7,
    "self-employed": 0.15,
    "commission": 0.05,
    "contract": 0.05,
    "retired": 0.03,
    "unemployed": 0.01,
    "student": 0.01,
}
EMPLOYMENT = make_slot(
    "{p}.employmentType",
    "category",
    categories=["salaried", "self-employed", "commission", "contract", "retired", "unemployed", "student"],
    population_prior=Categorical(probs=EMPLOYMENT_PRIOR),
)
RISK = make_slot("riskProfile.answers.objective", "category", categories=["1", "2", "3", "4", "5"])
RISK_CATEGORY = make_slot(
    "riskProfile.category",
    "category",
    categories=["conservative", "moderately-conservative", "moderate", "moderately-aggressive", "aggressive"],
)
ASSET_TYPE = make_slot(
    "assets[].type", "category", entity_type="asset", categories=["primary-residence", "vehicle", "cash", "unit-trust", "crypto", "other"]
)
MARITAL = make_slot("household.maritalStatus", "category", categories=["single", "married", "life-partner", "divorced", "widowed"])
HAS_WILL = make_slot("estate.{p}.hasWill", "category", categories=["yes", "no", "unsure"])
TERTIARY = make_slot("dependants[].tertiary", "boolean", entity_type="dependant", population_prior=Bernoulli(p=0.6))
SMOKER = make_slot("{p}.smoker", "boolean")
EVENT_P = make_slot("event[].probability", "probability", entity_type="event")
OCCUPATION = make_slot("{p}.occupation", "text")

PROFILE = profile(
    fact("client.dateOfBirth", Quantity(dist=Point(value="1974-05-12"), unit="date")),
    fact("spouse.dateOfBirth", Quantity(dist=stats.uniform_discrete(1976.0, 1977.0), unit="date")),
    fact(
        "dependants[].dateOfBirth#person.daughter_1",
        Quantity(dist=Point(value="2012-03-01"), unit="date"),
        slot_id="dependants[].dateOfBirth",
    ),
    fact("expenses.total", Quantity(dist=Point(value=30_000), unit="ZAR", period="monthly")),
    fact("expenses.annualTotal", Quantity(dist=Point(value=360_000), unit="ZAR", period="annual")),
    fact(
        "income.client.grossMonthly",
        Quantity(dist=Triangular(low=40_000, mode=45_000, high=50_000), unit="ZAR", period="monthly", basis="gross"),
    ),
    fact("income.spouse.grossMonthly", None, prior=Quantity(dist=Point(value=20_000), unit="ZAR", period="monthly", basis="gross")),
    fact("retirement.client.retirementAge", Quantity(dist=Point(value=60), unit="age")),
    fact(
        "policies[].lifeAssured#policy.disc_1",
        Quantity(dist=Categorical(probs={"spouse": 1.0}), unit="none"),
        slot_id="policies[].lifeAssured",
    ),
)


def norm(form: dict, slot, **kw):
    kw.setdefault("anchor_date", ANCHOR)
    return normalize_value(form, slot, kw.pop("profile", PROFILE), kw.pop("lex", LEX), **kw)


def amount(t: str, **kw) -> dict:
    base = {"type": t, "currency": "ZAR", "period": "monthly", "basis": "gross"}
    base.update(kw)
    return base


def summary(n):
    return stats.summary(n.quantity.dist)


# --------------------------------------------------------------------------- units


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("rand", ("ZAR", "once")),
        ("rand_monthly", ("ZAR", "monthly")),
        ("rand_annual", ("ZAR", "annual")),
        ("years", ("years", "none")),
        ("months", ("months", "none")),
        ("age", ("age", "none")),
        ("year", ("date", "none")),
        ("decimal", ("pct", "none")),
        ("date", ("date", "none")),
        ("count", ("count", "none")),
        ("category", ("none", "none")),
        ("boolean", ("none", "none")),
        ("text", ("none", "none")),
        ("probability", ("none", "none")),
    ],
)
def test_every_fna_unit_has_a_quantity_unit(unit, expected):
    assert quantity_unit(make_slot("x.y", unit)) == expected


# --------------------------------------------------------------------------- amounts


@pytest.mark.parametrize(
    ("slot", "period", "value", "expected", "flag"),
    [
        (GROSS, "monthly", 42_000, 42_000, None),
        (GROSS, "annual", 600_000, 50_000, None),
        (GROSS, "none", 42_000, 42_000, None),
        (GROSS, "once", 42_000, 42_000, "period_mismatch"),
        (BONUS, "monthly", 7_000, 84_000, None),  # §8.3: 'R7,000 a month' for an annual field ×12
        (BONUS, "annual", 85_000, 85_000, None),
        (BONUS, "once", 85_000, 85_000, None),
        (ASSET_VALUE, "once", 2_500_000, 2_500_000, None),
        (ASSET_VALUE, "monthly", 5_000, 5_000, "period_mismatch"),
    ],
)
def test_amount_exact_converts_period_to_the_field(slot, period, value, expected, flag):
    n = norm(amount("AMOUNT_EXACT", value=value, period=period), slot)
    assert n.quantity.dist == Point(value=expected)
    assert n.quantity.period == {"rand": "once", "rand_monthly": "monthly", "rand_annual": "annual"}[slot.fna_unit]
    assert (flag in n.flags) if flag else not n.flags
    assert n.normalization["form_type"] == "AMOUNT_EXACT" and n.normalization["lexicon_version"] == "1.3"


def test_amount_approx_uses_the_lexicon_then_converts():
    n = norm(amount("AMOUNT_APPROX", value=600_000, approx_word="about", period="annual"), GROSS)
    assert n.quantity.dist == Triangular(low=45_000, mode=50_000, high=55_000)
    assert n.normalization["lexicon"] == [
        {"section": "approx_words", "phrase": "about", "key": "about", "match": "exact", "base_value": 600_000.0}
    ]
    assert "annual → monthly: ÷12" in n.normalization["conversions"]
    assert n.normalization["value_factor"] == pytest.approx(1 / 12)


def test_amount_range_is_widened_five_percent_each_side():
    n = norm(amount("AMOUNT_RANGE", low=30_000, high=40_000), GROSS)
    assert n.quantity.dist == Triangular(low=28_500, mode=35_000, high=42_000)
    swapped = norm(amount("AMOUNT_RANGE", low=40_000, high=30_000), GROSS)
    assert swapped.quantity.dist == n.quantity.dist


def test_amount_bound_truncates_the_population_prior():
    at_least = norm(amount("AMOUNT_BOUND", bound=50_000, direction="at_least"), GROSS)
    assert stats.quantile(at_least.quantity.dist, 0.0) >= 50_000 - 1e-6
    assert stats.median(at_least.quantity.dist) > 50_000
    at_most = norm(amount("AMOUNT_BOUND", bound=20_000, direction="at_most"), GROSS)
    assert stats.quantile(at_most.quantity.dist, 1.0) <= 20_000 + 1e-6
    assert stats.median(at_most.quantity.dist) < 20_000
    yearly = norm(amount("AMOUNT_BOUND", bound=600_000, direction="at_least", period="annual"), GROSS)
    assert stats.quantile(yearly.quantity.dist, 0.0) >= 50_000 - 1e-6  # the bound is converted before truncating


def test_amount_bound_without_a_prior_spreads_beyond_the_bound():
    n = norm(amount("AMOUNT_BOUND", bound=1000, direction="at_least", period="annual"), BONUS)
    assert "no_prior" in n.flags
    assert stats.quantile(n.quantity.dist, 0.0) == pytest.approx(1000) and stats.median(n.quantity.dist) > 1000


def test_amount_vague_is_count_times_scale():
    n = norm(amount("AMOUNT_VAGUE", vague_word="a few hundred K", period="annual"), GROSS)
    d = n.quantity.dist
    assert isinstance(d, Triangular) and (d.low, d.mode, d.high) == pytest.approx((200_000 / 12, 300_000 / 12, 500_000 / 12))
    assert n.normalization["lexicon"][0]["key"] == "a few hundred" and n.normalization["lexicon"][0]["multiplier"] == 1000


def test_unknown_vague_word_falls_back_to_the_population_prior():
    lex = Lexicon.from_text(LEX.source_text)
    n = norm(amount("AMOUNT_VAGUE", vague_word="a decent whack"), GROSS, lex=lex)
    assert n.quantity.dist == GROSS_PRIOR
    assert {"lexicon_miss", "population_prior"} <= set(n.flags)
    assert n.normalization["lexicon"][0]["match"] == "miss"
    assert [m.phrase for m in lex.miss_log.entries()] == ["a decent whack"]  # logged for the weekly review


def test_near_miss_supplies_a_provisional_prior():
    lex = Lexicon.from_text(LEX.source_text, embedder=FakeEmbedder({"a fair bit less": {"a bit less": 0.9}}))
    n = norm({"type": "AMOUNT_RELATIVE", "reference_slot": "expenses.total", "relation_word": "a fair bit less"}, TARGET, lex=lex)
    assert "lexicon_near_miss" in n.flags and "lexicon_miss" not in n.flags
    assert stats.median(n.quantity.dist) == pytest.approx(30_000 * stats.median(LEX.relative("a bit less").dist), rel=0.01)


def test_amount_relative_is_reference_times_multiplier():
    n = norm({"type": "AMOUNT_RELATIVE", "reference_slot": "expenses.total", "relation_word": "a bit less"}, TARGET)
    assert n.quantity.dist == Triangular(low=22_500, mode=26_400, high=29_100)  # a point reference keeps the triangle
    assert n.normalization["reference"] == {"slot": "expenses.total", "fact_id": "expenses.total", "source": "value", "median": 30_000.0}


def test_amount_relative_converts_the_reference_period():
    n = norm({"type": "AMOUNT_RELATIVE", "reference_slot": "expenses.annualTotal", "relation_word": "about the same"}, TARGET)
    assert n.quantity.dist == Triangular(low=27_000, mode=30_000, high=33_000)


def test_amount_relative_samples_a_product_for_distributions():
    n = norm(
        {"type": "AMOUNT_RELATIVE", "reference_slot": "income.{p}.grossMonthly", "relation_word": "a bit less"},
        TARGET,
        fact_id="retirement.client.targetMonthly",
    )
    assert isinstance(n.quantity.dist, Mixture) and len(n.quantity.dist.components) == stats.N_QUANTILE_POINTS
    assert stats.median(n.quantity.dist) == pytest.approx(45_000 * stats.median(LEX.relative("a bit less").dist), rel=0.03)


def test_amount_relative_uses_the_prior_fna_value_and_falls_back_when_missing():
    spouse = norm(
        {"type": "AMOUNT_RELATIVE", "reference_slot": "income.{p}.grossMonthly", "relation_word": "about the same"},
        TARGET,
        fact_id="retirement.spouse.targetMonthly",
    )
    assert spouse.normalization["reference"]["source"] == "prior_value"
    assert stats.median(spouse.quantity.dist) == pytest.approx(20_000)
    missing = norm({"type": "AMOUNT_RELATIVE", "reference_slot": "expense.household_core", "relation_word": "a bit less"}, GROSS)
    assert {"reference_missing", "population_prior"} <= set(missing.flags)
    assert missing.quantity.dist == GROSS_PRIOR


def test_amounts_for_counts_are_plain_numbers():
    assert norm(amount("AMOUNT_EXACT", value=4), MEMBERS).quantity.dist == Point(value=4)
    assert norm(amount("AMOUNT_APPROX", value=4, approx_word="about"), MEMBERS).quantity.dist == Triangular(low=3.6, mode=4, high=4.4)
    assert norm(amount("AMOUNT_VAGUE", vague_word="a couple"), MEMBERS).quantity.dist == Discrete(support=[2, 3], weights=[0.75, 0.25])
    assert norm(amount("AMOUNT_EXACT", value=4), MEMBERS).quantity.unit == "count"


def test_amount_for_an_age_field_is_kept_but_flagged():
    n = norm(amount("AMOUNT_EXACT", value=62), RET_AGE)
    assert n.quantity.dist == Point(value=62) and "form_slot_mismatch" in n.flags


# --------------------------------------------------------------------------- SA rules: today's money, currency, basis


def test_future_goal_cost_is_deflated_with_cpi():
    n = norm(amount("AMOUNT_APPROX", value=400_000, approx_word="about", period="once", basis="na"), GOAL_COST, priced_year=2030)
    f = 1.045**-4
    assert n.quantity.dist == Triangular(low=360_000 * f, mode=400_000 * f, high=440_000 * f)
    assert "deflated" in n.flags and n.quantity.real_or_nominal == "real"
    d = n.normalization["deflation"]
    assert (d["rate_name"], d["rate"], d["years"]) == ("cpi", 0.045, 4.0) and d["factor"] == pytest.approx(f)


def test_education_costs_deflate_with_education_inflation():
    n = norm(amount("AMOUNT_EXACT", value=7_000, basis="na"), TERTIARY_COST, priced_year=2031)
    assert n.quantity.dist.value == pytest.approx(84_000 / 1.065**5)
    assert n.normalization["deflation"]["rate_name"] == "educationInflation"
    custom = norm(
        amount("AMOUNT_EXACT", value=100_000, period="annual", basis="na"),
        SCHOOL_FEES,
        priced_year=2028,
        assumptions=Assumptions(educationInflation=0.08),
    )
    assert custom.quantity.dist.value == pytest.approx(100_000 / 1.08**2)
    assert todays_money_rate(TARGET) == "cpi" and todays_money_rate(GROSS) is None


def test_today_or_past_prices_and_other_fields_are_not_deflated():
    assert "deflated" not in norm(amount("AMOUNT_EXACT", value=1, period="once", basis="na"), GOAL_COST, priced_year=2026).flags
    other = norm(amount("AMOUNT_EXACT", value=900_000, period="once", basis="na"), ASSET_VALUE, priced_year=2030)
    assert "deflated" not in other.flags and other.quantity.dist == Point(value=900_000)
    assert any("not held in today's money" in c for c in other.normalization["conversions"])


def test_priced_year_can_ride_on_a_raw_dict_form():
    form = amount("AMOUNT_EXACT", value=400_000, period="once", basis="na") | {"priced_year": 2030}
    n = norm(form, GOAL_COST)
    assert "deflated" in n.flags and n.normalization["priced_year"] == 2030


def test_foreign_amounts_stay_in_their_currency():
    n = norm(amount("AMOUNT_EXACT", value=30_000, currency="GBP", period="annual", basis="na"), TERTIARY_COST)
    assert n.quantity.unit == "GBP" and n.quantity.dist == Point(value=30_000)
    assert "foreign_currency" in n.flags
    future = norm(amount("AMOUNT_EXACT", value=30_000, currency="GBP", period="annual", basis="na"), TERTIARY_COST, priced_year=2031)
    assert future.quantity.dist == Point(value=30_000) and future.quantity.real_or_nominal == "nominal"
    assert "future_dated_foreign" in future.flags and future.normalization["deflation"]["applied"] is False


def test_income_basis_unknown_is_kept():
    unknown = norm(amount("AMOUNT_EXACT", value=42_000, basis="na"), GROSS)
    assert unknown.quantity.basis == "unknown"
    assert norm(amount("AMOUNT_EXACT", value=42_000, basis="net"), GROSS).quantity.basis == "net"
    assert norm(amount("AMOUNT_EXACT", value=42_000, basis="unknown"), GROSS).quantity.basis == "unknown"
    assert norm(amount("AMOUNT_EXACT", value=4, basis="na", period="once"), ASSET_VALUE).quantity.basis == "na"
    lex = Lexicon.from_text(LEX.source_text)
    fallback = norm(amount("AMOUNT_VAGUE", vague_word="a decent whack", basis="net"), GROSS, lex=lex)
    assert fallback.quantity.basis == "net"  # the spoken basis survives a prior fallback


# --------------------------------------------------------------------------- dates, durations, ages


def test_date_abs_for_each_unit():
    assert norm({"type": "DATE_ABS", "year": 1985, "month": 4, "day": 12}, DOB).quantity.dist == Point(value="1985-04-12")
    month = norm({"type": "DATE_ABS", "year": 2029, "month": 3, "day": 0}, DOB).quantity.dist
    assert isinstance(month, Discrete) and stats.iso_to_year("2029-03-01") <= stats.quantile(month, 0) <= stats.quantile(month, 1) < 2029.25
    year_only = norm({"type": "DATE_ABS", "year": 1985, "month": 0, "day": 0}, DOB).quantity.dist
    assert len(year_only.support) == 12 and stats.mean(year_only) == pytest.approx(1985.5)
    bad_day = norm({"type": "DATE_ABS", "year": 2029, "month": 2, "day": 30}, DOB).quantity.dist
    assert isinstance(bad_day, Discrete)  # an impossible day is read as month only
    assert norm({"type": "DATE_ABS", "year": 2029, "month": 3, "day": 0}, TARGET_YEAR).quantity.dist == Point(value=2029)


def test_date_to_age_and_years():
    exact = norm({"type": "DATE_ABS", "year": 2035, "month": 3, "day": 1}, RET_AGE, fact_id="retirement.client.retirementAge")
    assert exact.quantity.dist == Point(value=60)  # completed years on 1 March 2035 for a 12 May 1974 birthday
    year_only = norm({"type": "DATE_ABS", "year": 2035, "month": 0, "day": 0}, RET_AGE, fact_id="retirement.client.retirementAge")
    assert 60.6 < stats.median(year_only.quantity.dist) < 61.6
    years = norm({"type": "DATE_ABS", "year": 2030, "month": 10, "day": 16}, TERM_YEARS)
    assert years.quantity.dist.value == pytest.approx(4.0, abs=0.01)
    no_dob = norm({"type": "DATE_ABS", "year": 2035, "month": 0, "day": 0}, RET_AGE, profile=profile())
    assert {"reference_missing", "population_prior"} <= set(no_dob.flags)


def duration(value=0.0, vague="", unit="years", anchor="call"):
    return {
        "type": "DURATION_FROM_ANCHOR",
        "form": "vague" if vague else "value",
        "value": value,
        "vague_word": vague,
        "unit": unit,
        "anchor": anchor,
    }


def test_duration_to_years_months_and_calendar_year():
    assert norm(duration(4), TERT_YEARS).quantity.dist == Point(value=4)
    assert norm(duration(18, unit="months"), TERT_YEARS).quantity.dist == Point(value=1.5)
    assert norm(duration(3, unit="months"), WAITING).quantity.dist == Point(value=3)
    assert norm(duration(1, unit="years"), WAITING).quantity.dist == Point(value=12)
    assert norm(duration(3), TARGET_YEAR).quantity.dist == Point(value=2029)
    few = norm(duration(vague="in a few years"), TARGET_YEAR)
    assert few.quantity.dist == Triangular(low=2028, mode=2029, high=2031)
    assert few.normalization["anchor_offset"] == 2026


def test_duration_to_a_date_and_backwards():
    n = norm(duration(2), DOB)
    assert n.quantity.dist.value == pytest.approx(stats.iso_to_year("2026-10-16") + 2)
    ago = norm(duration(vague="five years ago"), DOB)
    assert ago.quantity.dist.value == pytest.approx(stats.iso_to_year("2026-10-16") - 5)


def test_duration_to_age_uses_the_date_of_birth():
    # §4.3: retirement age from 'in about 8 years' + date of birth; the client is 52 on the call date.
    n = norm(duration(8, anchor="client.dateOfBirth"), RET_AGE, fact_id="retirement.client.retirementAge")
    assert n.quantity.dist == Point(value=60)
    assert n.normalization["date_of_birth"]["fact_id"] == "client.dateOfBirth"
    call_anchor = norm(duration(vague="a few"), RET_AGE, fact_id="retirement.client.retirementAge")
    assert call_anchor.quantity.dist == Triangular(low=54, mode=55, high=57)
    spouse = norm(duration(10), RET_AGE, fact_id="retirement.spouse.retirementAge")  # an uncertain date of birth gives a distribution
    assert 59.7 < stats.median(spouse.quantity.dist) < 60.4


def test_duration_after_an_age_reference():
    planning = make_slot("retirement.{p}.planningAge", "age")
    n = norm(duration(25, anchor="retirement.{p}.retirementAge"), planning, fact_id="retirement.client.planningAge")
    assert n.quantity.dist == Point(value=85)


def test_duration_to_age_without_a_date_of_birth_falls_back():
    n = norm(duration(8), RET_AGE, profile=profile(), fact_id="retirement.client.retirementAge")
    assert n.quantity.dist == RET_AGE.population_prior
    assert {"reference_missing", "population_prior"} <= set(n.flags)


def age(value=0.0, low=0.0, high=0.0, vague=""):
    form = "vague" if vague else ("range" if high else "value")
    return {"type": "AGE", "form": form, "value": value, "low": low, "high": high, "vague_word": vague}


def test_age_forms_for_an_age_field():
    assert norm(age(62), RET_AGE).quantity.dist == Point(value=62)
    assert norm(age(low=60, high=65), RET_AGE).quantity.dist == Triangular(low=59.5, mode=62.5, high=65.5)
    assert norm(age(vague="early sixties"), RET_AGE).quantity.dist == Triangular(low=60, mode=61.5, high=63.5)
    assert norm(age(vague="after matric"), TERT_START).quantity.dist == Triangular(low=17, mode=18, high=19)


def test_age_to_date_of_birth_follows_the_derivation_rule():
    # §7.6: 1 July of (anchor year − age), uniform ±6 months.
    n = norm(age(14), DEP_DOB)
    assert isinstance(n.quantity.dist, Discrete) and len(n.quantity.dist.support) == 13
    assert stats.quantile(n.quantity.dist, 0) == pytest.approx(2012.0) and stats.quantile(n.quantity.dist, 1) == pytest.approx(2013.0)
    banded = norm(age(vague="early sixties"), DOB)
    assert stats.median(banded.quantity.dist) == pytest.approx(2026.5 - 61.63, abs=0.15)


def test_age_to_year_and_to_years_from_now():
    year = norm(age(60), TARGET_YEAR, fact_id="retirement.client.x")  # the client turns 60 in 2034
    assert year.quantity.dist == Point(value=2034)
    spouse_year = norm(age(60), TARGET_YEAR, fact_id="retirement.spouse.x")  # born 1976.0–1977.0: whole calendar years
    draws = stats.sample(spouse_year.quantity.dist, np.random.default_rng(0), 500)
    assert set(draws) <= {2036.0, 2037.0} and stats.median(spouse_year.quantity.dist) == 2036
    support = norm(age(23), SUPPORT_YEARS, fact_id="dependants[].supportYears#person.daughter_1")
    assert support.quantity.dist == Point(value=9)  # she is 14 on the call date
    term = norm(age(65), make_slot("policies[].benefitTermMonths", "months", entity_type="policy"), fact_id="policies[].x#policy.disc_1")
    assert 160 < stats.median(term.quantity.dist) < 180  # the life assured (spouse) is about 50
    missing = norm(age(65), TERM_YEARS, fact_id="policies[].benefitTermYears#policy.unknown")
    assert "reference_missing" in missing.flags


def test_age_phrase_not_in_the_lexicon():
    lex = Lexicon.from_text(LEX.source_text)
    n = norm(age(vague="when the kids are sorted"), RET_AGE, lex=lex)
    assert n.quantity.dist == RET_AGE.population_prior and "lexicon_miss" in n.flags


# --------------------------------------------------------------------------- rates and probabilities


def rate(value=0.0, vague="", rn="na"):
    return {"type": "RATE", "form": "vague" if vague else "value", "value": value, "vague_word": vague, "real_or_nominal": rn}


def test_rates_become_decimals():
    n = norm(rate(11.5, rn="nominal"), RATE)
    assert n.quantity.dist == Point(value=0.115) and n.quantity.unit == "pct" and n.quantity.real_or_nominal == "nominal"
    assert "percent → decimal: ÷100" in n.normalization["conversions"]
    assert norm(rate(0.115), RATE).quantity.dist == Point(value=0.115)
    assert norm(rate(70), TARGET_PCT).quantity.dist == Point(value=0.7)
    assert norm(rate(1.0), TARGET_PCT).quantity.dist == Point(value=1.0)


def test_vague_rates_are_real_rates_from_the_lexicon():
    n = norm(rate(vague="growing a bit", rn="nominal"), GROWTH)
    assert n.quantity.dist.kind == "normal" and n.quantity.dist.mean == 0.03 and n.quantity.real_or_nominal == "real"
    assert "lexicon rate words are real; the form said nominal" in n.normalization["conversions"]


def test_rate_for_a_probability_field():
    assert norm(rate(0.3), EVENT_P).quantity.dist == Bernoulli(p=0.3)
    assert norm(rate(30), EVENT_P).quantity.dist == Bernoulli(p=0.3)
    assert norm(rate(vague="fifty-fifty"), EVENT_P).quantity.dist == Bernoulli(p=0.5)


def test_probability_event_is_bernoulli_of_the_mean():
    n = norm({"type": "PROBABILITY_EVENT", "event": "study offshore", "likelihood_word": "might"}, TERTIARY)
    assert n.quantity.dist == Bernoulli(p=0.4)
    assert n.normalization["likelihood"] == {"mean": 0.4, "low": 0.25, "high": 0.55} and n.normalization["event"] == "study offshore"
    assert norm({"type": "PROBABILITY_EVENT", "event": "x", "likelihood_word": "probably"}, EVENT_P).quantity.dist == Bernoulli(p=0.7)


def test_probability_event_fallbacks():
    lex = Lexicon.from_text(LEX.source_text)
    unknown = norm({"type": "PROBABILITY_EVENT", "event": "x", "likelihood_word": "reckon so maybe-ish"}, TERTIARY, lex=lex)
    assert unknown.quantity.dist == Bernoulli(p=0.6) and "lexicon_miss" in unknown.flags  # the slot's prior
    wrong = norm({"type": "PROBABILITY_EVENT", "event": "x", "likelihood_word": "might"}, GROSS)
    assert "form_slot_mismatch" in wrong.flags and wrong.quantity.dist == GROSS_PRIOR


# --------------------------------------------------------------------------- categories, booleans, text


def category(label, word=""):
    return {"type": "CATEGORY", "label": label, "certainty_word": word}


def test_ordered_scale_named_level_gets_0_6_and_neighbours_share_0_4():
    assert norm(category("2", "fairly"), RISK).quantity.dist == Categorical(probs={"2": 0.6, "1": 0.2, "3": 0.2})
    assert norm(category("1", "fairly"), RISK).quantity.dist == Categorical(probs={"1": 0.6, "2": 0.4})
    conservative = norm(category("conservative", "fairly"), RISK_CATEGORY).quantity.dist
    assert conservative == Categorical(probs={"conservative": 0.6, "moderately-conservative": 0.4})
    assert norm(category("3"), RISK).quantity.dist == Categorical(probs={"3": 1.0})


def test_unordered_categories_spread_the_rest_by_the_prior():
    n = norm(category("own business", "kind of"), EMPLOYMENT)
    probs = n.quantity.dist.probs
    assert probs["self-employed"] == pytest.approx(0.6)
    assert probs["salaried"] == pytest.approx(0.4 * 0.7 / 0.85)
    assert n.normalization["category"] == "self-employed"
    married = norm(category("married", "I think"), MARITAL).quantity.dist.probs  # no prior: the rest is uniform
    assert married["married"] == 0.75 and married["single"] == pytest.approx(0.25 / 4)


def test_labels_match_by_synonym_and_typo():
    assert norm(category("Cautious"), RISK_CATEGORY).quantity.dist == Categorical(probs={"moderately-conservative": 1.0})
    assert norm(category("bakkie"), ASSET_TYPE).quantity.dist == Categorical(probs={"vehicle": 1.0})
    assert norm(category("Unit Trust"), ASSET_TYPE).quantity.dist == Categorical(probs={"unit-trust": 1.0})
    assert norm(category("primary residance"), ASSET_TYPE).quantity.dist == Categorical(probs={"primary-residence": 1.0})


def test_negated_labels_are_not_matched_by_containment():
    n = norm(category("not conservative at all"), RISK_CATEGORY)
    assert "label_unmatched" in n.flags and n.normalization["spoken_label"] == "not conservative at all"
    assert norm(category("a moderately aggressive investor"), RISK_CATEGORY).quantity.dist == Categorical(
        probs={"moderately-aggressive": 1.0}
    )


def test_labels_outside_the_union_become_other_with_the_label_kept():
    n = norm(category("Kruger rands"), ASSET_TYPE)
    assert n.quantity.dist == Categorical(probs={"other": 1.0})
    assert "label_mapped_to_other" in n.flags and n.normalization["spoken_label"] == "Kruger rands"
    no_other = norm(category("astronaut"), EMPLOYMENT)
    assert "label_unmatched" in no_other.flags and no_other.quantity.dist == EMPLOYMENT.population_prior
    uniform = norm(category("eloped"), MARITAL)
    assert uniform.quantity.dist == Categorical(probs={c: 0.2 for c in MARITAL.categories})


def test_boolean_is_0_97_without_a_certainty_word():
    assert norm({"type": "BOOLEAN", "value": True, "certainty_word": ""}, SMOKER).quantity.dist == Bernoulli(p=0.97)
    assert norm({"type": "BOOLEAN", "value": False, "certainty_word": ""}, SMOKER).quantity.dist.p == pytest.approx(0.03)
    assert norm({"type": "BOOLEAN", "value": True, "certainty_word": "I think"}, SMOKER).quantity.dist == Bernoulli(p=0.75)
    assert norm({"type": "BOOLEAN", "value": True, "certainty_word": "probably"}, SMOKER).quantity.dist == Bernoulli(p=0.7)
    assert norm({"type": "BOOLEAN", "value": False, "certainty_word": "likely"}, SMOKER).quantity.dist.p == pytest.approx(0.3)


def test_boolean_for_a_yes_no_unsure_field():
    # §8.1 example: 'we do have wills, I think' → hasWill (yes / no / unsure).
    n = norm({"type": "BOOLEAN", "value": True, "certainty_word": "I think"}, HAS_WILL)
    assert n.quantity.dist == Categorical(probs={"yes": 0.75, "unsure": 0.25})
    assert norm({"type": "BOOLEAN", "value": False, "certainty_word": ""}, HAS_WILL).quantity.dist.probs == pytest.approx(
        {"no": 0.97, "unsure": 0.03}
    )


def test_category_and_text_cross_over():
    assert norm(category("yes", "pretty sure"), SMOKER).quantity.dist == Bernoulli(p=0.85)
    assert norm({"type": "TEXT", "text": "Civil engineer"}, OCCUPATION).quantity.dist == Point(value="Civil engineer")
    assert norm({"type": "TEXT", "text": "retired"}, EMPLOYMENT).quantity.dist == Categorical(probs={"retired": 1.0})
    assert norm(category("pilot"), OCCUPATION).quantity.dist == Point(value="pilot")


# --------------------------------------------------------------------------- records and fallbacks


def test_form_not_allowed_and_mismatches_are_flagged():
    strict = make_slot(
        "retirement.{p}.retirementAge",
        "age",
        allowed_forms=["AGE", "DURATION_FROM_ANCHOR"],
        population_prior=Triangular(low=60, mode=65, high=70),
    )
    n = norm(amount("AMOUNT_EXACT", value=62), strict)
    assert {"form_not_allowed", "form_slot_mismatch"} <= set(n.flags)
    text_on_money = norm({"type": "TEXT", "text": "lots"}, GROSS)
    assert "form_slot_mismatch" in text_on_money.flags and text_on_money.quantity.dist == GROSS_PRIOR


def test_fna_default_then_nothing_when_no_prior():
    n = norm(
        amount("AMOUNT_VAGUE", vague_word="zzz"),
        make_slot("retirement.{p}.planningAge", "age", fna_default=92),
        lex=Lexicon.from_text(LEX.source_text),
    )
    assert n.quantity.dist == Point(value=92) and "fna_default" in n.flags
    none = norm(amount("AMOUNT_VAGUE", vague_word="zzz"), BONUS, lex=Lexicon.from_text(LEX.source_text))
    assert none.quantity is None and not none.ok and "no_prior" in none.flags


def test_models_and_dicts_give_the_same_result():
    form = AmountExact(value=42_000, currency="ZAR", period="monthly", basis="gross")
    assert norm(form, GROSS) == norm(form.model_dump(), GROSS)
    assert isinstance(norm(AmountRelative(reference_slot="expenses.total", relation_word="half"), TARGET).quantity.dist, Triangular)


def test_normalization_record_is_complete():
    n = norm(amount("AMOUNT_APPROX", value=600_000, approx_word="roughly", period="annual"), GROSS)
    rec = n.normalization
    assert rec["form"]["approx_word"] == "roughly" and rec["form_type"] == "AMOUNT_APPROX"
    assert rec["lexicon_version"] == "1.3" and rec["slot_id"] == "income.{p}.grossMonthly" and rec["fna_unit"] == "rand_monthly"
    assert rec["lexicon"][0]["key"] == "roughly" and rec["conversions"]


def test_anchor_date_is_required_only_when_time_matters():
    assert norm(amount("AMOUNT_EXACT", value=1), GROSS, profile=None, anchor_date=None).ok
    with pytest.raises(ValueError, match="anchor_date"):
        norm(duration(3), TARGET_YEAR, profile=None, anchor_date=None)
