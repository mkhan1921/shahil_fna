"""The FastAPI backend (spec §11.5, §14.3): calls, runs, review, follow-ups and the FNA file exchange.

=======================================  ==============================================================
``POST /calls``                          upload (multipart ``audio`` + ``metadata``) or JSON with ``audio_uri`` or a
                                         scripted call (``script``) → ``{call_id, workflow_id}``; starts ``CallPipelineWorkflow``
``GET  /calls/{id}``                     run state (Temporal query), quality report, versions
``GET  /calls/{id}/profile``             the profile (latest fact versions)
``GET  /calls/{id}/forecast``            the latest ``ForecastResult``
``POST /calls/{id}/forecast``            forecast only with new pins (``ForecastWorkflow``, §2.1)
``POST /calls/{id}/rerun``               ``{from_stage, versions?, mode}`` → a new run from that stage, or a reset
``GET  /calls/{id}/review``              the ``ReviewBundle`` for the overlay
``POST /calls/{id}/ops``                 ``[StateOp]`` reviewer ops (or with flag clearances, follow-up edits, term
                                         confirmations); an op sent again is applied once
``POST /calls/{id}/submit``              ``{reviewer_id}`` or the app's ``ReviewResult``; refused while blocking flags
                                         are open; freezes the version; signals; a repeat gets the first answer
``GET  /calls/{id}/fna``                 ``{file, sidecar}``: the reviewed ``shahil-fna`` file and its provenance sidecar
``POST /calls/{id}/followups/{fid}/answer``  signal, or ``FollowupIntakeWorkflow`` once the run has completed
``POST /clients/{id}/fna``               a plain ``shahil-fna`` export seeds the client's prior
``GET  /calls/{id}/review-package``      the review as a file (file exchange, §11.5)
``POST /calls/{id}/review-result``       the reviewed result as a file
``GET  /calls/{id}/audio``, ``/note``    the recording (local stores) and the meeting note
``POST /calls/{id}/note``                bring the note up to date with edited facts (§11.6)
=======================================  ==============================================================

Every endpoint but ``/healthz`` needs ``Authorization: Bearer <Settings.api_token>``; without a token
configured the API is open only in local and test environments. CORS admits the FNA app's origin
(``ADVISOR_INTEL_FNA_APP_ORIGIN``). Storage and Temporal are injected, so tests run against fakes.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import TypeAdapter, ValidationError
from starlette.datastructures import UploadFile
from temporalio.service import RPCError, RPCStatusCode

from .. import binding
from ..activities.keys import ingest_key
from ..activities.stages import StageFunctions
from ..config import Settings, get_settings
from ..models.fna import ExportFile
from ..models.forecast import ForecastResult
from ..models.profile import Profile, StateOp
from ..models.review import ReviewPackage, ReviewResult
from ..models.runs import CallArgs, FollowupAnswer, ReviewSubmission, RunState, Stage, VersionPins
from ..note import MeetingNote, apply_note, generate_note, update_note
from ..review_actions import apply_flag_clearances, apply_reviewer_ops, followup_edit_events, glossary_entry, term_confirmation_events
from ..storage.glossary import PostgresGlossaryStore
from ..storage.objects import LocalObjectStore, ObjectStore, open_object_store, safe_segment
from ..storage.records import CallRecord, RunRecord
from ..storage.store import Store
from ..telemetry import instrument_fastapi, run_context
from ..workflows.contracts import FollowupIntakeArgs, ForecastArgs
from .bundle import NotReady, build_bundle, prior_document, spot_check
from .schemas import (
    AnswerRequest,
    AnswerRouted,
    CallCreate,
    CallCreated,
    CallView,
    DocumentSeeded,
    FnaExport,
    OpsApplied,
    RejectedOpView,
    RerunRequest,
    RerunStarted,
    ReviewActions,
    SubmitRequest,
    Submitted,
)
from .temporal import ClientGateway, ResetRefused, TemporalGateway

log = logging.getLogger(__name__)

OPEN_ENVS = ("local", "test", "dev", "development")


def max_upload_bytes() -> int:
    """The largest recording ``POST /calls`` accepts (``ADVISOR_INTEL_MAX_UPLOAD_MB``, default 2048: a 90-minute stereo WAV is ~1 GB)."""
    return int(os.environ.get("ADVISOR_INTEL_MAX_UPLOAD_MB", "2048")) * 2**20


_OPS_BODY = TypeAdapter(list[StateOp] | ReviewActions)


@dataclass
class ApiDeps:
    settings: Settings
    store: Store
    objects: ObjectStore
    temporal: TemporalGateway
    stages: StageFunctions
    pins: Callable[[], Awaitable[VersionPins]]


def _now() -> datetime:
    return datetime.now(UTC)


async def deployment_pins(settings: Settings, store: Store | None = None) -> VersionPins:
    """The version pins a new call runs with: the deployment's model routing and ASR provider
    (``advisor_intel.pipeline.deployment_pins``) and the promoted calibrator.

    ``ADVISOR_INTEL_PINS=test`` (or ``env=test``) keeps the offline test models;
    ``ADVISOR_INTEL_ASR_MODEL_VERSION`` and ``ADVISOR_INTEL_LEXICON_VERSION`` override those pins.
    """
    if settings.env == "test" or os.environ.get("ADVISOR_INTEL_PINS") == "test":
        return VersionPins()
    from ..pipeline import deployment_pins as routing_pins

    base = routing_pins(settings)
    calibrator = (await store.current_calibrator_version()) if store is not None else None
    return base.model_copy(
        update={
            "asr_model_version": os.environ.get("ADVISOR_INTEL_ASR_MODEL_VERSION", base.asr_model_version),
            "lexicon_version": os.environ.get("ADVISOR_INTEL_LEXICON_VERSION", base.lexicon_version),
            "calibrator_version": calibrator or base.calibrator_version,
        }
    )


def _apply_overrides(pins: VersionPins, overrides: dict[str, Any]) -> VersionPins:
    unknown = set(overrides) - set(VersionPins.model_fields)
    if unknown:
        raise HTTPException(422, f"unknown version pins: {sorted(unknown)}")
    try:
        return VersionPins.model_validate({**pins.model_dump(), **overrides})
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e


def new_run_id(call_id: str) -> str:
    return f"{call_id}-run-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- auth


def require_token(request: Request) -> None:
    settings: Settings = request.app.state.deps.settings
    token = settings.api_token
    if not token:
        if settings.env in OPEN_ENVS:
            return
        raise HTTPException(503, "the API token is not configured (ADVISOR_INTEL_API_TOKEN)")
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip().encode(), token.encode()):
        raise HTTPException(401, "a valid bearer token is required", headers={"WWW-Authenticate": "Bearer"})


def deps(request: Request) -> ApiDeps:
    return request.app.state.deps


def reviewer_of(request: Request, given: str | None = None) -> str:
    return given or request.headers.get("x-reviewer-id") or "reviewer"


# --------------------------------------------------------------------------- lookups


async def _call(d: ApiDeps, call_id: str) -> CallRecord:
    call = await d.store.get_call(call_id)
    if call is None:
        raise HTTPException(404, f"no call {call_id}")
    return call


async def _run(d: ApiDeps, call: CallRecord, run_id: str | None = None) -> RunRecord:
    rid = run_id or call.workflow_id
    run = await d.store.get_run(rid) if rid else await d.store.latest_run(call.call_id)
    if run is None or run.call_id != call.call_id:
        raise HTTPException(404, f"call {call.call_id} has no run {run_id or ''}".strip())
    return run


async def _profile(d: ApiDeps, run: RunRecord, version: int | None = None) -> Profile:
    profile = await d.store.load_profile(run.workflow_id, version)
    if profile is None:
        raise HTTPException(409, f"run {run.workflow_id} has no profile yet" + (f" (version {version})" if version is not None else ""))
    return profile


async def _submitted(d: ApiDeps, run: RunRecord) -> bool:
    return any(v.frozen for v in await d.store.profile_versions(run.workflow_id))


# --------------------------------------------------------------------------- review actions (shared by /ops and /review-result)


async def apply_actions(d: ApiDeps, run: RunRecord, actions: ReviewActions, reviewer_id: str) -> OpsApplied:
    if await _submitted(d, run):
        raise HTTPException(409, "the review was already submitted; reviewer ops are closed")
    at = _now()

    def fn(profile: Profile) -> tuple[Profile, Any]:
        result, events = apply_reviewer_ops(profile, actions.ops, reviewer_id=reviewer_id, at=at)
        cleared, flag_events, unknown = apply_flag_clearances(result.profile, actions.flag_clearances)
        return cleared, (result, events + flag_events, unknown)

    try:
        # The ops and the review events describing them are written in one transaction.
        profile, (result, events, unknown) = await d.store.update_profile(run.workflow_id, fn, review_events=lambda x: x[1])
    except LookupError as e:
        raise HTTPException(409, str(e)) from e
    for edit in actions.followup_edits:
        await d.store.apply_followup_edit(run.workflow_id, edit)
    side = followup_edit_events(run.workflow_id, actions.followup_edits) + term_confirmation_events(
        run.workflow_id, actions.term_confirmations
    )
    if actions.term_confirmations:
        glossary = PostgresGlossaryStore(d.store.db)
        for c in actions.term_confirmations:
            await glossary.put(glossary_entry(c))
    n = len(events) + await d.store.record_review_events(side)
    return OpsApplied(
        run_id=run.workflow_id,
        profile_version=profile.version,
        applied=[op.seq for op in result.applied],
        rejected=[RejectedOpView(seq=int(r.op.payload.get("client_seq", r.op.seq)), op=r.op, reason=r.reason) for r in result.rejected],
        facts={fid: profile.facts.get(fid) for fid in result.touched},
        open_flags=profile.open_flags(),
        unknown_flags=unknown,
        review_events=n,
    )


async def _earlier_submission(d: ApiDeps, call: CallRecord, run: RunRecord, reviewer_id: str) -> Submitted:
    """A submission sent again (its reply was lost): the same answer, when the same reviewer sent it."""
    done = [r for r in await d.store.review_results(run.workflow_id) if r.submitted]
    record = ReviewResult.model_validate(done[-1].result) if done else None  # the record submit_review wrote last
    if record is None or record.reviewer_id != reviewer_id or record.submitted_at is None:
        raise HTTPException(409, "the review was already submitted")
    frozen = [v.version for v in await d.store.profile_versions(run.workflow_id) if v.frozen]
    return Submitted(call_id=call.call_id, run_id=run.workflow_id, profile_version=frozen[0], submitted_at=record.submitted_at)


async def submit_review(
    d: ApiDeps, call: CallRecord, run: RunRecord, reviewer_id: str, *, spot: bool | None, review_seconds: float | None
) -> Submitted:
    if await _submitted(d, run):
        return await _earlier_submission(d, call, run, reviewer_id)
    profile = await _profile(d, run)
    if profile.open_flags():
        raise HTTPException(
            409,
            {
                "message": "blocking flags must be cleared before the review can be submitted",
                "open_flags": [f.model_dump(mode="json") for f in profile.open_flags()],
            },
        )
    info = await d.temporal.describe(run.workflow_id)
    if info.status != "RUNNING":
        raise HTTPException(409, f"run {run.workflow_id} is {info.status.lower()}, not waiting for review")
    status = await d.temporal.run_status(run.workflow_id)
    if status is not None and status.state != RunState.AWAITING_REVIEW:
        raise HTTPException(409, f"run {run.workflow_id} is {status.state.value}, not waiting for review")
    at = _now()
    review = ReviewSubmission(
        call_id=call.call_id,
        reviewer_id=reviewer_id,
        profile_version=profile.version,
        submitted_at=at,
        spot_check=spot if spot is not None else spot_check(run.workflow_id, d.settings.spot_check_rate),
    )
    # Signal first, then freeze: if the run cannot take the signal nothing is frozen and the adviser can
    # submit again; apply_review freezes the reviewed version itself in any case.
    with run_context(call_id=call.call_id, workflow_id=run.workflow_id, stage="review", pins=run.versions):
        try:
            await d.temporal.signal_review(run.workflow_id, review)
        except RPCError as e:
            raise HTTPException(409, f"run {run.workflow_id} could not take the review: {e.message}") from e
    await d.store.freeze_profile(run.workflow_id, profile.version, frozen_by=reviewer_id)
    await d.store.save_review_result(
        run.workflow_id,
        ReviewResult(
            call_id=call.call_id,
            run_id=run.workflow_id,
            base_profile_version=profile.version,
            reviewer_id=reviewer_id,
            submitted=True,
            submitted_at=at,
            review_seconds=review_seconds,
        ),
    )
    return Submitted(call_id=call.call_id, run_id=run.workflow_id, profile_version=profile.version, submitted_at=at)


# --------------------------------------------------------------------------- routes

router = APIRouter(dependencies=[Depends(require_token)])


def _validation(e: ValidationError) -> HTTPException:
    return HTTPException(422, e.errors(include_url=False, include_context=False, include_input=False))


@router.post("/calls", status_code=201, response_model=CallCreated)
async def create_call(request: Request, d: ApiDeps = Depends(deps)) -> CallCreated:
    """Store the recording (or take an ``audio_uri`` already in the object store, or a scripted call) and start the
    call's workflow."""
    upload: UploadFile | None = None
    try:
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            limit = max_upload_bytes()
            if int(request.headers.get("content-length") or 0) > limit:
                raise HTTPException(413, f"the recording is larger than {limit // 2**20} MB")
            form = await request.form()
            meta = CallCreate.model_validate_json(str(form.get("metadata") or "{}"))
            audio = form.get("audio")
            if not isinstance(audio, UploadFile):
                raise HTTPException(422, "multipart uploads need an 'audio' file field")
            if meta.script:
                raise HTTPException(422, "send a recording or a script, not both")
            if (audio.size or 0) > limit:
                raise HTTPException(413, f"the recording is larger than {limit // 2**20} MB")
            upload = audio
        else:
            meta = CallCreate.model_validate(await request.json())
            if bool(meta.audio_uri) == bool(meta.script):
                raise HTTPException(422, "send the recording as a multipart 'audio' file, or give one of 'audio_uri' and 'script'")
    except ValidationError as e:
        raise _validation(e) from e
    call_id = meta.call_id or f"call-{_now():%Y%m%d}-{uuid.uuid4().hex[:8]}"
    if await d.store.get_call(call_id) is not None:
        raise HTTPException(409, f"call {call_id} exists; use POST /calls/{call_id}/rerun")
    script_uri: str | None = None
    if upload is not None:
        name = Path(upload.filename or "call.wav").name
        key = f"audio/{safe_segment(call_id)}/{safe_segment(name)}"
        with tempfile.NamedTemporaryFile(prefix="advisor-intel-upload-", suffix=Path(name).suffix) as tmp:
            await upload.seek(0)
            shutil.copyfileobj(upload.file, tmp)
            tmp.flush()
            audio_uri = await d.objects.put_file(key, Path(tmp.name), content_type=upload.content_type)
        content_type, filename = upload.content_type, name
    elif meta.script:
        audio_uri = content_type = filename = None
        script_uri = await d.objects.put_bytes(
            f"scripts/{safe_segment(call_id)}/call.txt", meta.script.encode(), content_type="text/plain; charset=utf-8"
        )
    else:
        assert meta.audio_uri is not None
        if not d.objects.owns(meta.audio_uri) or not await d.objects.exists(meta.audio_uri):
            raise HTTPException(422, "audio_uri must name an object in the pipeline's object store")
        audio_uri, content_type, filename = d.objects.uri_for(d.objects.key_for(meta.audio_uri)), None, Path(meta.audio_uri).name
    client = meta.client_ids[0] if meta.client_ids else None
    if meta.crm is not None and client:
        await d.store.save_crm_record(meta.crm.model_copy(update={"client_id": client}))
    prior_id = await d.store.latest_client_document_id(client) if client else None
    call_meta = {
        k: v
        for k, v in {
            "advisor_email": meta.advisor_email,
            "client_name": meta.client_name,
            "filename": filename,
            "content_type": content_type,
            "advisor_channel": meta.advisor_channel,
            "shared_client_channel": meta.shared_client_channel,
        }.items()
        if v is not None and v != ""
    }
    call_meta["prior_document_id"] = prior_id  # the prior is the document as it stood before this call (§3.6)
    if meta.script:
        call_meta.update(script_uri=script_uri, script_roles=dict(meta.script_roles))
    run_id = new_run_id(call_id)
    pins = _apply_overrides(await d.pins(), meta.versions)
    await d.store.upsert_call(
        CallRecord(
            call_id=call_id,
            advisor_id=meta.advisor_id,
            client_ids=meta.client_ids,
            started_at=meta.started_at,
            audio_uri=audio_uri,
            meta=call_meta,
        )
    )
    await d.store.register_run(run_id, call_id, pins, start_stage=Stage.INGEST)
    args = CallArgs(call_id=call_id, advisor_id=meta.advisor_id, client_ids=meta.client_ids, versions=pins, budget_usd=meta.budget_usd)
    if meta.review_reminder_seconds:
        args = args.model_copy(update={"review_reminder_seconds": meta.review_reminder_seconds})
    with run_context(call_id=call_id, workflow_id=run_id, stage="ingest", pins=pins):
        await d.temporal.start_call(args, workflow_id=run_id)
    await d.store.set_call_workflow(call_id, run_id)
    return CallCreated(call_id=call_id, workflow_id=run_id, run_id=run_id)


