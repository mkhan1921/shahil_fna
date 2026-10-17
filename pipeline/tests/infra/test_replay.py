"""Replay tests (spec §13.1, §13.9): recorded workflow histories must replay against the current worker code.

``fixtures/histories/*.json`` are histories recorded from real runs on the dev server (with mocked
activities). CI replays them on every change, so a non-deterministic change to a workflow fails
here instead of in a call that has been waiting days for review. To re-record after an intended,
versioned change: ``ADVISOR_INTEL_RECORD_HISTORIES=1 pytest -m temporal tests/infra/test_replay.py``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from advisor_intel.models.runs import CallArgs, FollowupAnswer, ReviewSubmission, RunState
from advisor_intel.workflows import (
    ALL_WORKFLOWS,
    CallPipelineWorkflow,
    FinetuneExtractorWorkflow,
    NightlyFeedbackWorkflow,
    TrainRlExtractorWorkflow,
)
from advisor_intel.workflows.contracts import FinetuneArgs, NightlyArgs, RlArgs
from advisor_intel.workflows.converter import data_converter

from .temporal_harness import EXEC_TIMEOUT, MockActivities, running_worker, temporal_env, wait_for, wait_state

HISTORIES = Path(__file__).parent / "fixtures" / "histories"


def _replayer() -> Replayer:
    return Replayer(workflows=ALL_WORKFLOWS, data_converter=data_converter())


def _histories() -> list[Path]:
    return sorted(HISTORIES.glob("*.json"))


def test_committed_histories_exist():
    names = {p.stem for p in _histories()}
    assert {"call_pipeline_full", "call_pipeline_partial_rerun", "nightly_feedback", "finetune_extractor", "train_rl_extractor"} <= names


@pytest.mark.parametrize("path", _histories(), ids=lambda p: p.stem)
async def test_recorded_history_replays(path: Path):
    data = json.loads(path.read_text())
    history = WorkflowHistory.from_json(data["workflow_id"], data["history"])
    result = await _replayer().replay_workflow(history)
    assert result.replay_failure is None


async def _record(env, handle, name: str) -> None:
    history = await handle.fetch_history()
    # A fresh history from the current code must replay before it is worth keeping.
    result = await _replayer().replay_workflow(history)
    assert result.replay_failure is None
    if os.environ.get("ADVISOR_INTEL_RECORD_HISTORIES") == "1":
        HISTORIES.mkdir(parents=True, exist_ok=True)
        (HISTORIES / f"{name}.json").write_text(
            json.dumps({"workflow_id": handle.id, "history": json.loads(history.to_json())}, indent=1) + "\n"
        )


@pytest.mark.temporal
async def test_record_and_replay_fresh_histories(temporal_bin):
    from datetime import datetime

    now = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
    args = CallArgs(call_id="call-replay", advisor_id="adv-1", client_ids=["cl-1"], review_reminder_seconds=1)
    mocks = MockActivities()
    async with temporal_env(temporal_bin) as env, running_worker(env.client, mocks) as tq:
        # A full run: a reminder, a follow-up answer during review, then the review.
        h = await env.client.start_workflow(
            CallPipelineWorkflow.run, args, id="call-replay-run-a", task_queue=tq, execution_timeout=EXEC_TIMEOUT
        )
        await wait_state(h, RunState.AWAITING_REVIEW)
        await wait_for(lambda: len(mocks.inputs("send_review_reminder")) >= 1)
        await h.signal(
            CallPipelineWorkflow.followup_answered,
            FollowupAnswer(call_id="call-replay", followup_id="fu.1", text="about 63", answered_at=now),
        )
        await wait_for(lambda: len(mocks.inputs("intake_followup_answer")) >= 1)
        await h.signal(
            CallPipelineWorkflow.review_submitted,
            ReviewSubmission(call_id="call-replay", reviewer_id="adv-1", profile_version=4, submitted_at=now),
        )
        await h.result()
        await _record(env, h, "call_pipeline_full")

    mocks = MockActivities(partial=True)
    async with temporal_env(temporal_bin) as env, running_worker(env.client, mocks) as tq:
        # A partial agent run, re-run from the agent stage.
        rerun = args.model_copy(update={"start_stage": 3, "review_reminder_seconds": 3600})
        h = await env.client.start_workflow(
            CallPipelineWorkflow.run, rerun, id="call-replay-run-b", task_queue=tq, execution_timeout=EXEC_TIMEOUT
        )
        await wait_state(h, RunState.AWAITING_REVIEW)
        await h.signal(
            CallPipelineWorkflow.review_submitted,
            ReviewSubmission(call_id="call-replay", reviewer_id="adv-1", profile_version=3, submitted_at=now),
        )
        await h.result()
        await _record(env, h, "call_pipeline_partial_rerun")
        h = await env.client.start_workflow(
            NightlyFeedbackWorkflow.run, NightlyArgs(until=now), id="nightly-replay", task_queue=tq, execution_timeout=EXEC_TIMEOUT
        )
        await h.result()
        await _record(env, h, "nightly_feedback")
        # The training runs (§15.8, §15.9): a full fine-tuning run and a full RL run with a signed-off review sheet.
        h = await env.client.start_workflow(
            FinetuneExtractorWorkflow.run,
            FinetuneArgs(label="finetune-replay", groups=["retirement"]),
            id="finetune-replay",
            task_queue=tq,
            execution_timeout=EXEC_TIMEOUT,
        )
        await h.result()
        await _record(env, h, "finetune_extractor")
        h = await env.client.start_workflow(
            TrainRlExtractorWorkflow.run,
            RlArgs(label="rl-replay", human_signed_off=True),
            id="rl-replay",
            task_queue=tq,
            execution_timeout=EXEC_TIMEOUT,
        )
        await h.result()
        await _record(env, h, "train_rl_extractor")
