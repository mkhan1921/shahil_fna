"""The fine-tuning (§15.8) and RL (§15.9) activities against local Postgres and object storage, with a fake
Tinker-compatible training service: the export never carries a held-out call, splits by client and gates on the
trigger; SFT and GRPO run through the injected client (and fail at once without one); the gold experiment decides per
slot group with the cost gate; promotion writes the routing change, the registry record and the pull request."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from advisor_intel.activities import StageFunctions
from advisor_intel.activities.keys import ingest_key
from advisor_intel.activities.training import MODEL_PREFIX, REGISTRY_PREFIX, TrainingActivities, composite_score
from advisor_intel.config import Settings
from advisor_intel.evals import finetune as FT
from advisor_intel.evals.datasets import LocalStore, sync_gold
from advisor_intel.evals.extraction_eval import EvalReport
from advisor_intel.evals.synthetic import generate_calls
from advisor_intel.extract.reducer import apply_state_ops, make_ops
from advisor_intel.extract.training_prompt import COMBINED, render_stage_prompt, rl_postprocess, tasks_from_profile
from advisor_intel.models.runs import VersionPins
from advisor_intel.schema import compiler as C
from advisor_intel.schema.slots import load_catalog
from advisor_intel.storage import CallRecord, ReviewEvent, Store
from advisor_intel.workflows import contracts as W
from tests.test_evals_finetune import DODGE, GOOD, INVENTED, FakeBackend, postprocess

from .factories import GROSS, RET_AGE, make_transcript, sample_document, seeded_profile, with_call_facts

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
N_CALLS = 6  # three clients, two calls each; call-0 is a held-out gold call, call-1 a gold call
RET_SLOT = "retirement.{p}.retirementAge"


def env(execution: str = "exec-1", heartbeats: list | None = None) -> ActivityEnvironment:
    e = ActivityEnvironment()
    e.info = dataclasses.replace(e.info, workflow_id="finetune-t1", workflow_run_id=execution, heartbeat_timeout=timedelta(seconds=3))
    if heartbeats is not None:
        e.on_heartbeat = lambda *d: heartbeats.append(d)
    return e


async def seed_reviewed_call(store: Store, i: int) -> None:
    call_id, run_id, client_id = f"call-{i}", f"call-{i}-run-a", f"cl-{i // 2}"
    doc_id = await store.save_client_document(client_id, sample_document(), source="import")
    await store.upsert_call(
        CallRecord(
            call_id=call_id,
            workflow_id=run_id,
            advisor_id="adv-1",
            client_ids=[client_id],
            started_at=NOW,
            meta={"prior_document_id": doc_id},
        )
    )
    pins = VersionPins()
    await store.register_run(run_id, call_id, pins)
    await store.save_transcript(call_id, ingest_key(pins), make_transcript(call_id))
    profile = await store.save_profile(with_call_facts(seeded_profile(run_id, call_id)))
    # The adviser accepts the retirement age and rejects the gross income (a misheard amount).
    specs = [
        ("CONFIRM", RET_AGE, None, {"review": {"status": "accepted", "reviewer_id": "adv-1"}}, "reviewer"),
        ("RETRACT", GROSS, None, {"reason": "misheard"}, "reviewer"),
    ]
    reviewed = await store.save_profile(apply_state_ops(profile, make_ops(profile, specs)).profile)
    await store.record_review_events(
        [
            ReviewEvent(
                workflow_id=run_id, action="submit", reviewer_id="adv-1", at=NOW + timedelta(minutes=i), profile_version=reviewed.version
            )
        ]
    )


class FakeService:
    """A Tinker-compatible service over the fake backend of the evals tests."""

    def __init__(self, completions: dict[str, list[str]] | None = None) -> None:
        self.backend = FakeBackend(completions or {})
        self.training_clients: list[tuple[str, int]] = []
        self.samplers: list[str | None] = []
        self.loaded: list[str] = []
        self.backend.load_state = lambda path: self.loaded.append(path)  # type: ignore[attr-defined]

    def create_lora_training_client(self, base_model: str, rank: int = 32) -> Any:
        self.training_clients.append((base_model, rank))
        return self.backend

    def create_sampling_client(self, base_model: str | None = None, model_path: str | None = None) -> Any:
        self.samplers.append(model_path or base_model)
        return self.backend


def _report(group_scores: dict[str, tuple[float, float, float]], ece: dict[str, float], cost: float | None) -> EvalReport:
    return EvalReport(
        n_items=20,
        scores={"cost_per_call_minute": cost},
        summary={"by_group": {g: dict(zip(FT.PROMOTION_METRICS, s)) for g, s in group_scores.items()}, "ece_by_group": ece},
    )


@pytest.fixture
async def ctx(store: Store, objects, tmp_path):
    for i in range(N_CALLS):
        await seed_reviewed_call(store, i)
    # The gold dataset: call-0 is one of the held-out calls, call-1 an adjudicated gold call.
    ds = LocalStore(tmp_path / "datasets")
    base = generate_calls(1, seed=41)[0].gold
    sync_gold(
        [
            base.model_copy(update={"call_id": "call-0", "source": "real", "split": "heldout"}),
            base.model_copy(update={"call_id": "call-1", "source": "real", "split": "dev"}),
        ],
        store=ds,
    )
    reports: dict[str, EvalReport] = {}

    def gold_experiment(*, settings, heldout_only=False):
        def run(pins):
            return reports.get(pins.reasoning_model) or reports["*"]

        run.n_cases = 20 if heldout_only else 60
        return run

    stages = StageFunctions(
        {
            "dataset_store": lambda **kw: ds,
            "current_version_pins": lambda: VersionPins(reasoning_model="frontier"),
            "gold_experiment": gold_experiment,
            "rl_postprocess": postprocess,
        }
    )
    service = FakeService({"retirement": [GOOD, INVENTED, DODGE]})
    prs: list[tuple[W.CandidateEvalOutcome, str]] = []

    def open_pr(ev, text):
        prs.append((ev, text))
        return "https://github.com/example/shahil_fna/pull/7"

    acts = TrainingActivities(
        store=store,
        objects=objects,
        settings=Settings(finetune_base_model="base-x", finetune_lora_rank=16),
        stages=stages,
        training_service=service,
        pull_requests=open_pr,
    )
    return SimpleNamespace(store=store, objects=objects, acts=acts, service=service, reports=reports, prs=prs, stages=stages)


async def _rows(objects, uri: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (await objects.get_bytes(uri)).decode().splitlines() if line.strip()]


# --------------------------------------------------------------------------- §15.8 export


async def test_export_keeps_heldout_calls_out_splits_by_client_and_renders_the_production_prompt(ctx):
    beats: list = []
    out = await env(heartbeats=beats).run(ctx.acts.export_finetune_data, W.FinetuneArgs(label="t1", min_reviewed_calls=4))
    assert out.eligible and out.reason == "5 reviewed calls" and out.n_reviewed_calls == 5 and out.n_clients == 3
    assert (
        out.heldout_ids == ["call-0"] and out.skipped_heldout_calls == 1 and out.labelled_facts_by_group == {"income": 5, "retirement": 5}
    )
    assert set(out.by_group) == {"income", "retirement"} and sum(out.by_split.values()) == out.examples > 0
    assert beats  # one heartbeat per reviewed call
    rows = await _rows(ctx.objects, out.data_uri)
    assert len(rows) == out.examples and "call-0" not in {r["call_id"] for r in rows}
    by_client: dict[str, set[str]] = {}
    for r in rows:
        by_client.setdefault(r["client_id"], set()).add(r["split"])
    assert all(len(s) == 1 for s in by_client.values())  # a client's calls land in one split
    assert {r["weight"] for r in rows if r["call_id"] == "call-1"} == {3.0} and {r["weight"] for r in rows if r["call_id"] == "call-2"} == {
        1.0
    }
    # The chat files per split hold the same rows, without a held-out call.
    for split in ("train", "val", "test"):
        chat = await _rows(ctx.objects, f"training/runs/t1/data/{split}.jsonl")
        assert len(chat) == out.by_split[split] and all(c["metadata"]["call_id"] != "call-0" for c in chat)
    # The exact production prompt: the stage instructions with the slot group's block, then the window and fields.
    ret = next(r for r in rows if r["group"] == "retirement" and r["stage"] == "statuses")
    system, user, target = ret["messages"]
    assert system["role"] == "system" and "## Slot group: retirement" in system["content"]
    assert "# Transcript window" in user["content"] and "retire at sixty" in user["content"] and f"- {RET_AGE}" in user["content"]
    assert json.loads(target["content"]) == [{"fna_path": "retirement.client.retirementAge", "status": "stated", "segment_ids": ["s0002"]}]
    income = next(r for r in rows if r["group"] == "income" and r["stage"] == "statuses")
    assert json.loads(income["messages"][-1]["content"]) == [
        {"fna_path": "income.client.grossMonthly", "status": "not_discussed", "segment_ids": []}
    ]
    assert not any(
        r["group"] == "income" and r["stage"] == "values" and json.loads(r["messages"][-1]["content"]) for r in rows
    )  # rejected: no value
    # A retry finds the recorded export; a new execution (a reset) exports again.
    again = await env().run(ctx.acts.export_finetune_data, W.FinetuneArgs(label="t1", min_reviewed_calls=4))
    assert again.cached and again.examples == out.examples
    assert not (await env("exec-2").run(ctx.acts.export_finetune_data, W.FinetuneArgs(label="t1", min_reviewed_calls=4))).cached


async def test_export_gates_on_the_trigger_and_lets_an_eligible_group_go_early(ctx):
    below = await env().run(ctx.acts.export_finetune_data, W.FinetuneArgs(label="t2"))
    assert (
        not below.eligible
        and below.reason == "5 reviewed calls < 300, and no slot group has 2000 labelled facts"
        and below.data_uri is None
    )
    early = await env().run(ctx.acts.export_finetune_data, W.FinetuneArgs(label="t3", min_group_facts=5, groups=["retirement"]))
    assert (
        early.eligible and early.groups == ["retirement"] and set(early.by_group) == {"retirement"} and "≥ 5 labelled facts" in early.reason
    )
    forced = await env().run(ctx.acts.export_finetune_data, W.FinetuneArgs(label="t4", force=True))
    assert forced.eligible and forced.reason.startswith("forced by the operator") and forced.examples > 0
    extra = await env().run(ctx.acts.export_finetune_data, W.FinetuneArgs(label="t5", min_reviewed_calls=4, heldout_ids=["call-3"]))
    assert extra.heldout_ids == ["call-0", "call-3"] and "call-3" not in {r["call_id"] for r in await _rows(ctx.objects, extra.data_uri)}


# --------------------------------------------------------------------------- §15.8 SFT and the experiment


async def test_sft_trains_through_the_injected_client_once(ctx):
    export = await env().run(ctx.acts.export_finetune_data, W.FinetuneArgs(label="t1", min_reviewed_calls=4))
    out = await env().run(ctx.acts.sft_train_extractor, W.SftInput(args=W.FinetuneArgs(label="t1", epochs=2), export=export))
    assert out.checkpoint == "sft-t1-final" and out.model == f"{MODEL_PREFIX}t1" and out.base_model == "base-x"
    assert (
        ctx.service.training_clients == [("base-x", 16)] and ctx.service.backend.batches and out.steps == len(ctx.service.backend.batches)
    )
    assert ctx.service.backend.batches[0][0] == "cross_entropy" and out.examples == export.by_split["train"]
    assert json.loads((await ctx.objects.get_bytes(out.metrics_uri)).decode())[-1]["checkpoint"] == "sft-t1-final"
    n = len(ctx.service.backend.batches)
    again = await env().run(ctx.acts.sft_train_extractor, W.SftInput(args=W.FinetuneArgs(label="t1"), export=export))
    assert again.cached and len(ctx.service.backend.batches) == n  # a retry does not train again


async def test_training_steps_fail_at_once_without_a_training_service_or_base_model(ctx, store, objects):
    export = await env().run(ctx.acts.export_finetune_data, W.FinetuneArgs(label="t1", min_reviewed_calls=4))
    bare = TrainingActivities(store=store, objects=objects, settings=Settings(), stages=ctx.stages)
    with pytest.raises(ApplicationError) as ei:
        await env().run(
            bare.sft_train_extractor, W.SftInput(args=W.FinetuneArgs(label="t9"), export=export.model_copy(update={"label": "t9"}))
        )
    assert ei.value.type == "TrainingClientUnavailable" and ei.value.non_retryable and "base model" in str(ei.value)
    no_service = TrainingActivities(store=store, objects=objects, settings=Settings(finetune_base_model="b"), stages=ctx.stages)
    with pytest.raises(ApplicationError) as ei:
        await env().run(
            no_service.sft_train_extractor, W.SftInput(args=W.FinetuneArgs(label="t9"), export=export.model_copy(update={"label": "t9"}))
        )
    assert ei.value.type == "TrainingClientUnavailable" and "ADVISOR_INTEL_TINKER_API_KEY" in str(ei.value)


async def test_candidate_is_decided_per_group_with_the_cost_gate(ctx):
    ctx.reports["frontier"] = _report(
        {"income": (0.9, 0.9, 0.8), "retirement": (0.9, 0.9, 0.8)}, {"income": 0.05, "retirement": 0.05}, 0.21
    )
    ctx.reports["vllm:extractor-t1"] = _report(
        {"income": (0.92, 0.85, 0.85), "retirement": (0.92, 0.9, 0.85)}, {"income": 0.04, "retirement": 0.05}, 0.08
    )
    out = await env().run(ctx.acts.evaluate_extractor_candidate, W.CandidateEvalInput(label="t1", candidate_model="vllm:extractor-t1"))
    assert out.incumbent_model == "frontier" and out.groups == ["income", "retirement"] and out.gold_calls == 60
    d = {x.group: x for x in out.decisions}
    assert d["retirement"].switch and out.switched == ["retirement"] and d["retirement"].metrics["status_accuracy"] == [0.9, 0.92]
    assert not d["income"].switch and d["income"].reasons == ["slot_recall 0.850 < 0.900"]
    assert (out.cost_incumbent, out.cost_candidate) == (0.21, 0.08)
    assert (await ctx.objects.get_json(out.candidate_report_uri))["scores"]["cost_per_call_minute"] == 0.08
    # Not cheaper per call-minute: no quality loss is not enough (§15.8, §16 M6 gate).
    ctx.reports["vllm:extractor-t2"] = ctx.reports["vllm:extractor-t1"].model_copy(update={"scores": {"cost_per_call_minute": 0.3}})
    dear = await env().run(
        ctx.acts.evaluate_extractor_candidate, W.CandidateEvalInput(label="t2", candidate_model="vllm:extractor-t2", groups=["retirement"])
    )
    assert dear.switched == [] and dear.decisions[0].reasons == ["cost per call-minute 0.3000 is not lower than 0.2100"]
    # RL: must beat its reference on the held-out calls, pass the hacking checks and be signed off (§15.9).
    ctx.reports["vllm:extractor-m6"] = ctx.reports["frontier"]
    ctx.reports["vllm:extractor-rl"] = _report({"retirement": (0.9, 0.9, 0.8)}, {"retirement": 0.05}, 0.08)
    tie = await env().run(
        ctx.acts.evaluate_extractor_candidate,
        W.CandidateEvalInput(
            label="r1",
            candidate_model="vllm:extractor-rl",
            groups=["retirement"],
            incumbent_model="vllm:extractor-m6",
            heldout_only=True,
            require_better=True,
            hacking_passed=False,
            human_signed_off=None,
        ),
    )
    assert tie.incumbent_model == "vllm:extractor-m6" and tie.gold_calls == 20 and not tie.decisions[0].switch
    assert tie.decisions[0].reasons == [
        "does not beat the incumbent on any of status accuracy, slot recall or value coverage",
        "the reward-hacking checks failed",
        "the sampled outputs have not been signed off by a person",
    ]


async def test_promotion_writes_the_routing_change_the_registry_and_the_pull_request(ctx):
    ev = W.CandidateEvalOutcome(
        label="t1",
        candidate_model="vllm:extractor-t1",
        incumbent_model="frontier",
        groups=["income", "retirement"],
        decisions=[
            W.GroupDecision(group="income", switch=False, reasons=["slot_recall 0.850 < 0.900"], metrics={"slot_recall": [0.9, 0.85]}),
            W.GroupDecision(group="retirement", switch=True, metrics={"status_accuracy": [0.9, 0.92], "ece": [0.05, 0.05]}),
        ],
        switched=["retirement"],
        cost_incumbent=0.21,
        cost_candidate=0.08,
        gold_calls=60,
    )
    inp = W.PromotionInput(
        label="t1", kind="sft", evaluation=ev, checkpoint="sft-t1-final", base_model="base-x", prompt_versions={"extract_values": "v3"}
    )
    out = await env().run(ctx.acts.promote_extractor_groups, inp)
    assert (
        out.switched == out.calibrator_retrain_needed == ["retirement"]
        and out.pull_request == "https://github.com/example/shahil_fna/pull/7"
    )
    routing = yaml.safe_load((await ctx.objects.get_bytes(out.routing_uri)).decode())
    assert routing["extraction_groups"] == {"retirement": "vllm:extractor-t1"}
    assert routing["history"][-1]["extraction_groups"] == ["retirement"] and routing["history"][-1]["training_run"] == "t1"
    [(pr_ev, pr_text)] = ctx.prs
    assert pr_ev.switched == ["retirement"] and "extraction_groups" in pr_text
    record = await ctx.objects.get_json(out.registry_uri)
    assert record["kind"] == "sft" and record["model"] == "vllm:extractor-t1" and record["checkpoint"] == "sft-t1-final"
    assert (
        record["switched"] == ["retirement"]
        and record["prompt_versions"] == {"extract_values": "v3"}
        and record["pull_request"] == out.pull_request
    )
    assert composite_score(record["decisions"][1]["metrics"]) is None  # slot recall and value coverage were not recorded for it
    # Nothing switched: no routing change or pull request, but the run is still on record.
    none = ev.model_copy(update={"label": "t2", "switched": [], "decisions": [ev.decisions[0]]})
    out2 = await env().run(ctx.acts.promote_extractor_groups, W.PromotionInput(label="t2", kind="sft", evaluation=none))
    assert out2.routing_uri is None and out2.pull_request is None and len(ctx.prs) == 1
    assert (await ctx.objects.get_json(out2.registry_uri))["switched"] == [] and len(await ctx.objects.list(REGISTRY_PREFIX)) == 2


# --------------------------------------------------------------------------- §15.9


def _registry_row(
    label: str, model: str, retirement: tuple[float, float, float], *, kind: str = "sft", checkpoint: str = ""
) -> dict[str, Any]:
    return {
        "label": label,
        "kind": kind,
        "model": model,
        "checkpoint": checkpoint or f"sft-{label}-final",
        "decisions": [{"group": "retirement", "switch": True, "metrics": {m: [0.8, v] for m, v in zip(FT.PROMOTION_METRICS, retirement)}}],
    }


async def test_rl_start_conditions(ctx, store, objects):
    live = Settings(finetune_base_model="base-x", extraction_groups={"retirement": "vllm:extractor-m6"})
    acts = TrainingActivities(store=store, objects=objects, settings=live, stages=ctx.stages, training_service=ctx.service)
    args = W.RlArgs(label="r1", min_reviewed_calls=5)
    # Nothing on record for the live model, and no corrections yet.
    out = await env().run(acts.check_rl_start_conditions, args)
    assert not out.can_start and out.checks == {
        "fine_tuned_extractor_live": True,
        "metrics_plateaued": False,
        "errors_targetable": False,
        "reviewed_calls": True,
    }
    assert (
        out.reasons[0].startswith("no training record for the live model 'vllm:extractor-m6'")
        and out.reasons[1] == "no reviewer corrections yet"
    )
    assert out.n_reviewed_calls == 6 and out.reference_model == "vllm:extractor-m6" and out.live_groups == ["retirement"]
    # The live model's round, then two retraining rounds that did not improve on it; corrections a reward can target.
    for i, (label, model, scores) in enumerate(
        [
            ("m6", "vllm:extractor-m6", (0.9, 0.9, 0.8)),
            ("m6b", "vllm:extractor-m6b", (0.9, 0.9, 0.8)),
            ("m6c", "vllm:extractor-m6c", (0.89, 0.9, 0.81)),
        ]
    ):
        await objects.put_json(f"{REGISTRY_PREFIX}2026100{i}T000000-{label}.json", _registry_row(label, model, scores))
    await store.record_review_events(
        [
            ReviewEvent(workflow_id="call-2-run-a", fact_id=GROSS, action=a, reviewer_id="adv-1", at=NOW)
            for a in ("reject", "reject", "reattribute", "mark_unknown")
        ]
    )
    ok = await env().run(acts.check_rl_start_conditions, args.model_copy(update={"label": "r2"}))
    assert ok.can_start and not ok.forced and all(ok.checks.values()) and ok.reasons == []
    assert ok.reference_checkpoint == "sft-m6-final" and ok.targetable_share == 0.75
    # A later round that improved: the metrics have not plateaued; an operator can still force the run.
    await objects.put_json(f"{REGISTRY_PREFIX}20261004T000000-m6d.json", _registry_row("m6d", "vllm:extractor-m6d", (0.95, 0.92, 0.85)))
    better = await env().run(acts.check_rl_start_conditions, args.model_copy(update={"label": "r3"}))
    assert not better.can_start and better.reasons == [
        "the fine-tuned extractor's gold metrics have not plateaued over 2 retraining rounds"
    ]
    forced = await env().run(acts.check_rl_start_conditions, args.model_copy(update={"label": "r4", "force": True}))
    assert forced.can_start and forced.forced and forced.reasons == better.reasons
    # Not live for the requested group.
    off = await env().run(acts.check_rl_start_conditions, args.model_copy(update={"label": "r5", "groups": ["income"]}))
    assert not off.can_start and off.reasons[0].startswith("the fine-tuned extractor is not live for income")
    unrouted = TrainingActivities(store=store, objects=objects, settings=Settings(), stages=ctx.stages)
    none = await env().run(unrouted.check_rl_start_conditions, args.model_copy(update={"label": "r6"}))
    assert not none.checks["fine_tuned_extractor_live"] and none.reference_model is None


async def test_rl_prompts_grpo_and_the_hacking_checks_on_the_fake_backend(ctx):
    start = W.RlStartOutcome(
        label="r1", can_start=True, live_groups=["retirement"], reference_model="vllm:extractor-m6", reference_checkpoint="sft-m6-final"
    )
    args = W.RlArgs(label="r1", config={"steps": 3, "prompts_per_step": 2, "group_size": 4}, n_review_samples=3, human_signed_off=True)
    export = await env().run(ctx.acts.export_rl_prompts, W.RlExportInput(args=args, start=start))
    assert (
        export.n_prompts == 5
        and export.by_group == {"retirement": 5}
        and export.skipped_heldout_calls == 1
        and export.heldout_ids == ["call-0"]
    )
    prompts = [FT.RLPrompt.model_validate(r) for r in await _rows(ctx.objects, export.prompts_uri)]
    assert {p.call_id for p in prompts} == {f"call-{i}" for i in range(1, 6)}
    p = prompts[0]
    assert p.slots == [(RET_SLOT, "client")] and [g.status for g in p.reviewed] == ["stated"]
    assert (p.reviewed[0].value.p10, p.reviewed[0].value.p90) == (61.0, pytest.approx(63.78, abs=0.01))  # the reviewed triangular 60/62/65
    assert (
        p.segments == {"s0002": "I'd like to retire at sixty, maybe sixty-five."}
        and "Fields (status and value)" in p.messages[1]["content"]
    )
    # GRPO starts from the fine-tuned checkpoint, with the reference sampler on it, and scores rollouts with the reward.
    grpo = await env().run(ctx.acts.grpo_train_extractor, W.GrpoInput(args=args, start=start, export=export))
    assert ctx.service.loaded == ["sft-m6-final"] and ctx.service.samplers[:1] == ["sft-m6-final"]
    assert (
        grpo.steps == 3
        and grpo.checkpoint.startswith("grpo-r1-")
        and grpo.model == f"{MODEL_PREFIX}r1"
        and grpo.first_mean_reward is not None
    )
    assert ctx.service.backend.batches and ctx.service.backend.batches[0][0] == "importance_sampling"
    assert len(json.loads((await ctx.objects.get_bytes(grpo.metrics_uri)).decode())) == 3
    assert (await env().run(ctx.acts.grpo_train_extractor, W.GrpoInput(args=args, start=start, export=export))).cached
    # The hacking checks sample the candidate and the reference on the same prompts and write the review sheet.
    hacking = await env().run(ctx.acts.run_rl_hacking_checks, W.HackingInput(args=args, export=export, training=grpo))
    assert hacking.passed and hacking.failures == [] and set(hacking.not_discussed) == {"retirement"} and hacking.human_signed_off is True
    sheet = await _rows(ctx.objects, hacking.review_sheet_uri)
    assert len(sheet) == 3 and all(s["reviewer_verdict"] is None and s["group"] == "retirement" for s in sheet)
    assert ctx.service.samplers[-2:] == [grpo.checkpoint, "sft-m6-final"]
    # Held-out calls never reach RL, even through a prompt file an operator edited.
    leaked = export.model_copy(update={"heldout_ids": ["call-0", "call-1"], "label": "r2"})
    with pytest.raises(ApplicationError, match="held-out"):
        await env().run(
            ctx.acts.grpo_train_extractor, W.GrpoInput(args=args.model_copy(update={"label": "r2"}), start=start, export=leaked)
        )


def test_the_default_postprocess_reads_a_combined_output_through_the_normaliser():
    catalog = load_catalog()
    slot = catalog.slot(RET_SLOT)
    model = C.compile_combined("retirement", [slot], {RET_SLOT: ("client",)})
    key = C._key(RET_SLOT)
    out = {
        key: [
            {
                "instance": "client",
                "status": "stated",
                "segment_ids": ["s0002"],
                "hypotheses": [
                    {
                        "intermediate": {"type": "AGE", "form": "range", "value": 0, "low": 60, "high": 65, "vague_word": ""},
                        "weight": 1.0,
                        "rationale": "between sixty and sixty-five",
                        "revises": False,
                    }
                ],
                "hedges": ["maybe"],
                "speaker_attribution": "client",
                "said": "retire at sixty, maybe sixty-five",
                "interpretation": "a range of 60 to 65",
                "self_score": 0.8,
            }
        ]
    }
    model.model_validate(out)  # the strict schema the policy decodes under
    prompt = FT.RLPrompt(
        prompt_id="p", call_id="c", group="retirement", messages=[], reviewed=[], segments={}, slots=[(RET_SLOT, "client")]
    )
    [fact] = rl_postprocess(json.dumps(out), prompt)
    assert fact.slot_id == RET_SLOT and fact.instance_key == "client" and fact.status == "stated" and fact.range_like
    assert fact.value is not None and fact.value.unit == "age" and fact.quotes == [("s0002", "retire at sixty, maybe sixty-five")]
    assert fact.self_score == 0.8 and fact.attribution == "client"
    assert rl_postprocess("not json", prompt) == [] and rl_postprocess(json.dumps({key: []}), prompt) == []


def test_tasks_and_prompts_are_rebuilt_from_the_reviewed_profile():
    profile = with_call_facts(seeded_profile())
    discourse = SimpleNamespace(topic_index={"retirement": ["s0001", "s0002"]}, rewrites={}, revisions=[], entities={})
    tasks = tasks_from_profile(profile, None)
    assert [(t.slot_group, t.slot_ids, t.instance_keys, t.segment_ids) for t in tasks] == [
        ("income", ["income.{p}.grossMonthly"], ["client"], ["s0003"]),
        ("retirement", [RET_SLOT], ["client"], ["s0002"]),
    ]
    [ret] = tasks_from_profile(profile, discourse, groups=["retirement"])  # type: ignore[arg-type]
    assert ret.segment_ids == ["s0001", "s0002"]  # the topic index widens the window to what the planner would retrieve
    call = FT.ReviewedCall(call_id="call-1", client_id="cl-1", profile=profile, tasks=tasks, transcript=make_transcript())
    for stage in ("statuses", "values", COMBINED):
        system, user = render_stage_prompt(call, ret, stage)
        assert system["role"] == "system" and "## Slot group: retirement" in system["content"]
        assert "# Transcript window" in user["content"] and "Thanks for coming in" in user["content"] and f"- {RET_AGE}" in user["content"]
    assert "Fields (status and value)" in render_stage_prompt(call, ret, COMBINED)[1]["content"]
    assert "s0002" in render_stage_prompt(call, ret, "values")[1]["content"]  # the reviewed status's segments travel with the values prompt
    with pytest.raises(ValueError, match="unknown extraction stage"):
        render_stage_prompt(call, ret, "objects_and_more")
