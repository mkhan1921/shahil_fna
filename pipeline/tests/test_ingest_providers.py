"""Stage 1 providers chosen from the run's pins and settings (ingest/providers.py)."""

from __future__ import annotations

import numpy as np
import pytest

from advisor_intel.config import Settings
from advisor_intel.ingest import (
    AssemblyAIASR,
    AudioModelCheck,
    ChannelDiarizer,
    DeepgramASR,
    GrammarConstrainedRedecode,
    PyannoteAIDiarizer,
)
from advisor_intel.ingest.providers import ProviderConfigError, build_asr, build_diarizer, build_second_pass
from advisor_intel.models.runs import VersionPins


def test_asr_follows_the_pins():
    s = Settings(assemblyai_api_key="k1", deepgram_api_key="k2")
    assert isinstance(build_asr(VersionPins(asr_provider="assemblyai", asr_model_version="universal-3"), s), AssemblyAIASR)
    assert isinstance(build_asr(VersionPins(asr_provider="deepgram", asr_model_version="nova-3"), s), DeepgramASR)
    with pytest.raises(ProviderConfigError):
        build_asr(VersionPins(asr_provider="nope"), s)
    with pytest.raises(ProviderConfigError, match="speechmatics_api_key"):  # a missing key fails before any audio is sent
        build_asr(VersionPins(asr_provider="speechmatics", asr_model_version="enhanced"), s)


def test_diarizer_needs_a_pyannote_key_for_shared_channels():
    assert isinstance(build_diarizer(VersionPins(), Settings()), ChannelDiarizer)
    d = build_diarizer(VersionPins(diarization_model="precision-3"), Settings(pyannote_api_key="k"))
    assert isinstance(d, PyannoteAIDiarizer) and d.model == "precision-3"


def test_second_pass_prefers_the_parakeet_redecode(monkeypatch):
    monkeypatch.delenv("ADVISOR_INTEL_SECOND_PASS", raising=False)
    assert build_second_pass(VersionPins(), Settings()) is None
    assert isinstance(
        build_second_pass(VersionPins(), Settings(parakeet_base_url="http://p", vllm_base_url="http://v")), GrammarConstrainedRedecode
    )
    assert isinstance(build_second_pass(VersionPins(), Settings(vllm_base_url="http://v")), AudioModelCheck)
    monkeypatch.setenv("ADVISOR_INTEL_SECOND_PASS", "off")
    assert build_second_pass(VersionPins(), Settings(parakeet_base_url="http://p")) is None
    monkeypatch.setenv("ADVISOR_INTEL_SECOND_PASS", "audio-model")
    with pytest.raises(ProviderConfigError):
        build_second_pass(VersionPins(), Settings(parakeet_base_url="http://p"))


async def test_without_a_pyannote_key_a_mono_call_fails_with_the_reason_instead_of_one_speaker():
    d = build_diarizer(VersionPins(), Settings())
    assert isinstance(d, ChannelDiarizer)  # stereo calls with one speaker per channel still work: they never call it
    with pytest.raises(ProviderConfigError, match="PYANNOTEAI_API_KEY"):
        await d.diarize(np.zeros(16_000, np.float32), 16_000, min_speakers=2, max_speakers=4)
