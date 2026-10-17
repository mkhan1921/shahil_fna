"""Workflow and activity payloads (spec §13.1): ids, version pins and small outcomes only.

Every activity reads and writes Postgres or object storage; what crosses the workflow boundary
is a run id, the call's ``CallArgs`` and a handful of counts. Activities are named by string so
workflow code never imports activity implementations (and their I/O libraries).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..models.runs import CallArgs, FollowupAnswer, ReviewSubmission, RunState
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
EXPORT_FINETUNE_DATA = "export_finetune_data"
SFT_TRAIN_EXTRACTOR = "sft_train_extractor"
EVALUATE_EXTRACTOR = "evaluate_extractor_candidate"
PROMOTE_EXTRACTOR = "promote_extractor_groups"
CHECK_RL_START = "check_rl_start_conditions"
EXPORT_RL_PROMPTS = "export_rl_prompts"
GRPO_TRAIN_EXTRACTOR = "grpo_train_extractor"
RL_HACKING_CHECKS = "run_rl_hacking_checks"

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
NIGHTLY_ACTIVITIES = (
    BUILD_LABELS,
    RETRAIN_CALIBRATOR,
    REFIT_CONFORMAL,
    PROPOSE_LEXICON_FIT,
    SYNC_ERROR_DATASET,
    SYNC_GOLD_CANDIDATES,
    RUN_MODEL_SWAP,
)
TRAINING_ACTIVITIES = (
    EXPORT_FINETUNE_DATA,
    SFT_TRAIN_EXTRACTOR,
    EVALUATE_EXTRACTOR,
    PROMOTE_EXTRACTOR,
    CHECK_RL_START,
    EXPORT_RL_PROMPTS,
    GRPO_TRAIN_EXTRACTOR,
    RL_HACKING_CHECKS,
)

# Workflow type names (stable across code moves: they are what the server and histories record).
CALL_PIPELINE = "CallPipelineWorkflow"
FOLLOWUP_INTAKE = "FollowupIntakeWorkflow"
FORECAST_ONLY = "ForecastWorkflow"
NIGHTLY_FEEDBACK = "NightlyFeedbackWorkflow"
MODEL_SWAP = "ModelSwapWorkflow"
FINETUNE_EXTRACTOR = "FinetuneExtractorWorkflow"
TRAIN_RL_EXTRACTOR = "TrainRlExtractorWorkflow"

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
    skipped: bool = False  # VOI is not installed in this worker: the agent's follow-ups and flags stand


class ForecastOutcome(BaseModel):
    run_id: str
    profile_version: int
    mode: Literal["preview", "final"]
    pos: float | None = None
    cached: bool = False
    skipped: bool = False  # the forecast engine is not installed in this worker


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


NotifyKind = Literal["review_ready", "review_reminder", "manual_review"]


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
    n_recall_misses: int = 0  # facts reviewers added: logged, not labels (§9.3)
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
    thresholds_uri: str | None = None  # the full thresholds (per family, pooled, counts) the run-time check reads
    skipped_reason: str | None = None


class LexiconFitOutcome(BaseModel):
    n_pairs: int = 0
    keys_fitted: list[str] = []  # "section: key" entries the proposal re-fits
    base_version: str | None = None
    new_version: str | None = None
    proposal_uri: str | None = None  # the proposed lexicon YAML
    diff_uri: str | None = None
    pull_request: str | None = None


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


# --------------------------------------------------------------------------- fine-tuning (§15.8) and RL (§15.9)


class FinetuneArgs(BaseModel):
    """``FinetuneExtractorWorkflow`` (§15.8): export reviewed calls, SFT, the gold experiment, per-group promotion."""

    label: str = ""  # names the run's artifacts under training/runs/<label>; empty = the workflow id
    groups: list[str] = []  # slot groups to train and evaluate; empty = every group with examples
    base_model: str = ""  # the Tinker base model; empty = ADVISOR_INTEL_FINETUNE_BASE_MODEL
    epochs: int = 1
    min_reviewed_calls: int = 300  # the §15.8 trigger …
    min_group_facts: int = 2000  # … or earlier for a slot group with this many labelled facts
    force: bool = False  # an operator's run below the trigger (recorded as such)
    heldout_ids: list[str] = []  # extra calls to keep out of training, besides the gold dataset's held-out calls


class TrainingExportOutcome(BaseModel):
    label: str
    eligible: bool = False
    reason: str = ""
    groups: list[str] = []
    n_reviewed_calls: int = 0
    n_clients: int = 0
    examples: int = 0
    by_split: dict[str, int] = {}
    by_group: dict[str, int] = {}
    labelled_facts_by_group: dict[str, int] = {}
    skipped_heldout_calls: int = 0
    skipped_disputed_tasks: int = 0
    skipped_unreviewed_tasks: int = 0
    gold_examples: int = 0
    heldout_ids: list[str] = []  # what was kept out: the gold dataset's held-out calls and the args' extras
    prompt_versions: dict[str, str] = {}  # the prompt versions frozen into the training prompts (§15.8, order with GEPA)
    data_uri: str | None = None  # the examples in object storage (chat JSONL, one file per split)
    cached: bool = False


class SftInput(BaseModel):
    args: FinetuneArgs
    export: TrainingExportOutcome


class SftOutcome(BaseModel):
    label: str
    base_model: str
    checkpoint: str  # the saved LoRA weights' name at the training service
    model: str  # the model id the extractor routes to once the weights are served
    steps: int = 0
    examples: int = 0
    final_loss: float | None = None
    metrics_uri: str | None = None
    cached: bool = False


class CandidateEvalInput(BaseModel):
    """The gold experiment of one candidate extractor against its incumbent, decided per slot group."""

    label: str
    candidate_model: str
    groups: list[str] = []
    incumbent_model: str | None = None  # None: the pinned extractor; RL: the fine-tuned model it started from
    heldout_only: bool = False  # RL: the held-out gold calls only (§15.9)
    require_better: bool = False  # RL: must beat the incumbent on at least one metric, not only match it
    hacking_passed: bool | None = None  # RL: the reward-hacking checks (None for SFT)
    human_signed_off: bool | None = None  # RL: a person read the sampled outputs (None for SFT)


class GroupDecision(BaseModel):
    group: str
    switch: bool
    reasons: list[str] = []
    metrics: dict[str, list[float | None]] = {}  # metric → [incumbent, candidate]


class CandidateEvalOutcome(BaseModel):
    label: str
    candidate_model: str
    incumbent_model: str
    groups: list[str] = []
    decisions: list[GroupDecision] = []
    switched: list[str] = []
    cost_incumbent: float | None = None
    cost_candidate: float | None = None
    gold_calls: int = 0
    candidate_report_uri: str | None = None
    incumbent_report_uri: str | None = None
    cached: bool = False


class PromotionInput(BaseModel):
    label: str
    kind: Literal["sft", "rl"]
    evaluation: CandidateEvalOutcome
    checkpoint: str = ""
    base_model: str = ""
    prompt_versions: dict[str, str] = {}


class PromotionOutcome(BaseModel):
    label: str
    model: str
    switched: list[str] = []
    routing_uri: str | None = None  # the routing change (routing.yaml with the groups switched), for the pull request
    pull_request: str | None = None  # opened when GitHub is configured; never merged by the workflow
    registry_uri: str | None = None  # the recorded versions of this run
    calibrator_retrain_needed: list[str] = []  # the switched groups: their calibrator is retrained after the switch
    cached: bool = False


class FinetuneResult(BaseModel):
    export: TrainingExportOutcome
    sft: SftOutcome | None = None
    evaluation: CandidateEvalOutcome | None = None
    promotion: PromotionOutcome | None = None
    skipped_reason: str | None = None


class RlArgs(BaseModel):
    """``TrainRlExtractorWorkflow`` (§15.9): GRPO on the fine-tuned extractor, only when the start conditions hold."""

    label: str = ""
    groups: list[str] = []  # empty = the groups the fine-tuned extractor is live for
    reference_model: str = ""  # the M6 model to start from and to beat; empty = the live fine-tuned model
    reference_checkpoint: str = ""  # its LoRA weights at the training service; empty = the registry's record
    base_model: str = ""
    config: dict[str, Any] = {}  # GRPOConfig fields (group_size, kl_coef, steps, …)
    min_reviewed_calls: int = 300
    plateau_rounds: int = 2  # retraining rounds without improvement before RL starts
    min_targetable_share: float = 0.5  # share of reviewer corrections a reward can target
    n_review_samples: int = 50
    human_signed_off: bool | None = None  # True once a person has read the sampled outputs
    force: bool = False  # run although a start condition fails (recorded)
    heldout_ids: list[str] = []


class RlStartOutcome(BaseModel):
    label: str
    can_start: bool = False
    forced: bool = False
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    live_groups: list[str] = []
    reference_model: str | None = None
    reference_checkpoint: str | None = None
    n_reviewed_calls: int = 0
    targetable_share: float | None = None


class RlExportInput(BaseModel):
    args: RlArgs
    start: RlStartOutcome


class RlExportOutcome(BaseModel):
    label: str
    n_prompts: int = 0
    by_group: dict[str, int] = {}
    n_reviewed_calls: int = 0
    skipped_heldout_calls: int = 0
    heldout_ids: list[str] = []
    prompts_uri: str | None = None
    cached: bool = False


class GrpoInput(BaseModel):
    args: RlArgs
    start: RlStartOutcome
    export: RlExportOutcome


class GrpoOutcome(BaseModel):
    label: str
    base_model: str
    reference_checkpoint: str
    checkpoint: str
    model: str
    steps: int = 0
    first_mean_reward: float | None = None
    last_mean_reward: float | None = None
    metrics_uri: str | None = None
    cached: bool = False


class HackingInput(BaseModel):
    args: RlArgs
    export: RlExportOutcome
    training: GrpoOutcome


class HackingOutcome(BaseModel):
    label: str
    passed: bool = False
    failures: list[str] = []
    not_discussed: dict[str, list[float | None]] = {}  # group → [candidate, reference]
    flagged_samples: int = 0
    review_sheet_uri: str | None = None
    human_signed_off: bool | None = None
    cached: bool = False


class RlResult(BaseModel):
    start: RlStartOutcome
    export: RlExportOutcome | None = None
    training: GrpoOutcome | None = None
    hacking: HackingOutcome | None = None
    evaluation: CandidateEvalOutcome | None = None
    promotion: PromotionOutcome | None = None
    skipped_reason: str | None = None
