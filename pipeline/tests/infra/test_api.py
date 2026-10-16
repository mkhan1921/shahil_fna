"""The FastAPI backend against local Postgres and object storage, with a fake Temporal gateway (spec §11.5, §14.3)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from advisor_intel.activities.keys import ingest_key
from advisor_intel.activities.stages import StageFunctions
from advisor_intel.api.app import create_app, deployment_pins
from advisor_intel.api.temporal import WorkflowInfo
from advisor_intel.config import Settings
from advisor_intel.extract.reducer import make_ops
from advisor_intel.models.fna import ExportFile
from advisor_intel.models.forecast import ForecastResult
from advisor_intel.models.runs import FollowupAnswer, RunState, Stage, VersionPins
from advisor_intel.note import NoteSentence
from advisor_intel.note.note import MeetingNote
from advisor_intel.storage import CalibratorRecord, CallRecord, PostgresGlossaryStore, Store
from advisor_intel.workflows.contracts import RunStatus

from .factories import GROSS, RET_AGE, flagged, make_transcript, sample_document, seeded_profile, with_call_facts

pytestmark = pytest.mark.postgres

TOKEN = "s3cret-token"
NOW = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)


class FakeGateway:
    def __init__(self) -> None:
        self.started: list = []
        self.signals: list = []
        self.intakes: list = []
        self.forecasts: list = []
        self.resets: list = []
        self.status: dict[str, str] = {}
        self.state = RunState.AWAITING_REVIEW

    async def start_call(self, args, *, workflow_id):
        self.started.append((workflow_id, args))
        self.status[workflow_id] = "RUNNING"
        return workflow_id

    async def describe(self, workflow_id):
        return WorkflowInfo(workflow_id=workflow_id, status=self.status.get(workflow_id, "NOT_FOUND"), execution_run_id="exec-1")

    async def run_status(self, workflow_id):
        return RunStatus(state=self.state, profile_version=3) if workflow_id in self.status else None

    async def signal_review(self, workflow_id, review):
        self.signals.append(("review", workflow_id, review))

    async def signal_answer(self, workflow_id, answer):
        self.signals.append(("answer", workflow_id, answer))

    async def start_followup_intake(self, args, *, workflow_id):
        self.intakes.append((workflow_id, args))
        return workflow_id

    async def start_forecast(self, args, *, workflow_id):
        self.forecasts.append((workflow_id, args))
        return workflow_id

    async def reset_to_stage(self, workflow_id, stage, *, reason):
        self.resets.append((workflow_id, stage))
        return "exec-2"


async def _pins() -> VersionPins:
    return VersionPins()


@pytest.fixture
async def api(store: Store, objects):
    gw = FakeGateway()
    stages = StageFunctions(
        {"fna_analyse": lambda doc, *, today: {"needs": {"retirement": "shortfall"}, "client": doc.client.firstName, "as_of": str(today)}}
    )
    app = create_app(settings=Settings(env="test", api_token=TOKEN), store=store, objects=objects, temporal=gw, stages=stages, pins=_pins)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {TOKEN}"}
    ) as c:
        yield SimpleNamespace(c=c, gw=gw, store=store, objects=objects, app=app)


async def seed_run(a, call_id: str = "call-1", run_id: str = "call-1-run-a"):
    """A call whose run is waiting for review: transcript, profile with a flag and a follow-up, a preview forecast."""
    doc_id = await a.store.save_client_document("cl-1", sample_document(), source="import")
    audio = await a.objects.put_bytes(f"audio/{call_id}/call.wav", b"RIFF" + b"\x00" * 64)
    await a.store.upsert_call(
        CallRecord(call_id=call_id, workflow_id=run_id, advisor_id="adv-1", client_ids=["cl-1"], started_at=NOW, audio_uri=audio,
                   meta={"prior_document_id": doc_id, "content_type": "audio/wav"})
    )  # fmt: skip
    await a.store.register_run(run_id, call_id, VersionPins())
    await a.store.save_transcript(call_id, ingest_key(VersionPins()), make_transcript(call_id))
    profile = await a.store.save_profile(flagged(with_call_facts(seeded_profile(run_id, call_id))))
    fr = ForecastResult(
        run_id=run_id,
        profile_version=str(profile.version),
        config_version="config_v1",
        cma_version="2026Q4",
        n_paths=1000,
        pos=0.71,
        pos_ci=(0.68, 0.74),
        variance_attribution={"market": 0.6, "client_content": 0.3, "extraction": 0.1},
    )
    await a.store.save_forecast(run_id, profile.version, "preview", fr, config_version="config_v1", cma_version="2026Q4")
    a.gw.status[run_id] = "RUNNING"
    return profile


def _clear_flag_body(reviewer="adv-1"):
    return {
        "reviewer_id": reviewer,
        "flag_clearances": [
            {"flag_id": "flag.tier_a.retirement_age", "resolution": "accepted", "reviewer_id": reviewer, "at": NOW.isoformat()}
        ],
    }


# --------------------------------------------------------------------------- auth and CORS


async def test_bearer_token_is_required(api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as anon:
        assert (await anon.get("/calls/x")).status_code == 401
        assert (await anon.get("/calls/x", headers={"Authorization": "Bearer wrong"})).status_code == 401
        r = await anon.get("/healthz")
        assert r.status_code == 200 and r.json() == {"ok": True, "database": True}
        pre = await anon.options(
            "/calls",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert (await api.c.get("/calls/x")).status_code == 404


async def test_api_is_closed_without_a_token_outside_local(store, objects):
    app = create_app(settings=Settings(env="production"), store=store, objects=objects, temporal=FakeGateway(), pins=_pins)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/calls/x")).status_code == 503


# --------------------------------------------------------------------------- calls and runs


async def test_create_call_by_upload_or_uri(api):
    seeded = await api.c.post("/clients/cl-1/fna", content=ExportFile(document=sample_document()).model_dump_json())
    assert seeded.status_code == 201
    meta = {
        "call_id": "call-7",
        "advisor_id": "adv-1",
        "advisor_email": "adv@practice.co.za",
        "client_ids": ["cl-1"],
        "started_at": NOW.isoformat(),
        "versions": {"cma_version": "2027Q1"},
        "review_reminder_seconds": 60,
    }
    r = await api.c.post("/calls", files={"audio": ("meeting.wav", b"RIFF-audio", "audio/wav")}, data={"metadata": json.dumps(meta)})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["call_id"] == "call-7" and body["workflow_id"].startswith("call-7-run-")
    wf, args = api.gw.started[0]
    assert (
        wf == body["workflow_id"]
        and args.versions.cma_version == "2027Q1"
        and args.review_reminder_seconds == 60
        and args.start_stage == Stage.INGEST
    )
    call = await api.store.get_call("call-7")
    assert await api.objects.get_bytes(call.audio_uri) == b"RIFF-audio"
    assert call.meta["prior_document_id"] == seeded.json()["document_id"] and call.meta["advisor_email"] == "adv@practice.co.za"
    assert (await api.store.get_run(wf)).versions.cma_version == "2027Q1"
    # The same call again is refused; a URI outside the store is refused; a URI in the store is accepted.
    assert (
        await api.c.post("/calls", files={"audio": ("m.wav", b"x", "audio/wav")}, data={"metadata": json.dumps(meta)})
    ).status_code == 409
    bad = await api.c.post("/calls", json={"call_id": "call-8", "audio_uri": "file:///etc/passwd"})
    assert bad.status_code == 422
    uri = await api.objects.put_bytes("audio/inbox/call-9.wav", b"RIFF")
    ok = await api.c.post("/calls", json={"call_id": "call-9", "audio_uri": uri, "advisor_id": "adv-1", "versions": {"nope": 1}})
    assert ok.status_code == 422 and "unknown version pins" in ok.text
    ok = await api.c.post("/calls", json={"call_id": "call-9", "audio_uri": uri, "advisor_id": "adv-1"})
    assert ok.status_code == 201
    assert (await api.c.post("/calls", json={"call_id": "bad id!", "audio_uri": uri})).status_code == 422


async def test_deployment_pins_follow_the_routing_the_asr_provider_and_the_promoted_calibrator(store: Store, monkeypatch):
    monkeypatch.delenv("ADVISOR_INTEL_PINS", raising=False)
    s = Settings(env="local", asr_provider="assemblyai")
    pins = await deployment_pins(s, store)
    assert (pins.asr_provider, pins.asr_model_version, pins.calibrator_version) == ("assemblyai", "universal-3", "cold-start")
    assert pins.frontier_model == s.models.frontier and pins.reasoning_model == s.models.reasoning
    await store.register_calibrator(CalibratorRecord(version="cal-7", promoted=True))
    monkeypatch.setenv("ADVISOR_INTEL_ASR_MODEL_VERSION", "universal-3-pro")
    pins = await deployment_pins(s, store)
    assert pins.calibrator_version == "cal-7" and pins.asr_model_version == "universal-3-pro"
    assert await deployment_pins(Settings(env="test")) == VersionPins()


async def test_create_a_scripted_call_and_stereo_hints(api):
    script = "advisor: Morning Sipho.\nclient_primary: Morning. I'd like to retire at sixty."
    body = {
        "call_id": "call-s",
        "advisor_id": "adv-1",
        "script": script,
        "script_roles": {"advisor": "advisor", "client_primary": "client_primary"},
    }
    r = await api.c.post("/calls", json={**body, "advisor_channel": 0, "shared_client_channel": False})
    assert r.status_code == 201, r.text
    call = await api.store.get_call("call-s")
    assert call.audio_uri is None and (await api.objects.get_bytes(call.meta["script_uri"])).decode() == script
    assert (
        call.meta["script_roles"] == body["script_roles"]
        and call.meta["advisor_channel"] == 0
        and call.meta["shared_client_channel"] is False
    )
    both = await api.c.post("/calls", json={**body, "call_id": "call-t", "audio_uri": "file:///x.wav"})
    assert both.status_code == 422 and "one of 'audio_uri' and 'script'" in both.text
    upload = await api.c.post(
        "/calls", files={"audio": ("a.wav", b"RIFF", "audio/wav")}, data={"metadata": json.dumps({**body, "call_id": "call-u"})}
    )
    assert upload.status_code == 422 and "not both" in upload.text
    assert (await api.c.post("/calls", json={**body, "call_id": "call-v", "script_roles": {"x": "boss"}})).status_code == 422


async def test_oversized_recordings_are_refused(api, monkeypatch):
    monkeypatch.setenv("ADVISOR_INTEL_MAX_UPLOAD_MB", "1")
    r = await api.c.post(
        "/calls", files={"audio": ("big.wav", b"0" * (2**20 + 10), "audio/wav")}, data={"metadata": json.dumps({"call_id": "big"})}
    )
    assert r.status_code == 413 and await api.store.get_call("big") is None


async def test_call_state_profile_and_forecast(api):
    profile = await seed_run(api)
    r = await api.c.get("/calls/call-1")
    assert r.status_code == 200
    v = r.json()
    assert v["state"] == "AWAITING_REVIEW" and v["workflow_status"] == "RUNNING" and v["quality"]["n_speakers"] == 2
    assert (
        v["profile_version"] == profile.version
        and v["open_flags"] == 1
        and v["versions"]["lexicon_version"] == VersionPins().lexicon_version
    )
    p = (await api.c.get("/calls/call-1/profile")).json()
    assert p["facts"][RET_AGE]["status"] == "stated" and p["version"] == profile.version
    f = await api.c.get("/calls/call-1/forecast")
    assert f.status_code == 200 and f.json()["pos"] == 0.71
    assert (await api.c.get("/calls/call-1/forecast", params={"mode": "final"})).status_code == 404
    started = await api.c.post("/calls/call-1/forecast", json={"versions": {"cma_version": "2027Q1"}})
    assert started.status_code == 202 and api.gw.forecasts[0][1].args.versions.cma_version == "2027Q1"


async def test_rerun_new_run_or_reset(api):
    await seed_run(api)
    r = await api.c.post("/calls/call-1/rerun", json={"from_stage": "forecast", "versions": {"cma_version": "2027Q1"}})
    assert r.status_code == 202, r.text
    body = r.json()
    run = await api.store.get_run(body["workflow_id"])
    assert run.source_run_id == "call-1-run-a" and run.start_stage == Stage.FORECAST and run.versions.cma_version == "2027Q1"
    assert api.gw.started[-1][1].start_stage == Stage.FORECAST and (await api.store.get_call("call-1")).workflow_id == body["workflow_id"]
    reset = await api.c.post("/calls/call-1/rerun", json={"from_stage": 3, "mode": "reset"})
    assert (
        reset.status_code == 202 and reset.json()["execution_run_id"] == "exec-2" and api.gw.resets == [(body["workflow_id"], Stage.AGENT)]
    )
    assert (
        await api.c.post("/calls/call-1/rerun", json={"from_stage": "agent", "mode": "reset", "versions": {"cma_version": "x"}})
    ).status_code == 422


# --------------------------------------------------------------------------- review


async def test_review_bundle(api):
    await seed_run(api)
    await api.store.record_followup_answer(
        "call-1-run-a", FollowupAnswer(call_id="call-1", followup_id="fu.retirement_age", text="sixty-three", answered_at=NOW)
    )
    r = await api.c.get("/calls/call-1/review")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["run_id"] == "call-1-run-a" and b["state"] == "AWAITING_REVIEW"
    assert b["median_document"]["retirement"]["client"]["retirementAge"] == 62
    assert b["prior_document"]["retirement"]["client"]["retirementAge"] == 65
    assert b["sidecar"]["fields"]["retirement.client.retirementAge"]["change"] == "changed"
    assert [s["segment_id"] for s in b["transcript"]["segments"]][-1] == "followup:fu.retirement_age"
    assert b["audio_url"] == "http://test/calls/call-1/audio"
    assert b["followups"][0]["status"] == "answered" and b["blocking_flags"][0]["kind"] == "tier_a_contradicted"
    assert b["preview_forecast"]["pos"] == 0.71 and b["attribution_text"].startswith(
        "Of the uncertainty in this plan, about 60% is markets"
    )
    assert b["deterministic_analysis"] == {"needs": {"retirement": "shortfall"}, "client": "Thabo", "as_of": "2026-10-15"}  # the call date
    assert b["quality"]["n_speakers"] == 2 and isinstance(b["spot_check"], bool)
    audio = await api.c.get("/calls/call-1/audio", headers={"Range": "bytes=0-3"})
    assert audio.status_code == 206 and audio.content == b"RIFF"


async def test_reviewer_ops_go_through_the_reducer_and_are_written_immediately(api):
    profile = await seed_run(api)
    ops = make_ops(
        profile,
        [
            ("CONFIRM", RET_AGE, None, {}, "reviewer"),
            ("RETRACT", GROSS, None, {"reason": "misheard"}, "reviewer"),
            ("CONFIRM", "no#fact", None, {}, "reviewer"),
        ],
    )
    ops.append(ops[0].model_copy(update={"actor": "extractor"}))
    r = await api.c.post(
        "/calls/call-1/ops", content=json.dumps([o.model_dump(mode="json") for o in ops]), headers={"X-Reviewer-Id": "adv-9"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["applied"]) == 2 and len(body["rejected"]) == 2 and body["profile_version"] == profile.version + 1
    assert body["facts"][RET_AGE]["review"]["status"] == "accepted" and body["facts"][RET_AGE]["review"]["reviewer_id"] == "adv-9"
    assert body["facts"][GROSS]["review"]["status"] == "rejected"
    stored = await api.store.load_profile("call-1-run-a")
    assert stored.facts[RET_AGE].review.status == "accepted" and stored.version == profile.version + 1
    events = await api.store.review_events(run_id="call-1-run-a")
    assert [(e.action, e.reviewer_id) for e in events] == [("accept", "adv-9"), ("reject", "adv-9")]
    # Side records: a flag clearance, a reworded follow-up and a confirmed term.
    side = {
        **_clear_flag_body(),
        "followup_edits": [
            {
                "followup_id": "fu.retirement_age",
                "action": "reword",
                "question": "Retire at 60, 63 or 65?",
                "reviewer_id": "adv-1",
                "at": NOW.isoformat(),
            }
        ],
        "term_confirmations": [
            {
                "term": "10X RA",
                "canonical_name": "10X Retirement Annuity",
                "product_type": "retirement-annuity",
                "provider": "10X",
                "reviewer_id": "adv-1",
                "at": NOW.isoformat(),
            }
        ],
    }
    r = await api.c.post("/calls/call-1/ops", json=side)
    assert r.status_code == 200 and r.json()["open_flags"] == [] and r.json()["unknown_flags"] == []
    assert (await api.store.get_followup("call-1-run-a", "fu.retirement_age")).followup.question == "Retire at 60, 63 or 65?"
    entry = await PostgresGlossaryStore(api.store.db).get("10x ra")
    assert entry.canonical_name == "10X Retirement Annuity" and entry.source == "reviewer" and entry.confirmed_by == "adv-1"
    assert {e.action for e in await api.store.review_events(run_id="call-1-run-a")} >= {"clear_flag", "edit_followup", "confirm_term"}


async def test_submit_is_refused_while_blocking_flags_are_open(api):
    await seed_run(api)
    r = await api.c.post("/calls/call-1/submit", json={"reviewer_id": "adv-1"})
    assert r.status_code == 409 and r.json()["detail"]["open_flags"][0]["flag_id"] == "flag.tier_a.retirement_age"
    assert not api.gw.signals
    assert (await api.c.post("/calls/call-1/ops", json=_clear_flag_body())).status_code == 200
    r = await api.c.post("/calls/call-1/submit", json={"reviewer_id": "adv-1", "review_seconds": 412.5})
    assert r.status_code == 200, r.text
    version = r.json()["profile_version"]
    [(kind, wf, review)] = api.gw.signals
    assert kind == "review" and wf == "call-1-run-a" and review.profile_version == version and review.reviewer_id == "adv-1"
    assert [v.frozen for v in await api.store.profile_versions("call-1-run-a")][-1] is True
    assert (await api.c.post("/calls/call-1/submit", json={"reviewer_id": "adv-1"})).status_code == 409
    assert (await api.c.post("/calls/call-1/ops", json=[])).status_code == 409
    [rec] = await api.store.review_results("call-1-run-a")
    assert rec.submitted and rec.result["review_seconds"] == 412.5


async def test_submit_needs_a_run_waiting_for_review(api):
    await seed_run(api)
    await api.c.post("/calls/call-1/ops", json=_clear_flag_body())
    api.gw.state = RunState.AGENT_RUNNING
    assert (await api.c.post("/calls/call-1/submit", json={"reviewer_id": "adv-1"})).status_code == 409
    api.gw.status["call-1-run-a"] = "COMPLETED"
    assert (await api.c.post("/calls/call-1/submit", json={"reviewer_id": "adv-1"})).status_code == 409


async def test_followup_answers_are_routed_by_run_status(api):
    await seed_run(api)
    r = await api.c.post("/calls/call-1/followups/fu.retirement_age/answer", json={"text": "Sixty-three", "recorded_by": "adv-1"})
    assert r.status_code == 200 and r.json()["routed"] == "signal" and api.gw.signals[0][0] == "answer"
    assert (await api.store.get_followup("call-1-run-a", "fu.retirement_age")).status == "answered"
    api.gw.status["call-1-run-a"] = "COMPLETED"
    r = await api.c.post("/calls/call-1/followups/fu.retirement_age/answer", json={"text": "Actually sixty-four"})
    assert r.status_code == 200 and r.json()["routed"] == "intake_workflow"
    wf, args = api.gw.intakes[0]
    assert r.json()["workflow_id"] == wf and args.run_id == "call-1-run-a" and args.answer.text == "Actually sixty-four"
    assert (await api.c.post("/calls/call-1/followups/fu.nope/answer", json={"text": "x"})).status_code == 404


async def test_fna_export_with_sidecar_and_note(api):
    await seed_run(api)
    assert (await api.c.get("/calls/call-1/fna")).status_code == 409  # blocked while a flag is open
    await api.c.post("/calls/call-1/ops", json=_clear_flag_body())
    profile = await api.store.load_profile("call-1-run-a")
    note = MeetingNote(
        call_id="call-1", run_id="call-1-run-a", profile_version=profile.version,
        sentences=[NoteSentence(section="summary", text="Thabo plans to retire at 62.", fact_ids=[RET_AGE])],
    )  # fmt: skip
    await api.store.save_note("call-1-run-a", profile.version, note.model_dump(mode="json"))
    r = await api.c.get("/calls/call-1/fna")
    assert r.status_code == 200
    body = r.json()
    export = ExportFile.model_validate(body["fna"])
    assert export.format == "shahil-fna" and export.version == 1 and export.document.schemaVersion == 2
    assert export.document.retirement["client"].retirementAge == 62 and export.document.advice.summary == "Thabo plans to retire at 62."
    assert body["sidecar"]["fields"]["retirement.client.retirementAge"]["p_correct"] == 0.5
    part = await api.c.get("/calls/call-1/fna", params={"part": "fna"})
    assert part.headers["content-disposition"].startswith("attachment") and part.json()["format"] == "shahil-fna"
    assert (await api.c.get("/calls/call-1/note")).json()["narrative_fields"] == {"advice.summary": "Thabo plans to retire at 62."}
    # Nothing it cites changed: regenerating keeps every sentence.
    regen = await api.c.post("/calls/call-1/note")
    assert regen.status_code == 200 and [s["text"] for s in regen.json()["sentences"]] == ["Thabo plans to retire at 62."]


async def test_file_exchange_review_package_and_result(api):
    profile = await seed_run(api)
    pkg = await api.c.get("/calls/call-1/review-package")
    assert pkg.status_code == 200 and pkg.headers["content-disposition"] == 'attachment; filename="review-call-1.json"'
    assert pkg.json()["format"] == "advisor-intel-review" and pkg.json()["bundle"]["call_id"] == "call-1"
    result = {
        "call_id": "call-1",
        "run_id": "call-1-run-a",
        "base_profile_version": profile.version,
        "reviewer_id": "adv-1",
        "ops": [o.model_dump(mode="json") for o in make_ops(profile, [("CONFIRM", RET_AGE, None, {}, "reviewer")])],
        "flag_clearances": _clear_flag_body()["flag_clearances"],
        "submitted": True,
        "submitted_at": NOW.isoformat(),
        "review_seconds": 300,
    }
    r = await api.c.post("/calls/call-1/review-result", json=result)
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["applied"] and r.json()["submitted"]["profile_version"] == profile.version + 1
    assert api.gw.signals[0][0] == "review" and len(await api.store.review_results("call-1-run-a")) == 2
    assert (await api.c.post("/calls/call-1/review-result", json={**result, "call_id": "other"})).status_code == 422


async def test_seed_client_fna_checks_the_file(api):
    plain = ExportFile(document=sample_document()).model_dump(mode="json")
    assert (await api.c.post("/clients/cl-1/fna", json={**plain, "encrypted": True})).status_code == 422
    assert (await api.c.post("/clients/cl-1/fna", json={"format": "something-else"})).status_code == 422
    r = await api.c.post("/clients/cl-1/fna", json=plain)
    assert r.status_code == 201 and r.json()["schema_version"] == 2
    assert (await api.store.latest_client_document("cl-1")).client.firstName == "Thabo"
