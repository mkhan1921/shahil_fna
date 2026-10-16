"""One extracted field through §7.3 steps 4–7 (normalize_field), and the lexicon fit reading its records."""

from __future__ import annotations

import pytest

from advisor_intel.extract.scale import ScaleContext
from advisor_intel.lexicon import Lexicon, load_lexicon, normalize_field
from advisor_intel.lexicon.fit import EditPair
from advisor_intel.models.dists import LogNormal, Point, Quantity, Triangular
from advisor_intel.models.extraction import AgeForm, AmountApprox, AmountExact, FieldValue, RawHypothesis
from advisor_intel.models.facts import Fact, Provenance
from advisor_intel.models.transcript import NumericSpan, Reading

from .lexicon_support import ANCHOR, make_slot

LEX = load_lexicon()
HOUSE = make_slot(
    "assets[].value", "rand", population_prior=LogNormal(median=1_500_000, p10=600_000, p90=4_000_000), bounds=(0, 200_000_000)
)
RET_AGE = make_slot("retirement.{p}.retirementAge", "age", bounds=(40, 80))


def field(hypotheses, hedges=()) -> FieldValue:
    return FieldValue(
        fna_path="assets[x].value",
        hypotheses=hypotheses,
        hedges=list(hedges),
        speaker_attribution="client",
        said="about two fifty",
        interpretation="the house value",
        self_score=0.8,
    )


def two_fifty() -> NumericSpan:
    readings = [Reading(value=250, weight=0.4), Reading(value=250_000, weight=0.35), Reading(value=2_500_000, weight=0.25)]
    return NumericSpan(span_id="n1", segment_id="s0012", kind="amount", word_start=1, word_end=3, surface="two fifty", readings=readings)


def test_scale_normalise_bound_and_weight_one_field():
    first = AmountApprox(value=250, approx_word="about", currency="ZAR", period="once", basis="na")
    second = AmountExact(value=3_000_000, currency="ZAR", period="once", basis="na")
    three = NumericSpan(
        span_id="n2",
        segment_id="s0014",
        kind="amount",
        word_start=0,
        word_end=2,
        surface="three mil",
        readings=[Reading(value=3_000_000, weight=1.0, scale_source="mil")],
    )
    fv = field(
        [
            RawHypothesis(intermediate=first, weight=0, rationale="'about two fifty'", revises=False),
            RawHypothesis(intermediate=second, weight=0, rationale="'actually three mil'", revises=True),
        ],
        hedges=["I think", "about"],
    )
    out = normalize_field(fv, HOUSE, None, LEX, spans=[two_fifty(), three], anchor_date=ANCHOR)
    values = [h.value.dist for h in out.hypotheses]
    assert values[0] == Triangular(low=225_000, mode=250_000, high=275_000)  # the scale came from the grammar, not the model
    assert values[1] == Triangular(low=2_250_000, mode=2_500_000, high=2_750_000)
    assert values[2] == Point(value=3_000_000)
    weights = [h.weight for h in out.hypotheses]
    assert sum(weights) == pytest.approx(1.0) and weights[2] == pytest.approx(0.8)  # the revising statement
    assert sum(weights[:2]) == pytest.approx(0.2)  # the ambiguous readings share the revised statement's 0.2
    assert [h.revises for h in out.hypotheses] == [False, False, True]
    assert out.features["scale_ambiguous"] == 1.0 and out.features["grammar_readings"] == 3.0
    assert out.features["hedge_strength_max"] == pytest.approx(0.3) and out.features["n_hypotheses"] == 3.0
    assert out.features["lexicon_miss"] == 0.0 and out.features["top_weight"] == pytest.approx(0.8)
    assert out.normalization["lexicon_version"] == "1.3" and len(out.normalization["hypotheses"]) == 3
    assert out.normalizations[0]["span_ids"] == ["n1"]
    assert [h.lexicon_key for h in out.hedges] == ["i think", "about"]


