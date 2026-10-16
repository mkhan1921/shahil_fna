"""Audio preprocessing (§5.2 step 1): load, resample to 16 kHz, loudness-normalise to −23 LUFS, detect the layout.

A stereo recording whose channels are not near-identical (normalised cross-correlation < 0.9 at any lag
within ±5 ms) is ``stereo_split``: each channel is one side of the call. Near-identical channels, or a
channel that is silent or 30 dB quieter than the other, are treated as one ``mono`` signal. Several files
(one per side) are stacked as channels and go through the same check.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import pyloudnorm
import soundfile as sf
from scipy.signal import correlate, resample_poly

TARGET_SR = 16_000
TARGET_LUFS = -23.0
NEAR_IDENTICAL_CORR = 0.9
MAX_LAG_S = 0.005
SILENT_RMS = 1e-4
CHANNEL_GAP_DB = 30.0
PEAK_CEILING = 0.99

Layout = Literal["stereo_split", "mono"]


@dataclass
class PreparedAudio:
    """16 kHz, loudness-normalised audio with its channel layout. ``samples`` is (channels, n) float32."""

    samples: np.ndarray
    sample_rate: int = TARGET_SR
    layout: Layout = "mono"
    channel_correlation: float | None = None
    input_loudness: tuple[float, ...] = ()
    source_sample_rate: int = TARGET_SR
    source_channels: int = 1
    notes: list[str] = field(default_factory=list)

    @property
    def n_channels(self) -> int:
        return int(self.samples.shape[0])

    @property
    def duration(self) -> float:
        return self.samples.shape[1] / self.sample_rate

    def channel(self, i: int) -> np.ndarray:
        return self.samples[i]

    def mono(self) -> np.ndarray:
        return self.samples.mean(axis=0) if self.n_channels > 1 else self.samples[0]

    def clip(self, t0: float, t1: float, channel: int | None = None) -> np.ndarray:
        """Samples between two call times (clamped), from one channel or the mono mix."""
        a = max(0, int(round(t0 * self.sample_rate)))
        b = min(self.samples.shape[1], int(round(t1 * self.sample_rate)))
        src = self.mono() if channel is None or channel >= self.n_channels else self.samples[channel]
        return src[a : max(a, b)].copy()


def load_audio(audio: Path | str | list[Path] | list[str]) -> tuple[np.ndarray, int]:
    """Read one file, or several (one per side) stacked as channels, as float32 (channels, n)."""
    paths = [Path(p) for p in (audio if isinstance(audio, list) else [audio])]
    if not paths:
        raise ValueError("no audio files")
    arrays, rates = [], []
    for p in paths:
        data, sr = sf.read(str(p), dtype="float32", always_2d=True)
        arrays.append(data.T)
        rates.append(int(sr))
    sr = rates[0]
    chans = [resample(a, r, sr) if r != sr else a for a, r in zip(arrays, rates)]
    n = max(c.shape[1] for c in chans)
    stacked = np.concatenate([np.pad(c, ((0, 0), (0, n - c.shape[1]))) for c in chans], axis=0)
    return stacked.astype(np.float32), sr


def resample(x: np.ndarray, sr_in: int, sr_out: int = TARGET_SR) -> np.ndarray:
    """Polyphase resampling along the last axis."""
    if sr_in == sr_out:
        return np.asarray(x, dtype=np.float32)
    g = math.gcd(int(sr_in), int(sr_out))
    return resample_poly(np.asarray(x, dtype=np.float64), sr_out // g, sr_in // g, axis=-1).astype(np.float32)


def integrated_loudness(x: np.ndarray, sr: int) -> float:
    """ITU-R BS.1770 integrated loudness in LUFS (−inf for silence or audio shorter than one 400 ms block)."""
    if x.size < int(0.4 * sr) + 1 or not np.any(x):
        return float("-inf")
    return float(pyloudnorm.Meter(sr).integrated_loudness(np.asarray(x, dtype=np.float64)))


def normalize_loudness(x: np.ndarray, sr: int, target: float = TARGET_LUFS) -> tuple[np.ndarray, float]:
    """Gain to the target loudness; peaks above 0.99 are scaled down instead of clipped."""
    measured = integrated_loudness(x, sr)
    if not math.isfinite(measured) or measured < -70.0:
        return np.asarray(x, dtype=np.float32), measured
    y = np.asarray(x, dtype=np.float64) * 10 ** ((target - measured) / 20.0)
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > PEAK_CEILING:
        y *= PEAK_CEILING / peak
    return y.astype(np.float32), measured


def _windows(n: int, sr: int, n_windows: int = 6, window_s: float = 10.0) -> list[tuple[int, int]]:
    w = int(window_s * sr)
    if n <= w * n_windows:
        return [(0, n)]
    starts = np.linspace(0, n - w, n_windows).astype(int)
    return [(int(s), int(s) + w) for s in starts]


def channel_correlation(a: np.ndarray, b: np.ndarray, sr: int, max_lag_s: float = MAX_LAG_S) -> float:
    """Normalised cross-correlation of two channels, maximised over |lag| ≤ max_lag_s (sampled windows on long audio)."""
    lag = max(1, int(max_lag_s * sr))
    num = np.zeros(2 * lag + 1)
    ea = eb = 0.0
    for s, e in _windows(min(a.size, b.size), sr):
        xa = np.asarray(a[s:e], dtype=np.float64)
        xb = np.asarray(b[s:e], dtype=np.float64)
        xa = xa - xa.mean()
        xb = xb - xb.mean()
        full = correlate(xa, xb, mode="full", method="fft")
        mid = xb.size - 1
        num += full[mid - lag : mid + lag + 1]
        ea += float(xa @ xa)
        eb += float(xb @ xb)
    if ea <= 0 or eb <= 0:
        return 0.0
    return float(np.max(np.abs(num)) / math.sqrt(ea * eb))


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if x.size else 0.0


def detect_layout(samples: np.ndarray, sr: int) -> tuple[Layout, float | None, np.ndarray, list[str]]:
    """The layout, the channel correlation and the samples to keep: both sides for stereo_split, a mono mix otherwise."""
    notes: list[str] = []
    if samples.shape[0] == 1:
        return "mono", None, samples, notes
    if samples.shape[0] > 2:
        notes.append(f"{samples.shape[0]} channels downmixed to mono")
        return "mono", None, samples.mean(axis=0, keepdims=True), notes
    r0, r1 = _rms(samples[0]), _rms(samples[1])
    loud, quiet = max(r0, r1), min(r0, r1)
    if quiet < SILENT_RMS or (quiet > 0 and 20 * math.log10(loud / quiet) > CHANNEL_GAP_DB):
        notes.append("one channel silent: treated as mono")
        return "mono", None, samples[[0 if r0 >= r1 else 1]], notes
    corr = channel_correlation(samples[0], samples[1], sr)
    if corr >= NEAR_IDENTICAL_CORR:
        notes.append(f"channels near-identical (corr {corr:.2f}): downmixed to mono")
        return "mono", corr, samples.mean(axis=0, keepdims=True), notes
    return "stereo_split", corr, samples, notes


def preprocess(
    audio: Path | str | list[Path] | list[str] | np.ndarray, sample_rate: int | None = None, *, target_lufs: float = TARGET_LUFS
) -> PreparedAudio:
    """Load (or take an array), resample to 16 kHz, detect the layout and loudness-normalise each kept channel."""
    if isinstance(audio, np.ndarray):
        if sample_rate is None:
            raise ValueError("sample_rate is required with an array")
        samples, sr = (audio[None, :] if audio.ndim == 1 else audio).astype(np.float32), int(sample_rate)
    else:
        samples, sr = load_audio(audio)
    source_channels = int(samples.shape[0])
    x = resample(samples, sr, TARGET_SR)
    layout, corr, kept, notes = detect_layout(x, TARGET_SR)
    normed, loudness = [], []
    for ch in kept:
        y, measured = normalize_loudness(ch, TARGET_SR, target_lufs)
        normed.append(y)
        loudness.append(measured)
    return PreparedAudio(
        samples=np.stack(normed).astype(np.float32),
        sample_rate=TARGET_SR,
        layout=layout,
        channel_correlation=corr,
        input_loudness=tuple(loudness),
        source_sample_rate=sr,
        source_channels=source_channels,
        notes=notes,
    )


def wav_bytes(samples: np.ndarray, sr: int = TARGET_SR) -> bytes:
    """16-bit PCM WAV of (n,) or (channels, n) float samples, for upload to a provider."""
    data = samples.T if samples.ndim == 2 else samples
    buf = io.BytesIO()
    sf.write(buf, np.clip(data, -1.0, 1.0), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()
