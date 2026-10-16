"""Synthetic test audio: speech-like formant syllables, hold music, tones and noise (deterministic per seed)."""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

SR = 16_000
VOWELS = [(730, 1090), (270, 2290), (300, 870), (530, 1840), (660, 1720), (490, 1350), (400, 1950)]


def _resonator(x: np.ndarray, freq: float, bw: float, sr: int) -> np.ndarray:
    r = np.exp(-np.pi * bw / sr)
    theta = 2 * np.pi * freq / sr
    return lfilter([1 - r], [1, -2 * r * np.cos(theta), r * r], x)


def syllable(rng: np.random.Generator, sr: int = SR) -> np.ndarray:
    """One voiced syllable: a glottal pulse train with a gliding pitch through two formants, with an onset and decay."""
    dur = rng.uniform(0.12, 0.28)
    n = int(dur * sr)
    f0 = rng.uniform(100, 220) * np.linspace(1.0, rng.uniform(0.85, 1.15), n)
    phase = np.cumsum(f0 / sr)
    pulses = (np.diff(np.floor(phase), prepend=0) > 0).astype(float)
    f1, f2 = VOWELS[rng.integers(len(VOWELS))]
    v = _resonator(pulses, f1, 90, sr) + 0.6 * _resonator(pulses, f2, 120, sr)
    env = np.minimum(1.0, np.linspace(0, 1, n) * 6) * np.minimum(1.0, np.linspace(1, 0, n) * 3)
    out = v * env
    if rng.random() < 0.35:  # a fricative onset
        k = int(rng.uniform(0.04, 0.08) * sr)
        fric = np.diff(rng.normal(0, 1, k + 1)) * 0.15
        out[:k] += fric * np.linspace(1, 0.2, k)
    return out / (np.max(np.abs(out)) + 1e-9)


def speech_like(duration: float, *, seed: int = 0, sr: int = SR, level: float = 0.3) -> np.ndarray:
    """Utterances of syllables separated by short gaps, filling ``duration`` seconds."""
    rng = np.random.default_rng(seed)
    parts, total = [], 0
    target = int(duration * sr)
    while total < target:
        s = syllable(rng, sr)
        gap = np.zeros(int(rng.uniform(0.02, 0.09) * sr))
        parts += [s, gap]
        total += s.size + gap.size
    x = np.concatenate(parts)[:target]
    return (x * level).astype(np.float32)


def hold_music(duration: float, *, seed: int = 0, sr: int = SR, level: float = 0.3) -> np.ndarray:
    """Sustained chords (three tones and an octave), changing every 0.6–1.2 s with short crossfades."""
    rng = np.random.default_rng(seed)
    roots = [196.0, 220.0, 246.9, 261.6, 293.7, 329.6]
    parts, total, target = [], 0, int(duration * sr)
    while total < target:
        n = int(rng.uniform(0.6, 1.2) * sr)
        t = np.arange(n) / sr
        root = roots[rng.integers(len(roots))]
        chord = sum(np.sin(2 * np.pi * root * r * t + rng.uniform(0, 6.28)) for r in (1.0, 1.26, 1.5, 2.0))
        fade = int(0.03 * sr)
        env = np.ones(n)
        env[:fade] = np.linspace(0, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
        parts.append(chord * env)
        total += n
    x = np.concatenate(parts)[:target]
    return (x / (np.max(np.abs(x)) + 1e-9) * level).astype(np.float32)


def tone(duration: float, freq: float = 440.0, *, sr: int = SR, level: float = 0.3) -> np.ndarray:
    t = np.arange(int(duration * sr)) / sr
    return (np.sin(2 * np.pi * freq * t) * level).astype(np.float32)


def noise(duration: float, *, seed: int = 0, sr: int = SR, level_db: float = -60.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.normal(0, 1, int(duration * sr)) * 10 ** (level_db / 20)).astype(np.float32)


def timeline(
    pieces: list[tuple[str, float]], *, seed: int = 0, sr: int = SR, floor_db: float = -60.0
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Concatenate ('speech'|'music'|'silence'|'tone', seconds) pieces over a noise floor; returns audio and the speech intervals."""
    out, speech, t = [], [], 0.0
    for i, (kind, dur) in enumerate(pieces):
        if kind == "speech":
            x = speech_like(dur, seed=seed + i, sr=sr)
            speech.append((t, t + dur))
        elif kind == "music":
            x = hold_music(dur, seed=seed + i, sr=sr)
        elif kind == "tone":
            x = tone(dur, sr=sr)
        else:
            x = np.zeros(int(dur * sr), dtype=np.float32)
        out.append(x)
        t += dur
    y = np.concatenate(out)
    return (y + noise(y.size / sr, seed=seed + 99, sr=sr, level_db=floor_db)).astype(np.float32), speech
