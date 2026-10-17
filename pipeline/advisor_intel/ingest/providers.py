"""ASR, diarization and second-pass providers for a run, from its version pins and settings (spec §1.3, §5).

The one place that chooses and constructs the stage 1 adapters, shared by the in-process runner
(``advisor_intel.pipeline``) and the Temporal ingest activity. ``pins.asr_provider`` /
``asr_model_version`` pick the ASR adapter (``asr_from_pins``); ``pins.diarization_model`` the
pyannoteAI model used when clients share a channel or the call is mono; the second pass is the
Parakeet constrained re-decode when a Parakeet gateway is configured, the audio-model check when
only a vLLM endpoint is, and off otherwise (``ADVISOR_INTEL_SECOND_PASS`` = ``constrained-redecode``,
``audio-model`` or ``off`` overrides the choice).
"""

from __future__ import annotations

import os

from ..config import Settings, get_settings
from ..models.runs import VersionPins
from .asr import ASRProvider, ParakeetASR, asr_from_pins
from .diarization import ChannelDiarizer, Diarizer, PyannoteAIDiarizer
from .second_pass import AudioModelCheck, GrammarConstrainedRedecode, SecondPassProvider
from .types import AsrError

SECOND_PASS_ENV = "ADVISOR_INTEL_SECOND_PASS"


class ProviderConfigError(AsrError):
    """A provider is unknown or missing its credentials (not worth retrying)."""


ASR_CREDENTIAL = {
    "assemblyai": "assemblyai_api_key",
    "deepgram": "deepgram_api_key",
    "speechmatics": "speechmatics_api_key",
    "granite": "vllm_base_url",
    "parakeet": "parakeet_base_url",
}


def build_asr(pins: VersionPins, settings: Settings | None = None) -> ASRProvider:
    """The pinned ASR adapter; a missing key or endpoint fails here, before any audio is sent."""
    s = settings or get_settings()
    needed = ASR_CREDENTIAL.get(pins.asr_provider)
    if needed and not getattr(s, needed, None):
        raise ProviderConfigError(f"ASR provider {pins.asr_provider!r} needs settings.{needed} (environment {needed.upper()})")
    try:
        return asr_from_pins(pins, s)
    except AsrError as e:
        raise ProviderConfigError(str(e)) from e


class UnconfiguredDiarizer(ChannelDiarizer):
    """Stands in when no pyannoteAI key is configured: stereo calls with one speaker per channel never need it, and a
    mono call or a shared client channel fails with the reason instead of quietly becoming one speaker."""

    name = "unconfigured"
    model_version = "none"

    async def diarize(self, samples, sample_rate, *, min_speakers: int = 1, max_speakers: int = 1, key: str = ""):
        raise ProviderConfigError(
            "diarization needed (mono call or two clients on one channel) but settings.pyannote_api_key is not set (environment PYANNOTEAI_API_KEY)"
        )


def build_diarizer(pins: VersionPins, settings: Settings | None = None) -> Diarizer:
    """pyannoteAI at the pinned model; without a key, a diarizer that only serves stereo calls with one speaker per channel."""
    s = settings or get_settings()
    if not s.pyannote_api_key:
        return UnconfiguredDiarizer()
    return PyannoteAIDiarizer(api_key=s.pyannote_api_key, model=pins.diarization_model)


def build_second_pass(pins: VersionPins, settings: Settings | None = None) -> SecondPassProvider | None:
    s = settings or get_settings()
    method = os.environ.get(SECOND_PASS_ENV, "")
    if method == "off":
        return None
    if method in ("", "constrained-redecode") and s.parakeet_base_url:
        return GrammarConstrainedRedecode(ParakeetASR(base_url=s.parakeet_base_url))
    if method in ("", "audio-model") and s.vllm_base_url:
        return AudioModelCheck(base_url=s.vllm_base_url)
    if method:
        raise ProviderConfigError(
            f"second pass {method!r} needs {'PARAKEET_BASE_URL' if method == 'constrained-redecode' else 'VLLM_BASE_URL'}"
        )
    return None
