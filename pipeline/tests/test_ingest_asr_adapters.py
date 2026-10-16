"""ASR adapters against their documented HTTP APIs, with httpx.MockTransport and realistic payloads (§5.2 step 3)."""

from __future__ import annotations

import base64
import io
import json

import httpx
import numpy as np
import pytest
import soundfile as sf

from advisor_intel.config import Settings
from advisor_intel.ingest.asr import (
    AsrAudio,
    ASRProvider,
    AssemblyAIASR,
    DeepgramASR,
    GraniteSpeechASR,
    ParakeetASR,
    SpeechmaticsASR,
    asr_from_pins,
    fold_nbest,
)
from advisor_intel.ingest.types import AsrError, AsrWord, BiasPhrase, Vocabulary
from advisor_intel.models.runs import VersionPins

from .audio_synth import speech_like

VOCAB = Vocabulary(
    phrases=[
        BiasPhrase(text="Thabo Mokoena", kind="name", boost=2.0),
        BiasPhrase(text="Allan Gray", kind="product", boost=2.0),
        BiasPhrase(text="one point two million", kind="amount", boost=1.5),
        BiasPhrase(text="TFSA", kind="vocab", sounds_like=["tee eff ess ay"]),
        BiasPhrase(text="twelve million", kind="distractor", boost=0.6),
    ]
)


def mono_audio(seconds: float = 2.0) -> AsrAudio:
    return AsrAudio(samples=speech_like(seconds, seed=1)[None, :], sample_rate=16_000, layout="mono", speech_regions=((0.0, seconds),))


def stereo_audio(seconds: float = 2.0) -> AsrAudio:
    return AsrAudio(
        samples=np.stack([speech_like(seconds, seed=1), speech_like(seconds, seed=2)]), sample_rate=16_000, layout="stereo_split"
    )


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ AssemblyAI

AAI_DONE = {
    "id": "6rlr37h8vi-1c1d-4bd4-a1a5-ff2e0d27ab32",
    "status": "completed",
    "speech_model": "universal-3",
    "language_code": "en",
    "audio_duration": 4,
    "text": "I take home about forty two thousand a month. Great.",
    "words": [
        {"text": "I", "start": 240, "end": 360, "confidence": 0.99, "speaker": None, "channel": "2"},
        {"text": "take", "start": 360, "end": 560, "confidence": 0.98, "speaker": None, "channel": "2"},
        {"text": "home", "start": 560, "end": 800, "confidence": 0.97, "speaker": None, "channel": "2"},
        {"text": "about", "start": 800, "end": 1040, "confidence": 0.96, "speaker": None, "channel": "2"},
        {"text": "forty", "start": 1040, "end": 1320, "confidence": 0.71, "speaker": None, "channel": "2"},
        {"text": "two", "start": 1320, "end": 1480, "confidence": 0.88, "speaker": None, "channel": "2"},
        {"text": "thousand", "start": 1480, "end": 1900, "confidence": 0.95, "speaker": None, "channel": "2"},
        {"text": "a", "start": 1900, "end": 1960, "confidence": 0.93, "speaker": None, "channel": "2"},
        {"text": "month.", "start": 1960, "end": 2300, "confidence": 0.97, "speaker": None, "channel": "2"},
        {"text": "Great.", "start": 2600, "end": 3000, "confidence": 0.99, "speaker": None, "channel": "1"},
    ],
}


