"""Pipeline and nightly activities against local Postgres and object storage, with fake stage functions.

The stage code (ingest, discourse, harness, VOI, forecast, extraction, calibration) is replaced by
fakes through ``StageFunctions`` overrides; everything around it is real: storage, caching and
idempotency, checkpoints, the reducer, review events and notifications.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from advisor_intel import binding
from advisor_intel.activities import NightlyActivities, PipelineActivities, StageFunctions
from advisor_intel.activities.pipeline import forecast_seed
from advisor_intel.activities.providers import Providers
from advisor_intel.config import Settings
from advisor_intel.extract.reducer import annotate, make_ops
from advisor_intel.harness.deps import AgentInputs
from advisor_intel.ingest import CallMeta, CrmNames
from advisor_intel.models.dists import Point, Quantity
from advisor_intel.models.entities import DiscourseOutput
from advisor_intel.models.facts import Evidence, Hypothesis, Provenance
from advisor_intel.models.forecast import ForecastResult
from advisor_intel.models.profile import StateOp
from advisor_intel.models.runs import AgentOutcome, CallArgs, FollowupAnswer, ReviewSubmission, Stage, VersionPins
from advisor_intel.notify.notifier import Notification
from advisor_intel.storage import CalibratorRecord, CallRecord, GlossaryEntry, PostgresGlossaryStore, Store, SyncGlossaryStore
from advisor_intel.workflows import contracts as C

from .factories import GROSS, RET_AGE, flagged, make_transcript, sample_document, with_call_facts

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
RUN = "call-1-run-a"


class Fakes:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.agent_inputs: AgentInputs | None = None
        self.agent_services: Any = None
        self.agent_followups = False
        self.resumed_from: list[int] = []
        self.crash_after_turn: int | None = None
        self.role_review = False
        self.ingest_meta: CallMeta | None = None
        self.sensitivity_base: list[Any] = []
        self.verified_with: tuple[Any, Any] | None = None
        self.verify_failures = 0

    def count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    async def ingest(self, audio, meta, *, asr, diarizer, role_agent_pins, second_pass=None, prior_fna=None, crm_names=None):
        self.count("ingest")
        assert isinstance(meta, CallMeta) and isinstance(crm_names, CrmNames)
        assert audio.read_bytes() == b"RIFF-call-audio" and meta.advisor_id == "adv-1" and asr == "asr" and diarizer == "diarizer"
        assert prior_fna is not None and prior_fna.client.firstName == "Thabo" and "Thabo" in crm_names.client_primary
        self.ingest_meta = meta
        await asyncio.sleep(0.05)
        t = make_transcript(meta.call_id, role_agent_pins.asr_model_version)
        if self.role_review:
            t = t.model_copy(update={"quality": t.quality.model_copy(update={"role_review_needed": True})})
        return t

    async def discourse(self, transcript, *, prior, crm, pins, catalog):
        self.count("discourse")
        return DiscourseOutput(
            entities=prior.entities if prior else {}, rewrites={"s0002": "The client would like to retire at sixty, maybe sixty-five."}
        )

    async def agent(self, inputs, *, services, checkpoints, heartbeat):
        self.count("agent")
        self.agent_inputs, self.agent_services = inputs, services
        cp = await checkpoints.load(inputs.run_id)
        start = cp.turn if cp else 0
        self.resumed_from.append(start)
        for turn in range(start + 1, 4):
            heartbeat({"turn": turn})
            await checkpoints.save(inputs.run_id, turn, [{"turn": t} for t in range(1, turn + 1)], {"turn": turn})
            if self.crash_after_turn == turn:
                self.crash_after_turn = None
                raise RuntimeError("worker crashed")
        profile = with_call_facts(inputs.profile)
        followups = flagged(profile).open_questions if self.agent_followups else []
        return SimpleNamespace(outcome=AgentOutcome(turns=3), profile=profile, followups=followups)

    async def verify_after(self, profile, *, transcript, discourse, pins, catalog, run_id, calibrator, conformal, settings):
        self.count("verify")
        if self.verify_failures:
            self.verify_failures -= 1
            raise RuntimeError("verifier timed out")
        assert transcript.segment("s0002").rewritten_text and discourse.rewrites and run_id == profile.run_id
        self.verified_with = (calibrator, conformal)
        return SimpleNamespace(profile=annotate(profile, RET_AGE, p_correct=0.91, p_raw=0.88))

    def sensitivity(self, profile, fact_ids, *, n_paths=1000, seed, base_document=None):
        self.sensitivity_base.append(base_document)
        return {fid: {"pos_q10": 0.6, "pos_q90": 0.8, "pos_prior": 0.5} for fid in fact_ids}

    async def voi(self, profile, *, catalog, pins, pos_runs, analyse, transcript, settings, base_document, seed):
        self.count("voi")
        assert analyse(binding.materialise(profile)) == {"needs": {"retirement": "shortfall"}, "as_of": transcript.anchor_date.isoformat()}
        rows = await pos_runs(profile, [RET_AGE], n_paths=1000, seed=seed)
        assert rows[RET_AGE]["pos_q90"] == 0.8 and base_document is not None and seed == forecast_seed(profile.run_id)
        prof = annotate(profile, RET_AGE, review_priority=0.09, voi={"I_content": 0.09})
        f = flagged(prof)
        return SimpleNamespace(profile=prof, followups=f.open_questions, flags=f.blocking_flags, priorities={RET_AGE: 0.09})

    def forecast(self, profile, *, base_document=None, mode="preview", seed=None):
        self.count("forecast")
        assert base_document is not None and seed is not None
        return ForecastResult(
            run_id=profile.run_id,
            profile_version=str(profile.version),
            config_version="config_v1",
            cma_version="2026Q4",
            n_paths=1000,
            seed=seed,
            pos=0.7,
            pos_ci=(0.67, 0.73),
        )

    async def extract_followup(self, profile, transcript, *, followup, pins, catalog):
        self.count("extract_followup")
        seg = transcript.segments[0]
        ev = Evidence(
            segment_id=seg.segment_id,
            char_start=0,
            char_end=len(seg.text),
            quote=seg.text,
            speaker_role="client_primary",
            t_start=0,
            t_end=1,
            asr_mean_conf=1.0,
            role="supports",
        )
        payload = {
            "slot_id": "retirement.{p}.retirementAge",
            "instance_key": "client",
            "fna_path": "retirement.client.retirementAge",
            "status": "stated",
            "hypotheses": [
                Hypothesis(value=Quantity(dist=Point(value=63), unit="age"), weight=1.0, rationale="answered 63").model_dump(mode="json")
            ],
            "evidence": [ev.model_dump(mode="json")],
            "entity_refs": ["person.client_primary"],
            "provenance": Provenance(run_id=profile.run_id).model_dump(mode="json"),
        }
        return [StateOp(op="ADD", fact_id=RET_AGE, segment_id=seg.segment_id, payload=payload, actor="extractor", seq=0)]

    def overrides(self) -> dict[str, Any]:
        """Fakes for the stage code; ``agent_inputs`` stays the harness's own model."""
        return {
            "ingest_call": self.ingest,
            "run_discourse": self.discourse,
            "agent_services": lambda *, glossary_store: {"glossary_store": glossary_store},
            "run_agent": self.agent,
            "verify_after_agent": self.verify_after,
            "run_voi_and_followups": self.voi,
            "sensitivity_runs": self.sensitivity,
            "fna_analyse": lambda doc, *, today: {"needs": {"retirement": "shortfall"}, "as_of": today.isoformat()},
            "run_forecast": self.forecast,
            "extract_followup": self.extract_followup,
        }


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[Notification] = []

    async def send(self, n: Notification) -> str:
        self.sent.append(n)
        return "log"


