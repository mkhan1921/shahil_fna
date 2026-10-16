"""``NightlyFeedbackWorkflow`` and ``ModelSwapWorkflow`` (spec §11.4, §15.4).

Nightly (02:00 SAST, Temporal Schedule ``nightly_feedback``), over the day's review events:

1. ``build_calibration_labels`` → ``retrain_calibrator`` → ``refit_conformal`` (§9.3–9.5);
2. ``propose_lexicon_fit`` (§8.2, a pull request a human merges);
3. ``sync_error_dataset`` (Braintrust ``production-errors``);
4. ``sync_gold_candidates`` (gold expansion and GEPA feedback, §15.6).

The four branches run concurrently. A failed branch does not stop the others; the workflow fails
at the end, naming the failed steps, so the schedule's failure shows up in Temporal Cloud alerts.

Monthly, ``ModelSwapWorkflow`` runs the gold suite with candidate models pinned (§15.4).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import VersioningBehavior
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from .contracts import (
        BUILD_LABELS,
        MODEL_SWAP,
        NIGHTLY_FEEDBACK,
        PROPOSE_LEXICON_FIT,
        REFIT_CONFORMAL,
        RETRAIN_CALIBRATOR,
        RUN_MODEL_SWAP,
        SYNC_ERROR_DATASET,
        SYNC_GOLD_CANDIDATES,
        CalibratorOutcome,
        ConformalOutcome,
        DatasetSyncOutcome,
        LabelsOutcome,
        LexiconFitOutcome,
        ModelSwapArgs,
        ModelSwapOutcome,
        NightlyArgs,
        NightlyResult,
        NightlyWindow,
    )
    from .options import CONFORMAL_OPTS, DATASET_SYNC_OPTS, LABELS_OPTS, LEXICON_FIT_OPTS, MODEL_SWAP_OPTS, RETRAIN_OPTS


@workflow.defn(name=NIGHTLY_FEEDBACK, versioning_behavior=VersioningBehavior.PINNED)
class NightlyFeedbackWorkflow:
    @workflow.run
    async def run(self, args: NightlyArgs) -> NightlyResult:
        until = args.until or workflow.now()
        since = args.since or until - timedelta(days=1)
        window = NightlyWindow(since=since, until=until)
        result = NightlyResult(window=window)

        async def calibration() -> None:
            labels = await workflow.execute_activity(BUILD_LABELS, window, result_type=LabelsOutcome, **LABELS_OPTS)
            result.labels = labels
            cal = await workflow.execute_activity(RETRAIN_CALIBRATOR, labels, result_type=CalibratorOutcome, **RETRAIN_OPTS)
            result.calibrator = cal
            result.conformal = await workflow.execute_activity(REFIT_CONFORMAL, cal, result_type=ConformalOutcome, **CONFORMAL_OPTS)

        async def lexicon() -> None:
            result.lexicon = await workflow.execute_activity(PROPOSE_LEXICON_FIT, window, result_type=LexiconFitOutcome, **LEXICON_FIT_OPTS)

        async def errors() -> None:
            result.errors_dataset = await workflow.execute_activity(
                SYNC_ERROR_DATASET, window, result_type=DatasetSyncOutcome, **DATASET_SYNC_OPTS
            )

        async def gold() -> None:
            result.gold_candidates = await workflow.execute_activity(
                SYNC_GOLD_CANDIDATES, window, result_type=DatasetSyncOutcome, **DATASET_SYNC_OPTS
            )

        branches: dict[str, Any] = {
            "calibration": calibration(),
            "lexicon_fit": lexicon(),
            "error_dataset": errors(),
            "gold_candidates": gold(),
        }
        outcomes = await asyncio.gather(*branches.values(), return_exceptions=True)
        for name, out in zip(branches, outcomes):
            if isinstance(out, BaseException):
                result.failures[name] = str(getattr(out, "cause", None) or out)
        if result.failures:
            raise ApplicationError(
                f"nightly feedback: {', '.join(sorted(result.failures))} failed",
                result.model_dump(mode="json"),
                type="NightlyStepsFailed",
                non_retryable=True,
            )
        return result


@workflow.defn(name=MODEL_SWAP, versioning_behavior=VersioningBehavior.PINNED)
class ModelSwapWorkflow:
    @workflow.run
    async def run(self, args: ModelSwapArgs) -> ModelSwapOutcome:
        return await workflow.execute_activity(RUN_MODEL_SWAP, args, result_type=ModelSwapOutcome, **MODEL_SWAP_OPTS)
