"""Constraint rules over the profile (§9.1c) and the scale-resolution penalty (§7.4 signal 4)."""

from __future__ import annotations

import pytest

from advisor_intel.extract.reducer import check_profile
from advisor_intel.models.dists import Point, Triangular
from advisor_intel.schema.constraints import RULE_INDEX, RULES, annotate_constraints, check_constraints, soft_penalty

from .verify_factories import ProfileBuilder, money, point, qty


def _ids(violations, rule_id):
    return [v for v in violations if v.rule_id == rule_id]


def test_registry_declares_every_kind_with_severity_and_weight():
    kinds = {r.kind for r in RULES}
    assert kinds == {"range", "ordering", "budget", "coherence", "cross_slot"}
    for r in RULES:
        assert r.severity in ("hard", "soft")
        assert 0 < r.weight <= 1
        assert r.severity == "soft" or r.weight == 1.0
    for rid in ("retirement_age_vs_current_age", "education_start_after_now", "core_expenses_vs_income",
                "self_employed_without_business", "fund_balance_after_contributions"):
        assert rid in RULE_INDEX


def test_retirement_age_below_current_age_is_hard_and_blames_the_call_fact():
    b = ProfileBuilder()
    dob = b.seed("{p}.dateOfBirth", "client", point("1974-07-01", "date"))
    ra = b.add("retirement.{p}.retirementAge", "client", point(45, "age"))
    p = b.build()
    v = _ids(check_constraints(p), "retirement_age_vs_current_age")
    assert len(v) == 1 and v[0].severity == "hard"
    assert set(v[0].fact_ids) == {ra, dob}
    assert v[0].blamed == [ra]  # the seeded date of birth is not blamed
    annotated = annotate_constraints(p, check_constraints(p))
    assert annotated.facts[ra].verification["constraint"] == "fail"
    assert annotated.facts[dob].verification["constraint"] == "pass"
    assert check_profile(annotated) == []  # annotations never touch values


def test_retired_people_are_exempt_and_valid_ages_pass():
    b = ProfileBuilder()
    b.seed("{p}.dateOfBirth", "client", point("1950-01-01", "date"))
    b.add("retirement.{p}.retirementAge", "client", point(65, "age"))
    b.add("{p}.employmentType", "client", point("retired"))
    assert _ids(check_constraints(b.build()), "retirement_age_vs_current_age") == []
    b2 = ProfileBuilder()
    b2.seed("{p}.dateOfBirth", "client", point("1980-01-01", "date"))
    b2.add("retirement.{p}.retirementAge", "client", qty(Triangular(low=60, mode=62, high=65), "age"))
    assert _ids(check_constraints(b2.build()), "retirement_age_vs_current_age") == []


def test_education_start_ordering_for_children_under_18():
    b = ProfileBuilder().entity("person.neo", "person", "Neo", relation="child")
    b.add("dependants[].relationship", "person.neo", point("child"))
    b.add("dependants[].dateOfBirth", "person.neo", point("2012-03-01", "date"))
    start = b.add("dependants[].tertiaryStartAge", "person.neo", point(12, "age"))
    v = _ids(check_constraints(b.build()), "education_start_after_now")
    assert len(v) == 1 and v[0].severity == "hard" and start in v[0].blamed
    ok = ProfileBuilder().entity("person.neo", "person", "Neo")
    ok.add("dependants[].dateOfBirth", "person.neo", point("2012-03-01", "date"))
    ok.add("dependants[].tertiaryStartAge", "person.neo", point(18, "age"))
    assert _ids(check_constraints(ok.build()), "education_start_after_now") == []
    adult = ProfileBuilder().entity("person.kid", "person", "Kid")
    adult.add("dependants[].dateOfBirth", "person.kid", point("2005-03-01", "date"))  # 21: already past 18
    adult.add("dependants[].tertiaryStartAge", "person.kid", point(19, "age"))
    assert _ids(check_constraints(adult.build()), "education_start_after_now") == []


def test_budget_uses_net_income_when_known_else_gross():
    b = ProfileBuilder()
    inc = b.add("income.{p}.grossMonthly", "client", money(40000))
    g = b.add("expenses.items[]", "groceries", money(30000))
    b.add("expenses.items[]", "rent", money(35000))
    b.add("expenses.items[]", "holidays", money(90000))  # not essential: not a core expense
    p = b.build()
    core = _ids(check_constraints(p), "core_expenses_vs_income")
    assert len(core) == 1 and core[0].severity == "soft"
    assert g in core[0].fact_ids and inc in core[0].fact_ids
    assert "gross" in core[0].message
    # with a higher net figure from the FNA engine nothing breaks; with a lower one it does
    assert _ids(check_constraints(p, net_income_monthly=50000), "core_expenses_vs_income") == []
    assert "net" in _ids(check_constraints(p, net_income_monthly=30000), "core_expenses_vs_income")[0].message
    line = _ids(check_constraints(p), "expense_line_vs_income")
    assert {v.fact_ids[0] for v in line} == {"expenses.items[]#holidays"}


