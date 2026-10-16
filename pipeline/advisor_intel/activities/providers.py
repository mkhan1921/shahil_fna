"""ASR, diarization and second-pass providers built from settings and version pins (spec §1.3, §5).

The adapters live in ``advisor_intel.ingest``; this module only chooses and constructs them, so the
ingest code never reads settings. A provider is named by the run's pins (``asr_provider``,
``diarization_model``), resolved through ``ASR_PROVIDERS`` / ``DIARIZERS`` to a class, and built with
its API key from ``Settings`` plus the pinned model version. A whole factory can be replaced with
``ADVISOR_INTEL_ASR_FACTORY`` / ``ADVISOR_INTEL_DIARIZER_FACTORY`` / ``ADVISOR_INTEL_SECOND_PASS_FACTORY``
(``module:callable`` taking ``(settings, pins)``).
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..models.runs import VersionPins


class ProviderConfigError(RuntimeError):
    """A provider is unknown, not installed, or missing its credentials."""


@dataclass(frozen=True)
class ProviderSpec:
    target: str  # "module:Class"
    key_setting: str | None = None  # the Settings field holding the API key
    kwargs: dict[str, Any] = field(default_factory=dict)
    model_kwarg: str | None = "model"  # the constructor argument that takes the pinned model version


ASR_PROVIDERS: dict[str, ProviderSpec] = {
    "assemblyai": ProviderSpec("advisor_intel.ingest.asr:AssemblyAIProvider", "assemblyai_api_key"),
    "deepgram": ProviderSpec("advisor_intel.ingest.asr:DeepgramProvider", "deepgram_api_key"),
    "speechmatics": ProviderSpec("advisor_intel.ingest.asr:SpeechmaticsProvider", "speechmatics_api_key"),
    "granite": ProviderSpec("advisor_intel.ingest.asr:GraniteSpeechProvider", "vllm_base_url", model_kwarg="model"),
    "parakeet": ProviderSpec("advisor_intel.ingest.asr:ParakeetProvider", "vllm_base_url", model_kwarg="model"),
    "fake": ProviderSpec("advisor_intel.ingest.asr:FakeASRProvider", None, model_kwarg=None),
}

DIARIZERS: dict[str, ProviderSpec] = {
    "precision-3": ProviderSpec("advisor_intel.ingest.diarization:PyannoteDiarizer", "pyannote_api_key"),
    "fake": ProviderSpec("advisor_intel.ingest.diarization:FakeDiarizer", None, model_kwarg=None),
}

SECOND_PASS: dict[str, ProviderSpec] = {
    "constrained-redecode": ProviderSpec("advisor_intel.ingest.second_pass:ConstrainedRedecode", "vllm_base_url", model_kwarg=None),
    "audio-model": ProviderSpec("advisor_intel.ingest.second_pass:AudioModelCheck", "vllm_base_url", model_kwarg=None),
}


def _load(target: str) -> Any:
    module, _, attr = target.partition(":")
    try:
        return getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as e:
        raise ProviderConfigError(f"cannot load {target}: {e}") from e


def _build(kind: str, name: str, table: dict[str, ProviderSpec], settings: Settings, model: str | None) -> Any:
    spec = table.get(name)
    if spec is None:
        raise ProviderConfigError(f"unknown {kind} {name!r}; known: {sorted(table)}")
    kwargs = dict(spec.kwargs)
    if spec.key_setting:
        key = getattr(settings, spec.key_setting)
        if not key:
            raise ProviderConfigError(f"{kind} {name!r} needs settings.{spec.key_setting} (environment ADVISOR_INTEL_{spec.key_setting.upper()})")
        kwargs["base_url" if spec.key_setting.endswith("_url") else "api_key"] = key
    if spec.model_kwarg and model:
        kwargs[spec.model_kwarg] = model
    return _load(spec.target)(**kwargs)


def _factory_override(env: str, settings: Settings, pins: VersionPins) -> Any | None:
    target = os.environ.get(env)
    return _load(target)(settings, pins) if target else None


def build_asr(settings: Settings, pins: VersionPins) -> Any:
    """The ASR provider for the run: ``pins.asr_provider`` at ``pins.asr_model_version``."""
    override = _factory_override("ADVISOR_INTEL_ASR_FACTORY", settings, pins)
    if override is not None:
        return override
    return _build("ASR provider", pins.asr_provider or settings.asr_provider, ASR_PROVIDERS, settings, pins.asr_model_version)


def build_diarizer(settings: Settings, pins: VersionPins) -> Any:
    override = _factory_override("ADVISOR_INTEL_DIARIZER_FACTORY", settings, pins)
    if override is not None:
        return override
    name = "fake" if pins.asr_provider == "fake" else pins.diarization_model
    return _build("diarizer", name, DIARIZERS, settings, pins.diarization_model)


def build_second_pass(settings: Settings, pins: VersionPins) -> Any | None:
    """The constrained second pass for risky spans (§5.6), or None when no self-hosted endpoint is configured."""
    override = _factory_override("ADVISOR_INTEL_SECOND_PASS_FACTORY", settings, pins)
    if override is not None:
        return override
    method = os.environ.get("ADVISOR_INTEL_SECOND_PASS", "constrained-redecode")
    if not settings.vllm_base_url or method == "off":
        return None
    return _build("second pass", method, SECOND_PASS, settings, None)


@dataclass(frozen=True)
class Providers:
    """The provider factories an ingest activity uses; replace them to run ingest against fakes."""

    asr: Callable[[Settings, VersionPins], Any] = build_asr
    diarizer: Callable[[Settings, VersionPins], Any] = build_diarizer
    second_pass: Callable[[Settings, VersionPins], Any | None] = build_second_pass
