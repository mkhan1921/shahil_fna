"""The constrained second pass (§5.6): risky-span selection, clips, both providers, agreement, first pass untouched."""

from __future__ import annotations

import base64
import io
import json
from datetime import date

import httpx
import numpy as np
import pytest
import soundfile as sf

from advisor_intel.ingest.asr import ParakeetASR
from advisor_intel.ingest.audio import PreparedAudio
from advisor_intel.ingest.grammars import parse
from advisor_intel.ingest.run import transcript_from_script_async
from advisor_intel.ingest.second_pass import (
    AudioModelCheck,
    FakeSecondPass,
    GrammarConstrainedRedecode,
    SecondPassRequest,
    build_requests,
    run_second_pass,
    select_risky,
)
from advisor_intel.ingest.spans import annotate_transcript
from advisor_intel.models.transcript import Segment, Transcript, Word

from .audio_synth import tone


def seg(sid: str, speaker: str, start: float, items, step: float = 0.4) -> Segment:
    words, t = [], start
    for it in items:
        text, conf = (it, 0.95) if isinstance(it, str) else it
        words.append(Word(text=text, t_start=round(t, 3), t_end=round(t + step - 0.05, 3), conf=conf))
        t += step
    return Segment(
        segment_id=sid,
        speaker_label=speaker,
        t_start=words[0].t_start,
        t_end=words[-1].t_end,
        words=words,
        text=" ".join(w.text for w in words),
    )


def call() -> Transcript:
    segs = [
        seg("s0001", "ch0", 0.0, ["what", "is", "the", "bond", "at"]),
        seg("s0002", "ch1", 2.5, ["about", "two", "fifty"]),  # close readings
        seg("s0003", "ch1", 5.0, ["and", "forty", "two", "thousand", "a", "month"]),  # clear: not risky
        seg("s0004", "ch1", 9.0, ["roughly", ("eighty", 0.5), ("five", 0.55), "thousand"]),  # low confidence
        seg("s0005", "ch1", 13.0, ["two", "fifty", "thousand", "million"]),  # an unaccepted stretch
        seg("s0006", "ch0", 18.0, ["the", "RA", "has", "three", "hundred", "thousand"]),
        seg("s0007", "ch1", 19.2, ["mm", "yes"]),  # overlaps the amount above
    ]
    t = Transcript(
        call_id="c1", anchor_date=date(2026, 10, 16), segments=segs, asr_provider="t", asr_model_version="t", channel_layout="stereo_split"
    )
    return annotate_transcript(t)


def test_risky_spans_and_their_reasons():
    t = call()
    risky = {r.span.surface: r.reasons for r in select_risky(t)}
    assert risky["two fifty"] == ["close_readings"]
    assert "forty two thousand a month" not in risky
    assert risky["eighty five thousand"] == ["low_conf"]
    assert risky["million"] == ["low_conf", "no_grammar_path"] or "no_grammar_path" in risky["million"]
    assert "crosstalk" in risky["three hundred thousand"]
    assert all(r.t_end > r.t_start for r in select_risky(t))


async def test_results_are_stored_beside_the_first_pass():
    t = call()
    by_surface = {s.surface: s for s in t.numeric_spans}
    fake = FakeSecondPass(answers={by_surface["two fifty"].span_id: 250_000.0, by_surface["eighty five thousand"].span_id: 85_000.0})
    out = await run_second_pass(t, fake)
    spans = {s.surface: s for s in out.numeric_spans}
    two_fifty = spans["two fifty"]
    assert two_fifty.second_pass_reading.value == 250_000.0 and two_fifty.second_pass_method == "fake"
    top = by_surface["two fifty"].readings[0].value
    assert two_fifty.second_pass_agreement is (top == 250_000.0)
    assert two_fifty.readings == by_surface["two fifty"].readings  # the first pass is never overwritten
    assert spans["eighty five thousand"].second_pass_agreement is True
    assert spans["forty two thousand a month"].second_pass_method is None  # not risky: not re-checked
    assert spans["million"].second_pass_reading is None and spans["million"].second_pass_agreement is None
    assert len(fake.requests) == len(select_risky(t))
    req = next(r for r in fake.requests if r.span.surface == "two fifty")
    assert "[two fifty]" in req.context and req.candidates == by_surface["two fifty"].readings


def test_clips_run_two_seconds_either_side_on_the_speakers_channel():
    t = call()
    audio = PreparedAudio(samples=np.stack([tone(25, 300), tone(25, 900)]), layout="stereo_split")
    reqs = build_requests(t, select_risky(t), audio=audio)
    r = next(r for r in reqs if r.span.surface == "two fifty")
    assert r.clip_start == pytest.approx(r.span_start - 2.0)
    assert r.clip.size == pytest.approx((r.span_end + 2.0 - r.clip_start) * 16_000, abs=2)
    spec = np.abs(np.fft.rfft(r.clip))
    assert abs(np.fft.rfftfreq(r.clip.size, 1 / 16_000)[np.argmax(spec)] - 900) < 5  # ch1, the client's side
    assert build_requests(t, select_risky(t), audio=None)[0].clip is None


