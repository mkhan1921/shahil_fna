"""IBM Granite Speech, self-hosted behind vLLM's OpenAI-compatible transcription API (``vllm_base_url``).

Granite Speech is a speech-LLM: it transcribes short clips and has no native word timestamps or word
confidences, so the adapter sends the VAD speech regions packed into chunks of ≤ 30 s, one request per
chunk and channel (bounded concurrency), and offsets the times back. Assumed mapping:

    POST {vllm_base_url}/v1/audio/transcriptions   multipart: file=<WAV chunk>, model=<id>, language=en,
         response_format=verbose_json, timestamp_granularities[]=word, temperature=0,
         prompt="Vocabulary: RA, TFSA, Allan Gray, …"   (the top keyterms; Granite is prompted, not boosted)
         headers {Authorization: "Bearer <key>"} when the server was started with --api-key
    → {"text", "language", "duration", "words": [{"word", "start", "end", "probability"?}]?,
       "segments": [{"start", "end", "text", "avg_logprob"}]?}

A word's confidence is its ``probability`` when the server returns one, else exp(avg_logprob) of its
segment. When the server returns segments but no words, word times are spread over the segment in
proportion to word length (±0.3 s typical error; diarization and segmentation tolerate it). No n-best:
``alternatives_available=False``.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass

from ..audio import wav_bytes
from ..types import AsrError, AsrResult, AsrWord, Vocabulary
from .base import AsrAudio, HttpProvider


@dataclass
class GraniteSpeechASR(HttpProvider):
    base_url: str = ""
    model: str = "ibm-granite/granite-speech-4.0"
    language: str = "en"
    chunk_s: float = 30.0
    concurrency: int = 4
    prompt_terms: int = 60
    name: str = "granite"

    @property
    def model_version(self) -> str:
        return self.model

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def _chunk(self, clip, sr: int, offset: float, channel: int | None, prompt: str) -> list[AsrWord]:
        data = {
            "model": self.model,
            "language": self.language,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
            "temperature": "0",
        }
        if prompt:
            data["prompt"] = prompt
        resp = await self.http().post(
            f"{self.base_url.rstrip('/')}/v1/audio/transcriptions",
            data=data,
            files={"file": ("chunk.wav", wav_bytes(clip, sr), "audio/wav")},
            headers=self._headers(),
        )
        return parse_granite(self.json_or_raise(resp, "Granite"), offset=offset, channel=channel)

    async def transcribe(self, audio: AsrAudio | None, vocab: Vocabulary | None = None, want_alternatives: bool = True) -> AsrResult:
        if audio is None:
            raise ValueError("Granite needs audio")
        if not self.base_url:
            raise AsrError("Granite: no vllm_base_url configured")
        prompt = ("Vocabulary: " + ", ".join(vocab.terms(self.prompt_terms))) if vocab and len(vocab) else ""
        sem = asyncio.Semaphore(self.concurrency)
        channels = range(audio.n_channels) if audio.layout == "stereo_split" else [None]

        async def run(s: float, e: float, ch: int | None) -> list[AsrWord]:
            async with sem:
                mono = (
                    audio.clip(s, e, ch)
                    if ch is not None
                    else audio.clip(s, e).mean(axis=0)
                    if audio.samples.ndim == 2
                    else audio.clip(s, e)
                )
                return await self._chunk(mono, audio.sample_rate, s, ch, prompt)

        chunks = await asyncio.gather(*(run(s, e, ch) for ch in channels for s, e in audio.chunks(self.chunk_s)))
        words = sorted((w for c in chunks for w in c), key=lambda w: (w.t_start, w.channel or 0))
        return AsrResult(
            words=words,
            provider="granite",
            model_version=self.model,
            alternatives_available=False,
            language=self.language,
            duration=audio.duration,
        )


def _spread(text: str, start: float, end: float) -> list[tuple[str, float, float]]:
    toks = text.split()
    total = sum(len(t) + 1 for t in toks) or 1
    out, t = [], start
    for tok in toks:
        d = (end - start) * (len(tok) + 1) / total
        out.append((tok, t, t + d))
        t += d
    return out


def parse_granite(body: dict, *, offset: float = 0.0, channel: int | None = None) -> list[AsrWord]:
    segments = body.get("segments") or []

    def seg_conf(t: float) -> float | None:
        for s in segments:
            if s.get("avg_logprob") is not None and float(s["start"]) - 1e-3 <= t <= float(s["end"]) + 1e-3:
                return math.exp(float(s["avg_logprob"]))
        return None

    out: list[AsrWord] = []
    if body.get("words"):
        for w in body["words"]:
            start, end = float(w["start"]), float(w["end"])
            p = w.get("probability")
            conf = float(p) if p is not None else seg_conf((start + end) / 2)
            out.append(
                AsrWord(
                    text=str(w["word"]).strip(),
                    t_start=round(offset + start, 3),
                    t_end=round(offset + end, 3),
                    conf=round(min(1.0, max(0.0, conf if conf is not None else 0.0)), 4),
                    channel=channel,
                )
            )
    else:
        for s in segments:
            conf = math.exp(float(s["avg_logprob"])) if s.get("avg_logprob") is not None else 0.0
            for tok, a, b in _spread(str(s.get("text", "")), float(s["start"]), float(s["end"])):
                out.append(
                    AsrWord(
                        text=tok, t_start=round(offset + a, 3), t_end=round(offset + b, 3), conf=round(min(1.0, conf), 4), channel=channel
                    )
                )
        if not segments and body.get("text"):
            raise AsrError("Granite: response has no word or segment timestamps; use response_format=verbose_json")
    return [w for w in out if w.text]
