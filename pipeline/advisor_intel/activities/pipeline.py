"""The call pipeline's activities (spec §13.1): each loads its inputs from Postgres and object storage,
runs its stage, saves the outputs, and is idempotent on ``(call_id, stage, versions)``.

- ``ingest_audio`` and ``run_discourse`` cache their outputs per call and stage versions, so a
  re-run never re-transcribes (§5) and extraction experiments start from the cache (§15.1). A
  scripted call (no audio) runs the stage 1 back half on the script's words.
- ``run_agent`` drives the harness with ``activity.heartbeat`` and a Postgres checkpoint store, so
  a retry resumes from the last turn (§13.2); it saves the profile, facts and ops.
- ``run_voi_and_followups``, ``run_forecast``, ``render_meeting_note`` record their output per run
  and stage inputs; ``apply_review`` and ``intake_followup_answer`` per submission and answer;
  notifications per scheduled activity, so a retry never sends twice.
- VOI and the forecast are skipped (and say so in their outcome) when their packages are not
  installed in the worker, as in the in-process runner (``advisor_intel.pipeline``).
- A run started from a later stage (``start_stage``) takes its profile from the run it re-runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
import hashlib
import inspect
import logging
import tempfile
from collections.abc import Callable, Iterator
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from temporalio import activity
from temporalio.exceptions import ApplicationError

from .. import binding
from ..config import Settings
from ..extract.reducer import apply_state_ops
from ..models.crm import CrmRecord
from ..discourse.run import with_rewrites
from ..models.entities import DiscourseOutput
from ..models.facts import Provenance
from ..models.fna import FnaDocument
from ..models.forecast import ForecastResult
from ..models.profile import BlockingFlag, FollowUp, Profile, StateOp
from ..models.runs import AgentOutcome, VersionPins
from ..models.transcript import Transcript
from ..note import MeetingNote, apply_note, generate_note, update_note
from ..notify import Notifier, ReviewSummary, compose
from ..review_actions import apply_reviewer_ops, change_summary
from ..schema.slots import Catalog, load_catalog
from ..storage.checkpoints import PostgresCheckpointStore
from ..storage.glossary import PostgresGlossaryStore, SyncGlossaryStore, glossary_lines
from ..storage.objects import ObjectStore, safe_segment
from ..storage.records import CallRecord, ReviewEvent, RunRecord
from ..storage.store import Store
from ..telemetry import run_context
from ..voi.intake import answer_transcript
from ..workflows.contracts import (
    APPLY_REVIEW,
    INGEST_AUDIO,
    INTAKE_FOLLOWUP,
    NOTIFY_ADVISOR,
    RENDER_NOTE,
    RUN_AGENT,
    RUN_DISCOURSE,
    RUN_FORECAST,
    RUN_VOI,
    SEND_REVIEW_REMINDER,
    AnswerInput,
    ForecastOutcome,
    IngestOutcome,
    IntakeOutcome,
    NoteOutcome,
    NotifyInput,
    NotifyOutcome,
    ReviewInput,
    ReviewOutcome,
    StageInput,
    StageOutcome,
    VoiOutcome,
)
from .keys import digest, discourse_key, ingest_key, run_key
from .providers import Providers
from .stages import StageFunctions, heartbeating, in_activity, invoke

log = logging.getLogger(__name__)

COLLAPSE_PRIORITY = 0.005  # §10.3: auto-accepted facts below this priority are collapsed in review
SAST = ZoneInfo("Africa/Johannesburg")
COLD_START = "cold-start"  # the calibrator version before any is trained (VersionPins.calibrator_version)


def input_error(message: str) -> ApplicationError:
    """A failure no retry can fix (a missing call, profile or follow-up)."""
    return ApplicationError(message, type="InputError", non_retryable=True)


def dump(obj: Any) -> Any:
    """Plain data from a stage's return value (Pydantic model, dataclass or dict)."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


def _execution() -> str:
    """The Temporal execution (run id) of the calling workflow: a reset starts a new one."""
    return (activity.info().workflow_run_id or "") if in_activity() else ""


def call_date(call: CallRecord) -> date:
    """The call's date in South Africa, the anchor for relative time (§5.1): from its start, else its upload."""
    t = call.started_at or call.created_at or datetime.now(timezone.utc)
    return (t.astimezone(SAST) if t.tzinfo else t).date()


def forecast_seed(run_id: str) -> int:
    """A deterministic seed per run (§13.1: pure numpy, deterministic seed per run)."""
    return int(hashlib.sha256(run_id.encode()).hexdigest()[:8], 16)


