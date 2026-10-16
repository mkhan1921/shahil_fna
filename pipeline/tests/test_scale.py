"""Scale resolution for spoken amounts (spec §7.4) and the extractor glue (the LLM never chooses the scale)."""

from __future__ import annotations

import math

import pytest

from advisor_intel.extract.scale import (
    MARGIN_CAP,
    ScaleContext,
    match_span,
    plausible_range,
    resolve_amount,
    resolve_hypotheses,
    resolve_scale,
    scale_features,
)
from advisor_intel.lexicon import assign_weights, load_lexicon, normalize_value
from advisor_intel.models.dists import LogNormal, Point, Quantity, Triangular
from advisor_intel.models.extraction import AmountBound, AmountExact, AmountRange, AmountVague, RawHypothesis
from advisor_intel.models.transcript import NumericSpan, Reading

from .lexicon_support import fact, make_slot, profile

GROSS = make_slot(
    "income.{p}.grossMonthly", "rand_monthly", population_prior=LogNormal(median=35_000, p10=12_000, p90=90_000), bounds=(0, 2_000_000)
)
HOUSE = make_slot(
    "assets[].value", "rand", population_prior=LogNormal(median=1_500_000, p10=600_000, p90=4_000_000), bounds=(0, 200_000_000)
)
FEES = make_slot("dependants[].schoolFeesAnnual", "rand_annual", population_prior=LogNormal(median=40_000, p10=10_000, p90=150_000))
EXPENSE = make_slot("expenses.items[groceries]", "rand_monthly", population_prior=LogNormal(median=6_000, p10=2_500, p90=14_000))


def two_fifty(span_id: str = "n1", **kw) -> NumericSpan:
    """The spec's example: 'two fifty' has readings 250, 250,000 and 2,500,000 (§5.3)."""
    readings = [
        Reading(value=250, weight=0.4, scale_source="digits"),
        Reading(value=250_000, weight=0.35, scale_source="elided thousand"),
        Reading(value=2_500_000, weight=0.25, scale_source="elided million"),
    ]
    return NumericSpan(
        span_id=span_id, segment_id="s0007", kind="amount", word_start=3, word_end=5, surface="two fifty", readings=readings, **kw
    )


def span(span_id: str, *values_weights: tuple[float, float], **kw) -> NumericSpan:
    readings = [Reading(value=v, weight=w) for v, w in values_weights]
    return NumericSpan(
        span_id=span_id, segment_id="s0001", kind="amount", word_start=0, word_end=1, surface=span_id, readings=readings, **kw
    )


def values(res) -> list[float]:
    return [r.value for r in res.chosen]


def test_field_range_picks_the_scale_for_a_monthly_income():
    res = resolve_scale(two_fifty(), GROSS)
    assert values(res) == [250_000] and res.chosen[0].weight == 1.0 and not res.scale_ambiguous
    by_value = {r.value: r for r in res.readings}
    assert by_value[250].signals["range"] < 0.05  # 250 a month is far below the plausible range
    assert by_value[2_500_000].signals["range"] == pytest.approx(1e-6)  # beyond the slot's hard bounds
    assert res.features == {
        "grammar_readings": 3.0,
        "grammar_top_margin": pytest.approx(0.4 / 0.35),
        "scale_ambiguous": 0.0,
        "second_pass_run": 0.0,
        "second_pass_agreement": None,
    }


def test_two_plausible_readings_become_weighted_hypotheses():
    res = resolve_scale(two_fifty(), HOUSE)
    assert sorted(values(res)) == [250_000, 2_500_000]
    assert res.scale_ambiguous and res.features["scale_ambiguous"] == 1.0
    assert sum(r.weight for r in res.chosen) == pytest.approx(1.0)
    assert all(r.weight >= 0.1 for r in res.chosen)
    assert {r.value: r for r in res.readings}[250].weight < 0.1  # 250 for a house is dropped


def test_prior_fna_value_within_20_percent_gets_a_boost():
    plain = {r.value: r.weight for r in resolve_scale(two_fifty(), HOUSE).readings}
    boosted = resolve_scale(two_fifty(), HOUSE, context=ScaleContext(prior_value=2_400_000))
    assert boosted.chosen[0].value == 2_500_000 and boosted.chosen[0].signals["prior"] == 3.0
    assert {r.value: r.weight for r in boosted.readings}[2_500_000] > plain[2_500_000]
    far = resolve_scale(two_fifty(), HOUSE, context=ScaleContext(prior_value=3_500_000))  # 2.5m is not within 20% of 3.5m
    assert all(r.signals["prior"] == 1.0 for r in far.readings)


