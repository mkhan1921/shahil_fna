"""The §15.4 CI gate, the baseline store and the model-swap protocol."""

from __future__ import annotations

import base64
import json

import httpx
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
    return EvalReport(
        dataset=dataset,
        n_items=n_items,
        n_errors=n_errors,
        scores=base,
        summary={
            "ece": ece,
            "ece_by_group": ece_by_group or {"income": 0.03, "events": 0.2},
            "ece_counts": counts or {"income": 120, "events": 4},
            "cost_per_call_minute": cost,
            "brier": 0.1,
        },
    )


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


def _github(existing: str | None):
    """A fake GitHub REST API: records requests, serves the base ref and the routing file, opens pull request 7."""
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/repos/o/r")
        seen.append((request.method, path, json.loads(request.content) if request.content else {}))
        if request.method == "GET" and path == "/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "base-sha"}})
        if request.method == "POST" and path == "/git/refs":
            return httpx.Response(201, json={})
        if request.method == "GET" and path == "/contents/pipeline/routing.yaml":
            if existing is None:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"content": base64.b64encode(existing.encode()).decode(), "sha": "file-sha"})
        if request.method == "PUT" and path == "/contents/pipeline/routing.yaml":
            return httpx.Response(200, json={})
        if request.method == "POST" and path == "/pulls":
            return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/7"})
        return httpx.Response(404, json={})

    return M.GitHubPullRequests("o/r", "token", client=httpx.Client(transport=httpx.MockTransport(handler))), seen


def test_run_model_swap_opens_a_pull_request_for_a_win():
    runs = []

    def run_eval(pins: VersionPins) -> EvalReport:
        runs.append(pins)
        if pins.reasoning_model == "openai:candidate":
            return report(slot_recall_tier_a=0.95)
        return report(ece=0.08) if pins.verifier_model == "openai:worse" else report()

    opener, seen = _github("roles:\n  verifier: anthropic:v\nhistory: []\n")
    pins = VersionPins(reasoning_model="anthropic:current", verifier_model="anthropic:v")
    out = M.run_model_swap(
        {"reasoning": "openai:candidate", "verifier": "openai:worse", "small": "test", "orchestra": "x"},
        pins=pins,
        run_eval=run_eval,
        open_pull_request=opener,
    )
    assert out["pull_requests"] == ["https://github.com/o/r/pull/7"]
    by_role = {r["role"]: r for r in out["results"]}
    assert by_role["reasoning"]["wins"] and by_role["reasoning"]["pull_request"].endswith("/7")
    assert not by_role["verifier"]["wins"] and "ece" in by_role["verifier"]["reason"]
    assert by_role["small"]["skipped"] == "already the current model" and "unknown role" in by_role["orchestra"]["skipped"]
    assert len(runs) == 3 and runs[0] == pins  # one baseline run serves every role
    assert [(m, p) for m, p, _ in seen][:2] == [("GET", "/git/ref/heads/main"), ("POST", "/git/refs")]
    ref = next(b for m, p, b in seen if p == "/git/refs")
    assert ref["sha"] == "base-sha" and ref["ref"].startswith("refs/heads/model-swap/reasoning-openai-candidate-")
    put = next(b for m, p, b in seen if m == "PUT")
    routing = yaml.safe_load(base64.b64decode(put["content"]).decode())
    assert put["sha"] == "file-sha" and routing["roles"] == {"verifier": "anthropic:v", "reasoning": "openai:candidate"}
    pr = next(b for m, p, b in seen if p == "/pulls")
    assert pr["base"] == "main" and pr["head"] == ref["ref"].removeprefix("refs/heads/") and "| `slot_recall_tier_a` |" in pr["body"]


def test_run_model_swap_without_github_writes_the_local_routing_file(tmp_path, monkeypatch):
    for var in ("GITHUB_REPOSITORY", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    def run_eval(pins: VersionPins) -> EvalReport:
        return report(ece=0.02) if pins.verifier_model == "openai:v2" else report()

    out = M.run_model_swap({"verifier": "openai:v2"}, pins=VersionPins(), run_eval=run_eval, routing_path=tmp_path / "routing.yaml")
    assert out["pull_requests"] == [] and out["results"][0]["routing_file"] == str(tmp_path / "routing.yaml")
    assert yaml.safe_load((tmp_path / "routing.yaml").read_text())["roles"] == {"verifier": "openai:v2"}
    with pytest.raises(ValueError, match="gold-calls"):
        M.gold_suite(store=LocalStore(tmp_path / "empty"))
