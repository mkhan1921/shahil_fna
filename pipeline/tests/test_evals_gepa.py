"""The GEPA adapter (§15.6) against gepa 0.1.4's real optimiser, with a prompt-aware fake pipeline."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from advisor_intel.evals import gepa_adapter as GA
from advisor_intel.evals._runtime import PROMPTS_DIR_ENV
from advisor_intel.evals.datasets import LocalBlobStore, LocalStore, Stage12Cache, sync_synthetic
from advisor_intel.evals.extraction_eval import eval_cases
from advisor_intel.evals.gold import GoldCall
from advisor_intel.evals.synthetic import generate_calls
from advisor_intel.models.runs import VersionPins
from advisor_intel.schema.slots import load_catalog
from tests.evals_fakes import FakePipeline

CAT = load_catalog()
RULE_ATTRIBUTION = "Advisor suggestions are not client goals unless the client agrees."
RULE_IMPLIED = "Return implied retirement ages from 'in N years' with status implied."
COMPONENT = "extract_slot_group/retirement"


def prompt_text(pins: VersionPins, component: str) -> str:
    """What a prompt loader would read: the pinned version under ADVISOR_INTEL_PROMPTS_DIR."""
    root = Path(os.environ[PROMPTS_DIR_ENV])
    return (root / component / f"{pins.prompt_versions[component]}.md").read_text()


def make_runner(golds):
    def policy(gf, call, pins):
        if CAT.slot(gf.slot_id).group != "retirement":
            return {}
        text = prompt_text(pins, COMPONENT)
        if "Skip retirement" in text:
            return {"drop": True}
        out = {}
        if gf.attribution == "advisor_suggested" and RULE_ATTRIBUTION not in text:
            out["attribution"] = "client"
        if gf.status == "implied" and RULE_IMPLIED not in text:
            out["drop"] = True
        return out

    return FakePipeline(golds.__getitem__, policy=policy)


@pytest.fixture
def setup(tmp_path):
    calls = generate_calls(30, seed=31)
    golds = {c.call_id: c.gold for c in calls}
    store = LocalStore(tmp_path / "ds")
    sync_synthetic(calls, store=store)
    prompts = tmp_path / "prompts"
    (prompts / COMPONENT).mkdir(parents=True)
    (prompts / COMPONENT / "v1.md").write_text("Extract the retirement goal for each person.\n")
    (prompts / "entailment").mkdir()
    (prompts / "entailment" / "v1.md").write_text("Judge whether the evidence entails the claim.\n")
    cases = eval_cases("synthetic-calls", store=store)
    return {"cases": cases, "golds": golds, "store": GA.PromptStore(prompts), "cache": Stage12Cache(LocalBlobStore(tmp_path / "cache"))}


def test_prompt_store_versions_and_overlay(setup):
    store = setup["store"]
    assert store.latest(COMPONENT) == (1, "Extract the retirement goal for each person.\n")
    with store.overlay({COMPONENT: "new text", "entailment": store.read("entailment")}) as (root, versions):
        assert os.environ[PROMPTS_DIR_ENV] == str(root)
        assert versions["entailment"] == "v1" and versions[COMPONENT].startswith("v2+gepa.")
        assert (root / COMPONENT / "v2.md").read_text() == "new text" == (root / COMPONENT / f"{versions[COMPONENT]}.md").read_text()
    assert PROMPTS_DIR_ENV not in os.environ and not root.exists()
    assert store.versions(COMPONENT) == [1]  # the real tree is untouched
    with pytest.raises(ValueError):
        store.write_next(COMPONENT, store.read(COMPONENT))


def test_evaluate_scores_and_feedback(setup):
    adapter = GA.ExtractionAdapter(
        runner=make_runner(setup["golds"]), pins=VersionPins(), groups=["retirement"], prompt_store=setup["store"], cache=setup["cache"]
    )
    batch = [
        c
        for c in setup["cases"]
        if any(f.slot_id.startswith("retirement.") and f.status == "implied" for f in GoldCall.model_validate(c["expected"]).facts)
    ][:2]
    assert batch, "the synthetic set should contain implied retirement ages"
    seed = {COMPONENT: setup["store"].read(COMPONENT)}
    weak = adapter.evaluate(batch, seed, capture_traces=True)
    strong = adapter.evaluate(batch, {COMPONENT: seed[COMPONENT] + RULE_ATTRIBUTION + "\n" + RULE_IMPLIED}, capture_traces=True)
    assert all(s < 1.0 for s in weak.scores) and all(s == pytest.approx(1.0) for s in strong.scores)
    assert set(weak.objective_scores[0]) == {"status_accuracy", "slot_recall", "value_coverage"}
    s = weak.objective_scores[0]
    assert weak.scores[0] == pytest.approx(0.4 * s["status_accuracy"] + 0.4 * s["slot_recall"] + 0.2 * s["value_coverage"])
    feedback = "; ".join(line for t in weak.trajectories for line in t["feedback"])
    assert "missed retirement." in feedback and ".retirementAge" in feedback
    ds = adapter.make_reflective_dataset(seed, weak, [COMPONENT, "entailment"])
    rec = ds[COMPONENT][0]
    assert set(rec) == {"Inputs", "Generated Outputs", "Feedback", "score"} and "missed" in rec["Feedback"]
    assert "transcript excerpt" in rec["Inputs"] and "[s" in rec["Inputs"]["transcript excerpt"]
    assert ds["entailment"] and adapter.metric_calls == 4


def test_heldout_calls_never_reach_the_optimiser(setup):
    held = GA.heldout_ids(setup["cases"], n=5)
    assert len(held) == 5 and held == GA.heldout_ids(list(reversed(setup["cases"])), n=5)
    train, val, test = GA.split_cases(setup["cases"], heldout=held)
    assert {c["input"]["call_id"] for c in test} == held
    assert not ({c["input"]["call_id"] for c in train + val} & held)
    adapter = GA.ExtractionAdapter(
        runner=make_runner(setup["golds"]), pins=VersionPins(), heldout=held, prompt_store=setup["store"], cache=setup["cache"]
    )
    with pytest.raises(ValueError, match="held-out"):
        adapter.evaluate(test[:1], {COMPONENT: "x"})
    with pytest.raises(ValueError, match="100–500"):
        GA.check_budget(50)
    assert GA.check_budget(300) == 300


def test_gepa_optimises_the_instruction_block_and_promotes_it(setup):
    proposals = []

    def reflection_lm(prompt):
        text = prompt if isinstance(prompt, str) else str(prompt)
        proposals.append(text)
        current = text.split("```")[1].strip("\n")
        additions = []
        if "advisor suggestion as client goal" in text and RULE_ATTRIBUTION not in current:
            additions.append(RULE_ATTRIBUTION)
        if "missed retirement." in text and RULE_IMPLIED not in current:
            additions.append(RULE_IMPLIED)
        return "```\n" + current + ("\n" + "\n".join(additions) if additions else "") + "\n```"

    held = GA.heldout_ids(setup["cases"], n=6)
    runner = make_runner(setup["golds"])
    summary, result = GA.optimize_group(
        "retirement",
        setup["cases"],
        runner=runner,
        pins=VersionPins(),
        reflection_lm=reflection_lm,
        budget=60,
        enforce_budget=False,
        prompt_store=setup["store"],
        cache=setup["cache"],
        heldout=held,
        seed=1,
    )
    assert proposals, "GEPA should have asked the reflection model for a new instruction"
    assert "## Feedback" in proposals[0] or "Feedback" in proposals[0]
    best = summary.best_candidate[COMPONENT]
    assert RULE_ATTRIBUTION in best or RULE_IMPLIED in best
    assert summary.val_score_best > summary.val_score_seed
    assert summary.metric_calls <= 60 + 10  # GEPA may finish the iteration it started

    test_cases = [c for c in setup["cases"] if c["input"]["call_id"] in held]
    promo = GA.promote_if_gate_passes(
        summary.best_candidate, test_cases, runner=runner, pins=VersionPins(), prompt_store=setup["store"], cache=setup["cache"]
    )
    assert promo.promoted and promo.heldout_calls == 6
    assert promo.paths == [str(setup["store"].root / COMPONENT / "v2.md")]
    assert setup["store"].read(COMPONENT) == best + ("" if best.endswith("\n") else "\n")
    # a candidate that regresses on the held-out calls is not promoted
    worse = {COMPONENT: "Skip retirement goals entirely."}
    promo2 = GA.promote_if_gate_passes(
        worse, test_cases, runner=runner, pins=VersionPins(prompt_versions={}), prompt_store=setup["store"], cache=setup["cache"]
    )
    assert not promo2.promoted and "failed" in promo2.gate_markdown