@pytest.fixture
async def ctx(store: Store, objects):
    doc_id = await store.save_client_document("cl-1", sample_document(), source="import")
    audio = await objects.put_bytes("audio/call-1/call.wav", b"RIFF-call-audio")
    await store.upsert_call(
        CallRecord(
            call_id="call-1",
            advisor_id="adv-1",
            client_ids=["cl-1"],
            started_at=NOW,
            audio_uri=audio,
            meta={"advisor_email": "adv@practice.co.za", "prior_document_id": doc_id},
        )
    )
    fakes = Fakes()
    notifier = RecordingNotifier()
    acts = PipelineActivities(
        store=store,
        objects=objects,
        settings=Settings(),
        notifier=notifier,  # type: ignore[arg-type]
        stages=StageFunctions(fakes.overrides()),
        providers=Providers(asr=lambda p, s: "asr", diarizer=lambda p, s: "diarizer", second_pass=lambda p, s: None),
    )
    return SimpleNamespace(store=store, objects=objects, acts=acts, fakes=fakes, notifier=notifier)


def env(run_id: str = RUN, execution: str = "exec-1", activity_id: str = "1", heartbeats: list | None = None) -> ActivityEnvironment:
    e = ActivityEnvironment()
    e.info = dataclasses.replace(
        e.info, workflow_id=run_id, workflow_run_id=execution, activity_id=activity_id, heartbeat_timeout=timedelta(seconds=3)
    )
    if heartbeats is not None:
        e.on_heartbeat = lambda *d: heartbeats.append(d)
    return e


