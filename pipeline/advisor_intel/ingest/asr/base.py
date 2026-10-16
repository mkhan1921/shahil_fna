"""The ``ASRProvider`` contract (§5.2 step 3) and helpers shared by the adapters.

``transcribe(audio, vocab, want_alternatives=True) -> AsrResult`` must return word timestamps, word
confidence and, where the provider can, n-best transcripts (n ≥ 3) or per-word alternatives. Providers
that give neither return ``alts=[]`` and ``alternatives_available=False``; later stages cope (§5.2).
Adapters receive the VAD-compacted audio (silence and hold music cut out, §5.2 step 2) and return times
relative to it; ``run.py`` maps them back to call time.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

import httpx
import numpy as np

from ..audio import wav_bytes
from ..types import AsrError, AsrResult, AsrWord, Vocabulary

Region = tuple[float, float]


@dataclass(frozen=True)
class AsrAudio:
    """The audio an adapter transcribes: float32 (channels, n) samples, usually 16 kHz and VAD-compacted."""

    samples: np.ndarray
    sample_rate: int = 16_000
    layout: Literal["stereo_split", "mono"] = "mono"
    speech_regions: tuple[Region, ...] = ()  # speech in this audio's own time base

    @property
    def n_channels(self) -> int:
        return int(self.samples.shape[0]) if self.samples.ndim == 2 else 1

    @property
    def duration(self) -> float:
        return self.samples.shape[-1] / self.sample_rate

    def channel(self, i: int) -> np.ndarray:
        return self.samples[i] if self.samples.ndim == 2 else self.samples

    def wav(self, channel: int | None = None) -> bytes:
        """16-bit PCM WAV of every channel, or of one."""
        data = self.samples if channel is None else self.channel(channel)
        return wav_bytes(data, self.sample_rate)

    def chunks(self, max_s: float = 30.0) -> list[Region]:
        """Speech regions packed into chunks of at most ``max_s`` (for providers that take short clips)."""
        regions = list(self.speech_regions) or [(0.0, self.duration)]
        out: list[list[float]] = []
        for s, e in regions:
            while e - s > max_s:
                out.append([s, s + max_s])
                s += max_s
            if out and e - out[-1][0] <= max_s and s - out[-1][1] < 1.0:
                out[-1][1] = e
            else:
                out.append([s, e])
        return [(s, e) for s, e in out if e > s]

    def clip(self, t0: float, t1: float, channel: int | None = None) -> np.ndarray:
        a, b = int(round(t0 * self.sample_rate)), int(round(t1 * self.sample_rate))
        src = self.samples if channel is None else self.channel(channel)
        return src[..., max(0, a) : max(a, b)]


@runtime_checkable
class ASRProvider(Protocol):
    """Anything that turns audio into words (§5.2 step 3)."""

    name: str
    model_version: str

    async def transcribe(self, audio: AsrAudio | None, vocab: Vocabulary | None = None, want_alternatives: bool = True) -> AsrResult: ...


def fold_nbest(
    best: list[AsrWord], others: list[tuple[list[AsrWord], float]], best_conf: float = 1.0, *, max_alts: int = 4
) -> list[AsrWord]:
    """Fold n-best transcripts into per-word alternatives.

    Each word of alternative k goes to the 1-best word it overlaps most in time; words of one alternative
    that land on the same 1-best word are joined ("two fifty" against a 1-best "two-fifty"). An alternative
    differing from the 1-best text becomes an ``alts`` entry scored ``word conf × (alt conf / 1-best conf)``.
    """
    out = [w.model_copy(update={"alts": list(w.alts)}) for w in best]
    if not out:
        return out
    starts = np.array([w.t_start for w in out])
    ends = np.array([w.t_end for w in out])
    for words, conf in others:
        rel = min(1.0, conf / best_conf) if best_conf > 0 else 1.0
        groups: dict[int, list[AsrWord]] = {}
        for w in words:
            overlap = np.minimum(ends, w.t_end) - np.maximum(starts, w.t_start)
            i = int(np.argmax(overlap))
            if overlap[i] <= 0:
                i = int(np.argmin(np.abs((starts + ends) / 2 - (w.t_start + w.t_end) / 2)))
            groups.setdefault(i, []).append(w)
        for i, ws in groups.items():
            text = " ".join(w.text for w in ws)
            if text.strip().lower() == out[i].text.strip().lower():
                continue
            score = float(np.mean([w.conf for w in ws])) * rel
            known = {a.lower(): j for j, (a, _) in enumerate(out[i].alts)}
            if text.lower() in known:
                j = known[text.lower()]
                out[i].alts[j] = (out[i].alts[j][0], max(out[i].alts[j][1], score))
            else:
                out[i].alts.append((text, score))
    for w in out:
        w.alts = sorted(w.alts, key=lambda a: -a[1])[:max_alts]
    return out


@dataclass
class HttpProvider:
    """Shared plumbing for HTTP adapters: an injectable ``httpx.AsyncClient`` and a polling helper."""

    api_key: str | None = None
    base_url: str = ""
    client: httpx.AsyncClient | None = None
    timeout_s: float = 120.0
    poll_interval_s: float = 3.0
    max_wait_s: float = 1800.0
    _owned: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    def http(self) -> httpx.AsyncClient:
        if self.client is not None:
            return self.client
        if self._owned is None:
            self._owned = httpx.AsyncClient(timeout=self.timeout_s)
        return self._owned

    async def aclose(self) -> None:
        if self._owned is not None:
            await self._owned.aclose()
            self._owned = None

    @staticmethod
    def json_or_raise(resp: httpx.Response, provider: str) -> Any:
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text[:500]
            raise AsrError(f"{provider} HTTP {resp.status_code}: {detail}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    async def poll(
        self, fetch: Callable[[], Awaitable[dict]], done: Callable[[dict], bool], failed: Callable[[dict], str | None], provider: str
    ) -> dict:
        """Call ``fetch`` until ``done``; raise on ``failed`` or after ``max_wait_s``."""
        deadline = time.monotonic() + self.max_wait_s
        while True:
            body = await fetch()
            reason = failed(body)
            if reason:
                raise AsrError(f"{provider} job failed: {reason}")
            if done(body):
                return body
            if time.monotonic() > deadline:
                raise AsrError(f"{provider} job did not finish in {self.max_wait_s:.0f}s")
            await asyncio.sleep(self.poll_interval_s)


def require_key(key: str | None, provider: str) -> str:
    if not key:
        raise AsrError(f"{provider}: no API key configured")
    return key
