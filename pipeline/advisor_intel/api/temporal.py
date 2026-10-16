"""What the API asks of Temporal, behind one small interface so tests inject a fake (spec §11.5, §14.3).

``TemporalGateway`` starts call, follow-up intake and forecast workflows, queries a run's state,
sends the review and follow-up signals, and resets a run to a stage (re-run from stage, §2.1).
``ClientGateway`` implements it over a ``temporalio`` client.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Literal, Protocol

from pydantic import BaseModel
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.enums.v1 import EventType
from temporalio.api.workflowservice.v1 import ResetWorkflowExecutionRequest
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowQueryFailedError, WorkflowQueryRejectedError
from temporalio.common import WorkflowIDReusePolicy
from temporalio.service import RPCError, RPCStatusCode

from ..models.runs import CallArgs, FollowupAnswer, ReviewSubmission, Stage
from ..workflows import CallPipelineWorkflow, FollowupIntakeWorkflow, ForecastWorkflow
from ..workflows.contracts import (
    INGEST_AUDIO,
    NOTIFY_ADVISOR,
    RUN_AGENT,
    RUN_DISCOURSE,
    RUN_FORECAST,
    RUN_VOI,
    FollowupIntakeArgs,
    ForecastArgs,
    RunStatus,
)

log = logging.getLogger(__name__)

WorkflowState = Literal["RUNNING", "COMPLETED", "FAILED", "CANCELED", "TERMINATED", "CONTINUED_AS_NEW", "TIMED_OUT", "NOT_FOUND"]

# The activity a reset to each stage rewinds to (the workflow task that scheduled it).
STAGE_ACTIVITY: dict[Stage, str] = {
    Stage.INGEST: INGEST_AUDIO,
    Stage.DISCOURSE: RUN_DISCOURSE,
    Stage.AGENT: RUN_AGENT,
    Stage.VOI: RUN_VOI,
    Stage.FORECAST: RUN_FORECAST,
    Stage.REVIEW: NOTIFY_ADVISOR,
}


class WorkflowInfo(BaseModel):
    workflow_id: str
    status: WorkflowState
    execution_run_id: str | None = None


class ResetRefused(RuntimeError):
    pass


class TemporalGateway(Protocol):
    async def start_call(self, args: CallArgs, *, workflow_id: str) -> str: ...
    async def describe(self, workflow_id: str) -> WorkflowInfo: ...
    async def run_status(self, workflow_id: str) -> RunStatus | None: ...
    async def signal_review(self, workflow_id: str, review: ReviewSubmission) -> None: ...
    async def signal_answer(self, workflow_id: str, answer: FollowupAnswer) -> None: ...
    async def start_followup_intake(self, args: FollowupIntakeArgs, *, workflow_id: str) -> str: ...
    async def start_forecast(self, args: ForecastArgs, *, workflow_id: str) -> str: ...
    async def reset_to_stage(self, workflow_id: str, stage: Stage, *, reason: str) -> str: ...


class ClientGateway:
    """``TemporalGateway`` over a connected ``temporalio`` client."""

    def __init__(self, client: Client, task_queue: str) -> None:
        self.client = client
        self.task_queue = task_queue

    async def start_call(self, args: CallArgs, *, workflow_id: str) -> str:
        handle = await self.client.start_workflow(
            CallPipelineWorkflow.run,
            args,
            id=workflow_id,
            task_queue=self.task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            memo={"call_id": args.call_id, "start_stage": int(args.start_stage)},
        )
        return handle.id

    async def describe(self, workflow_id: str) -> WorkflowInfo:
        try:
            d = await self.client.get_workflow_handle(workflow_id).describe()
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                return WorkflowInfo(workflow_id=workflow_id, status="NOT_FOUND")
            raise
        status = d.status.name if isinstance(d.status, WorkflowExecutionStatus) else "RUNNING"
        return WorkflowInfo(workflow_id=workflow_id, status=status, execution_run_id=d.run_id)  # type: ignore[arg-type]

    async def run_status(self, workflow_id: str) -> RunStatus | None:
        """The run's status from the workflow query, or None when it cannot be read right now.

        A query needs a worker to answer it; with none polling (or the run gone) the API reports the
        state as unknown rather than failing the request.
        """
        try:
            return await self.client.get_workflow_handle(workflow_id).query(
                CallPipelineWorkflow.run_status, rpc_timeout=timedelta(seconds=10)
            )
        except (RPCError, WorkflowQueryFailedError, WorkflowQueryRejectedError) as e:
            log.warning("run_status query for %s failed: %s", workflow_id, e)
            return None

    async def signal_review(self, workflow_id: str, review: ReviewSubmission) -> None:
        await self.client.get_workflow_handle(workflow_id).signal(CallPipelineWorkflow.review_submitted, review)

    async def signal_answer(self, workflow_id: str, answer: FollowupAnswer) -> None:
        await self.client.get_workflow_handle(workflow_id).signal(CallPipelineWorkflow.followup_answered, answer)

    async def start_followup_intake(self, args: FollowupIntakeArgs, *, workflow_id: str) -> str:
        handle = await self.client.start_workflow(FollowupIntakeWorkflow.run, args, id=workflow_id, task_queue=self.task_queue)
        return handle.id

    async def start_forecast(self, args: ForecastArgs, *, workflow_id: str) -> str:
        handle = await self.client.start_workflow(ForecastWorkflow.run, args, id=workflow_id, task_queue=self.task_queue)
        return handle.id

    async def reset_to_stage(self, workflow_id: str, stage: Stage, *, reason: str) -> str:
        """Reset the run to just before the stage's activity was scheduled; returns the new execution's run id."""
        handle = self.client.get_workflow_handle(workflow_id)
        history = await handle.fetch_history()
        activity = STAGE_ACTIVITY[stage]
        target = None
        for e in history.events:
            if (
                e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                and e.activity_task_scheduled_event_attributes.activity_type.name == activity
            ):
                target = e.activity_task_scheduled_event_attributes.workflow_task_completed_event_id
                break
        if target is None:
            raise ResetRefused(f"run {workflow_id} never scheduled {activity}; start a new run from that stage instead")
        desc = await handle.describe()
        resp = await self.client.workflow_service.reset_workflow_execution(
            ResetWorkflowExecutionRequest(
                namespace=self.client.namespace,
                workflow_execution=WorkflowExecution(workflow_id=workflow_id, run_id=desc.run_id),
                reason=reason,
                workflow_task_finish_event_id=target,
                request_id=str(uuid.uuid4()),
            )
        )
        return resp.run_id
