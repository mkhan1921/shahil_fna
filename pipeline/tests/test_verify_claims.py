"""Claim sentences per slot and value (§9.1a)."""

from __future__ import annotations

from advisor_intel.models.dists import Bernoulli, Categorical, Discrete, LogNormal, Triangular
from advisor_intel.verify.claims import claims_for_fact

from .verify_factories import ProfileBuilder, money, point, qty


def _claims(build):
    b = ProfileBuilder(spouse=True)
    b.entity("person.thandi", "person", "Thandi").entity("policy.disc", "policy", "Discovery life").entity("goal.study", "goal", "Study overseas")
    fid = build(b)
    p = b.build()
    return [c.text for c in claims_for_fact(p.facts[fid], profile=p)]


def test_spec_example_range():
    texts = _claims(lambda b: b.add("retirement.{p}.retirementAge", "client", qty(Triangular(low=60, mode=62.5, high=65), "age")))
    assert texts == ["The client's target retirement age is between 60 and 65."]


def test_one_claim_per_hypothesis_with_weight_at_least_0_1():
    texts = _claims(lambda b: b.add("retirement.{p}.retirementAge", "spouse", [(point(60, "age"), 0.55), (point(65, "age"), 0.4), (point(70, "age"), 0.05)]))
    assert texts == ["The client's spouse's target retirement age is 60.", "The client's spouse's target retirement age is 65."]


def test_amounts_with_period_and_spread():
    assert _claims(lambda b: b.add("income.{p}.grossMonthly", "client", money(45000))) == ["The client's gross salary is R45,000 a month."]
    spread = _claims(lambda b: b.add("income.{p}.grossMonthly", "client", qty(LogNormal(median=30000, p10=25000, p90=36000), "ZAR", "monthly")))
    assert spread == ["The client's gross salary is about R30,000 a month (between R25,000 and R36,000)."]
    assert _claims(lambda b: b.add("expenses.items[]", "groceries", money(7000))) == ["The household spends R7,000 a month on groceries & toiletries."]
    cover = _claims(lambda b: b.add("policies[].cover", "policy.disc", qty(Triangular(low=2_250_000, mode=2_500_000, high=2_750_000), "ZAR", "once")))
    assert cover == ["For the policy 'Discovery life', the cover amount is between R2,250,000 and R2,750,000."]


def test_dates_ages_and_counts():
    dob = _claims(lambda b: b.add("{p}.dateOfBirth", "client", qty(Discrete(support=[1974.0, 1974.5, 1975.0], weights=[0.3, 0.4, 0.3]), "date")))
    assert dob == ["The client's date of birth is around July 1974 (about 52 years old at the call)."]
    years = _claims(lambda b: b.add("dependants[].tertiaryYears", "person.thandi", point(4, "years")))
    assert years == ["For the dependant 'Thandi', the length of tertiary study is 4 years."]
    rate = _claims(lambda b: b.add("liabilities[].interestRate", "liability.bond", point(0.1175, "pct")))
    assert rate == ["For the debt, the interest rate is 11.75%."]


def test_booleans_categories_events_and_text():
    assert _claims(lambda b: b.add("{p}.smoker", "client", point(False))) == ["The client does not smoke."]
    assert _claims(lambda b: b.add("dependants[].tertiary", "person.thandi", qty(Bernoulli(p=0.7), "none"))) == [
        "Probably the dependant 'Thandi' plans to go on to tertiary study."
    ]
    cat = _claims(lambda b: b.add("{p}.employmentType", "client", qty(Categorical(probs={"self-employed": 0.6, "contract": 0.4}), "none")))
    assert cat == ["The client's employment type is probably 'self-employed' (otherwise 'contract')."]
    assert _claims(lambda b: b.add("estate.{p}.hasWill", "client", point("yes"))) == ["The client has a will."]
    assert _claims(lambda b: b.add("event[].occurs", "goal.study", qty(Bernoulli(p=0.4), "none"))) == ["The event 'Study overseas' might happen."]
    assert _claims(lambda b: b.add("{p}.occupation", "client", point("electrical engineer"))) == [
        "The client's occupation is described as \"electrical engineer\"."
    ]
    risk = _claims(lambda b: b.add("riskProfile.answers[horizon]", None, point("More than 10 years")))
    assert risk == ["The client's answer to the risk question 'When will you need to start drawing on the majority of this money?' is 'More than 10 years'."]


def test_claims_never_carry_the_extractors_reading():
    texts = _claims(lambda b: b.add("income.{p}.grossMonthly", "client", money(45000), interpretation="net figure grossed up", self_score=0.42))
    assert all("grossed" not in t and "0.42" not in t for t in texts)
