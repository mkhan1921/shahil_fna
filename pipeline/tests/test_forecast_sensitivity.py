"""Sensitivity runs for value of information, engine side (spec §10.2), and the shared fact_extremes."""

from __future__ import annotations

import pytest

from advisor_intel import stats
from advisor_intel.forecast import fact_extremes, sensitivity_runs
from advisor_intel.forecast.config import load_cma, load_config
from advisor_intel.models.dists import Bernoulli, Categorical, LogNormal, Point, Triangular
from advisor_intel.schema.slots import load_catalog
from tests.forecast_support import StubTaxModel, add_fact, point, prior_profile, reject, sample_document

CFG = load_config()
CMA_ = load_cma()
TAX = StubTaxModel()
CATALOG = load_catalog()


@pytest.fixture(scope="module")
def candidates():
    doc = sample_document()
    prof = prior_profile(doc)
    prof, wide = add_fact(prof, "retirement.{p}.retirementAge", "retirement.client.retirementAge", Triangular(low=57, mode=63, high=68),
                          instance="client", unit="age", p_correct=0.7)
    prof, narrow = add_fact(prof, "expenses.items[]", "expenses.items[groceries]", Triangular(low=8_800, mode=9_000, high=9_200),
                            instance="groceries", period="monthly", review="accepted")
    neo = next(e for e in prof.entities.values() if e.canonical_name.startswith("Neo"))
    common = {"instance": "event.offshore_1", "entity_refs": [neo.entity_id], "review": "accepted"}
    prof, event = add_fact(prof, "event[].occurs", "event[e1].occurs", Bernoulli(p=0.5), unit="none", **common)
    prof, _ = add_fact(prof, "event[].cashImpact", "event[e1].cashImpact", point(-60_000), unit="USD", period="annual", **common)
    prof, _ = add_fact(prof, "event[].kind", "event[e1].kind", point("education-offshore"), unit="none", **common)
    fund = next(f for f in prof.facts.values() if f.slot_id == "retirementFunds[].owner" and f.value.dist == Point(value="client"))
    prof, owner = add_fact(prof, "retirementFunds[].owner", fund.fna_path, Categorical(probs={"client": 0.6, "spouse": 0.4}),
                           instance=fund.instance_key, unit="none", review="accepted", entity_refs=fund.entity_refs)
    cover = next(f.fact_id for f in prof.facts.values() if f.slot_id == "policies[].cover")
    ids = {"wide": wide, "narrow": narrow, "event": event, "owner": owner, "cover": cover}
    rows = sensitivity_runs(prof, list(ids.values()), n_paths=1_000, seed=9, base_document=doc, config=CFG, cma=CMA_, tax_model=TAX)
    return prof, doc, ids, rows


def test_a_wide_tier_a_fact_outranks_a_narrow_one(candidates):
    _, _, ids, rows = candidates
    wide, narrow = rows[ids["wide"]], rows[ids["narrow"]]
    assert wide.tier == narrow.tier == "A" and wide.kind == "numeric"
    dist = Triangular(low=57, mode=63, high=68)
    assert (wide.low, wide.high) == pytest.approx((stats.quantile(dist, 0.1), stats.quantile(dist, 0.9)))  # 59.57 and 65.65
    assert wide.pos_low < wide.pos_high  # retiring later helps
    assert wide.i_content == pytest.approx(wide.pos_high - wide.pos_low) and wide.i_content > 10 * max(narrow.i_content, 1e-3)


def test_extraction_impact_weights_the_prior_by_the_risk_of_mishearing(candidates):
    _, _, ids, rows = candidates
    wide, narrow = rows[ids["wide"]], rows[ids["narrow"]]
    assert wide.trust == 0.7 and wide.pos_prior is not None
    assert wide.i_extract == pytest.approx(0.3 * abs(wide.pos_base - wide.pos_prior), abs=1e-6)
    assert narrow.trust == 1.0 and narrow.i_extract == 0.0  # reviewed: nothing left to mishear


