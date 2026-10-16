"""Temporal workflows (spec §13.1, §14.1): the call pipeline, follow-up intake, forecast-only and the nightly feedback loop."""

from .call_pipeline import CallPipelineWorkflow
from .followup_intake import FollowupIntakeWorkflow
from .forecast import ForecastWorkflow
from .nightly import ModelSwapWorkflow, NightlyFeedbackWorkflow

ALL_WORKFLOWS = [CallPipelineWorkflow, FollowupIntakeWorkflow, ForecastWorkflow, NightlyFeedbackWorkflow, ModelSwapWorkflow]

__all__ = [
    "ALL_WORKFLOWS",
    "CallPipelineWorkflow",
    "FollowupIntakeWorkflow",
    "ForecastWorkflow",
    "ModelSwapWorkflow",
    "NightlyFeedbackWorkflow",
]
