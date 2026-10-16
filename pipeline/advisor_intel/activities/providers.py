"""Stage 1 providers for the ingest activity: ASR, diarization and the second pass (spec §1.3, §5).

Construction lives in ``advisor_intel.ingest.providers`` (``build_asr`` / ``build_diarizer`` /
``build_second_pass``, each ``(pins, settings)``), shared with the in-process runner. This module adapts
it for Temporal: a provider that is unknown, not installed or missing its credentials raises the
ingest package's ``ProviderConfigError``, which becomes a non-retryable ``ProviderConfigError`` here,
since another attempt cannot fix configuration. A deployment can replace a whole factory with
``ADVISOR_INTEL_ASR_FACTORY`` / ``ADVISOR_INTEL_DIARIZER_FACTORY`` / ``ADVISOR_INTEL_SECOND_PASS_FACTORY``
(``module:callable`` taking ``(pins, settings)``).
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from temporalio.exceptions import ApplicationError

from ..config import Settings, get_settings
from ..models.runs import VersionPins

FACTORY_ENV = {
    "build_asr": "ADVISOR_INTEL_ASR_FACTORY",
    "build_diarizer": "ADVISOR_INTEL_DIARIZER_FACTORY",
    "build_second_pass": "ADVISOR_INTEL_SECOND_PASS_FACTORY",
}


class ProviderConfigError(ApplicationError):
    """A provider is unknown, not installed, or missing its credentials: not retried."""

    def __init__(self, message: str) -> None:
        super().__init__(message, type="ProviderConfigError", non_retryable=True)


def _load(target: str) -> Callable[..., Any]:
    module, _, attr = target.partition(":")
    try:
        return getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as e:
        raise ProviderConfigError(f"cannot load {target}: {e}") from e


def _build(name: str, pins: VersionPins, settings: Settings | None) -> Any:
    """``ingest.providers.<name>(pins, settings)``, or the factory named in its environment variable."""
    s = settings or get_settings()
    override = os.environ.get(FACTORY_ENV[name])
    fn = _load(override) if override else _load(f"advisor_intel.ingest.providers:{name}")
    try:
        from ..ingest.types import AsrError
    except ImportError as e:  # the ingest package is not installed in this worker
        raise ProviderConfigError(f"stage 1 is not installed: {e}") from e
    try:
        return fn(pins, s)
    except AsrError as e:  # ingest.providers.ProviderConfigError: an unknown provider or a missing key
        raise ProviderConfigError(f"{name.removeprefix('build_').replace('_', ' ')}: {e}") from e


def build_asr(pins: VersionPins, settings: Settings | None = None) -> Any:
    """The ASR adapter the run is pinned to (``pins.asr_provider`` at ``pins.asr_model_version``)."""
    return _build("build_asr", pins, settings)


def build_diarizer(pins: VersionPins, settings: Settings | None = None) -> Any:
    """pyannoteAI at ``pins.diarization_model``, or the stereo channel split without a pyannote key."""
    return _build("build_diarizer", pins, settings)


def build_second_pass(pins: VersionPins, settings: Settings | None = None) -> Any | None:
    """The constrained second pass over risky spans (§5.6), or None when no self-hosted endpoint is configured."""
    return _build("build_second_pass", pins, settings)


@dataclass(frozen=True)
class Providers:
    """The provider factories the ingest activity uses, each ``(pins, settings)``; replace them to ingest against fakes."""

    asr: Callable[[VersionPins, Settings], Any] = build_asr
    diarizer: Callable[[VersionPins, Settings], Any] = build_diarizer
    second_pass: Callable[[VersionPins, Settings], Any | None] = build_second_pass