def stage_in(run_id: str = RUN, **kw) -> C.StageInput:
    return C.StageInput(run_id=run_id, args=CallArgs(call_id="call-1", advisor_id="adv-1", client_ids=["cl-1"], **kw))


async def _through_forecast(c, run_id: str = RUN) -> None:
    e = env(run_id)
    await e.run(c.acts.ingest_audio, stage_in(run_id))
    await e.run(c.acts.run_discourse, stage_in(run_id))
    await e.run(c.acts.run_agent, stage_in(run_id))
    await e.run(c.acts.run_voi_and_followups, stage_in(run_id))
    await e.run(c.acts.run_forecast, stage_in(run_id))


# --------------------------------------------------------------------------- stages 1–2: cached per stage versions


async def test_ingest_is_cached_by_asr_versions_and_never_retranscribes(ctx):
    beats: list = []
    out = await env(heartbeats=beats).run(ctx.acts.ingest_audio, stage_in())
    assert not out.cached and out.quality.n_speakers == 2 and not out.manual and beats
    assert ctx.fakes.calls["ingest"] == 1
    meta = ctx.fakes.ingest_meta
    assert meta.call_id == "call-1" and meta.started_at == NOW and meta.client_ids == ["cl-1"] and meta.anchor == date(2026, 10, 16)
    assert (await ctx.store.load_transcript("call-1", out.cache_key)) is not None
    assert await ctx.objects.exists(f"transcripts/call-1/{out.cache_key}.json")
    # Another run with a new forecast pin reuses the transcript …
    again = await env("call-1-run-b").run(ctx.acts.ingest_audio, stage_in("call-1-run-b", versions=VersionPins(cma_version="2027Q1")))
    assert again.cached and again.cache_key == out.cache_key and ctx.fakes.calls["ingest"] == 1
    # … a new ASR model does not.
    new = await env("call-1-run-c").run(ctx.acts.ingest_audio, stage_in("call-1-run-c", versions=VersionPins(asr_model_version="fake-2")))
    assert not new.cached and new.cache_key != out.cache_key and ctx.fakes.calls["ingest"] == 2


async def test_discourse_is_cached_and_survives_extraction_prompt_changes(ctx):
    await env().run(ctx.acts.ingest_audio, stage_in())
    first = await env().run(ctx.acts.run_discourse, stage_in())
    pins = VersionPins(prompt_versions={"extract_slot_group": "v7"})
    second = await env("call-1-run-b").run(ctx.acts.run_discourse, stage_in("call-1-run-b", versions=pins))
    assert second.cached and second.cache_key == first.cache_key and ctx.fakes.calls["discourse"] == 1
    third = await env("call-1-run-c").run(
        ctx.acts.run_discourse, stage_in("call-1-run-c", versions=VersionPins(prompt_versions={"rewrite": "v2"}))
    )
    assert not third.cached and ctx.fakes.calls["discourse"] == 2


# --------------------------------------------------------------------------- stages 3–6: the agent


async def test_agent_resumes_from_its_checkpoint_after_a_crash(ctx):
    e = env()
    await e.run(ctx.acts.ingest_audio, stage_in())
    await e.run(ctx.acts.run_discourse, stage_in())
    await PostgresGlossaryStore(ctx.store.db).put(
        GlossaryEntry(term="10X RA", canonical_name="10X Retirement Annuity", provider="10X", confirmed_by="adv-1", confirmed_at=NOW)
    )
    ctx.fakes.crash_after_turn = 2
    ctx.fakes.agent_followups = True
    with pytest.raises(RuntimeError, match="worker crashed"):
        await e.run(ctx.acts.run_agent, stage_in())
    outcome = await e.run(ctx.acts.run_agent, stage_in())  # Temporal's retry: same execution, same scope
    assert outcome.turns == 3 and ctx.fakes.resumed_from == [0, 2]
    inputs = ctx.fakes.agent_inputs
    assert isinstance(inputs, AgentInputs) and inputs.run_id == RUN and inputs.profile.facts and inputs.quality.n_speakers == 2
    assert inputs.transcript.segment("s0002").rewritten_text.startswith("The client would like")
    assert inputs.budgets.orchestrator_requests == Settings().budgets.orchestrator_requests and inputs.budgets.scaled_for_minutes
    # The harness gets the client's prior file, the call length and the practice glossary (§7.2, §8.5, §13.6).
    assert inputs.base_document.client.firstName == "Thabo" and inputs.crm is None
    assert inputs.call_minutes == pytest.approx(inputs.transcript.duration / 60)
    assert inputs.glossary == ["10X RA = 10X Retirement Annuity (10X)"]
    assert isinstance(ctx.fakes.agent_services["glossary_store"], SyncGlossaryStore)
    profile = await ctx.store.load_profile(RUN)
    assert profile.facts[RET_AGE].status == "stated" and profile.facts[RET_AGE].prior_value is not None
    assert [q.followup_id for q in profile.open_questions] == ["fu.retirement_age"]  # the agent's drafts, until VOI
    # Stage 5 ran after the loop on the cold-start calibrator, without thresholds (nothing auto-accepted).
    assert profile.facts[RET_AGE].p_correct == 0.91 and ctx.fakes.verified_with == (None, None)
    assert await ctx.objects.exists(f"profiles/{RUN}/v{profile.version}.json")
    # Done is done: a later attempt returns the recorded outcome without running the agent.
    assert (await e.run(ctx.acts.run_agent, stage_in())).turns == 3 and ctx.fakes.calls["agent"] == 2
    # A reset (a new execution) runs the agent again, from a clean checkpoint.
    await env(execution="exec-2").run(ctx.acts.run_agent, stage_in())
    assert ctx.fakes.resumed_from[-1] == 0


