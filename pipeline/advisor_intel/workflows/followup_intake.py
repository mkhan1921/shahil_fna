"""``FollowupIntakeWorkflow``: a follow-up answer recorded after the call workflow completed (spec §2.1, §10.6).

Runs extraction on the answer (a one-segment transcript attributed to the client), the reducer,
and the final forecast on the updated profile.
"""

from __future__ import annotations

from temporalio import workflow
from temporalio.common import VersioningBehavior

with workflow.unsafe.imports_passed_through():
    from .contracts import (
        FOLLOWUP_INTAKE,
        INTAKE_FOLLOWUP,
        RUN_FORECAST,
        AnswerInput,
        FollowupIntakeArgs,
        FollowupIntakeResult,
        ForecastOutcome,
        IntakeOutcome,
        StageInput,
    )
    from .options import FORECAST_OPTS, INTAKE_OPTS


@workflow.defn(name=FOLLOWUP_INTAKE, versioning_behavior=VersioningBehavior.PINNED)
class FollowupIntakeWorkflow:
    @workflow.run
    async def run(self, inp: FollowupIntakeArgs) -> FollowupIntakeResult:
        intake = await workflow.execute_activity(
            INTAKE_FOLLOWUP, AnswerInput(run_id=inp.run_id, args=inp.args, answer=inp.answer), result_type=IntakeOutcome, **INTAKE_OPTS
        )
        forecast = await workflow.execute_activity(
            RUN_FORECAST, StageInput(run_id=inp.run_id, args=inp.args.final()), result_type=ForecastOutcome, **FORECAST_OPTS
        )
        return FollowupIntakeResult(intake=intake, forecast=forecast)
