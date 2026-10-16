"""Row models returned by the ``Store`` (spec §14.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..models.profile import FollowUp
from ..models.runs import FollowupAnswer, VersionPins


class CallRecord(BaseModel):
    call_id: str
    workflow_id: str | None = None  # the latest run
    advisor_id: str = ""
    client_ids: list[str] = []
    started_at: datetime | None = None
    audio_uri: str | None = None
    meta: dict[str, Any] = {}  # advisor_email, filename, content_type, anchor_date …
    created_at: datetime | None = None

    @property
    def primary_client_id(self) -> str | None:
        return self.client_ids[0] if self.client_ids else None


class RunRecord(BaseModel):
    workflow_id: str  # the run id (§1.4)
    call_id: str
    versions: VersionPins = Field(default_factory=VersionPins)
    versions_key: str = ""
    start_stage: int = 1
    source_run_id: str | None = None
    created_at: datetime | None = None


class StageRecord(BaseModel):
    call_id: str
    stage: str
    cache_key: str
    run_id: str
    versions: dict[str, Any] = {}
    output: dict[str, Any] = {}
    created_at: datetime | None = None


class ProfileVersionRecord(BaseModel):
    workflow_id: str
    version: int
    last_seq: int
    frozen: bool = False
    frozen_at: datetime | None = None
    frozen_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FactVersion(BaseModel):
    fact_id: str
    version: int
    profile_version: int
    deleted: bool
    fact: dict[str, Any] | None
    created_at: datetime | None = None


FollowupStatus = Literal["open", "asked", "answered", "dropped"]
STATUS_RANK: dict[str, int] = {"open": 0, "asked": 1, "answered": 2, "dropped": 2}


class FollowupRecord(BaseModel):
    workflow_id: str
    followup: FollowUp
    status: FollowupStatus = "open"
    answer: FollowupAnswer | None = None
    answered_at: datetime | None = None


ReviewAction = Literal[
    "accept",
    "edit",
    "change_status",
    "reject",
    "remove",
    "reattribute",
    "add",
    "mark_unknown",
    "clear_flag",
    "edit_followup",
    "confirm_term",
    "submit",
]


class ReviewEvent(BaseModel):
    """One reviewer action (§11.2), the source of calibration labels (§9.3)."""

    id: int | None = None
    workflow_id: str
    fact_id: str | None = None
    action: ReviewAction
    reason: str | None = None
    reviewer_id: str
    at: datetime
    op_seq: int | None = None
    profile_version: int | None = None
    detail: dict[str, Any] = {}  # op, fact_before, fact_after, note, flag, edit …


class ForecastRecord(BaseModel):
    workflow_id: str
    profile_version: int
    config_version: str
    cma_version: str
    mode: str
    result: dict[str, Any]
    created_at: datetime | None = None


class CalibratorRecord(BaseModel):
    version: str
    slot_group: str = "*"  # "*" for a pooled calibrator
    artifact_uri: str | None = None
    metrics: dict[str, Any] = {}
    tau: float | None = None
    alpha: float | None = None
    n_calib_calls: int | None = None
    promoted: bool = False
    created_at: datetime | None = None


class ReviewResultRecord(BaseModel):
    id: int
    workflow_id: str
    reviewer_id: str
    base_profile_version: int
    submitted: bool
    result: dict[str, Any]
    created_at: datetime | None = None


class NotificationRecord(BaseModel):
    idem_key: str
    workflow_id: str
    kind: str
    channel: str | None = None
    recipient: str | None = None
    payload: dict[str, Any] = {}
    created_at: datetime | None = None
    sent_at: datetime | None = None
    error: str | None = None
