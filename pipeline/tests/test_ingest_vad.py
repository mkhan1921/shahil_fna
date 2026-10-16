"""VAD (§5.2 step 2): silence and hold music dropped, offsets kept call-relative."""

from __future__ import annotations

import numpy as np
import pytest

from advisor_intel.ingest.vad import SpeechMap, VadConfig, detect_speech, frame_features, tidy_regions, union_regions

from .audio_synth import SR, noise, speech_like, timeline


def _covered(regions, a, b) -> float:
    return sum(max(0.0, min(b, e) - max(a, s)) for s, e in regions) / (b - a)


@pytest.mark.parametrize("seed", range(4))
def test_speech_kept_silence_and_hold_music_dropped(seed: int):
    pieces = [("silence", 2), ("speech", 4), ("silence", 1.5), ("music", 6), ("speech", 3), ("tone", 2), ("speech", 5), ("silence", 2)]
    x, truth = timeline(pieces, seed=seed)
    regions = detect_speech(x, SR)
    for a, b in truth:
        assert _covered(regions, a, b) > 0.9, (a, b, regions)
    assert _covered(regions, 7.5, 13.5) < 0.15  # hold music
    assert _covered(regions, 16.5, 18.5) < 0.05  # a steady tone
    assert _covered(regions, 0.0, 1.7) == 0.0 and _covered(regions, 23.8, 25.5) == 0.0  # silence
    assert all(0 <= s < e <= x.size / SR for s, e in regions)


def test_hangover_bridges_short_pauses_but_not_long_ones():
    a, b = speech_like(2, seed=1), speech_like(2, seed=2)
    short = np.concatenate([a, np.zeros(int(0.2 * SR), np.float32), b]) + noise(4.2, seed=3)
    assert len(detect_speech(short, SR)) == 1
    long = np.concatenate([a, np.zeros(int(1.5 * SR), np.float32), b]) + noise(5.5, seed=3)
    regions = detect_speech(long, SR)
    assert len(regions) == 2 and regions[1][0] > 3.0


def test_frame_features_shapes_and_tidy_rules():
    x = speech_like(3, seed=4) + noise(3, seed=5)
    f = frame_features(x, SR)
    assert f.times.shape == f.energy_db.shape == f.speech.shape
    assert f.speech.mean() > 0.5
    # merge close regions, drop short ones, pad and clamp
    out = tidy_regions([(1.0, 2.0), (2.1, 3.0), (5.0, 5.05), (9.95, 10.0)], duration=10.0, merge_gap=0.3, min_len=0.2, pad=0.1)
    assert out == [(0.9, 3.1)]
    assert detect_speech(np.zeros(0, np.float32), SR) == []


def test_speech_map_round_trips_call_time():
    m = SpeechMap(((2.0, 5.0), (10.0, 12.0), (20.0, 21.5)), gap_s=0.3)
    assert m.compact_starts == [0.0, 3.3, 5.6]
    assert m.compact_duration == pytest.approx(3.0 + 2.0 + 1.5 + 0.6)
    for t in (2.0, 3.7, 5.0, 10.0, 11.2, 20.5, 21.5):
        assert m.to_call(m.to_compact(t)) == pytest.approx(t)
    assert m.to_call(3.05) == 5.0  # inside a joining gap: snaps to the nearer region edge
    assert m.to_call(3.25) == 10.0
    assert m.to_call(99.0) == 21.5
    x = np.arange(25 * 100, dtype=np.float32)
    out = m.compact(x, 100)
    assert out.size == int(round(m.compact_duration * 100))
    assert out[0] == 200.0 and out[300 + 30] == 1000.0
    stereo = m.compact(np.stack([x, -x]), 100)
    assert stereo.shape == (2, out.size) and np.array_equal(stereo[1], -out)
    assert SpeechMap.identity(7.0).to_call(3.0) == 3.0


def test_union_regions_across_channels():
    assert union_regions([[(0, 2), (5, 6)], [(1.5, 3), (7, 8)]]) == [(0, 3), (5, 6), (7, 8)]
    assert union_regions([[(0, 1)], [(1.2, 2)]], merge_gap=0.3) == [(0, 2)]


def test_config_is_tunable():
    x, truth = timeline([("silence", 1), ("speech", 2), ("silence", 1)], seed=7)
    strict = detect_speech(x, SR, VadConfig(energy_margin_db=60.0))
    assert strict == []
