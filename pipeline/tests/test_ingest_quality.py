"""The §5.5 quality report: thresholds, banners, cap_p_correct, role review and the speaker-count failure."""

from __future__ import annotations

import pytest

from advisor_intel.ingest.quality import detected_speakers, quality_report
from advisor_intel.models.transcript import NumericSpan, Reading, Segment, Word


def seg(sid: str, speaker: str, start: float, end: float, conf: float = 0.95, flags=(), role_conf: float = 0.95) -> Segment:
    n = max(1, int((end - start) / 0.4))
    step = (end - start) / n
    words = [Word(text="w", t_start=start + k * step, t_end=start + (k + 1) * step - 0.01, conf=conf) for k in range(n)]
    return Segment(
        segment_id=sid,
        speaker_label=speaker,
        speaker_role="advisor",
        role_conf=role_conf,
        t_start=start,
        t_end=end,
        words=words,
        text=" ".join("w" for _ in words),
        flags=set(flags),
    )


def good_call() -> list[Segment]:
    return [seg("s0001", "spk_0", 0, 10), seg("s0002", "spk_1", 10.5, 20), seg("s0003", "spk_0", 21, 30)]


def test_a_clean_call_passes_every_check():
    q = quality_report(good_call(), role_conf={"spk_0": 0.95, "spk_1": 0.9})
    assert q.mean_word_conf == pytest.approx(0.95) and q.low_asr_share == 0.0 and q.n_speakers == 2
    assert not (q.cap_p_correct or q.role_review_needed or q.failed) and q.banners == []


def test_low_confidence_and_low_asr_time_cap_p_correct():
    segs = [seg("s0001", "spk_0", 0, 10, conf=0.7), seg("s0002", "spk_1", 10, 20, conf=0.75)]
    q = quality_report(segs, role_conf={"spk_0": 0.9, "spk_1": 0.9})
    assert q.cap_p_correct and "mean word confidence 0.72" in q.banners[0]
    segs2 = [seg("s0001", "spk_0", 0, 80), seg("s0002", "spk_1", 80, 100, conf=0.5, flags={"low_asr"})]
    q2 = quality_report(segs2, role_conf={"spk_0": 0.9, "spk_1": 0.9})
    assert q2.low_asr_share == pytest.approx(0.2) and q2.cap_p_correct and "20% of speech" in q2.banners[0]


def test_role_confidence_below_0_8_asks_for_role_review():
    q = quality_report(good_call(), role_conf={"spk_0": 0.95, "spk_1": 0.62})
    assert q.role_review_needed and q.role_conf_min == pytest.approx(0.62) and not q.failed
    # without an explicit map the segments' own role confidences are used
    segs = good_call()
    segs[1] = segs[1].model_copy(update={"role_conf": 0.5})
    assert quality_report(segs).role_conf_min == pytest.approx(0.5)


@pytest.mark.parametrize("n", [1, 5])
def test_speakers_outside_two_to_four_fail_the_run(n: int):
    segs = [seg(f"s{k:04d}", f"spk_{k}", 10 * k, 10 * k + 8) for k in range(n)]
    q = quality_report(segs, role_conf={f"spk_{k}": 0.9 for k in range(n)})
    assert q.failed and q.n_speakers == n and "routed to manual" in q.banners[-1]


def test_a_stray_label_with_under_a_second_of_speech_is_not_a_speaker():
    segs = good_call() + [seg("s0004", "spk_9", 31, 31.4)]
    assert detected_speakers(segs) == ["spk_0", "spk_1"]
    assert not quality_report(segs, role_conf={"spk_0": 0.9, "spk_1": 0.9}).failed


def test_scale_ambiguous_count_unaccepted_spans_and_missing_alternatives():
    spans = [
        NumericSpan(
            span_id="n0001",
            segment_id="s0002",
            kind="amount",
            word_start=0,
            word_end=2,
            surface="two fifty",
            readings=[Reading(value=250.0, weight=0.5), Reading(value=250000.0, weight=0.5)],
            flags={"scale_ambiguous"},
        ),
        NumericSpan(
            span_id="n0002",
            segment_id="s0002",
            kind="amount",
            word_start=3,
            word_end=4,
            surface="million",
            accepted=False,
            flags={"low_asr"},
        ),
    ]
    q = quality_report(good_call(), spans, asr_alternatives=False, role_conf={"spk_0": 0.9, "spk_1": 0.9})
    assert q.scale_ambiguous_spans == 1
    assert any("no word alternatives" in b for b in q.banners) and any("1 number(s)" in b for b in q.banners)


def test_crosstalk_share():
    a = seg("s0001", "spk_0", 0, 10)
    b = seg("s0002", "spk_1", 9, 11)
    q = quality_report([a, b], role_conf={"spk_0": 0.9, "spk_1": 0.9})
    assert 0 < q.crosstalk_share < 0.15
