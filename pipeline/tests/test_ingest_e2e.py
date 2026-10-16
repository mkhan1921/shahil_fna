"""End to end: a ~10-minute scripted SA advice call through Stage 1, and ``ingest_call`` on synthetic audio."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf

from advisor_intel.ingest import (
    CrmNames,
    DeepgramASR,
    FakeDiarizer,
    NoiseConfig,
    ScriptedASR,
    ScriptedCall,
    ingest_call,
    meta_for,
    transcript_from_script,
    transcript_from_script_async,
)
from advisor_intel.ingest.metrics import der, label_role_accuracy, numeric_wer, speaker_role_accuracy
from advisor_intel.ingest.types import SpeakerTurn
from advisor_intel.models.fna import FnaDocument
from advisor_intel.models.runs import VersionPins
from advisor_intel.models.transcript import NumericSpan, Transcript

from .audio_synth import SR, hold_music, noise, speech_like

ANCHOR = date(2026, 10, 16)
SCRIPT_FILE = Path(__file__).parent / "fixtures" / "scripted_call_review.txt"
ROLES = {"advisor": "advisor", "thabo": "client_primary", "lerato": "client_secondary"}
CRM = CrmNames(advisor=["Sipho Dlamini"], client_primary=["Thabo Mokoena"], client_secondary=["Lerato Mokoena"])
NOISE = NoiseConfig(low_conf_number_rate=0.1, mishear_rate=0.05, crosstalk_rate=0.1, long_pause_rate=0.05)


def script() -> ScriptedCall:
    return ScriptedCall.parse(SCRIPT_FILE.read_text(), roles=ROLES)


def span(t: Transcript, surface: str, kind: str = "amount", nth: int = 0) -> NumericSpan:
    hits = [s for s in t.numeric_spans if s.kind == kind and s.surface.lower().strip(",.?!") == surface]
    assert len(hits) > nth, (surface, [s.surface for s in t.numeric_spans if s.kind == kind])
    return hits[nth]


@pytest.fixture(scope="module")
def call(sample_fna_json_module):
    asr = ScriptedASR(script(), seed=11, noise=NOISE)
    t0 = time.perf_counter()
    t = transcript_from_script(
        script(),
        ANCHOR,
        asr=asr,
        crm_names=CRM,
        prior_fna=FnaDocument.model_validate(sample_fna_json_module),
        second_pass="scripted",
        call_id="call-review-1",
    )
    return t, asr.render(), time.perf_counter() - t0


@pytest.fixture(scope="module")
def sample_fna_json_module():
    return json.loads((Path(__file__).parent / "fixtures" / "sample_fna.json").read_text())


def test_the_call_is_ten_minutes_with_three_speakers_and_the_right_roles(call):
    t, render, seconds = call
    assert 9 * 60 <= t.duration <= 11 * 60 and seconds < 30
    assert t.call_id == "call-review-1" and t.anchor_date == ANCHOR and t.asr_provider == "scripted" and t.asr_alternatives
    labels = {s.speaker_label for s in t.segments}
    assert labels == {"spk_0", "spk_1", "spk_2"}
    roles = {s.speaker_label: s.speaker_role for s in t.segments}
    assert label_role_accuracy({"spk_0": "advisor", "spk_1": "client_primary", "spk_2": "client_secondary"}, roles) == 1.0
    gold = [(turn.start, turn.end, ROLES[turn.speaker]) for turn in render.turns]
    assert speaker_role_accuracy(gold, t) >= 0.98
    assert [s.segment_id for s in t.segments] == [f"s{k:04d}" for k in range(1, len(t.segments) + 1)]
    assert all(s.t_end - s.t_start <= 30.0 for s in t.segments)


def test_quality_report_passes(call):
    t, _, _ = call
    q = t.quality
    assert q is not None and not q.failed and q.n_speakers == 3
    assert q.mean_word_conf >= 0.8 and not q.cap_p_correct and not q.role_review_needed and q.role_conf_min >= 0.8
    assert q.scale_ambiguous_spans == sum(1 for s in t.numeric_spans if "scale_ambiguous" in s.flags) > 5


@pytest.mark.parametrize(
    "surface,value,period,currency",
    [
        ("forty two thousand a month", 42_000, "monthly", None),
        ("eight bar a month", 8_000, "monthly", "ZAR"),
        ("five bar a month", 5_000, "monthly", "ZAR"),
        ("ninety eight thousand a year", 98_000, "annual", None),
        ("one point eight five million", 1_850_000, "none", None),
        ("one point three two million", 1_320_000, "none", None),
        ("a hundred and eighty four thousand", 184_000, "none", None),
        ("half a bar a month", 500, "monthly", "ZAR"),
        ("six hundred k", 600_000, "none", None),
        ("sestig duisend rand per maand", 60_000, "monthly", "ZAR"),
        ("a hundred thousand", 100_000, "none", None),
        ("fifty five thousand", 55_000, "none", None),
    ],
)
def test_amounts_read_with_scale_currency_and_period(call, surface, value, period, currency):
    t, _, _ = call
    top = span(t, surface).readings[0]
    assert top.value == value and top.period == period and top.currency == currency


def test_slang_shorthand_and_rates_keep_every_reading(call):
    t, _, _ = call
    assert {r.value for r in span(t, "two point eight").readings} == {2_800_000.0, 2_800.0, 2.8}
    assert span(t, "two point eight").readings[0].value == 2_800_000.0
    assert {250.0, 250_000.0, 2_500_000.0} <= {r.value for r in span(t, "two fifty").readings}
    assert {184.0, 184_000.0, 1_840_000.0} == {r.value for r in span(t, "one eight four").readings}
    fifteen_hundred = span(t, "fifteen hundred a month")
    assert fifteen_hundred.readings[0].value == 1_500 and 1_500_000.0 in {r.value for r in fifteen_hundred.readings}
    for surface, rate in (
        ("ten point seven five percent", 10.75),
        ("twelve and a quarter percent", 12.25),
        ("twenty three and a half percent", 23.5),
    ):
        top = span(t, surface).readings[0]
        assert top.is_rate and top.value == rate


def test_self_correction_keeps_both_values_and_the_second_pass_flags_the_disagreement(call):
    t, _, _ = call
    first = span(t, "two fifty")
    corrected = span(t, "one point eight five million")
    assert first.segment_id == corrected.segment_id and first.word_end <= corrected.word_start  # both stay, in order
    assert first.second_pass_method == "scripted" and first.second_pass_reading.value == 250_000.0
    assert first.second_pass_agreement is (first.readings[0].value == 250_000.0)
    assert first.readings and corrected.second_pass_method is None  # a clear explicit amount is not re-checked


def test_misheard_numbers_are_recovered_from_the_alternatives(call):
    t, render, _ = call
    assert ("five hundred and sixty", "five hundred and sixteen") in render.misheard
    s = span(t, "five hundred and sixteen thousand")
    assert {516_000.0, 560_000.0} <= {r.value for r in s.readings} and s.mean_conf < 0.75
    assert s.second_pass_reading.value == 560_000.0 and s.second_pass_agreement is False


def test_ages_and_products(call):
    t, _, _ = call
    ages = {s.surface.lower().strip(",.?!"): s.readings[0] for s in t.numeric_spans if s.kind == "age"}
    assert ages["grade nine"].value == 15 and ages["grade nine"].lexicon_key == "grade"
    assert ages["in matric"].value == 18 and ages["late sixties"].value == 68.5
    assert ages["turning forty"].lexicon_key == "turning" and ages["thirty seven"].value == 37 and ages["sixty five"].value == 65
    products = [(s.readings[0].value, s.readings[0].provider) for s in t.numeric_spans if s.kind == "product"]
    for expected in (
        ("retirement-annuity", "Allan Gray"),
        ("tfsa", "EasyEquities"),
        ("life", "Discovery"),
        ("funeral", "Old Mutual"),
        ("two-pot", None),
        ("savings-pot", None),
        ("money-market", None),
        ("offshore-allowance", None),
        ("gap-cover", None),
    ):
        assert expected in products, expected
    pres = span(t, "preservation fund", kind="product")
    assert {r.value for r in pres.readings} == {"preservation-pension", "preservation-provident"}


def test_spans_are_numbered_normalised_and_mark_their_words(call):
    t, _, _ = call
    assert [s.span_id for s in t.numeric_spans] == [f"n{k:04d}" for k in range(1, len(t.numeric_spans) + 1)]
    for s in t.numeric_spans:
        if s.readings:
            assert sum(r.weight for r in s.readings) == pytest.approx(1.0)
        seg = t.segment(s.segment_id)
        assert 0 <= s.word_start < s.word_end <= len(seg.words)
    s = span(t, "forty two thousand a month")
    words = t.segment(s.segment_id).words[s.word_start : s.word_end]
    assert words[0].numeric_candidates == [42_000.0] and words[-1].numeric_candidates == []  # "month" carries none


def test_stage_metrics_on_the_scripted_call(call):
    t, render, _ = call
    w = numeric_wer(render.reference_text(), " ".join(s.text for s in t.segments))
    assert 0 < w.wer <= 0.08 and w.targets > 300  # the noise misheard a few numbers; within the §15.3 target
    hyp = [SpeakerTurn(speaker=s.speaker_label, start=s.t_start, end=s.t_end) for s in t.segments]
    ref = [SpeakerTurn(speaker=turn.speaker, start=turn.start, end=turn.end) for turn in render.turns]
    assert der(ref, hyp, collar=0.25).der < 0.05


def test_transcript_round_trips_and_is_deterministic(call):
    t, _, _ = call
    assert Transcript.model_validate_json(t.model_dump_json()) == t
    again = transcript_from_script(
        script(),
        ANCHOR,
        seed=11,
        noise=NOISE,
        crm_names=CRM,
        second_pass="scripted",
        call_id="call-review-1",
        prior_fna=FnaDocument.model_validate(json.loads((Path(__file__).parent / "fixtures" / "sample_fna.json").read_text())),
    )
    assert again.model_dump_json() == t.model_dump_json()


async def test_provider_style_itn_is_kept_as_a_candidate():
    t = await transcript_from_script_async(script(), ANCHOR, seed=11, noise=NoiseConfig(itn=True), crm_names=CRM)
    s = next(s for s in t.numeric_spans if s.provider_itn and s.readings and s.readings[0].value == 42_000.0)
    assert s.provider_itn.startswith("42 000") and s.accepted
    w = t.segment(s.segment_id).words[s.word_start]
    assert w.raw_spoken == "forty two thousand" and w.text == "42 000"
    two_fifty = next(s for s in t.numeric_spans if s.provider_itn and s.provider_itn.startswith("250"))
    assert {250.0, 250_000.0, 2_500_000.0} <= {r.value for r in two_fifty.readings}  # ITN wrote 250; the scale stays open


# ------------------------------------------------------------------ ingest_call on audio


def _render_audio(render, channels: int, music: tuple[float, float] | None = None, music_channel: int = 0) -> np.ndarray:
    n = int((render.duration + 1.0) * SR)
    out = np.stack([noise(n / SR, seed=c, level_db=-62.0) for c in range(channels)])
    for k, turn in enumerate(render.turns):
        c = turn.channel or 0
        a, b = int(turn.start * SR), int(turn.end * SR)
        out[c, a:b] += speech_like((b - a) / SR, seed=100 + k)[: b - a]
    if music:
        a, b = int(music[0] * SR), int(music[1] * SR)
        out[music_channel, a:b] += hold_music((b - a) / SR, seed=7)[: b - a]
    return out


STEREO_SCRIPT = """
advisor@0: Good morning Thabo, good morning Lerato. What do you take home each month?
thabo@1: I take home about forty two thousand a month.
lerato@1: And I earn about twenty eight thousand rand a month.
advisor@0: Thanks Lerato. Please hold for a moment while I pull up your file.
[pause 12]
advisor@0: Thanks for holding. And the RA with Allan Gray?
thabo@1: It's about three hundred and fifteen thousand.
"""


async def test_ingest_call_on_stereo_audio_with_two_clients_on_one_channel(tmp_path):
    call_script = ScriptedCall.parse(STEREO_SCRIPT, roles=ROLES)
    asr = ScriptedASR(call_script, seed=2)
    render = asr.render()
    hold = (render.turns[3].end + 0.5, render.turns[4].start - 0.5)
    audio = _render_audio(render, 2, music=hold)
    path = tmp_path / "call.wav"
    sf.write(path, audio.T, SR)
    client_turns = [
        SpeakerTurn(speaker=turn.speaker, start=turn.start, end=turn.end, channel=1, confidence=0.93)
        for turn in render.turns
        if turn.channel == 1
    ]
    diarizer = FakeDiarizer(client_turns, confidence=0.93)
    meta = meta_for("call-stereo", ANCHOR, client_ids=["c1", "c2"], advisor_channel=0)
    t = await ingest_call(path, meta, asr=asr, diarizer=diarizer, crm_names=CRM, role_agent_pins=VersionPins())
    assert t.channel_layout == "stereo_split"
    assert diarizer.calls and diarizer.calls[0]["min_speakers"] == 1 and diarizer.calls[0]["max_speakers"] == 3
    assert diarizer.calls[0]["seconds"] < render.duration - 8.0  # the hold music and silences were cut before diarization
    roles = {s.speaker_label: s.speaker_role for s in t.segments}
    assert roles == {"ch0": "advisor", "ch1_spk0": "client_primary", "ch1_spk1": "client_secondary"}
    assert all(s.diarization_conf == (1.0 if s.speaker_label == "ch0" else 0.93) for s in t.segments)
    assert {s.readings[0].value for s in t.numeric_spans if s.kind == "amount"} >= {42_000.0, 28_000.0, 315_000.0}
    assert t.quality.n_speakers == 3 and not t.quality.failed


async def test_ingest_call_on_mono_audio_maps_provider_times_back_to_the_call(tmp_path):
    x = np.concatenate([speech_like(4, seed=1), hold_music(10, seed=2), speech_like(4, seed=3)]) + noise(18, seed=4)
    path = tmp_path / "mono.wav"
    sf.write(path, x, SR)
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        info = sf.info(__import__("io").BytesIO(req.read()))
        seen["seconds"] = info.duration
        mid = info.duration / 2

        def words(spec, t0):
            out, t = [], t0
            for w in spec.split():
                out.append({"word": w, "start": round(t, 2), "end": round(t + 0.3, 2), "confidence": 0.93, "punctuated_word": w})
                t += 0.35
            return out

        ws = words("the bond is about two fifty", 0.4) + words("fifteen hundred a month for the car", mid + 0.6)
        return httpx.Response(
            200,
            json={
                "metadata": {"model_info": {"m": {"name": "general-nova-3", "version": "2025-04"}}},
                "results": {"channels": [{"alternatives": [{"transcript": "", "confidence": 0.93, "words": ws}]}]},
            },
        )

    asr = DeepgramASR(api_key="k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    diarizer = FakeDiarizer(
        [SpeakerTurn(speaker="A", start=0.0, end=4.2), SpeakerTurn(speaker="B", start=4.3, end=9.0)], time_base="input", confidence=0.88
    )
    t = await ingest_call(path, meta_for("call-mono", ANCHOR), asr=asr, diarizer=diarizer, role_agent_pins=VersionPins())
    assert t.channel_layout == "mono" and 7.5 < seen["seconds"] < 10.0  # 18 s of audio, 10 s of music cut out
    assert diarizer.calls[0]["min_speakers"] == 2 and diarizer.calls[0]["max_speakers"] == 4
    first, second = t.segments[0], t.segments[-1]
    assert first.speaker_label != second.speaker_label and second.diarization_conf == pytest.approx(0.88)
    assert first.t_start < 4.0 and 13.5 < second.t_start < 16.0  # back in call time, after the hold music
    assert t.asr_model_version == "general-nova-3:2025-04" and t.asr_provider == "deepgram"
    amounts = {s.surface: {r.value for r in s.readings} for s in t.numeric_spans if s.kind == "amount"}
    assert {250.0, 250_000.0, 2_500_000.0} <= amounts["two fifty"] and 1_500.0 in amounts["fifteen hundred a month"]


@pytest.mark.parametrize("kind", ["silence", "hold_music"])
async def test_ingest_call_without_speech_skips_the_provider_and_routes_to_manual(tmp_path, kind):
    body = hold_music(8, seed=5) if kind == "hold_music" else np.zeros(8 * SR, np.float32)
    x = np.concatenate([np.zeros(SR, np.float32), body]) + noise(9, seed=6)
    path = tmp_path / f"{kind}.wav"
    sf.write(path, x, SR)
    requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return httpx.Response(500)

    asr = DeepgramASR(api_key="k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    diarizer = FakeDiarizer([])
    t = await ingest_call(path, meta_for(f"call-{kind}", ANCHOR), asr=asr, diarizer=diarizer, role_agent_pins=VersionPins())
    assert not requests and not diarizer.calls  # nothing was sent to the provider or the diarizer
    assert t.segments == [] and t.numeric_spans == [] and t.call_id == f"call-{kind}" and t.asr_provider == "deepgram"
    assert t.quality.failed and t.quality.n_speakers == 0 and t.quality.cap_p_correct and t.quality.banners