def test_out_of_bounds_values_are_dropped_before_weighting():
    fv = field(
        [
            RawHypothesis(
                intermediate=AgeForm(form="value", value=62, low=0, high=0, vague_word=""), weight=0, rationale="62", revises=False
            ),
            RawHypothesis(
                intermediate=AgeForm(form="value", value=620, low=0, high=0, vague_word=""), weight=0, rationale="620", revises=False
            ),
        ]
    )
    out = normalize_field(fv, RET_AGE, None, LEX, fact_id="retirement.client.retirementAge")
    assert [h.value.dist for h in out.hypotheses] == [Point(value=62)] and out.hypotheses[0].weight == 1.0
    assert "out_of_bounds" in out.flags and len(out.dropped) == 1 and out.rejected[0].has("out_of_bounds")
    assert out.features["scale_ambiguous"] == 0.0 and out.features["grammar_readings"] == 0.0
    assert out.normalization["form_type"] == "AGE"  # one hypothesis: its own record


def test_lexicon_misses_surface_as_features():
    lex = Lexicon.from_text(LEX.source_text)
    fv = field(
        [
            RawHypothesis(
                intermediate=AgeForm(form="vague", value=0, low=0, high=0, vague_word="once the bond is paid"),
                weight=0,
                rationale="",
                revises=False,
            )
        ],
        hedges=["kinda-sorta maybe-ish"],
    )
    slot = make_slot("retirement.{p}.retirementAge", "age", bounds=(40, 80), population_prior=Triangular(low=60, mode=65, high=70))
    out = normalize_field(fv, slot, None, lex)
    assert out.features["lexicon_miss"] == 1.0 and {"lexicon_miss", "population_prior"} <= set(out.flags)
    assert out.hypotheses[0].value.dist == slot.population_prior
    assert len(lex.miss_log) == 2


def test_prior_fna_value_steers_scale_through_the_context():
    fv = field(
        [
            RawHypothesis(
                intermediate=AmountExact(value=250, currency="ZAR", period="once", basis="na"), weight=0, rationale="", revises=False
            )
        ]
    )
    plain = normalize_field(fv, HOUSE, None, LEX, spans=[two_fifty()])
    steered = normalize_field(fv, HOUSE, None, LEX, spans=[two_fifty()], scale_context=ScaleContext(prior_value=2_400_000))
    assert len(plain.hypotheses) == 2
    top = max(steered.hypotheses, key=lambda h: h.weight)
    assert top.value.dist == Point(value=2_500_000)


def test_fit_pairs_come_only_from_unambiguous_records():
    one = normalize_field(
        field(
            [
                RawHypothesis(
                    intermediate=AgeForm(form="vague", value=0, low=0, high=0, vague_word="early sixties"),
                    weight=0,
                    rationale="",
                    revises=False,
                )
            ]
        ),
        RET_AGE,
        None,
        LEX,
    )
    fact = Fact(
        fact_id="retirement.client.retirementAge",
        slot_id=RET_AGE.slot_id,
        fna_path="retirement.client.retirementAge",
        status="stated",
        normalization=one.normalization,
        provenance=Provenance(run_id="r"),
    )
    (pair,) = EditPair.from_fact(fact, Quantity(dist=Point(value=62), unit="age"))
    assert (pair.section, pair.key) == ("age_bands", "early sixties")
    two = normalize_field(
        field(
            [
                RawHypothesis(
                    intermediate=AgeForm(form="vague", value=0, low=0, high=0, vague_word="early sixties"),
                    weight=0,
                    rationale="",
                    revises=False,
                ),
                RawHypothesis(
                    intermediate=AgeForm(form="vague", value=0, low=0, high=0, vague_word="mid sixties"),
                    weight=0,
                    rationale="",
                    revises=True,
                ),
            ]
        ),
        RET_AGE,
        None,
        LEX,
    )
    both = fact.model_copy(update={"normalization": two.normalization})
    assert EditPair.from_fact(both, Quantity(dist=Point(value=62), unit="age")) == []