def test_self_employed_needs_a_business_entity():
    b = ProfileBuilder()
    emp = b.add("{p}.employmentType", "client", point("self-employed"))
    v = _ids(check_constraints(b.build()), "self_employed_without_business")
    assert [x.fact_ids for x in v] == [[emp]] and v[0].severity == "soft"
    b2 = ProfileBuilder().entity("business.plumbing", "business", "Mokoena Plumbing")
    b2.add("{p}.employmentType", "client", point("self-employed"))
    assert _ids(check_constraints(b2.build()), "self_employed_without_business") == []


def test_fund_balance_with_contributions_hard_when_fund_age_known():
    b = ProfileBuilder().entity("account.ra", "account", "Allan Gray RA", start_year=2015)
    val = b.add("retirementFunds[].value", "account.ra", money(0, "once"))
    b.add("retirementFunds[].employeeMonthly", "account.ra", money(2500))
    p = b.build()
    vs = check_constraints(p)
    assert len(_ids(vs, "fund_balance_after_contributions")) == 1
    assert _ids(vs, "fund_balance_zero_with_contributions") == []
    assert annotate_constraints(p, vs).facts[val].verification["constraint"] == "fail"
    b2 = ProfileBuilder().entity("account.ra", "account", "Allan Gray RA")
    b2.add("retirementFunds[].value", "account.ra", money(0, "once"))
    b2.add("retirementFunds[].employeeMonthly", "account.ra", money(2500))
    vs2 = check_constraints(b2.build())
    assert _ids(vs2, "fund_balance_after_contributions") == []
    assert len(_ids(vs2, "fund_balance_zero_with_contributions")) == 1


def test_coherence_and_range_rules():
    b = ProfileBuilder()
    b.add("household.includeSpouse", None, point(True, "none"))
    b.add("household.maritalStatus", None, point("single"))
    b.add("household.maritalRegime", None, point("icop"))
    b.entity("account.fund", "account", "Fund")
    b.add("retirementFunds[].value", "account.fund", money(100000, "once"))
    b.add("retirementFunds[].savingsPot", "account.fund", money(150000, "once"))
    b.add("retirement.{p}.retirementAge", "client", point(65, "age"))
    b.add("retirement.{p}.planningAge", "client", point(60, "age"))
    b.entity("goal.trip", "goal", "Trip")
    b.add("goals[].targetYear", "goal.trip", point(2020, "date"))
    vs = check_constraints(b.build())
    found = {v.rule_id for v in vs}
    assert {"spouse_without_partner", "regime_without_marriage", "savings_pot_within_fund", "planning_age_after_retirement",
            "goal_year_not_past"} <= found


def test_annotate_counts_soft_violations_and_resets_cleared_ones():
    b = ProfileBuilder()
    emp = b.add("{p}.employmentType", "client", point("self-employed"))
    p = b.build()
    a = annotate_constraints(p, check_constraints(p))
    ver = a.facts[emp].verification
    assert ver["constraint"] == "soft" and ver["constraint_soft_violations"] == 1
    assert ver["constraint_violations"] == ["self_employed_without_business"]
    cleared = annotate_constraints(a, [])
    assert cleared.facts[emp].verification["constraint"] == "pass"
    assert cleared.facts[emp].verification["constraint_soft_violations"] == 0


def test_soft_penalty_downweights_readings_that_break_rules():
    b = ProfileBuilder()
    b.add("income.{p}.grossMonthly", "client", money(60000))
    b.seed("retirementFunds[].value", "account.ra", money(300000, "once"))
    p = b.build()
    # "two fifty" for groceries: R250 and R2,500 are fine; R250,000 a month breaks two budget rules
    assert soft_penalty(p, "expenses.items[]#groceries", 2500.0) == 1.0
    big = soft_penalty(p, "expenses.items[]#groceries", 250000.0)
    assert big == pytest.approx(0.5 * 0.5)  # core expenses vs income and one line above income
    # a fund value 10× its prior FNA value is down-weighted by the rule's weight
    assert soft_penalty(p, "retirementFunds[].value#account.ra", 3_000_000.0) == pytest.approx(0.5)
    assert soft_penalty(p, "retirementFunds[].value#account.ra", 320_000.0) == 1.0
    # quantities and distributions are accepted as candidates too
    assert soft_penalty(p, "expenses.items[]#groceries", money(250000)) == pytest.approx(0.25)


def test_soft_penalty_hard_rule_keeps_a_floor():
    b = ProfileBuilder()
    b.seed("{p}.dateOfBirth", "client", point("1974-07-01", "date"))
    p = b.build()
    assert soft_penalty(p, "retirement.{p}.retirementAge#client", 45.0) == pytest.approx(0.05)
    assert soft_penalty(p, "retirement.{p}.retirementAge#client", Point(value=62)) == 1.0
