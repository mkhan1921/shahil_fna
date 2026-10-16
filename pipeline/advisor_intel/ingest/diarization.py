"""Diarization (§5.2 step 4) and word-to-speaker assignment.

- Stereo: speaker = channel (``ChannelDiarizer``, confidence 1.0). If two clients share a channel (a spouse
  on speaker-phone) that channel is diarized with Precision-3, ``max_speakers=3``.
- Mono: pyannoteAI Precision-3 with ``min_speakers=2, max_speakers=4``.
- ``diarization_conf`` per word is the confidence of the turn it falls in: Precision-3's per-turn confidence,
  or the mean posterior of the assigned speaker over the turn's frames, or 1.0 for channel-based labels.

pyannoteAI request/response mapping (assumed from the public API docs; validate with a real key):

    POST {base}/media/input  {"url": "media://advisor-intel/<key>.wav"}        → {"url": <pre-signed PUT URL>}
    PUT  <pre-signed URL>    body: 16 kHz PCM WAV
    POST {base}/diarize      {"url": "media://…", "model": "precision-3", "minSpeakers": 2, "maxSpeakers": 4,
                              "confidence": true, "turnLevelConfidence": true}     → {"jobId": "…", "status": "created"}
    GET  {base}/jobs/{jobId}                                                      → {"status": "pending|created|running|succeeded|failed|canceled",
         "output": {"diarization": [{"speaker": "SPEAKER_00", "start": 0.52, "end": 3.1, "confidence": 0.93?}, …],
                    "confidence": {"resolution": 0.02, "score": {"SPEAKER_00": [0.97, …], …}}?}}

Per-turn ``confidence`` is used when present; otherwise the frame posteriors (a dict per speaker, or a
frame-major matrix with a ``speakers`` list) are averaged over the turn; scores above 1 are read as
percentages. Labels are renamed ``spk_0``, ``spk_1``… in order of first speech.
"""

from __future__ import annotations

import bisect
import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from .asr.base import HttpProvider, require_key
from .audio import wav_bytes
from .types import AsrError, AsrWord, SpeakerTurn

Region = tuple[float, float]
PYANNOTE_URL = "https://api.pyannote.ai/v1"


@runtime_checkable
class Diarizer(Protocol):
    name: str
    model_version: str

    async def diarize(self, samples: np.ndarray, sample_rate: int, *, min_speakers: int, max_speakers: int, key: str = "") -> list[SpeakerTurn]:
        """Speaker turns of one mono signal, in its own time base."""
        ...


def relabel(turns: Sequence[SpeakerTurn], prefix: str = "spk_") -> list[SpeakerTurn]:
    """Rename speakers ``{prefix}0``, ``{prefix}1``… in order of first speech."""
    order: dict[str, str] = {}
    out = []
    for t in sorted(turns, key=lambda t: (t.start, t.end)):
        if t.speaker not in order:
            order[t.speaker] = f"{prefix}{len(order)}"
        out.append(t.model_copy(update={"speaker": order[t.speaker]}))
    return out


class ChannelDiarizer:
    """Stereo split: each channel is one speaker, confidence 1.0 (§5.2 step 4)."""

    name = "channel"
    model_version = "channel-1"

    @staticmethod
    def label(channel: int) -> str:
        return f"ch{channel}"

    def turns(self, regions_per_channel: Sequence[Sequence[Region]]) -> list[SpeakerTurn]:
        return [SpeakerTurn(speaker=self.label(c), start=s, end=e, confidence=1.0, channel=c)
                for c, regions in enumerate(regions_per_channel) for s, e in regions]

    async def diarize(self, samples: np.ndarray, sample_rate: int, *, min_speakers: int = 1, max_speakers: int = 1, key: str = "") -> list[SpeakerTurn]:
        dur = samples.shape[-1] / sample_rate
        return [SpeakerTurn(speaker=self.label(0), start=0.0, end=dur, confidence=1.0)]


@dataclass
class PyannoteAIDiarizer(HttpProvider):
    """pyannoteAI Precision-3 over its HTTP API (submit, poll, map turns and confidence)."""

    base_url: str = PYANNOTE_URL
    model: str = "precision-3"
    name: str = "pyannoteai"

    @property
    def model_version(self) -> str:
        return self.model

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {require_key(self.api_key, 'pyannoteAI')}"}

    async def diarize(self, samples: np.ndarray, sample_rate: int, *, min_speakers: int, max_speakers: int, key: str = "") -> list[SpeakerTurn]:
        http = self.http()
        media = f"media://advisor-intel/{key or uuid.uuid4().hex}.wav"
        up = self.json_or_raise(await http.post(f"{self.base_url}/media/input", json={"url": media}, headers=self._headers()), "pyannoteAI")
        put = await http.put(up["url"], content=wav_bytes(samples, sample_rate), headers={"Content-Type": "audio/wav"})
        self.json_or_raise(put, "pyannoteAI upload")
        body = {"url": media, "model": self.model, "minSpeakers": min_speakers, "maxSpeakers": max_speakers, "confidence": True,
                "turnLevelConfidence": True}
        job = self.json_or_raise(await http.post(f"{self.base_url}/diarize", json=body, headers=self._headers()), "pyannoteAI")
        job_id = job.get("jobId") or job.get("id")
        if not job_id:
            raise AsrError(f"pyannoteAI: no job id in {job}")

        async def fetch() -> dict:
            return self.json_or_raise(await http.get(f"{self.base_url}/jobs/{job_id}", headers=self._headers()), "pyannoteAI")

        done = await self.poll(fetch, lambda b: b.get("status") == "succeeded",
                               lambda b: (b.get("error") or b.get("status")) if b.get("status") in ("failed", "canceled") else None, "pyannoteAI")
        return relabel(parse_pyannote_output(done.get("output") or {}))