async def test_checks_after_the_agent_use_the_pinned_calibrator_and_retry_without_the_loop(ctx):
    e = env()
    await e.run(ctx.acts.ingest_audio, stage_in())
    await e.run(ctx.acts.run_discourse, stage_in())
    artifact = await ctx.objects.put_bytes("calibration/calibrators/cal-3.bin", FakeCalibrator("cal-3").to_bytes())
    thresholds = await ctx.objects.put_json("calibration/conformal/cal-3/c1.json", Thresholds(version="c1", enabled=True, tau_pooled=0.9))
    await ctx.store.register_calibrator(
        CalibratorRecord(version="cal-3", artifact_uri=artifact, metrics={"thresholds_uri": thresholds}, promoted=True)
    )
    ctx.acts.stages.overrides.update(calibrator_type=FakeCalibrator, conformal_thresholds=Thresholds)
    pins = VersionPins(calibrator_version="cal-3")
    ctx.fakes.verify_failures = 1
    with pytest.raises(RuntimeError, match="verifier timed out"):
        await e.run(ctx.acts.run_agent, stage_in(versions=pins))
    outcome = await e.run(ctx.acts.run_agent, stage_in(versions=pins))  # the retry runs the checks, not the loop again
    assert outcome.turns == 3 and ctx.fakes.calls["agent"] == 1 and ctx.fakes.calls["verify"] == 2
    calibrator, conformal = ctx.fakes.verified_with
    assert calibrator.version == "cal-3" and conformal.enabled and conformal.tau_pooled == 0.9
    with pytest.raises(ApplicationError, match="not in the registry") as ei:
        await env(execution="exec-2").run(ctx.acts.run_agent, stage_in(versions=VersionPins(calibrator_version="cal-404")))
    assert ei.value.non_retryable


async def test_voi_saves_followups_flags_and_a_role_review_flag(ctx):
    ctx.fakes.role_review = True
    e = env()
    await e.run(ctx.acts.ingest_audio, stage_in())
    await e.run(ctx.acts.run_discourse, stage_in())
    await e.run(ctx.acts.run_agent, stage_in())
    out = await e.run(ctx.acts.run_voi_and_followups, stage_in())
    assert out.n_followups == 1 and out.n_flags == 2
    profile = await ctx.store.load_profile(RUN)
    assert [f.kind for f in profile.blocking_flags] == ["role_review", "tier_a_contradicted"]
    assert profile.facts[RET_AGE].review_priority == 0.09 and profile.open_questions[0].followup_id == "fu.retirement_age"
    # The sensitivity runs saw the client's prior file (§10.2).
    assert [d.client.firstName for d in ctx.fakes.sensitivity_base] == ["Thabo"]


async def test_voi_runs_without_the_forecast_and_fna_engines(ctx):
    seen: dict[str, Any] = {}

    async def voi(profile, *, pos_runs, analyse, **kw):
        seen.update(pos_runs=pos_runs, analyse=analyse)
        return SimpleNamespace(profile=profile, followups=[], flags=[])

    table = {k: v for k, v in ctx.acts.stages.table.items() if k not in ("sensitivity_runs", "fna_analyse")}
    overrides = {k: v for k, v in ctx.fakes.overrides().items() if k not in ("sensitivity_runs", "fna_analyse")}
    ctx.acts.stages = StageFunctions({**overrides, "run_voi_and_followups": voi}, table=table)
    e = env()
    for step in (ctx.acts.ingest_audio, ctx.acts.run_discourse, ctx.acts.run_agent, ctx.acts.run_voi_and_followups):
        await e.run(step, stage_in())
    assert seen == {"pos_runs": None, "analyse": None}


