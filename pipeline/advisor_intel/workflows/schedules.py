"""Temporal Schedules for the learning loop (spec §11.4, §15.4).

- ``nightly_feedback``: ``NightlyFeedbackWorkflow`` every day at 02:00 SAST;
- ``monthly_model_swap``: ``ModelSwapWorkflow`` on the 1st of each month at 03:00 SAST.

``ensure_schedules`` creates them, or updates them in place when they exist, so a deploy can call
it every time. Overlapping runs are skipped; a missed run within the catch-up window still runs.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    SchedulePolicy,
    ScheduleOverlapPolicy,
    ScheduleRange,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
    ScheduleUpdateInput,
)

from .contracts import MODEL_SWAP, NIGHTLY_FEEDBACK, ModelSwapArgs, NightlyArgs

SAST = "Africa/Johannesburg"
NIGHTLY_SCHEDULE_ID = "nightly_feedback"
MODEL_SWAP_SCHEDULE_ID = "monthly_model_swap"


def nightly_schedule(task_queue: str, *, paused: bool = False) -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            NIGHTLY_FEEDBACK, NightlyArgs(), id="nightly-feedback", task_queue=task_queue, execution_timeout=timedelta(hours=6)
        ),
        spec=ScheduleSpec(calendars=[ScheduleCalendarSpec(hour=[ScheduleRange(2)], comment="02:00 SAST")], time_zone_name=SAST),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP, catchup_window=timedelta(hours=12)),
        state=ScheduleState(paused=paused, note="calibration labels, calibrator, conformal threshold, lexicon fit, Braintrust datasets"),
    )


def model_swap_schedule(task_queue: str, *, paused: bool = False) -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            MODEL_SWAP, ModelSwapArgs(), id="monthly-model-swap", task_queue=task_queue, execution_timeout=timedelta(hours=12)
        ),
        spec=ScheduleSpec(
            calendars=[ScheduleCalendarSpec(day_of_month=[ScheduleRange(1)], hour=[ScheduleRange(3)], comment="1st of the month, 03:00 SAST")],
            time_zone_name=SAST,
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP, catchup_window=timedelta(days=1)),
        state=ScheduleState(paused=paused, note="gold suite with candidate models pinned per role"),
    )


def schedules(task_queue: str) -> dict[str, Schedule]:
    return {NIGHTLY_SCHEDULE_ID: nightly_schedule(task_queue), MODEL_SWAP_SCHEDULE_ID: model_swap_schedule(task_queue)}


async def ensure_schedules(client: Client, task_queue: str) -> dict[str, str]:
    """Create or update every schedule. Returns ``{schedule_id: "created" | "updated"}``."""
    out: dict[str, str] = {}
    for sid, sched in schedules(task_queue).items():
        try:
            await client.create_schedule(sid, sched)
            out[sid] = "created"
        except ScheduleAlreadyRunningError:

            def update(inp: ScheduleUpdateInput, new: Schedule = sched) -> ScheduleUpdate:
                # Keep a pause an operator set by hand.
                new.state.paused = inp.description.schedule.state.paused
                return ScheduleUpdate(schedule=new)

            await client.get_schedule_handle(sid).update(update)
            out[sid] = "updated"
    return out
