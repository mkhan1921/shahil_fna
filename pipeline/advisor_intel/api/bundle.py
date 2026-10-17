"""Building the review bundle from storage (spec §11.1, §11.5 ``GET /calls/{call_id}/review``)."""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from typing import Any

from .. import binding
from ..activities.keys import ingest_key
from ..activities.pipeline import dump
from ..activities.stages import StageFunctions
from ..config import Settings
from ..models.fna import FnaDocument
from ..models.forecast import ForecastResult
from ..models.profile import Profile
from ..models.review import ReviewBundle
from ..models.runs import RunState
from ..models.transcript import Transcript
from ..schema.slots import load_catalog
from ..storage.objects import ObjectStore
from ..storage.records import CallRecord, RunRecord
from ..storage.store import Store
from ..voi.intake import answer_transcript, append_answer

log = logging.getLogger(__name__)


class NotReady(LookupError):
    """The run has not produced what the review needs yet."""


def spot_check(run_id: str, rate: float) -> bool:
    """1 review in 10 (``rate``) also shows every auto-accepted fact (§11.3); stable per run."""
    return int(hashlib.sha256(f"spot:{run_id}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < rate


def attribution_text(f: ForecastResult | None) -> str | None:
    """'Of the uncertainty in this plan, about X% is markets, …' (§12.5)."""
    if f is None or not f.variance_attribution:
        return None
    va = f.variance_attribution
    parts = []
    for key, label in (
        ("market", "markets"),
        ("client_content", "the client's own uncertainty"),
        ("extraction", "what may have been misheard or misread"),
    ):
        share = va.get(key)
        if share is not None and share >= 0.005:
            parts.append(f"{share:.0%} is {label}")
    if not parts:
        return None
    listed = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
    return f"Of the uncertainty in this plan, about {listed}."


async def prior_document(store: Store, call: CallRecord) -> FnaDocument | None:
    doc_id = call.meta.get("prior_document_id")
    if doc_id:
        return await store.client_document(int(doc_id))
    if "prior_document_id" in call.meta or not call.primary_client_id:
        return None
    return await store.latest_client_document(call.primary_client_id)


async def transcript_for(store: Store, call_id: str, run: RunRecord) -> Transcript | None:
    return await store.load_transcript(call_id, ingest_key(run.versions)) or await store.load_transcript(call_id)


def analyse_or_none(stages: StageFunctions, doc: FnaDocument, today: date) -> dict[str, Any] | None:
    """The FNA engine's analysis of the median document as of the call date (§12.7), when the engine is installed."""
    if not stages.available("fna_analyse"):
        return None
    try:
        return dump(stages.get("fna_analyse")(doc, today=today))
    except Exception as e:  # noqa: BLE001 - any engine failure is logged; the overlay works without it and the app runs analyse() itself
        log.warning("FNA analysis failed for the review bundle: %s", e)
        return None


async def build_bundle(
    *,
    store: Store,
    objects: ObjectStore,
    settings: Settings,
    stages: StageFunctions,
    call: CallRecord,
    run: RunRecord,
    profile: Profile,
    state: RunState | None,
    audio_fallback_url: str | None,
) -> ReviewBundle:
    transcript = await transcript_for(store, call.call_id, run)
    if transcript is None:
        raise NotReady(f"call {call.call_id} has no transcript yet")
    answered = [r for r in await store.list_followups(run.workflow_id) if r.answer is not None]
    for r in answered:  # follow-up answers are evidence too (§10.6): their segments follow the call's end
        assert r.answer is not None
        answer = answer_transcript(r.answer, r.followup, call_id=call.call_id, anchor_date=transcript.anchor_date)
        transcript = append_answer(transcript, answer)
    prior = await prior_document(store, call)
    catalog = load_catalog(profile.schema_version)
    median = binding.materialise(profile, "median", base=prior, catalog=catalog)
    forecast = await store.get_forecast(run.workflow_id, mode="preview")
    preview = ForecastResult.model_validate(forecast.result) if forecast else None
    audio_url = None
    if call.audio_uri:
        audio_url = await objects.url(call.audio_uri) or audio_fallback_url
    return ReviewBundle(
        call_id=call.call_id,
        run_id=run.workflow_id,
        profile_version=profile.version,
        state=state or RunState.AWAITING_REVIEW,
        median_document=median,
        prior_document=prior,
        sidecar=binding.sidecar(profile, catalog=catalog),
        transcript=transcript,
        audio_url=audio_url,
        entities=profile.entities,
        followups=profile.open_questions,
        blocking_flags=profile.blocking_flags,
        preview_forecast=preview,
        deterministic_analysis=analyse_or_none(stages, median, profile.anchor_date),
        quality=transcript.quality,
        spot_check=spot_check(run.workflow_id, settings.spot_check_rate),
        versions=run.versions,
        attribution_text=attribution_text(preview),
    )
