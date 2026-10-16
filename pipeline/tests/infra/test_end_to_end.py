"""One call end to end on the Temporal dev server: the API (with the real Temporal gateway), the worker
(real workflows and activities over local Postgres and object storage) and fake stage functions."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from advisor_intel.activities import StageFunctions
from advisor_intel.activities.providers import Providers
from advisor_intel.api.app import create_app
from advisor_intel.api.temporal import ClientGateway
from advisor_intel.config import Settings
from advisor_intel.extract.reducer import make_ops
from advisor_intel.models.fna import ExportFile
from advisor_intel.models.runs import CallResult, VersionPins
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
    providers = Providers(asr=lambda s, p: "asr", diarizer=lambda s, p: "diarizer", second_pass=lambda s, p: None)
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
                assert notifier.sent and notifier.sent[0].kind == "review_ready"
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

                export = (await c.get("/calls/call-e2e/fna")).json()
                doc = ExportFile.model_validate(export["fna"]).document
                assert doc.advice.summary and "retirement age" in doc.advice.summary.lower()
                assert export["sidecar"]["fields"]["retirement.client.retirementAge"]["review"]["status"] == "accepted"
                assert (await store.get_forecast(run_id, mode="final")) is not None
                events = [e.action for e in await store.review_events(run_id=run_id)]
                assert sorted(events[:2]) == ["accept", "clear_flag"] and events[-1] == "submit"
                assert fakes.calls == {"ingest": 1, "discourse": 1, "agent": 1, "voi": 1, "forecast": 3, "extract_followup": 1}

                # An answer after completion goes to FollowupIntakeWorkflow, which re-runs the final forecast.
                late = await c.post("/calls/call-e2e/followups/fu.retirement_age/answer", json={"text": "We now think sixty-four"})
                assert late.json()["routed"] == "intake_workflow"
                intake = await env.client.get_workflow_handle(late.json()["workflow_id"], result_type=FollowupIntakeResult).result()
                assert intake.intake.applied == 1 and intake.forecast.mode == "final" and fakes.calls["forecast"] == 4


async def test_rerun_by_reset_rewinds_to_the_stage_and_reuses_earlier_outputs(store, objects, temporal_bin):
    fakes, notifier = Fakes(), RecordingNotifier()
    tq = f"e2e-{uuid.uuid4().hex[:6]}"
    settings = Settings(env="test", api_token=TOKEN, task_queue=tq)
    stages = StageFunctions(fakes.overrides())
    providers = Providers(asr=lambda s, p: "asr", diarizer=lambda s, p: "diarizer", second_pass=lambda s, p: None)
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