@router.get("/calls/{call_id}", response_model=CallView)
async def get_call(call_id: str, d: ApiDeps = Depends(deps)) -> CallView:
    call = await _call(d, call_id)
    runs = await d.store.runs_for_call(call_id)
    if not runs:
        return CallView(call_id=call_id, run_id=None)
    run = await _run(d, call)
    info = await d.temporal.describe(run.workflow_id)
    status = await d.temporal.run_status(run.workflow_id) if info.status != "NOT_FOUND" else None
    quality = await d.store.transcript_quality(call_id, ingest_key(run.versions)) or await d.store.transcript_quality(call_id)
    versions = await d.store.profile_versions(run.workflow_id)
    return CallView(
        call_id=call_id,
        run_id=run.workflow_id,
        state=status.state if status else None,
        workflow_status=info.status,
        status=status,
        quality=quality,
        versions=run.versions,
        profile_version=versions[-1].version if versions else None,
        open_flags=len(await d.store.open_flags(run.workflow_id)),
        runs=[r.workflow_id for r in runs],
    )


@router.get("/calls/{call_id}/profile")
async def get_profile(call_id: str, run_id: str | None = None, version: int | None = None, d: ApiDeps = Depends(deps)) -> JSONResponse:
    run = await _run(d, await _call(d, call_id), run_id)
    profile = await _profile(d, run, version)
    return JSONResponse(profile.model_dump(mode="json"))


