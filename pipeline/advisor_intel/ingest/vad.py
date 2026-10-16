"""Voice activity detection (§5.2 step 2): drop silence and hold music, keep call-relative offsets.

A frame (25 ms, 10 ms hop) is *active* when its energy is ``energy_margin_db`` above the channel's noise
floor (10th percentile of frame energies) and above an absolute floor. Active audio is *speech* only when
it also moves like speech over the surrounding second: enough envelope modulation in the syllabic band
(2.5–8 Hz) and enough spectral change between frames 50 ms apart. Hold music and tones are loud but
spectrally steady, and their envelope moves at the beat or chord rate, so they fail one of the two tests.
Decisions then pass a hysteresis with hangover: a region opens after ``onset_frames`` speech frames and
closes after ``hangover_s`` without speech; regions closer than ``merge_gap_s`` merge, shorter than
``min_speech_s`` drop, and the rest are padded by ``pad_s``.

``SpeechMap`` cuts the speech regions out of the call and joins them with short silences so the ASR
provider never hears (or bills) silence and music; it maps provider times back to call time.

The thresholds are v1 defaults, chosen on synthetic speech, noise and music, and are tuned on gold calls.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .audio import PreparedAudio

Region = tuple[float, float]


@dataclass(frozen=True)
class VadConfig:
    frame_s: float = 0.025
    hop_s: float = 0.010
    n_fft: int = 512
    band_hz: tuple[float, float] = (100.0, 4000.0)
    energy_margin_db: float = 10.0
    abs_floor_db: float = -55.0
    floor_percentile: float = 10.0
    window_s: float = 1.0  # context for the envelope-modulation feature
    flux_window_s: float = 0.6  # context for the spectral-change feature
    modulation_band_hz: tuple[float, float] = (2.5, 8.0)
    modulation_total_hz: tuple[float, float] = (0.5, 20.0)
    min_modulation: float = 0.25
    flux_lag_frames: int = 5
    min_flux: float = 0.12
    strong_flux: float = 0.30  # spectral change this high counts as speech whatever the modulation
    onset_frames: int = 3
    hangover_s: float = 0.30
    merge_gap_s: float = 0.30
    min_speech_s: float = 0.20
    pad_s: float = 0.10
    block_frames: int = 6000  # frames per STFT block, bounds memory on long calls


@dataclass
class VadFrames:
    """Per-frame features, exposed for tests and tuning."""

    times: np.ndarray  # frame centre, seconds
    energy_db: np.ndarray
    active: np.ndarray
    modulation: np.ndarray
    flux: np.ndarray
    speech: np.ndarray


def _frames(x: np.ndarray, frame: int, hop: int) -> np.ndarray:
    if x.size < frame:
        x = np.pad(x, (0, frame - x.size))
    return sliding_window_view(x, frame)[::hop]


def _moving_mean(v: np.ndarray, width: int) -> np.ndarray:
    if v.size == 0:
        return v
    width = max(1, min(width, v.size))
    kernel = np.ones(width) / width
    pad_l, pad_r = width // 2, width - 1 - width // 2
    return np.convolve(np.pad(v, (pad_l, pad_r), mode="edge"), kernel, mode="valid")


def frame_features(x: np.ndarray, sr: int, cfg: VadConfig = VadConfig()) -> VadFrames:
    frame, hop = int(cfg.frame_s * sr), int(cfg.hop_s * sr)
    frames_all = _frames(np.asarray(x, dtype=np.float32), frame, hop)
    n = frames_all.shape[0]
    window = np.hanning(frame).astype(np.float32)
    freqs = np.fft.rfftfreq(cfg.n_fft, 1.0 / sr)
    band = (freqs >= cfg.band_hz[0]) & (freqs <= cfg.band_hz[1])
    energy = np.empty(n)
    spectra = np.empty((n, int(band.sum())), dtype=np.float32)
    for s in range(0, n, cfg.block_frames):
        blk = np.asarray(frames_all[s : s + cfg.block_frames], dtype=np.float32)
        energy[s : s + blk.shape[0]] = 10 * np.log10(np.mean(np.square(blk, dtype=np.float64), axis=1) + 1e-12)
        mag = np.abs(np.fft.rfft(blk * window, n=cfg.n_fft, axis=1))[:, band]
        norm = np.linalg.norm(mag, axis=1, keepdims=True)
        spectra[s : s + blk.shape[0]] = mag / np.maximum(norm, 1e-12)

    floor = float(np.percentile(energy, cfg.floor_percentile)) if n else -120.0
    active = energy > max(floor + cfg.energy_margin_db, cfg.abs_floor_db)

    lag = cfg.flux_lag_frames
    flux = np.zeros(n)
    if n > lag:
        flux[lag:] = 1.0 - np.sum(spectra[lag:] * spectra[:-lag], axis=1)
    win = max(4, int(round(cfg.window_s / cfg.hop_s)))
    fwin = max(4, int(round(cfg.flux_window_s / cfg.hop_s)))
    # Mean spectral change over the window, counting active frames only (pauses inside speech would dilute it).
    act = active.astype(float)
    flux_mean = _moving_mean(flux * act, fwin) / np.maximum(_moving_mean(act, fwin), 1e-9)

    # Envelope modulation spectrum over sliding one-second windows, evaluated every 10 frames.
    env = np.sqrt(10 ** (energy / 10))
    modulation = np.zeros(n)
    if n >= win:
        rate = 1.0 / cfg.hop_s
        mfreqs = np.fft.rfftfreq(win, 1.0 / rate)
        sb = (mfreqs >= cfg.modulation_band_hz[0]) & (mfreqs <= cfg.modulation_band_hz[1])
        tb = (mfreqs >= cfg.modulation_total_hz[0]) & (mfreqs <= cfg.modulation_total_hz[1])
        step = 10
        starts = np.arange(0, n - win + 1, step)
        segs = sliding_window_view(env, win)[starts]
        segs = segs - segs.mean(axis=1, keepdims=True)
        p = np.abs(np.fft.rfft(segs * np.hanning(win), axis=1)) ** 2
        ratio = p[:, sb].sum(axis=1) / np.maximum(p[:, tb].sum(axis=1), 1e-12)
        centres = starts + win // 2
        modulation = np.interp(np.arange(n), centres, ratio)
    else:
        modulation[:] = 1.0  # too short to judge: let energy decide

    speech_like = (flux_mean >= cfg.strong_flux) | ((flux_mean >= cfg.min_flux) & (modulation >= cfg.min_modulation))
    times = (np.arange(n) * hop + frame / 2) / sr
    return VadFrames(times=times, energy_db=energy, active=active, modulation=modulation, flux=flux_mean, speech=active & speech_like)


def _hysteresis(speech: np.ndarray, cfg: VadConfig) -> list[tuple[int, int]]:
    hang = int(round(cfg.hangover_s / cfg.hop_s))
    regions, start, run, silent = [], None, 0, 0
    last_speech = -1
    for i, s in enumerate(speech):
        if s:
            run += 1
            silent = 0
            last_speech = i
            if start is None and run >= cfg.onset_frames:
                start = i - run + 1
        else:
            run = 0
            if start is not None:
                silent += 1
                if silent > hang:
                    regions.append((start, last_speech + 1))
                    start, silent = None, 0
    if start is not None:
        regions.append((start, last_speech + 1))
    return regions


def tidy_regions(regions: list[Region], *, duration: float, merge_gap: float, min_len: float, pad: float) -> list[Region]:
    """Merge close regions, drop short ones, pad and clamp to the call."""
    merged: list[list[float]] = []
    for s, e in sorted(regions):
        if merged and s - merged[-1][1] < merge_gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out: list[list[float]] = []
    for s, e in merged:
        if e - s < min_len:
            continue
        s, e = max(0.0, s - pad), min(duration, e + pad)
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(round(s, 3), round(e, 3)) for s, e in out]


def detect_speech(x: np.ndarray, sr: int, cfg: VadConfig = VadConfig()) -> list[Region]:
    """Speech regions of one channel, in seconds from its start."""
    if x.size == 0:
        return []
    f = frame_features(x, sr, cfg)
    hop, frame = cfg.hop_s, cfg.frame_s
    raw = [(a * hop, b * hop + frame) for a, b in _hysteresis(f.speech, cfg)]
    return tidy_regions(raw, duration=x.size / sr, merge_gap=cfg.merge_gap_s, min_len=cfg.min_speech_s, pad=cfg.pad_s)


def union_regions(lists: list[list[Region]], *, merge_gap: float = 0.0) -> list[Region]:
    out: list[list[float]] = []
    for s, e in sorted(r for rs in lists for r in rs):
        if out and s <= out[-1][1] + merge_gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def speech_regions(audio: PreparedAudio, cfg: VadConfig = VadConfig()) -> list[list[Region]]:
    """Speech regions per channel."""
    return [detect_speech(audio.channel(i), audio.sample_rate, cfg) for i in range(audio.n_channels)]


@dataclass(frozen=True)
class SpeechMap:
    """Call-time speech regions and the compacted timeline the ASR hears (regions joined by ``gap_s`` of silence)."""

    regions: tuple[Region, ...]
    gap_s: float = 0.3

    @property
    def compact_starts(self) -> list[float]:
        starts, t = [], 0.0
        for s, e in self.regions:
            starts.append(t)
            t += (e - s) + self.gap_s
        return starts

    @property
    def compact_duration(self) -> float:
        return max(0.0, sum(e - s for s, e in self.regions) + self.gap_s * (len(self.regions) - 1))

    def compact_regions(self) -> list[Region]:
        return [(c, c + (e - s)) for c, (s, e) in zip(self.compact_starts, self.regions)]

    def to_call(self, t: float) -> float:
        """A time in the compacted audio → call time (times in a joining gap snap to the nearest region edge)."""
        if not self.regions:
            return t
        starts = self.compact_starts
        for i, (c, (s, e)) in enumerate(zip(starts, self.regions)):
            end_c = c + (e - s)
            if t <= end_c:
                return s + max(0.0, t - c)
            if i + 1 < len(self.regions) and t < starts[i + 1]:
                return e if t - end_c < (starts[i + 1] - end_c) / 2 else self.regions[i + 1][0]
        return self.regions[-1][1]

    def to_compact(self, t: float) -> float:
        for c, (s, e) in zip(self.compact_starts, self.regions):
            if t <= e:
                return c + max(0.0, t - s)
        return self.compact_duration

    def compact(self, samples: np.ndarray, sr: int) -> np.ndarray:
        """Cut the regions out of (channels, n) samples and join them with silence."""
        x = samples if samples.ndim == 2 else samples[None, :]
        gap = np.zeros((x.shape[0], int(round(self.gap_s * sr))), dtype=x.dtype)
        pieces = []
        for i, (s, e) in enumerate(self.regions):
            if i:
                pieces.append(gap)
            pieces.append(x[:, int(round(s * sr)) : int(round(e * sr))])
        out = np.concatenate(pieces, axis=1) if pieces else np.zeros((x.shape[0], 0), dtype=x.dtype)
        return out if samples.ndim == 2 else out[0]

    @classmethod
    def identity(cls, duration: float) -> "SpeechMap":
        return cls(regions=((0.0, duration),))