async def test_forecast_is_saved_once_per_profile_version_and_mode(ctx):
    await _through_forecast(ctx)
    rec = await ctx.store.get_forecast(RUN, mode="preview")
    assert rec.result["pos"] == 0.7 and rec.result["seed"] > 0
    e = env()
    again = await e.run(ctx.acts.run_forecast, stage_in())
    assert again.cached and ctx.fakes.calls["forecast"] == 1
    final = await e.run(ctx.acts.run_forecast, stage_in(forecast_mode="final"))
    assert final.mode == "final" and ctx.fakes.calls["forecast"] == 2


async def test_rerun_from_a_later_stage_starts_from_the_source_runs_profile(ctx):
    await _through_forecast(ctx)
    source = await ctx.store.load_profile(RUN)
    await ctx.store.register_run("call-1-run-b", "call-1", VersionPins(cma_version="2027Q1"), start_stage=Stage.FORECAST, source_run_id=RUN)
    out = await env("call-1-run-b").run(
        ctx.acts.run_forecast, stage_in("call-1-run-b", start_stage=Stage.FORECAST, versions=VersionPins(cma_version="2027Q1"))
    )
    copy = await ctx.store.load_profile("call-1-run-b")
    assert copy.facts == source.facts and copy.run_id == "call-1-run-b" and out.profile_version == source.version
    assert "ingest" not in ctx.fakes.calls or ctx.fakes.calls["ingest"] == 1


# --------------------------------------------------------------------------- review, follow-ups, note, notifications


async def test_apply_review_with_file_exchange_ops(ctx):
    await _through_forecast(ctx)
    profile = await ctx.store.load_profile(RUN)
    ops = make_ops(
        profile,
        [
            ("CONFIRM", RET_AGE, None, {}, "reviewer"),
            ("RETRACT", GROSS, None, {"reason": "misheard", "note": "it was 58k"}, "reviewer"),
            ("CONFIRM", "no.such#fact", None, {}, "reviewer"),
        ],
    )
    review = ReviewSubmission(call_id="call-1", reviewer_id="adv-1", profile_version=profile.version, submitted_at=NOW, ops=ops)
    out = await env().run(ctx.acts.apply_review, C.ReviewInput(run_id=RUN, review=review))
    assert out.applied == 2 and out.rejected == 1 and out.profile_version == profile.version + 1
    assert out.changes.get("changed", 0) >= 1
    reviewed = await ctx.store.load_profile(RUN)
    assert reviewed.facts[RET_AGE].review.status == "accepted" and reviewed.facts[GROSS].review.status == "rejected"
    assert [v.frozen for v in await ctx.store.profile_versions(RUN)][-1] is True
    events = await ctx.store.review_events(run_id=RUN)
    assert [e.action for e in events] == ["accept", "reject", "submit"]
    reject = events[1]
    assert (
        reject.reason == "misheard"
        and reject.detail["fact_before"]["review"]["status"] == "pending"
        and reject.detail["note"] == "it was 58k"
    )
    # The reviewed document becomes the client's prior for the next call.
    latest = await ctx.store.latest_client_document("cl-1")
    assert latest.retirement["client"].retirementAge == 62
    again = await env().run(ctx.acts.apply_review, C.ReviewInput(run_id=RUN, review=review))
    assert again.cached and len(await ctx.store.review_events(run_id=RUN)) == 3


async def test_a_retry_after_a_crash_does_not_apply_ops_twice(ctx, pg_dsn):
    """The ops were written but the attempt died before recording its output: the retry must not repeat them."""
    import psycopg

    await _through_forecast(ctx)
    profile = await ctx.store.load_profile(RUN)
    ops = make_ops(profile, [("CONFIRM", RET_AGE, None, {}, "reviewer")])
    review = ReviewSubmission(call_id="call-1", reviewer_id="adv-1", profile_version=profile.version, submitted_at=NOW, ops=ops)
    answer = FollowupAnswer(call_id="call-1", followup_id="fu.retirement_age", text="Sixty-three", answered_at=NOW)
    await env().run(ctx.acts.intake_followup_answer, C.AnswerInput(run_id=RUN, args=CallArgs(call_id="call-1"), answer=answer))
    await env().run(ctx.acts.apply_review, C.ReviewInput(run_id=RUN, review=review))
    before = await ctx.store.load_profile(RUN)
    async with await psycopg.AsyncConnection.connect(pg_dsn, autocommit=True) as conn:
        await conn.execute("DELETE FROM stage_outputs WHERE stage IN ('review', 'intake')")  # the crash: no recorded outputs
    out = await env().run(ctx.acts.apply_review, C.ReviewInput(run_id=RUN, review=review))
    intake = await env().run(ctx.acts.intake_followup_answer, C.AnswerInput(run_id=RUN, args=CallArgs(call_id="call-1"), answer=answer))
    after = await ctx.store.load_profile(RUN)
    assert [o.seq for o in after.ops_log] == [o.seq for o in before.ops_log] and out.applied == 0 and intake.applied == 1
    assert [e.action for e in await ctx.store.review_events(run_id=RUN)] == ["accept", "submit"]


