"""Request and response bodies of the external and review APIs (spec §11.5, §14.3)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..models.crm import CrmRecord
from ..models.facts import Fact
from ..models.fna import ExportFile
from ..models.profile import BlockingFlag, StateOp
from ..models.review import FlagClearance, FollowupEdit, TermConfirmation
from ..models.runs import RunState, Stage, VersionPins
from ..models.transcript import QualityReport, SpeakerRole
from ..workflows.contracts import RunStatus

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def check_id(v: str, what: str = "id") -> str:
    if not _ID.match(v):
        raise ValueError(f"{what} must be 1–128 letters, digits, '.', '_' or '-'")
    return v


class CallCreate(BaseModel):
    """Metadata for ``POST /calls``: the ``metadata`` form field with an upload, or the JSON body with ``audio_uri``
    or a ``script`` (a scripted call for synthetic and demo runs: ``speaker[@channel]: text`` lines, no audio)."""

    call_id: str | None = None
    advisor_id: str = ""
    advisor_email: str | None = None
    client_ids: list[str] = []
    client_name: str | None = None
    started_at: datetime | None = None
    audio_uri: str | None = None
    script: str | None = Field(default=None, max_length=1_000_000)
    script_roles: dict[str, SpeakerRole] = {}  # the script's speakers → roles; empty: roles are inferred
    advisor_channel: int | None = Field(default=None, ge=0, le=1)  # stereo: the channel the recorder puts the adviser on
    shared_client_channel: bool | None = None  # two clients on one channel (default: two or more client ids, §5.2)
    versions: dict[str, Any] = {}  # overrides of the deployment's version pins
    review_reminder_seconds: int | None = Field(default=None, ge=1)
    budget_usd: float | None = Field(default=None, gt=0)
    crm: CrmRecord | None = None

    @field_validator("call_id")
    @classmethod
    def _call_id(cls, v: str | None) -> str | None:
        return None if v is None else check_id(v, "call_id")

    @field_validator("client_ids")
    @classmethod
    def _client_ids(cls, v: list[str]) -> list[str]:
        return [check_id(c, "client id") for c in v]


class CallCreated(BaseModel):
    call_id: str
    workflow_id: str
    run_id: str


class CallView(BaseModel):
    """``GET /calls/{call_id}``: run state (Temporal query), quality report, versions."""

    call_id: str
    run_id: str | None
    state: RunState | None = None
    workflow_status: str | None = None
    status: RunStatus | None = None
    quality: QualityReport | None = None
    versions: VersionPins | None = None
    profile_version: int | None = None
    open_flags: int = 0
    runs: list[str] = []


class RerunRequest(BaseModel):
    from_stage: Stage | Literal["ingest", "discourse", "agent", "voi", "forecast", "review"]
    versions: dict[str, Any] = {}
    mode: Literal["new", "reset"] = "new"
    review_reminder_seconds: int | None = Field(default=None, ge=1)

    def stage(self) -> Stage:
        return self.from_stage if isinstance(self.from_stage, Stage) else Stage[self.from_stage.upper()]


class RerunStarted(BaseModel):
    call_id: str
    workflow_id: str
    mode: Literal["new", "reset"]
    start_stage: Stage
    execution_run_id: str | None = None
    source_run_id: str | None = None


class ReviewActions(BaseModel):
    """``POST /calls/{call_id}/ops`` body: reviewer ops plus the side records of §11.2 (a bare op list also works)."""

    reviewer_id: str | None = None
    ops: list[StateOp] = []
    flag_clearances: list[FlagClearance] = []
    followup_edits: list[FollowupEdit] = []
    term_confirmations: list[TermConfirmation] = []


class RejectedOpView(BaseModel):
    seq: int  # the seq the client gave the op
    op: StateOp
    reason: str


class OpsApplied(BaseModel):
    run_id: str
    profile_version: int
    applied: list[int]  # seq of each applied op
    rejected: list[RejectedOpView] = []
    facts: dict[str, Fact | None] = {}  # every touched fact as it now stands (None: removed)
    open_flags: list[BlockingFlag] = []
    unknown_flags: list[str] = []
    review_events: int = 0


class SubmitRequest(BaseModel):
    reviewer_id: str
    spot_check: bool | None = None
    review_seconds: float | None = None


class Submitted(BaseModel):
    call_id: str
    run_id: str
    profile_version: int
    submitted_at: datetime


class FnaExport(BaseModel):
    """``GET /calls/{call_id}/fna``: the reviewed document as a ``shahil-fna`` file and its provenance sidecar."""

    file: ExportFile
    sidecar: dict[str, Any]


class AnswerRequest(BaseModel):
    text: str = Field(min_length=1)
    source: Literal["followup", "advisor_note", "client_email"] = "followup"
    recorded_by: str = ""


class AnswerRouted(BaseModel):
    call_id: str
    run_id: str
    followup_id: str
    routed: Literal["signal", "intake_workflow"]
    workflow_id: str
    duplicate: bool = False  # the same answer was already recorded: nothing was signalled or started


class DocumentSeeded(BaseModel):
    client_id: str
    document_id: int
    schema_version: int
