"""One call end to end on the Temporal dev server: the API (with the real Temporal gateway), the worker
(real workflows and activities over local Postgres and object storage) and fake stage functions; the last
test runs a scripted call through the stage code as merged."""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx
import pytest

from advisor_intel import llm
from advisor_intel.activities import StageFunctions
from advisor_intel.activities.providers import Providers
from advisor_intel.api.app import create_app
from advisor_intel.api.temporal import ClientGateway
from advisor_intel.config import Settings
from advisor_intel.extract.reducer import make_ops
from advisor_intel.models.fna import ExportFile
from advisor_intel.models.runs import CallResult, VersionPins
from advisor_intel.storage import PostgresCheckpointStore
from advisor_intel.testing.calls import SAMPLE_CALL, SAMPLE_GOLD, SAMPLE_OBJECTS
from advisor_intel.testing.fakes import scripted_model
from advisor_intel.testing.oracle import Oracle
from advisor_intel.testing.transcripts import ingested_transcript
from advisor_intel.worker import build_worker
from advisor_intel.workflows.contracts import FollowupIntakeResult
from advisor_intel.workflows.versioning import deployment_config, set_current_version

from .factories import RET_AGE, sample_document
from .temporal_harness import temporal_env
from .test_activities import Fakes, RecordingNotifier

pytestmark = [pytest.mark.temporal, pytest.mark.postgres]

TOKEN = "e2e-token"


async def _pins() -> VersionPins:
    return VersionPins()


def _history_summary(history) -> dict:
    """What the run's Temporal history records: activities in order, failures, retries, signals, payload sizes."""
    from temporalio.api.enums.v1 import EventType as E

    scheduled, failed, signals, timed_out = [], [], [], 0
    attempts: dict[int, int] = {}
    biggest = 0
    for e in history.events:
        if e.event_type == E.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            scheduled.append(e.activity_task_scheduled_event_attributes.activity_type.name)
            for p in e.activity_task_scheduled_event_attributes.input.payloads:
                biggest = max(biggest, p.ByteSize())
        elif e.event_type == E.EVENT_TYPE_ACTIVITY_TASK_STARTED:
            a = e.activity_task_started_event_attributes
            attempts[a.scheduled_event_id] = a.attempt
        elif e.event_type == E.EVENT_TYPE_ACTIVITY_TASK_FAILED:
            failed.append(e.activity_task_failed_event_attributes.failure.message)
        elif e.event_type == E.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT:
            timed_out += 1
        elif e.event_type == E.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED:
            signals.append(e.workflow_execution_signaled_event_attributes.signal_name)
        elif e.event_type == E.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED:
            for p in e.workflow_execution_completed_event_attributes.result.payloads:
                biggest = max(biggest, p.ByteSize())
    return {
        "activities": scheduled,
        "failed": failed,
        "timed_out": timed_out,
        "retried": sum(1 for a in attempts.values() if a > 1),
        "signals": signals,
        "events": len(history.events),
        "largest_payload_bytes": biggest,
    }


async def _until(fn, *, timeout: float = 30.0, every: float = 0.2):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        got = await fn()
        if got:
            return got
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(every)