def _posterior(conf: dict, speaker: str, start: float, end: float) -> float | None:
    """Mean posterior of ``speaker`` over [start, end] from a frame-level confidence block."""
    res = float(conf.get("resolution") or 0.0)
    score = conf.get("score")
    if res <= 0 or score is None:
        return None
    if isinstance(score, dict):
        series = score.get(speaker)
    else:
        speakers = conf.get("speakers") or []
        if speaker not in speakers:
            return None
        k = speakers.index(speaker)
        series = [row[k] for row in score]
    if not series:
        return None
    a, b = int(start / res), max(int(start / res) + 1, int(np.ceil(end / res)))
    window = np.asarray(series[a:b], dtype=float)
    if window.size == 0:
        return None
    return float(window.mean())


def _unit(x: float) -> float:
    return x / 100.0 if x > 1.0 else x


def parse_pyannote_output(output: dict) -> list[SpeakerTurn]:
    turns = []
    frames = output.get("confidence") if isinstance(output.get("confidence"), dict) else None
    for t in output.get("diarization") or []:
        c = t.get("confidence")
        if c is None and frames:
            c = _posterior(frames, t["speaker"], float(t["start"]), float(t["end"]))
        turns.append(SpeakerTurn(speaker=str(t["speaker"]), start=float(t["start"]), end=float(t["end"]),
                                 confidence=round(min(1.0, max(0.0, _unit(float(c)))), 4) if c is not None else 1.0))
    return turns


class FakeDiarizer:
    """Returns given turns (e.g. a script's gold turns), optionally with boundary jitter and a confidence spread.

    ``time_base="call"`` (the default) says the turns are already in call time, so the orchestrator does not
    map them through the VAD-compacted timeline; use ``"input"`` for turns relative to the audio passed in.
    """

    name = "fake"
    model_version = "fake-1"

    def __init__(self, turns: Sequence[SpeakerTurn] = (), *, jitter_s: float = 0.0, confidence: float = 0.95, conf_sd: float = 0.0,
                 seed: int = 0, keep_labels: bool = False, time_base: str = "call"):
        self.base = list(turns)
        self.jitter_s, self.confidence, self.conf_sd, self.seed, self.keep_labels = jitter_s, confidence, conf_sd, seed, keep_labels
        self.time_base = time_base
        self.calls: list[dict] = []

    async def diarize(self, samples: np.ndarray, sample_rate: int, *, min_speakers: int, max_speakers: int, key: str = "") -> list[SpeakerTurn]:
        self.calls.append({"min_speakers": min_speakers, "max_speakers": max_speakers, "key": key, "seconds": samples.shape[-1] / sample_rate})
        return self.turns()

    def turns(self) -> list[SpeakerTurn]:
        rng = random.Random(self.seed)
        out = []
        for t in self.base:
            j0 = rng.uniform(-self.jitter_s, self.jitter_s) if self.jitter_s else 0.0
            j1 = rng.uniform(-self.jitter_s, self.jitter_s) if self.jitter_s else 0.0
            conf = self.confidence + (rng.gauss(0, self.conf_sd) if self.conf_sd else 0.0)
            out.append(t.model_copy(update={"start": max(0.0, t.start + j0), "end": max(t.start + j0 + 0.05, t.end + j1),
                                            "confidence": round(min(1.0, max(0.0, conf)), 4)}))
        return out if self.keep_labels else relabel(out)


@dataclass
class Assignment:
    speaker: str
    confidence: float


def assign_by_turns(words: Sequence[AsrWord], turns: Sequence[SpeakerTurn], *, unknown: str = "spk_unknown") -> list[Assignment]:
    """Each word goes to the turn it overlaps most (ties: higher confidence, then the later turn); words outside every
    turn take the nearest turn within 1 s, else the previous word's speaker."""
    if not turns:
        return [Assignment(unknown, 0.0) for _ in words]
    ts = sorted(turns, key=lambda t: t.start)
    starts = [t.start for t in ts]
    max_len = max(t.end - t.start for t in ts)
    out: list[Assignment] = []
    for w in words:
        lo = bisect.bisect_left(starts, w.t_start - max_len)
        hi = bisect.bisect_right(starts, w.t_end)
        best, key = None, None
        for t in ts[lo:hi]:
            ov = min(w.t_end, t.end) - max(w.t_start, t.start)
            k = (ov, t.confidence, t.start)
            if ov > 0 and (key is None or k > key):
                best, key = t, k
        if best is None:
            mid = (w.t_start + w.t_end) / 2
            near = min(ts, key=lambda t: min(abs(mid - t.start), abs(mid - t.end)))
            if min(abs(mid - near.start), abs(mid - near.end)) <= 1.0:
                best = near
        if best is not None:
            out.append(Assignment(best.speaker, best.confidence))
        else:
            out.append(Assignment(out[-1].speaker, out[-1].confidence * 0.5) if out else Assignment(unknown, 0.0))
    return out


def overlap_seconds(turns: Sequence[SpeakerTurn], t0: float, t1: float) -> float:
    """Seconds within [t0, t1] where turns of two different speakers overlap (mono crosstalk from diarization)."""
    events = []
    for t in turns:
        a, b = max(t0, t.start), min(t1, t.end)
        if b > a:
            events += [(a, 1, t.speaker), (b, -1, t.speaker)]
    events.sort(key=lambda e: (e[0], e[1]))
    active: dict[str, int] = {}
    total, last = 0.0, None
    for x, d, spk in events:
        if last is not None and sum(1 for v in active.values() if v > 0) >= 2:
            total += x - last
        active[spk] = active.get(spk, 0) + d
        last = x
    return total
