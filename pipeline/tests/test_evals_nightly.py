"""The nightly feedback activities (spec §11.4, §13.6) calling the eval package's entry points as the worker binds
them: rows built by the activity code from stored review events, into the dataset syncs and the model-swap run."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from functools import partial

import pytest

pytest.importorskip("advisor_intel.activities.nightly")

from temporalio.testing import ActivityEnvironment  # noqa: E402

from advisor_intel.activities import NightlyActivities, StageFunctions  # noqa: E402
from advisor_intel.activities.stages import ENTRYPOINTS  # noqa: E402
from advisor_intel.config import Settings  # noqa: E402
from advisor_intel.evals import datasets as D  # noqa: E402
from advisor_intel.evals import model_swap as M  # noqa: E402
from advisor_intel.evals.extraction_eval import EvalReport  # noqa: E402
from advisor_intel.evals.gate import TIER_A_METRICS  # noqa: E402
from advisor_intel.models.runs import VersionPins  # noqa: E402
from advisor_intel.review_actions import apply_reviewer_ops, followup_edit_events  # noqa: E402
from advisor_intel.storage.records import ReviewEvent, RunRecord  # noqa: E402
from advisor_intel.workflows import contracts as C  # noqa: E402
from tests.test_evals_datasets import AT, profile_with_facts, review_result  # noqa: E402

WINDOW = C.NightlyWindow(since=AT - timedelta(hours=1), until=AT + timedelta(hours=1))


class ReviewStore:
    """The two reads these activities make of Postgres."""

    def __init__(self, events: list[ReviewEvent]) -> None:
        self.events = events

    async def review_events(self, *, since: datetime, until: datetime) -> list[ReviewEvent]:
        return [e for e in self.events if since <= e.at < until]

    async def get_run(self, run_id: str) -> RunRecord:
        return RunRecord(workflow_id=run_id, call_id="c1", versions=VersionPins())


def reviewed_events() -> list[ReviewEvent]:
    """A submitted review as the API stores it: the reviewer's ops through the reducer, then the follow-up edits."""
    profile = profile_with_facts()
    review = review_result(profile)
    _, events = apply_reviewer_ops(profile, review.ops, reviewer_id="adv-1", at=AT)
    return events + followup_edit_events(profile.run_id, review.followup_edits)


def env() -> ActivityEnvironment:
    e = ActivityEnvironment()
    e.info = dataclasses.replace(e.info, workflow_id="nightly", heartbeat_timeout=timedelta(seconds=3))
    return e


def test_the_worker_binds_these_entry_points():
    assert ENTRYPOINTS["sync_error_dataset"] == "advisor_intel.evals.datasets:sync_error_dataset"
    assert ENTRYPOINTS["sync_gold_candidates"] == "advisor_intel.evals.datasets:sync_gold_candidates"
    assert ENTRYPOINTS["run_model_swap"] == "advisor_intel.evals.model_swap:run_model_swap"
    stages = StageFunctions()
    assert stages.get("sync_error_dataset") is D.sync_error_dataset and stages.get("run_model_swap") is M.run_model_swap


async def test_the_nightly_dataset_syncs_write_errors_candidates_and_followup_edits(tmp_path):
    events = reviewed_events()
    bad_edit = {"edit": {"question": "?"}}  # an edit event that does not parse is skipped, not fatal to the night
    events.append(ReviewEvent(workflow_id="wf-1", action="edit_followup", reason="reword", reviewer_id="adv-1", at=AT, detail=bad_edit))
    local = D.LocalStore(tmp_path)
    stages = StageFunctions(
        {
            "sync_error_dataset": partial(D.sync_error_dataset, store=local),
            "sync_gold_candidates": partial(D.sync_gold_candidates, store=local),
        }
    )
    acts = NightlyActivities(store=ReviewStore(events), objects=None, settings=Settings(braintrust_api_key="bt"), stages=stages)

    errs = await env().run(acts.sync_error_dataset, WINDOW)
    gold = await env().run(acts.sync_gold_candidates, WINDOW)

    rows = {r.metadata["kind"]: r for r in local.fetch(D.PRODUCTION_ERRORS)}
    assert errs.inserted == 2 and set(rows) == {"rejected", "missed"}
    rejected = rows["rejected"]
    assert rejected.input["slot_id"] == "retirement.{p}.retirementAge" and rejected.input["quotes"] == ["I want to retire at sixty"]
    assert rejected.metadata["reason"] == "advisor_said_it" and rejected.metadata["tier"] == "A" and "group:retirement" in rejected.tags
    assert rejected.input["extracted"] and rejected.expected["action"] == "reject"
    assert rows["missed"].input["slot_id"] == "{p}.smoker" and rows["missed"].metadata["reason"] == "missed_slot"

    (cand,) = local.fetch(D.GOLD_CANDIDATES)
    (edit,) = local.fetch(D.FOLLOWUP_EDITS)
    assert gold.inserted == 2 and cand.input["call_id"] == "c1" and cand.expected["reviewed_profile"] == "profiles/wf-1/"
    assert edit.expected == {"action": "reword", "question": "Would 60, 63 or 65 be most realistic?"}

    again = await env().run(acts.sync_error_dataset, WINDOW)  # a repeated night upserts the same rows
    assert again.inserted == 2 and len(local.fetch(D.PRODUCTION_ERRORS)) == 2


async def test_without_a_key_the_activities_skip_before_calling_the_syncs():
    def unreachable(*a, **k):
        raise AssertionError("called without a Braintrust key")

    stages = StageFunctions({"sync_error_dataset": unreachable, "sync_gold_candidates": unreachable})
    acts = NightlyActivities(store=ReviewStore(reviewed_events()), objects=None, settings=Settings(braintrust_api_key=None), stages=stages)
    assert (await env().run(acts.sync_error_dataset, WINDOW)).skipped_reason


async def test_the_monthly_model_swap_activity_returns_the_decisions(tmp_path):
    def report(**scores) -> EvalReport:
        return EvalReport(
            dataset="gold-calls",
            n_items=40,
            scores={m: 0.9 for m in TIER_A_METRICS} | scores,
            summary={"ece": 0.04, "ece_by_group": {}, "ece_counts": {}, "cost_per_call_minute": 0.05, "brier": 0.1},
        )

    def run_eval(pins: VersionPins) -> EvalReport:
        return report(slot_recall_tier_a=0.95) if pins.reasoning_model == "openai:candidate" else report()

    opened = []

    def open_pr(decision) -> str:
        opened.append(decision.role)
        return "https://github.com/o/r/pull/9"

    run = partial(M.run_model_swap, pins=VersionPins(reasoning_model="anthropic:current"), run_eval=run_eval, open_pull_request=open_pr)
    acts = NightlyActivities(store=ReviewStore([]), objects=None, settings=Settings(), stages=StageFunctions({"run_model_swap": run}))
    out = await env().run(acts.run_model_swap_eval, C.ModelSwapArgs(candidates={"reasoning": "openai:candidate", "nope": "x"}))
    by_role = {r["role"]: r for r in out.results}
    assert out.pull_requests == ["https://github.com/o/r/pull/9"] and opened == ["reasoning"]
    assert by_role["reasoning"]["wins"] and "unknown role" in by_role["nope"]["skipped"]
