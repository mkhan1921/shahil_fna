"""Constrained second pass for risky spans (§5.6).

A span is risky when any of these hold: an amount, age or product span with mean word confidence below
0.75; its top two grammar readings within 2× in score; no grammar path accepted; crosstalk over it. For
each risky span the audio from 2 s before to 2 s after is re-checked by a ``SecondPassProvider`` whose
output is limited to the span's candidate readings:

1. ``GrammarConstrainedRedecode`` — Parakeet with TurboBias boosting restricted to the spoken forms of the
   candidate readings; the re-decoded words (with their n-best) go back through the grammar, and the
   candidate the re-decode supports most is the answer.
2. ``AudioModelCheck`` — an audio-capable open model (Nemotron 3 Nano Omni or Qwen3-Omni) on vLLM's
   OpenAI-compatible chat API, given the clip, the transcript context and the candidates as labels, and
   forced to answer with one label through guided (XGrammar) choice decoding.

The result goes on the span as ``second_pass_reading``, ``second_pass_agreement`` (does it match the top
first-pass reading?) and ``second_pass_method``. The first pass is never changed; disagreements are what
the reviewer sees first. ``FakeSecondPass`` and ``ScriptedSecondPass`` stand in for tests and synthetic calls.
"""

from __future__ import annotations

import asyncio
import base64
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

import httpx
import numpy as np
from pydantic import BaseModel

from ..models.transcript import NumericSpan, Reading, SpanKind, Transcript
from .asr.base import AsrAudio, HttpProvider
from .asr.fake import ScriptRender
from .asr.parakeet import ParakeetASR
from .audio import PreparedAudio, wav_bytes
from .grammars import ProductName, format_reading, lattice_readings, parse, reading_spoken_forms, same_value
from .spans import span_interval
from .types import BiasPhrase, Vocabulary

CONF_THRESHOLD = 0.75
MARGIN = 2.0
CLIP_PAD_S = 2.0
CONCURRENCY = 8


@dataclass
class RiskySpan:
    span: NumericSpan
    reasons: list[str]
    t_start: float
    t_end: float
    speaker_label: str


def select_risky(transcript: Transcript, *, conf_threshold: float = CONF_THRESHOLD, margin: float = MARGIN) -> list[RiskySpan]:
    """Spans that meet any §5.6 condition, with the reasons."""
    out = []
    for span in transcript.numeric_spans:
        reasons = []
        if span.mean_conf < conf_threshold:
            reasons.append("low_conf")
        if len(span.readings) >= 2 and span.top_margin < margin:
            reasons.append("close_readings")
        if not span.accepted:
            reasons.append("no_grammar_path")
        if "crosstalk" in span.flags:
            reasons.append("crosstalk")
        if reasons:
            seg = transcript.segment(span.segment_id)
            t0, t1 = span_interval(seg, span)
            out.append(RiskySpan(span=span, reasons=reasons, t_start=t0, t_end=t1, speaker_label=seg.speaker_label))
    return out


@dataclass
class SecondPassRequest:
    span: NumericSpan
    kind: SpanKind
    candidates: list[Reading]  # the only allowed answers
    clip: np.ndarray | None  # mono samples from 2 s before to 2 s after the span (None without audio)
    sample_rate: int
    clip_start: float  # call time of the clip's first sample
    span_start: float
    span_end: float
    context: str  # the segment with the span in [brackets], plus its neighbours
    reasons: list[str] = field(default_factory=list)
    extra_products: tuple[ProductName, ...] = ()


class SecondPassResult(BaseModel):
    reading: Reading | None
    method: str
    confidence: float | None = None
    raw: str | None = None


@runtime_checkable
class SecondPassProvider(Protocol):
    method: str

    async def check(self, request: SecondPassRequest) -> SecondPassResult: ...


def best_candidate(decoded: Sequence[Reading], candidates: Sequence[Reading]) -> tuple[Reading | None, float]:
    """The candidate the decoded readings support most (summed decoded weight of matching readings)."""
    best, best_mass = None, 0.0
    for c in candidates:
        mass = sum(d.weight for d in decoded if same_value(d, c))
        if mass > best_mass:
            best, best_mass = c, mass
    return best, best_mass


