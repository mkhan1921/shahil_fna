"""NVIDIA Parakeet, self-hosted behind a NeMo/Riva-style HTTP service with NeMo TurboBias phrase boosting.

Riva's own API is gRPC; this adapter assumes a thin JSON gateway that mirrors Riva's ``RecognitionConfig``
and ``RecognizeResponse`` field names, plus a ``turbobias`` block for per-stream phrase boosting (§5.3):

    POST {base}/v1/asr/recognize
    {"config": {"encoding": "LINEAR_PCM", "sample_rate_hertz": 16000, "language_code": "en", "model": "<id>",
                "max_alternatives": 4, "enable_word_time_offsets": true, "enable_word_confidence": true,
                "enable_automatic_punctuation": true, "audio_channel_count": 2,
                "enable_separate_recognition_per_channel": true,
                "turbobias": {"alpha": 1.0, "phrases": [{"phrase": "one point two million", "boost": 1.5}, …]}},
     "audio": "<base64 WAV>"}
    → {"model_version": "…", "results": [{"channel_tag": 1, "is_final": true, "audio_processed": 12.3,
         "alternatives": [{"transcript", "confidence", "words": [{"word", "start_time", "end_time", "confidence"}]}, …]}]}

Times are milliseconds (Riva's convention); ``channel_tag`` is 1-based. The n-best per result is folded
into per-word alternatives. The same service backs the grammar-constrained second pass (§5.6), which sends
a clip with only the span's readings as boost phrases.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from ..types import AsrResult, AsrWord, Vocabulary
from .base import AsrAudio, HttpProvider, fold_nbest


@dataclass
class ParakeetASR(HttpProvider):
    base_url: str = ""
    model: str = "nvidia/parakeet-tdt-0.6b-v3"
    language_code: str = "en"
    max_alternatives: int = 4
    phrase_limit: int = 2000
    boost_alpha: float = 1.0
    name: str = "parakeet"

    @property
    def model_version(self) -> str:
        return self.model

    def request_body(self, audio: AsrAudio, vocab: Vocabulary | None, want_alternatives: bool) -> dict:
        n_ch = audio.n_channels if audio.layout == "stereo_split" else 1
        cfg: dict = {
            "encoding": "LINEAR_PCM",
            "sample_rate_hertz": audio.sample_rate,
            "language_code": self.language_code,
            "model": self.model,
            "max_alternatives": self.max_alternatives if want_alternatives else 1,
            "enable_word_time_offsets": True,
            "enable_word_confidence": True,
            "enable_automatic_punctuation": True,
            "audio_channel_count": n_ch,
            "enable_separate_recognition_per_channel": n_ch > 1,
        }
        if vocab and len(vocab):
            cfg["turbobias"] = {
                "alpha": self.boost_alpha,
                "phrases": [{"phrase": p.text, "boost": p.boost} for p in vocab.select(self.phrase_limit)],
            }
        samples = audio.samples if n_ch > 1 or audio.samples.ndim == 1 else audio.samples.mean(axis=0)
        wav = AsrAudio(samples=samples, sample_rate=audio.sample_rate, layout=audio.layout).wav()
        return {"config": cfg, "audio": base64.b64encode(wav).decode("ascii")}

    async def transcribe(self, audio: AsrAudio | None, vocab: Vocabulary | None = None, want_alternatives: bool = True) -> AsrResult:
        if audio is None:
            raise ValueError("Parakeet needs audio")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        resp = await self.http().post(
            f"{self.base_url.rstrip('/')}/v1/asr/recognize", json=self.request_body(audio, vocab, want_alternatives), headers=headers
        )
        return parse_parakeet(
            self.json_or_raise(resp, "Parakeet"), self.model, multichannel=audio.layout == "stereo_split" and audio.n_channels > 1
        )


def _words(alt: dict, channel: int | None) -> list[AsrWord]:
    return [
        AsrWord(
            text=w["word"],
            t_start=float(w["start_time"]) / 1000.0,
            t_end=float(w["end_time"]) / 1000.0,
            conf=float(w.get("confidence") or 0.0),
            channel=channel,
        )
        for w in alt.get("words") or []
    ]


def parse_parakeet(body: dict, requested_model: str, *, multichannel: bool) -> AsrResult:
    words: list[AsrWord] = []
    has_alts = False
    for r in body.get("results") or []:
        alts = r.get("alternatives") or []
        if not alts:
            continue
        channel = int(r.get("channel_tag", 1)) - 1 if multichannel else None
        best = _words(alts[0], channel)
        others = [(_words(a, channel), float(a.get("confidence") or 0.0)) for a in alts[1:]]
        has_alts = has_alts or bool(others)
        words += fold_nbest(best, others, float(alts[0].get("confidence") or 1.0)) if others else best
    words.sort(key=lambda w: (w.t_start, w.channel or 0))
    return AsrResult(
        words=words, provider="parakeet", model_version=str(body.get("model_version") or requested_model), alternatives_available=has_alts
    )
