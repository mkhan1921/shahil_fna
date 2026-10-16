"""Deepgram (Nova-3) over its pre-recorded ``/v1/listen`` API.

Request/response mapping:

    POST {base}/v1/listen?model=nova-3&language=en&punctuate=true&smart_format=false&numerals=false
                          &multichannel=true (stereo)&alternatives=3&keyterm=<term>&keyterm=<term>…
         headers {Authorization: "Token <key>", Content-Type: audio/wav}; body: WAV bytes
    → {"metadata": {"model_info": {<uuid>: {"name", "version", "arch"}}, "duration"},
       "results": {"channels": [{"alternatives": [{"transcript", "confidence",
                   "words": [{"word", "start", "end", "confidence", "punctuated_word"}]}, …]}, …]}}

Times are seconds. ``smart_format`` and ``numerals`` stay off so numbers come back spoken. The n-best
alternatives (n = 3) are folded into per-word alternatives by time overlap (``fold_nbest``). Keyterm
prompting is capped at 100 terms.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..types import AsrResult, AsrWord, Vocabulary
from .base import AsrAudio, HttpProvider, fold_nbest, require_key


@dataclass
class DeepgramASR(HttpProvider):
    base_url: str = "https://api.deepgram.com"
    model: str = "nova-3"
    language: str = "en"
    n_alternatives: int = 3
    keyterm_limit: int = 100
    name: str = "deepgram"

    @property
    def model_version(self) -> str:
        return self.model

    def params(self, audio: AsrAudio | None, vocab: Vocabulary | None, want_alternatives: bool) -> list[tuple[str, str]]:
        p = [("model", self.model), ("language", self.language), ("punctuate", "true"), ("smart_format", "false"), ("numerals", "false")]
        if audio is not None and audio.layout == "stereo_split" and audio.n_channels > 1:
            p.append(("multichannel", "true"))
        if want_alternatives and self.n_alternatives > 1:
            p.append(("alternatives", str(self.n_alternatives)))
        p += [("keyterm", t) for t in (vocab.terms(self.keyterm_limit) if vocab else [])]
        return p

    async def transcribe(self, audio: AsrAudio | None, vocab: Vocabulary | None = None, want_alternatives: bool = True) -> AsrResult:
        if audio is None:
            raise ValueError("Deepgram needs audio")
        headers = {"Authorization": f"Token {require_key(self.api_key, 'Deepgram')}", "Content-Type": "audio/wav"}
        resp = await self.http().post(
            f"{self.base_url}/v1/listen", params=self.params(audio, vocab, want_alternatives), content=audio.wav(), headers=headers
        )
        return parse_deepgram(
            self.json_or_raise(resp, "Deepgram"), self.model, multichannel=audio.layout == "stereo_split" and audio.n_channels > 1
        )


def _words(alt: dict, channel: int | None) -> list[AsrWord]:
    return [
        AsrWord(
            text=w.get("punctuated_word") or w["word"],
            t_start=float(w["start"]),
            t_end=float(w["end"]),
            conf=float(w.get("confidence") or 0.0),
            channel=channel,
            speaker=str(w["speaker"]) if w.get("speaker") is not None else None,
        )
        for w in alt.get("words") or []
    ]


def parse_deepgram(body: dict, requested_model: str, *, multichannel: bool) -> AsrResult:
    words: list[AsrWord] = []
    has_alts = False
    for c, ch in enumerate((body.get("results") or {}).get("channels") or []):
        alts = ch.get("alternatives") or []
        if not alts:
            continue
        channel = c if multichannel else None
        best = _words(alts[0], channel)
        others = [(_words(a, channel), float(a.get("confidence") or 0.0)) for a in alts[1:]]
        has_alts = has_alts or bool(others)
        words += fold_nbest(best, others, float(alts[0].get("confidence") or 1.0)) if others else best
    meta = body.get("metadata") or {}
    info = next(iter((meta.get("model_info") or {}).values()), {})
    version = f"{info.get('name', requested_model)}:{info['version']}" if info.get("version") else requested_model
    words.sort(key=lambda w: (w.t_start, w.channel or 0))
    return AsrResult(
        words=words,
        provider="deepgram",
        model_version=version,
        alternatives_available=has_alts,
        language=None,
        duration=meta.get("duration"),
    )
