"""``CallPipelineWorkflow``: one durable workflow per call (spec §2, §13.1).

The workflow holds no data: its payloads are the run id, the call's version pins and small
outcomes; every activity reads and writes Postgres or object storage and is idempotent on
``(call_id, stage, versions)``. Workflow code does no I/O and reads no clock or randomness except
through ``workflow.now()`` and timers, so it replays deterministically.

Flow: ingest → discourse → agent (stages 3–6) → VOI and follow-ups → preview forecast →
notify the adviser → wait for ``review_submitted`` (a reminder every ``review_reminder_seconds``,
3 days by default; follow-up answers that arrive meanwhile are taken in and the preview forecast
re-run) → apply the review → meeting note → final forecast. ``start_stage`` skips the stages
before it (a re-run reuses their stored outputs).
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import VersioningBehavior
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from ..models.runs import AgentOutcome, CallArgs, CallResult, FollowupAnswer, ReviewSubmission, RunState, Stage
    from .contracts import (
        APPLY_REVIEW,
        CALL_PIPELINE,
        INGEST_AUDIO,
        INTAKE_FOLLOWUP,
        NOTIFY_ADVISOR,
        RENDER_NOTE,
        RUN_AGENT,
        RUN_DISCOURSE,
        RUN_FORECAST,
        RUN_VOI,
        SEND_REVIEW_REMINDER,
        AnswerInput,
        ForecastOutcome,
        IngestOutcome,
        IntakeOutcome,
        NoteOutcome,
        NotifyInput,
        NotifyKind,
        NotifyOutcome,
        ReviewInput,
        ReviewOutcome,
        RunStatus,
        StageInput,
        StageOutcome,
        VoiOutcome,
    )
    from .options import (
        AGENT_OPTS,
        DISCOURSE_OPTS,
        FORECAST_OPTS,
        INGEST_OPTS,
        INTAKE_OPTS,
        NOTE_OPTS,
        NOTIFY_OPTS,
        REVIEW_OPTS,
        VOI_OPTS,
    )


# Open for days in review, so new worker versions take over running calls (AUTO_UPGRADE); every
# change must replay recorded histories (tests/infra/test_replay.py) or be gated by workflow.patched.
@workflow.defn(name=CALL_PIPELINE, versioning_behavior=VersioningBehavior.AUTO_UPGRADE)
class CallPipelineWorkflow:
    def __init__(self) -> None:
        self.review: ReviewSubmission | None = None
        self.answers: list[FollowupAnswer] = []
        self.state = RunState.STARTED
        self._processed = 0
        self._stage = ""
        self._partial = False
        self._manual = False
        self._reminders = 0
        self._profile_version: int | None = None
        self._pos: float | None = None
        self._failed_answers: list[str] = []
        self._note_error: str | None = None

    # ------------------------------------------------------------------ signals and queries

    @workflow.signal
    def review_submitted(self, r: ReviewSubmission) -> None:
        if self.review is None:  # the first submission wins; the API refuses later ones
            self.review = r

    @workflow.signal
    def followup_answered(self, a: FollowupAnswer) -> None:
        self.answers.append(a)

    @workflow.query
    def run_state(self) -> RunState:
        return self.state

    @workflow.query
    def run_status(self) -> RunStatus:
        return RunStatus(
            state=self.state,
            stage=self._stage,
            partial=self._partial,
            manual=self._manual,
            review_submitted=self.review is not None,
            answers_received=len(self.answers),
            answers_processed=self._processed,
            reminders_sent=self._reminders,
            profile_version=self._profile_version,
            pos=self._pos,
            failed_answers=list(self._failed_answers),
            note_error=self._note_error,
        )

    # ------------------------------------------------------------------ run

    @workflow.run
    async def run(self, args: CallArgs) -> CallResult:
        try:
            return await self._run(args)
        except BaseException:
            self.state = RunState.FAILED  # what run_state reports for a run that ended in an error
            raise

    async def _run(self, args: CallArgs) -> CallResult:
        run_id = workflow.info().workflow_id
        stage_in = StageInput(run_id=run_id, args=args)

        if args.start_stage <= Stage.INGEST:
            self._stage = INGEST_AUDIO
            ingest = await workflow.execute_activity(INGEST_AUDIO, stage_in, result_type=IngestOutcome, **INGEST_OPTS)
            self.state = RunState.INGESTED
            if ingest.manual:
                # Speakers outside 2–4 (§5.5): the run stops here and goes to manual handling.
                self.state = RunState.MANUAL
                self._manual = True
                await self._notify(run_id, args, "manual_review")
                return CallResult(call_id=args.call_id, run_id=run_id, profile_version=0, state=RunState.MANUAL)
        if args.start_stage <= Stage.DISCOURSE:
            self._stage = RUN_DISCOURSE
            await workflow.execute_activity(RUN_DISCOURSE, stage_in, result_type=StageOutcome, **DISCOURSE_OPTS)
            self.state = RunState.DISCOURSE_DONE
        if args.start_stage <= Stage.AGENT:
            self.state = RunState.AGENT_RUNNING
            self._stage = RUN_AGENT
            outcome = await workflow.execute_activity(RUN_AGENT, stage_in, result_type=AgentOutcome, **AGENT_OPTS)
            self._partial = outcome.partial
        if args.start_stage <= Stage.VOI:
            self._stage = RUN_VOI
            voi = await workflow.execute_activity(RUN_VOI, stage_in, result_type=VoiOutcome, **VOI_OPTS)
            self._profile_version = voi.profile_version
        if args.start_stage <= Stage.FORECAST:
            await self._forecast(StageInput(run_id=run_id, args=args.preview()))
        if args.start_stage <= Stage.AGENT:
            self.state = RunState.AGENT_PARTIAL if self._partial else RunState.AGENT_DONE

        self.state = RunState.AWAITING_REVIEW
        self._stage = "review"
        await self._notify(run_id, args, "review_ready")
        await self._await_review(run_id, args)

        assert self.review is not None
        self._stage = APPLY_REVIEW
        applied = await workflow.execute_activity(
            APPLY_REVIEW, ReviewInput(run_id=run_id, review=self.review), result_type=ReviewOutcome, **REVIEW_OPTS
        )
        self._profile_version = applied.profile_version
        self.state = RunState.REVIEWED
        await self._take_answers(run_id, args, forecast=False)  # answers that arrived after submission
        await self._render_note(stage_in)

        result = await self._forecast(StageInput(run_id=run_id, args=args.final()))
        # An answer signalled while the note and the final forecast ran would otherwise be lost: the API
        # signals a run it saw RUNNING, and FollowupIntakeWorkflow only takes answers once this run is over.
        while self._pending_answers():
            await self._take_answers(run_id, args, forecast=False)
            await self._render_note(stage_in)
            result = await self._forecast(StageInput(run_id=run_id, args=args.final()))
        self.state = RunState.COMPLETE
        self._stage = ""
        return CallResult(
            call_id=args.call_id,
            run_id=run_id,
            profile_version=result.profile_version,
            pos=result.pos,
            partial=self._partial,
            state=RunState.COMPLETE,
        )

    # ------------------------------------------------------------------ steps

    async def _await_review(self, run_id: str, args: CallArgs) -> None:
        """Wait for the review; a reminder every period; take in follow-up answers as they come."""
        period = timedelta(seconds=max(1, args.review_reminder_seconds))
        deadline = workflow.now() + period
        while self.review is None:
            if self._pending_answers():
                await self._take_answers(run_id, args, forecast=True)
                continue
            remaining = deadline - workflow.now()
            if remaining <= timedelta(0):
                self._reminders += 1
                await self._notify(run_id, args, "review_reminder", reminder=self._reminders, activity=SEND_REVIEW_REMINDER)
                deadline = workflow.now() + period
                continue
            try:
                await workflow.wait_condition(lambda: self.review is not None or self._pending_answers(), timeout=remaining)
            except TimeoutError:
                pass  # the loop sends the reminder

    def _pending_answers(self) -> bool:
        return self._processed < len(self.answers)

    async def _take_answers(self, run_id: str, args: CallArgs, *, forecast: bool) -> None:
        """Run follow-up intake for each new answer (§10.6), then re-run the preview forecast once."""
        took = False
        while self._pending_answers():
            answer = self.answers[self._processed]
            self._stage = INTAKE_FOLLOWUP
            try:
                out = await workflow.execute_activity(
                    INTAKE_FOLLOWUP, AnswerInput(run_id=run_id, args=args, answer=answer), result_type=IntakeOutcome, **INTAKE_OPTS
                )
                self._profile_version = out.profile_version
                took = True
            except ActivityError as e:
                # One unreadable answer must not end a call that is waiting for review: record it and go on.
                workflow.logger.warning("follow-up intake failed for %s: %s", answer.followup_id, e.cause or e)
                self._failed_answers.append(answer.followup_id)
            self._processed += 1
        if took and forecast:
            await self._forecast(StageInput(run_id=run_id, args=args.preview()))
        self._stage = "review" if self.review is None else self._stage

    async def _forecast(self, stage_in: StageInput) -> ForecastOutcome:
        self._stage = RUN_FORECAST
        out = await workflow.execute_activity(RUN_FORECAST, stage_in, result_type=ForecastOutcome, **FORECAST_OPTS)
        self._profile_version = out.profile_version
        self._pos = out.pos
        return out

    async def _notify(self, run_id: str, args: CallArgs, kind: NotifyKind, *, reminder: int = 0, activity: str = NOTIFY_ADVISOR) -> None:
        """Notifications are best effort: a mail outage must not fail a call waiting for review."""
        try:
            await workflow.execute_activity(
                activity,
                NotifyInput(run_id=run_id, args=args, kind=kind, reminder_number=reminder, partial=self._partial),
                result_type=NotifyOutcome,
                **NOTIFY_OPTS,
            )
        except ActivityError as e:
            workflow.logger.warning("%s (%s) failed: %s", activity, kind, e.cause or e)

    async def _render_note(self, stage_in: StageInput) -> None:
        """The meeting note is rendered after review (§11.6); a failure leaves it to be regenerated from the API."""
        self._stage = RENDER_NOTE
        try:
            await workflow.execute_activity(RENDER_NOTE, stage_in, result_type=NoteOutcome, **NOTE_OPTS)
        except ActivityError as e:
            self._note_error = str(e.cause or e)
            workflow.logger.warning("meeting note failed: %s", self._note_error)