class GrammarConstrainedRedecode:
    """Option 1: Parakeet + TurboBias restricted to the span's readings, mapped back through the grammar."""

    method = "parakeet_turbobias"

    def __init__(self, parakeet: ParakeetASR, *, boost: float = 4.0, window_pad_s: float = 0.4):
        self.parakeet, self.boost, self.window_pad_s = parakeet, boost, window_pad_s

    def phrases(self, request: SecondPassRequest) -> Vocabulary:
        seen, phrases = set(), []
        for r in request.candidates:
            for text in reading_spoken_forms(r, request.kind):
                if text not in seen:
                    seen.add(text)
                    phrases.append(BiasPhrase(text=text, kind="amount" if request.kind != "product" else "product", boost=self.boost,
                                              source=request.span.span_id))
        return Vocabulary(phrases=phrases)

    async def check(self, request: SecondPassRequest) -> SecondPassResult:
        if request.clip is None or request.clip.size == 0:
            return SecondPassResult(reading=None, method=self.method, raw="no audio")
        audio = AsrAudio(samples=request.clip[None, :], sample_rate=request.sample_rate, layout="mono")
        result = await self.parakeet.transcribe(audio, self.phrases(request), want_alternatives=True)
        lo = request.span_start - request.clip_start - self.window_pad_s
        hi = request.span_end - request.clip_start + self.window_pad_s
        words = [w for w in result.words if w.t_end > lo and w.t_start < hi]
        heard = " ".join(w.text for w in words)
        if not words:
            return SecondPassResult(reading=None, method=self.method, raw="")
        decoded = lattice_readings([[(w.text, max(w.conf, 1e-3)), *w.alts] for w in words], request.kind, extra_products=request.extra_products)
        reading, mass = best_candidate(decoded.readings, request.candidates)
        return SecondPassResult(reading=reading, method=self.method, confidence=round(mass, 4) if reading else None, raw=heard)


@dataclass
class AudioModelCheck(HttpProvider):
    """Option 2: an audio LLM on vLLM chooses one candidate label under guided choice decoding."""

    base_url: str = ""
    model: str = "Qwen/Qwen3-Omni-30B-A3B-Instruct"
    choice_param: Literal["structured_outputs", "guided_choice"] = "structured_outputs"  # newer vLLM / older vLLM
    method: str = "audio_model"

    def labels(self, request: SecondPassRequest) -> list[str]:
        out: list[str] = []
        for r in request.candidates:
            label = format_reading(r, request.kind)
            while label in out:
                label += " (alt)"
            out.append(label)
        return out

    def body(self, request: SecondPassRequest, labels: list[str]) -> dict:
        audio = base64.b64encode(wav_bytes(request.clip, request.sample_rate)).decode("ascii")
        a, b = request.span_start - request.clip_start, request.span_end - request.clip_start
        what = {"amount": "amount of money or rate", "age": "age", "product": "financial product or provider"}[request.kind]
        text = (
            f"Transcript context (the uncertain words in [brackets]):\n{request.context}\n\n"
            f"In this clip, between {a:.1f}s and {b:.1f}s, the speaker states an {what}. The transcription heard "
            f"\"{request.span.surface}\". Which of these did they mean?\n" + "\n".join(f"- {lab}" for lab in labels) +
            "\nAnswer with one option, exactly as written."
        )
        constraint = {"structured_outputs": {"choice": labels}} if self.choice_param == "structured_outputs" else {"guided_choice": labels}
        return {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 32,
            "logprobs": True,
            "messages": [
                {"role": "system", "content": "You check numbers and product names in recordings of South African financial advice meetings."},
                {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}}, {"type": "text", "text": text}]},
            ],
            **constraint,
        }

    async def check(self, request: SecondPassRequest) -> SecondPassResult:
        if request.clip is None or request.clip.size == 0:
            return SecondPassResult(reading=None, method=self.method, raw="no audio")
        labels = self.labels(request)
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        resp = await self.http().post(f"{self.base_url.rstrip('/')}/v1/chat/completions", json=self.body(request, labels), headers=headers)
        body = self.check_response(resp)
        choice = (body.get("choices") or [{}])[0]
        content = ((choice.get("message") or {}).get("content") or "").strip()
        reading = request.candidates[labels.index(content)] if content in labels else None
        lp = ((choice.get("logprobs") or {}).get("content")) or []
        conf = math.exp(sum(float(t.get("logprob", 0.0)) for t in lp)) if lp else None
        return SecondPassResult(reading=reading, method=self.method, confidence=round(conf, 4) if conf is not None else None, raw=content)

    def check_response(self, resp: httpx.Response) -> dict:
        return HttpProvider.json_or_raise(resp, "audio model")


class FakeSecondPass:
    """Answers from a callable or a span_id → value table (choosing among the candidates)."""

    method = "fake"

    def __init__(self, choose: Callable[[SecondPassRequest], Reading | float | str | None] | None = None, *,
                 answers: dict[str, float | str] | None = None, method: str = "fake"):
        self.choose, self.answers, self.method = choose, answers or {}, method
        self.requests: list[SecondPassRequest] = []

    async def check(self, request: SecondPassRequest) -> SecondPassResult:
        self.requests.append(request)
        pick = self.choose(request) if self.choose else self.answers.get(request.span.span_id)
        if pick is None or isinstance(pick, Reading):
            return SecondPassResult(reading=pick, method=self.method)
        for c in request.candidates:
            if (isinstance(pick, str) and c.value == pick) or (not isinstance(pick, str) and not isinstance(c.value, str)
                                                                and math.isclose(float(c.value), float(pick), rel_tol=1e-9)):
                return SecondPassResult(reading=c, method=self.method)
        return SecondPassResult(reading=None, method=self.method, raw=str(pick))