async def test_a_call_from_upload_to_reviewed_export(store, objects, temporal_bin):
    fakes, notifier = Fakes(), RecordingNotifier()
    tq = f"e2e-{uuid.uuid4().hex[:6]}"
    settings = Settings(env="test", api_token=TOKEN, task_queue=tq)
    stages = StageFunctions(fakes.overrides())
    providers = Providers(asr=lambda p, s: "asr", diarizer=lambda p, s: "diarizer", second_pass=lambda p, s: None)
    async with temporal_env(temporal_bin, objects, threshold=64 * 1024) as env:
        cfg = deployment_config(build=f"e2e-{uuid.uuid4().hex[:6]}")
        worker = build_worker(
            env.client,
            settings,
            store=store,
            objects=objects,
            notifier=notifier,
            stages=stages,
            providers=providers,
            deployment=cfg,
            task_queue=tq,
        )
        async with worker:
            await set_current_version(env.client, cfg.version)
            app = create_app(
                settings=settings, store=store, objects=objects, temporal=ClientGateway(env.client, tq), stages=stages, pins=_pins
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {TOKEN}"}
            ) as c:
                assert (
                    await c.post("/clients/cl-1/fna", content=ExportFile(document=sample_document()).model_dump_json())
                ).status_code == 201
                meta = {
                    "call_id": "call-e2e",
                    "advisor_id": "adv-1",
                    "advisor_email": "adv@practice.co.za",
                    "client_ids": ["cl-1"],
                    "review_reminder_seconds": 3600,
                }
                r = await c.post(
                    "/calls", files={"audio": ("call.wav", b"RIFF-call-audio", "audio/wav")}, data={"metadata": json.dumps(meta)}
                )
                assert r.status_code == 201, r.text
                run_id = r.json()["workflow_id"]

                async def state():
                    v = (await c.get("/calls/call-e2e")).json()
                    return v if v.get("state") == "AWAITING_REVIEW" else None

                view = await _until(state)
                assert view["open_flags"] == 1 and view["quality"]["n_speakers"] == 2
                await _until(lambda: asyncio.sleep(0, result=bool(notifier.sent)))  # the state flips before the notify activity runs
                assert notifier.sent[0].kind == "review_ready"
                bundle = (await c.get("/calls/call-e2e/review")).json()
                assert bundle["median_document"]["retirement"]["client"]["retirementAge"] == 62 and bundle["preview_forecast"]["pos"] == 0.7

                # The adviser clears the flag, accepts the retirement age and records the client's answer.
                profile = await store.load_profile(run_id)
                ops = [o.model_dump(mode="json") for o in make_ops(profile, [("CONFIRM", RET_AGE, None, {}, "reviewer")])]
                clear = [
                    {
                        "flag_id": "flag.tier_a.retirement_age",
                        "resolution": "accepted",
                        "reviewer_id": "adv-1",
                        "at": "2026-10-16T10:00:00Z",
                    }
                ]
                assert (
                    await c.post("/calls/call-e2e/ops", json={"reviewer_id": "adv-1", "ops": ops, "flag_clearances": clear})
                ).status_code == 200
                a = await c.post("/calls/call-e2e/followups/fu.retirement_age/answer", json={"text": "Sixty-three is realistic"})
                assert a.json()["routed"] == "signal"

                async def answered():
                    v = (await c.get("/calls/call-e2e")).json()
                    return v["status"]["answers_processed"] == 1 and v["state"] == "AWAITING_REVIEW"

                await _until(answered)
                s = await c.post("/calls/call-e2e/submit", json={"reviewer_id": "adv-1"})
                assert s.status_code == 200, s.text

                async def complete():
                    v = (await c.get("/calls/call-e2e")).json()
                    return v if v.get("workflow_status") == "COMPLETED" else None

                done = await _until(complete)
                assert done["state"] == "COMPLETE"
                result = await env.client.get_workflow_handle(run_id, result_type=CallResult).result()
                assert result.pos == 0.7 and result.profile_version == done["profile_version"]
                # The history (§13.1): every stage once, in order; the answer taken in during review with a preview
                # forecast after it; no failed or timed-out activity attempt; payloads are ids and small outcomes.
                hist = _history_summary(await env.client.get_workflow_handle(run_id).fetch_history())
                print("\nhistory:", json.dumps(hist, indent=1))
                assert hist["activities"] == [
                    "ingest_audio", "run_discourse", "run_agent", "run_voi_and_followups", "run_forecast", "notify_advisor",
                    "intake_followup_answer", "run_forecast", "apply_review", "render_meeting_note", "run_forecast",
                ]  # fmt: skip
                assert hist["failed"] == [] and hist["timed_out"] == 0 and hist["retried"] == 0
                assert hist["signals"] == ["followup_answered", "review_submitted"]
                assert hist["largest_payload_bytes"] < 4096

                export = (await c.get("/calls/call-e2e/fna")).json()
                doc = ExportFile.model_validate(export["file"]).document
                assert doc.advice.summary and "retirement age" in doc.advice.summary.lower()
                assert export["sidecar"]["fields"]["retirement.client.retirementAge"]["review"]["status"] == "accepted"
                assert (await store.get_forecast(run_id, mode="final")) is not None
                events = [e.action for e in await store.review_events(run_id=run_id)]
                assert sorted(events[:2]) == ["accept", "clear_flag"] and events[-1] == "submit"
                assert fakes.calls == {"ingest": 1, "discourse": 1, "agent": 1, "verify": 1, "voi": 1, "forecast": 3, "extract_followup": 1}

                # An answer after completion goes to FollowupIntakeWorkflow, which re-runs the final forecast.
                late = await c.post("/calls/call-e2e/followups/fu.retirement_age/answer", json={"text": "We now think sixty-four"})
                assert late.json()["routed"] == "intake_workflow"
                intake = await env.client.get_workflow_handle(late.json()["workflow_id"], result_type=FollowupIntakeResult).result()
                assert intake.intake.applied == 1 and intake.forecast.mode == "final" and fakes.calls["forecast"] == 4
                note_version, _ = await store.latest_note(run_id)
                assert note_version == intake.intake.profile_version  # the note follows the changed fact


