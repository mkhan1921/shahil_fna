"""Diarization adapters and word-to-speaker assignment (§5.2 step 4)."""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from advisor_intel.ingest.diarization import (
    ChannelDiarizer,
    Diarizer,
    FakeDiarizer,
    PyannoteAIDiarizer,
    assign_by_turns,
    overlap_seconds,
    parse_pyannote_output,
    relabel,
)
from advisor_intel.ingest.types import AsrError, AsrWord, SpeakerTurn

from .audio_synth import speech_like

JOB_OUTPUT = {
    "diarization": [
        {"speaker": "SPEAKER_01", "start": 0.52, "end": 4.10, "confidence": 92},
        {"speaker": "SPEAKER_00", "start": 4.30, "end": 9.80},
        {"speaker": "SPEAKER_01", "start": 9.60, "end": 12.00},
    ],
    "confidence": {"resolution": 0.5, "score": {"SPEAKER_00": [0.1] * 8 + [0.8, 0.9, 0.7, 0.9, 0.8, 0.95, 0.9, 0.85, 0.9, 0.9, 0.9, 0.9] + [0.2] * 6,
                                               "SPEAKER_01": [0.9] * 26}},
}


async def test_pyannote_upload_submit_poll_and_confidence():
    seen = {"polls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "storage.example.com":
            seen["put"] = (req.method, req.headers["content-type"], len(req.read()))
            return httpx.Response(200)
        assert req.headers["authorization"] == "Bearer pa-key"
        if req.url.path == "/v1/media/input":
            seen["media"] = json.loads(req.read())["url"]
            return httpx.Response(201, json={"url": "https://storage.example.com/presigned?sig=abc"})
        if req.url.path == "/v1/diarize":
            seen["body"] = json.loads(req.read())
            return httpx.Response(200, json={"jobId": "3c8a89a5-dcc6-4edb-a75d-ffd64739674d", "status": "created"})
        if req.url.path.startswith("/v1/jobs/"):
            seen["polls"] += 1
            status = "succeeded" if seen["polls"] > 2 else "running"
            return httpx.Response(200, json={"jobId": "3c8a89a5", "status": status, **({"output": JOB_OUTPUT} if status == "succeeded" else {})})
        return httpx.Response(404)

    d = PyannoteAIDiarizer(api_key="pa-key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), poll_interval_s=0)
    assert isinstance(d, Diarizer)
    turns = await d.diarize(speech_like(3, seed=1), 16_000, min_speakers=2, max_speakers=4, key="call-1")
    assert seen["media"] == "media://advisor-intel/call-1.wav" and seen["put"][0] == "PUT" and seen["put"][1] == "audio/wav"
    assert seen["body"] == {"url": "media://advisor-intel/call-1.wav", "model": "precision-3", "minSpeakers": 2, "maxSpeakers": 4,
                            "confidence": True, "turnLevelConfidence": True}
    assert seen["polls"] == 3
    assert [t.speaker for t in turns] == ["spk_0", "spk_1", "spk_0"]  # renamed in order of first speech
    assert turns[0].confidence == pytest.approx(0.92)  # turn-level confidence, given as a percentage
    assert turns[1].confidence == pytest.approx(np.mean([0.8, 0.9, 0.7, 0.9, 0.8, 0.95, 0.9, 0.85, 0.9, 0.9, 0.9]), abs=0.03)  # frame posteriors


async def test_pyannote_failure_and_missing_key():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/media/input":
            return httpx.Response(201, json={"url": "https://storage.example.com/p"})
        if req.method == "PUT":
            return httpx.Response(200)
        if req.url.path == "/v1/diarize":
            return httpx.Response(200, json={"jobId": "j"})
        return httpx.Response(200, json={"status": "failed", "error": "audio too short"})

    d = PyannoteAIDiarizer(api_key="k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), poll_interval_s=0)
    with pytest.raises(AsrError, match="too short"):
        await d.diarize(np.zeros(16_000, np.float32), 16_000, min_speakers=2, max_speakers=4)
    with pytest.raises(AsrError, match="no API key"):
        await PyannoteAIDiarizer().diarize(np.zeros(10), 16_000, min_speakers=2, max_speakers=4)


def test_frame_matrix_confidence_format():
    out = {"diarization": [{"speaker": "A", "start": 0.0, "end": 1.0}],
           "confidence": {"resolution": 0.25, "speakers": ["A", "B"], "score": [[0.9, 0.1], [0.8, 0.2], [0.7, 0.3], [0.6, 0.4]]}}
    assert parse_pyannote_output(out)[0].confidence == pytest.approx(0.75)
    assert parse_pyannote_output({"diarization": [{"speaker": "A", "start": 0, "end": 1}]})[0].confidence == 1.0


async def test_channel_and_fake_diarizers():
    ch = ChannelDiarizer()
    turns = ch.turns([[(0.0, 2.0), (5.0, 6.0)], [(2.5, 4.0)]])
    assert [(t.speaker, t.channel, t.confidence) for t in turns] == [("ch0", 0, 1.0), ("ch0", 0, 1.0), ("ch1", 1, 1.0)]
    fake = FakeDiarizer([SpeakerTurn(speaker="advisor", start=1.0, end=3.0), SpeakerTurn(speaker="client", start=3.2, end=5.0)],
                        jitter_s=0.2, conf_sd=0.05, seed=3)
    out = await fake.diarize(np.zeros(16_000), 16_000, min_speakers=2, max_speakers=4, key="k")
    assert [t.speaker for t in out] == ["spk_0", "spk_1"] and fake.calls[0]["max_speakers"] == 4
    assert all(abs(o.start - b.start) <= 0.2 for o, b in zip(out, fake.base)) and fake.turns() == out
    assert fake.time_base == "call"


def test_assign_words_by_turn_overlap():
    turns = [SpeakerTurn(speaker="A", start=0.0, end=5.0, confidence=0.9), SpeakerTurn(speaker="B", start=4.5, end=9.0, confidence=0.8),
             SpeakerTurn(speaker="C", start=12.0, end=14.0, confidence=0.7)]
    words = [AsrWord(text=t, t_start=s, t_end=e, conf=0.9) for t, s, e in
             [("one", 1.0, 1.4), ("two", 4.4, 4.9), ("three", 4.6, 4.9), ("four", 9.2, 9.5), ("five", 10.5, 10.8), ("six", 13.0, 13.3)]]
    a = assign_by_turns(words, turns)
    assert [x.speaker for x in a] == ["A", "A", "A", "B", "B", "C"]
    # "three" sits inside both turns: the tie goes to the more confident one; "four" is outside every turn but
    # within 1 s of B; "five" is far from every turn and keeps the previous word's speaker at half confidence.
    assert a[4].confidence == pytest.approx(0.4)
    assert assign_by_turns(words[:1], []) [0].speaker == "spk_unknown"


def test_relabel_and_overlap_seconds():
    turns = relabel([SpeakerTurn(speaker="X", start=3, end=4), SpeakerTurn(speaker="Y", start=1, end=2), SpeakerTurn(speaker="X", start=5, end=6)],
                    prefix="ch1_spk")
    assert [(t.speaker, t.start) for t in turns] == [("ch1_spk0", 1), ("ch1_spk1", 3), ("ch1_spk1", 5)]
    over = [SpeakerTurn(speaker="A", start=0, end=5), SpeakerTurn(speaker="B", start=4, end=8), SpeakerTurn(speaker="A", start=7.5, end=9)]
    assert overlap_seconds(over, 0, 10) == pytest.approx(1.5)
    assert overlap_seconds(over, 4.5, 7.6) == pytest.approx(0.6)
