"""The extraction experiment (§15.4): the offline runner, the Braintrust Eval definition and its gating reporter."""

from __future__ import annotations

from pathlib import Path

import pytest

from advisor_intel.evals import extraction_eval as E
from advisor_intel.evals.datasets import LocalBlobStore, LocalStore, Stage12Cache, stage12_builder, sync_synthetic
from advisor_intel.evals.synthetic import generate_calls
from advisor_intel.models.runs import VersionPins
from tests.evals_fakes import FakePipeline


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
    monkeypatch.setenv("ADVISOR_INTEL_DATA_DIR", str(tmp_path))
    calls = generate_calls(4, seed=13)
    golds = {c.call_id: c.gold for c in calls}
    store = LocalStore(tmp_path / "datasets")
    sync_synthetic(calls, store=store)
    cache = Stage12Cache(LocalBlobStore(tmp_path / "cache"))
    yield {"calls": calls, "golds": golds, "store": store, "cache": cache, "tmp": tmp_path}
    E.reset()


def test_run_local_scores_a_perfect_and_a_noisy_pipeline(world):
    cases = E.eval_cases("synthetic-calls", store=world["store"])
    assert len(cases) == 4 and all(c["input"]["dataset"] == "synthetic-calls" for c in cases)
    perfect = E.run_local(
        cases, runner=FakePipeline(world["golds"].__getitem__), pins=VersionPins(), cache=world["cache"], save=world["tmp"] / "p.json"
    )
    assert perfect.n_errors == 0
    for m in ("status_accuracy_tier_a", "slot_recall_tier_a", "value_coverage_tier_a", "grounding_tier_a", "attribution_tier_a"):
        assert perfect.scores[m] == pytest.approx(1.0), m
    assert perfect.summary["critical_miss_rate"] == 0.0 and perfect.summary["ece"] == pytest.approx(0.1, abs=0.02)
    assert (world["tmp"] / "p.json").exists() and E.report_path().exists()
    noisy = E.run_local(
        cases,
        runner=FakePipeline(world["golds"].__getitem__, drop=0.3, wrong=0.2, flip_attribution=0.3, seed=1),
        pins=VersionPins(),
        cache=world["cache"],
        save=False,
    )
    assert noisy.scores["slot_recall_tier_a"] < 0.9 and noisy.scores["value_coverage"] < 1.0 and noisy.scores["attribution"] < 1.0
    assert noisy.summary["critical_miss_rate"] > 0
    assert any(it.misses.get("slot_recall") for it in noisy.items)
    from advisor_intel.evals.gate import compare

    assert not compare(noisy, perfect).passed and compare(perfect, perfect).passed


def test_errored_items_are_reported_not_raised(world):
    cases = E.eval_cases("synthetic-calls", store=world["store"])

    def broken(call, pins):
        raise RuntimeError("provider down")

    rep = E.run_local(cases[:2], runner=broken, pins=VersionPins(), cache=world["cache"], save=False)
    assert rep.n_errors == 2 and "provider down" in rep.items[0].error and rep.task_error_rate == 1.0
    assert E._CONTEXT.runner is None  # noqa: SLF001 - run_local restores the context


def test_runner_gets_the_cached_call_and_pins(world):
    seen = []
    fake = FakePipeline(world["golds"].__getitem__)

    def runner(call, pins):
        seen.append((call.call_id, call.transcript_kind, len(call.transcript.segments), pins.reasoning_model))
        return fake(call, pins)

    E.configure(runner=runner, pins=VersionPins(reasoning_model="test:x"), cache=world["cache"])
    case = E.eval_cases("synthetic-calls", store=world["store"])[0]
    out = E.run_stages_3_to_6_from_cache(case["input"])
    assert seen[0][0] == case["input"]["call_id"] and seen[0][1] == "script" and seen[0][3] == "test:x"
    assert out["call_id"] == case["input"]["call_id"] and out["facts"] and out["call_minutes"] > 0


def test_default_runner_is_the_pipeline():
    try:
        import advisor_intel.pipeline  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="process_cached_call"):
            E.default_runner()
    else:
        assert callable(E.default_runner())