async def test_assemblyai_upload_submit_poll_and_map():
    seen: dict = {"polls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "aai-key"
        if req.url.path == "/v2/upload":
            seen["upload_bytes"] = len(req.read())
            return httpx.Response(200, json={"upload_url": "https://cdn.assemblyai.com/upload/abc"})
        if req.url.path == "/v2/transcript" and req.method == "POST":
            seen["body"] = json.loads(req.read())
            return httpx.Response(200, json={"id": AAI_DONE["id"], "status": "queued"})
        if req.url.path == f"/v2/transcript/{AAI_DONE['id']}":
            seen["polls"] += 1
            return httpx.Response(200, json=AAI_DONE if seen["polls"] > 1 else {"id": AAI_DONE["id"], "status": "processing"})
        return httpx.Response(404)

    asr = AssemblyAIASR(api_key="aai-key", client=client_for(handler), poll_interval_s=0)
    assert isinstance(asr, ASRProvider)
    res = await asr.transcribe(stereo_audio(), VOCAB)
    body = seen["body"]
    assert body["audio_url"] == "https://cdn.assemblyai.com/upload/abc" and body["speech_model"] == "universal-3"
    assert body["multichannel"] is True and body["format_text"] is False and body["punctuate"] is True
    assert body["keyterms_prompt"][:2] == ["Thabo Mokoena", "Allan Gray"] and "word_boost" not in body
    assert seen["upload_bytes"] > 1000 and seen["polls"] == 2
    assert res.provider == "assemblyai" and res.model_version == "universal-3" and res.alternatives_available is False
    assert [w.text for w in res.words][:3] == ["I", "take", "home"]
    assert res.words[4].t_start == pytest.approx(1.04) and res.words[4].conf == 0.71 and res.words[4].alts == []
    assert res.words[0].channel == 1 and res.words[-1].channel == 0


async def test_assemblyai_word_boost_for_older_models_and_errors():
    asr = AssemblyAIASR(api_key="k", model="universal-2")
    body = asr.request_body("u", mono_audio(), VOCAB)
    assert body["word_boost"] and body["boost_param"] == "high" and "keyterms_prompt" not in body and body["multichannel"] is False

    def failing(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v2/upload":
            return httpx.Response(200, json={"upload_url": "u"})
        if req.method == "POST":
            return httpx.Response(200, json={"id": "x", "status": "queued"})
        return httpx.Response(200, json={"id": "x", "status": "error", "error": "Audio file is corrupt"})

    with pytest.raises(AsrError, match="corrupt"):
        await AssemblyAIASR(api_key="k", client=client_for(failing), poll_interval_s=0).transcribe(mono_audio())
    with pytest.raises(AsrError, match="no API key"):
        await AssemblyAIASR(api_key=None).transcribe(mono_audio())

    def unauthorised(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Authentication error, API token missing/invalid"})

    with pytest.raises(AsrError, match="401"):
        await AssemblyAIASR(api_key="bad", client=client_for(unauthorised)).transcribe(mono_audio())


# ------------------------------------------------------------------ Deepgram


def _dg_words(spec):
    return [{"word": w, "start": s, "end": e, "confidence": c, "punctuated_word": p} for w, s, e, c, p in spec]


DG_RESPONSE = {
    "metadata": {
        "request_id": "a1b2c3",
        "duration": 4.0,
        "channels": 2,
        "model_info": {"30089e05-99d1-4376-b32e-c263170674af": {"name": "general-nova-3", "version": "2025-04-17.21547", "arch": "nova-3"}},
    },
    "results": {
        "channels": [
            {
                "alternatives": [
                    {
                        "transcript": "and what is the bond",
                        "confidence": 0.99,
                        "words": _dg_words(
                            [
                                ("and", 0.1, 0.3, 0.99, "And"),
                                ("what", 0.3, 0.5, 0.99, "what"),
                                ("is", 0.5, 0.6, 0.99, "is"),
                                ("the", 0.6, 0.7, 0.99, "the"),
                                ("bond", 0.7, 1.0, 0.98, "bond?"),
                            ]
                        ),
                    },
                ]
            },
            {
                "alternatives": [
                    {
                        "transcript": "it's about two fifty",
                        "confidence": 0.86,
                        "words": _dg_words(
                            [
                                ("it's", 1.4, 1.6, 0.97, "It's"),
                                ("about", 1.6, 1.9, 0.95, "about"),
                                ("two", 1.9, 2.2, 0.9, "two"),
                                ("fifty", 2.2, 2.6, 0.62, "fifty."),
                            ]
                        ),
                    },
                    {
                        "transcript": "it's about two fifteen",
                        "confidence": 0.74,
                        "words": _dg_words(
                            [
                                ("it's", 1.4, 1.6, 0.97, "It's"),
                                ("about", 1.6, 1.9, 0.95, "about"),
                                ("two", 1.9, 2.2, 0.9, "two"),
                                ("fifteen", 2.2, 2.6, 0.55, "fifteen."),
                            ]
                        ),
                    },
                    {
                        "transcript": "it's about to fifty",
                        "confidence": 0.61,
                        "words": _dg_words(
                            [
                                ("it's", 1.4, 1.6, 0.97, "It's"),
                                ("about", 1.6, 1.9, 0.95, "about"),
                                ("to", 1.9, 2.2, 0.5, "to"),
                                ("fifty", 2.2, 2.6, 0.62, "fifty."),
                            ]
                        ),
                    },
                ]
            },
        ]
    },
}


async def test_deepgram_params_keyterms_and_nbest_folding():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["params"] = req.url.params
        seen["auth"] = req.headers["authorization"]
        seen["ctype"] = req.headers["content-type"]
        return httpx.Response(200, json=DG_RESPONSE)

    res = await DeepgramASR(api_key="dg-key", client=client_for(handler)).transcribe(stereo_audio(), VOCAB)
    p = seen["params"]
    assert seen["auth"] == "Token dg-key" and seen["ctype"] == "audio/wav"
    assert p["model"] == "nova-3" and p["smart_format"] == "false" and p["numerals"] == "false" and p["multichannel"] == "true"
    assert p["alternatives"] == "3" and p.get_list("keyterm")[0] == "Thabo Mokoena" and len(p.get_list("keyterm")) == 5
    assert res.model_version == "general-nova-3:2025-04-17.21547" and res.alternatives_available
    fifty = next(w for w in res.words if w.text == "fifty.")
    assert fifty.channel == 1 and fifty.conf == 0.62
    assert fifty.alts[0][0] == "fifteen." and fifty.alts[0][1] == pytest.approx(0.55 * 0.74 / 0.86, rel=1e-3)
    two = next(w for w in res.words if w.text == "two")
    assert two.alts and two.alts[0][0] == "to"
    assert next(w for w in res.words if w.text == "bond?").channel == 0


async def test_deepgram_without_alternatives():
    single = json.loads(json.dumps(DG_RESPONSE))
    for ch in single["results"]["channels"]:
        ch["alternatives"] = ch["alternatives"][:1]
    res = await DeepgramASR(api_key="k", client=client_for(lambda r: httpx.Response(200, json=single))).transcribe(
        mono_audio(), None, False
    )
    assert res.alternatives_available is False and all(w.alts == [] for w in res.words) and all(w.channel is None for w in res.words)


# ------------------------------------------------------------------ Speechmatics

SM_TRANSCRIPT = {
    "format": "2.9",
    "metadata": {
        "created_at": "2026-10-16T09:12:00.000Z",
        "type": "transcription",
        "transcription_config": {"language": "en", "operating_point": "enhanced", "diarization": "none", "enable_entities": True},
    },
    "results": [
        {"type": "word", "start_time": 0.42, "end_time": 0.66, "alternatives": [{"content": "We", "confidence": 1.0, "language": "en"}]},
        {"type": "word", "start_time": 0.66, "end_time": 0.84, "alternatives": [{"content": "put", "confidence": 0.99, "language": "en"}]},
        {
            "type": "entity",
            "entity_class": "money",
            "start_time": 0.84,
            "end_time": 2.4,
            "alternatives": [{"content": "R250,000", "confidence": 0.82}],
            "spoken_form": [
                {"type": "word", "start_time": 0.84, "end_time": 1.02, "alternatives": [{"content": "two", "confidence": 0.93}]},
                {"type": "word", "start_time": 1.02, "end_time": 1.38, "alternatives": [{"content": "hundred", "confidence": 0.9}]},
                {"type": "word", "start_time": 1.38, "end_time": 1.5, "alternatives": [{"content": "and", "confidence": 0.85}]},
                {"type": "word", "start_time": 1.5, "end_time": 1.8, "alternatives": [{"content": "fifty", "confidence": 0.71}]},
                {"type": "word", "start_time": 1.8, "end_time": 2.1, "alternatives": [{"content": "thousand", "confidence": 0.95}]},
                {"type": "word", "start_time": 2.1, "end_time": 2.4, "alternatives": [{"content": "rand", "confidence": 0.9}]},
            ],
            "written_form": [
                {"type": "word", "start_time": 0.84, "end_time": 2.4, "alternatives": [{"content": "R250,000", "confidence": 0.82}]}
            ],
        },
        {"type": "word", "start_time": 0.84, "end_time": 2.4, "alternatives": [{"content": "R250,000", "confidence": 0.82}]},
        {"type": "word", "start_time": 2.5, "end_time": 2.8, "alternatives": [{"content": "into", "confidence": 0.97}]},
        {
            "type": "word",
            "start_time": 2.8,
            "end_time": 3.4,
            "alternatives": [{"content": "TFSA", "confidence": 0.64}, {"content": "TSA", "confidence": 0.2}],
        },
        {"type": "punctuation", "start_time": 3.4, "end_time": 3.4, "is_eos": True, "alternatives": [{"content": ".", "confidence": 1.0}]},
    ],
}


async def test_speechmatics_job_with_entities_and_vocab():
    seen = {"polls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer sm-key"
        if req.method == "POST" and req.url.path == "/v2/jobs/":
            seen["body"] = req.read()
            return httpx.Response(201, json={"id": "dlwpdfiv8i"})
        if req.url.path == "/v2/jobs/dlwpdfiv8i":
            seen["polls"] += 1
            return httpx.Response(200, json={"job": {"id": "dlwpdfiv8i", "status": "done" if seen["polls"] > 1 else "running"}})
        if req.url.path == "/v2/jobs/dlwpdfiv8i/transcript":
            assert req.url.params["format"] == "json-v2"
            return httpx.Response(200, json=SM_TRANSCRIPT)
        return httpx.Response(404)

    res = await SpeechmaticsASR(api_key="sm-key", client=client_for(handler), poll_interval_s=0).transcribe(mono_audio(), VOCAB)
    body = seen["body"]
    assert b'name="data_file"' in body and b'name="config"' in body
    config = json.loads(body.split(b'name="config"\r\n\r\n')[1].split(b"\r\n--")[0])
    tc = config["transcription_config"]
    assert tc["enable_entities"] is True and tc["diarization"] == "none" and tc["operating_point"] == "enhanced"
    assert {"content": "TFSA", "sounds_like": ["tee eff ess ay"]} in tc["additional_vocab"]
    texts = [w.text for w in res.words]
    assert texts == ["We", "put", "R250,000", "into", "TFSA."]
    money = res.words[2]
    assert money.raw_spoken == "two hundred and fifty thousand rand" and money.conf == pytest.approx(0.8733, abs=1e-3)
    assert res.words[-1].alts == [("TSA", 0.2)] and res.alternatives_available
    assert res.model_version == "speechmatics-enhanced" and res.language == "en"


async def test_speechmatics_channel_diarization_config_and_rejection():
    cfg = SpeechmaticsASR(api_key="k").config(stereo_audio(), None)["transcription_config"]
    assert cfg["diarization"] == "channel" and cfg["channel_diarization_labels"] == ["ch0", "ch1"]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(201, json={"id": "j"})
        return httpx.Response(200, json={"job": {"id": "j", "status": "rejected", "errors": [{"message": "unsupported audio"}]}})

    with pytest.raises(AsrError, match="rejected"):
        await SpeechmaticsASR(api_key="k", client=client_for(handler), poll_interval_s=0).transcribe(mono_audio())


# ------------------------------------------------------------------ Granite (vLLM, OpenAI-compatible)


async def test_granite_chunks_offsets_and_confidence_fallbacks():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/audio/transcriptions"
        body = req.read()
        calls.append(body)
        assert b"verbose_json" in body and b"timestamp_granularities[]" in body and b"Vocabulary: Thabo Mokoena" in body
        wav = body.split(b'filename="chunk.wav"')[1].split(b"\r\n\r\n", 1)[1]
        seconds = sf.info(io.BytesIO(wav)).duration
        assert seconds <= 30.01
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "text": "we save five bar a month",
                    "words": [
                        {"word": "we", "start": 0.2, "end": 0.4, "probability": 0.97},
                        {"word": "save", "start": 0.4, "end": 0.7, "probability": 0.95},
                        {"word": "five", "start": 0.7, "end": 1.0, "probability": 0.8},
                        {"word": "bar", "start": 1.0, "end": 1.3},
                        {"word": "a", "start": 1.3, "end": 1.4},
                        {"word": "month", "start": 1.4, "end": 1.8},
                    ],
                    "segments": [{"start": 0.0, "end": 2.0, "text": "we save five bar a month", "avg_logprob": -0.2}],
                },
            )
        return httpx.Response(
            200, json={"text": "thank you", "segments": [{"start": 1.0, "end": 2.0, "text": "thank you", "avg_logprob": -0.1}]}
        )

    audio = AsrAudio(
        samples=speech_like(50, seed=3)[None, :], sample_rate=16_000, layout="mono", speech_regions=((0.0, 20.0), (25.0, 50.0))
    )
    asr = GraniteSpeechASR(base_url="http://vllm:8000", client=client_for(handler), concurrency=1)
    res = await asr.transcribe(audio, VOCAB)
    assert len(calls) == len(audio.chunks(30.0)) == 2
    assert res.alternatives_available is False and res.provider == "granite"
    bar = next(w for w in res.words if w.text == "bar")
    assert bar.conf == pytest.approx(np.exp(-0.2), abs=1e-3)  # no word probability: the segment's
    thank = next(w for w in res.words if w.text == "thank")
    assert thank.t_start == pytest.approx(25.0 + 1.0, abs=1e-3)  # second chunk starts at 25 s; spread over its segment
    with pytest.raises(AsrError):
        await GraniteSpeechASR(base_url="").transcribe(audio)


# ------------------------------------------------------------------ Parakeet (Riva-style gateway, TurboBias)


async def test_parakeet_turbobias_phrases_and_riva_results():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.read())
        return httpx.Response(
            200,
            json={
                "model_version": "parakeet-tdt-0.6b-v3+turbobias-2.0",
                "results": [
                    {
                        "channel_tag": 1,
                        "is_final": True,
                        "audio_processed": 3.1,
                        "alternatives": [
                            {
                                "transcript": "about forty two thousand",
                                "confidence": 0.9,
                                "words": [
                                    {"word": "about", "start_time": 200, "end_time": 480, "confidence": 0.97},
                                    {"word": "forty", "start_time": 480, "end_time": 820, "confidence": 0.66},
                                    {"word": "two", "start_time": 820, "end_time": 1000, "confidence": 0.9},
                                    {"word": "thousand", "start_time": 1000, "end_time": 1500, "confidence": 0.95},
                                ],
                            },
                            {
                                "transcript": "about fourteen two thousand",
                                "confidence": 0.7,
                                "words": [
                                    {"word": "about", "start_time": 200, "end_time": 480, "confidence": 0.97},
                                    {"word": "fourteen", "start_time": 480, "end_time": 820, "confidence": 0.5},
                                    {"word": "two", "start_time": 820, "end_time": 1000, "confidence": 0.9},
                                    {"word": "thousand", "start_time": 1000, "end_time": 1500, "confidence": 0.95},
                                ],
                            },
                        ],
                    },
                    {
                        "channel_tag": 2,
                        "is_final": True,
                        "alternatives": [
                            {
                                "transcript": "okay",
                                "confidence": 0.99,
                                "words": [{"word": "okay", "start_time": 1700, "end_time": 2000, "confidence": 0.99}],
                            }
                        ],
                    },
                ],
            },
        )

    res = await ParakeetASR(base_url="http://parakeet:9000/", client=client_for(handler)).transcribe(stereo_audio(), VOCAB)
    cfg = seen["body"]["config"]
    assert cfg["max_alternatives"] == 4 and cfg["enable_word_time_offsets"] and cfg["audio_channel_count"] == 2
    assert cfg["enable_separate_recognition_per_channel"] is True
    assert {"phrase": "one point two million", "boost": 1.5} in cfg["turbobias"]["phrases"]
    wav = base64.b64decode(seen["body"]["audio"])
    assert sf.info(io.BytesIO(wav)).channels == 2
    forty = next(w for w in res.words if w.text == "forty")
    assert forty.t_start == pytest.approx(0.48) and forty.channel == 0 and forty.alts[0][0] == "fourteen"
    assert next(w for w in res.words if w.text == "okay").channel == 1
    assert res.model_version == "parakeet-tdt-0.6b-v3+turbobias-2.0" and res.alternatives_available


# ------------------------------------------------------------------ factory and n-best folding


def test_asr_from_pins_builds_the_pinned_adapter():
    s = Settings(
        assemblyai_api_key="a", deepgram_api_key="d", speechmatics_api_key="s", vllm_base_url="http://vllm", parakeet_base_url="http://pk"
    )
    assert isinstance(asr_from_pins(VersionPins(asr_provider="assemblyai", asr_model_version="universal-3"), s), AssemblyAIASR)
    dg = asr_from_pins(VersionPins(asr_provider="deepgram", asr_model_version="nova-3"), s)
    assert isinstance(dg, DeepgramASR) and dg.api_key == "d" and dg.model == "nova-3"
    sm = asr_from_pins(VersionPins(asr_provider="speechmatics", asr_model_version="speechmatics-enhanced"), s)
    assert isinstance(sm, SpeechmaticsASR) and sm.operating_point == "enhanced"
    gr = asr_from_pins(VersionPins(asr_provider="granite", asr_model_version="ibm-granite/granite-speech-4.0"), s)
    assert isinstance(gr, GraniteSpeechASR) and gr.base_url == "http://vllm"
    pk = asr_from_pins(VersionPins(asr_provider="parakeet", asr_model_version="nvidia/parakeet-tdt-0.6b-v3"), s)
    assert isinstance(pk, ParakeetASR) and pk.base_url == "http://pk"
    with pytest.raises(AsrError):
        asr_from_pins(VersionPins(asr_provider="whisper"), s)


def test_fold_nbest_joins_split_words_and_keeps_best_score():
    best = [AsrWord(text="two-fifty", t_start=1.0, t_end=1.6, conf=0.6)]
    alt1 = [AsrWord(text="two", t_start=1.0, t_end=1.25, conf=0.7), AsrWord(text="fifteen", t_start=1.25, t_end=1.6, conf=0.5)]
    alt2 = [AsrWord(text="two", t_start=1.0, t_end=1.25, conf=0.9), AsrWord(text="fifteen", t_start=1.25, t_end=1.6, conf=0.7)]
    out = fold_nbest(best, [(alt1, 0.5), (alt2, 0.5)], best_conf=1.0)
    assert out[0].alts == [("two fifteen", pytest.approx(0.4))]
    assert best[0].alts == []  # inputs are not mutated