@router.get("/calls/{call_id}/forecast", response_model=ForecastResult)
async def get_forecast(
    call_id: str, run_id: str | None = None, mode: str | None = Query(None, pattern="^(preview|final)$"), d: ApiDeps = Depends(deps)
) -> ForecastResult:
    run = await _run(d, await _call(d, call_id), run_id)
    rec = await d.store.get_forecast(run.workflow_id, mode=mode)
    if rec is None:
        raise HTTPException(404, f"run {run.workflow_id} has no {mode or ''} forecast yet".replace("  ", " "))
    return ForecastResult.model_validate(rec.result)


@router.post("/calls/{call_id}/forecast", status_code=202)
async def start_forecast(call_id: str, body: dict[str, Any], d: ApiDeps = Depends(deps)) -> dict[str, Any]:
    """Forecast only (§2.1): new capital market assumptions or config over a saved profile version."""
    call = await _call(d, call_id)
    run = await _run(d, call, body.get("run_id"))
    pins = _apply_overrides(run.versions, dict(body.get("versions") or {}))
    mode = body.get("mode", "final")
    if mode not in ("preview", "final"):
        raise HTTPException(422, "mode must be preview or final")
    version = body.get("profile_version")
    await _profile(d, run, version)
    args = CallArgs(call_id=call_id, advisor_id=call.advisor_id, client_ids=call.client_ids, versions=pins, forecast_mode=mode)
    wf = f"{run.workflow_id}.forecast.{uuid.uuid4().hex[:8]}"
    await d.temporal.start_forecast(ForecastArgs(run_id=run.workflow_id, args=args, profile_version=version), workflow_id=wf)
    return {"call_id": call_id, "run_id": run.workflow_id, "workflow_id": wf}


