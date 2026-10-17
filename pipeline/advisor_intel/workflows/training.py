"""``FinetuneExtractorWorkflow`` (spec §15.8) and ``TrainRlExtractorWorkflow`` (§15.9).

Both start on demand (the API's ``POST /admin/training/finetune`` and ``/rl``, or ``workflows.client``); the spec
gives the fine-tuning run a cadence (quarterly, or after every 200 new reviewed calls) but no schedule, and RL runs
only when its start conditions hold, so neither has a Temporal Schedule. Promotion is never automatic: a run ends
with a routing change for a pull request and a registry record, and the calibrator of a switched group is retrained
after the switch.

``FinetuneExtractorWorkflow``: ``export_finetune_data`` (the §15.8 trigger; ends early with the reason when it does
not hold) → ``sft_train_extractor`` → ``evaluate_extractor_candidate`` (the gold experiment, per slot group) →
``promote_extractor_groups``.

``TrainRlExtractorWorkflow``: ``check_rl_start_conditions`` (ends early with the recorded reasons) →
``export_rl_prompts`` → ``grpo_train_extractor`` → ``run_rl_hacking_checks`` → ``evaluate_extractor_candidate`` (the
held-out gold calls against the fine-tuned model it started from, plus the hacking checks and a person's sign-off) →
``promote_extractor_groups``.
"""

from __future__ import annotations

from temporalio import workflow
from temporalio.common import VersioningBehavior

with workflow.unsafe.imports_passed_through():
    from .contracts import (
        CHECK_RL_START,
        EVALUATE_EXTRACTOR,
        EXPORT_FINETUNE_DATA,
        EXPORT_RL_PROMPTS,
        FINETUNE_EXTRACTOR,
        GRPO_TRAIN_EXTRACTOR,
        PROMOTE_EXTRACTOR,
        RL_HACKING_CHECKS,
        SFT_TRAIN_EXTRACTOR,
        TRAIN_RL_EXTRACTOR,
        CandidateEvalInput,
        CandidateEvalOutcome,
        FinetuneArgs,
        FinetuneResult,
        GrpoInput,
        GrpoOutcome,
        HackingInput,
        HackingOutcome,
        PromotionInput,
        PromotionOutcome,
        RlArgs,
        RlExportInput,
        RlExportOutcome,
        RlResult,
        RlStartOutcome,
        SftInput,
        SftOutcome,
        TrainingExportOutcome,
    )
    from .options import (
        CANDIDATE_EVAL_OPTS,
        GRPO_OPTS,
        HACKING_OPTS,
        PROMOTE_OPTS,
        RL_START_OPTS,
        SFT_OPTS,
        TRAINING_EXPORT_OPTS,
    )


@workflow.defn(name=FINETUNE_EXTRACTOR, versioning_behavior=VersioningBehavior.PINNED)
class FinetuneExtractorWorkflow:
    def __init__(self) -> None:
        self.step = "export"

    @workflow.query
    def progress(self) -> str:
        return self.step

    @workflow.run
    async def run(self, args: FinetuneArgs) -> FinetuneResult:
        args = args.model_copy(update={"label": args.label or workflow.info().workflow_id})
        export = await workflow.execute_activity(EXPORT_FINETUNE_DATA, args, result_type=TrainingExportOutcome, **TRAINING_EXPORT_OPTS)
        result = FinetuneResult(export=export)
        if not export.eligible:
            self.step = "skipped"
            result.skipped_reason = export.reason
            return result
        self.step = "sft"
        sft = await workflow.execute_activity(SFT_TRAIN_EXTRACTOR, SftInput(args=args, export=export), result_type=SftOutcome, **SFT_OPTS)
        result.sft = sft
        self.step = "evaluate"
        evaluation = await workflow.execute_activity(
            EVALUATE_EXTRACTOR,
            CandidateEvalInput(label=args.label, candidate_model=sft.model, groups=export.groups),
            result_type=CandidateEvalOutcome,
            **CANDIDATE_EVAL_OPTS,
        )
        result.evaluation = evaluation
        self.step = "promote"
        result.promotion = await workflow.execute_activity(
            PROMOTE_EXTRACTOR,
            PromotionInput(
                label=args.label,
                kind="sft",
                evaluation=evaluation,
                checkpoint=sft.checkpoint,
                base_model=sft.base_model,
                prompt_versions=export.prompt_versions,
            ),
            result_type=PromotionOutcome,
            **PROMOTE_OPTS,
        )
        self.step = "done"
        return result


@workflow.defn(name=TRAIN_RL_EXTRACTOR, versioning_behavior=VersioningBehavior.PINNED)
class TrainRlExtractorWorkflow:
    def __init__(self) -> None:
        self.step = "start_conditions"

    @workflow.query
    def progress(self) -> str:
        return self.step

    @workflow.run
    async def run(self, args: RlArgs) -> RlResult:
        args = args.model_copy(update={"label": args.label or workflow.info().workflow_id})
        start = await workflow.execute_activity(CHECK_RL_START, args, result_type=RlStartOutcome, **RL_START_OPTS)
        result = RlResult(start=start)
        if not start.can_start:
            self.step = "skipped"
            result.skipped_reason = "start conditions not met: " + "; ".join(start.reasons)
            return result
        self.step = "export"
        export = await workflow.execute_activity(
            EXPORT_RL_PROMPTS, RlExportInput(args=args, start=start), result_type=RlExportOutcome, **TRAINING_EXPORT_OPTS
        )
        result.export = export
        if not export.n_prompts:
            self.step = "skipped"
            result.skipped_reason = "no slot-group prompts outside the held-out calls"
            return result
        self.step = "grpo"
        training = await workflow.execute_activity(
            GRPO_TRAIN_EXTRACTOR, GrpoInput(args=args, start=start, export=export), result_type=GrpoOutcome, **GRPO_OPTS
        )
        result.training = training
        self.step = "hacking_checks"
        hacking = await workflow.execute_activity(
            RL_HACKING_CHECKS, HackingInput(args=args, export=export, training=training), result_type=HackingOutcome, **HACKING_OPTS
        )
        result.hacking = hacking
        self.step = "evaluate"
        evaluation = await workflow.execute_activity(
            EVALUATE_EXTRACTOR,
            CandidateEvalInput(
                label=args.label,
                candidate_model=training.model,
                groups=start.live_groups,
                incumbent_model=start.reference_model,
                heldout_only=True,
                require_better=True,
                hacking_passed=hacking.passed,
                human_signed_off=hacking.human_signed_off,
            ),
            result_type=CandidateEvalOutcome,
            **CANDIDATE_EVAL_OPTS,
        )
        result.evaluation = evaluation
        self.step = "promote"
        result.promotion = await workflow.execute_activity(
            PROMOTE_EXTRACTOR,
            PromotionInput(
                label=args.label, kind="rl", evaluation=evaluation, checkpoint=training.checkpoint, base_model=training.base_model
            ),
            result_type=PromotionOutcome,
            **PROMOTE_OPTS,
        )
        self.step = "done"
        return result
