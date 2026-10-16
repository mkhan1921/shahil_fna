"""ASR provider adapters (§5.2 step 3). The vendor is chosen by the M1 bake-off (§17); every adapter has the same contract."""

from __future__ import annotations

import httpx

from ...config import Settings, get_settings
from ...models.runs import VersionPins
from ..types import AsrError
from .assemblyai import AssemblyAIASR
from .base import AsrAudio, ASRProvider, HttpProvider, fold_nbest
from .deepgram import DeepgramASR
from .fake import NoiseConfig, ScriptedASR, ScriptedCall, ScriptRender, ScriptTurn
from .granite import GraniteSpeechASR
from .parakeet import ParakeetASR
from .speechmatics import SpeechmaticsASR

PROVIDERS = ("assemblyai", "deepgram", "speechmatics", "granite", "parakeet")


def asr_from_pins(pins: VersionPins, settings: Settings | None = None, *, client: httpx.AsyncClient | None = None) -> ASRProvider:
    """The adapter a run is pinned to (``VersionPins.asr_provider`` / ``asr_model_version``), configured from settings."""
    s = settings or get_settings()
    name, model = pins.asr_provider, pins.asr_model_version
    if name == "assemblyai":
        return AssemblyAIASR(api_key=s.assemblyai_api_key, model=model, client=client)
    if name == "deepgram":
        return DeepgramASR(api_key=s.deepgram_api_key, model=model, client=client)
    if name == "speechmatics":
        return SpeechmaticsASR(api_key=s.speechmatics_api_key, operating_point=model.removeprefix("speechmatics-"), client=client)
    if name == "granite":
        return GraniteSpeechASR(base_url=s.vllm_base_url or "", model=model, client=client)
    if name == "parakeet":
        return ParakeetASR(base_url=s.parakeet_base_url or "", model=model, client=client)
    raise AsrError(f"unknown ASR provider {name!r} (scripted calls use ScriptedASR directly)")


__all__ = [
    "PROVIDERS",
    "ASRProvider",
    "AsrAudio",
    "AssemblyAIASR",
    "DeepgramASR",
    "GraniteSpeechASR",
    "HttpProvider",
    "NoiseConfig",
    "ParakeetASR",
    "ScriptRender",
    "ScriptTurn",
    "ScriptedASR",
    "ScriptedCall",
    "SpeechmaticsASR",
    "asr_from_pins",
    "fold_nbest",
]
