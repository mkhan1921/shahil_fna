"""Hedge lookup, bounds check and hypothesis weighting (spec §7.3 steps 5–7)."""

from __future__ import annotations

import logging

import pytest

from advisor_intel import stats
from advisor_intel.lexicon import (
    Lexicon,
    apply_bounds,
    assign_weights,
    build_hypotheses,
    check_bounds,
    filter_bounds,
    find_hedges,
    hedge_strength_max,
    load_lexicon,
    lookup_hedges,
    normalize_value,
)
from advisor_intel.models.dists import Categorical, LogNormal, Normal, Point, Quantity, Triangular
from advisor_intel.models.extraction import AmountExact, RawHypothesis
from advisor_intel.models.facts import Hedge

from .lexicon_support import ANCHOR, make_slot

LEX = load_lexicon()
RET_AGE = make_slot("retirement.{p}.retirementAge", "age", bounds=(40, 80))
GROSS = make_slot("income.{p}.grossMonthly", "rand_monthly", bounds=(0, 2_000_000))


def raw(value: float = 1.0, weight: float = 0.0, revises: bool = False, rationale: str = "") -> RawHypothesis:
    form = AmountExact(value=value, currency="ZAR", period="monthly", basis="gross")
    return RawHypothesis(intermediate=form, weight=weight, rationale=rationale or f"said {value}", revises=revises)


# --------------------------------------------------------------------------- hedges (step 5)


def test_hedges_map_to_lexicon_strengths():
    res = lookup_hedges(["I think", "thinking of", "i think", "maybe"], LEX)
    assert [h.lexicon_key for h in res.hedges] == ["i think", "thinking of", "maybe"]  # repeats kept once
    assert [h.strength for h in res.hedges] == [0.3, 0.5, 0.6]
    assert res.strength_max == 0.6 and not res.flags


def test_unknown_hedge_gets_the_default_and_is_logged():
    lex = Lexicon.from_text(LEX.source_text)
    res = lookup_hedges(["quite possibly-ish blah"], lex)
    assert res.hedges == [Hedge(term="quite possibly-ish blah", strength=0.5, lexicon_key="")]
    assert res.flags == ["lexicon_miss"] and len(lex.miss_log) == 1


def test_hedge_strength_max_is_zero_without_hedges():
    assert hedge_strength_max([]) == 0.0
    assert hedge_strength_max([Hedge(term="a", strength=0.2, lexicon_key="a"), Hedge(term="b", strength=1.0, lexicon_key="b")]) == 1.0


def test_find_hedges_in_text_longest_first():
    hs = find_hedges("I think it's about fifty, but I'm not sure. Maybe more.", LEX)
    assert [h.lexicon_key for h in hs] == ["i think", "about", "im not sure", "maybe"]  # 'not sure' is inside 'im not sure'
    assert find_hedges("", LEX) == []


# --------------------------------------------------------------------------- bounds (step 6)


def q(dist, unit="age") -> Quantity:
    return Quantity(dist=dist, unit=unit)


def test_values_inside_the_bounds_are_kept():
    assert check_bounds(q(Point(value=62)), RET_AGE).action == "keep"
    assert check_bounds(q(Triangular(low=60, mode=61.5, high=63.5)), RET_AGE).action == "keep"
    assert check_bounds(q(Point(value=40)), RET_AGE).action == "keep"  # the bounds are inclusive


def test_values_outside_the_bounds_are_dropped_and_logged(caplog):
    chk = check_bounds(q(Point(value=95)), RET_AGE)
    assert chk.action == "dropped" and not chk.keep and chk.p_outside == 1.0
    n = normalize_value(
        {"type": "AGE", "form": "value", "value": 95, "low": 0, "high": 0, "vague_word": ""}, RET_AGE, None, LEX, anchor_date=ANCHOR
    )
    with caplog.at_level(logging.WARNING, logger="advisor_intel.lexicon.bounds"):
        assert apply_bounds(n, RET_AGE, fact_id="retirement.client.retirementAge") is None
    assert "retirement.client.retirementAge" in caplog.text and "outside [40, 80]" in caplog.text


def test_a_tail_outside_the_bounds_is_truncated():
    n = normalize_value({"type": "AGE", "form": "range", "value": 0, "low": 74, "high": 84, "vague_word": ""}, RET_AGE, None, LEX)
    chk = check_bounds(n.quantity, RET_AGE)
    assert chk.action == "truncated" and 0.01 < chk.p_outside < 0.5
    kept = apply_bounds(n, RET_AGE)
    assert kept is not None and "truncated_to_bounds" in kept.flags
    assert stats.quantile(kept.quantity.dist, 1.0) <= 80 + 1e-9
    assert kept.normalization["bounds"]["high"] == 80


def test_bounds_skip_what_they_cannot_judge():
    assert check_bounds(q(Categorical(probs={"a": 1.0}), "none"), make_slot("x.y", "category", bounds=(0, 1))).action == "skipped"
    assert check_bounds(Quantity(dist=Point(value=5e7), unit="USD", period="monthly"), GROSS).action == "skipped"
    assert check_bounds(q(Point(value=500)), make_slot("x.y", "age")).action == "skipped"
    assert check_bounds(None, RET_AGE).action == "skipped"


