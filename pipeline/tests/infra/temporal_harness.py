"""Mock activities and a worker harness for workflow tests on the Temporal dev server.

The mocks have the real activity names and payload types, record every call, and return small
outcomes, so the workflows run exactly as in production without Postgres, object storage or models.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from advisor_intel.models.runs import AgentOutcome, RunState
from advisor_intel.workflows import ALL_WORKFLOWS, CallPipelineWorkflow
from advisor_intel.workflows import contracts as C
from advisor_intel.workflows.converter import data_converter
from advisor_intel.workflows.versioning import deployment_config, set_current_version


@dataclass
class MockActivities:
    partial: bool = False
    manual: bool = False
    failing_answers: tuple[str, ...] = ()
    failing_nightly: tuple[str, ...] = ()
    calls: list[tuple[str, Any]] = field(default_factory=list)
    profile_version: int = 3

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def inputs(self, name: str) -> list[Any]:
        return [p for n, p in self.calls if n == name]

    def build(self) -> list[Any]:
        m = self

        @activity.defn(name=C.INGEST_AUDIO)
        async def ingest_audio(inp: C.StageInput) -> C.IngestOutcome:
            m.calls.append((C.INGEST_AUDIO, inp))
            return C.IngestOutcome(cache_key="asr-1", manual=m.manual)

        @activity.defn(name=C.RUN_DISCOURSE)
        async def run_discourse(inp: C.StageInput) -> C.StageOutcome:
            m.calls.append((C.RUN_DISCOURSE, inp))
            return C.StageOutcome(stage="discourse", cache_key="disc-1")

        @activity.defn(name=C.RUN_AGENT)
        async def run_agent(inp: C.StageInput) -> AgentOutcome:
            m.calls.append((C.RUN_AGENT, inp))
            return AgentOutcome(
                partial=m.partial,
                reason="token budget exhausted" if m.partial else None,
                turns=12,
                unfinished_groups=["estate"] if m.partial else [],
            )

        @activity.defn(name=C.RUN_VOI)
        async def run_voi(inp: C.StageInput) -> C.VoiOutcome:
            m.calls.append((C.RUN_VOI, inp))
            return C.VoiOutcome(profile_version=m.profile_version, n_followups=2, n_flags=1)

        @activity.defn(name=C.RUN_FORECAST)
        async def run_forecast(inp: C.StageInput) -> C.ForecastOutcome:
            m.calls.append((C.RUN_FORECAST, inp))
            return C.ForecastOutcome(run_id=inp.run_id, profile_version=m.profile_version, mode=inp.args.forecast_mode, pos=0.72)

        @activity.defn(name=C.APPLY_REVIEW)
        async def apply_review(inp: C.ReviewInput) -> C.ReviewOutcome:
            m.calls.append((C.APPLY_REVIEW, inp))
            m.profile_version += 1
            return C.ReviewOutcome(profile_version=m.profile_version, applied=len(inp.review.ops), changes={"confirmed": 4, "changed": 1})

        @activity.defn(name=C.NOTIFY_ADVISOR)
        async def notify_advisor(inp: C.NotifyInput) -> C.NotifyOutcome:
            m.calls.append((C.NOTIFY_ADVISOR, inp))
            return C.NotifyOutcome(sent=True, channel="log")

        @activity.defn(name=C.SEND_REVIEW_REMINDER)
        async def send_review_reminder(inp: C.NotifyInput) -> C.NotifyOutcome:
            m.calls.append((C.SEND_REVIEW_REMINDER, inp))
            return C.NotifyOutcome(sent=True, channel="log")

        @activity.defn(name=C.INTAKE_FOLLOWUP)
        async def intake(inp: C.AnswerInput) -> C.IntakeOutcome:
            m.calls.append((C.INTAKE_FOLLOWUP, inp))
            if inp.answer.followup_id in m.failing_answers:
                raise ApplicationError("could not read the answer", type="InputError", non_retryable=True)
            m.profile_version += 1
            return C.IntakeOutcome(followup_id=inp.answer.followup_id, profile_version=m.profile_version, applied=1)

        @activity.defn(name=C.RENDER_NOTE)
        async def render_note(inp: C.StageInput) -> C.NoteOutcome:
            m.calls.append((C.RENDER_NOTE, inp))
            return C.NoteOutcome(profile_version=m.profile_version, sentences=6)

        def check(name: str, inp: Any) -> None:
            m.calls.append((name, inp))
            if name in m.failing_nightly:
                raise ApplicationError(f"{name} broke", non_retryable=True)

        @activity.defn(name=C.BUILD_LABELS)
        async def build_labels(w: C.NightlyWindow) -> C.LabelsOutcome:
            check(C.BUILD_LABELS, w)
            return C.LabelsOutcome(since=w.since, until=w.until, n_events=40, n_labels=35, n_calls=3)

        @activity.defn(name=C.RETRAIN_CALIBRATOR)
        async def retrain(labels: C.LabelsOutcome) -> C.CalibratorOutcome:
            check(C.RETRAIN_CALIBRATOR, labels)
            return C.CalibratorOutcome(version="cal-2", promoted=True, n_labels=labels.n_labels)

        @activity.defn(name=C.REFIT_CONFORMAL)
        async def refit(cal: C.CalibratorOutcome) -> C.ConformalOutcome:
            check(C.REFIT_CONFORMAL, cal)
            return C.ConformalOutcome(calibrator_version=cal.version, tau={"*": 0.93}, n_calib_calls=55, auto_accept_enabled=True)

        @activity.defn(name=C.PROPOSE_LEXICON_FIT)
        async def lexicon_fit(w: C.NightlyWindow) -> C.LexiconFitOutcome:
            check(C.PROPOSE_LEXICON_FIT, w)
            return C.LexiconFitOutcome(n_pairs=25, keys_fitted=["about"])

        @activity.defn(name=C.SYNC_ERROR_DATASET)
        async def sync_errors(w: C.NightlyWindow) -> C.DatasetSyncOutcome:
            check(C.SYNC_ERROR_DATASET, w)
            return C.DatasetSyncOutcome(dataset="production-errors", inserted=4)

        @activity.defn(name=C.SYNC_GOLD_CANDIDATES)
        async def sync_gold(w: C.NightlyWindow) -> C.DatasetSyncOutcome:
            check(C.SYNC_GOLD_CANDIDATES, w)
            return C.DatasetSyncOutcome(dataset="gold-candidates", inserted=1)

        @activity.defn(name=C.RUN_MODEL_SWAP)
        async def model_swap(a: C.ModelSwapArgs) -> C.ModelSwapOutcome:
            check(C.RUN_MODEL_SWAP, a)
            return C.ModelSwapOutcome(results=[{"role": "small", "won": False}])

        return [
            ingest_audio,
            run_discourse,
            run_agent,
            run_voi,
            run_forecast,
            apply_review,
            notify_advisor,
            send_review_reminder,
            intake,
            render_note,
            build_labels,
            retrain,
            refit,
            lexicon_fit,
            sync_errors,
            sync_gold,
            model_swap,
        ]


@contextlib.asynccontextmanager
async def temporal_env(temporal_bin: str, objects: Any = None, *, threshold: int | None = None) -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_local(
        dev_server_existing_path=temporal_bin, data_converter=data_converter(objects, threshold=threshold), dev_server_log_level="error"
    )
    try:
        yield env
    finally:
        await env.shutdown()


@contextlib.asynccontextmanager
async def running_worker(client: Client, mocks: MockActivities) -> AsyncIterator[str]:
    """A versioned worker (its build made current) running every workflow over the mocks."""
    task_queue = f"tq-{uuid.uuid4().hex[:8]}"
    cfg = deployment_config(build=f"test-{uuid.uuid4().hex[:6]}")
    async with Worker(client, task_queue=task_queue, workflows=ALL_WORKFLOWS, activities=mocks.build(), deployment_config=cfg):
        await set_current_version(client, cfg.version)
        yield task_queue


async def wait_state(handle: WorkflowHandle, state: RunState, timeout: float = 20.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        cur = await handle.query(CallPipelineWorkflow.run_state)
        if cur == state:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"workflow state is {cur}, waited for {state}")
        await asyncio.sleep(0.1)


async def wait_for(pred, timeout: float = 20.0, interval: float = 0.1) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(interval)


EXEC_TIMEOUT = timedelta(minutes=2)
