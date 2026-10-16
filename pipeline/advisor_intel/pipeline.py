"""One call through every stage in-process: local runs, the CLI and the eval harness (spec §14, §15.4).

``process_call`` runs stage 1 (a ready transcript, a scripted call or audio), stage 2 discourse,
the client's prior file, the orchestrated extraction and verification (stages 3–5), VOI and
follow-ups (stage 6), the preview forecast (stage 8) and the meeting note, with the same stage
code the Temporal activities call. Everything stays in memory: no database, no workflow history,
no retries; ``advisor_intel.workflows`` adds those. ``process_cached_call`` is the eval harness's
entry: stages 3–6 and the forecast on a dataset call's cached stage 1–2 output.

A stage whose package is not installed is skipped and named in ``PipelineResult.skipped``.
"""

from __future__ import annotations

import functools
import hashlib
import importlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from . import binding
from .config import Settings, get_settings
from .discourse.run import run_discourse
from .harness.deps import AgentInputs, HarnessServices
from .harness.run import run_agent
from .models.crm import CrmRecord
from .models.entities import DiscourseOutput
from .models.facts import Provenance
from .models.fna import FnaDocument
from .models.profile import BlockingFlag, FollowUp, Profile
from .models.runs import AgentOutcome, VersionPins
from .models.transcript import SpeakerRole, Transcript
from .schema.slots import load_catalog

log = logging.getLogger(__name__)


class CallInput(BaseModel):
    """One call to process. Give a ``transcript``, a scripted call (``script``) or ``audio``."""

    call_id: str
    anchor_date: date | None = None  # the call date (defaults to the transcript's, or today)
    transcript: Transcript | None = None
    script: str | None = None  # ``speaker[@channel]: text`` lines (``ingest.ScriptedCall``)
    script_roles: dict[str, SpeakerRole] = {}  # the script's speakers → roles; empty: roles are inferred
    audio: list[Path] = []
    discourse: DiscourseOutput | None = None  # a cached stage 2 output
    prior_document: FnaDocument | None = None  # the client's FNA before the call
    crm: CrmRecord | None = None
    glossary: list[str] = []  # confirmed practice terms (§8.5)


@dataclass
class PipelineResult:
    call_id: str
    run_id: str
    transcript: Transcript
    discourse: DiscourseOutput
    profile: Profile
    outcome: AgentOutcome
    followups: list[FollowUp] = field(default_factory=list)
    flags: list[BlockingFlag] = field(default_factory=list)
    forecast: Any = None  # forecast.ForecastResult (preview)
    note: Any = None  # note.MeetingNote
    analysis: dict[str, Any] | None = None  # the FNA engine's analyse() of the median document
    document: FnaDocument | None = None  # the profile materialised at its median (§3.6)
    sidecar: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, float] = field(default_factory=dict)  # cost_usd, requests, tokens, wall_clock_s
    tool_calls: list[str] = field(default_factory=list)
    summary: str = ""
    skipped: list[str] = field(default_factory=list)  # stages not installed in this deployment

    @property
    def partial(self) -> bool:
        return self.outcome.partial


def run_id_for(call_id: str, pins: VersionPins) -> str:
    """A stable run id per call and version pins (re-running the same versions is the same run)."""
    return f"run-{call_id}-{pins.key()}"


def forecast_seed(run_id: str) -> int:
    return int(hashlib.sha256(run_id.encode()).hexdigest()[:8], 16)


def _optional(module: str, attr: str) -> Any | None:
    try:
        return getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as e:
        log.info("%s.%s is not installed: %s", module, attr, e)
        return None


def crm_names(crm: CrmRecord | None, doc: FnaDocument | None = None) -> Any:
    """CRM and prior-file names as stage 1 role hints and vocabulary (§5.2 step 6, §5.4)."""
    from .ingest import CrmNames

    by_role: dict[str, list[str]] = {"advisor": [], "client_primary": [], "client_secondary": [], "others": []}
    if crm is not None:
        if crm.adviser_name:
            by_role["advisor"].append(crm.adviser_name)
        for p in crm.household:
            role = {"advisor": "advisor", "client_primary": "client_primary", "spouse": "client_secondary"}.get(p.role, "others")
            by_role[role].append(p.name)
    if doc is not None:
        by_role["client_primary"] += [n for n in (doc.client.firstName, " ".join(x for x in (doc.client.firstName, doc.client.surname) if x)) if n]
        by_role["client_secondary"] += [n for n in (doc.spouse.firstName,) if n]
        by_role["others"] += [d.name for d in doc.dependants if d.name]
    return CrmNames(**{k: list(dict.fromkeys(v)) for k, v in by_role.items()})


async def transcribe(call: CallInput, pins: VersionPins, settings: Settings | None = None) -> Transcript:
    """Stage 1: the given transcript, the scripted call through the stage 1 back half, or the audio through every step."""
    if call.transcript is not None:
        return call.transcript
    from . import ingest

    anchor = call.anchor_date or date.today()
    names = crm_names(call.crm, call.prior_document)
    if call.script is not None:
        script = ingest.ScriptedCall.parse(call.script, roles=call.script_roles or None)
        return await ingest.transcript_from_script_async(script, anchor, call_id=call.call_id, roles="script" if call.script_roles else "heuristic",
                                                         role_agent_pins=pins, prior_fna=call.prior_document, crm_names=names)
    if not call.audio:
        raise ValueError(f"call {call.call_id} has no transcript, script or audio")
    from .ingest.providers import build_asr, build_diarizer, build_second_pass

    s = settings or get_settings()
    meta = ingest.CallMeta(call_id=call.call_id, started_at=datetime.combine(anchor, time(9, 0)), anchor_date=anchor,
                           client_ids=[p.crm_id for p in call.crm.household if p.role in ("client_primary", "spouse")] if call.crm else [])
    return await ingest.ingest_call(call.audio, meta, asr=build_asr(pins, s), diarizer=build_diarizer(pins, s), role_agent_pins=pins,
                                    second_pass=build_second_pass(pins, s), prior_fna=call.prior_document, crm_names=names)