def test_second_pass_agreement_multiplies_by_four():
    res = resolve_scale(two_fifty(second_pass_reading=Reading(value=2_500_000, weight=1.0)), HOUSE)
    assert values(res) == [2_500_000] and not res.scale_ambiguous
    by_value = {r.value: r for r in res.readings}
    assert by_value[2_500_000].signals["second_pass"] == 4.0 and by_value[250_000].signals["second_pass"] == 0.25
    assert res.features["second_pass_run"] == 1.0 and res.features["second_pass_agreement"] == 1.0


def test_second_pass_contradiction_divides_by_four():
    res = resolve_scale(two_fifty(), GROSS, context=ScaleContext(second_pass=Reading(value=250, weight=1.0)))
    top = res.chosen[0]
    assert top.value == 250_000 and top.signals["second_pass"] == 0.25
    assert res.scale_ambiguous and res.features["second_pass_agreement"] == 0.0  # disagreements go to the reviewer first


def test_consistency_penalty_down_weights_a_reading():
    def expenses_below_income(x: float) -> float:
        return 0.02 if x > 50_000 else 1.0  # a monthly grocery bill above the household's monthly income (R50,000)

    groceries = span("g1", (8_000, 0.5), (80_000, 0.5))
    plain = resolve_scale(groceries, EXPENSE)
    penalised = resolve_scale(
        groceries, EXPENSE, context=ScaleContext(consistency=expenses_below_income, plausible_range=(1_000, 1_000_000))
    )
    assert values(penalised) == [8_000]
    assert {r.value: r.signals["consistency"] for r in penalised.readings}[80_000] == 0.02
    assert plain.chosen[0].value == 8_000


def test_readings_are_compared_in_the_fields_unit():
    # 'seven' for annual school fees, said 'a month': 7,000 a month is 84,000 a year, inside the range.
    fees = span("f1", (7, 0.5), (7_000, 0.5))
    res = resolve_scale(fees, FEES, context=ScaleContext(period="monthly"))
    assert values(res) == [7_000] and res.chosen[0].slot_value == 84_000


def test_single_reading_and_duplicate_paths():
    one = resolve_scale(span("x", (45_000, 1.0)), GROSS)
    assert values(one) == [45_000] and one.features["grammar_top_margin"] == MARGIN_CAP and one.features["grammar_readings"] == 1.0
    dup = resolve_scale(span("y", (45_000, 0.3), (45_000, 0.3), (450, 0.4)), GROSS)
    assert dup.features["grammar_readings"] == 2.0 and values(dup) == [45_000]
    empty = resolve_scale(NumericSpan(span_id="z", segment_id="s", kind="amount", word_start=0, word_end=1, surface="?"), GROSS)
    assert empty.chosen == [] and empty.features["grammar_readings"] == 0.0


def test_plausible_range_sources():
    lo, hi = plausible_range(GROSS)
    assert 4_000 < lo < 6_000 and 200_000 < hi < 300_000  # the prior's 1st–99th percentiles
    assert plausible_range(GROSS, ScaleContext(plausible_range=(5_000, 500_000))) == (5_000, 500_000)
    assert plausible_range(make_slot("x.y", "rand", bounds=(0, 10))) == (0.0, 10.0)
    assert plausible_range(make_slot("x.y", "rand")) is None


# --------------------------------------------------------------------------- glue: the extractor's quoted amounts


def test_the_llm_scale_is_replaced_by_the_resolved_reading():
    quoted = AmountExact(value=2_500_000, currency="ZAR", period="monthly", basis="gross")  # the model picked the wrong scale
    res = resolve_amount(quoted, [two_fifty()], GROSS)
    assert [h.form.value for h in res.hypotheses] == [250_000] and res.hypotheses[0].weight == 1.0
    assert res.hypotheses[0].span_ids == ["n1"] and res.hypotheses[0].form.period == "monthly"
    assert not res.flags


def test_ambiguous_amount_becomes_two_forms():
    quoted = AmountExact(value=250, currency="ZAR", period="once", basis="na")
    res = resolve_amount(quoted, [two_fifty()], HOUSE)
    assert sorted(h.form.value for h in res.hypotheses) == [250_000, 2_500_000]
    assert res.scale_ambiguous and sum(h.weight for h in res.hypotheses) == pytest.approx(1.0)


def test_range_ends_resolve_together():
    thirty = span("a", (30, 0.6), (30_000, 0.4))
    forty = span("b", (40_000, 1.0))
    res = resolve_amount(AmountRange(low=30_000, high=40_000, currency="ZAR", period="monthly", basis="gross"), [forty, thirty], GROSS)
    assert [(h.form.low, h.form.high) for h in res.hypotheses] == [(30_000, 40_000)]
    assert sorted(res.hypotheses[0].span_ids) == ["a", "b"]
    assert res.features["grammar_readings"] == 2.0 and res.features["grammar_top_margin"] == pytest.approx(1.5)