async def test_rerun_by_reset_rewinds_to_the_stage_and_reuses_earlier_outputs(store, objects, temporal_bin):
    fakes, notifier = Fakes(), RecordingNotifier()
    tq = f"e2e-{uuid.uuid4().hex[:6]}"
    settings = Settings(env="test", api_token=TOKEN, task_queue=tq)
    stages = StageFunctions(fakes.overrides())
    providers = Providers(asr=lambda p, s: "asr", diarizer=lambda p, s: "diarizer", second_pass=lambda p, s: None)
    async with temporal_env(temporal_bin, objects) as env:
        cfg = deployment_config(build=f"e2e-{uuid.uuid4().hex[:6]}")
        worker = build_worker(
            env.client,
            settings,
            store=store,
            objects=objects,
            notifier=notifier,
            stages=stages,
            providers=providers,
            deployment=cfg,
            task_queue=tq,
        )
        async with worker:
            await set_current_version(env.client, cfg.version)
            gateway = ClientGateway(env.client, tq)
            app = create_app(settings=settings, store=store, objects=objects, temporal=gateway, stages=stages, pins=_pins)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {TOKEN}"}
            ) as c:
                await c.post("/clients/cl-1/fna", content=ExportFile(document=sample_document()).model_dump_json())
                meta = {"call_id": "call-reset", "advisor_id": "adv-1", "client_ids": ["cl-1"]}
                r = await c.post(
                    "/calls", files={"audio": ("call.wav", b"RIFF-call-audio", "audio/wav")}, data={"metadata": json.dumps(meta)}
                )
                run_id = r.json()["workflow_id"]

                async def waiting():
                    return (await c.get("/calls/call-reset")).json().get("state") == "AWAITING_REVIEW"

                await _until(waiting)
                first = (await gateway.describe(run_id)).execution_run_id
                reset = await c.post("/calls/call-reset/rerun", json={"from_stage": "voi", "mode": "reset"})
                assert reset.status_code == 202, reset.text
                assert reset.json()["workflow_id"] == run_id and reset.json()["execution_run_id"] not in (None, first)

                async def back_in_review():
                    info = await gateway.describe(run_id)
                    st = await gateway.run_status(run_id)
                    return (
                        info.execution_run_id != first
                        and st is not None
                        and st.state.value == "AWAITING_REVIEW"
                        and fakes.calls.get("voi") == 2
                    )

                await _until(back_in_review)
                # Stages before VOI were not run again; VOI ran in the new execution; the unchanged preview forecast was reused.
                assert fakes.calls["ingest"] == 1 and fakes.calls["agent"] == 1 and fakes.calls["forecast"] == 1
                bad = await c.post(
                    "/calls/call-reset/rerun", json={"from_stage": "review", "mode": "reset", "versions": {"cma_version": "x"}}
                )
                assert bad.status_code == 422


