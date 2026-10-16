"""The §15.4 CI gate, the baseline store and the model-swap protocol."""

from __future__ import annotations

import pytest
import yaml

from advisor_intel.evals import gate as G
from advisor_intel.evals import model_swap as M
from advisor_intel.evals.datasets import LocalStore
from advisor_intel.evals.extraction_eval import EvalReport, save_report
from advisor_intel.evals.scorers import TIER_A_METRICS
from advisor_intel.models.runs import VersionPins


def report(dataset="synthetic-calls", n_items=40, n_errors=0, ece=0.04, counts=None, ece_by_group=None, cost=0.05, **scores):
    base = {m: 0.9 for m in TIER_A_METRICS}
    base.update(scores)
    return EvalReport(dataset=dataset, n_items=n_items, n_errors=n_errors, scores=base,
                      summary={"ece": ece, "ece_by_group": ece_by_group or {"income": 0.03, "events": 0.2}, "ece_counts": counts or {"income": 120, "events": 4},
                               "cost_per_call_minute": cost, "brier": 0.1})


def test_tier_a_regressions_beyond_two_points_fail():
    base = report()
    assert G.compare(report(slot_recall_tier_a=0.881), base).passed  # −1.9 points
    res = G.compare(report(slot_recall_tier_a=0.879), base)  # −2.1 points
    assert not res.passed and [r.metric for r in res.failures] == ["slot_recall_tier_a"]
    assert "regressed" in res.markdown() and "❌" in res.markdown()


def test_ece_may_not_worsen_by_more_than_a_hundredth():
    base = report()
    assert G.compare(report(ece=0.0495), base).passed
    res = G.compare(report(ece=0.0505), base)
    assert [r.metric for r in res.failures] == ["ece"]
    # per-group ECE is gated where both reports have enough labelled facts: income yes, events (4 facts) no
    res = G.compare(report(ece_by_group={"income": 0.045, "events": 0.6}), base)
    assert [r.metric for r in res.failures] == ["ece_by_group.income"]
    assert "ece_by_group.events" not in {r.metric for r in res.rows}


def test_lost_metrics_and_task_errors():
    base = report()
    lost = report()
    lost.scores["grounding_tier_a"] = None
    res = G.compare(lost, base)
    assert [r.metric for r in res.failures] == ["grounding_tier_a"] and res.failures[0].note == "not measured in this experiment"
    assert not G.compare(report(n_errors=4), base).passed  # 10% of items errored vs none
    new_metric = report()
    base_without = report()
    base_without.scores["attribution_tier_a"] = None
    assert G.compare(new_metric, base_without).passed


def test_baseline_store_and_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
    monkeypatch.setenv("ADVISOR_INTEL_DATA_DIR", str(tmp_path))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    base_path = tmp_path / "base.json"
    save_report(report(), base_path)
    assert G.main(["publish", "--report", str(base_path), "--ref", "main"]) == 0
    stored = G.BaselineStore(LocalStore(tmp_path / "datasets")).get("advisor-extraction", "synthetic-calls", "main")
    assert stored is not None and stored.scores["slot_recall_tier_a"] == 0.9 and stored.items == []

    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    save_report(report(status_accuracy_tier_a=0.95), good)
    save_report(report(status_accuracy_tier_a=0.85), bad)
    assert G.main(["check", "--current", str(good), "--baseline-ref", "main"]) == 0
    assert G.main(["check", "--current", str(bad), "--baseline-ref", "main", "--markdown", str(tmp_path / "gate.md")]) == 1
    assert "status_accuracy_tier_a" in (tmp_path / "gate.md").read_text() and "Eval gate failed" in summary.read_text()
    assert G.main(["check", "--current", str(good), "--baseline", str(base_path)]) == 0
    other = tmp_path / "other.json"
    save_report(report(dataset="gold-calls"), other)
    assert G.main(["check", "--current", str(other)]) == 0  # no gold-calls baseline yet: skipped
    assert G.main(["check", "--current", str(other), "--require-baseline"]) == 2


def test_model_swap_wins_only_when_better_and_not_dearer():
    cur = report(cost=0.05)
    better = report(cost=0.04, status_accuracy_tier_a=0.93)
    d = M.decide("reasoning", cur, better, current_model="anthropic:a", candidate_model="openai:b")
    assert d.wins and "status_accuracy_tier_a" in d.reason
    assert not M.decide("reasoning", cur, report(cost=0.09, status_accuracy_tier_a=0.93), current_model="a", candidate_model="b").wins
    worse = report(status_accuracy_tier_a=0.95, value_coverage_tier_a=0.85)
    d = M.decide("reasoning", cur, worse, current_model="a", candidate_model="b")
    assert not d.wins and "value_coverage_tier_a" in d.reason
    assert not M.decide("reasoning", cur, report(), current_model="a", candidate_model="b").wins  # ties are not a win
    v = M.decide("verifier", report(ece=0.05), report(ece=0.03), current_model="a", candidate_model="b")
    assert v.wins  # lower ECE is better
    assert "| `ece` |" in v.markdown()


def test_evaluate_swap_pins_one_role_and_writes_routing(tmp_path):
    seen = []

    def run_eval(pins: VersionPins) -> EvalReport:
        seen.append(pins)
        return report(slot_recall_tier_a=0.95) if pins.reasoning_model == "openai:candidate" else report()

    pins = VersionPins(reasoning_model="anthropic:current")
    d = M.evaluate_swap("reasoning", "openai:candidate", pins=pins, run_eval=run_eval)
    assert d.wins and [p.reasoning_model for p in seen] == ["anthropic:current", "openai:candidate"]
    assert seen[1].model_copy(update={"reasoning_model": "anthropic:current"}) == pins  # nothing else changed
    path = M.write_routing_change(d, tmp_path / "routing.yaml")
    data = yaml.safe_load(path.read_text())
    assert data["roles"] == {"reasoning": "openai:candidate"} and data["history"][0]["from"] == "anthropic:current"
    assert M.routing_env(path) == {"ADVISOR_INTEL_MODEL_REASONING": "openai:candidate"}
    lose = M.decide("reasoning", report(), report(), current_model="a", candidate_model="b")
    with pytest.raises(ValueError):
        M.write_routing_change(lose, tmp_path / "routing.yaml")
    with pytest.raises(ValueError):
        M.evaluate_swap("orchestra", "x", pins=pins, run_eval=run_eval)