async def test_followup_answer_goes_through_the_reducer_once(ctx):
    await _through_forecast(ctx)
    answer = FollowupAnswer(call_id="call-1", followup_id="fu.retirement_age", text="Sixty-three is realistic", answered_at=NOW)
    inp = C.AnswerInput(run_id=RUN, args=CallArgs(call_id="call-1"), answer=answer)
    out = await env().run(ctx.acts.intake_followup_answer, inp)
    assert out.applied == 1 and not out.duplicate
    profile = await ctx.store.load_profile(RUN)
    fact = profile.facts[RET_AGE]
    assert any(e.segment_id == "fu-fu.retirement_age" for e in fact.evidence) and len(fact.hypotheses) == 2
    assert profile.ops_log[-1].payload["source"] == "followup"
    assert (await ctx.store.get_followup(RUN, "fu.retirement_age")).status == "answered"
    dup = await env().run(ctx.acts.intake_followup_answer, inp)
    assert dup.duplicate and ctx.fakes.calls["extract_followup"] == 1
    with pytest.raises(ApplicationError, match="no follow-up"):
        await env().run(
            ctx.acts.intake_followup_answer, inp.model_copy(update={"answer": answer.model_copy(update={"followup_id": "fu.nope"})})
        )


async def test_meeting_note_is_rendered_and_written_into_the_reviewed_document(ctx):
    await _through_forecast(ctx)
    out = await env().run(ctx.acts.render_meeting_note, stage_in())
    assert out.sentences >= 2 and out.removed == 0
    version, note = await ctx.store.latest_note(RUN)
    assert note["sentences"] and version == out.profile_version
    doc = await ctx.store.latest_client_document("cl-1")
    assert "retirement age" in doc.advice.summary.lower()


async def test_notifications_are_sent_once_per_scheduled_activity(ctx):
    await _through_forecast(ctx)
    inp = C.NotifyInput(run_id=RUN, args=CallArgs(call_id="call-1"), kind="review_ready", partial=True)
    first = await env(activity_id="7").run(ctx.acts.notify_advisor, inp)
    retry = await env(activity_id="7").run(ctx.acts.notify_advisor, inp)
    assert first.sent and not first.duplicate and retry.duplicate and len(ctx.notifier.sent) == 1
    n = ctx.notifier.sent[0]
    assert (
        n.recipient == "adv@practice.co.za" and "Thabo Mokoena" in n.subject and n.data["open_flags"] == 1 and n.data["open_followups"] == 1
    )
    await env(activity_id="9").run(ctx.acts.send_review_reminder, inp.model_copy(update={"kind": "review_reminder", "reminder_number": 1}))
    assert len(ctx.notifier.sent) == 2 and ctx.notifier.sent[1].subject.startswith("Reminder")


async def test_missing_call_is_a_non_retryable_input_error(ctx):
    with pytest.raises(ApplicationError) as ei:
        await env().run(ctx.acts.ingest_audio, C.StageInput(run_id="x-run", args=CallArgs(call_id="nope")))
    assert ei.value.non_retryable


# --------------------------------------------------------------------------- nightly


class Label(BaseModel):
    """The shape of a calibration label the fakes below exchange (``verify.labels.Label`` in a full install)."""

    call_id: str
    run_id: str
    fact_id: str
    correct: int
    p_correct: float
    calibrator_version: str = ""


@dataclasses.dataclass
class FakeCalibrator:
    version: str
    metrics: dict = dataclasses.field(default_factory=dict)

    def to_bytes(self) -> bytes:
        return f"calibrator:{self.version}".encode()

    @staticmethod
    def from_bytes(blob: bytes) -> FakeCalibrator:
        return FakeCalibrator(version=blob.decode().removeprefix("calibrator:"))

    def experiment(self) -> dict:
        return {"name": f"calibration/{self.version}", "metrics": self.metrics}


class Thresholds(BaseModel):
    version: str = "off"
    enabled: bool = False
    reason: str = ""
    n_calls: int = 0
    tau_pooled: float | None = None
    family_alpha: float = 0.02
    tau_by_family: dict[str, float | None] = {}
    n_by_family: dict[str, int] = {}
    source_by_family: dict[str, str] = {}


