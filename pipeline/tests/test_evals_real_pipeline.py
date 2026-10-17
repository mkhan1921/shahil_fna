"""The extraction eval (§15.4) against the real in-process runner, ``advisor_intel.pipeline.process_cached_call``.

The gold-call oracle answers the extraction model's questions from the sample call's annotations, so everything
after the model call runs for real: the Stage 1–2 cache, stages 3–6, ``summarise_result`` and the §15.3 scorers.
Synthetic calls run on the "test" pins, whose schema-driven fakes give arbitrary values: those runs check that the
task completes, not its scores.
"""

from __future__ import annotations

import importlib.util

import pytest

pipeline = pytest.importorskip("advisor_intel.pipeline")

from advisor_intel import llm
from advisor_intel.evals import extraction_eval as E
from advisor_intel.evals.datasets import GOLD_CALLS, LocalBlobStore, LocalStore, Stage12Cache, sync_gold, sync_synthetic
from advisor_intel.evals.extraction_eval import load_rows, rows_path
from advisor_intel.evals.gold import GoldCall, GoldFact, GoldValue, Span
from advisor_intel.evals.synthetic import generate_calls
from advisor_intel.models.runs import VersionPins
from advisor_intel.testing.calls import SAMPLE_CALL, SAMPLE_GOLD, SAMPLE_OBJECTS
from advisor_intel.testing.fakes import scripted_model
from advisor_intel.testing.oracle import Oracle
from advisor_intel.testing.transcripts import ingested_transcript
from tests.evals_fakes import small_forecast

CALL_ID = "call-eval"


def span_of(transcript, quote: str) -> Span:
    for s in transcript.segments:
        i = s.text.find(quote)
        if i >= 0:
            return Span(segment_id=s.segment_id, char_start=i, char_end=i + len(quote), quote=quote, t_start=s.t_start, t_end=s.t_end)
    raise KeyError(quote)


