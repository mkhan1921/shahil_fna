"""``ForecastWorkflow``: forecast only, for new capital market assumptions or config (spec §2.1).

Re-runs ``run_forecast`` over a saved profile version of a run with the pins in ``args``
(typically a new ``cma_version`` or ``forecast_config_version``).
"""

from __future__ import annotations

from temporalio import workflow
from temporalio.common import VersioningBehavior

with workflow.unsafe.imports_passed_through():
    from .contracts import FORECAST_ONLY, RUN_FORECAST, ForecastArgs, ForecastOutcome, StageInput
    from .options import FORECAST_OPTS


@workflow.defn(name=FORECAST_ONLY, versioning_behavior=VersioningBehavior.PINNED)
class ForecastWorkflow:
    @workflow.run
    async def run(self, inp: ForecastArgs) -> ForecastOutcome:
        stage_in = StageInput(run_id=inp.run_id, args=inp.args, profile_version=inp.profile_version)
        return await workflow.execute_activity(RUN_FORECAST, stage_in, result_type=ForecastOutcome, **FORECAST_OPTS)
