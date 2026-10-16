"""The vagueness lexicon and its lookups (spec §8.2, §8.3)."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from advisor_intel import stats
from advisor_intel.lexicon import CURRENT_VERSION, Lexicon, load_lexicon
from advisor_intel.lexicon.loader import available_versions
from advisor_intel.lexicon.phrases import canon, parse_number
from advisor_intel.models.dists import Bernoulli, Discrete, Normal, Point, Triangular
from advisor_intel.models.facts import Hedge

from .lexicon_support import FakeEmbedder


@pytest.fixture(scope="module")
def lex() -> Lexicon:
    return load_lexicon()


def fresh(**similar) -> Lexicon:
    """A private lexicon (own miss log) bound to a fake embedder."""
    return Lexicon.from_text(load_lexicon().source_text, embedder=FakeEmbedder(similar))


# The §8.2 seed, copied from the spec: these numbers must stay exactly as given.
SEED_DIST = {
    ("approx_words", "exactly"): {"dist": "point"},
    ("approx_words", "about"): {"dist": "triangular", "rel_halfwidth": 0.10},
    ("approx_words", "roughly"): {"dist": "triangular", "rel_halfwidth": 0.15},
    ("approx_words", "ballpark"): {"dist": "triangular", "rel_halfwidth": 0.25},
    ("approx_words", "give or take"): {"dist": "triangular", "rel_halfwidth": 0.10},
    ("vague_counts", "a couple"): {"dist": "discrete", "support": [2, 3], "weights": [0.75, 0.25]},
    ("vague_counts", "a few"): {"dist": "triangular", "low": 2, "mode": 3, "high": 5},
    ("vague_counts", "several"): {"dist": "triangular", "low": 3, "mode": 5, "high": 8},
    ("vague_counts", "a few hundred"): {"dist": "triangular", "low": 200, "mode": 300, "high": 500},
    ("age_bands", "early sixties"): {"dist": "triangular", "low": 60, "mode": 61.5, "high": 63.5},
    ("age_bands", "mid sixties"): {"dist": "triangular", "low": 63, "mode": 65, "high": 67},
    ("age_bands", "late fifties"): {"dist": "triangular", "low": 57, "mode": 58.5, "high": 59.9},
    ("relative_words", "about the same"): {"dist": "triangular", "low": 0.9, "mode": 1.0, "high": 1.1},
    ("relative_words", "a bit less"): {"dist": "triangular", "low": 0.75, "mode": 0.88, "high": 0.97},
    ("relative_words", "a lot less"): {"dist": "triangular", "low": 0.4, "mode": 0.6, "high": 0.8},
    ("relative_words", "a bit more"): {"dist": "triangular", "low": 1.03, "mode": 1.12, "high": 1.25},
    ("rate_words", "flat"): {"dist": "normal", "mean": 0.0, "sd": 0.02},
    ("rate_words", "growing a bit"): {"dist": "normal", "mean": 0.03, "sd": 0.03},
    ("rate_words", "growing well"): {"dist": "normal", "mean": 0.08, "sd": 0.05},
    ("rate_words", "struggling"): {"dist": "normal", "mean": -0.05, "sd": 0.05},
    ("likelihood_words", "definitely"): {"mean": 0.95, "low": 0.9, "high": 0.99},
    ("likelihood_words", "probably"): {"mean": 0.70, "low": 0.6, "high": 0.8},
    ("likelihood_words", "might"): {"mean": 0.40, "low": 0.25, "high": 0.55},
    ("likelihood_words", "maybe"): {"mean": 0.35, "low": 0.2, "high": 0.5},
    ("likelihood_words", "unlikely"): {"mean": 0.15, "low": 0.05, "high": 0.25},
    ("likelihood_words", "thinking of"): {"mean": 0.55, "low": 0.4, "high": 0.7},
}
SEED_SCALAR = {
    ("hedge_strength", "definitely"): 0.0,
    ("hedge_strength", "i think"): 0.3,
    ("hedge_strength", "probably"): 0.3,
    ("hedge_strength", "thinking of"): 0.5,
    ("hedge_strength", "maybe"): 0.6,
    ("hedge_strength", "might"): 0.6,
    ("hedge_strength", "no idea"): 1.0,
    ("scale_words", "bar"): 1000,
    ("scale_words", "k"): 1000,
    ("scale_words", "mil"): 1000000,
    ("scale_words", "million"): 1000000,
}


def test_loads_once_per_version(lex):
    assert lex.version == CURRENT_VERSION == "1.3"
    assert load_lexicon("1.3") is lex
    assert "1.3" in available_versions()
    with pytest.raises(FileNotFoundError):
        load_lexicon("0.0")


@pytest.mark.parametrize(("section", "key"), list(SEED_DIST))
def test_seed_distribution_entries_are_the_spec_values(lex, section, key):
    assert lex.entry(section, key).model_dump(exclude_none=True) == SEED_DIST[(section, key)]


@pytest.mark.parametrize(("section", "key"), list(SEED_SCALAR))
def test_seed_scalar_entries_are_the_spec_values(lex, section, key):
    assert lex.entry(section, key) == SEED_SCALAR[(section, key)]


def test_seed_recency_weights(lex):
    rw = lex.recency_weights()
    assert (rw.revising, rw.revised) == (0.8, 0.2)


def test_approx_is_a_triangle_around_the_value(lex):
    lk = lex.approx("about", 100_000)
    assert lk.match == "exact" and lk.key == "about" and lk.flag is None
    assert lk.dist == Triangular(low=90_000, mode=100_000, high=110_000)
    # p10 of Triangular(a, c, b) below the mode is a + sqrt(q (b - a)(c - a)).
    assert stats.quantile(lk.dist, 0.1) == pytest.approx(90_000 + math.sqrt(0.1 * 20_000 * 10_000))
    assert lex.approx("exactly", 42_000).dist == Point(value=42_000)
    under = lex.approx("just under", 50_000).dist
    assert under.high == 50_000 and under.low == pytest.approx(46_000) and under.mode == pytest.approx(48_500)


def test_approx_word_with_the_number_quoted(lex):
    lk = lex.approx("about R2.5 million", 2_500_000)
    assert lk.key == "about" and lk.dist == Triangular(low=2_250_000, mode=2_500_000, high=2_750_000)
    assert lex.approx("in the region of", 10).key == "in the region of"  # 'of' is not stripped from a key
    assert lex.approx("", 10).match == "default"


def test_unknown_approx_word_keeps_the_stated_number(lex):
    lk = fresh().approx("zorbly", 50_000)
    assert lk.match == "miss" and lk.flag == "lexicon_miss"
    assert stats.median(lk.dist) == 50_000  # the number stands; only the spread comes from params.approx_default


@pytest.mark.parametrize(
    ("method", "phrase", "key"),
    [
        ("age_band", "Early 60's", "early sixties"),
        ("age_band", "early sixty's", "early sixties"),
        ("age_band", "in his late fifties", "late fifties"),
        ("age_band", "laat vyftigs", "late fifties"),
        ("age_band", "vroeë sestigs", "early sixties"),
        ("age_band", "after a gap year", "after a gap year"),
        ("vague_count", "couple of", "a couple"),
        ("vague_count", "'n paar", "n paar"),
        ("vague_count", "Several", "several"),
        ("relative", "bit less", "a bit less"),
        ("relative", "a bit less than now", "a bit less"),
        ("relative", "About the same as now.", "about the same"),
        ("likelihood", "Definitely!", "definitely"),
        ("likelihood", "fifty-fifty", "fifty fifty"),
        ("likelihood", "50/50", "50 50"),
        ("certainty", "FAIRLY", "fairly"),
        ("hedge_lookup", "I'm not sure", "im not sure"),
        ("hedge_lookup", "Don't quote me", "dont quote me"),
        ("rate", "Growing a bit.", "growing a bit"),
        ("scale", "Bar", "bar"),
        ("scale", "half-a-bar", "half a bar"),
    ],
)
def test_lookups_normalise_case_punctuation_and_variants(lex, method, phrase, key):
    lk = getattr(lex, method)(phrase)
    assert lk.match == "exact", lk
    assert lk.key == key


def test_composite_vague_amounts(lex):
    lk = lex.vague_amount("a few hundred K")
    assert (lk.section, lk.key, lk.multiplier) == ("vague_counts", "a few hundred", 1000)  # the seed key, re-fittable as itself
    assert lk.dist == Triangular(low=200_000, mode=300_000, high=500_000)
    assert lex.vague_amount("a couple of bar").dist == Discrete(support=[2000, 3000], weights=[0.75, 0.25])
    assert lex.vague_amount("a few mil").dist == Triangular(low=2e6, mode=3e6, high=5e6)
    assert lex.vague_amount("half a bar").dist == Point(value=500)
    assert lex.vague_amount("a bar and a half").dist == Point(value=1500)
    assert lex.vague_amount("two and a half mil").dist == Point(value=2.5e6)
    assert lex.vague_amount("two fifty k").dist == Point(value=250_000)
    assert lex.vague_amount("twee honderd duisend").dist == Point(value=200_000)
    assert lex.vague_amount("about two hundred grand").dist == Triangular(low=180_000, mode=200_000, high=220_000)
    assert lex.vague_amount("six figures").key == "six figures"
    assert stats.quantile(lex.vague_amount("six figures").dist, 0.1) == pytest.approx(104_167, rel=1e-3)


def test_vague_counts_and_numbers(lex):
    assert lex.vague_count("a few").dist == Triangular(low=2, mode=3, high=5)
    assert lex.vague_count("five").dist == Point(value=5)
    about5 = lex.vague_count("about five")
    assert (about5.section, about5.key) == ("approx_words", "about") and about5.dist == Triangular(low=4.5, mode=5, high=5.5)
    assert lex.vague_count("a couple of hundred").dist == Discrete(support=[200, 300], weights=[0.75, 0.25])


@pytest.mark.parametrize(
    ("phrase", "dist"),
    [
        ("turning sixty", Point(value=60)),
        ("when I turn sixty", Point(value=60)),
        ("62 years old", Point(value=62)),
        ("op sestig", Point(value=60)),
        ("before fifty-five", Triangular(low=50, mode=53.5, high=55)),
        ("voor vyf-en-vyftig", Triangular(low=50, mode=53.5, high=55)),
        ("sixty-ish", Triangular(low=58, mode=60, high=62)),
        ("roughly sixty", Triangular(low=57, mode=60, high=63)),
        ("grade nine", Triangular(low=14.5, mode=15, high=15.5)),
        ("after matric", Triangular(low=17, mode=18, high=19)),
        ("in her mid-30s", Triangular(low=33, mode=35, high=37)),
    ],
)
def test_age_bands_and_templates(lex, phrase, dist):
    assert lex.age_band(phrase).dist == dist


def test_age_approx_spread_scales_with_the_word(lex):
    # 'about sixty' is ±2 years; 'ballpark' is 2.5× as wide as 'about' in approx_words, so ±5 years.
    assert lex.age_band("about sixty").dist == Triangular(low=58, mode=60, high=62)
    bp = lex.age_band("ballpark sixty")
    assert (bp.section, bp.key) == ("approx_words", "ballpark")
    assert bp.dist == Triangular(low=55, mode=60, high=65)


def test_relative_rate_and_likelihood(lex):
    assert lex.relative("a bit less").dist == Triangular(low=0.75, mode=0.88, high=0.97)
    assert lex.relative("eighty percent of now").dist == Point(value=0.8)
    assert lex.rate("growing a bit").dist == Normal(mean=0.03, sd=0.03)
    assert lex.rate("inflation plus two").dist == Normal(mean=0.02, sd=0.01)
    assert lex.rate("5%").dist == Point(value=0.05)
    might = lex.likelihood("might")
    assert (might.likelihood.mean, might.likelihood.low, might.likelihood.high) == (0.40, 0.25, 0.55)
    assert might.dist == Bernoulli(p=0.4)
    assert lex.likelihood("80% chance").likelihood.mean == pytest.approx(0.8)
    assert lex.likelihood("nine out of ten").likelihood.mean == pytest.approx(0.9)


def test_certainty_and_hedges(lex):
    assert lex.certainty("", kind="boolean").value == 0.97  # §8.3 BOOLEAN with no certainty word
    assert lex.certainty("", kind="category").value == 1.0
    assert lex.certainty("fairly").value == 0.6  # §8.3 'fairly conservative'
    assert lex.certainty("likely", kind="boolean").value == 0.7  # falls back to the likelihood table
    assert lex.certainty("90 percent sure").value == pytest.approx(0.9)
    unknown = fresh().certainty("blorp")
    assert unknown.match == "miss" and unknown.value == 0.75
    assert lex.hedge("thinking of") == Hedge(term="thinking of", strength=0.5, lexicon_key="thinking of")
    h = fresh().hedge("blorp-blorp")
    assert h.strength == 0.5 and h.lexicon_key == ""


def test_near_miss_supplies_the_nearest_key_at_0_8():
    lex = fresh(**{"a fair bit less": {"a bit less": 0.85}, "a hair less": {"a bit less": 0.79}})
    near = lex.relative("a fair bit less")  # the spec's own example (§8.3)
    assert (near.match, near.key, near.flag) == ("near_miss", "a bit less", "lexicon_near_miss")
    assert near.similarity == pytest.approx(0.85, abs=1e-5)
    assert near.dist == Triangular(low=0.75, mode=0.88, high=0.97)
    below = lex.relative("a hair less")
    assert (below.match, below.flag, below.dist) == ("miss", "lexicon_miss", None)  # caller falls back to the population prior
    assert below.similarity == pytest.approx(0.79, abs=1e-5)


def test_near_miss_never_crosses_polarity_or_negation():
    lex = fresh(**{"a fair bit more": {"a bit less": 0.95}, "loads more": {"a lot more": 0.9}, "not likely": {"likely": 0.95}})
    assert lex.relative("a fair bit more").match == "miss"  # 'less' never stands in for 'more'
    assert lex.relative("loads more").key == "a lot more"
    assert lex.likelihood("not likely").match == "miss"


def test_near_miss_with_the_default_hashed_embedder():
    lex = Lexicon.from_text(load_lexicon().source_text)
    lk = lex.relative("quite a bit less")
    assert lk.match == "near_miss" and lk.key == "a bit less" and lk.similarity >= 0.8


def test_miss_log_keeps_phrases_for_the_weekly_review():
    lex = fresh(**{"a fair bit less": {"a bit less": 0.85}})
    lex.relative("a fair bit less")
    lex.vague_amount("a decent whack")
    lex.vague_amount("A decent whack!")
    lex.relative("a bit less")  # an exact hit is not logged
    entries = lex.miss_log.entries()
    assert [(e.section, e.phrase, e.match) for e in entries] == [
        ("relative_words", "a fair bit less", "near_miss"),
        ("vague_amounts", "a decent whack", "miss"),
        ("vague_amounts", "A decent whack!", "miss"),
    ]
    assert entries[0].nearest_key == "a bit less"
    assert lex.miss_log.counts()[("vague_amounts", "a decent whack")] == 2
    assert len(lex.miss_log.drain()) == 3 and len(lex.miss_log) == 0


def test_with_embedder_shares_data_and_log():
    base = Lexicon.from_text(load_lexicon().source_text)
    bound = base.with_embedder(FakeEmbedder({}))
    assert bound.data is base.data and bound.miss_log is base.miss_log
    bound.age_band("zzz")
    assert len(base.miss_log) == 1
    via_load = load_lexicon(embedder=FakeEmbedder({}))
    assert via_load.data is load_lexicon().data and via_load.embedder.name == "fake-test"


def test_lexicon_cannot_be_mutated(lex):
    with pytest.raises(TypeError):
        lex.sections["approx_words"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        lex.sections["approx_words"].entries["about"] = None  # type: ignore[index]
    with pytest.raises(ValidationError):
        lex.entry("approx_words", "about").rel_halfwidth = 0.5


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('"a few": {dist: triangular, low: 2, mode: 3, high: 5}', '"a few": {dist: triangular, low: 4, mode: 3, high: 5}', "low <= mode"),
        ("recency_weight: {revising: 0.8, revised: 0.2}", "recency_weight: {revising: 0.8, revised: 0.3}", "sum to 1"),
        ('"maybe": 0.6          # seed', '"maybe": 1.6', r"in \[0, 1\]"),
        ('"about": {dist: triangular, rel_halfwidth: 0.10}', '"about": {dist: triangular}', "rel_halfwidth"),
    ],
)
def test_invalid_lexicons_are_rejected(old, new, message):
    text = load_lexicon().source_text
    assert old in text
    with pytest.raises(ValidationError, match=message):
        Lexicon.from_text(text.replace(old, new, 1))


def test_keys_that_collide_after_canonicalisation_are_rejected():
    text = load_lexicon().source_text.replace('"around": {dist', '"Give-or-take": {dist', 1)
    with pytest.raises(ValueError, match="same after canonicalisation"):
        Lexicon.from_text(text)


def test_ordered_scales_and_synonyms(lex):
    assert lex.ordered_scale(["yes", "no", "unsure"]) == ["no", "unsure", "yes"]
    assert lex.ordered_scale(["3", "1", "2", "5", "4"]) == ["1", "2", "3", "4", "5"]
    risk = ["aggressive", "moderate", "conservative", "moderately-aggressive", "moderately-conservative"]
    assert lex.ordered_scale(risk) == ["conservative", "moderately-conservative", "moderate", "moderately-aggressive", "aggressive"]
    assert lex.ordered_scale(["salaried", "retired", "student"]) is None
    assert lex.category_synonym("Cautious", risk) == "moderately-conservative"
    assert lex.category_synonym("business", ["primary-residence", "business-interest", "other"]) == "business-interest"
    assert lex.category_synonym("business", ["estate", "spouse", "business"]) == "business"
    assert lex.category_synonym("bakkie", ["vehicle", "cash"]) == "vehicle"
    assert lex.category_synonym("bakkie", ["cash"]) is None


def test_phrases_canonicalise_and_parse_numbers():
    assert canon("Plus-minus R5 000") == "plus minus 5000"
    assert canon("Early 60's") == canon("early sixties") == "early sixties"
    assert canon("R50k") == "50 k" and canon("sixtyish") == "sixty ish" and canon("Clientèle") == "clientele"
    for text, n in [("sixty-five", 65), ("vyf-en-sestig", 65), ("vyfensestig", 65), ("a hundred and twenty", 120), ("two thousand five hundred", 2500),
                    ("two point five", 2.5), ("two and a half", 2.5), ("two fifty", 250), ("nineteen eighty five", 1985), ("twenty twenty", 2020),
                    ("twee honderd", 200)]:  # fmt: skip
        assert parse_number(text) == n, text
    assert parse_number("about") is None and parse_number("sixty about") is None and parse_number("five sixty five sixty") is None
