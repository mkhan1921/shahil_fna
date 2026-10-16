"""Segmentation (§5.2 steps 5 and 7): pauses, the 30 s limit, crosstalk and low-ASR flags."""

from __future__ import annotations

from itertools import pairwise

import pytest

from advisor_intel.ingest.segmentation import LabeledWord, crosstalk_seconds, segment_words
from advisor_intel.ingest.types import SpeakerTurn
from advisor_intel.models.transcript import Word


def words(speaker: str, start: float, texts: list[str], *, step: float = 0.4, dur: float = 0.3, conf: float = 0.95, dconf: float = 1.0):
    out = []
    t = start
    for text in texts:
        out.append(LabeledWord(Word(text=text, t_start=round(t, 3), t_end=round(t + dur, 3), conf=conf), speaker, dconf))
        t += step
    return out


def test_merges_a_speaker_and_splits_at_long_pauses():
    lw = words("spk_0", 0.0, ["so", "tell", "me", "about"]) + words("spk_0", 1.5 + 1.3, ["the", "house"])  # gap 1.3 s: split
    lw += words("spk_0", 5.0, ["and", "bond"]) + words("spk_0", 5.7 + 1.1, ["please"])  # gap 1.1 s: same segment
    segs = segment_words(lw)
    assert [s.text for s in segs] == ["so tell me about", "the house", "and bond please"]
    assert [s.segment_id for s in segs] == ["s0001", "s0002", "s0003"]
    assert segs[0].t_start == 0.0 and segs[0].t_end == pytest.approx(1.5)


def test_another_speakers_turn_in_a_short_gap_splits_the_run():
    lw = words("spk_0", 0.0, ["what", "is", "it", "worth"]) + words("spk_1", 1.7, ["two", "point", "five"], step=0.25, dur=0.2)
    lw += words("spk_0", 2.5, ["okay", "thanks"])
    segs = segment_words(lw)
    assert [(s.speaker_label, s.text) for s in segs] == [
        ("spk_0", "what is it worth"),
        ("spk_1", "two point five"),
        ("spk_0", "okay thanks"),
    ]


def test_long_runs_split_before_30_seconds_at_a_sentence_end_not_inside_a_number():
    texts = []
    for k in range(20):
        texts += ["we", "spend", "about", "two", "thousand", "rand."] if k % 3 == 2 else ["and", "then", "we", "also", "pay", "it,"]
    lw = words("spk_0", 0.0, texts, step=0.4)
    segs = segment_words(lw)
    assert len(segs) >= 2
    assert all(s.t_end - s.t_start <= 30.0 for s in segs)
    assert sum(len(s.words) for s in segs) == len(texts)
    assert segs[0].text.endswith("rand.")  # the break falls after a sentence end
    for a, b in pairwise(segs):
        assert not (a.words[-1].text in ("two", "thousand") and b.words[0].text in ("thousand", "rand."))


def test_crosstalk_flag_needs_more_than_300ms_of_overlap():
    lw = words("spk_0", 0.0, ["and", "the", "monthly", "contribution", "is"], step=0.4)
    lw += words("spk_1", 1.2, ["fifteen", "hundred"], step=0.3, dur=0.28)  # overlaps spk_0's words by 0.28 + 0.18 s
    lw += words("spk_0", 10.0, ["right", "and", "the", "bond"])
    lw += words("spk_1", 11.0, ["mm"], dur=0.2)  # 0.1 s overlap: below the threshold
    segs = segment_words(lw)
    flagged = {s.text: "crosstalk" in s.flags for s in segs}
    assert flagged["and the monthly contribution is"] and flagged["fifteen hundred"]
    assert not flagged["right and the bond"] and not flagged["mm"]
    assert crosstalk_seconds(segs) == pytest.approx(0.46 + 0.1, abs=1e-6)


def test_mono_crosstalk_from_overlapping_diarization_turns():
    lw = words("spk_0", 0.0, ["one", "two", "three", "four"]) + words("spk_1", 2.0, ["five", "six"])
    turns = [SpeakerTurn(speaker="spk_0", start=0.0, end=1.9), SpeakerTurn(speaker="spk_1", start=1.4, end=3.0)]
    segs = segment_words(lw, turns=turns)
    assert all("crosstalk" in s.flags for s in segs)


def test_low_asr_flag_and_diarization_confidence():
    lw = words("spk_0", 0.0, ["mumble", "mumble"], conf=0.45, dconf=0.7) + words("spk_1", 3.0, ["clear", "words"], conf=0.95, dconf=0.9)
    segs = segment_words(lw)
    assert "low_asr" in segs[0].flags and "low_asr" not in segs[1].flags
    assert segs[0].diarization_conf == pytest.approx(0.7) and segs[1].diarization_conf == pytest.approx(0.9)
    assert segment_words([]) == []


def test_segments_sorted_by_start_with_stable_ids():
    lw = words("spk_1", 5.0, ["later"]) + words("spk_0", 0.0, ["first"]) + words("spk_2", 2.0, ["middle"])
    segs = segment_words(lw)
    assert [(s.segment_id, s.text) for s in segs] == [("s0001", "first"), ("s0002", "middle"), ("s0003", "later")]
