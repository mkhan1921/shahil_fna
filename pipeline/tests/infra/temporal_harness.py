"""Mock activities and a worker harness for workflow tests on the Temporal dev server.

The mocks have the real activity names and payload types, record every call, and return small
outcomes, so the workflows run exactly as in production without Postgres, object storage or models.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from advisor_intel.models.runs import AgentOutcome, RunState
from advisor_intel.workflows import ALL_WORKFLOWS, CallPipelineWorkflow
from advisor_intel.workflows import contracts as C
from advisor_intel.workflows.converter import data_converter
from advisor_intel.workflows.versioning import deployment_config, set_current_version


@dataclass
class MockActivities:
    partial: bool = False
    manual: bool = False
    failing_answers: tuple[str, ...] = ()
    failing_nightly: tuple[str, ...] = ()
    fail_discourse: bool = False
    slow_note_s: float = 0.0  # how long render_meeting_note takes (to race signals against the final steps)
    finetune_eligible: bool = True  # the §15.8 trigger holds
    rl_can_start: bool = True  # the §15.9 start conditions hold
    hacking_passed: bool = True
    switch_groups: tuple[str, ...] = ("retirement",)  # groups the candidate wins on the gold experiment
    calls: list[tuple[str, Any]] = field(default_factory=list)
    profile_version: int = 3

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def inputs(self, name: str) -> list[Any]:
        return [p for n, p in self.calls if n == name]

    def build(self) -> list[Any]:
        m = self

        @activity.defn(name=C.INGEST_AUDIO)
        async def ingest_audio(inp: C.StageInput) -> C.IngestOutcome:
            m.calls.append((C.INGEST_AUDIO, inp))
            return C.IngestOutcome(cache_key="asr-1", manual=m.manual)

        @activity.defn(name=C.RUN_DISCOURSE)
        async def run_discourse(inp: C.StageInput) -> C.StageOutcome:
            m.calls.append((C.RUN_DISCOURSE, inp))
            if m.fail_discourse:
                raise ApplicationError("discourse cannot run", type="StageUnavailable", non_retryable=True)
            return C.StageOutcome(stage="discourse", cache_key="disc-1")

        @activity.defn(name=C.RUN_AGENT)
        async def run_agent(inp: C.StageInput) -> AgentOutcome:
            m.calls.append((C.RUN_AGENT, inp))
            return AgentOutcome(
                partial=m.partial,
                reason="token budget exhausted" if m.partial else None,
                turns=12,
                unfinished_groups=["estate"] if m.partial else [],
            )

        @activity.defn(name=C.RUN_VOI)
        async def run_voi(inp: C.StageInput) -> C.VoiOutcome:
            m.calls.append((C.RUN_VOI, inp))
            return C.VoiOutcome(profile_version=m.profile_version, n_followups=2, n_flags=1)

        @activity.defn(name=C.RUN_FORECAST)
        async def run_forecast(inp: C.StageInput) -> C.ForecastOutcome:
            m.calls.append((C.RUN_FORECAST, inp))
            return C.ForecastOutcome(run_id=inp.run_id, profile_version=m.profile_version, mode=inp.args.forecast_mode, pos=0.72)

        @activity.defn(name=C.APPLY_REVIEW)
        async def apply_review(inp: C.ReviewInput) -> C.ReviewOutcome:
            m.calls.append((C.APPLY_REVIEW, inp))
            m.profile_version += 1
            return C.ReviewOutcome(profile_version=m.profile_version, applied=len(inp.review.ops), changes={"confirmed": 4, "changed": 1})

        @activity.defn(name=C.NOTIFY_ADVISOR)
        async def notify_advisor(inp: C.NotifyInput) -> C.NotifyOutcome:
            m.calls.append((C.NOTIFY_ADVISOR, inp))
            return C.NotifyOutcome(sent=True, channel="log")

        @activity.defn(name=C.SEND_REVIEW_REMINDER)
        async def send_review_reminder(inp: C.NotifyInput) -> C.NotifyOutcome:
            m.calls.append((C.SEND_REVIEW_REMINDER, inp))
            return C.NotifyOutcome(sent=True, channel="log")

        @activity.defn(name=C.INTAKE_FOLLOWUP)
        async def intake(inp: C.AnswerInput) -> C.IntakeOutcome:
            m.calls.append((C.INTAKE_FOLLOWUP, inp))
            if inp.answer.followup_id in m.failing_answers:
                raise ApplicationError("could not read the answer", type="InputError", non_retryable=True)
            m.profile_version += 1
            return C.IntakeOutcome(followup_id=inp.answer.followup_id, profile_version=m.profile_version, applied=1)

        @activity.defn(name=C.RENDER_NOTE)
        async def render_note(inp: C.StageInput) -> C.NoteOutcome:
            m.calls.append((C.RENDER_NOTE, inp))
            if m.slow_note_s:
                await asyncio.sleep(m.slow_note_s)
            return C.NoteOutcome(profile_version=m.profile_version, sentences=6)

        def check(name: str, inp: Any) -> None:
            m.calls.append((name, inp))
            if name in m.failing_nightly:
                raise ApplicationError(f"{name} broke", non_retryable=True)

        @activity.defn(name=C.BUILD_LABELS)
        async def build_labels(w: C.NightlyWindow) -> C.LabelsOutcome:
            check(C.BUILD_LABELS, w)
            return C.LabelsOutcome(since=w.since, until=w.until, n_events=40, n_labels=35, n_calls=3)

        @activity.defn(name=C.RETRAIN_CALIBRATOR)
        async def retrain(labels: C.LabelsOutcome) -> C.CalibratorOutcome:
            check(C.RETRAIN_CALIBRATOR, labels)
            return C.CalibratorOutcome(version="cal-2", promoted=True, n_labels=labels.n_labels)

        @activity.defn(name=C.REFIT_CONFORMAL)
        async def refit(cal: C.CalibratorOutcome) -> C.ConformalOutcome:
            check(C.REFIT_CONFORMAL, cal)
            return C.ConformalOutcome(calibrator_version=cal.version, tau={"*": 0.93}, n_calib_calls=55, auto_accept_enabled=True)

        @activity.defn(name=C.PROPOSE_LEXICON_FIT)
        async def lexicon_fit(w: C.NightlyWindow) -> C.LexiconFitOutcome:
            check(C.PROPOSE_LEXICON_FIT, w)
            return C.LexiconFitOutcome(n_pairs=25, keys_fitted=["about"])

        @activity.defn(name=C.SYNC_ERROR_DATASET)
        async def sync_errors(w: C.NightlyWindow) -> C.DatasetSyncOutcome:
            check(C.SYNC_ERROR_DATASET, w)
            return C.DatasetSyncOutcome(dataset="production-errors", inserted=4)

        @activity.defn(name=C.SYNC_GOLD_CANDIDATES)
        async def sync_gold(w: C.NightlyWindow) -> C.DatasetSyncOutcome:
            check(C.SYNC_GOLD_CANDIDATES, w)
            return C.DatasetSyncOutcome(dataset="gold-candidates", inserted=1)

        @activity.defn(name=C.RUN_MODEL_SWAP)
        async def model_swap(a: C.ModelSwapArgs) -> C.ModelSwapOutcome:
            check(C.RUN_MODEL_SWAP, a)
            return C.ModelSwapOutcome(results=[{"role": "small", "won": False}])

        # Fine-tuning (§15.8) and RL (§15.9)

        @activity.defn(name=C.EXPORT_FINETUNE_DATA)
        async def export_finetune(a: C.FinetuneArgs) -> C.TrainingExportOutcome:
            check(C.EXPORT_FINETUNE_DATA, a)
            if not m.finetune_eligible:
                return C.TrainingExportOutcome(
                    label=a.label,
                    eligible=False,
                    reason="120 reviewed calls < 300, and no slot group has 2000 labelled facts",
                    n_reviewed_calls=120,
                )
            return C.TrainingExportOutcome(
                label=a.label,
                eligible=True,
                reason="320 reviewed calls",
                groups=list(a.groups) or ["retirement", "income"],
                n_reviewed_calls=320,
                n_clients=290,
                examples=1800,
                by_split={"train": 1440, "val": 180, "test": 180},
                skipped_heldout_calls=20,
                prompt_versions={"extract_values": "v3"},
                data_uri="file:///objects/training/runs/x/data/examples.jsonl",
            )

        @activity.defn(name=C.SFT_TRAIN_EXTRACTOR)
        async def sft(inp: C.SftInput) -> C.SftOutcome:
            check(C.SFT_TRAIN_EXTRACTOR, inp)
            return C.SftOutcome(
                label=inp.export.label,
                base_model="base",
                checkpoint=f"sft-{inp.export.label}-final",
                model=f"vllm:extractor-{inp.export.label}",
                steps=90,
                examples=1440,
                final_loss=0.41,
            )

        @activity.defn(name=C.EVALUATE_EXTRACTOR)
        async def evaluate(inp: C.CandidateEvalInput) -> C.CandidateEvalOutcome:
            check(C.EVALUATE_EXTRACTOR, inp)
            blocked = inp.hacking_passed is False or inp.human_signed_off is False
            decisions = [
                C.GroupDecision(
                    group=g,
                    switch=g in m.switch_groups and not blocked,
                    reasons=["matches or beats the incumbent"] if g in m.switch_groups and not blocked else ["slot_recall 0.880 < 0.900"],
                    metrics={"status_accuracy": [0.9, 0.92], "slot_recall": [0.9, 0.9 if g in m.switch_groups else 0.88]},
                )
                for g in inp.groups
            ]
            return C.CandidateEvalOutcome(
                label=inp.label,
                candidate_model=inp.candidate_model,
                incumbent_model=inp.incumbent_model or "frontier",
                groups=list(inp.groups),
                decisions=decisions,
                switched=[d.group for d in decisions if d.switch],
                cost_incumbent=0.21,
                cost_candidate=0.08,
                gold_calls=20 if inp.heldout_only else 60,
            )

        @activity.defn(name=C.PROMOTE_EXTRACTOR)
        async def promote(inp: C.PromotionInput) -> C.PromotionOutcome:
            check(C.PROMOTE_EXTRACTOR, inp)
            switched = list(inp.evaluation.switched)
            return C.PromotionOutcome(
                label=inp.label,
                model=inp.evaluation.candidate_model,
                switched=switched,
                routing_uri="file:///objects/training/runs/x/routing.yaml" if switched else None,
                pull_request="https://github.com/example/shahil_fna/pull/42" if switched else None,
                registry_uri="file:///objects/training/registry/extractor/x.json",
                calibrator_retrain_needed=switched,
            )

        @activity.defn(name=C.CHECK_RL_START)
        async def rl_start(a: C.RlArgs) -> C.RlStartOutcome:
            check(C.CHECK_RL_START, a)
            ok = m.rl_can_start
            return C.RlStartOutcome(
                label=a.label,
                can_start=ok,
                checks={"fine_tuned_extractor_live": ok, "metrics_plateaued": ok, "errors_targetable": True, "reviewed_calls": True},
                reasons=[] if ok else ["the fine-tuned extractor is not live for any slot group"],
                live_groups=["retirement"] if ok else [],
                reference_model="vllm:extractor-m6" if ok else None,
                reference_checkpoint="sft-m6-final" if ok else None,
                n_reviewed_calls=320,
            )

        @activity.defn(name=C.EXPORT_RL_PROMPTS)
        async def rl_export(inp: C.RlExportInput) -> C.RlExportOutcome:
            check(C.EXPORT_RL_PROMPTS, inp)
            return C.RlExportOutcome(
                label=inp.start.label,
                n_prompts=600,
                by_group={"retirement": 600},
                n_reviewed_calls=300,
                skipped_heldout_calls=20,
                prompts_uri="file:///p",
            )

        @activity.defn(name=C.GRPO_TRAIN_EXTRACTOR)
        async def grpo(inp: C.GrpoInput) -> C.GrpoOutcome:
            check(C.GRPO_TRAIN_EXTRACTOR, inp)
            return C.GrpoOutcome(
                label=inp.export.label,
                base_model="base",
                reference_checkpoint=inp.start.reference_checkpoint or "",
                checkpoint=f"grpo-{inp.export.label}-50",
                model=f"vllm:extractor-{inp.export.label}",
                steps=50,
                first_mean_reward=1.1,
                last_mean_reward=1.6,
            )

        @activity.defn(name=C.RL_HACKING_CHECKS)
        async def hacking(inp: C.HackingInput) -> C.HackingOutcome:
            check(C.RL_HACKING_CHECKS, inp)
            return C.HackingOutcome(
                label=inp.training.label,
                passed=m.hacking_passed,
                failures=[] if m.hacking_passed else ["retirement: dodging penalties"],
                flagged_samples=2,
                review_sheet_uri="file:///sheet",
                human_signed_off=inp.args.human_signed_off,
            )

        return [
            export_finetune,
            sft,
            evaluate,
            promote,
            rl_start,
            rl_export,
            grpo,
            hacking,
            ingest_audio,
            run_discourse,
            run_agent,
            run_voi,
            run_forecast,
            apply_review,
            notify_advisor,
            send_review_reminder,
            intake,
            render_note,
            build_labels,
            retrain,
            refit,
            lexicon_fit,
            sync_errors,
            sync_gold,
            model_swap,
        ]


@contextlib.asynccontextmanager
async def temporal_env(
    temporal_bin: str, objects: Any = None, *, threshold: int | None = None, interceptors: list[Any] | None = None
) -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_local(
        dev_server_existing_path=temporal_bin,
        data_converter=data_converter(objects, threshold=threshold),
        interceptors=interceptors or [],
        dev_server_log_level="error",
    )
    try:
        yield env
    finally:
        await env.shutdown()


@contextlib.asynccontextmanager
async def running_worker(client: Client, mocks: MockActivities) -> AsyncIterator[str]:
    """A versioned worker (its build made current) running every workflow over the mocks."""
    task_queue = f"tq-{uuid.uuid4().hex[:8]}"
    cfg = deployment_config(build=f"test-{uuid.uuid4().hex[:6]}")
    async with Worker(client, task_queue=task_queue, workflows=ALL_WORKFLOWS, activities=mocks.build(), deployment_config=cfg):
        await set_current_version(client, cfg.version)
        yield task_queue


async def wait_state(handle: WorkflowHandle, state: RunState, timeout: float = 20.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        cur = await handle.query(CallPipelineWorkflow.run_state)
        if cur == state:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"workflow state is {cur}, waited for {state}")
        await asyncio.sleep(0.1)


async def wait_for(pred, timeout: float = 20.0, interval: float = 0.1) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(interval)


EXEC_TIMEOUT = timedelta(minutes=2)
