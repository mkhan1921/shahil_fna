"""Workflow and activity payloads (spec §13.1): ids, version pins and small outcomes only.

Every activity reads and writes Postgres or object storage; what crosses the workflow boundary
is a run id, the call's ``CallArgs`` and a handful of counts. Activities are named by string so
workflow code never imports activity implementations (and their I/O libraries).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..models.runs import AgentOutcome, CallArgs, FollowupAnswer, ReviewSubmission, RunState
from ..models.transcript import QualityReport

# --------------------------------------------------------------------------- activity names

INGEST_AUDIO = "ingest_audio"
RUN_DISCOURSE = "run_discourse"
RUN_AGENT = "run_agent"
RUN_VOI = "run_voi_and_followups"
RUN_FORECAST = "run_forecast"
APPLY_REVIEW = "apply_review"
NOTIFY_ADVISOR = "notify_advisor"
SEND_REVIEW_REMINDER = "send_review_reminder"
INTAKE_FOLLOWUP = "intake_followup_answer"
RENDER_NOTE = "render_meeting_note"
BUILD_LABELS = "build_calibration_labels"
RETRAIN_CALIBRATOR = "retrain_calibrator"
REFIT_CONFORMAL = "refit_conformal"
PROPOSE_LEXICON_FIT = "propose_lexicon_fit"
SYNC_ERROR_DATASET = "sync_error_dataset"
SYNC_GOLD_CANDIDATES = "sync_gold_candidates"
RUN_MODEL_SWAP = "run_model_swap_eval"

PIPELINE_ACTIVITIES = (
    INGEST_AUDIO,
    RUN_DISCOURSE,
    RUN_AGENT,
    RUN_VOI,
    RUN_FORECAST,
    APPLY_REVIEW,
    NOTIFY_ADVISOR,
    SEND_REVIEW_REMINDER,
    INTAKE_FOLLOWUP,
    RENDER_NOTE,
)
NIGHTLY_ACTIVITIES = (BUILD_LABELS, RETRAIN_CALIBRATOR, REFIT_CONFORMAL, PROPOSE_LEXICON_FIT, SYNC_ERROR_DATASET, SYNC_GOLD_CANDIDATES, RUN_MODEL_SWAP)

# Workflow type names (stable across code moves: they are what the server and histories record).
CALL_PIPELINE = "CallPipelineWorkflow"
FOLLOWUP_INTAKE = "FollowupIntakeWorkflow"
FORECAST_ONLY = "ForecastWorkflow"
NIGHTLY_FEEDBACK = "NightlyFeedbackWorkflow"
MODEL_SWAP = "ModelSwapWorkflow"

# --------------------------------------------------------------------------- call pipeline


class StageInput(BaseModel):
    """What a pipeline activity receives: the run it belongs to (the call workflow's id) and the call's args."""

    run_id: str
    args: CallArgs
    profile_version: int | None = None  # forecast a specific saved version (ForecastWorkflow); None = latest


class IngestOutcome(BaseModel):
    cached: bool = False
    cache_key: str = ""
    quality: QualityReport | None = None
    manual: bool = False  # speakers outside 2–4: route to manual handling (§5.5)
    role_review_needed: bool = False


class StageOutcome(BaseModel):
    stage: str
    cached: bool = False
    cache_key: str = ""
    detail: dict[str, Any] = {}


class VoiOutcome(BaseModel):
    cached: bool = False
    profile_version: int = 0
    n_followups: int = 0
    n_flags: int = 0


class ForecastOutcome(BaseModel):
    run_id: str
    profile_version: int
    mode: Literal["preview", "final"]
    pos: float | None = None
    cached: bool = False


class ReviewInput(BaseModel):
    run_id: str
    review: ReviewSubmission


class ReviewOutcome(BaseModel):
    profile_version: int
    applied: int = 0
    rejected: int = 0
    review_events: int = 0
    changes: dict[str, int] = {}  # new / changed / confirmed / removed / unchanged_not_discussed counts (§3.6)
    cached: bool = False


NotifyKind = Literal["review_ready", "review_reminder", "manual_review", "review_complete"]


class NotifyInput(BaseModel):
    run_id: str
    args: CallArgs
    kind: NotifyKind
    reminder_number: int = 0
    partial: bool = False  # the agent ran out of budget: unfinished groups are marked (§13.6)


class NotifyOutcome(BaseModel):
    sent: bool
    channel: str
    duplicate: bool = False
    recipient: str | None = None


class AnswerInput(BaseModel):
    run_id: str
    args: CallArgs
    answer: FollowupAnswer


class IntakeOutcome(BaseModel):
    followup_id: str
    profile_version: int
    applied: int = 0
    rejected: int = 0
    duplicate: bool = False


class NoteOutcome(BaseModel):
    profile_version: int
    sentences: int = 0
    removed: int = 0
    skipped: bool = False


class RunStatus(BaseModel):
    """The ``run_status`` query: ``run_state`` plus what the review overlay and API show."""

    state: RunState
    stage: str = ""
    partial: bool = False
    manual: bool = False
    review_submitted: bool = False
    answers_received: int = 0
    answers_processed: int = 0
    reminders_sent: int = 0
    profile_version: int | None = None
    pos: float | None = None
    failed_answers: list[str] = []
    note_error: str | None = None


# --------------------------------------------------------------------------- other workflows


class FollowupIntakeArgs(BaseModel):
    run_id: str  # the completed call workflow whose profile the answer updates
    args: CallArgs
    answer: FollowupAnswer


class FollowupIntakeResult(BaseModel):
    intake: IntakeOutcome
    forecast: ForecastOutcome


class ForecastArgs(BaseModel):
    """Forecast only (§2.1): new capital market assumptions or config over a saved profile version."""

    run_id: str
    args: CallArgs
    profile_version: int | None = None


class NightlyArgs(BaseModel):
    since: datetime | None = None  # default: 24 h before ``until``
    until: datetime | None = None  # default: the workflow's start time (the schedule's fire time)


class NightlyWindow(BaseModel):
    since: datetime
    until: datetime


class LabelsOutcome(BaseModel):
    since: datetime
    until: datetime
    n_events: int = 0
    n_labels: int = 0
    n_calls: int = 0
    labels_uri: str | None = None


class CalibratorOutcome(BaseModel):
    version: str | None = None
    promoted: bool = False
    n_labels: int = 0
    metrics: dict[str, Any] = {}
    artifact_uri: str | None = None
    skipped_reason: str | None = None


class ConformalOutcome(BaseModel):
    calibrator_version: str | None = None
    alpha: float = 0.1
    tau: dict[str, float] = {}  # family (or "*") → threshold
    n_calib_calls: int = 0
    auto_accept_enabled: bool = False
    skipped_reason: str | None = None


class LexiconFitOutcome(BaseModel):
    n_pairs: int = 0
    keys_fitted: list[str] = []
    pull_request: str | None = None
    proposal_uri: str | None = None


class DatasetSyncOutcome(BaseModel):
    dataset: str
    inserted: int = 0
    skipped_reason: str | None = None


class NightlyResult(BaseModel):
    window: NightlyWindow
    labels: LabelsOutcome | None = None
    calibrator: CalibratorOutcome | None = None
    conformal: ConformalOutcome | None = None
    lexicon: LexiconFitOutcome | None = None
    errors_dataset: DatasetSyncOutcome | None = None
    gold_candidates: DatasetSyncOutcome | None = None
    failures: dict[str, str] = Field(default_factory=dict)


class ModelSwapArgs(BaseModel):
    """Monthly model-swap run (§15.4): the gold suite with a candidate pinned for one role."""

    candidates: dict[str, str] = {}  # role → candidate model id; empty = the configured candidate list


class ModelSwapOutcome(BaseModel):
    results: list[dict[str, Any]] = []
    pull_requests: list[str] = []
