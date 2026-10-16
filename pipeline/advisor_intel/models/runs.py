"""Run-level contracts shared by Temporal workflows, activities and the API (spec §2.1, §13.1).

Workflow payloads hold ids and version pins only; activities read and write Postgres and
object storage (§13.1), so a 90-minute call never bloats the workflow history.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum, IntEnum
from typing import Literal

from pydantic import BaseModel, Field

from .profile import StateOp


class Stage(IntEnum):
    INGEST = 1
    DISCOURSE = 2
    AGENT = 3
    VOI = 4
    FORECAST = 5
    REVIEW = 6


class RunState(str, Enum):
    STARTED = "STARTED"
    INGESTED = "INGESTED"
    DISCOURSE_DONE = "DISCOURSE_DONE"
    AGENT_RUNNING = "AGENT_RUNNING"
    AGENT_DONE = "AGENT_DONE"
    AGENT_PARTIAL = "AGENT_PARTIAL"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    REVIEWED = "REVIEWED"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    MANUAL = "MANUAL"  # routed to manual handling (e.g. speakers outside 2–4)


class VersionPins(BaseModel):
    """Everything that changes what a run produces. A change to any pin is a new version (§13.8)."""

    schema_version: str = "profile_v1"
    lexicon_version: str = "1.3"
    grammar_version: str = "1"
    calibrator_version: str = "cold-start"
    asr_provider: str = "fake"
    asr_model_version: str = "fake-1"
    diarization_model: str = "precision-3"
    frontier_model: str = "test"
    reasoning_model: str = "test"  # extraction (thinking on)
    verifier_model: str = "test"  # a different family from the extractor (§9.1e)
    small_model: str = "test"
    embedding_model: str = "hashed-256"
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    forecast_config_version: str = "config_v1"
    cma_version: str = "2026Q4"
    fna_tables_version: str = "za_2026_27"

    def key(self) -> str:
        """A stable cache key for stage outputs (idempotency on (call_id, stage, versions))."""
        import hashlib

        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:16]


class CallArgs(BaseModel):
    call_id: str
    advisor_id: str = ""
    client_ids: list[str] = []
    start_stage: Stage = Stage.INGEST
    versions: VersionPins = Field(default_factory=VersionPins)
    forecast_mode: Literal["preview", "final"] = "preview"
    review_reminder_seconds: int = 3 * 24 * 3600
    budget_usd: float | None = None

    def preview(self) -> "CallArgs":
        return self.model_copy(update={"forecast_mode": "preview"})

    def final(self) -> "CallArgs":
        return self.model_copy(update={"forecast_mode": "final"})


class AgentOutcome(BaseModel):
    partial: bool = False
    reason: str | None = None
    turns: int = 0
    unfinished_groups: list[str] = []
    usage: dict[str, float] = {}


class ReviewSubmission(BaseModel):
    call_id: str
    reviewer_id: str
    profile_version: int
    submitted_at: datetime
    spot_check: bool = False  # this review also showed every auto-accepted fact (§11.3)
    ops: list[StateOp] = []  # normally already stored through POST /ops; kept for file-exchange reviews


class FollowupAnswer(BaseModel):
    call_id: str
    followup_id: str
    text: str
    source: Literal["followup", "advisor_note", "client_email"] = "followup"
    recorded_by: str = ""
    answered_at: datetime


class CallResult(BaseModel):
    call_id: str
    run_id: str
    profile_version: int
    pos: float | None = None
    partial: bool = False
    state: RunState = RunState.COMPLETE
