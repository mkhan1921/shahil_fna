"""Lexicon re-fit from advisor edits (spec §11.4 item 2), on synthetic edits with known truth."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from advisor_intel.lexicon import Lexicon, load_lexicon, normalize_value
from advisor_intel.lexicon.fit import (
    EditPair,
    bump_version,
    fit_beta,
    fit_discrete,
    fit_normal,
    fit_symmetric_triangular,
    fit_triangular,
    observation,
    propose_lexicon_fit,
    replace_entry,
)
from advisor_intel.lexicon.golden import load_cases
from advisor_intel.models.dists import Bernoulli, Categorical, Point, Quantity
from advisor_intel.models.extraction import AgeForm, AmountApprox, AmountRelative, AmountVague, BooleanForm, CategoryForm, ProbabilityEvent, RateForm
from advisor_intel.models.facts import Fact, Provenance

from .lexicon_support import make_slot

RNG = np.random.default_rng(20261016)
LEX = load_lexicon()
GOLDEN = load_cases(Path(__file__).parent / "lexicon_cases.yaml")


def approx_pairs(word: str, halfwidth: float, n: int) -> list[EditPair]:
    out = []
    for r in RNG.triangular(-halfwidth, 0, halfwidth, n):
        base = float(RNG.choice([42_000, 120_000, 2_500_000]))
        form = AmountApprox(value=base, approx_word=word, currency="ZAR", period="once", basis="na")
        out.append(EditPair(phrase=word, form=form, edited=Point(value=base * (1 + r))))
    return out


def relative_pairs(word: str, tri: tuple[float, float, float], n: int) -> list[EditPair]:
    form = AmountRelative(reference_slot="expenses.total", relation_word=word)
    return [EditPair(phrase=word, form=form, edited=Point(value=30_000 * m), reference_median=30_000) for m in RNG.triangular(*tri, n)]


def age_pairs(word: str, tri: tuple[float, float, float], n: int) -> list[EditPair]:
    form = AgeForm(form="vague", value=0, low=0, high=0, vague_word=word)
    return [EditPair(phrase=word, form=form, edited=Point(value=a)) for a in RNG.triangular(*tri, n)]


def test_estimators_recover_known_parameters():
    assert fit_symmetric_triangular(RNG.triangular(-0.2, 0, 0.2, 2000)) == pytest.approx(0.2, abs=0.01)
    a, c, b = fit_triangular(RNG.triangular(1, 2, 5, 2000))
    assert (a, c, b) == pytest.approx((1, 2, 5), abs=0.15)
    mean, sd = fit_normal(RNG.normal(0.05, 0.02, 2000))
    assert (mean, sd) == pytest.approx((0.05, 0.02), abs=0.002)
    m, lo, hi = fit_beta(RNG.beta(8, 2, 2000))
    assert m == pytest.approx(0.8, abs=0.01) and lo < m < hi
    assert fit_discrete([2, 2, 2, 3, 2.2], [2, 3]) == pytest.approx([4.5 / 6, 1.5 / 6])


def test_estimators_handle_degenerate_edits():
    assert fit_symmetric_triangular([0.0] * 30, floor=0.005) == 0.005  # the advisor never moved the value
    assert fit_triangular([61.0] * 25, floor=0.25) == (60.75, 61.0, 61.25)
    assert fit_beta([0.4] * 21) == pytest.approx((0.4, 0.35, 0.45))


def test_refit_recovers_synthetic_truth_for_every_family():
    pairs = approx_pairs("about", 0.2, 300)
    pairs += relative_pairs("a bit less", (0.6, 0.75, 0.9), 300)
    pairs += age_pairs("early sixties", (59, 61, 64), 300)
    pairs += [EditPair(phrase="growing a bit", form=RateForm(form="vague", value=0, vague_word="growing a bit", real_or_nominal="real"),
                       edited=Point(value=r)) for r in RNG.normal(0.05, 0.02, 200)]  # fmt: skip
    pairs += [EditPair(phrase="might", form=ProbabilityEvent(event="study offshore", likelihood_word="might"), edited=Bernoulli(p=float(p)))
              for p in RNG.beta(6, 6, 200)]  # fmt: skip
    d = propose_lexicon_fit(pairs, LEX)
    new = {(c.section, c.key): c.new for c in d.changes}
    assert new[("approx_words", "about")]["rel_halfwidth"] == pytest.approx(0.2, abs=0.02)
    rel = new[("relative_words", "a bit less")]
    assert (rel["low"], rel["mode"], rel["high"]) == pytest.approx((0.6, 0.75, 0.9), abs=0.05)
    band = new[("age_bands", "early sixties")]
    assert (band["low"], band["mode"], band["high"]) == pytest.approx((59, 61, 64), abs=0.5)
    rate = new[("rate_words", "growing a bit")]
    assert (rate["mean"], rate["sd"]) == pytest.approx((0.05, 0.02), abs=0.005)
    assert new[("likelihood_words", "might")]["mean"] == pytest.approx(0.5, abs=0.03)
    for c in d.changes:
        assert c.mean_loglik_new is None or c.mean_loglik_old is None or c.mean_loglik_new >= c.mean_loglik_old - 1e-9


def test_proposed_yaml_is_a_minimal_valid_diff_and_the_lexicon_is_untouched():
    before_text, before_about = LEX.source_text, LEX.entry("approx_words", "about")
    d = propose_lexicon_fit(approx_pairs("about", 0.2, 60) + relative_pairs("a bit less", (0.6, 0.75, 0.9), 40), LEX)
    assert d.changed and d.base_version == "1.3" and d.new_version == "1.4" and d.new_path == "lexicon/v1.4.yaml"
    proposed = Lexicon.from_text(d.yaml_text)
    assert proposed.version == "1.4"
    assert proposed.entry("approx_words", "about").rel_halfwidth == pytest.approx(d.changes[0].new["rel_halfwidth"], rel=1e-3)
    old_lines, new_lines = LEX.source_text.splitlines(), d.yaml_text.splitlines()
    changed = [(a, b) for a, b in zip(old_lines, new_lines) if a != b]
    assert len(old_lines) == len(new_lines) and len(changed) == 3  # version + two entries; comments and order kept
    assert any(b.startswith('  "about": {dist: triangular, rel_halfwidth: ') and "re-fit in v1.4 from 60 advisor edits" in b for _, b in changed)
    assert "-version: \"1.3\"" in d.diff_text and "+version: \"1.4\"" in d.diff_text
    assert "a bit less" in d.summary() and "v1.3 → v1.4" in d.summary()
    assert LEX.source_text == before_text and LEX.entry("approx_words", "about") == before_about
    assert load_lexicon().entry("approx_words", "about").rel_halfwidth == 0.10
    assert len(LEX.miss_log) == 0  # re-looking phrases up never touches the shared miss log


def test_keys_need_twenty_pairs_and_points_stay_fixed():
    pairs = approx_pairs("roughly", 0.3, 19) + approx_pairs("exactly", 0.05, 25)
    d = propose_lexicon_fit(pairs, LEX)
    assert not d.changed and d.new_version == "1.3" and d.diff_text == ""
    assert d.skipped == {"approx_words: roughly": 19} and d.not_fitted == {"approx_words: exactly": 25}
    assert propose_lexicon_fit(approx_pairs("roughly", 0.3, 20), LEX).changed
    assert propose_lexicon_fit(approx_pairs("roughly", 0.3, 12), LEX, min_pairs=10).changed


def test_phrases_without_an_exact_key_are_reported_for_review():
    form = AmountRelative(reference_slot="x", relation_word="a fair bit less")
    pairs = [EditPair(phrase="a fair bit less", form=form, edited=Point(value=0.8), reference_median=1.0)] * 3
    d = propose_lexicon_fit(pairs, LEX)
    assert d.unmatched == {"a fair bit less": 3} and not d.changed


def test_composite_vague_amounts_fit_the_count_key():
    form = AmountVague(vague_word="a few hundred K", currency="ZAR", period="annual", basis="na")
    pairs = [EditPair(phrase="a few hundred K", form=form, edited=Point(value=v * 1000)) for v in RNG.triangular(250, 350, 600, 40)]
    obs = observation(pairs[0], LEX)
    assert obs[:2] == ("vague_counts", "a few hundred") and 250 <= obs[2] <= 600
    d = propose_lexicon_fit(pairs, LEX)
    fitted = d.changes[0]
    assert (fitted.section, fitted.key) == ("vague_counts", "a few hundred") and fitted.new["mode"] > 300


def test_certainty_and_discrete_entries_refit():
    cat = [EditPair(phrase="fairly", form=CategoryForm(label="conservative", certainty_word="fairly"), category="conservative",
                    edited=Categorical(probs={"conservative": p, "moderately-conservative": 1 - p})) for p in RNG.uniform(0.7, 0.9, 25)]  # fmt: skip
    boolean = [EditPair(phrase="I think", form=BooleanForm(value=False, certainty_word="I think"), edited=Bernoulli(p=0.1)) for _ in range(25)]
    couple = [EditPair(phrase="a couple", form=AmountVague(vague_word="a couple", currency="ZAR", period="none", basis="na"), edited=Point(value=v))
              for v in [2.0] * 10 + [3.0] * 10]  # fmt: skip
    d = propose_lexicon_fit(cat + boolean + couple, LEX)
    new = {(c.section, c.key): c.new for c in d.changes}
    assert new[("certainty_words", "fairly")]["value"] == pytest.approx(0.8, abs=0.03)
    assert new[("certainty_words", "i think")]["value"] == pytest.approx(0.9)
    assert new[("vague_counts", "a couple")]["weights"] == pytest.approx([0.5, 0.5])
    assert Lexicon.from_text(d.yaml_text).data.certainty_words["fairly"] == pytest.approx(0.8, abs=0.03)


def test_pairs_from_reviewed_facts_undo_the_unit_conversion():
    slot = make_slot("income.{p}.grossMonthly", "rand_monthly")
    form = {"type": "AMOUNT_APPROX", "value": 600_000, "approx_word": "about", "currency": "ZAR", "period": "annual", "basis": "gross"}
    n = normalize_value(form, slot, None, LEX)
    f = Fact(fact_id="income.client.grossMonthly", slot_id=slot.slot_id, fna_path="income.client.grossMonthly", status="stated", value=n.quantity,
             normalization=n.normalization, provenance=Provenance(run_id="r"))  # fmt: skip
    pairs = EditPair.from_fact(f, Quantity(dist=Point(value=55_000), unit="ZAR", period="monthly"))
    assert len(pairs) == 1 and (pairs[0].section, pairs[0].key) == ("approx_words", "about")
    assert observation(pairs[0], LEX)[2] == pytest.approx(0.10)  # R55,000 a month is 10% above 'about R600,000 a year'


def test_pairs_from_facts_for_relative_and_duration_forms():
    slot = make_slot("goals[].targetYear", "year", entity_type="goal")
    form = {"type": "DURATION_FROM_ANCHOR", "form": "vague", "value": 0, "vague_word": "in a few years", "unit": "years", "anchor": "call"}
    from datetime import date

    n = normalize_value(form, slot, None, LEX, anchor_date=date(2026, 10, 16))
    f = Fact(fact_id="goals[].targetYear#goal.car", slot_id=slot.slot_id, fna_path="goals[x].targetYear", status="stated", value=n.quantity,
             normalization=n.normalization, provenance=Provenance(run_id="r"))  # fmt: skip
    (pair,) = EditPair.from_fact(f, Point(value=2030))
    assert observation(pair, LEX) == ("vague_counts", "a few", 4.0)


def test_golden_cases_the_proposal_would_move_are_listed():
    d = propose_lexicon_fit(age_pairs("early sixties", (70, 72, 75), 40), LEX, golden_cases=GOLDEN)  # 72 is > 10% above 61.6
    assert any("'early sixties'" in m for m in d.golden_moved)
    assert "Golden cases to update" in d.summary()


def test_yaml_helpers():
    assert bump_version("1.3") == "1.4" and bump_version("2.9") == "2.10"
    with pytest.raises(KeyError):
        replace_entry(LEX.source_text, "approx_words", "no such key", "{dist: point}", "x")
    text = replace_entry(LEX.source_text, "hedge_strength", "maybe", "0.55", "note")
    assert '  "maybe": 0.55  # note' in text and Lexicon.from_text(text).data.hedge_strength["maybe"] == 0.55
    assert Lexicon.from_text(text).hedge_lookup("maybe").value == 0.55