def test_range_combinations_keep_low_below_high():
    low = span("a", (300, 0.5), (300_000, 0.5))
    high = span("b", (40_000, 0.5), (400_000, 0.5))
    res = resolve_amount(AmountRange(low=300_000, high=400_000, currency="ZAR", period="once", basis="na"), [low, high], HOUSE)
    assert all(h.form.low <= h.form.high for h in res.hypotheses)


def test_bound_and_vague_forms():
    bound = resolve_amount(
        AmountBound(bound=250_000, direction="at_least", currency="ZAR", period="monthly", basis="gross"), [two_fifty()], GROSS
    )
    assert [h.form.bound for h in bound.hypotheses] == [250_000]
    vague = AmountVague(vague_word="a few hundred K", currency="ZAR", period="annual", basis="gross")
    passthrough = resolve_amount(vague, [two_fifty()], GROSS)
    assert passthrough.hypotheses[0].form == vague and passthrough.hypotheses[0].weight == 1.0


def test_unmatched_number_keeps_the_quote_and_is_flagged():
    res = resolve_amount(AmountExact(value=42_000, currency="ZAR", period="monthly", basis="net"), [two_fifty()], GROSS)
    assert [h.form.value for h in res.hypotheses] == [42_000] and res.flags == ["no_matching_span"]
    assert res.features["grammar_readings"] == 0.0 and res.features["grammar_top_margin"] is None


def test_match_span_ignores_other_kinds_and_used_spans():
    ages = NumericSpan(
        span_id="age1", segment_id="s", kind="age", word_start=0, word_end=1, surface="sixty", readings=[Reading(value=250, weight=1)]
    )
    assert match_span(250_000, [ages]) is None
    assert match_span(250_000, [ages, two_fifty()]).span_id == "n1"
    assert match_span(250_000, [two_fifty()], used={"n1"}) is None
    assert match_span(1_234, [two_fifty()]) is None


def test_resolved_hypotheses_feed_weighting_and_normalisation():
    lex = load_lexicon()
    raws = [
        RawHypothesis(
            intermediate=AmountExact(value=250, currency="ZAR", period="once", basis="na"), weight=0, rationale="'two fifty'", revises=False
        ),
        RawHypothesis(
            intermediate=AmountExact(value=3_000_000, currency="ZAR", period="once", basis="na"),
            weight=0,
            rationale="'three mil'",
            revises=False,
        ),
    ]
    three = span("n2", (3_000_000, 1.0))
    scaled = resolve_hypotheses(raws, [two_fifty(), three], HOUSE)
    assert scaled.groups == [0, 0, 1] and scaled.features["scale_ambiguous"] == 1.0
    weights = assign_weights([h.raw for h in scaled.items], lex, groups=scaled.groups, scale_weights=scaled.scale_weights)
    assert sum(weights[:2]) == pytest.approx(0.5) and weights[2] == pytest.approx(0.5)
    normalised = [normalize_value(h.raw.intermediate, HOUSE, None, lex) for h in scaled.items]
    assert sorted(n.quantity.dist.value for n in normalised) == [250_000, 2_500_000, 3_000_000]


def test_context_for_fact_reads_the_prior_fna_value():
    prof = profile(
        fact(
            "assets[].value#property.home",
            None,
            slot_id="assets[].value",
            prior=Quantity(dist=Point(value=2_400_000), unit="ZAR", period="once"),
        )
    )
    ctx = ScaleContext.for_fact(HOUSE, prof, "assets[].value#property.home")
    assert ctx.prior_value == 2_400_000
    assert ScaleContext.for_fact(HOUSE, prof, "assets[].value#property.other").prior_value is None
    assert ScaleContext.for_fact(HOUSE, None, None).prior_value is None


def test_scale_features_aggregate_over_spans():
    a = resolve_scale(two_fifty(), HOUSE)
    b = resolve_scale(span("b", (40_000, 1.0)), HOUSE, context=ScaleContext(second_pass=Reading(value=40_000, weight=1)))
    f = scale_features([a, b])
    assert (
        f["grammar_readings"] == 3.0 and f["scale_ambiguous"] == 1.0 and f["second_pass_run"] == 1.0 and f["second_pass_agreement"] == 1.0
    )
    assert f["grammar_top_margin"] == pytest.approx(0.4 / 0.35)
    assert scale_features([])["second_pass_agreement"] is None
    assert all(math.isfinite(v) for v in f.values() if v is not None)


def test_triangular_prior_works_too():
    slot = make_slot("retirement.{p}.retirementAge", "age", population_prior=Triangular(low=55, mode=65, high=70))
    res = resolve_scale(span("age", (6, 0.5), (60, 0.5)), slot)
    assert values(res) == [60]