@router.post("/calls/{call_id}/rerun", status_code=202, response_model=RerunStarted)
async def rerun(call_id: str, body: RerunRequest, d: ApiDeps = Depends(deps)) -> RerunStarted:
    """Re-run from a stage (§2.1): a new workflow with ``start_stage`` reusing stored outputs, or a Temporal reset."""
    call = await _call(d, call_id)
    source = await _run(d, call)
    stage = body.stage()
    if body.mode == "reset":
        if body.versions:
            raise HTTPException(422, "a reset re-runs with the run's own pins; start a new run to change versions")
        try:
            execution = await d.temporal.reset_to_stage(source.workflow_id, stage, reason=f"re-run from {stage.name.lower()} via the API")
        except ResetRefused as e:
            raise HTTPException(409, str(e)) from e
        return RerunStarted(call_id=call_id, workflow_id=source.workflow_id, mode="reset", start_stage=stage, execution_run_id=execution)
    pins = _apply_overrides(source.versions, body.versions)
    run_id = new_run_id(call_id)
    await d.store.register_run(run_id, call_id, pins, start_stage=stage, source_run_id=source.workflow_id)
    args = CallArgs(call_id=call_id, advisor_id=call.advisor_id, client_ids=call.client_ids, start_stage=stage, versions=pins)
    if body.review_reminder_seconds:
        args = args.model_copy(update={"review_reminder_seconds": body.review_reminder_seconds})
    with run_context(call_id=call_id, workflow_id=run_id, pins=pins):
        await d.temporal.start_call(args, workflow_id=run_id)
    await d.store.set_call_workflow(call_id, run_id)
    return RerunStarted(call_id=call_id, workflow_id=run_id, mode="new", start_stage=stage, source_run_id=source.workflow_id)


