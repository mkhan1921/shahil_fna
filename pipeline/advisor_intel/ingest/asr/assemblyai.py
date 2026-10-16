"""AssemblyAI (Universal-3) over its v2 REST API.

Request/response mapping:

    POST {base}/v2/upload            headers {authorization: key}; body: WAV bytes      → {"upload_url": …}
    POST {base}/v2/transcript        {"audio_url", "speech_model", "language_code", "punctuate": true,
                                      "format_text": false (spoken numbers for the grammars), "multichannel": stereo,
                                      "keyterms_prompt": [...]  (Universal-3 / Slam-1; ≤ 1000 terms of ≤ 6 words)
                                      | "word_boost": [...], "boost_param": "high"  (Universal-2 and older)} → {"id", "status"}
    GET  {base}/v2/transcript/{id}   until status == "completed" ("error" fails)       → {"words": [{"text", "start", "end",
                                      "confidence", "speaker", "channel"}], "speech_model", "language_code", "audio_duration"}

Times are milliseconds; ``channel`` is "1"-based. AssemblyAI returns no n-best and no word alternatives,
so results carry ``alternatives_available=False``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..types import AsrResult, AsrWord, Vocabulary
from .base import AsrAudio, HttpProvider, require_key

WORD_BOOST_MODELS = ("universal-2", "best", "nano", "universal")


@dataclass
class AssemblyAIASR(HttpProvider):
    base_url: str = "https://api.assemblyai.com"
    model: str = "universal-3"
    language_code: str = "en"
    spoken_form: bool = True  # format_text=false: words stay as spoken, which the grammars read best
    keyterm_limit: int = 1000
    boost_param: str = "high"
    name: str = "assemblyai"
    poll_interval_s: float = 3.0

    @property
    def model_version(self) -> str:
        return self.model

    def request_body(self, audio_url: str, audio: AsrAudio | None, vocab: Vocabulary | None) -> dict:
        body: dict = {
            "audio_url": audio_url,
            "speech_model": self.model,
            "language_code": self.language_code,
            "punctuate": True,
            "format_text": not self.spoken_form,
            "disfluencies": False,
            "multichannel": bool(audio is not None and audio.layout == "stereo_split" and audio.n_channels > 1),
        }
        terms = vocab.terms(self.keyterm_limit, max_words=6) if vocab else []
        if terms:
            if self.model in WORD_BOOST_MODELS:
                body["word_boost"] = terms
                body["boost_param"] = self.boost_param
            else:
                body["keyterms_prompt"] = terms
        return body

    async def transcribe(self, audio: AsrAudio | None, vocab: Vocabulary | None = None, want_alternatives: bool = True) -> AsrResult:
        if audio is None:
            raise ValueError("AssemblyAI needs audio")
        headers = {"authorization": require_key(self.api_key, "AssemblyAI")}
        http = self.http()
        up = self.json_or_raise(
            await http.post(
                f"{self.base_url}/v2/upload", content=audio.wav(), headers={**headers, "content-type": "application/octet-stream"}
            ),
            "AssemblyAI",
        )
        job = self.json_or_raise(
            await http.post(f"{self.base_url}/v2/transcript", json=self.request_body(up["upload_url"], audio, vocab), headers=headers),
            "AssemblyAI",
        )

        async def fetch() -> dict:
            return self.json_or_raise(await http.get(f"{self.base_url}/v2/transcript/{job['id']}", headers=headers), "AssemblyAI")

        done = await self.poll(
            fetch,
            lambda b: b.get("status") == "completed",
            lambda b: b.get("error") or "error" if b.get("status") == "error" else None,
            "AssemblyAI",
        )
        return parse_assemblyai(done, self.model)


def parse_assemblyai(body: dict, requested_model: str) -> AsrResult:
    words = []
    for w in body.get("words") or []:
        ch = w.get("channel")
        words.append(
            AsrWord(
                text=w["text"],
                t_start=w["start"] / 1000.0,
                t_end=w["end"] / 1000.0,
                conf=float(w.get("confidence") or 0.0),
                channel=int(ch) - 1 if ch not in (None, "") else None,
                speaker=w.get("speaker"),
            )
        )
    model = body.get("speech_model") or requested_model
    return AsrResult(
        words=words,
        provider="assemblyai",
        model_version=str(model),
        alternatives_available=False,
        language=body.get("language_code"),
        duration=body.get("audio_duration"),
    )