async def process_call(
    call: CallInput,
    pins: VersionPins | None = None,
    *,
    run_id: str | None = None,
    services: HarnessServices | None = None,
    settings: Settings | None = None,
    forecast: bool = True,
    note: bool = True,
) -> PipelineResult:
    """Every stage for one call, in memory. ``services`` replace the harness's stage wiring (tests)."""
    pins = pins or VersionPins()
    settings = settings or get_settings()
    run_id = run_id or run_id_for(call.call_id, pins)
    catalog = load_catalog(pins.schema_version)
    skipped: list[str] = []

    transcript = await transcribe(call, pins, settings)
    doc = call.prior_document
    provenance = Provenance(run_id=run_id, schema_version=catalog.version, lexicon_version=pins.lexicon_version, calibrator_version=pins.calibrator_version,
                            extractor_model=pins.reasoning_model)
    prior = binding.load_prior(doc, call.crm, call_id=call.call_id, run_id=run_id, anchor_date=transcript.anchor_date, catalog=catalog,
                               provenance=provenance)
    discourse = call.discourse or await run_discourse(transcript, prior=prior, crm=call.crm, pins=pins, catalog=catalog)

    inputs = AgentInputs(call_id=call.call_id, run_id=run_id, transcript=transcript, discourse=discourse, profile=prior, pins=pins,
                         budgets=settings.budgets, quality=transcript.quality, crm=call.crm, glossary=call.glossary, base_document=doc,
                         call_minutes=transcript.duration / 60.0)
    agent = await run_agent(inputs, services=services)
    profile = agent.profile
    followups, flags = list(agent.followups or profile.open_questions), list(profile.blocking_flags)

    engine = _optional("advisor_intel.fna_engine.analysis", "analyse")
    analyse = functools.partial(engine, today=transcript.anchor_date) if engine is not None else None  # needs as of the call date
    voi = _optional("advisor_intel.voi.run", "run_voi_and_followups")
    if voi is not None:
        runs = _optional("advisor_intel.forecast.sensitivity", "sensitivity_runs")
        pos_runs = functools.partial(runs, base_document=doc) if runs is not None else None  # the forecast sees the prior file too
        out = await voi(profile, catalog=catalog, pins=pins, pos_runs=pos_runs, analyse=analyse, transcript=transcript, settings=settings,
                        base_document=doc, seed=forecast_seed(run_id))
        profile, followups, flags = out.profile, list(out.followups), list(out.flags)
    else:
        skipped.append("voi")
    if transcript.quality is not None and transcript.quality.role_review_needed and not any(f.kind == "role_review" for f in flags):
        flags.insert(0, BlockingFlag(flag_id="flag.role_review", kind="role_review", fact_id=None,
                                     message="Confirm who is speaking before reviewing: a speaker role is below 0.8 confidence (§5.5)."))
    profile = profile.model_copy(update={"open_questions": followups, "blocking_flags": flags})

    export, sidecar = binding.export_bundle(profile, base=doc)
    document = export.document
    analysis = None
    if analyse is not None:
        try:
            analysis = analyse(document).model_dump(mode="json")
        except Exception as e:  # noqa: BLE001 - the review still works without the deterministic analysis
            log.warning("FNA analysis failed on the median document: %s", e)

    fc = None
    run_forecast = _optional("advisor_intel.forecast.run", "run_forecast") if forecast else None
    if run_forecast is not None:
        fc = run_forecast(profile, base_document=doc, mode="preview", seed=forecast_seed(run_id))
        if hasattr(fc, "__await__"):
            fc = await fc
    elif forecast:
        skipped.append("forecast")

    mn = None
    generate_note = _optional("advisor_intel.note.note", "generate_note") if note else None
    if generate_note is not None:
        mn = await generate_note(profile, pins=pins, catalog=catalog)
    elif note:
        skipped.append("note")

    usage = dict(agent.outcome.usage)
    usage["cost_usd"] = float(usage.get("usd", 0.0))
    return PipelineResult(
        call_id=call.call_id, run_id=run_id, transcript=transcript, discourse=discourse, profile=profile, outcome=agent.outcome, followups=followups,
        flags=flags, forecast=fc, note=mn, analysis=analysis, document=document, sidecar=sidecar, usage=usage,
        tool_calls=[e["tool"] for e in agent.state.get("events", [])], summary=agent.summary, skipped=skipped,
    )


async def process_cached_call(call: Any, pins: VersionPins) -> PipelineResult:
    """The eval harness's task (§15.4): stages 3–6 and the preview forecast on cached stage 1–2 output."""
    ci = CallInput(call_id=call.call_id, anchor_date=call.anchor_date, transcript=call.transcript, discourse=getattr(call, "discourse", None),
                   prior_document=getattr(call, "prior_document", None), crm=getattr(call, "crm", None))
    return await process_call(ci, pins, note=False)