async def _bundle(d: ApiDeps, request: Request, call_id: str, run_id: str | None):
    call = await _call(d, call_id)
    run = await _run(d, call, run_id)
    profile = await _profile(d, run)
    status = await d.temporal.run_status(run.workflow_id)
    try:
        return await build_bundle(
            store=d.store,
            objects=d.objects,
            settings=d.settings,
            stages=d.stages,
            call=call,
            run=run,
            profile=profile,
            state=status.state if status else None,
            audio_fallback_url=str(request.url_for("get_audio", call_id=call_id)),
        )
    except NotReady as e:
        raise HTTPException(409, str(e)) from e


@router.get("/calls/{call_id}/review")
async def get_review(call_id: str, request: Request, run_id: str | None = None, d: ApiDeps = Depends(deps)) -> JSONResponse:
    bundle = await _bundle(d, request, call_id, run_id)
    return JSONResponse(bundle.model_dump(mode="json"))


@router.get("/calls/{call_id}/review-package")
async def get_review_package(call_id: str, request: Request, run_id: str | None = None, d: ApiDeps = Depends(deps)) -> JSONResponse:
    pkg = ReviewPackage(bundle=await _bundle(d, request, call_id, run_id))
    return JSONResponse(pkg.model_dump(mode="json"), headers={"Content-Disposition": f'attachment; filename="review-{call_id}.json"'})