def test_events_and_categories_use_the_two_most_different_outcomes(candidates):
    _, _, ids, rows = candidates
    event = rows[ids["event"]]
    assert (event.kind, event.low, event.high) == ("boolean", False, True)
    assert event.pos_low > event.pos_high  # paying for offshore study lowers the odds
    owner = rows[ids["owner"]]
    assert owner.kind == "categorical" and {owner.low, owner.high} == {"client", "spouse"} and owner.pos_low <= owner.pos_high


def test_facts_the_forecast_does_not_use_have_no_engine_impact(candidates):
    _, _, ids, rows = candidates
    cover = rows[ids["cover"]]
    assert cover.bound is False and cover.i_content == 0 and cover.pos_low == cover.pos_high == cover.pos_base


def test_common_random_numbers_make_runs_repeatable(candidates):
    prof, doc, ids, rows = candidates
    again = sensitivity_runs(prof, [ids["wide"], ids["narrow"]], n_paths=1_000, seed=9, base_document=doc, config=CFG, cma=CMA_, tax_model=TAX)
    assert again[ids["wide"]] == rows[ids["wide"]] and again[ids["narrow"]].pos_base == rows[ids["wide"]].pos_base
    with pytest.raises(KeyError):
        sensitivity_runs(prof, ["nope#1"], seed=1, base_document=doc, tax_model=TAX)


def test_fact_extremes():
    doc = sample_document()
    prof = prior_profile(doc)
    prof, age = add_fact(prof, "retirement.{p}.retirementAge", "retirement.client.retirementAge", Triangular(low=57, mode=63, high=68),
                         instance="client", unit="age", p_correct=0.7)
    slot = CATALOG.slot("retirement.{p}.retirementAge")
    assert fact_extremes(prof.facts[age], slot) == (60, 66)  # p10 59.57 and p90 65.65, whole years as the document stores them
    prof, dob = add_fact(prof, "{p}.dateOfBirth", "client.dateOfBirth", Triangular(low=1985.5, mode=1986.3, high=1987.0), instance="client",
                         unit="date", p_correct=0.9)
    lo, hi = fact_extremes(prof.facts[dob], CATALOG.slot("{p}.dateOfBirth"))
    assert lo.startswith("1985-") and hi.startswith("1986-")
    prof, fee = add_fact(prof, "dependants[].tertiaryCostAnnual", "dependants[x].tertiaryCostAnnual", LogNormal(median=10_000, p10=8_000, p90=12_000),
                         instance="person.neo_mokoena_1", unit="USD", period="annual", review="accepted")
    lo, hi = fact_extremes(prof.facts[fee], CATALOG.slot("dependants[].tertiaryCostAnnual"))
    _, hi_r = fact_extremes(prof.facts[fee], CATALOG.slot("dependants[].tertiaryCostAnnual"), spot=CMA_.fx.spot)
    assert hi_r == pytest.approx(hi * 17.5, rel=1e-6) and lo < 10_000 < hi
    prof, tert = add_fact(prof, "dependants[].tertiary", "dependants[x].tertiary", Bernoulli(p=0.4), instance="person.neo_mokoena_1", unit="none",
                          review="accepted")
    assert fact_extremes(prof.facts[tert], CATALOG.slot("dependants[].tertiary")) == (False, True)
    prof, mode = add_fact(prof, "retirement.{p}.targetMode", "retirement.client.targetMode", Categorical(probs={"percent": 0.3, "amount": 0.7}),
                          instance="client", unit="none", review="accepted")
    assert fact_extremes(prof.facts[mode], CATALOG.slot("retirement.{p}.targetMode")) == ("amount", "percent")
    prof, gone = add_fact(prof, "income.{p}.otherTaxableMonthly", "income.client.otherTaxableMonthly", point(5_000), instance="client",
                          period="monthly", p_correct=0.5)
    prof = reject(prof, gone)
    assert fact_extremes(prof.facts[gone], CATALOG.slot("income.{p}.otherTaxableMonthly")) == (None, None)