@pytest.mark.slow
async def test_a_scripted_call_through_the_merged_stage_code(store, objects, temporal_bin):
    """The stage code as merged, under Temporal: scripted stage 1, discourse, the agent harness (the oracle's
    extractions, Postgres checkpoints, the practice glossary), the checks and scoring after it, VOI with the
    forecast's sensitivity runs, the forecasts, the review and the meeting note. A stage whose package is not
    installed is skipped, as in the in-process runner."""
    oracle = Oracle(await ingested_transcript(SAMPLE_CALL, call_id="call-real"), SAMPLE_OBJECTS, SAMPLE_GOLD)
    pins = VersionPins(reasoning_model=llm.register_local_model("oracle-e2e", scripted_model(oracle.handler)))

    async def oracle_pins() -> VersionPins:
        return pins

    notifier = RecordingNotifier()
    tq = f"e2e-{uuid.uuid4().hex[:6]}"
    settings = Settings(env="test", api_token=TOKEN, task_queue=tq)
    try:
        async with temporal_env(temporal_bin, objects) as env:
            cfg = deployment_config(build=f"e2e-{uuid.uuid4().hex[:6]}")
            async with build_worker(env.client, settings, store=store, objects=objects, notifier=notifier, deployment=cfg, task_queue=tq):
                await set_current_version(env.client, cfg.version)
                gateway = ClientGateway(env.client, tq)
                app = create_app(settings=settings, store=store, objects=objects, temporal=gateway, pins=oracle_pins)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {TOKEN}"}
                ) as c:
                    script = "\n".join(f"{role}: {text}" for role, text in SAMPLE_CALL)
                    body = {
                        "call_id": "call-real",
                        "advisor_id": "adv-1",
                        "client_ids": ["cl-real"],
                        "started_at": "2026-10-15T09:00:00+02:00",
                        "script": script,
                        "script_roles": {r: r for r in ("advisor", "client_primary", "client_secondary")},
                    }
                    r = await c.post("/calls", json=body)
                    assert r.status_code == 201, r.text
                    run_id = r.json()["workflow_id"]

                    seen: list[str] = []

                    async def in_review():
                        v = (await c.get("/calls/call-real")).json()
                        stage = f"{v.get('state')}/{(v.get('status') or {}).get('stage')}"
                        if not seen or seen[-1] != stage:
                            seen.append(stage)
                            print(f"\n[{asyncio.get_running_loop().time():.0f}] {stage}", flush=True)
                        return v if v.get("state") in ("AWAITING_REVIEW", "FAILED") else None

                    # The stage code runs for real here; a loaded CI machine needs minutes (ADVISOR_INTEL_E2E_WAIT_S overrides the default).
                    view = await _until(in_review, timeout=float(os.environ.get("ADVISOR_INTEL_E2E_WAIT_S", "300")))
                    assert view["state"] == "AWAITING_REVIEW" and view["quality"]["n_speakers"] == 3
                    bundle = (await c.get("/calls/call-real/review")).json()
                    doc = bundle["median_document"]
                    assert doc["income"]["spouse"]["grossMonthly"] == 35_000 and {d["name"] for d in doc["dependants"]} == {
                        "Thandi",
                        "Lwazi",
                    }
                    assert bundle["sidecar"]["fields"]["income.spouse.grossMonthly"]["status"] == "stated"
                    assert bundle["deterministic_analysis"]  # the FNA engine ran as of the call date
                    assert (bundle["preview_forecast"] is None) == (not StageFunctions().available("run_forecast"))
                    # The agent checkpointed every turn to Postgres, in its execution's scope.
                    execution = (await gateway.describe(run_id)).execution_run_id
                    cp = await PostgresCheckpointStore(store, scope=execution).load(run_id)
                    assert cp is not None and cp.turn >= 3 and cp.messages and cp.harness_state["plan"]
                    # Stage 5 scored the call's facts with the cold-start calibrator (§9.4).
                    profile = (await c.get("/calls/call-real/profile")).json()
                    scored = [f for f in profile["facts"].values() if f["source"] == "call"]
                    assert scored and all(f["p_correct"] is not None for f in scored)
                    # The adviser settles every blocking flag VOI raised (§10.3), then submits.
                    flags = [f for f in bundle["blocking_flags"] if not f["cleared"]]
                    if flags:
                        assert (await c.post("/calls/call-real/submit", json={"reviewer_id": "adv-1"})).status_code == 409
                        at = "2026-10-16T10:00:00Z"
                        clear = [
                            {
                                "flag_id": f["flag_id"],
                                "resolution": "marked_unknown" if f["kind"] == "tier_a_missing" else "accepted",
                                "reviewer_id": "adv-1",
                                "at": at,
                            }
                            for f in flags
                        ]
                        ops = await c.post("/calls/call-real/ops", json={"reviewer_id": "adv-1", "ops": [], "flag_clearances": clear})
                        assert ops.status_code == 200 and ops.json()["open_flags"] == [], ops.text
                    s = await c.post("/calls/call-real/submit", json={"reviewer_id": "adv-1"})
                    assert s.status_code == 200, s.text

                    async def complete():
                        v = (await c.get("/calls/call-real")).json()
                        return v if v.get("workflow_status") in ("COMPLETED", "FAILED") else None

                    done = await _until(complete, timeout=120)
                    assert done["state"] == "COMPLETE"
                    final = await store.get_forecast(run_id, mode="final")
                    assert (final is None) == (not StageFunctions().available("run_forecast"))
                    export = (await c.get("/calls/call-real/fna")).json()
                    assert ExportFile.model_validate(export["file"]).document.income["spouse"].grossMonthly == 35_000
                    version, note = await store.latest_note(run_id)
                    assert version == done["profile_version"] and note["sentences"]
                    assert notifier.sent and notifier.sent[0].kind == "review_ready"
                    hist = _history_summary(await env.client.get_workflow_handle(run_id).fetch_history())
                    print("\nhistory:", json.dumps(hist, indent=1))
                    assert hist["failed"] == [] and hist["timed_out"] == 0 and hist["retried"] == 0, hist
                    assert hist["activities"][:3] == ["ingest_audio", "run_discourse", "run_agent"] and hist["largest_payload_bytes"] < 4096
    finally:
        llm.unregister_local_model("oracle-e2e")
