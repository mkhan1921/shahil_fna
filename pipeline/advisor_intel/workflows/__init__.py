"""Temporal workflows (spec §13.1, §14.1): the call pipeline, follow-up intake, forecast-only, the nightly feedback loop,
and the fine-tuning (§15.8) and RL (§15.9) training runs."""

from .call_pipeline import CallPipelineWorkflow
from .followup_intake import FollowupIntakeWorkflow
from .forecast import ForecastWorkflow
from .nightly import ModelSwapWorkflow, NightlyFeedbackWorkflow
from .training import FinetuneExtractorWorkflow, TrainRlExtractorWorkflow

ALL_WORKFLOWS = [
    CallPipelineWorkflow,
    FollowupIntakeWorkflow,
    ForecastWorkflow,
    NightlyFeedbackWorkflow,
    ModelSwapWorkflow,
    FinetuneExtractorWorkflow,
    TrainRlExtractorWorkflow,
]

__all__ = [
    "ALL_WORKFLOWS",
    "CallPipelineWorkflow",
    "FinetuneExtractorWorkflow",
    "FollowupIntakeWorkflow",
    "ForecastWorkflow",
    "ModelSwapWorkflow",
    "NightlyFeedbackWorkflow",
    "TrainRlExtractorWorkflow",
]