class ScriptedSecondPass:
    """Re-listens through a ``ScriptRender``: what was really said in the span (and what it meant, from
    ``means=`` markup) picks the candidate. Stands in for the audio model on synthetic calls."""

    method = "scripted"

    def __init__(self, render: ScriptRender):
        self.render = render

    async def check(self, request: SecondPassRequest) -> SecondPassResult:
        a, b = request.span_start - 0.05, request.span_end + 0.05
        for it in self.render.intended:
            if it.t_end > a and it.t_start < b:
                for c in request.candidates:
                    if (isinstance(it.value, str) and c.value == it.value) or (
                            not isinstance(it.value, str) and not isinstance(c.value, str) and math.isclose(float(c.value), float(it.value))):
                        return SecondPassResult(reading=c, method=self.method, confidence=1.0, raw=it.said)
        said = " ".join(w.text.strip(",.?!") for w in self.render.reference if w.t_end > a and w.t_start < b)
        decoded = parse(said, request.kind, extra_products=request.extra_products).readings if said else []
        reading, mass = best_candidate(decoded, request.candidates)
        return SecondPassResult(reading=reading, method=self.method, confidence=round(mass, 4) if reading else None, raw=said)


def channel_for_label(label: str) -> int | None:
    m = re.match(r"^ch(\d+)", label)
    return int(m.group(1)) if m else None


def _context(transcript: Transcript, span: NumericSpan) -> str:
    segs = transcript.segments
    idx = next(i for i, s in enumerate(segs) if s.segment_id == span.segment_id)
    lines = []
    for s in segs[max(0, idx - 2) : idx + 2]:
        if s.segment_id == span.segment_id:
            words = [w.text for w in s.words]
            words[span.word_start] = "[" + words[span.word_start]
            words[span.word_end - 1] = words[span.word_end - 1] + "]"
            text = " ".join(words)
        else:
            text = s.text
        lines.append(f"{s.speaker_role if s.speaker_role != 'unknown' else s.speaker_label}: {text}")
    return "\n".join(lines)


def build_requests(transcript: Transcript, risky: Sequence[RiskySpan], *, audio: PreparedAudio | None,
                   extra_products: Sequence[ProductName] = (), pad_s: float = CLIP_PAD_S) -> list[SecondPassRequest]:
    reqs = []
    for r in risky:
        clip_start = max(0.0, r.t_start - pad_s)
        clip = None
        sr = audio.sample_rate if audio is not None else 16_000
        if audio is not None:
            ch = channel_for_label(r.speaker_label) if audio.layout == "stereo_split" else None
            clip = audio.clip(clip_start, r.t_end + pad_s, ch)
        reqs.append(SecondPassRequest(span=r.span, kind=r.span.kind, candidates=list(r.span.readings), clip=clip, sample_rate=sr,
                                      clip_start=clip_start, span_start=r.t_start, span_end=r.t_end, context=_context(transcript, r.span),
                                      reasons=r.reasons, extra_products=tuple(extra_products)))
    return reqs


async def run_second_pass(transcript: Transcript, provider: SecondPassProvider, *, audio: PreparedAudio | None = None,
                          extra_products: Sequence[ProductName] = (), concurrency: int = CONCURRENCY) -> Transcript:
    """Re-check every risky span and store the results beside the first pass (§5.6)."""
    risky = select_risky(transcript)
    if not risky:
        return transcript
    requests = build_requests(transcript, risky, audio=audio, extra_products=extra_products)
    sem = asyncio.Semaphore(concurrency)

    async def one(req: SecondPassRequest) -> SecondPassResult:
        async with sem:
            return await provider.check(req)

    results = await asyncio.gather(*(one(r) for r in requests))
    by_id = {req.span.span_id: res for req, res in zip(requests, results)}
    spans = []
    for s in transcript.numeric_spans:
        res = by_id.get(s.span_id)
        if res is None:
            spans.append(s)
            continue
        top = s.readings[0] if s.readings else None
        agreement = same_value(res.reading, top) if res.reading is not None and top is not None else None
        spans.append(s.model_copy(update={"second_pass_reading": res.reading, "second_pass_agreement": agreement, "second_pass_method": res.method}))
    return transcript.model_copy(update={"numeric_spans": spans})