@router.post("/calls/{call_id}/ops", response_model=OpsApplied)
async def post_ops(call_id: str, request: Request, run_id: str | None = None, d: ApiDeps = Depends(deps)) -> OpsApplied:
    """Reviewer ops through the reducer, written immediately; returns the touched facts as they now stand."""
    try:
        body = _OPS_BODY.validate_json(await request.body())
    except ValidationError as e:
        raise _validation(e) from e
    actions = ReviewActions(ops=body) if isinstance(body, list) else body
    run = await _run(d, await _call(d, call_id), run_id)
    stamped = next((r for op in actions.ops if (r := (op.payload.get("review") or {}).get("reviewer_id"))), None)
    return await apply_actions(d, run, actions, reviewer_of(request, actions.reviewer_id or stamped))


async def take_result(d: ApiDeps, call: CallRecord, body: ReviewResult, *, submit: bool) -> tuple[OpsApplied | None, Submitted | None]:
    """A reviewer's result: its ops (each applied once, however often it is sent) and side records, then the submission."""
    if body.call_id != call.call_id:
        raise HTTPException(422, f"the result is for call {body.call_id}, not {call.call_id}")
    run = await _run(d, call, body.run_id)
    if submit and await _submitted(d, run):
        return None, await _earlier_submission(d, call, run, body.reviewer_id)
    actions = ReviewActions(
        reviewer_id=body.reviewer_id,
        ops=body.ops,
        flag_clearances=body.flag_clearances,
        followup_edits=body.followup_edits,
        term_confirmations=body.term_confirmations,
    )
    applied = await apply_actions(d, run, actions, body.reviewer_id)
    await d.store.save_review_result(run.workflow_id, body)
    submitted = await submit_review(d, call, run, body.reviewer_id, spot=None, review_seconds=body.review_seconds) if submit else None
    return applied, submitted


@router.post("/calls/{call_id}/submit", response_model=Submitted)
async def submit(call_id: str, request: Request, d: ApiDeps = Depends(deps)) -> Submitted:
    """Freeze the reviewed profile and signal the run (§11.5). The body is ``{reviewer_id}`` after the ops went
    through ``/ops``, or the app's whole ``ReviewResult``: its side records (flag clearances, follow-up edits,
    term confirmations) and any ops not sent yet are applied first. Sending a submission again is answered
    with the first one."""
    try:
        raw = json.loads(await request.body() or b"{}")
        body = ReviewResult.model_validate(raw) if isinstance(raw, dict) and "call_id" in raw else SubmitRequest.model_validate(raw)
    except ValueError as e:  # pydantic's ValidationError is a ValueError too
        raise (_validation(e) if isinstance(e, ValidationError) else HTTPException(422, "the body is not JSON")) from e
    call = await _call(d, call_id)
    if isinstance(body, ReviewResult):
        _, submitted = await take_result(d, call, body, submit=True)
        assert submitted is not None
        return submitted
    run = await _run(d, call)
    return await submit_review(d, call, run, body.reviewer_id, spot=body.spot_check, review_seconds=body.review_seconds)


@router.post("/calls/{call_id}/review-result")
async def post_review_result(call_id: str, body: ReviewResult, d: ApiDeps = Depends(deps)) -> dict[str, Any]:
    """The file exchange's return leg: the reviewed result as a file (§11.5, §17 default for M3)."""
    applied, submitted = await take_result(d, await _call(d, call_id), body, submit=body.submitted)
    return {
        "applied": applied.model_dump(mode="json") if applied else None,
        "submitted": submitted.model_dump(mode="json") if submitted else None,
    }