class PipelineActivities:
    """Activities of ``CallPipelineWorkflow``, ``FollowupIntakeWorkflow`` and ``ForecastWorkflow``."""

    def __init__(
        self,
        *,
        store: Store,
        objects: ObjectStore,
        settings: Settings,
        notifier: Notifier | None = None,
        stages: StageFunctions | None = None,
        providers: Providers | None = None,
        note_model: Any = None,
    ) -> None:
        self.store = store
        self.objects = objects
        self.settings = settings
        self.notifier = notifier or Notifier.from_env()
        self.stages = stages or StageFunctions()
        self.providers = providers or Providers()
        self.note_model = note_model

    def activities(self) -> list[Any]:
        return [
            self.ingest_audio,
            self.run_discourse,
            self.run_agent,
            self.run_voi_and_followups,
            self.run_forecast,
            self.apply_review,
            self.notify_advisor,
            self.send_review_reminder,
            self.intake_followup_answer,
            self.render_meeting_note,
        ]

    # ------------------------------------------------------------------ helpers

    @contextlib.contextmanager
    def _ctx(self, run_id: str, call_id: str, stage: str, pins: VersionPins) -> Iterator[None]:
        with run_context(call_id=call_id, workflow_id=run_id, stage=stage, pins=pins):
            yield

    async def _call(self, call_id: str) -> CallRecord:
        call = await self.store.get_call(call_id)
        if call is None:
            raise input_error(f"no call {call_id}")
        return call

    async def _ensure_run(self, inp: StageInput) -> RunRecord:
        """Record the run's pins (the API does this when it starts a run; a run started elsewhere gets it here)."""
        run = await self.store.get_run(inp.run_id)
        if run is not None:
            return run
        await self._call(inp.args.call_id)
        return await self.store.register_run(inp.run_id, inp.args.call_id, inp.args.versions, start_stage=inp.args.start_stage)

    async def _prior(self, call: CallRecord) -> tuple[FnaDocument | None, CrmRecord | None]:
        """The client's FNA document as it was before the call, and the CRM record."""
        client = call.primary_client_id
        if not client:
            return None, None
        doc_id = call.meta.get("prior_document_id")
        doc = await self.store.client_document(int(doc_id)) if doc_id else None
        if doc is None and "prior_document_id" not in call.meta:
            doc = await self.store.latest_client_document(client)
        return doc, await self.store.get_crm_record(client)

    def _catalog(self, pins: VersionPins) -> Catalog:
        return load_catalog(pins.schema_version)

    async def _transcript(self, call_id: str, pins: VersionPins) -> Transcript:
        t = await self.store.load_transcript(call_id, ingest_key(pins))
        if t is None:
            t = await self.store.load_transcript(call_id)  # a re-run with new ASR pins that started after ingest
        if t is None:
            raise input_error(f"call {call_id} has no transcript; run from the ingest stage")
        return t

    async def _discourse(self, call_id: str, pins: VersionPins) -> DiscourseOutput | None:
        return await self.store.load_discourse(call_id, discourse_key(pins)) or await self.store.load_discourse(call_id)

    async def _run_profile(self, run_id: str, call_id: str) -> Profile:
        """The run's latest profile; a run started from a later stage starts from its source run's."""
        profile = await self.store.load_profile(run_id)
        if profile is not None:
            return profile
        run = await self.store.get_run(run_id)
        sources = [run.source_run_id] if run and run.source_run_id else []
        sources += [r.workflow_id for r in await self.store.runs_for_call(call_id) if r.workflow_id != run_id]
        for src in sources:
            if not src:
                continue
            base = await self.store.load_profile(src)
            if base is not None:
                copy = base.model_copy(update={"run_id": run_id})
                log.info("run %s starts from run %s's profile v%s", run_id, src, base.version)
                return await self.store.save_profile(copy)
        raise input_error(f"run {run_id} has no profile and no earlier run of call {call_id} has one; run from the agent stage")

    async def _save_new_profile(self, profile: Profile) -> Profile:
        """Save a stage's profile, superseding any version an interrupted attempt stored."""
        versions = await self.store.profile_versions(profile.run_id)
        if versions and versions[-1].version >= profile.version:
            profile = profile.model_copy(update={"version": versions[-1].version + 1})
        saved = await self.store.save_profile(profile)
        await self.objects.put_json(f"profiles/{safe_segment(profile.run_id)}/v{saved.version}.json", saved)
        return saved

    # ------------------------------------------------------------------ stage 1: ingest

    @activity.defn(name=INGEST_AUDIO)
    async def ingest_audio(self, inp: StageInput) -> IngestOutcome:
        args, pins = inp.args, inp.args.versions
        with self._ctx(inp.run_id, args.call_id, "ingest", pins):
            await self._ensure_run(inp)
            key = ingest_key(pins)
            done = await self.store.get_stage_output(args.call_id, "ingest", key)
            if done is not None:
                return IngestOutcome.model_validate({**done.output, "cached": True})
            transcript = await self.store.load_transcript(args.call_id, key)
            if transcript is None:
                transcript = await self._transcribe(args.call_id, pins)
                uri = await self.objects.put_json(f"transcripts/{safe_segment(args.call_id)}/{key}.json", transcript)
                await self.store.save_transcript(args.call_id, key, transcript, uri=uri)
            q = transcript.quality
            out = IngestOutcome(cache_key=key, quality=q, manual=bool(q and q.failed), role_review_needed=bool(q and q.role_review_needed))
            await self.store.put_stage_output(
                args.call_id, "ingest", key, inp.run_id, out.model_dump(mode="json"), pins.model_dump(mode="json")
            )
            return out

    async def _transcribe(self, call_id: str, pins: VersionPins) -> Transcript:
        call = await self._call(call_id)
        if call.meta.get("script_uri"):
            return await self._from_script(call, pins)
        if not call.audio_uri:
            raise input_error(f"call {call_id} has no audio")
        ingest_call = self.stages.get("ingest_call")
        from ..ingest.types import CallMeta
        from ..pipeline import crm_names

        prior, crm = await self._prior(call)
        started = call.started_at or call.created_at or datetime.now(timezone.utc)
        meta = CallMeta(
            call_id=call.call_id,
            started_at=started,
            advisor_id=call.advisor_id,
            client_ids=call.client_ids,
            anchor_date=call_date(call),
            advisor_channel=call.meta.get("advisor_channel"),
            shared_client_channel=call.meta.get("shared_client_channel"),
        )
        # Providers are built before the download: a configuration error fails the attempt at once, never retried.
        asr = self.providers.asr(pins, self.settings)
        diarizer = self.providers.diarizer(pins, self.settings)
        second_pass = self.providers.second_pass(pins, self.settings)
        suffix = Path(call.meta.get("filename") or self.objects.key_for(call.audio_uri)).suffix or ".wav"
        with tempfile.TemporaryDirectory(prefix="advisor-intel-audio-") as tmp:
            audio = await self.objects.download(call.audio_uri, Path(tmp) / f"call{suffix}")
            async with heartbeating("ingest"):
                return await invoke(
                    ingest_call,
                    audio,
                    meta,
                    asr=asr,
                    diarizer=diarizer,
                    role_agent_pins=pins,
                    second_pass=second_pass,
                    prior_fna=prior,
                    crm_names=crm_names(crm, prior),
                )

    async def _from_script(self, call: CallRecord, pins: VersionPins) -> Transcript:
        """A scripted call (synthetic and demo calls, §15): the stage 1 back half on the script's words, no audio."""
        from_script = self.stages.get("ingest_script")
        from ..ingest import ScriptedCall
        from ..pipeline import crm_names

        prior, crm = await self._prior(call)
        roles = dict(call.meta.get("script_roles") or {})
        script = ScriptedCall.parse((await self.objects.get_bytes(call.meta["script_uri"])).decode(), roles=roles or None)
        async with heartbeating("ingest"):
            return await invoke(
                from_script,
                script,
                call_date(call),
                call_id=call.call_id,
                roles="script" if roles else "heuristic",
                role_agent_pins=pins,
                prior_fna=prior,
                crm_names=crm_names(crm, prior),
            )

    # ------------------------------------------------------------------ stage 2: discourse

    @activity.defn(name=RUN_DISCOURSE)
    async def run_discourse(self, inp: StageInput) -> StageOutcome:
        args, pins = inp.args, inp.args.versions
        with self._ctx(inp.run_id, args.call_id, "discourse", pins):
            await self._ensure_run(inp)
            key = discourse_key(pins)
            done = await self.store.get_stage_output(args.call_id, "discourse", key)
            if done is not None:
                return StageOutcome.model_validate({**done.output, "cached": True})
            output = await self.store.load_discourse(args.call_id, key)
            if output is None:
                call = await self._call(args.call_id)
                transcript = await self._transcript(args.call_id, pins)
                doc, crm = await self._prior(call)
                catalog = self._catalog(pins)
                prior = (
                    binding.load_prior(
                        doc, crm, call_id=args.call_id, run_id=inp.run_id, anchor_date=transcript.anchor_date, catalog=catalog
                    )
                    if doc is not None or crm is not None
                    else None
                )
                async with heartbeating("discourse"):
                    output = await invoke(self.stages.get("run_discourse"), transcript, prior=prior, crm=crm, pins=pins, catalog=catalog)
                output = DiscourseOutput.model_validate(dump(output))
                uri = await self.objects.put_json(f"discourse/{safe_segment(args.call_id)}/{key}.json", output)
                await self.store.save_discourse(args.call_id, key, output, versions=pins.model_dump(mode="json"), uri=uri)
            out = StageOutcome(
                stage="discourse", cache_key=key, detail={"entities": len(output.entities), "rewrites": len(output.rewrites)}
            )
            await self.store.put_stage_output(
                args.call_id, "discourse", key, inp.run_id, out.model_dump(mode="json"), pins.model_dump(mode="json")
            )
            return out

    # ------------------------------------------------------------------ stages 3–6: the agent (§13.2)

    @activity.defn(name=RUN_AGENT)
    async def run_agent(self, inp: StageInput) -> AgentOutcome:
        args, pins = inp.args, inp.args.versions
        with self._ctx(inp.run_id, args.call_id, "agent", pins):
            await self._ensure_run(inp)
            scope = _execution()
            key = run_key("agent", inp.run_id, pins, execution=scope)
            done = await self.store.get_stage_output(args.call_id, "agent", key)
            if done is not None:
                return AgentOutcome.model_validate(done.output)
            call = await self._call(args.call_id)
            transcript = await self._transcript(args.call_id, pins)
            discourse = await self._discourse(args.call_id, pins)
            if discourse is None:
                raise input_error(f"call {args.call_id} has no discourse output; run from the discourse stage")
            doc, crm = await self._prior(call)
            catalog = self._catalog(pins)
            transcript = with_rewrites(transcript, discourse)
            # The agent loop's result is recorded on its own, so a retry of the checks after it does not rerun the loop.
            loop_key = run_key("agent_loop", inp.run_id, pins, execution=scope)
            looped = await self.store.get_stage_output(args.call_id, "agent_loop", loop_key)
            if looped is not None:
                outcome = AgentOutcome.model_validate(looped.output["outcome"])
                profile = await self.store.load_profile(inp.run_id, int(looped.output["profile_version"]))
                if profile is None:
                    raise input_error(f"run {inp.run_id} lost profile version {looped.output['profile_version']}")
            else:
                outcome, profile = await self._agent_loop(inp, transcript, discourse, doc, crm, catalog, scope)
                await self.store.put_stage_output(
                    args.call_id,
                    "agent_loop",
                    loop_key,
                    inp.run_id,
                    {"outcome": outcome.model_dump(mode="json"), "profile_version": profile.version},
                    pins.model_dump(mode="json"),
                )
            # Stage 5 after the agent (§9): the omission check, re-checks of facts changed since the agent's
            # checks, then calibration with the run's pinned calibrator and conformal auto-accept.
            verify_after = self.stages.optional("verify_after_agent")
            if verify_after is not None:
                calibrator, conformal = await self._calibration(pins.calibrator_version)
                async with heartbeating("verify"):
                    checked = await invoke(
                        verify_after,
                        profile,
                        transcript=transcript,
                        discourse=discourse,
                        pins=pins,
                        catalog=catalog,
                        run_id=inp.run_id,
                        calibrator=calibrator,
                        conformal=conformal,
                        settings=self.settings,
                    )
                await self._save_new_profile(Profile.model_validate(dump(checked.profile)))
            else:
                log.warning("run %s: verification is not installed in this worker; facts are not scored", inp.run_id)
            await self.store.put_stage_output(
                args.call_id, "agent", key, inp.run_id, outcome.model_dump(mode="json"), pins.model_dump(mode="json")
            )
            return outcome

    async def _agent_loop(
        self,
        inp: StageInput,
        transcript: Transcript,
        discourse: DiscourseOutput,
        doc: FnaDocument | None,
        crm: CrmRecord | None,
        catalog: Catalog,
        scope: str,
    ) -> tuple[AgentOutcome, Profile]:
        """The orchestrator over the client's prior profile (§13.2), checkpointed per turn; returns its saved profile."""
        args, pins = inp.args, inp.args.versions
        provenance = Provenance(
            run_id=inp.run_id,
            schema_version=catalog.version,
            lexicon_version=pins.lexicon_version,
            calibrator_version=pins.calibrator_version,
            extractor_model=pins.reasoning_model,
            extractor_prompt_version=pins.prompt_versions.get("extract_slot_group", ""),
        )
        prior = binding.load_prior(
            doc,
            crm,
            call_id=args.call_id,
            run_id=inp.run_id,
            anchor_date=transcript.anchor_date,
            catalog=catalog,
            provenance=provenance,
        )
        budgets = self.settings.budgets.scaled(transcript.duration / 60.0)
        if args.budget_usd is not None:
            budgets = budgets.model_copy(update={"usd_cap": args.budget_usd})
        inputs = self.stages.get("agent_inputs")(
            call_id=args.call_id,
            run_id=inp.run_id,
            transcript=transcript,
            discourse=discourse,
            profile=prior,
            pins=pins,
            budgets=budgets,
            quality=transcript.quality,
            crm=crm,
            glossary=glossary_lines(await PostgresGlossaryStore(self.store.db).entries()),
            base_document=doc,
            call_minutes=transcript.duration / 60.0,
        )
        checkpoints = PostgresCheckpointStore(self.store, scope=scope)
        # The harness heartbeats every node; the background beat covers one long model request.
        async with heartbeating("agent"):
            result = await invoke(
                self.stages.get("run_agent"),
                inputs,
                services=self._agent_services(),
                checkpoints=checkpoints,
                heartbeat=activity.heartbeat,
            )
        outcome = AgentOutcome.model_validate(dump(result.outcome))
        profile = Profile.model_validate(dump(result.profile))
        followups = [FollowUp.model_validate(dump(q)) for q in getattr(result, "followups", None) or []]
        if followups:  # the agent's drafts stand until VOI ranks them (§10.5)
            profile = profile.model_copy(update={"open_questions": followups})
        return outcome, await self._save_new_profile(profile)

    async def _calibration(self, version: str) -> tuple[Any, Any]:
        """The run's pinned calibrator and conformal thresholds from the registry and object storage (§9.4–9.5).

        Before any calibrator is trained (``cold-start``) both may be None: the cold-start identity scores the
        facts and nothing is auto-accepted. Any other version must be in the registry.
        """
        rows = {r.slot_group: r for r in await self.store.calibrators(version=version)}
        pooled = rows.get("*")
        if pooled is None:
            if version != COLD_START:
                raise input_error(f"calibrator {version!r} is not in the registry")
            return None, None
        calibrator = None
        if pooled.artifact_uri:
            calibrator = self.stages.get("calibrator_type").from_bytes(await self.objects.get_bytes(pooled.artifact_uri))
        conformal = None
        if pooled.metrics.get("thresholds_uri"):
            conformal = self.stages.get("conformal_thresholds").model_validate(
                await self.objects.get_json(pooled.metrics["thresholds_uri"])
            )
        return calibrator, conformal

    def _agent_services(self) -> Any:
        """Every installed stage behind the agent's tools, with the practice glossary in Postgres (§8.5, §13.3)."""
        factory = self.stages.optional("agent_services")
        return factory(glossary_store=SyncGlossaryStore(self.store.db.dsn)) if factory is not None else None

    # ------------------------------------------------------------------ stage 6: VOI and follow-ups (§10)

    def _pos_runs(self, base_document: FnaDocument | None) -> Callable[..., Any] | None:
        """The forecast's reduced runs for VOI (§10.2), on the client's prior file; CPU-bound, so in a thread."""
        runs = self.stages.optional("sensitivity_runs")
        if runs is None:
            return None
        bound = functools.partial(runs, base_document=base_document)
        if inspect.iscoroutinefunction(runs):
            return bound

        async def threaded(*a: Any, **kw: Any) -> Any:
            return await asyncio.to_thread(bound, *a, **kw)

        return threaded

    def _analyser(self, today: date) -> Callable[[FnaDocument], Any] | None:
        """The FNA engine's ``analyse()`` as of the call date (§12.7), or None when the engine is not installed."""
        engine = self.stages.optional("fna_analyse")
        return functools.partial(engine, today=today) if engine is not None else None

    @activity.defn(name=RUN_VOI)
    async def run_voi_and_followups(self, inp: StageInput) -> VoiOutcome:
        args, pins = inp.args, inp.args.versions
        with self._ctx(inp.run_id, args.call_id, "voi", pins):
            await self._ensure_run(inp)
            key = run_key("voi", inp.run_id, pins, execution=_execution())
            done = await self.store.get_stage_output(args.call_id, "voi", key)
            if done is not None:
                return VoiOutcome.model_validate({**done.output, "cached": True})
            profile = await self._run_profile(inp.run_id, args.call_id)
            transcript = await self._transcript(args.call_id, pins)
            voi = self.stages.optional("run_voi_and_followups")
            if voi is None:  # as in the in-process runner: the agent's follow-ups and flags stand
                log.warning("run %s: VOI is not installed in this worker; the review gets the agent's follow-ups", inp.run_id)
                new, followups, flags = profile, list(profile.open_questions), list(profile.blocking_flags)
            else:
                doc, _ = await self._prior(await self._call(args.call_id))
                async with heartbeating("voi"):
                    out = await invoke(
                        voi,
                        profile,
                        catalog=self._catalog(pins),
                        pins=pins,
                        pos_runs=self._pos_runs(doc),
                        analyse=self._analyser(transcript.anchor_date),
                        transcript=transcript,
                        settings=self.settings,
                        base_document=doc,
                        seed=forecast_seed(inp.run_id),
                    )
                new = Profile.model_validate(dump(out.profile))
                followups = [FollowUp.model_validate(dump(q)) for q in out.followups]
                flags = [BlockingFlag.model_validate(dump(f)) for f in out.flags]
            if transcript.quality and transcript.quality.role_review_needed and not any(f.kind == "role_review" for f in flags):
                flags.insert(
                    0,
                    BlockingFlag(
                        flag_id="flag.role_review",
                        kind="role_review",
                        fact_id=None,
                        message="Confirm who is speaking before reviewing: a speaker role is below 0.8 confidence (§5.5).",
                    ),
                )
            # A flag the reviewer already cleared stays cleared when VOI raises it again.
            cleared = {f.flag_id: f for f in profile.blocking_flags if f.cleared}
            flags = [cleared.get(f.flag_id, f) for f in flags]
            saved = await self.store.save_profile(new.model_copy(update={"open_questions": followups, "blocking_flags": flags}))
            result = VoiOutcome(
                profile_version=saved.version, n_followups=len(followups), n_flags=sum(not f.cleared for f in flags), skipped=voi is None
            )
            if voi is not None:  # a skipped VOI is not recorded: a worker with VOI installed runs it on a retry or re-run
                await self.store.put_stage_output(
                    args.call_id, "voi", key, inp.run_id, result.model_dump(mode="json"), pins.model_dump(mode="json")
                )
            return result

    # ------------------------------------------------------------------ stage 8: forecast (§12)

    @activity.defn(name=RUN_FORECAST)
    async def run_forecast(self, inp: StageInput) -> ForecastOutcome:
        args, pins = inp.args, inp.args.versions
        mode = args.forecast_mode
        with self._ctx(inp.run_id, args.call_id, "forecast", pins):
            if inp.profile_version is not None:
                profile = await self.store.load_profile(inp.run_id, inp.profile_version)
                if profile is None:
                    raise input_error(f"run {inp.run_id} has no profile version {inp.profile_version}")
            else:
                profile = await self._run_profile(inp.run_id, args.call_id)
            key = run_key("forecast", inp.run_id, pins, profile.version, mode)
            done = await self.store.get_stage_output(args.call_id, "forecast", key)
            if done is not None:
                return ForecastOutcome.model_validate({**done.output, "cached": True})
            engine = self.stages.optional("run_forecast")
            if engine is None:  # as in the in-process runner; not recorded, so a worker with the engine runs it
                log.warning("run %s: the forecast engine is not installed in this worker; no %s forecast", inp.run_id, mode)
                return ForecastOutcome(run_id=inp.run_id, profile_version=profile.version, mode=mode, skipped=True)
            base, _ = await self._prior(await self._call(args.call_id))
            async with heartbeating("forecast"):
                raw = await invoke(engine, profile, base_document=base, mode=mode, seed=forecast_seed(inp.run_id))
            result = ForecastResult.model_validate(dump(raw))
            await self.store.save_forecast(
                inp.run_id, profile.version, mode, result, config_version=pins.forecast_config_version, cma_version=pins.cma_version
            )
            out = ForecastOutcome(run_id=inp.run_id, profile_version=profile.version, mode=mode, pos=result.pos)
            await self.store.put_stage_output(
                args.call_id, "forecast", key, inp.run_id, out.model_dump(mode="json"), pins.model_dump(mode="json")
            )
            return out

    # ------------------------------------------------------------------ stage 7: review (§11)

    @activity.defn(name=APPLY_REVIEW)
    async def apply_review(self, inp: ReviewInput) -> ReviewOutcome:
        r = inp.review
        run = await self.store.get_run(inp.run_id)
        pins = run.versions if run else VersionPins()
        with self._ctx(inp.run_id, r.call_id, "review", pins):
            key = digest("apply_review", inp.run_id, r.profile_version, r.submitted_at.isoformat(), r.reviewer_id)
            done = await self.store.get_stage_output(r.call_id, "review", key)
            if done is not None:
                return ReviewOutcome.model_validate({**done.output, "cached": True})
            applied = rejected = n = 0
            if r.ops:
                # File-exchange reviews carry their ops (API reviews wrote theirs through POST /ops). The ops
                # are tagged with this submission, and they and their review events are written in one
                # transaction, so a retry after a crash finds them applied and does not apply them twice.
                tagged = [op.model_copy(update={"payload": {**op.payload, "batch": key}}) for op in r.ops]

                def fn(profile: Profile) -> tuple[Profile, tuple[list[ReviewEvent], int, int]]:
                    if any(op.payload.get("batch") == key for op in profile.ops_log):
                        return profile, ([], 0, 0)
                    result, evs = apply_reviewer_ops(profile, tagged, reviewer_id=r.reviewer_id, at=r.submitted_at)
                    return result.profile, (evs, len(result.applied), len(result.rejected))

                profile, (events, applied, rejected) = await self.store.update_profile(inp.run_id, fn, review_events=lambda x: x[0])
                n = len(events)
            else:
                profile = await self._run_profile(inp.run_id, r.call_id)
            if not any(e.action == "submit" and e.detail.get("key") == key for e in await self.store.review_events(run_id=inp.run_id)):
                submit = ReviewEvent(
                    workflow_id=inp.run_id,
                    action="submit",
                    reviewer_id=r.reviewer_id,
                    at=r.submitted_at,
                    profile_version=profile.version,
                    detail={"spot_check": r.spot_check, "submitted_version": r.profile_version, "key": key},
                )
                n += await self.store.record_review_events([submit])
            await self.store.freeze_profile(inp.run_id, profile.version, frozen_by=r.reviewer_id)
            await self.objects.put_json(f"profiles/{safe_segment(inp.run_id)}/v{profile.version}.json", profile)
            await self._save_reviewed_document(r.call_id, profile, note=None)
            out = ReviewOutcome(
                profile_version=profile.version, applied=applied, rejected=rejected, review_events=n, changes=change_summary(profile)
            )
            await self.store.put_stage_output(r.call_id, "review", key, inp.run_id, out.model_dump(mode="json"))
            return out

    async def _save_reviewed_document(self, call_id: str, profile: Profile, note: MeetingNote | None) -> None:
        """The reviewed median document becomes the client's prior for the next call (§3.6)."""
        call = await self._call(call_id)
        if not call.primary_client_id:
            return
        base, _ = await self._prior(call)
        export, _ = binding.export_bundle(profile, base=base)
        doc = apply_note(export.document, note) if note is not None else export.document
        await self.store.save_client_document(call.primary_client_id, doc, source="reviewed", call_id=call_id)

    # ------------------------------------------------------------------ follow-up answers (§10.6)

    @activity.defn(name=INTAKE_FOLLOWUP)
    async def intake_followup_answer(self, inp: AnswerInput) -> IntakeOutcome:
        a, pins = inp.answer, inp.args.versions
        with self._ctx(inp.run_id, inp.args.call_id, "followup_intake", pins):
            key = digest("intake", inp.run_id, a.followup_id, a.answered_at.isoformat(), a.text)
            done = await self.store.get_stage_output(inp.args.call_id, "intake", key)
            if done is not None:
                return IntakeOutcome.model_validate({**done.output, "duplicate": True})
            rec = await self.store.get_followup(inp.run_id, a.followup_id)
            if rec is None:
                raise input_error(f"run {inp.run_id} has no follow-up {a.followup_id}")
            await self.store.record_followup_answer(inp.run_id, a)
            profile = await self._run_profile(inp.run_id, inp.args.call_id)
            # The answer as a one-segment transcript by the client, segment ``fu-<followup id>`` (§10.6).
            transcript = answer_transcript(a, rec.followup, call_id=inp.args.call_id, anchor_date=profile.anchor_date)
            segment = transcript.segments[0]
            async with heartbeating("followup_intake"):
                raw = await invoke(
                    self.stages.get("extract_followup"), profile, transcript, followup=rec.followup, pins=pins, catalog=self._catalog(pins)
                )
            ops = [StateOp.model_validate(dump(o)) for o in raw]

            def fn(p: Profile) -> tuple[Profile, Any]:
                if any(op.payload.get("batch") == key for op in p.ops_log):
                    return p, None  # an earlier attempt applied this answer
                seq = p.next_seq()
                ready = []
                for i, op in enumerate(ops):
                    payload = {**op.payload, "batch": key}
                    if op.op == "ADD":
                        payload["source"] = "followup"
                    ready.append(
                        op.model_copy(update={"seq": seq + i, "segment_id": op.segment_id or segment.segment_id, "payload": payload})
                    )
                res = apply_state_ops(p, ready)
                return res.profile, res

            profile, res = await self.store.update_profile(inp.run_id, fn)
            await self.store.set_followup_status(inp.run_id, a.followup_id, "answered")
            applied = len(res.applied) if res else sum(op.payload.get("batch") == key for op in profile.ops_log)
            rejected = len(res.rejected) if res else 0
            out = IntakeOutcome(followup_id=a.followup_id, profile_version=profile.version, applied=applied, rejected=rejected)
            if res and res.rejected:
                log.warning("follow-up %s: %d op(s) rejected: %s", a.followup_id, len(res.rejected), res.rejected[0].reason)
            await self.store.put_stage_output(inp.args.call_id, "intake", key, inp.run_id, out.model_dump(mode="json"))
            return out

    # ------------------------------------------------------------------ the meeting note (§11.6)

    @activity.defn(name=RENDER_NOTE)
    async def render_meeting_note(self, inp: StageInput) -> NoteOutcome:
        args, pins = inp.args, inp.args.versions
        with self._ctx(inp.run_id, args.call_id, "note", pins):
            profile = await self._run_profile(inp.run_id, args.call_id)
            key = run_key("note", inp.run_id, pins, profile.version)
            done = await self.store.get_stage_output(args.call_id, "note", key)
            if done is not None:
                return NoteOutcome.model_validate(done.output)
            prev = await self.store.latest_note(inp.run_id)
            catalog = self._catalog(pins)
            if prev is not None and prev[0] <= profile.version:
                note = await update_note(MeetingNote.model_validate(prev[1]), profile, pins=pins, catalog=catalog, model=self.note_model)
            else:
                note = await generate_note(profile, pins=pins, catalog=catalog, model=self.note_model)
            await self.store.save_note(inp.run_id, profile.version, note.model_dump(mode="json"))
            await self._save_reviewed_document(args.call_id, profile, note)
            out = NoteOutcome(profile_version=profile.version, sentences=len(note.sentences), removed=len(note.removed))
            await self.store.put_stage_output(args.call_id, "note", key, inp.run_id, out.model_dump(mode="json"))
            return out

    # ------------------------------------------------------------------ notifications

    @activity.defn(name=NOTIFY_ADVISOR)
    async def notify_advisor(self, inp: NotifyInput) -> NotifyOutcome:
        return await self._notify(inp)

    @activity.defn(name=SEND_REVIEW_REMINDER)
    async def send_review_reminder(self, inp: NotifyInput) -> NotifyOutcome:
        return await self._notify(inp)

    async def _notify(self, inp: NotifyInput) -> NotifyOutcome:
        with self._ctx(inp.run_id, inp.args.call_id, "notify", inp.args.versions):
            if in_activity():
                info = activity.info()
                key = f"{inp.run_id}/{info.workflow_run_id}/{info.activity_id}"
            else:
                key = f"{inp.run_id}/{inp.kind}/{inp.reminder_number}"
            call = await self._call(inp.args.call_id)
            profile = await self.store.load_profile(inp.run_id)
            summary = await self._summary(inp, profile)
            label = (
                profile.entities["person.client_primary"].canonical_name if profile and "person.client_primary" in profile.entities else ""
            )
            label = (
                call.meta.get("client_name") or (label if label and label != "Client" else "") or (call.primary_client_id or call.call_id)
            )
            n = compose(
                inp.kind, call_id=call.call_id, run_id=inp.run_id, recipient=call.meta.get("advisor_email"), client_label=label, s=summary
            )
            rec = await self.store.claim_notification(
                key, run_id=inp.run_id, kind=inp.kind, recipient=n.recipient, payload=n.model_dump(mode="json")
            )
            if rec.sent_at is not None:
                return NotifyOutcome(sent=True, channel=rec.channel or "", duplicate=True, recipient=rec.recipient)
            try:
                channel = await self.notifier.send(n)
            except Exception as e:
                await self.store.mark_notification(key, channel="failed", error=str(e))
                raise
            await self.store.mark_notification(key, channel=channel)
            return NotifyOutcome(sent=True, channel=channel, recipient=n.recipient)

    async def _summary(self, inp: NotifyInput, profile: Profile | None) -> ReviewSummary:
        s = ReviewSummary(partial=inp.partial, reminder_number=inp.reminder_number)
        q = await self.store.transcript_quality(inp.args.call_id, ingest_key(inp.args.versions))
        if q is not None:
            s.banners = list(q.banners)
        if profile is not None:
            s.needs_you = sum(
                1
                for f in profile.facts.values()
                if f.review.status == "pending"
                and not f.auto_accepted
                and f.change != "unchanged_not_discussed"
                and (f.review_priority >= COLLAPSE_PRIORITY or f.status == "contradicted" or not f.voi)
            )
            s.open_flags = len(profile.open_flags())
            s.open_followups = sum(q.status in ("open", "asked") for q in profile.open_questions)
        if inp.partial:
            done = [r for r in await self.store.stage_outputs_for_run(inp.run_id) if r.stage == "agent"]
            if done:
                s.unfinished_groups = list(done[-1].output.get("unfinished_groups") or [])
        return s
