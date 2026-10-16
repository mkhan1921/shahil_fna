"""Audio preprocessing (§5.2 step 1): resampling, loudness normalisation and channel layout detection."""

from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf

from advisor_intel.ingest import audio as A

from .audio_synth import noise, speech_like, tone

SR = 16_000


def _peak_hz(x: np.ndarray, sr: int) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    return float(np.fft.rfftfreq(x.size, 1 / sr)[np.argmax(spec)])


def test_resample_keeps_frequency_and_duration():
    sr_in = 44_100
    t = np.arange(int(2.0 * sr_in)) / sr_in
    x = np.sin(2 * np.pi * 1000 * t).astype(np.float32)
    y = A.resample(x, sr_in, SR)
    assert abs(y.size - 2.0 * SR) <= 1
    assert abs(_peak_hz(y, SR) - 1000) < 5
    assert A.resample(y, SR, SR) is not None and A.resample(y, SR, SR).dtype == np.float32


def test_loudness_is_normalised_to_minus_23_lufs():
    measured_levels = []
    for level in (0.02, 0.3, 0.8):
        x = speech_like(6.0, seed=1, level=level)
        y, measured = A.normalize_loudness(x, SR)
        measured_levels.append(measured)
        assert A.integrated_loudness(y, SR) == pytest.approx(-23.0, abs=0.3)
        assert (np.max(np.abs(y)) > np.max(np.abs(x))) == (measured < -23.0)  # quiet input gains, loud input attenuates
    assert measured_levels == sorted(measured_levels)


def test_silence_and_very_short_audio_get_no_gain():
    z = np.zeros(SR, dtype=np.float32)
    y, measured = A.normalize_loudness(z, SR)
    assert measured == float("-inf") and not np.any(y)
    short = tone(0.2)
    y2, m2 = A.normalize_loudness(short, SR)
    assert m2 == float("-inf") and np.array_equal(y2, short)


def test_peaks_are_scaled_not_clipped():
    x = noise(5.0, level_db=-50.0)
    x[SR : SR + 50] = 0.9  # a click far louder than the quiet bed
    y, _ = A.normalize_loudness(x, SR)
    assert np.max(np.abs(y)) <= A.PEAK_CEILING + 1e-6


def test_independent_channels_are_split():
    left = np.concatenate([speech_like(3, seed=1), np.zeros(3 * SR, np.float32)])
    right = np.concatenate([np.zeros(3 * SR, np.float32), speech_like(3, seed=2)])
    stereo = np.stack([left + noise(6, seed=3), right + noise(6, seed=4)])
    layout, corr, kept, _ = A.detect_layout(stereo, SR)
    assert layout == "stereo_split" and corr < A.NEAR_IDENTICAL_CORR
    assert kept.shape[0] == 2


def test_near_identical_channels_are_downmixed():
    x = speech_like(5, seed=5)
    delayed = np.concatenate([np.zeros(32, np.float32), x[:-32]])  # 2 ms between two microphones
    stereo = np.stack([x + noise(5, seed=1, level_db=-55), 0.8 * delayed + noise(5, seed=2, level_db=-55)])
    layout, corr, kept, notes = A.detect_layout(stereo, SR)
    assert layout == "mono" and corr >= A.NEAR_IDENTICAL_CORR
    assert kept.shape[0] == 1 and "near-identical" in notes[0]


def test_silent_channel_and_multichannel_become_mono():
    x = speech_like(3, seed=2)
    layout, _, kept, notes = A.detect_layout(np.stack([np.zeros_like(x), x]), SR)
    assert layout == "mono" and np.array_equal(kept[0], x) and "silent" in notes[0]
    layout3, _, kept3, _ = A.detect_layout(np.stack([x, x, x]), SR)
    assert layout3 == "mono" and kept3.shape[0] == 1


def test_preprocess_files_end_to_end(tmp_path):
    left = np.concatenate([speech_like(2, seed=1, level=0.05), np.zeros(2 * SR, np.float32)])
    right = np.concatenate([np.zeros(2 * SR, np.float32), speech_like(2, seed=2, level=0.5)])
    sf.write(tmp_path / "stereo.wav", np.stack([left, right]).T, SR)
    p = A.preprocess(tmp_path / "stereo.wav")
    assert p.layout == "stereo_split" and p.n_channels == 2 and p.sample_rate == SR
    assert len(p.input_loudness) == 2 and p.input_loudness[0] < p.input_loudness[1]  # each side measured on its own
    for ch in range(2):  # and normalised on its own
        assert A.integrated_loudness(p.channel(ch), SR) == pytest.approx(-23.0, abs=0.5)

    # One file per side at different rates: resampled and stacked as channels.
    adv = speech_like(2, seed=3)
    cli = A.resample(speech_like(2, seed=4), SR, 8_000)
    sf.write(tmp_path / "adviser.wav", np.concatenate([adv, np.zeros(2 * SR, np.float32)]), SR)
    sf.write(tmp_path / "client.wav", np.concatenate([np.zeros(16_000, np.float32), cli]), 8_000)
    p2 = A.preprocess([tmp_path / "adviser.wav", tmp_path / "client.wav"])
    assert p2.layout == "stereo_split" and p2.source_channels == 2
    assert p2.duration == pytest.approx(4.0, abs=0.01)


def test_preprocess_array_and_clip():
    x = np.concatenate([tone(1.0, 300), tone(1.0, 900)])
    p = A.preprocess(A.resample(x, SR, 48_000), 48_000)
    assert p.layout == "mono" and p.duration == pytest.approx(2.0, abs=0.01)
    assert abs(_peak_hz(p.clip(1.2, 1.8), SR) - 900) < 10
    assert p.clip(5.0, 6.0).size == 0
    with pytest.raises(ValueError):
        A.preprocess(x)


def test_wav_bytes_round_trip():
    x = np.stack([tone(0.5, 440), tone(0.5, 660)])
    data, sr = sf.read(io.BytesIO(A.wav_bytes(x, SR)), always_2d=True)
    assert sr == SR and data.shape == (x.shape[1], 2)
    assert np.max(np.abs(data[:, 0] - x[0])) < 1e-3
