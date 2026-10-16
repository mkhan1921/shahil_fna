"""The extraction experiment (§15.4): the offline runner, the Braintrust Eval definition and its gating reporter."""

from __future__ import annotations

from pathlib import Path

import pytest

from advisor_intel.evals import extraction_eval as E
from advisor_intel.evals.datasets import LocalBlobStore, LocalStore, Stage12Cache, sync_synthetic
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
    perfect = E.run_local(cases, runner=FakePipeline(world["golds"].__getitem__), pins=VersionPins(), cache=world["cache"], save=world["tmp"] / "p.json")
    assert perfect.n_errors == 0
    for m in ("status_accuracy_tier_a", "slot_recall_tier_a", "value_coverage_tier_a", "grounding_tier_a", "attribution_tier_a"):
        assert perfect.scores[m] == pytest.approx(1.0), m
    assert perfect.summary["critical_miss_rate"] == 0.0 and perfect.summary["ece"] == pytest.approx(0.1, abs=0.02)
    assert (world["tmp"] / "p.json").exists() and E.report_path().exists()
    noisy = E.run_local(cases, runner=FakePipeline(world["golds"].__getitem__, drop=0.3, wrong=0.2, flip_attribution=0.3, seed=1), pins=VersionPins(),
                        cache=world["cache"], save=False)
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


def test_report_metric_lookup():
    rep = E.EvalReport(n_items=10, n_errors=1, scores={"slot_recall": 0.9}, summary={"ece_by_group": {"income": 0.02}, "by_group": {"income": {"slot_recall": 0.8}}})
    assert rep.metric("slot_recall") == 0.9 and rep.metric("ece_by_group.income") == 0.02 and rep.metric("by_group.income.slot_recall") == 0.8
    assert rep.metric("task_error_rate") == pytest.approx(0.1) and rep.metric("nope") is None


def test_braintrust_eval_runs_offline_and_the_reporter_gates(world, monkeypatch, capsys):
    monkeypatch.setenv("BRAINTRUST_DISABLE_ATEXIT_FLUSH", "1")
    result = E.braintrust_eval(dataset="synthetic-calls", runner=FakePipeline(world["golds"].__getitem__, drop=0.2, seed=3), pins=VersionPins(),
                               cache=world["cache"], data=lambda: E.eval_cases("synthetic-calls", store=world["store"]), max_concurrency=2)
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


def test_cli_runs_the_local_dataset(world, monkeypatch):
    monkeypatch.setattr(E, "default_runner", lambda: FakePipeline(world["golds"].__getitem__))
    E.configure(pins=VersionPins(), cache=world["cache"])
    monkeypatch.setattr(E, "get_store", lambda: world["store"])
    out = world["tmp"] / "cli.json"
    assert E.main(["--dataset", "synthetic-calls", "--limit", "2", "--out", str(out)]) == 0
    assert E.load_report(Path(out)).n_items == 2
    assert E.main(["--dataset", "gold-calls"]) == 2  # empty dataset
