"""Stage 1 metrics (§15.3): numeric-token WER, DER with a collar, speaker-role accuracy, numeric-span accuracy."""

from __future__ import annotations

from datetime import date

import pytest

from advisor_intel.ingest.metrics import (
    canonical_tokens,
    der,
    label_role_accuracy,
    numeric_span_accuracy,
    numeric_wer,
    speaker_role_accuracy,
)
from advisor_intel.ingest.spans import annotate_transcript
from advisor_intel.ingest.types import SpeakerTurn
from advisor_intel.models.transcript import Segment, Transcript, Word

REF = "I take home about forty two thousand a month and the RA with Allan Gray has two hundred and fifty thousand in it."


def test_canonical_tokens_spell_out_written_numbers():
    assert canonical_tokens("R250 000 in the RA") == ["two", "hundred", "fifty", "thousand", "rand", "in", "the", "ra"]
    assert canonical_tokens("two hundred and fifty thousand rand in the RA") == canonical_tokens("R250 000 in the RA")
    assert canonical_tokens("11.5% a year") == ["eleven", "point", "five", "percent", "a", "year"]


def test_perfect_and_itn_style_transcripts_score_zero():
    assert numeric_wer(REF, REF).wer == 0.0
    itn = "I take home about 42 000 a month and the RA with Allan Gray has 250 000 in it."
    r = numeric_wer(REF, itn)
    assert r.wer == 0.0 and r.targets >= 12


def test_only_numeric_and_financial_errors_count():
    misheard = REF.replace("forty two", "fourteen two")
    r = numeric_wer(REF, misheard)
    assert r.substitutions == 1 and r.deletions == 0 and r.wer == pytest.approx(1 / r.targets)
    assert ("sub", "forty", "fourteen") in r.errors
    other = REF.replace("take home", "bake foam").replace("in it", "on it")
    r2 = numeric_wer(REF, other)
    assert r2.wer == 0.0 and r2.overall_wer > 0
    lost = REF.replace("with Allan Gray ", "")
    r3 = numeric_wer(REF, lost)
    assert r3.deletions == 3  # "with" is a connector inside the product span; "allan", "gray"
    extra = numeric_wer("the bond", "the bond two thousand")
    assert extra.insertions == 2 and extra.targets == 1


def test_der_identical_swapped_and_partial():
    ref = [SpeakerTurn(speaker="A", start=0, end=10), SpeakerTurn(speaker="B", start=10, end=20)]
    assert der(ref, ref).der == 0.0
    swapped = [SpeakerTurn(speaker="spk_1", start=0, end=10), SpeakerTurn(speaker="spk_0", start=10, end=20)]
    r = der(ref, swapped)
    assert r.der == 0.0 and r.mapping == {"A": "spk_1", "B": "spk_0"}
    wrong = [SpeakerTurn(speaker="X", start=0, end=15), SpeakerTurn(speaker="Y", start=15, end=20)]
    r2 = der(ref, wrong, collar=0.0)
    assert r2.confusion == pytest.approx(5.0, abs=0.02) and r2.der == pytest.approx(0.25, abs=0.002)
    missed = der(ref, [SpeakerTurn(speaker="X", start=0, end=10)], collar=0.0)
    assert missed.missed == pytest.approx(10.0, abs=0.02) and missed.der == pytest.approx(0.5, abs=0.002)
    fa = der(ref, ref + [SpeakerTurn(speaker="C", start=20, end=25)], collar=0.0)
    assert fa.false_alarm == pytest.approx(5.0, abs=0.02)


def test_der_collar_forgives_boundary_jitter_and_overlap_can_be_skipped():
    ref = [SpeakerTurn(speaker="A", start=0, end=10), SpeakerTurn(speaker="B", start=10, end=20)]
    jitter = [SpeakerTurn(speaker="A", start=0.2, end=10.2), SpeakerTurn(speaker="B", start=10.2, end=20)]
    assert der(ref, jitter, collar=0.0).der > 0 and der(ref, jitter, collar=0.25).der == 0.0
    overlap_ref = ref + [SpeakerTurn(speaker="B", start=8, end=10)]
    hyp = [SpeakerTurn(speaker="A", start=0, end=10), SpeakerTurn(speaker="B", start=10, end=20)]
    assert der(overlap_ref, hyp, collar=0.0).missed > 0
    assert der(overlap_ref, hyp, collar=0.0, skip_overlap=True).der == 0.0


def _transcript() -> Transcript:
    def seg(sid, label, role, start, texts):
        words = [Word(text=t, t_start=start + 0.4 * k, t_end=start + 0.4 * k + 0.3, conf=0.95) for k, t in enumerate(texts)]
        return Segment(segment_id=sid, speaker_label=label, speaker_role=role, role_conf=0.9, t_start=words[0].t_start, t_end=words[-1].t_end,
                       words=words, text=" ".join(texts))
    segs = [seg("s0001", "spk_0", "advisor", 0.0, ["what", "is", "it", "worth"]),
            seg("s0002", "spk_1", "client_primary", 2.0, ["about", "two", "point", "five", "million"]),
            seg("s0003", "spk_2", "client_primary", 5.0, ["no", "two", "fifty"])]
    return annotate_transcript(Transcript(call_id="c", anchor_date=date(2026, 10, 16), segments=segs, asr_provider="t", asr_model_version="t"))


def test_role_accuracy_by_time_and_by_label():
    t = _transcript()
    gold = [(0.0, 1.5, "advisor"), (2.0, 3.9, "client_primary"), (5.0, 6.1, "client_secondary")]
    acc = speaker_role_accuracy(gold, t)
    assert acc == pytest.approx((1.5 + 1.9) / (1.5 + 1.9 + 1.1), abs=0.02)
    assert label_role_accuracy({"spk_0": "advisor", "spk_1": "client_primary", "spk_2": "client_secondary"},
                               {"spk_0": "advisor", "spk_1": "client_primary", "spk_2": "client_primary"}) == pytest.approx(2 / 3)


def test_numeric_span_accuracy():
    t = _transcript()
    gold = [(2.4, 3.9, 2_500_000.0), (5.4, 6.1, 250_000.0), (30.0, 31.0, 100.0)]
    acc = numeric_span_accuracy(gold, t)
    assert acc.n == 3 and acc.found == pytest.approx(2 / 3)
    two_fifty = next(s for s in t.numeric_spans if s.surface == "two fifty")
    expected_top1 = (1 + (two_fifty.readings[0].value == 250_000.0)) / 3  # 250 and 250k tie; scale resolution decides later
    assert acc.top1 == pytest.approx(expected_top1)
    assert acc.recall == pytest.approx(2 / 3)