@router.get("/calls/{call_id}/fna")
async def get_fna(call_id: str, part: str | None = Query(None, pattern="^(fna|sidecar)$"), d: ApiDeps = Depends(deps)) -> JSONResponse:
    """The reviewed document as a ``shahil-fna`` file plus the provenance sidecar; blocked while flags are open (§10.3)."""
    call = await _call(d, call_id)
    run = await _run(d, call)
    profile = await _profile(d, run)
    if profile.open_flags():
        raise HTTPException(
            409, {"message": "export is blocked while blocking flags are open", "open_flags": [f.flag_id for f in profile.open_flags()]}
        )
    export, sidecar = binding.export_bundle(profile, base=await prior_document(d.store, call))
    note = await d.store.latest_note(run.workflow_id)
    if note is not None:
        export = export.model_copy(update={"document": apply_note(export.document, MeetingNote.model_validate(note[1]))})
    if part == "fna":
        return JSONResponse(
            export.model_dump(mode="json"), headers={"Content-Disposition": f'attachment; filename="{call_id}-reviewed.json"'}
        )
    if part == "sidecar":
        return JSONResponse(sidecar, headers={"Content-Disposition": f'attachment; filename="{call_id}-sidecar.json"'})
    return JSONResponse(FnaExport(file=export, sidecar=sidecar).model_dump(mode="json"))


@router.post("/calls/{call_id}/followups/{followup_id}/answer", response_model=AnswerRouted)
async def answer_followup(call_id: str, followup_id: str, body: AnswerRequest, d: ApiDeps = Depends(deps)) -> AnswerRouted:
    """An answer to a follow-up question (§10.6): a signal to a running call, else a ``FollowupIntakeWorkflow``."""
    call = await _call(d, call_id)
    run = await _run(d, call)
    if await d.store.get_followup(run.workflow_id, followup_id) is None:
        raise HTTPException(404, f"run {run.workflow_id} has no follow-up {followup_id}")
    answer = FollowupAnswer(
        call_id=call_id, followup_id=followup_id, text=body.text, source=body.source, recorded_by=body.recorded_by, answered_at=_now()
    )
    info = await d.temporal.describe(run.workflow_id)
    with run_context(call_id=call_id, workflow_id=run.workflow_id, stage="followup_intake", pins=run.versions):
        routed = None
        if info.status == "RUNNING":
            try:
                await d.temporal.signal_answer(run.workflow_id, answer)
                routed, wf = "signal", run.workflow_id
            except RPCError as e:
                # The run finished between describe and signal: the answer goes to the intake workflow instead.
                if e.status != RPCStatusCode.NOT_FOUND:
                    raise
                log.info("run %s completed before the answer to %s reached it; starting the intake workflow", run.workflow_id, followup_id)
        if routed is None:
            wf = f"{run.workflow_id}.followup.{safe_segment(followup_id)}.{uuid.uuid4().hex[:6]}"
            args = CallArgs(call_id=call_id, advisor_id=call.advisor_id, client_ids=call.client_ids, versions=run.versions)
            await d.temporal.start_followup_intake(FollowupIntakeArgs(run_id=run.workflow_id, args=args, answer=answer), workflow_id=wf)
            routed = "intake_workflow"
    await d.store.record_followup_answer(run.workflow_id, answer)
    return AnswerRouted(call_id=call_id, run_id=run.workflow_id, followup_id=followup_id, routed=routed, workflow_id=wf)  # type: ignore[arg-type]


@router.post("/clients/{client_id}/fna", status_code=201, response_model=DocumentSeeded)
async def seed_client_fna(client_id: str, request: Request, d: ApiDeps = Depends(deps)) -> DocumentSeeded:
    """A plain ``shahil-fna`` export from the app seeds the prior profile for the client's next call (§3.6, §11.5)."""
    try:
        data = await request.json()
    except ValueError as e:
        raise HTTPException(422, "the body must be a shahil-fna JSON file") from e
    if not isinstance(data, dict) or data.get("format") != "shahil-fna":
        raise HTTPException(422, "not a shahil-fna export file")
    if data.get("encrypted"):
        raise HTTPException(422, "encrypted shahil-fna exports cannot be read here: export a plain (unencrypted) file from the FNA app")
    try:
        export = ExportFile.model_validate(data)
    except ValidationError as e:
        raise _validation(e) from e
    doc_id = await d.store.save_client_document(client_id, export.document, source="import")
    return DocumentSeeded(client_id=client_id, document_id=doc_id, schema_version=export.document.schemaVersion)


@router.get("/calls/{call_id}/audio", name="get_audio")
async def get_audio(call_id: str, d: ApiDeps = Depends(deps)) -> Response:
    call = await _call(d, call_id)
    if not call.audio_uri:
        raise HTTPException(404, "the call has no audio")
    media = call.meta.get("content_type") or "audio/wav"
    if isinstance(d.objects, LocalObjectStore):
        return FileResponse(d.objects.root / d.objects.key_for(call.audio_uri), media_type=media)  # supports Range requests for seeking
    url = await d.objects.url(call.audio_uri)
    if url:
        return Response(status_code=307, headers={"Location": url})
    return Response(await d.objects.get_bytes(call.audio_uri), media_type=media)