def _request(surface_values=(250.0, 250_000.0, 2_500_000.0)) -> SecondPassRequest:
    res = parse("two fifty", "amount")
    span = call().numeric_spans[0]
    return SecondPassRequest(
        span=span,
        kind="amount",
        candidates=res.readings,
        clip=tone(4.6, 200),
        sample_rate=16_000,
        clip_start=1.1,
        span_start=3.1,
        span_end=3.7,
        context="client: about [two fifty]",
    )


async def test_audio_model_check_uses_guided_choice_over_the_candidates():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.read())
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "model": "Qwen/Qwen3-Omni-30B-A3B-Instruct",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "R250 000"},
                        "logprobs": {"content": [{"token": "R", "logprob": -0.05}, {"token": "250 000", "logprob": -0.1}]},
                    }
                ],
            },
        )

    check = AudioModelCheck(base_url="http://vllm:8000", api_key="secret", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    req = _request()
    res = await check.check(req)
    body = seen["body"]
    labels = body["structured_outputs"]["choice"]
    assert labels == ["R250", "R250 000", "R2 500 000"] and seen["auth"] == "Bearer secret"
    assert body["temperature"] == 0 and body["model"] == "Qwen/Qwen3-Omni-30B-A3B-Instruct"
    content = body["messages"][1]["content"]
    wav = base64.b64decode(content[0]["input_audio"]["data"])
    assert sf.info(io.BytesIO(wav)).duration == pytest.approx(4.6, abs=0.01)
    assert "[two fifty]" in content[1]["text"] and "between 2.0s and 2.6s" in content[1]["text"]
    assert res.reading.value == 250_000.0 and res.method == "audio_model" and res.confidence == pytest.approx(np.exp(-0.15), abs=1e-3)

    old = AudioModelCheck(
        base_url="http://vllm:8000",
        choice_param="guided_choice",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "something else"}}]}))
        ),
    )
    assert old.body(req, old.labels(req))["guided_choice"] == ["R250", "R250 000", "R2 500 000"]
    out = await old.check(req)
    assert out.reading is None and out.raw == "something else"
    no_audio = await old.check(SecondPassRequest(**{**req.__dict__, "clip": None}))
    assert no_audio.reading is None and no_audio.raw == "no audio"


async def test_grammar_constrained_redecode_boosts_only_the_candidates():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.read())
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "channel_tag": 1,
                        "alternatives": [
                            {
                                "transcript": "about two hundred and fifty thousand so",
                                "confidence": 0.8,
                                "words": [
                                    {"word": "about", "start_time": 1500, "end_time": 1900, "confidence": 0.9},
                                    {"word": "two", "start_time": 2000, "end_time": 2150, "confidence": 0.9},
                                    {"word": "hundred", "start_time": 2150, "end_time": 2350, "confidence": 0.85},
                                    {"word": "and", "start_time": 2350, "end_time": 2400, "confidence": 0.8},
                                    {"word": "fifty", "start_time": 2400, "end_time": 2500, "confidence": 0.7},
                                    {"word": "thousand", "start_time": 2500, "end_time": 2600, "confidence": 0.9},
                                    {"word": "so", "start_time": 4000, "end_time": 4200, "confidence": 0.9},
                                ],
                            }
                        ],
                    }
                ]
            },
        )

    redecode = GrammarConstrainedRedecode(
        ParakeetASR(base_url="http://pk:9000", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    )
    req = _request()
    res = await redecode.check(req)
    phrases = [p["phrase"] for p in seen["body"]["config"]["turbobias"]["phrases"]]
    assert "two hundred and fifty thousand" in phrases and "two point five million" in phrases and "two hundred and fifty" in phrases
    assert all(
        any(parse(p, "amount").readings[k].value == c.value for c in req.candidates for k in range(len(parse(p, "amount").readings)))
        for p in phrases
    )
    assert all(p["boost"] == 4.0 for p in seen["body"]["config"]["turbobias"]["phrases"])
    assert res.reading.value == 250_000.0 and res.method == "parakeet_turbobias"
    assert res.raw == "two hundred and fifty thousand"  # words outside the span window ("about", "so") are ignored


async def test_scripted_second_pass_listens_to_the_script():
    script = """
advisor: And the house?
client: It's worth about {two fifty|means=2500000} now, and we owe {two fifty|heard=two fifteen} on it.
"""
    t = await transcript_from_script_async(script, date(2026, 10, 16), seed=1, second_pass="scripted")
    spans = [s for s in t.numeric_spans if s.kind == "amount"]
    house, bond = spans[0], spans[1]
    assert house.second_pass_reading.value == 2_500_000.0 and house.second_pass_method == "scripted"
    # "two fifteen" was heard; the script said "two fifty", which the second pass hears again
    assert bond.readings[0].value in (215.0, 215_000.0, 2_150_000.0)
    assert bond.second_pass_reading is not None and bond.second_pass_reading.value in (250.0, 250_000.0, 2_500_000.0)
    assert bond.second_pass_agreement is False
