"""Numeric and product spans over transcript words (§5.3): lattice readings, ITN candidate, exclusions, flags."""

from __future__ import annotations

from datetime import date

import pytest

from advisor_intel.ingest.grammars import ProductName
from advisor_intel.ingest.spans import annotate_transcript, canonical_text, segment_spans
from advisor_intel.models.transcript import Segment, Transcript, Word


def seg(sid: str, speaker: str, start: float, items, step: float = 0.35) -> Segment:
    """items: text, or (text, conf), or (text, conf, alts), or (text, conf, alts, raw_spoken)."""
    words = []
    t = start
    for it in items:
        it = (it,) if isinstance(it, str) else it
        text, conf, alts, raw = (list(it) + [0.95, [], None][len(it) - 1 :])[:4]
        words.append(Word(text=text, t_start=round(t, 3), t_end=round(t + step - 0.05, 3), conf=conf, alts=alts, raw_spoken=raw))
        t += step
    return Segment(
        segment_id=sid,
        speaker_label=speaker,
        t_start=words[0].t_start,
        t_end=words[-1].t_end,
        words=words,
        text=" ".join(w.text for w in words),
    )


def transcript(*segments: Segment) -> Transcript:
    return Transcript(call_id="c1", anchor_date=date(2026, 10, 16), segments=list(segments), asr_provider="scripted", asr_model_version="t")


def test_amount_age_and_product_spans_with_readings_and_candidates():
    s = seg("s0001", "spk_1", 0.0, ["We", "put", "two", "fifty", "into", "the", "Allan", "Gray", "RA", "and", "I'm", "fifty", "two"])
    t = annotate_transcript(transcript(s))
    kinds = [(n.span_id, n.kind, n.surface) for n in t.numeric_spans]
    assert kinds == [("n0001", "amount", "two fifty"), ("n0002", "product", "Allan Gray RA"), ("n0003", "age", "fifty two")]
    amount = t.numeric_spans[0]
    assert {r.value for r in amount.readings} == {250.0, 250_000.0, 2_500_000.0}
    assert "scale_ambiguous" in amount.flags and amount.accepted and amount.mean_conf == pytest.approx(0.95)
    words = t.segments[0].words
    assert words[2].numeric_candidates == words[3].numeric_candidates and set(words[2].numeric_candidates) == {
        250.0,
        250_000.0,
        2_500_000.0,
    }
    assert words[0].numeric_candidates == []
    assert t.numeric_spans[1].readings[0].provider == "Allan Gray"
    assert t.numeric_spans[2].readings[0].value == 52.0
    assert s.words[2].numeric_candidates == []  # the input transcript is not mutated


def test_alternatives_enter_the_lattice():
    s = seg("s0001", "spk_1", 0.0, ["I", "pay", ("for", 0.6, [("four", 0.3)]), "thousand", "rand", "for", "it"])
    spans = segment_spans(s)
    assert len(spans) == 1 and spans[0].surface == "for thousand rand"
    assert spans[0].readings[0].value == 4000.0 and spans[0].readings[0].currency == "ZAR"
    # A weak homophone alternative on its own does not start a span.
    lone = seg("s0002", "spk_1", 0.0, ["I", "went", ("for", 0.95, [("four", 0.08)]), "a", "walk"])
    assert segment_spans(lone) == []


def test_misheard_number_keeps_both_readings_weighted_by_asr():
    s = seg("s0001", "spk_1", 0.0, ["it's", "two", ("fifteen", 0.55, [("fifty", 0.4)]), "a", "month"])
    span = segment_spans(s)[0]
    w = {r.value: r.weight for r in span.readings}
    assert w[215.0] > w[250.0] > 0 and w[215_000.0] > w[250_000.0]
    assert all(r.period == "monthly" for r in span.readings)


def test_provider_itn_is_one_more_candidate_never_alone():
    s = seg("s0001", "spk_1", 0.0, ["we", ("R250 000", 0.9, [], "two hundred and fifty thousand rand"), "a", "month"])
    span = segment_spans(s)[0]
    assert span.provider_itn == "R250 000 a month" and span.accepted
    assert [r.value for r in span.readings] == [250_000.0] and span.readings[0].weight == pytest.approx(1.0)
    # The spoken form is garbled: only the ITN parses. The span is kept with the ITN reading but not accepted.
    bad = seg("s0002", "spk_1", 0.0, ["about", ("R1,2m", 0.5, [], "won coma too mill")])
    span2 = segment_spans(bad)[0]
    assert not span2.accepted and "low_asr" in span2.flags
    assert span2.readings and span2.readings[0].value == 1_200_000.0