async def test_nightly_labels_retrain_and_refit(ctx):
    import numpy as np

    await _through_forecast(ctx)
    voi_version = (await ctx.store.load_profile(RUN)).version
    profile = await ctx.store.load_profile(RUN)
    ops = make_ops(profile, [("CONFIRM", RET_AGE, None, {}, "reviewer"), ("RETRACT", GROSS, None, {"reason": "misheard"}, "reviewer")])
    review = ReviewSubmission(
        call_id="call-1", reviewer_id="adv-1", profile_version=profile.version, submitted_at=NOW, ops=ops, spot_check=True
    )
    await env().run(ctx.acts.apply_review, C.ReviewInput(run_id=RUN, review=review))
    seen: dict[str, Any] = {}

    def build_labels(before, ops, *, after, spot_check):
        seen["labels"] = (before.version, [o.op for o in ops], after.version, spot_check)
        assert not any(o.actor == "reviewer" for o in before.ops_log)
        rows = [
            Label(call_id="call-1", run_id=RUN, fact_id=RET_AGE, correct=1, p_correct=0.97, calibrator_version="cal-1"),
            Label(call_id="call-1", run_id=RUN, fact_id=GROSS, correct=0, p_correct=0.4),  # scored before any calibrator
        ]
        return SimpleNamespace(labels=rows, recall_misses=[])

    def retrain(labels, *, incumbent, pins):
        seen.setdefault("retrain", []).append((len(labels), incumbent, pins.calibrator_version))
        assert all(isinstance(lb, Label) for lb in labels)
        if incumbent is not None:
            return SimpleNamespace(candidate=None, promoted=False, reason="not due")
        cal = FakeCalibrator("cal-1", metrics={"overall": {"ece": np.float64(0.04)}})
        return SimpleNamespace(candidate=cal, promoted=True, reason="no incumbent", trigger="first", candidate_ece=np.float64(0.04))

    def fit_conformal(labels, *, alpha, min_calls, calibrator_version, settings):
        seen["refit"] = ([lb.fact_id for lb in labels], alpha, min_calls, calibrator_version)
        return Thresholds(
            version="conformal-55-abc",
            enabled=True,
            reason="55 calibration calls",
            n_calls=55,
            tau_pooled=0.9,
            tau_by_family={"household": 0.93, "goals": None},
            n_by_family={"household": 55, "goals": 3},
            source_by_family={"household": "family", "goals": "pooled"},
        )

    stages = StageFunctions(
        {
            "build_labels": build_labels,
            "calibration_label": Label,
            "retrain_calibrator": retrain,
            "calibrator_type": FakeCalibrator,
            "refit_conformal": fit_conformal,
        }
    )
    nightly = NightlyActivities(store=ctx.store, objects=ctx.objects, settings=Settings(), stages=stages)
    window = C.NightlyWindow(since=NOW - timedelta(hours=1), until=NOW + timedelta(hours=1))
    e = env("nightly")
    labels = await e.run(nightly.build_calibration_labels, window)
    assert labels.n_events == 3 and labels.n_labels == 2 and labels.n_calls == 1 and labels.labels_uri
    # The profile as VOI left it, the reviewer's ops, and the frozen reviewed profile.
    assert seen["labels"] == (voi_version, ["CONFIRM", "RETRACT"], voi_version + 1, True)
    cal = await e.run(nightly.retrain_calibrator, labels)
    assert (
        cal.version == "cal-1" and cal.promoted and cal.metrics["overall"]["ece"] == 0.04 and seen["retrain"] == [(2, None, "cold-start")]
    )
    assert await ctx.objects.get_bytes(cal.artifact_uri) == b"calibrator:cal-1"
    assert (await ctx.objects.get_json("calibration/experiments/cal-1.json"))["name"] == "calibration/cal-1"
    conf = await e.run(nightly.refit_conformal, cal)
    # Only the calls the promoted calibrator scored are its calibration set (§9.5).
    assert seen["refit"] == ([RET_AGE], 0.1, 50, "cal-1")
    assert conf.tau == {"*": 0.9, "household": 0.93} and conf.auto_accept_enabled and conf.n_calib_calls == 55
    assert (await ctx.objects.get_json(conf.thresholds_uri))["tau_by_family"] == {"household": 0.93, "goals": None}
    rows = {r.slot_group: r for r in await ctx.store.calibrators(version="cal-1")}
    assert (
        rows["*"].artifact_uri == cal.artifact_uri and rows["*"].tau == 0.9 and rows["*"].metrics["thresholds_uri"] == conf.thresholds_uri
    )
    assert rows["household"].tau == 0.93 and rows["household"].alpha == 0.02 and all(r.promoted for r in rows.values())
    # The next night: the promoted calibrator is the incumbent, and the calibrator code says no retrain is due.
    again = await e.run(nightly.retrain_calibrator, labels)
    assert again.skipped_reason == "not due" and again.version == "cal-1" and seen["retrain"][-1][1].version == "cal-1"