def test_filter_bounds_reports_each_drop():
    forms = [{"type": "AMOUNT_EXACT", "value": v, "currency": "ZAR", "period": "monthly", "basis": "gross"} for v in (45_000, 45_000_000)]
    ns = [normalize_value(f, GROSS, None, LEX) for f in forms]
    res = filter_bounds([*ns, None], GROSS, fact_id="income.client.grossMonthly")
    assert res.values[0] is ns[0] and res.values[1] is None and res.values[2] is None
    assert len(res.dropped) == 1 and "hypothesis 1" in res.dropped[0] and "out of bounds" in res.dropped[0]
    (rejected,) = res.rejected
    assert rejected.has("out_of_bounds") and rejected.normalization["bounds"]["high"] == 2_000_000
    assert rejected.quantity.dist == Point(value=45_000_000)


def test_wide_normal_rates_are_truncated_not_dropped():
    rate = make_slot("event[].growth", "decimal", bounds=(-0.5, 0.5))
    chk = check_bounds(Quantity(dist=Normal(mean=0.45, sd=0.1), unit="pct"), rate)
    assert chk.action == "truncated"
    assert check_bounds(Quantity(dist=LogNormal(median=0.9, p10=0.7, p90=1.2), unit="pct"), rate).action == "dropped"


# --------------------------------------------------------------------------- weighting (step 7)


def test_equal_split_without_client_weights():
    assert assign_weights([raw(1), raw(2), raw(3), raw(4)], LEX) == pytest.approx([0.25] * 4)
    assert assign_weights([], LEX) == []


def test_client_weights_are_kept_and_the_rest_split():
    assert assign_weights([raw(60, 0.7), raw(65, 0.3)], LEX) == pytest.approx([0.7, 0.3])
    assert assign_weights([raw(60, 0.6), raw(62), raw(65)], LEX) == pytest.approx([0.6, 0.2, 0.2])
    assert assign_weights([raw(60, 3), raw(65, 1)], LEX) == pytest.approx([0.75, 0.25])
    assert assign_weights([raw(60, 0.8), raw(62, 0.6), raw(65)], LEX) == pytest.approx([0.8 / 1.4 * (1.4 / 2.0), 0.6 / 2.0, 0.6 / 2.0])


def test_recency_gives_the_revising_statement_0_8():
    assert assign_weights([raw(60), raw(65, revises=True)], LEX) == pytest.approx([0.2, 0.8])
    assert assign_weights([raw(60), raw(62), raw(65, revises=True)], LEX) == pytest.approx([0.1, 0.1, 0.8])
    assert assign_weights([raw(60), raw(63, revises=True), raw(65, revises=True)], LEX) == pytest.approx([0.2, 0.4, 0.4])
    assert assign_weights([raw(60, 0.9), raw(65, 0.1, revises=True)], LEX) == pytest.approx([0.2, 0.8])
    assert assign_weights([raw(63, revises=True), raw(65, revises=True)], LEX) == pytest.approx([0.5, 0.5])


def test_recency_weights_are_lexicon_tunable():
    text = LEX.source_text.replace("recency_weight: {revising: 0.8, revised: 0.2}", "recency_weight: {revising: 0.7, revised: 0.3}")
    assert assign_weights([raw(60), raw(65, revises=True)], Lexicon.from_text(text)) == pytest.approx([0.3, 0.7])


def test_scale_readings_share_their_parent_weight():
    # Two spoken options; the first was scale-ambiguous and split 0.6 / 0.4 between two readings.
    hyps = [raw(250_000), raw(2_500_000), raw(300_000)]
    w = assign_weights(hyps, LEX, groups=[0, 0, 1], scale_weights=[0.6, 0.4, 1.0])
    assert w == pytest.approx([0.3, 0.2, 0.5])
    revised = assign_weights([raw(1), raw(2), raw(3, revises=True)], LEX, groups=[0, 0, 1], scale_weights=[0.5, 0.5, 1.0])
    assert revised == pytest.approx([0.1, 0.1, 0.8])
    with pytest.raises(ValueError):
        assign_weights(hyps, LEX, groups=[0, 1])


def test_build_hypotheses_drops_out_of_bounds_values_and_renormalises():
    raws = [raw(45_000), raw(45_000_000), raw(50_000, revises=True)]
    ns = [normalize_value(h.intermediate, GROSS, None, LEX) for h in raws]
    kept = filter_bounds(ns, GROSS).values
    hyps = build_hypotheses(raws, kept, LEX)
    assert [h.value.dist for h in hyps] == [Point(value=45_000), Point(value=50_000)]
    assert [h.weight for h in hyps] == pytest.approx([0.2, 0.8])
    assert hyps[1].revises and hyps[0].rationale == "said 45000"
    assert sum(h.weight for h in hyps) == pytest.approx(1.0)
    assert build_hypotheses([raw(1)], [None], LEX) == []
