"""Speechmatics batch API (v2).

Request/response mapping:

    POST {base}/jobs/        multipart: data_file=<WAV>, config=<JSON> with
                             {"type": "transcription", "transcription_config": {"language": "en", "operating_point": "enhanced",
                              "enable_entities": true, "additional_vocab": [{"content": "TFSA", "sounds_like": ["tee eff ess ay"]}, …],
                              "diarization": "channel" (stereo) | "none", "channel_diarization_labels": ["ch0", "ch1"]}}
                             headers {Authorization: "Bearer <key>"}                           → {"id": …}
    GET  {base}/jobs/{id}    until job.status == "done" ("rejected", "deleted", "expired" fail)
    GET  {base}/jobs/{id}/transcript?format=json-v2
         → {"metadata": {"transcription_config": …}, "results": [
              {"type": "word", "start_time", "end_time", "channel"?, "alternatives": [{"content", "confidence", "speaker"?}, …]},
              {"type": "punctuation", "alternatives": [{"content": ","}], …},
              {"type": "entity", "entity_class": "money", "start_time", "end_time", "alternatives": [{"content": "R250,000"}],
               "spoken_form": [<word results>], "written_form": [<word results>]}]}

An entity becomes one word whose ``text`` is the written form (the provider's ITN) and whose ``raw_spoken``
is the spoken form, so the grammars read the spoken words and keep the ITN as one more candidate; words
inside an entity's time span are not repeated. Extra ``alternatives`` on a word become ``alts``. The
custom vocabulary is capped at 1000 entries.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..types import AsrResult, AsrWord, Vocabulary
from .base import AsrAudio, HttpProvider, require_key


@dataclass
class SpeechmaticsASR(HttpProvider):
    base_url: str = "https://asr.api.speechmatics.com/v2"
    language: str = "en"
    operating_point: str = "enhanced"
    entities: bool = True
    vocab_limit: int = 1000
    name: str = "speechmatics"
    poll_interval_s: float = 5.0

    @property
    def model_version(self) -> str:
        return f"speechmatics-{self.operating_point}"

    def config(self, audio: AsrAudio | None, vocab: Vocabulary | None) -> dict:
        tc: dict = {"language": self.language, "operating_point": self.operating_point, "enable_entities": self.entities}
        if vocab:
            tc["additional_vocab"] = [{"content": p.text, "sounds_like": p.sounds_like} if p.sounds_like else {"content": p.text}
                                      for p in vocab.select(self.vocab_limit)]
        stereo = audio is not None and audio.layout == "stereo_split" and audio.n_channels > 1
        tc["diarization"] = "channel" if stereo else "none"
        if stereo:
            tc["channel_diarization_labels"] = [f"ch{i}" for i in range(audio.n_channels)]
        return {"type": "transcription", "transcription_config": tc}

    async def transcribe(self, audio: AsrAudio | None, vocab: Vocabulary | None = None, want_alternatives: bool = True) -> AsrResult:
        if audio is None:
            raise ValueError("Speechmatics needs audio")
        headers = {"Authorization": f"Bearer {require_key(self.api_key, 'Speechmatics')}"}
        http = self.http()
        job = self.json_or_raise(await http.post(f"{self.base_url}/jobs/", headers=headers, files={"data_file": ("audio.wav", audio.wav(), "audio/wav")},
                                         data={"config": json.dumps(self.config(audio, vocab))}), "Speechmatics")
        job_id = job["id"]

        async def fetch() -> dict:
            return self.json_or_raise(await http.get(f"{self.base_url}/jobs/{job_id}", headers=headers), "Speechmatics")

        def failed(b: dict) -> str | None:
            status = (b.get("job") or {}).get("status")
            return f"{status}: {(b.get('job') or {}).get('errors')}" if status in ("rejected", "deleted", "expired") else None

        await self.poll(fetch, lambda b: (b.get("job") or {}).get("status") == "done", failed, "Speechmatics")
        body = self.json_or_raise(await http.get(f"{self.base_url}/jobs/{job_id}/transcript", params={"format": "json-v2"}, headers=headers), "Speechmatics")
        return parse_speechmatics(body, self.model_version)


def _channel(item: dict) -> int | None:
    raw = item.get("channel") or (item.get("alternatives") or [{}])[0].get("speaker")
    m = re.search(r"(\d+)", str(raw)) if raw and str(raw).lower().startswith(("ch", "channel")) else None
    if not m:
        return None
    n = int(m.group(1))
    return n - 1 if str(raw).lower().startswith("channel_") else n


def _word(item: dict) -> AsrWord:
    alts = item.get("alternatives") or [{}]
    best = alts[0]
    return AsrWord(text=best.get("content", ""), t_start=float(item["start_time"]), t_end=float(item["end_time"]),
                   conf=float(best.get("confidence") or 0.0), alts=[(a["content"], float(a.get("confidence") or 0.0)) for a in alts[1:] if a.get("content")],
                   channel=_channel(item), speaker=best.get("speaker") if not str(best.get("speaker", "")).startswith("ch") else None)


def parse_speechmatics(body: dict, model_version: str) -> AsrResult:
    results = body.get("results") or []
    entities = [r for r in results if r.get("type") == "entity" and r.get("spoken_form")]
    spans = [(float(e["start_time"]), float(e["end_time"]), _channel(e)) for e in entities]
    words: list[AsrWord] = []
    for e in entities:
        spoken = [_word(w) for w in e["spoken_form"] if w.get("type", "word") == "word"]
        written = (e.get("alternatives") or [{}])[0].get("content") or " ".join(w.text for w in spoken)
        conf = sum(w.conf for w in spoken) / len(spoken) if spoken else float((e.get("alternatives") or [{}])[0].get("confidence") or 0.0)
        raw = " ".join(w.text for w in spoken) or None
        words.append(AsrWord(text=written, t_start=float(e["start_time"]), t_end=float(e["end_time"]), conf=round(conf, 4),
                             raw_spoken=raw if raw and raw != written else None, channel=_channel(e)))
    for r in results:
        kind = r.get("type")
        if kind == "word":
            w = _word(r)
            if any(s - 1e-3 <= w.t_start and w.t_end <= e + 1e-3 and (c is None or c == w.channel) for s, e, c in spans):
                continue
            words.append(w)
        elif kind == "punctuation" and words:
            p = (r.get("alternatives") or [{}])[0].get("content", "")
            prev = max((w for w in words if w.t_end <= float(r.get("start_time", 0)) + 1e-3), key=lambda w: w.t_end, default=None)
            if prev is not None and p:
                prev.text += p
    words.sort(key=lambda w: (w.t_start, w.channel or 0))
    has_alts = any(w.alts for w in words)
    lang = ((body.get("metadata") or {}).get("transcription_config") or {}).get("language")
    return AsrResult(words=words, provider="speechmatics", model_version=model_version, alternatives_available=has_alts, language=lang)
