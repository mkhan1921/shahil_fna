"""Stage 1 providers through the ingest package's factories, the stage entry-point table and cache keys (spec §1.3, §5)."""

from __future__ import annotations

import pytest

from advisor_intel import pipeline as runner
from advisor_intel.activities import providers as P
from advisor_intel.activities.keys import discourse_key, ingest_key, run_key
from advisor_intel.activities.pipeline import forecast_seed
from advisor_intel.activities.stages import ENTRYPOINTS, StageFunctions, StageUnavailable, invoke
from advisor_intel.config import Settings
from advisor_intel.ingest import AssemblyAIASR, ChannelDiarizer, PyannoteAIDiarizer
from advisor_intel.models.runs import VersionPins


class FakeASR:
    def __init__(self, provider: str, key: str | None) -> None:
        self.provider, self.key = provider, key


def fake_factory(pins, settings):
    return FakeASR(pins.asr_provider, settings.assemblyai_api_key)


def test_providers_come_from_the_ingest_factories_with_pins_then_settings():
    asr = P.build_asr(VersionPins(asr_provider="assemblyai", asr_model_version="universal-3"), Settings(assemblyai_api_key="aai"))
    assert isinstance(asr, AssemblyAIASR) and asr.api_key == "aai" and asr.model_version == "universal-3"
    assert isinstance(P.build_diarizer(VersionPins(), Settings()), ChannelDiarizer)  # no pyannote key: the stereo split
    d = P.build_diarizer(VersionPins(diarization_model="precision-3"), Settings(pyannote_api_key="pk"))
    assert isinstance(d, PyannoteAIDiarizer) and d.model_version == "precision-3"
    assert P.build_second_pass(VersionPins(), Settings()) is None  # no self-hosted endpoint configured
    assert P.Providers().asr is P.build_asr


def test_configuration_errors_are_not_retried(monkeypatch):
    with pytest.raises(P.ProviderConfigError, match="unknown ASR provider 'fake'") as ei:
        P.build_asr(VersionPins(), Settings())  # the offline pins have no audio provider: scripted calls skip ingest_call
    assert ei.value.non_retryable and ei.value.type == "ProviderConfigError"
    monkeypatch.setenv("ADVISOR_INTEL_SECOND_PASS", "constrained-redecode")
    with pytest.raises(P.ProviderConfigError, match="needs PARAKEET_BASE_URL"):
        P.build_second_pass(VersionPins(), Settings())
    monkeypatch.setenv("ADVISOR_INTEL_ASR_FACTORY", "tests.infra.test_providers:no_such_factory")
    with pytest.raises(P.ProviderConfigError, match="cannot load"):
        P.build_asr(VersionPins(), Settings())


def test_a_deployment_can_replace_a_factory(monkeypatch):
    monkeypatch.setenv("ADVISOR_INTEL_ASR_FACTORY", "tests.infra.test_providers:fake_factory")
    asr = P.build_asr(VersionPins(asr_provider="assemblyai"), Settings(assemblyai_api_key="k"))
    assert isinstance(asr, FakeASR) and (asr.provider, asr.key) == ("assemblyai", "k")


async def test_stage_functions_resolve_lazily_and_overrides_win():
    stages = StageFunctions({"run_forecast": lambda profile, **kw: ("forecast", kw)})
    assert await invoke(stages.get("run_forecast"), "p", mode="final") == ("forecast", {"mode": "final"})
    assert StageFunctions(table={"x": "json:dumps"}).get("x")({"a": 1}) == '{"a": 1}'
    with pytest.raises(StageUnavailable) as ei:
        StageFunctions(table={"x": "advisor_intel.no_such_module:fn"}).get("x")
    assert ei.value.non_retryable and not StageFunctions().available("no_such_entry")
    assert StageFunctions().optional("no_such_entry") is None


def test_every_entry_point_whose_package_is_installed_resolves():
    """A renamed stage function fails here, not in a worker; a package not merged into this checkout is skipped."""
    import importlib

    missing = []
    for name, target in ENTRYPOINTS.items():
        module, _, attr = target.partition(":")
        try:
            mod = importlib.import_module(module)
        except ModuleNotFoundError as e:
            assert e.name and module.startswith(e.name), f"{name}: {target} fails to import: {e}"
            continue
        if not hasattr(mod, attr):
            missing.append(f"{name}: {target}")
    assert not missing
    for name in ("ingest_call", "run_discourse", "agent_inputs", "agent_services", "run_agent", "extract_followup", "fna_analyse"):
        assert StageFunctions().available(name), ENTRYPOINTS[name]
    from advisor_intel.extract.followup import extract_followup_ops
    from advisor_intel.harness.deps import AgentInputs

    assert StageFunctions().get("agent_inputs") is AgentInputs and StageFunctions().get("extract_followup") is extract_followup_ops


def test_cache_keys_follow_the_stage_pins_and_seeds_match_the_in_process_runner():
    base = VersionPins()
    assert ingest_key(base) == ingest_key(base.model_copy(update={"cma_version": "2027Q1", "frontier_model": "x"}))
    assert ingest_key(base) != ingest_key(base.model_copy(update={"asr_model_version": "fake-2"}))
    assert discourse_key(base) == discourse_key(base.model_copy(update={"prompt_versions": {"extract_slot_group": "v9"}}))
    assert discourse_key(base) != discourse_key(base.model_copy(update={"prompt_versions": {"rewrite": "v2"}}))
    assert run_key("agent", "r", base, execution="e1") != run_key("agent", "r", base, execution="e2")
    assert forecast_seed("run-call-1") == runner.forecast_seed("run-call-1")  # the same forecast in or out of Temporal