@router.get("/calls/{call_id}/note")
async def get_note(call_id: str, d: ApiDeps = Depends(deps)) -> JSONResponse:
    run = await _run(d, await _call(d, call_id))
    note = await d.store.latest_note(run.workflow_id)
    if note is None:
        raise HTTPException(404, "no meeting note yet")
    m = MeetingNote.model_validate(note[1])
    return JSONResponse({**m.model_dump(mode="json"), "text": m.text(), "narrative_fields": m.narrative_fields()})


@router.post("/calls/{call_id}/note")
async def regenerate_note(call_id: str, d: ApiDeps = Depends(deps)) -> JSONResponse:
    """Bring the meeting note up to date (§11.6): only sentences citing a changed fact are rewritten; written fresh if none exists."""
    run = await _run(d, await _call(d, call_id))
    profile = await _profile(d, run)
    prev = await d.store.latest_note(run.workflow_id)
    with run_context(call_id=call_id, workflow_id=run.workflow_id, stage="note", pins=run.versions):
        if prev is not None:
            note = await update_note(MeetingNote.model_validate(prev[1]), profile, pins=run.versions)
        else:
            note = await generate_note(profile, pins=run.versions)
    await d.store.save_note(run.workflow_id, profile.version, note.model_dump(mode="json"))
    return JSONResponse({**note.model_dump(mode="json"), "text": note.text(), "narrative_fields": note.narrative_fields()})


# --------------------------------------------------------------------------- app


def create_app(
    *,
    settings: Settings | None = None,
    store: Store | None = None,
    objects: ObjectStore | None = None,
    temporal: TemporalGateway | None = None,
    stages: StageFunctions | None = None,
    pins: Callable[[], Awaitable[VersionPins]] | None = None,
) -> FastAPI:
    """The API app. Anything not injected is built from settings when the app starts."""
    settings = settings or get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        opened: list[Any] = []
        if getattr(app.state, "deps", None) is None:
            s = store
            if s is None:
                if os.environ.get("ADVISOR_INTEL_MIGRATE_ON_START", "").lower() in ("1", "true", "yes"):
                    from ..storage.migrate import migrate

                    await migrate(settings.database_url)
                s = Store.from_dsn(settings.database_url)
                opened.append(s)
            o = objects or open_object_store(settings.object_store_uri)
            t = temporal
            if t is None:
                from ..workflows.client import connect_temporal

                t = ClientGateway(await connect_temporal(settings, objects=o), settings.task_queue)
            app.state.deps = _deps(settings, s, o, t, stages, pins)
        try:
            yield
        finally:
            for s in opened:
                await s.close()

    app = FastAPI(title="Advisor Call Intelligence", version="1", lifespan=lifespan)
    app.state.deps = (
        _deps(settings, store, objects, temporal, stages, pins)
        if store is not None and objects is not None and temporal is not None
        else None
    )
    origins = [o.strip() for o in os.environ.get("ADVISOR_INTEL_FNA_APP_ORIGIN", "http://localhost:3000").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Reviewer-Id", "traceparent", "baggage"],
        expose_headers=["Content-Disposition"],
    )
    instrument_fastapi(app)

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, Any]:
        d = request.app.state.deps
        return {"ok": True, "database": await d.store.ping() if d else False}

    app.include_router(router)
    return app


def _deps(
    settings: Settings,
    store: Store | None,
    objects: ObjectStore | None,
    temporal: TemporalGateway | None,
    stages: StageFunctions | None,
    pins: Any,
) -> ApiDeps:
    assert store is not None and objects is not None and temporal is not None
    return ApiDeps(
        settings=settings,
        store=store,
        objects=objects,
        temporal=temporal,
        stages=stages or StageFunctions(),
        pins=pins or (lambda: deployment_pins(settings, store)),
    )


def main() -> None:
    """``advisor-intel-api``: serve the API with uvicorn (``ADVISOR_INTEL_API_HOST`` / ``_PORT``)."""
    import uvicorn

    from ..telemetry import setup_telemetry

    logging.basicConfig(level=os.environ.get("ADVISOR_INTEL_LOG_LEVEL", "INFO"))
    settings = get_settings()
    setup_telemetry(settings, service_name="advisor-intel-api")
    host = os.environ.get("ADVISOR_INTEL_API_HOST") or ("0.0.0.0" if settings.api_token else "127.0.0.1")
    uvicorn.run(create_app(settings=settings), host=host, port=int(os.environ.get("ADVISOR_INTEL_API_PORT", "8000")), proxy_headers=True)


if __name__ == "__main__":
    main()