@pytest.fixture
async def oracle_world(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
    monkeypatch.setenv("ADVISOR_INTEL_DATA_DIR", str(tmp_path))
    small_forecast(monkeypatch)
    transcript = await ingested_transcript(SAMPLE_CALL, call_id=CALL_ID)
    oracle = Oracle(transcript, SAMPLE_OBJECTS, SAMPLE_GOLD)
    pins = VersionPins(reasoning_model=llm.register_local_model("oracle-eval", scripted_model(oracle.handler)))
    yield {"transcript": transcript, "pins": pins, "tmp": tmp_path}
    llm.unregister_local_model("oracle-eval")
    E.reset()


def sample_gold(t) -> GoldCall:
    """Five annotated facts of the sample call (a partial gold call: recall, values, grounding and attribution)."""

    def fact(slot, inst, value, quote, attribution="client"):
        return GoldFact(slot_id=slot, instance_key=inst, status="stated", value=value, spans=[span_of(t, quote)], attribution=attribution)

    return GoldCall(
        call_id=CALL_ID,
        anchor_date=t.anchor_date,
        transcript="asr",
        facts=[
            fact("{p}.smoker", "client", GoldValue(kind="boolean", label="false"), "No, neither of us."),
            fact("{p}.smoker", "spouse", GoldValue(kind="boolean", label="false"), "No, neither of us."),
            fact(
                "income.{p}.grossMonthly",
                "spouse",
                GoldValue(kind="numeric", p10=33_000, p50=35_000, p90=37_000),
                "I earn about thirty-five a month gross",
            ),
            fact(
                "retirement.{p}.retirementAge", "client", GoldValue(kind="numeric", p10=60, p50=60, p90=62), "I'd like to retire at sixty."
            ),
            fact("estate.{p}.hasWill", "client", GoldValue(kind="categorical", label="yes"), "We did wills years ago, I think."),
        ],
    )


async def test_the_eval_task_scores_the_real_pipeline(oracle_world):
    t, pins, tmp = oracle_world["transcript"], oracle_world["pins"], oracle_world["tmp"]
    assert E.default_runner(note=False) is pipeline.process_cached_call  # the task's runner when none is configured
    store = LocalStore(tmp / "datasets")
    sync_gold([sample_gold(t)], store=store, transcript_kinds=("asr",))
    cases = E.eval_cases(GOLD_CALLS, store=store)
    built = []

    def build(record, p):  # Stage 1–2 for a gold call: the ingested transcript, discourse left to the runner
        built.append(record.id)
        return t, None

    cache = Stage12Cache(LocalBlobStore(tmp / "cache"))
    report = E.run_local(cases, pins=pins, cache=cache, build=build, dataset=GOLD_CALLS, experiment="oracle")
    assert report.n_items == 1 and report.n_errors == 0, report.items[0].error
    assert built == [f"{CALL_ID}:asr"]
    for m in ("slot_recall", "slot_recall_tier_a", "value_coverage", "attribution"):
        assert report.scores[m] == pytest.approx(1.0), (m, report.items[0].misses)
    assert report.scores["span_f1"] > 0.6
    # The meeting note, written after stages 3–6 on the "test" frontier pin (the note package's template): every
    # sentence it keeps is supported, and the gold facts it cites are measured.
    assert 0.0 < report.scores["note_completeness"] <= 1.0 and report.scores["note_supported"] == pytest.approx(1.0)
    row = load_rows(rows_path(E.EVAL_NAME, GOLD_CALLS))[0]
    out = row["output"]
    assert out["tool_calls"][0] == "make_plan" and "verify_facts" in out["tool_calls"] and out["document"] is not None
    if importlib.util.find_spec("advisor_intel.forecast") is not None:  # the preview forecast ran
        assert 0.0 <= out["pos"] <= 1.0 and isinstance(out["forecast_seed"], int)
    assert any(f["p_correct"] is not None for f in out["facts"])
    facts = {f["fact_id"]: f for f in out["facts"]}
    assert facts["income.{p}.grossMonthly#spouse"]["value"]["p50"] == pytest.approx(35_000)
    assert facts["{p}.smoker#client"]["value"]["p_true"] < 0.5 and facts["{p}.smoker#client"]["spans"]
    assert report.pins["reasoning_model"] == pins.reasoning_model


async def test_synthetic_calls_complete_on_the_real_pipeline(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
    monkeypatch.setenv("ADVISOR_INTEL_DATA_DIR", str(tmp_path))
    small_forecast(monkeypatch)
    store = LocalStore(tmp_path / "datasets")
    sync_synthetic(generate_calls(1, seed=77), store=store)
    cases = E.eval_cases("synthetic-calls", store=store)
    report = E.run_local(cases, pins=VersionPins(), cache=Stage12Cache(LocalBlobStore(tmp_path / "cache")), save=False)
    assert report.n_items == 1 and report.n_errors == 0, [it.error for it in report.items]
    assert all(it.scores for it in report.items) and "status_accuracy" in report.scores
    assert report.summary["n_needs"] > 0  # the system and gold documents went through the FNA engine
    E.reset()


def test_the_gold_profile_is_forecast_on_the_systems_seed(monkeypatch):
    """§15.3's forecast metric: the gold document's profile through the real forecast engine, on a given seed."""
    from advisor_intel.evals.scorers import forecast_gold_pos

    gold_pos = forecast_gold_pos()
    if gold_pos is None:
        pytest.skip("advisor_intel.forecast is not installed")
    small_forecast(monkeypatch)
    gold = generate_calls(1, seed=78)[0].gold
    a = gold_pos(gold, seed=11)
    assert a is not None and 0.0 <= a <= 1.0 and gold_pos(gold, seed=11) == a
    assert gold_pos(gold.model_copy(update={"document": None, "call_id": "no-doc"})) is None


async def test_gepa_candidates_reach_the_real_extraction_prompts(oracle_world):
    """The GEPA overlay swaps the pipeline's own prompt loader, so the extraction sub-agent runs on the candidate."""
    from advisor_intel import prompts
    from advisor_intel.evals import gepa_adapter as GA
    from advisor_intel.evals.datasets import stage12_key

    t, tmp = oracle_world["transcript"], oracle_world["tmp"]
    oracle = Oracle(t, SAMPLE_OBJECTS, SAMPLE_GOLD)
    seen: list[str] = []

    def handler(prompt, schema, info):
        seen.append(info.instructions or "")
        return oracle.handler(prompt, schema, info)

    pins = VersionPins(reasoning_model=llm.register_local_model("oracle-gepa", scripted_model(handler)))
    try:
        store = LocalStore(tmp / "datasets")
        sync_gold([sample_gold(t)], store=store, transcript_kinds=("asr",))
        cases = E.eval_cases(GOLD_CALLS, store=store)
        cache = Stage12Cache(LocalBlobStore(tmp / "cache"))
        cache.put(GOLD_CALLS, CALL_ID, stage12_key(pins, "asr"), t, None)
        component = GA.extract_component("retirement")
        real = GA.PromptStore().read(component)
        adapter = GA.ExtractionAdapter(runner=pipeline.process_cached_call, pins=pins, groups=["retirement"], cache=cache)
        candidate = {component: real + "\nGEPA-MARKER: read a hedged retirement age as a range.\n"}
        batch = adapter.evaluate(cases, candidate, capture_traces=True)
    finally:
        llm.unregister_local_model("oracle-gepa")
    assert batch.scores[0] > 0.9 and batch.outputs[0].get("error") is None, batch.outputs[0].get("error")
    assert any("GEPA-MARKER" in s and "## Slot group: retirement" in s for s in seen)
    assert not any("GEPA-MARKER" in s and "## Slot group: income" in s for s in seen)  # other groups keep their prompts
    assert GA.PromptStore().root == prompts.PROMPTS_DIR and "GEPA-MARKER" not in prompts.load_prompt(component).text