def test_current_version_pins_read_prompt_versions(tmp_path, monkeypatch):
    root = tmp_path / "prompts"
    for rel in ("extract_slot_group/income/v1.md", "extract_slot_group/income/v3.md", "entailment/v2.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    monkeypatch.setenv("ADVISOR_INTEL_PIN_CALIBRATOR_VERSION", "cal-7")
    pins = E.current_version_pins(prompts_root=root)
    assert pins.prompt_versions == {"entailment": "v2", "extract_slot_group/income": "v3"}
    assert pins.calibrator_version == "cal-7"
    # the same routing and ASR pins a production call starts with (advisor_intel.pipeline.deployment_pins)
    runner = pytest.importorskip("advisor_intel.pipeline")
    from advisor_intel.config import get_settings

    deployed = runner.deployment_pins(get_settings())
    assert (pins.asr_provider, pins.asr_model_version, pins.reasoning_model) == (
        deployed.asr_provider,
        deployed.asr_model_version,
        deployed.reasoning_model,
    )
    assert pins.asr_provider == get_settings().asr_provider != "fake"


def test_report_metric_lookup():
    rep = E.EvalReport(
        n_items=10,
        n_errors=1,
        scores={"slot_recall": 0.9},
        summary={"ece_by_group": {"income": 0.02}, "by_group": {"income": {"slot_recall": 0.8}}},
    )
    assert (
        rep.metric("slot_recall") == 0.9 and rep.metric("ece_by_group.income") == 0.02 and rep.metric("by_group.income.slot_recall") == 0.8
    )
    assert rep.metric("task_error_rate") == pytest.approx(0.1) and rep.metric("nope") is None


def test_braintrust_eval_runs_offline_and_the_reporter_gates(world, monkeypatch, capsys):
    monkeypatch.setenv("BRAINTRUST_DISABLE_ATEXIT_FLUSH", "1")
    result = E.braintrust_eval(
        dataset="synthetic-calls",
        runner=FakePipeline(world["golds"].__getitem__, drop=0.2, seed=3),
        pins=VersionPins(),
        cache=world["cache"],
        data=lambda: E.eval_cases("synthetic-calls", store=world["store"]),
        max_concurrency=2,
    )
    assert len(result.results) == 4 and not any(r.error for r in result.results)
    names = set(result.results[0].scores)
    assert {"status_accuracy", "status_accuracy_tier_a", "slot_recall_tier_a", "span_f1", "note_completeness"} <= names
    rep = E.load_report(E.report_path(dataset="synthetic-calls"))  # written by the reporter
    assert rep.n_items == 4 and rep.summary.get("ece") is not None and rep.pins["reasoning_model"] == "test"

    reporter = E.gate_reporter(dataset="synthetic-calls", gate=True, name="gate-test")
    better = rep.model_copy(update={"scores": {k: (v + 0.1 if v is not None else None) for k, v in rep.scores.items()}})
    baseline_path = world["tmp"] / "baseline.json"
    E.save_report(better, baseline_path)
    monkeypatch.setenv(E.BASELINE_ENV, str(baseline_path))
    assert reporter.report_run([rep], False, False) is False  # regressed against the better baseline
    monkeypatch.setenv(E.BASELINE_ENV, str(world["tmp"] / "missing.json"))
    assert reporter.report_run([rep], False, False) is True  # no baseline: nothing to gate
    assert "gate skipped" in capsys.readouterr().out
    # under ``braintrust eval --jsonl`` (the eval action) the reporter prints the summary as one JSON line, as the default one does
    import json

    reporter.report_eval(None, result, False, True)
    line = capsys.readouterr().out.strip().splitlines()[-1]
    printed = json.loads(line)
    assert printed["scores"] and printed["report"].endswith("advisor-extraction-synthetic-calls-latest.json")
    assert printed["summary_scores"]["ece"] == pytest.approx(rep.summary["ece"])


def test_the_asr_rows_use_the_gold_human_transcript_as_reference(world):
    """An ASR-transcript run is scored for numeric WER, DER and role accuracy against the call's human transcript
    from gold-transcripts; a human-transcript run, or a call without one, is not."""
    from advisor_intel.evals.datasets import GOLD_CALLS, sync_gold, sync_transcripts

    call = world["calls"][0]
    gold = call.gold.model_copy(update={"source": "real", "call_id": "real-0"})
    human = call.transcript().model_copy(update={"call_id": "real-0"})
    asr_t = human.model_copy(update={"segments": [s.model_copy(update={"speaker_role": "advisor"}) for s in human.segments]})
    sync_gold([gold], store=world["store"])
    sync_transcripts([human], store=world["store"], kind="human")
    sync_transcripts([asr_t], store=world["store"], kind="asr")
    lookup = E.gold_transcript_lookup(world["store"])
    assert lookup("real-0") == human and lookup("nope") is None
    fake = FakePipeline({"real-0": gold}.__getitem__)
    E.configure(runner=fake, pins=VersionPins(), cache=world["cache"], build=stage12_builder(store=world["store"], discourse=False))
    E.configure(reference_transcript=lookup)
    by_kind = {c["input"]["transcript_kind"]: c for c in E.eval_cases(GOLD_CALLS, store=world["store"])}
    asr_out = E.run_stages_3_to_6_from_cache(by_kind["asr"]["input"], by_kind["asr"]["metadata"])
    human_out = E.run_stages_3_to_6_from_cache(by_kind["human"]["input"], by_kind["human"]["metadata"])
    assert asr_out["transcript_metrics"]["numeric_wer"] == 0.0 and asr_out["transcript_metrics"]["der"] == 0.0
    assert asr_out["transcript_metrics"]["role_accuracy"] < 1.0  # every ASR segment carries the advisor role
    assert human_out["transcript_metrics"] == {}


async def test_the_default_runner_writes_the_meeting_note(monkeypatch):
    """The in-process runner leaves the note to the review; the eval task writes it on the system's profile."""
    from types import SimpleNamespace

    runner_mod = pytest.importorskip("advisor_intel.pipeline")
    note_mod = pytest.importorskip("advisor_intel.note")
    profile, written = object(), []

    async def process_cached_call(call, pins):
        return SimpleNamespace(call_id=call.call_id, note=None, profile=profile)

    async def generate_note(p, *, pins):
        written.append((p, pins))
        return SimpleNamespace(sentences=[])

    async def broken(p, *, pins):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(runner_mod, "process_cached_call", process_cached_call)
    monkeypatch.setattr(note_mod, "generate_note", generate_note)
    call, pins = SimpleNamespace(call_id="c1"), VersionPins()
    result = await E.default_runner()(call, pins)
    assert result.note is not None and written == [(profile, pins)]
    assert E.default_runner(note=False) is process_cached_call
    monkeypatch.setattr(note_mod, "generate_note", broken)
    assert (await E.default_runner()(call, pins)).note is None  # extraction is still scored without the note


def test_cli_runs_the_local_dataset(world, monkeypatch):
    monkeypatch.setattr(E, "default_runner", lambda **_: FakePipeline(world["golds"].__getitem__))
    E.configure(pins=VersionPins(), cache=world["cache"], build=stage12_builder(discourse=False), gold_pos=lambda gold: None)
    monkeypatch.setattr(E, "get_store", lambda: world["store"])
    out = world["tmp"] / "cli.json"
    assert E.main(["--dataset", "synthetic-calls", "--limit", "2", "--out", str(out)]) == 0
    assert E.load_report(Path(out)).n_items == 2
    assert E.main(["--dataset", "gold-calls"]) == 2  # empty dataset


def test_the_lexicon_version_pin_compares_versions_numerically(tmp_path):
    lex = tmp_path / "lexicon"
    lex.mkdir()
    for name, version in (("v1.3.yaml", "1.3"), ("v1.10.yaml", "1.10"), ("catalogue_za_v1.yaml", "za-1")):
        (lex / name).write_text(f"version: '{version}'\n")
    assert E._lexicon_version(lex) == "1.10"  # noqa: SLF001 - lexicographic order would pick v1.3
    assert E._lexicon_version(tmp_path / "missing") is None  # noqa: SLF001
    assert E.current_version_pins().lexicon_version == E._lexicon_version()  # noqa: SLF001 - the checkout's newest lexicon


def test_the_eval_reads_the_pipelines_own_forecast_seed():
    """§15.3: the gold profile is forecast on the system run's seed, which is one function shared by the in-process
    runner and the Temporal activities."""
    from advisor_intel.models.runs import forecast_seed

    assert forecast_seed("run-1") == forecast_seed("run-1") != forecast_seed("run-2")
    runner = pytest.importorskip("advisor_intel.pipeline")
    assert runner.forecast_seed is forecast_seed
    activities = pytest.importorskip("advisor_intel.activities.pipeline")
    assert activities.forecast_seed is forecast_seed