def test_unaccepted_numeric_stretch_is_flagged():
    s = seg("s0001", "spk_1", 0.0, ["two", "fifty", "thousand", "million", "rand", "okay"])
    spans = segment_spans(s)
    assert [(n.surface, n.accepted) for n in spans] == [("two fifty thousand", True), ("million", False)]
    assert spans[1].flags == {"low_asr"} and spans[1].readings == []


def test_a_run_of_two_amounts_is_cut_where_the_numbers_are_whole():
    # "a hundred thousand and fifty five thousand" with "fifty" heard as "fifteen": two amounts, not 100,055 and an orphan.
    s = seg(
        "s0001",
        "spk_1",
        0.0,
        ["income", "of", "a", "hundred", "thousand", "and", ("fifteen", 0.5, [("fifty", 0.4)]), "five", "thousand", "gross"],
    )
    assert [(n.surface, n.readings[0].value) for n in segment_spans(s)] == [
        ("a hundred thousand", 100_000.0),
        ("fifteen five thousand", 55_000.0),
    ]
    # A weak homophone at the edge does not stretch a span: "for twenty five thousand" is 25,000, not 425,000.
    s2 = seg("s0002", "spk_1", 0.0, ["a", "funeral", "policy", ("for", 0.93, [("four", 0.05)]), "twenty", "five", "thousand"])
    assert [(n.surface, n.readings[0].value) for n in segment_spans(s2) if n.kind == "amount"] == [("twenty five thousand", 25_000.0)]
    # Three digits read one by one stay one number.
    s3 = seg("s0003", "spk_1", 0.0, ["a", "preservation", "fund", "of", "one", "eight", "four"])
    assert {r.value for r in segment_spans(s3)[-1].readings} == {184.0, 184_000.0, 1_840_000.0}


@pytest.mark.parametrize(
    "text",
    [
        "How old are the kids now?",
        "Oh ja, that's right",
        "it's investment grade paper",
        "we bought it in twenty nineteen",
        "back in 2019 we moved",
        "we have two kids at school",
        "there are eight years left on it",
        "let's meet at two thirty on Friday",
        "the call is at two pm",
        "a bar of chocolate",
    ],
)
def test_years_counts_durations_and_times_are_not_amounts(text: str):
    assert segment_spans(seg("s0001", "spk_0", 0.0, text.split())) == []


@pytest.mark.parametrize(
    "text,kind,value",
    [
        ("the bond is sitting at two fifteen", "amount", 215.0),
        ("I want to retire at sixty five", "age", 65.0),
        ("she is turning sixty next year", "age", 60.0),
        ("my son is fourteen", "age", 14.0),
        ("and Kagiso is seven now", "age", 7.0),
        ("I'm only thirty seven", "age", 37.0),
        ("The RA is fifty", "amount", 50.0),
        ("he is in grade nine now", "age", 15.0),
        ("I earn about forty two", "amount", 42.0),
        ("the rate is eleven and a half percent", "amount", 11.5),
    ],
)
def test_kind_follows_the_words_around_the_number(text: str, kind: str, value: float):
    spans = [s for s in segment_spans(seg("s0001", "spk_0", 0.0, text.split())) if s.kind != "product"]
    assert spans and spans[0].kind == kind and spans[0].readings[0].value == value


def test_crosstalk_over_a_span_and_client_product_names():
    a = seg("s0001", "spk_0", 0.0, ["and", "what", "is", "the", "Highveld", "Logistics", "Pension", "Fund", "worth"])
    b = seg("s0002", "spk_1", 1.4, ["about", "one", "point", "three", "million"], step=0.3)
    t = annotate_transcript(
        transcript(a, b), extra_products=[ProductName("Highveld Logistics Pension Fund", "pension", "Employer umbrella fund")]
    )
    prod = next(n for n in t.numeric_spans if n.kind == "product")
    assert prod.surface == "Highveld Logistics Pension Fund" and prod.readings[0].lexicon_key == "client"
    amount = next(n for n in t.numeric_spans if n.kind == "amount")
    assert amount.readings[0].value == 1_300_000.0 and "crosstalk" in amount.flags


def test_canonical_text_for_alignment():
    assert (
        canonical_text("we put two hundred and fifty thousand in the Allan Gray RA") == "we put 250000 in the retirement-annuity@Allan Gray"
    )
    assert canonical_text("R250 000 a month") == "250000"
    assert canonical_text("nothing numeric here") == "nothing numeric here"
