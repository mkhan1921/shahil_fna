"""Pipeline and nightly activities against local Postgres and object storage, with fake stage functions.

The stage code (ingest, discourse, harness, VOI, forecast, extraction, calibration) is replaced by
fakes through ``StageFunctions`` overrides; everything around it is real: storage, caching and
idempotency, checkpoints, the reducer, review events and notifications.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from advisor_intel import binding
from advisor_intel.activities import NightlyActivities, PipelineActivities, StageFunctions
from advisor_intel.activities.providers import Providers
from advisor_intel.config import Settings
from advisor_intel.extract.reducer import annotate, make_ops
from advisor_intel.models.dists import Point, Quantity
from advisor_intel.models.entities import DiscourseOutput
from advisor_intel.models.facts import Evidence, Hypothesis, Provenance
from advisor_intel.models.forecast import ForecastResult
from advisor_intel.models.profile import StateOp
from advisor_intel.models.runs import AgentOutcome, CallArgs, FollowupAnswer, ReviewSubmission, Stage, VersionPins
from advisor_intel.notify.notifier import Notification
from advisor_intel.storage import CallRecord, Store
from advisor_intel.workflows import contracts as C

from .factories import GROSS, RET_AGE, flagged, make_transcript, sample_document, with_call_facts

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
RUN = "call-1-run-a"


@dataclass
class AgentInputs:
    call_id: str
    run_id: str
    transcript: Any
    discourse: Any
    profile: Any
    pins: Any
    budgets: Any
    quality: Any


class Fakes:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.agent_inputs: AgentInputs | None = None
        self.resumed_from: list[int] = []
        self.crash_after_turn: int | None = None
        self.role_review = False

    def count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    async def ingest(self, audio, meta, *, asr, diarizer, role_agent_pins, second_pass=None, prior_fna=None, crm_names=None):
        self.count("ingest")
        assert audio.read_bytes() == b"RIFF-call-audio" and meta["advisor_id"] == "adv-1" and asr == "asr" and diarizer == "diarizer"
        assert prior_fna is not None and prior_fna.client.firstName == "Thabo"
        await asyncio.sleep(0.05)
        t = make_transcript(meta["call_id"], role_agent_pins.asr_model_version)
        if self.role_review:
            t = t.model_copy(update={"quality": t.quality.model_copy(update={"role_review_needed": True})})
        return t

    async def discourse(self, transcript, *, prior, crm, pins, catalog):
        self.count("discourse")
        return DiscourseOutput(entities=prior.entities if prior else {}, rewrites={"s0002": "The client would like to retire at sixty, maybe sixty-five."})

    async def agent(self, inputs, *, checkpoints, heartbeat):
        self.count("agent")
        self.agent_inputs = inputs
        cp = await checkpoints.load(inputs.run_id)
        start = cp.turn if cp else 0
        self.resumed_from.append(start)
        for turn in range(start + 1, 4):
            heartbeat(turn)
            await checkpoints.save(inputs.run_id, turn, [{"turn": t} for t in range(1, turn + 1)], {"turn": turn})
            if self.crash_after_turn == turn:
                self.crash_after_turn = None
                raise RuntimeError("worker crashed")
        return SimpleNamespace(outcome=AgentOutcome(turns=3), profile=with_call_facts(inputs.profile))

    def voi(self, profile, *, catalog, pins, pos_runs, analyse, transcript, settings):
        self.count("voi")
        assert analyse(binding.materialise(profile)) == {"needs": {"retirement": "shortfall"}} and pos_runs == "sensitivity"
        prof = annotate(profile, RET_AGE, review_priority=0.09, voi={"I_content": 0.09})
        f = flagged(prof)
        return SimpleNamespace(profile=prof, followups=f.open_questions, flags=f.blocking_flags, priorities={RET_AGE: 0.09})

    def forecast(self, profile, *, base_document=None, mode="preview", seed=None):
        self.count("forecast")
        assert base_document is not None and seed is not None
        return ForecastResult(
            run_id=profile.run_id, profile_version=str(profile.version), config_version="config_v1", cma_version="2026Q4", n_paths=1000, seed=seed, pos=0.7,
            pos_ci=(0.67, 0.73),
        )  # fmt: skip

    async def extract_followup(self, profile, transcript, *, followup, pins, catalog):
        self.count("extract_followup")
        seg = transcript.segments[0]
        ev = Evidence(
            segment_id=seg.segment_id, char_start=0, char_end=len(seg.text), quote=seg.text, speaker_role="client_primary", t_start=0, t_end=1,
            asr_mean_conf=1.0, role="supports",
        )  # fmt: skip
        payload = {
            "slot_id": "retirement.{p}.retirementAge",
            "instance_key": "client",
            "fna_path": "retirement.client.retirementAge",
            "status": "stated",
            "hypotheses": [Hypothesis(value=Quantity(dist=Point(value=63), unit="age"), weight=1.0, rationale="answered 63").model_dump(mode="json")],
            "evidence": [ev.model_dump(mode="json")],
            "entity_refs": ["person.client_primary"],
            "provenance": Provenance(run_id=profile.run_id).model_dump(mode="json"),
        }
        return [StateOp(op="ADD", fact_id=RET_AGE, segment_id=seg.segment_id, payload=payload, actor="extractor", seq=0)]

    def overrides(self) -> dict[str, Any]:
        return {
            "ingest_call": self.ingest,
            "run_discourse": self.discourse,
            "agent_inputs": AgentInputs,
            "run_agent": self.agent,
            "run_voi_and_followups": self.voi,
            "sensitivity_runs": "sensitivity",
            "fna_analyse": lambda doc: {"needs": {"retirement": "shortfall"}},
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
        CallRecord(call_id="call-1", advisor_id="adv-1", client_ids=["cl-1"], started_at=NOW, audio_uri=audio, meta={"advisor_email": "adv@practice.co.za", "prior_document_id": doc_id})
    )
    fakes = Fakes()
    notifier = RecordingNotifier()
    acts = PipelineActivities(
        store=store,
        objects=objects,
        settings=Settings(),
        notifier=notifier,  # type: ignore[arg-type]
        stages=StageFunctions(fakes.overrides()),
        providers=Providers(asr=lambda s, p: "asr", diarizer=lambda s, p: "diarizer", second_pass=lambda s, p: None),
    )
    return SimpleNamespace(store=store, objects=objects, acts=acts, fakes=fakes, notifier=notifier)


def env(run_id: str = RUN, execution: str = "exec-1", activity_id: str = "1", heartbeats: list | None = None) -> ActivityEnvironment:
    e = ActivityEnvironment()
    e.info = dataclasses.replace(e.info, workflow_id=run_id, workflow_run_id=execution, activity_id=activity_id, heartbeat_timeout=timedelta(seconds=3))
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
    third = await env("call-1-run-c").run(ctx.acts.run_discourse, stage_in("call-1-run-c", versions=VersionPins(prompt_versions={"rewrite": "v2"})))
    assert not third.cached and ctx.fakes.calls["discourse"] == 2


# --------------------------------------------------------------------------- stages 3–6: the agent


async def test_agent_resumes_from_its_checkpoint_after_a_crash(ctx):
    e = env()
    await e.run(ctx.acts.ingest_audio, stage_in())
    await e.run(ctx.acts.run_discourse, stage_in())
    ctx.fakes.crash_after_turn = 2
    with pytest.raises(RuntimeError, match="worker crashed"):
        await e.run(ctx.acts.run_agent, stage_in())
    outcome = await e.run(ctx.acts.run_agent, stage_in())  # Temporal's retry: same execution, same scope
    assert outcome.turns == 3 and ctx.fakes.resumed_from == [0, 2]
    inputs = ctx.fakes.agent_inputs
    assert inputs.run_id == RUN and inputs.profile.facts and inputs.discourse is not None and inputs.quality.n_speakers == 2
    assert inputs.transcript.segment("s0002").rewritten_text.startswith("The client would like")
    assert inputs.budgets.orchestrator_requests == Settings().budgets.orchestrator_requests
    profile = await ctx.store.load_profile(RUN)
    assert profile.facts[RET_AGE].status == "stated" and profile.facts[RET_AGE].prior_value is not None
    assert await ctx.objects.exists(f"profiles/{RUN}/v{profile.version}.json")
    # Done is done: a later attempt returns the recorded outcome without running the agent.
    assert (await e.run(ctx.acts.run_agent, stage_in())).turns == 3 and ctx.fakes.calls["agent"] == 2
    # A reset (a new execution) runs the agent again, from a clean checkpoint.
    await env(execution="exec-2").run(ctx.acts.run_agent, stage_in())
    assert ctx.fakes.resumed_from[-1] == 0


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
    out = await env("call-1-run-b").run(ctx.acts.run_forecast, stage_in("call-1-run-b", start_stage=Stage.FORECAST, versions=VersionPins(cma_version="2027Q1")))
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
    assert reject.reason == "misheard" and reject.detail["fact_before"]["review"]["status"] == "pending" and reject.detail["note"] == "it was 58k"
    # The reviewed document becomes the client's prior for the next call.
    latest = await ctx.store.latest_client_document("cl-1")
    assert latest.retirement["client"].retirementAge == 62
    again = await env().run(ctx.acts.apply_review, C.ReviewInput(run_id=RUN, review=review))
    assert again.cached and len(await ctx.store.review_events(run_id=RUN)) == 3


async def test_followup_answer_goes_through_the_reducer_once(ctx):
    await _through_forecast(ctx)
    answer = FollowupAnswer(call_id="call-1", followup_id="fu.retirement_age", text="Sixty-three is realistic", answered_at=NOW)
    inp = C.AnswerInput(run_id=RUN, args=CallArgs(call_id="call-1"), answer=answer)
    out = await env().run(ctx.acts.intake_followup_answer, inp)
    assert out.applied == 1 and not out.duplicate
    profile = await ctx.store.load_profile(RUN)
    fact = profile.facts[RET_AGE]
    assert any(e.segment_id == "followup:fu.retirement_age" for e in fact.evidence) and len(fact.hypotheses) == 2
    assert profile.ops_log[-1].payload["source"] == "followup"
    assert (await ctx.store.get_followup(RUN, "fu.retirement_age")).status == "answered"
    dup = await env().run(ctx.acts.intake_followup_answer, inp)
    assert dup.duplicate and ctx.fakes.calls["extract_followup"] == 1
    with pytest.raises(ApplicationError, match="no follow-up"):
        await env().run(ctx.acts.intake_followup_answer, inp.model_copy(update={"answer": answer.model_copy(update={"followup_id": "fu.nope"})}))


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
    assert n.recipient == "adv@practice.co.za" and "Thabo Mokoena" in n.subject and n.data["open_flags"] == 1 and n.data["open_followups"] == 1
    await env(activity_id="9").run(ctx.acts.send_review_reminder, inp.model_copy(update={"kind": "review_reminder", "reminder_number": 1}))
    assert len(ctx.notifier.sent) == 2 and ctx.notifier.sent[1].subject.startswith("Reminder")


async def test_missing_call_is_a_non_retryable_input_error(ctx):
    with pytest.raises(ApplicationError) as ei:
        await env().run(ctx.acts.ingest_audio, C.StageInput(run_id="x-run", args=CallArgs(call_id="nope")))
    assert ei.value.non_retryable


# --------------------------------------------------------------------------- nightly


async def test_nightly_labels_retrain_refit_and_lexicon(store: Store, objects):
    from advisor_intel.storage import ReviewEvent

    before = {"slot_id": "retirement.{p}.retirementAge", "normalization": {"lexicon_key": "about", "phrase": "about sixty"}, "value": {"dist": {"kind": "point", "value": 60}}}
    after = {"slot_id": "retirement.{p}.retirementAge", "value": {"dist": {"kind": "point", "value": 62}}}
    await store.record_review_events(
        [
            ReviewEvent(workflow_id="r1", fact_id="f1", action="edit", reviewer_id="a", at=NOW, detail={"fact_before": before, "fact_after": after}),
            ReviewEvent(workflow_id="r2", fact_id="f2", action="reject", reason="misheard", reviewer_id="a", at=NOW, detail={"fact_before": before}),
            ReviewEvent(workflow_id="r2", action="submit", reviewer_id="a", at=NOW),
        ]
    )
    seen: dict[str, Any] = {}

    def build_labels(events):
        seen["events"] = events
        return [{"workflow_id": e["workflow_id"], "fact_id": e["fact_id"], "correct": int(e["action"] == "accept")} for e in events]

    def retrain(labels, *, previous, settings):
        seen["retrain"] = (len(labels), previous)
        return {"version": "cal-2026-10-16", "artifact": b"model-bytes", "metrics": {"ece": 0.04}, "promote": True}

    def refit(labels, *, calibrator, alpha, min_calls):
        seen["refit"] = (calibrator, alpha, min_calls)
        return {"tau": {"*": 0.93, "income_expenses": 0.95}, "n_calib_calls": 55, "enabled": True}

    def propose(pairs, lex):
        seen["pairs"] = pairs
        return {"keys_fitted": ["about"], "yaml": "about: {p10: 0.9}\n", "pull_request": "https://github.com/x/y/pull/7"}

    stages = StageFunctions({"build_labels": build_labels, "retrain_calibrator": retrain, "refit_conformal": refit, "load_lexicon": lambda v: f"lex-{v}", "propose_lexicon_fit": propose})
    nightly = NightlyActivities(store=store, objects=objects, settings=Settings(), stages=stages)
    window = C.NightlyWindow(since=NOW - timedelta(hours=1), until=NOW + timedelta(hours=1))
    e = env("nightly")
    labels = await e.run(nightly.build_calibration_labels, window)
    assert labels.n_events == 3 and labels.n_labels == 2 and labels.n_calls == 2 and labels.labels_uri
    assert [x["action"] for x in seen["events"]] == ["edit", "reject"]
    cal = await e.run(nightly.retrain_calibrator, labels)
    assert cal.version == "cal-2026-10-16" and cal.promoted and seen["retrain"] == (2, None)
    conf = await e.run(nightly.refit_conformal, cal)
    assert conf.tau == {"*": 0.93, "income_expenses": 0.95} and conf.auto_accept_enabled and seen["refit"] == (b"model-bytes", 0.1, 50)
    rows = await store.calibrators(version="cal-2026-10-16")
    assert {r.slot_group: r.tau for r in rows} == {"*": 0.93, "income_expenses": 0.95} and all(r.promoted for r in rows)
    # Fewer than 200 new labels since the promoted calibrator: no retrain.
    again = await e.run(nightly.retrain_calibrator, labels)
    assert again.skipped_reason and again.version == "cal-2026-10-16"
    lex = await e.run(nightly.propose_lexicon_fit, window)
    assert lex.keys_fitted == ["about"] and lex.n_pairs == 1 and lex.pull_request.endswith("/pull/7")
    assert seen["pairs"][0]["lexicon_key"] == "about" and seen["pairs"][0]["edited_value"]["dist"]["value"] == 62
    assert (await objects.get_bytes(lex.proposal_uri)) == b"about: {p10: 0.9}\n"
    skipped = await e.run(nightly.sync_error_dataset, window)
    assert skipped.skipped_reason == "no Braintrust API key"


async def test_dataset_sync_with_a_key(store: Store, objects):
    from advisor_intel.storage import ReviewEvent

    await store.record_review_events(
        [
            ReviewEvent(workflow_id="r2", fact_id="f2", action="reject", reason="wrong_person", reviewer_id="a", at=NOW, detail={"fact_before": {"slot_id": "dependants[].dateOfBirth", "evidence": [{"quote": "she was born in 2012"}]}}),
            ReviewEvent(workflow_id="r2", action="edit_followup", reason="reword", reviewer_id="a", at=NOW, detail={"edit": {"question": "When was Neo born?"}}),
        ]
    )  # fmt: skip
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
