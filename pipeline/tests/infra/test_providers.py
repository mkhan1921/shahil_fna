"""Provider construction from settings and pins, and the stage entry-point table (spec §1.3, §5)."""

from __future__ import annotations

import pytest

from advisor_intel.activities import providers as P
from advisor_intel.activities.keys import discourse_key, ingest_key, run_key
from advisor_intel.activities.stages import StageFunctions, StageUnavailable, invoke
from advisor_intel.config import Settings
from advisor_intel.models.runs import VersionPins


class FakeASR:
    def __init__(self, **kw):
        self.kw = kw


def fake_factory(settings, pins):
    return FakeASR(provider=pins.asr_provider)


def test_asr_provider_gets_its_key_and_pinned_model(monkeypatch):
    monkeypatch.setitem(P.ASR_PROVIDERS, "assemblyai", P.ProviderSpec("tests.infra.test_providers:FakeASR", "assemblyai_api_key"))
    asr = P.build_asr(Settings(assemblyai_api_key="aai-key"), VersionPins(asr_provider="assemblyai", asr_model_version="universal-3"))
    assert isinstance(asr, FakeASR) and asr.kw == {"api_key": "aai-key", "model": "universal-3"}


def test_provider_errors_are_not_retried(monkeypatch):
    with pytest.raises(P.ProviderConfigError, match="needs settings.assemblyai_api_key") as ei:
        P.build_asr(Settings(), VersionPins(asr_provider="assemblyai"))
    assert ei.value.non_retryable
    with pytest.raises(P.ProviderConfigError, match="unknown ASR provider"):
        P.build_asr(Settings(), VersionPins(asr_provider="nope"))
    assert P.build_second_pass(Settings(), VersionPins()) is None  # no self-hosted endpoint configured
    monkeypatch.setenv("ADVISOR_INTEL_ASR_FACTORY", "tests.infra.test_providers:fake_factory")
    assert P.build_asr(Settings(), VersionPins()).kw == {"provider": "fake"}


async def test_stage_functions_resolve_lazily_and_overrides_win():
    stages = StageFunctions({"run_forecast": lambda profile, **kw: ("forecast", kw)})
    assert await invoke(stages.get("run_forecast"), "p", mode="final") == ("forecast", {"mode": "final"})
    assert StageFunctions(table={"x": "json:dumps"}).get("x")({"a": 1}) == '{"a": 1}'
    with pytest.raises(StageUnavailable) as ei:
        StageFunctions(table={"x": "advisor_intel.no_such_module:fn"}).get("x")
    assert ei.value.non_retryable and not StageFunctions().available("no_such_entry")


def test_cache_keys_follow_the_stage_pins():
    base = VersionPins()
    assert ingest_key(base) == ingest_key(base.model_copy(update={"cma_version": "2027Q1", "frontier_model": "x"}))
    assert ingest_key(base) != ingest_key(base.model_copy(update={"asr_model_version": "fake-2"}))
    assert discourse_key(base) == discourse_key(base.model_copy(update={"prompt_versions": {"extract_slot_group": "v9"}}))
    assert discourse_key(base) != discourse_key(base.model_copy(update={"prompt_versions": {"rewrite": "v2"}}))
    assert run_key("agent", "r", base, execution="e1") != run_key("agent", "r", base, execution="e2")