async def test_nightly_lexicon_fit_proposes_a_new_version_from_reviewed_edits(store: Store, objects):
    """The real lexicon fit: edits of an 'early sixties' retirement age re-fit that entry (§11.4 step 2)."""
    from advisor_intel.lexicon import Lexicon, load_lexicon, normalize_field
    from advisor_intel.models.extraction import AgeForm, FieldValue, RawHypothesis
    from advisor_intel.models.facts import Fact
    from advisor_intel.schema.slots import load_catalog
    from advisor_intel.storage import ReviewEvent

    slot = load_catalog().slot("retirement.{p}.retirementAge")
    form = AgeForm(form="vague", value=0, low=0, high=0, vague_word="early sixties")
    fv = FieldValue(
        fna_path="retirement.client.retirementAge",
        hypotheses=[RawHypothesis(intermediate=form, weight=0, rationale="'early sixties'", revises=False)],
        hedges=[],
        speaker_attribution="client",
        said="in my early sixties",
        interpretation="target retirement age",
        self_score=0.8,
    )
    nf = normalize_field(fv, slot, None, load_lexicon())
    fact = Fact(
        fact_id="retirement.client.retirementAge",
        slot_id=slot.slot_id,
        fna_path="retirement.client.retirementAge",
        status="stated",
        value=nf.hypotheses[0].value,
        normalization=nf.normalization,
        provenance=Provenance(run_id="r"),
    )
    edits = [64, 65, 64, 66, 65, 64, 65, 66, 64, 65, 65, 64, 66, 65, 64, 65, 66, 64, 65, 65]
    events = []
    for i, age in enumerate(edits):
        after = fact.model_copy(update={"value": Quantity(dist=Point(value=age), unit="age")})
        events.append(
            ReviewEvent(
                workflow_id=f"run-{i}",
                fact_id=fact.fact_id,
                action="edit",
                reviewer_id="adv-1",
                at=NOW,
                detail={"fact_before": fact.model_dump(mode="json"), "fact_after": after.model_dump(mode="json")},
            )
        )
    await store.record_review_events(events)
    nightly = NightlyActivities(store=store, objects=objects, settings=Settings(), stages=StageFunctions())
    window = C.NightlyWindow(since=NOW - timedelta(hours=1), until=NOW + timedelta(hours=1))
    out = await env("nightly").run(nightly.propose_lexicon_fit, window)
    assert out.n_pairs == 20 and out.keys_fitted == ["age_bands: early sixties"] and out.base_version == "1.3"
    proposed = Lexicon.from_text((await objects.get_bytes(out.proposal_uri)).decode())
    assert (
        proposed.version == out.new_version != "1.3"
        and proposed.data.age_bands["early sixties"] != load_lexicon().data.age_bands["early sixties"]
    )
    assert b"early sixties" in await objects.get_bytes(out.diff_uri)


async def test_dataset_sync_with_a_key(store: Store, objects):
    from advisor_intel.storage import ReviewEvent

    before = {"slot_id": "dependants[].dateOfBirth", "evidence": [{"quote": "she was born in 2012"}]}
    await store.record_review_events(
        [
            ReviewEvent(
                workflow_id="r2",
                fact_id="f2",
                action="reject",
                reason="wrong_person",
                reviewer_id="a",
                at=NOW,
                detail={"fact_before": before},
            ),
            ReviewEvent(
                workflow_id="r2",
                action="edit_followup",
                reason="reword",
                reviewer_id="a",
                at=NOW,
                detail={"edit": {"question": "When was Neo born?"}},
            ),
        ]
    )
    got: dict[str, Any] = {}

    def sink(name):
        def fn(rows, *, settings):
            got[name] = rows
            return len(rows)

        return fn

    stages = StageFunctions({"sync_error_dataset": sink("errors"), "sync_gold_candidates": sink("gold")})
    nightly = NightlyActivities(store=store, objects=objects, settings=Settings(braintrust_api_key="bt"), stages=stages)
    window = C.NightlyWindow(since=NOW - timedelta(hours=1), until=NOW + timedelta(hours=1))
    errs = await env("nightly").run(nightly.sync_error_dataset, window)
    gold = await env("nightly").run(nightly.sync_gold_candidates, window)
    assert errs.inserted == 1 and got["errors"][0]["reason"] == "wrong_person" and got["errors"][0]["quotes"] == ["she was born in 2012"]
    assert gold.inserted == 1 and got["gold"][0]["followup_edits"] == [{"question": "When was Neo born?"}]
    assert json.dumps(got["gold"])  # rows are plain JSON
