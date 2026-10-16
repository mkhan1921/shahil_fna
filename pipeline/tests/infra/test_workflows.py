"""Workflows on the Temporal dev server with mocked activities (spec §13.1, §13.9).

Workers run with Worker Versioning on (the build is made the deployment's current version), the
production data converter (Pydantic payloads + payload offload to object storage), and the
review reminder shortened through ``CallArgs.review_reminder_seconds``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowFailureError

from advisor_intel.models.profile import StateOp
from advisor_intel.models.runs import CallArgs, CallResult, FollowupAnswer, ReviewSubmission, RunState, Stage
from advisor_intel.workflows import CallPipelineWorkflow, FollowupIntakeWorkflow, ForecastWorkflow, NightlyFeedbackWorkflow
from advisor_intel.workflows import contracts as C
from advisor_intel.workflows.schedules import MODEL_SWAP_SCHEDULE_ID, NIGHTLY_SCHEDULE_ID, SAST, ensure_schedules

from .temporal_harness import EXEC_TIMEOUT, MockActivities, running_worker, temporal_env, wait_for, wait_state

pytestmark = pytest.mark.temporal

NOW = datetime(2026, 10, 16, 10, 0, tzinfo=timezone.utc)


def _args(**kw) -> CallArgs:
    return CallArgs(call_id="call-1", advisor_id="adv-1", client_ids=["cl-1"], **kw)


def _review(ops: list[StateOp] | None = None) -> ReviewSubmission:
    return ReviewSubmission(call_id="call-1", reviewer_id="adv-1", profile_version=3, submitted_at=NOW, ops=ops or [])


def _answer(fid: str = "fu.retirement_age", text: str = "Sixty-three, I think") -> FollowupAnswer:
    return FollowupAnswer(call_id="call-1", followup_id=fid, text=text, answered_at=NOW)


async def test_full_run_completes_after_review(temporal_bin):
    mocks = MockActivities()
    async with temporal_env(temporal_bin) as env, running_worker(env.client, mocks) as tq:
        run_id = f"call-1-{uuid.uuid4().hex[:6]}"
        handle = await env.client.start_workflow(CallPipelineWorkflow.run, _args(), id=run_id, task_queue=tq, execution_timeout=EXEC_TIMEOUT)
        await wait_state(handle, RunState.AWAITING_REVIEW)
        status = await handle.query(CallPipelineWorkflow.run_status)
        assert status.profile_version == 3 and status.pos == 0.72 and not status.review_submitted
        await handle.signal(CallPipelineWorkflow.review_submitted, _review())
        result: CallResult = await handle.result()
    assert result == CallResult(call_id="call-1", run_id=run_id, profile_version=4, pos=0.72, partial=False, state=RunState.COMPLETE)
    assert mocks.names() == [
        C.INGEST_AUDIO,
        C.RUN_DISCOURSE,
        C.RUN_AGENT,
        C.RUN_VOI,
        C.RUN_FORECAST,
        C.NOTIFY_ADVISOR,
        C.APPLY_REVIEW,
        C.RENDER_NOTE,
        C.RUN_FORECAST,
    ]
    forecasts = mocks.inputs(C.RUN_FORECAST)
    assert [f.args.forecast_mode for f in forecasts] == ["preview", "final"]
    assert all(i.run_id == run_id for n, i in mocks.calls if hasattr(i, "run_id"))
    assert mocks.inputs(C.NOTIFY_ADVISOR)[0].kind == "review_ready"


async def test_rerun_from_a_later_stage_skips_earlier_stages(temporal_bin):
    mocks = MockActivities()
    async with temporal_env(temporal_bin) as env, running_worker(env.client, mocks) as tq:
        handle = await env.client.start_workflow(
            CallPipelineWorkflow.run, _args(start_stage=Stage.VOI), id=f"call-1-{uuid.uuid4().hex[:6]}", task_queue=tq, execution_timeout=EXEC_TIMEOUT
        )
        await wait_state(handle, RunState.AWAITING_REVIEW)
        await handle.signal(CallPipelineWorkflow.review_submitted, _review())
        await handle.result()
    assert mocks.names()[:3] == [C.RUN_VOI, C.RUN_FORECAST, C.NOTIFY_ADVISOR]
    assert C.INGEST_AUDIO not in mocks.names() and C.RUN_AGENT not in mocks.names()


async def test_review_reminders_while_waiting(temporal_bin):
    mocks = MockActivities()
    async with temporal_env(temporal_bin) as env, running_worker(env.client, mocks) as tq:
        handle = await env.client.start_workflow(
            CallPipelineWorkflow.run, _args(review_reminder_seconds=1), id=f"call-1-{uuid.uuid4().hex[:6]}", task_queue=tq, execution_timeout=EXEC_TIMEOUT
        )
        await wait_state(handle, RunState.AWAITING_REVIEW)
        await wait_for(lambda: len(mocks.inputs(C.SEND_REVIEW_REMINDER)) >= 2, timeout=15)
        await handle.signal(CallPipelineWorkflow.review_submitted, _review())
        result = await handle.result()
        status = await handle.query(CallPipelineWorkflow.run_status)
    reminders = mocks.inputs(C.SEND_REVIEW_REMINDER)
    assert [r.reminder_number for r in reminders][:2] == [1, 2] and all(r.kind == "review_reminder" for r in reminders)
    assert status.reminders_sent == len(reminders) and result.state == RunState.COMPLETE


async def test_followup_answers_during_review_are_taken_in(temporal_bin):
    mocks = MockActivities(failing_answers=("fu.bad",))
    async with temporal_env(temporal_bin) as env, running_worker(env.client, mocks) as tq:
        handle = await env.client.start_workflow(CallPipelineWorkflow.run, _args(), id=f"call-1-{uuid.uuid4().hex[:6]}", task_queue=tq, execution_timeout=EXEC_TIMEOUT)
        await wait_state(handle, RunState.AWAITING_REVIEW)
        await handle.signal(CallPipelineWorkflow.followup_answered, _answer())
        await wait_for(lambda: len(mocks.inputs(C.RUN_FORECAST)) >= 2)
        # An answer the intake cannot read is recorded; the call keeps waiting for review.
        await handle.signal(CallPipelineWorkflow.followup_answered, _answer("fu.bad", "???"))
        await wait_for(lambda: len(mocks.inputs(C.INTAKE_FOLLOWUP)) >= 2)
        status = await handle.query(CallPipelineWorkflow.run_status)
        assert status.state == RunState.AWAITING_REVIEW and status.answers_processed == 2 and status.failed_answers == ["fu.bad"]
        assert status.profile_version == 4  # the intake's new version, forecast again in preview
        await handle.signal(CallPipelineWorkflow.review_submitted, _review())
        result = await handle.result()
    names = mocks.names()
    i = names.index(C.INTAKE_FOLLOWUP)
    assert names[i : i + 2] == [C.INTAKE_FOLLOWUP, C.RUN_FORECAST]
    assert mocks.inputs(C.INTAKE_FOLLOWUP)[0].answer.text == "Sixty-three, I think"
    assert [f.args.forecast_mode for f in mocks.inputs(C.RUN_FORECAST)] == ["preview", "preview", "final"]
    assert result.profile_version == 5


async def test_agent_partial_still_goes_to_review(temporal_bin):
    mocks = MockActivities(partial=True)
    async with temporal_env(temporal_bin) as env, running_worker(env.client, mocks) as tq:
        handle = await env.client.start_workflow(CallPipelineWorkflow.run, _args(), id=f"call-1-{uuid.uuid4().hex[:6]}", task_queue=tq, execution_timeout=EXEC_TIMEOUT)
        await wait_state(handle, RunState.AWAITING_REVIEW)
        assert (await handle.query(CallPipelineWorkflow.run_status)).partial is True
        await handle.signal(CallPipelineWorkflow.review_submitted, _review())
        result = await handle.result()
    assert result.partial is True and result.state == RunState.COMPLETE
    assert mocks.inputs(C.NOTIFY_ADVISOR)[0].partial is True


async def test_manual_route_when_speakers_out_of_range(temporal_bin):
    mocks = MockActivities(manual=True)
    async with temporal_env(temporal_bin) as env, running_worker(env.client, mocks) as tq:
        result = await env.client.execute_workflow(
            CallPipelineWorkflow.run, _args(), id=f"call-1-{uuid.uuid4().hex[:6]}", task_queue=tq, execution_timeout=EXEC_TIMEOUT
        )
    assert result.state == RunState.MANUAL
    assert mocks.names() == [C.INGEST_AUDIO, C.NOTIFY_ADVISOR] and mocks.inputs(C.NOTIFY_ADVISOR)[0].kind == "manual_review"


async def test_large_review_submission_is_offloaded_to_object_storage(temporal_bin, objects):
    mocks = MockActivities()
    ops = [
        StateOp(op="CONFIRM", fact_id=f"expenses.items[]#item_{i}", segment_id=None, payload={"review": {"status": "accepted", "reviewer_id": "adv-1"}}, actor="reviewer", seq=100 + i)
        for i in range(400)
    ]
    async with temporal_env(temporal_bin, objects, threshold=16 * 1024) as env, running_worker(env.client, mocks) as tq:
        handle = await env.client.start_workflow(CallPipelineWorkflow.run, _args(), id=f"call-1-{uuid.uuid4().hex[:6]}", task_queue=tq, execution_timeout=EXEC_TIMEOUT)
        await wait_state(handle, RunState.AWAITING_REVIEW)
        await handle.signal(CallPipelineWorkflow.review_submitted, _review(ops))
        await handle.result()
        history = await handle.fetch_history()
    # The activity got every op back from storage …
    assert len(mocks.inputs(C.APPLY_REVIEW)[0].review.ops) == 400
    # … while the history holds only references.
    signal = next(e for e in history.events if e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED)
    payload = signal.workflow_execution_signaled_event_attributes.input.payloads[0]
    assert payload.ByteSize() < 2048 and payload.metadata["encoding"] == b"json/protobuf"
    assert any(k.startswith("temporal-payloads/") for k in await objects.list("temporal-payloads"))


async def test_followup_intake_workflow(temporal_bin):
    mocks = MockActivities()
    async with temporal_env(temporal_bin) as env, running_worker(env.client, mocks) as tq:
        out = await env.client.execute_workflow(
            FollowupIntakeWorkflow.run,
            C.FollowupIntakeArgs(run_id="call-1-run-a", args=_args(), answer=_answer()),
            id=f"call-1-run-a-followup-{uuid.uuid4().hex[:6]}",
            task_queue=tq,
            execution_timeout=EXEC_TIMEOUT,
        )
    assert mocks.names() == [C.INTAKE_FOLLOWUP, C.RUN_FORECAST]
    assert mocks.inputs(C.RUN_FORECAST)[0].run_id == "call-1-run-a" and mocks.inputs(C.RUN_FORECAST)[0].args.forecast_mode == "final"
    assert out.intake.followup_id == "fu.retirement_age" and out.forecast.pos == 0.72


async def test_forecast_workflow(temporal_bin):
    mocks = MockActivities()
    async with temporal_env(temporal_bin) as env, running_worker(env.client, mocks) as tq:
        args = _args().final().model_copy(update={"versions": _args().versions.model_copy(update={"cma_version": "2027Q1"})})
        out = await env.client.execute_workflow(
            ForecastWorkflow.run, C.ForecastArgs(run_id="call-1-run-a", args=args, profile_version=7), id=f"fc-{uuid.uuid4().hex[:6]}", task_queue=tq,
            execution_timeout=EXEC_TIMEOUT,
        )  # fmt: skip
    [inp] = mocks.inputs(C.RUN_FORECAST)
    assert inp.profile_version == 7 and inp.args.versions.cma_version == "2027Q1" and out.mode == "final"


async def test_nightly_feedback_runs_every_branch_in_order(temporal_bin):
    mocks = MockActivities()
    async with temporal_env(temporal_bin) as env, running_worker(env.client, mocks) as tq:
        until = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)
        out = await env.client.execute_workflow(
            NightlyFeedbackWorkflow.run, C.NightlyArgs(until=until), id=f"nightly-{uuid.uuid4().hex[:6]}", task_queue=tq, execution_timeout=EXEC_TIMEOUT
        )
    names = mocks.names()
    assert names.index(C.BUILD_LABELS) < names.index(C.RETRAIN_CALIBRATOR) < names.index(C.REFIT_CONFORMAL)
    assert set(names) >= {C.PROPOSE_LEXICON_FIT, C.SYNC_ERROR_DATASET, C.SYNC_GOLD_CANDIDATES}
    assert out.window.since == until - timedelta(days=1)
    assert out.conformal.tau == {"*": 0.93} and out.lexicon.keys_fitted == ["about"] and not out.failures


async def test_nightly_failure_in_one_branch_lets_the_others_finish(temporal_bin):
    mocks = MockActivities(failing_nightly=(C.RETRAIN_CALIBRATOR,))
    async with temporal_env(temporal_bin) as env, running_worker(env.client, mocks) as tq:
        with pytest.raises(WorkflowFailureError) as ei:
            await env.client.execute_workflow(
                NightlyFeedbackWorkflow.run, C.NightlyArgs(), id=f"nightly-{uuid.uuid4().hex[:6]}", task_queue=tq, execution_timeout=EXEC_TIMEOUT
            )
    assert "calibration" in str(ei.value.cause)
    names = mocks.names()
    assert C.REFIT_CONFORMAL not in names and {C.PROPOSE_LEXICON_FIT, C.SYNC_ERROR_DATASET, C.SYNC_GOLD_CANDIDATES} <= set(names)


async def test_schedules_are_created_then_updated(temporal_bin):
    async with temporal_env(temporal_bin) as env:
        assert await ensure_schedules(env.client, "advisor-intel") == {NIGHTLY_SCHEDULE_ID: "created", MODEL_SWAP_SCHEDULE_ID: "created"}
        assert await ensure_schedules(env.client, "advisor-intel") == {NIGHTLY_SCHEDULE_ID: "updated", MODEL_SWAP_SCHEDULE_ID: "updated"}
        desc = await env.client.get_schedule_handle(NIGHTLY_SCHEDULE_ID).describe()
        spec = desc.schedule.spec
        assert spec.time_zone_name == SAST
        [cal] = spec.calendars
        assert [r.start for r in cal.hour] == [2] and [r.start for r in cal.minute] == [0]
        assert desc.schedule.action.workflow == C.NIGHTLY_FEEDBACK
        swap = await env.client.get_schedule_handle(MODEL_SWAP_SCHEDULE_ID).describe()
        assert [r.start for r in swap.schedule.spec.calendars[0].day_of_month] == [1]
