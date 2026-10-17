"""The fine-tuning (spec §15.8) and RL (§15.9) activities behind ``FinetuneExtractorWorkflow`` and
``TrainRlExtractorWorkflow``.

The learning code lives in ``evals/finetune.py`` and ``evals/rl_reward.py``; these activities gather its inputs
from Postgres and object storage, call it, and store what it returns under ``training/runs/<label>/``:

- ``export_finetune_data``: every reviewed call (the profile frozen at the review, the run's cached transcript and
  discourse output) → the production stage prompts paired with the reviewed outputs, split by client, held-out gold
  calls excluded; also the §15.8 trigger (300 reviewed calls, or a slot group with 2,000 labelled facts);
- ``sft_train_extractor``: LoRA supervised fine-tuning through the Tinker-compatible training service;
- ``evaluate_extractor_candidate``: the gold experiment with the candidate pinned as the extractor against the
  incumbent, decided per slot group (quality no worse, ECE no worse, lower cost per call-minute; RL additionally
  beats its reference, passes the reward-hacking checks and has a person's sign-off);
- ``promote_extractor_groups``: the routing change for the switched groups (a pull request when GitHub is
  configured, never a merge) and a registry record of the run's versions;
- ``check_rl_start_conditions``, ``export_rl_prompts``, ``grpo_train_extractor``, ``run_rl_hacking_checks``: §15.9.

Every activity is idempotent on ``(label, step, Temporal execution)``: a retry finds the step's recorded outcome, a
reset (a new execution) runs it again. The training service is injected (``training_service``), else built from
``Settings.tinker_api_key``; without one the training steps fail at once with ``TrainingClientUnavailable``.
"""

from __future__ import annotations

import logging
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..config import Settings
from ..schema.slots import Catalog, load_catalog
from ..storage.objects import ObjectStore, safe_segment
from ..storage.store import Store
from ..telemetry import run_context
from ..workflows.contracts import (
    CHECK_RL_START,
    EVALUATE_EXTRACTOR,
    EXPORT_FINETUNE_DATA,
    EXPORT_RL_PROMPTS,
    GRPO_TRAIN_EXTRACTOR,
    PROMOTE_EXTRACTOR,
    RL_HACKING_CHECKS,
    SFT_TRAIN_EXTRACTOR,
    CandidateEvalInput,
    CandidateEvalOutcome,
    FinetuneArgs,
    GroupDecision,
    GrpoInput,
    GrpoOutcome,
    HackingInput,
    HackingOutcome,
    PromotionInput,
    PromotionOutcome,
    RlArgs,
    RlExportInput,
    RlExportOutcome,
    RlStartOutcome,
    SftInput,
    SftOutcome,
    TrainingExportOutcome,
)
from .keys import discourse_key, ingest_key
from .pipeline import _execution, dump, input_error
from .stages import StageFunctions, heartbeat, heartbeating, invoke

log = logging.getLogger(__name__)

RUNS_PREFIX = "training/runs/"
REGISTRY_PREFIX = "training/registry/extractor/"
MODEL_PREFIX = "vllm:extractor-"  # the served id of a run's LoRA weights (the deployment maps it to the vLLM endpoint)
REVIEWED = ("accepted", "edited", "rejected")
CORRECTIONS = ("reject", "reattribute", "edit", "change_status", "remove", "mark_unknown", "add")
TARGETABLE = ("reject", "reattribute", "edit")  # invented values, misattributed speakers, ranges too wide or narrow (§15.9)
PROMOTION_METRICS = ("status_accuracy", "slot_recall", "value_coverage")
COMPOSITE = {"status_accuracy": 0.4, "slot_recall": 0.4, "value_coverage": 0.2}  # the §15.6 weights, for the plateau check
PLATEAU_TOL = 0.005


def training_unavailable(message: str) -> ApplicationError:
    """No training service, base model or checkpoint: another attempt cannot fix it."""
    return ApplicationError(message, type="TrainingClientUnavailable", non_retryable=True)


def tinker_service_client(settings: Settings) -> Any:
    """A Tinker service client from the settings (managed Tinker, or a Tinker-compatible backend by base URL)."""
    if not settings.tinker_api_key:
        raise training_unavailable(
            "no Tinker-compatible training service is configured: set ADVISOR_INTEL_TINKER_API_KEY "
            "(and ADVISOR_INTEL_TINKER_BASE_URL for a self-hosted backend), or inject a training service into the worker"
        )
    try:
        import tinker  # type: ignore[import-not-found]
    except ImportError as e:
        raise training_unavailable(f"the tinker package is not installed in this worker ({e})") from e
    kwargs: dict[str, Any] = {"api_key": settings.tinker_api_key}
    if settings.tinker_base_url:
        kwargs["base_url"] = settings.tinker_base_url
    return tinker.ServiceClient(**kwargs)


def live_group_routing(settings: Settings) -> dict[str, str]:
    """Slot group → the fine-tuned model it is served by: the settings, else the checkout's routing file."""
    if settings.extraction_groups:
        return dict(settings.extraction_groups)
    from ..evals.model_swap import ROUTING_FILE

    if ROUTING_FILE.exists():
        data = yaml.safe_load(ROUTING_FILE.read_text()) or {}
        return {str(k): str(v) for k, v in (data.get("extraction_groups") or {}).items()}
    return {}


def gold_experiment(*, settings: Settings, heldout_only: bool = False) -> Callable[[Any], Any]:
    """The gold experiment as ``run_eval(pins) -> EvalReport`` (every gold call, or the held-out ones only)."""
    from ..evals.datasets import GOLD_CALLS, get_store
    from ..evals.extraction_eval import eval_cases, run_local

    cases = eval_cases(GOLD_CALLS, store=get_store(settings=settings), only_heldout=heldout_only)
    if not cases:
        raise input_error("the gold-calls dataset is empty: the promotion experiment needs the gold calls")

    def run(pins: Any) -> Any:
        return run_local(cases, pins=pins, dataset=GOLD_CALLS, experiment="extractor-candidate", save=False)

    run.n_cases = len(cases)  # type: ignore[attr-defined]
    return run


def composite_score(metrics: dict[str, list[float | None]]) -> float | None:
    """0.4·status accuracy + 0.4·slot recall + 0.2·value coverage of the candidate side of a decision's metrics."""
    vals = {m: (metrics.get(m) or [None, None])[1] for m in COMPOSITE}
    if any(v is None for v in vals.values()):
        return None
    return sum(COMPOSITE[m] * float(vals[m]) for m in COMPOSITE)  # type: ignore[arg-type]


class TrainingActivities:
    def __init__(
        self,
        *,
        store: Store,
        objects: ObjectStore,
        settings: Settings,
        stages: StageFunctions | None = None,
        training_service: Any = None,
        pull_requests: Callable[..., str | None] | None = None,
    ) -> None:
        self.store = store
        self.objects = objects
        self.settings = settings
        self.stages = stages or StageFunctions()
        self.training_service = training_service
        self.pull_requests = pull_requests

    def activities(self) -> list[Any]:
        return [
            self.export_finetune_data,
            self.sft_train_extractor,
            self.evaluate_extractor_candidate,
            self.promote_extractor_groups,
            self.check_rl_start_conditions,
            self.export_rl_prompts,
            self.grpo_train_extractor,
            self.run_rl_hacking_checks,
        ]

    # ------------------------------------------------------------------ shared

    def _service(self) -> Any:
        svc = self.training_service
        if svc is None:
            return tinker_service_client(self.settings)
        return svc() if callable(svc) and not hasattr(svc, "create_lora_training_client") else svc

    def _base_model(self, requested: str) -> str:
        base = requested or self.settings.finetune_base_model
        if not base:
            raise training_unavailable("no base model is configured for fine-tuning (ADVISOR_INTEL_FINETUNE_BASE_MODEL or args.base_model)")
        return base

    @staticmethod
    def _key(label: str, name: str) -> str:
        return f"{RUNS_PREFIX}{safe_segment(label)}/{name}"

    async def _cached(self, label: str, step: str, model_type: type[Any]) -> Any | None:
        key = self._key(label, f"{step}.json")
        if not await self.objects.exists(key):
            return None
        data = await self.objects.get_json(key)
        if data.get("execution", "") != _execution():
            return None  # a new execution (a reset) runs the step again
        return model_type.model_validate(data["outcome"]).model_copy(update={"cached": True})

    async def _record(self, label: str, step: str, outcome: Any) -> None:
        await self.objects.put_json(self._key(label, f"{step}.json"), {"execution": _execution(), "outcome": dump(outcome)})

    def _dataset_rows(self) -> list[Any]:
        try:
            from ..evals.datasets import GOLD_CALLS

            return list(self.stages.get("dataset_store")(settings=self.settings).fetch(GOLD_CALLS))
        except Exception as e:  # noqa: BLE001 - an unreachable dataset must not hide a reviewed call; the flags still apply
            log.warning("could not read the gold dataset: %s: %s", type(e).__name__, e)
            return []

    def _gold_ids(self, rows: Iterable[Any]) -> tuple[set[str], set[str]]:
        """(every gold call id, the held-out ones) from the gold dataset rows."""
        gold, held = set(), set()
        for r in rows:
            cid = str((r.input or {}).get("call_id") or r.id.split(":")[0])
            gold.add(cid)
            if r.metadata.get("split") == "heldout":
                held.add(cid)
        return gold, held

    async def _submits(self) -> dict[str, Any]:
        """The first review submission of every reviewed run (§13.1: the first submission is the review)."""
        submits: dict[str, Any] = {}
        for e in await self.store.review_events():
            if e.action == "submit":
                submits.setdefault(e.workflow_id, e)
        return submits

    async def _reviewed_calls(self, *, groups: Sequence[str], extra_heldout: Iterable[str]) -> tuple[list[Any], set[str]]:
        """Every reviewed call as a ``ReviewedCall`` (tasks rebuilt from the reviewed profile), and the held-out ids."""
        from ..evals.finetune import ReviewedCall

        gold, held = self._gold_ids(self._dataset_rows())
        held |= set(extra_heldout)
        tasks_of = self.stages.get("training_tasks")
        calls: list[Any] = []
        for run_id, submit in sorted((await self._submits()).items()):
            run = await self.store.get_run(run_id)
            if run is None:
                continue
            call = await self.store.get_call(run.call_id)
            profile = await self.store.load_profile(run_id, submit.profile_version) if submit.profile_version else None
            profile = profile or await self.store.load_profile(run_id)
            transcript = await self.store.load_transcript(run.call_id, ingest_key(run.versions)) or await self.store.load_transcript(
                run.call_id
            )
            if profile is None or transcript is None:
                log.warning("reviewed run %s has no profile or transcript: left out of training", run_id)
                continue
            discourse = await self.store.load_discourse(run.call_id, discourse_key(run.versions)) or await self.store.load_discourse(
                run.call_id
            )
            heartbeat(run_id)
            catalog = load_catalog(profile.schema_version)
            calls.append(
                ReviewedCall(
                    call_id=run.call_id,
                    client_id=(call.primary_client_id if call else None) or run.call_id,
                    profile=profile,
                    tasks=tasks_of(profile, discourse, catalog=catalog, groups=list(groups) or None),
                    transcript=transcript,
                    discourse=discourse,
                    is_gold=run.call_id in gold,
                    heldout=run.call_id in held,
                    prompt_versions=dict(run.versions.prompt_versions),
                )
            )
        return calls, held

    @staticmethod
    def _labelled_facts(calls: Sequence[Any], catalog: Catalog) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for c in calls:
            if c.heldout:
                continue
            for f in c.profile.facts.values():
                if f.source == "call" and (f.review.status in REVIEWED or f.auto_accepted):
                    counts[catalog.slot(f.slot_id).group] += 1
        return dict(counts)

    # ------------------------------------------------------------------ §15.8 export

    @activity.defn(name=EXPORT_FINETUNE_DATA)
    async def export_finetune_data(self, args: FinetuneArgs) -> TrainingExportOutcome:
        with run_context(stage="finetune"):
            label = args.label or (activity.info().workflow_id if _execution() else "finetune")
            cached = await self._cached(label, "export", TrainingExportOutcome)
            if cached is not None:
                return cached
            from ..evals.finetune import export_training_data, write_jsonl

            calls, held = await self._reviewed_calls(groups=args.groups, extra_heldout=args.heldout_ids)
            catalog = load_catalog()
            usable = [c for c in calls if not c.heldout]
            facts = self._labelled_facts(calls, catalog)
            out = TrainingExportOutcome(
                label=label,
                n_reviewed_calls=len(usable),
                n_clients=len({c.client_id for c in usable}),
                labelled_facts_by_group=facts,
                heldout_ids=sorted(held),
            )
            groups = list(args.groups)
            if len(usable) >= args.min_reviewed_calls:
                eligible, reason = True, f"{len(usable)} reviewed calls"
            else:
                early = sorted(g for g, n in facts.items() if n >= args.min_group_facts and (not groups or g in groups))
                if early:
                    eligible, reason, groups = True, f"slot groups with ≥ {args.min_group_facts} labelled facts: {', '.join(early)}", early
                else:
                    eligible = False
                    reason = f"{len(usable)} reviewed calls < {args.min_reviewed_calls}, and no slot group has {args.min_group_facts} labelled facts"
            if not eligible and args.force:
                eligible, reason = True, f"forced by the operator ({reason})"
            if not eligible:
                out = out.model_copy(update={"eligible": False, "reason": reason, "groups": groups})
                await self._record(label, "export", out)
                return out
            render = self.stages.get("render_stage_prompt")
            async with heartbeating("export"):
                examples, st = await invoke(export_training_data, calls, render_prompt=render, heldout_ids=held, catalog=catalog)
            if groups:
                examples = [e for e in examples if e.group in groups]
            leaked = {e.call_id for e in examples} & held
            if leaked:
                raise input_error(f"held-out calls reached the training export: {sorted(leaked)}")
            if not examples:
                out = out.model_copy(update={"eligible": False, "reason": "no training examples from the reviewed calls", "groups": groups})
                await self._record(label, "export", out)
                return out
            prompt_versions: dict[str, str] = {}
            for c in calls:
                prompt_versions.update(c.prompt_versions)
            with tempfile.TemporaryDirectory() as tmp:
                for split, path in write_jsonl(examples, Path(tmp)).items():
                    await self.objects.put_bytes(
                        self._key(label, f"data/{split}.jsonl"), path.read_bytes(), content_type="application/x-ndjson"
                    )
            rows = "".join(e.model_dump_json() + "\n" for e in examples).encode()
            data_uri = await self.objects.put_bytes(self._key(label, "data/examples.jsonl"), rows, content_type="application/x-ndjson")
            out = out.model_copy(
                update={
                    "eligible": True,
                    "reason": reason,
                    "groups": groups or sorted({e.group for e in examples}),
                    "examples": len(examples),
                    "by_split": {s: sum(e.split == s for e in examples) for s in ("train", "val", "test")},
                    "by_group": dict(Counter(e.group for e in examples)),
                    "skipped_heldout_calls": st.skipped_heldout_calls,
                    "skipped_disputed_tasks": st.skipped_disputed_tasks,
                    "skipped_unreviewed_tasks": st.skipped_unreviewed_tasks,
                    "gold_examples": sum(e.weight > 1.0 for e in examples),
                    "prompt_versions": prompt_versions,
                    "data_uri": data_uri,
                }
            )
            await self._record(label, "export", out)
            return out

    # ------------------------------------------------------------------ §15.8 SFT

    @activity.defn(name=SFT_TRAIN_EXTRACTOR)
    async def sft_train_extractor(self, inp: SftInput) -> SftOutcome:
        with run_context(stage="finetune"):
            label = inp.export.label
            cached = await self._cached(label, "sft", SftOutcome)
            if cached is not None:
                return cached
            if not inp.export.data_uri:
                raise input_error(f"training run {label} has no exported data")
            from ..evals.finetune import TrainingExample

            base = self._base_model(inp.args.base_model)
            service = self._service()
            lines = (await self.objects.get_bytes(inp.export.data_uri)).decode().splitlines()
            examples = [TrainingExample.model_validate_json(line) for line in lines if line.strip()]
            leaked = {e.call_id for e in examples} & set(inp.export.heldout_ids)
            if leaked:
                raise input_error(f"held-out calls in the training data of {label}: {sorted(leaked)}")
            client = service.create_lora_training_client(base_model=base, rank=self.settings.finetune_lora_rank)
            name = f"sft-{safe_segment(label)}"
            async with heartbeating("sft"):
                metrics = await invoke(self.stages.get("sft_train"), examples, client, epochs=inp.args.epochs, name=name)
            metrics_uri = await self.objects.put_json(self._key(label, "sft_metrics.json"), [dump(m) for m in metrics])
            out = SftOutcome(
                label=label,
                base_model=base,
                checkpoint=next((m.checkpoint for m in reversed(metrics) if m.checkpoint), f"{name}-final"),
                model=f"{MODEL_PREFIX}{safe_segment(label)}",
                steps=len(metrics),
                examples=sum(e.split == "train" for e in examples),
                final_loss=metrics[-1].loss if metrics else None,
                metrics_uri=metrics_uri,
            )
            await self._record(label, "sft", out)
            return out

    # ------------------------------------------------------------------ the gold experiment and the gate

    @activity.defn(name=EVALUATE_EXTRACTOR)
    async def evaluate_extractor_candidate(self, inp: CandidateEvalInput) -> CandidateEvalOutcome:
        with run_context(stage="finetune"):
            cached = await self._cached(inp.label, "eval", CandidateEvalOutcome)
            if cached is not None:
                return cached
            from ..evals.finetune import promotion_check

            pins = self.stages.get("current_version_pins")()
            incumbent_model = inp.incumbent_model or pins.reasoning_model
            run_eval = self.stages.get("gold_experiment")(settings=self.settings, heldout_only=inp.heldout_only)
            async with heartbeating("gold-experiment"):
                incumbent = await invoke(run_eval, pins.model_copy(update={"reasoning_model": incumbent_model}))
                candidate = await invoke(run_eval, pins.model_copy(update={"reasoning_model": inp.candidate_model}))
            groups = list(inp.groups) or sorted(set(candidate.summary.get("by_group") or {}) & set(incumbent.summary.get("by_group") or {}))
            cost_i, cost_c = incumbent.metric("cost_per_call_minute"), candidate.metric("cost_per_call_minute")
            decisions = []
            for p in promotion_check(candidate, incumbent, groups):
                reasons = [] if p.switch else list(p.reasons)
                if cost_i is None or cost_c is None:
                    reasons.append("cost per call-minute not measured for both models")
                elif cost_c >= cost_i:
                    reasons.append(f"cost per call-minute {cost_c:.4f} is not lower than {cost_i:.4f}")
                if inp.require_better and not any(
                    (c is not None and i is not None and c > i + 1e-9) for m, (i, c) in p.metrics.items() if m in PROMOTION_METRICS
                ):
                    reasons.append("does not beat the incumbent on any of status accuracy, slot recall or value coverage")
                if inp.hacking_passed is False:
                    reasons.append("the reward-hacking checks failed")
                if inp.human_signed_off is False or (inp.hacking_passed is not None and inp.human_signed_off is None):
                    reasons.append("the sampled outputs have not been signed off by a person")
                decisions.append(
                    GroupDecision(
                        group=p.group,
                        switch=not reasons,
                        reasons=reasons or ["matches or beats the incumbent with ECE no worse, at lower cost per call-minute"],
                        metrics={m: [i, c] for m, (i, c) in p.metrics.items()},
                    )
                )
            cand_uri = await self.objects.put_json(self._key(inp.label, "reports/candidate.json"), dump(candidate))
            inc_uri = await self.objects.put_json(self._key(inp.label, "reports/incumbent.json"), dump(incumbent))
            out = CandidateEvalOutcome(
                label=inp.label,
                candidate_model=inp.candidate_model,
                incumbent_model=incumbent_model,
                groups=groups,
                decisions=decisions,
                switched=[d.group for d in decisions if d.switch],
                cost_incumbent=cost_i,
                cost_candidate=cost_c,
                gold_calls=int(getattr(run_eval, "n_cases", 0) or candidate.n_items),
                candidate_report_uri=cand_uri,
                incumbent_report_uri=inc_uri,
            )
            await self._record(inp.label, "eval", out)
            return out

    @activity.defn(name=PROMOTE_EXTRACTOR)
    async def promote_extractor_groups(self, inp: PromotionInput) -> PromotionOutcome:
        with run_context(stage="finetune"):
            cached = await self._cached(inp.label, "promotion", PromotionOutcome)
            if cached is not None:
                return cached
            ev = inp.evaluation
            now = datetime.now(UTC)
            record = {
                "label": inp.label,
                "kind": inp.kind,
                "model": ev.candidate_model,
                "incumbent_model": ev.incumbent_model,
                "checkpoint": inp.checkpoint,
                "base_model": inp.base_model,
                "groups": ev.groups,
                "switched": ev.switched,
                "decisions": [d.model_dump(mode="json") for d in ev.decisions],
                "cost_per_call_minute": {"incumbent": ev.cost_incumbent, "candidate": ev.cost_candidate},
                "prompt_versions": inp.prompt_versions,
                "gold_calls": ev.gold_calls,
                "reports": {"candidate": ev.candidate_report_uri, "incumbent": ev.incumbent_report_uri},
                "created_at": now.isoformat(),
                "pull_request": None,
                "routing_uri": None,
            }
            out = PromotionOutcome(
                label=inp.label, model=ev.candidate_model, switched=list(ev.switched), calibrator_retrain_needed=list(ev.switched)
            )
            if ev.switched:
                text = group_routing_text(ev, inp.label, now)
                out.routing_uri = await self.objects.put_bytes(
                    self._key(inp.label, "routing.yaml"), text.encode(), content_type="text/yaml"
                )
                opener = self.pull_requests if self.pull_requests is not None else default_pull_requests()
                if opener is not None:
                    out.pull_request = await invoke(opener, ev, text)
                record.update(pull_request=out.pull_request, routing_uri=out.routing_uri)
            out.registry_uri = await self.objects.put_json(f"{REGISTRY_PREFIX}{now:%Y%m%dT%H%M%S}-{safe_segment(inp.label)}.json", record)
            await self._record(inp.label, "promotion", out)
            return out

    # ------------------------------------------------------------------ §15.9 RL

    async def _registry(self) -> list[dict[str, Any]]:
        rows = []
        for key in sorted(await self.objects.list(REGISTRY_PREFIX)):
            rows.append(await self.objects.get_json(key))
        return rows

    @activity.defn(name=CHECK_RL_START)
    async def check_rl_start_conditions(self, args: RlArgs) -> RlStartOutcome:
        with run_context(stage="rl"):
            label = args.label or (activity.info().workflow_id if _execution() else "rl")
            live = live_group_routing(self.settings)
            groups = list(args.groups) or sorted(live)
            reasons: list[str] = []
            checks: dict[str, bool] = {}
            # 1. The fine-tuned extractor is live for at least one slot group, and its gold metrics have plateaued.
            live_models = Counter(live[g] for g in groups if g in live)
            reference = args.reference_model or (live_models.most_common(1)[0][0] if live_models else None)
            checks["fine_tuned_extractor_live"] = bool(live) and bool(groups) and all(g in live for g in groups)
            if not checks["fine_tuned_extractor_live"]:
                missing = [g for g in groups if g not in live]
                reasons.append(
                    "the fine-tuned extractor is not live for "
                    + (", ".join(missing) if missing else "any slot group")
                    + " (ADVISOR_INTEL_EXTRACTION_GROUPS or routing.yaml extraction_groups)"
                )
            registry = await self._registry()
            ref_row = next((r for r in reversed(registry) if r.get("model") == reference), None) if reference else None
            plateau = False
            if ref_row is None:
                reasons.append(f"no training record for the live model {reference!r}: its retraining history is unknown")
            else:
                later = [r for r in registry[registry.index(ref_row) + 1 :] if r.get("kind") == "sft"]
                per_group = []
                for g in groups:
                    base = _group_score(ref_row, g)
                    rounds = [_group_score(r, g) for r in later if _group_score(r, g) is not None]
                    improved = [s for s in rounds[-args.plateau_rounds :] if base is not None and s is not None and s > base + PLATEAU_TOL]
                    per_group.append(len(rounds) >= args.plateau_rounds and not improved)
                plateau = bool(per_group) and all(per_group)
                if not plateau:
                    reasons.append(
                        f"the fine-tuned extractor's gold metrics have not plateaued over {args.plateau_rounds} retraining rounds"
                    )
            checks["metrics_plateaued"] = plateau
            # 2. The remaining errors are ones the reward can target.
            events = await self.store.review_events()
            corrections = [e for e in events if e.action in CORRECTIONS]
            share = (sum(e.action in TARGETABLE for e in corrections) / len(corrections)) if corrections else None
            checks["errors_targetable"] = share is not None and share >= args.min_targetable_share
            if not checks["errors_targetable"]:
                reasons.append(
                    "no reviewer corrections yet"
                    if share is None
                    else f"only {share:.0%} of reviewer corrections are invented values, misattributions or value edits (< {args.min_targetable_share:.0%})"
                )
            # 3. At least 300 reviewed calls.
            n_reviewed = len({e.workflow_id for e in events if e.action == "submit"})
            checks["reviewed_calls"] = n_reviewed >= args.min_reviewed_calls
            if not checks["reviewed_calls"]:
                reasons.append(f"{n_reviewed} reviewed calls < {args.min_reviewed_calls}")
            can_start = all(checks.values())
            forced = bool(args.force and not can_start)
            out = RlStartOutcome(
                label=label,
                can_start=can_start or forced,
                forced=forced,
                checks=checks,
                reasons=reasons,
                live_groups=groups,
                reference_model=reference,
                reference_checkpoint=args.reference_checkpoint or (str(ref_row.get("checkpoint") or "") if ref_row else None) or None,
                n_reviewed_calls=n_reviewed,
                targetable_share=share,
            )
            await self._record(label, "rl_start", out)
            return out

    @activity.defn(name=EXPORT_RL_PROMPTS)
    async def export_rl_prompts(self, inp: RlExportInput) -> RlExportOutcome:
        with run_context(stage="rl"):
            label = inp.start.label
            cached = await self._cached(label, "rl_export", RlExportOutcome)
            if cached is not None:
                return cached
            from ..evals._runtime import stable_hash
            from ..evals.finetune import RLPrompt
            from ..extract.training_prompt import COMBINED

            groups = list(inp.start.live_groups)
            calls, held = await self._reviewed_calls(groups=groups, extra_heldout=inp.args.heldout_ids)
            render, keys_of, reviewed_facts = (
                self.stages.get("render_stage_prompt"),
                self.stages.get("training_task_keys"),
                self.stages.get("reviewed_facts"),
            )
            prompts: list[RLPrompt] = []
            skipped = 0
            for c in calls:
                if c.heldout or c.call_id in held:
                    skipped += 1
                    continue
                heartbeat(c.call_id)
                catalog = load_catalog(c.profile.schema_version)
                truth = reviewed_facts(c.profile, catalog=catalog)
                index = c.transcript.index() if c.transcript is not None else {}
                for task in c.tasks:
                    if groups and task.slot_group not in groups:
                        continue
                    keys = await invoke(keys_of, c, task)
                    slots = [(slot.slot_id, inst) for slot, inst in keys.values()]
                    wanted = set(slots)
                    prompts.append(
                        RLPrompt(
                            prompt_id=stable_hash([c.call_id, task.task_id]),
                            call_id=c.call_id,
                            client_id=c.client_id,
                            group=task.slot_group,
                            messages=await invoke(render, c, task, COMBINED),
                            reviewed=[g for g in truth if g.key in wanted],
                            segments={sid: index[sid].text for sid in task.segment_ids if sid in index},
                            slots=slots,
                        )
                    )
            leaked = {p.call_id for p in prompts} & held
            if leaked:
                raise input_error(f"held-out calls reached the RL prompts: {sorted(leaked)}")
            uri = None
            if prompts:
                rows = "".join(p.model_dump_json() + "\n" for p in prompts).encode()
                uri = await self.objects.put_bytes(self._key(label, "rl_prompts.jsonl"), rows, content_type="application/x-ndjson")
            out = RlExportOutcome(
                label=label,
                n_prompts=len(prompts),
                by_group=dict(Counter(p.group for p in prompts)),
                n_reviewed_calls=len(calls) - skipped,
                skipped_heldout_calls=skipped,
                heldout_ids=sorted(held),
                prompts_uri=uri,
            )
            await self._record(label, "rl_export", out)
            return out

    async def _prompts(self, export: RlExportOutcome) -> list[Any]:
        from ..evals.finetune import RLPrompt

        if not export.prompts_uri:
            raise input_error(f"RL run {export.label} has no prompts")
        lines = (await self.objects.get_bytes(export.prompts_uri)).decode().splitlines()
        return [RLPrompt.model_validate_json(line) for line in lines if line.strip()]

    @activity.defn(name=GRPO_TRAIN_EXTRACTOR)
    async def grpo_train_extractor(self, inp: GrpoInput) -> GrpoOutcome:
        with run_context(stage="rl"):
            label = inp.export.label
            cached = await self._cached(label, "grpo", GrpoOutcome)
            if cached is not None:
                return cached
            from ..evals.finetune import GRPOConfig

            base = self._base_model(inp.args.base_model)
            ref_ckpt = inp.start.reference_checkpoint
            if not ref_ckpt:
                raise training_unavailable("RL starts from the fine-tuned extractor's checkpoint (§15.9): none is recorded or given")
            service = self._service()
            prompts = await self._prompts(inp.export)
            held = set(inp.export.heldout_ids)
            leaked = {p.call_id for p in prompts} & held
            if leaked:
                raise input_error(f"held-out calls in the RL prompts of {label}: {sorted(leaked)}")
            training_client = service.create_lora_training_client(base_model=base, rank=self.settings.finetune_lora_rank)
            load_state = getattr(training_client, "load_state", None)
            if load_state is not None:
                result = load_state(ref_ckpt)
                if hasattr(result, "result"):
                    result.result()
            reference_client = service.create_sampling_client(model_path=ref_ckpt)
            cfg = GRPOConfig(**{**inp.args.config, "name": f"grpo-{safe_segment(label)}"})
            steps: list[Any] = []
            async with heartbeating("grpo"):
                res = await invoke(
                    self.stages.get("grpo_train"),
                    prompts,
                    training_client=training_client,
                    reference_client=reference_client,
                    postprocess=self.stages.get("rl_postprocess"),
                    config=cfg,
                    heldout_ids=held,
                    on_step=steps.append,
                )
            metrics_uri = await self.objects.put_json(self._key(label, "grpo_metrics.json"), [dump(m) for m in res.steps])
            rewards = [m.mean_reward for m in res.steps if m.mean_reward is not None]
            out = GrpoOutcome(
                label=label,
                base_model=base,
                reference_checkpoint=ref_ckpt,
                checkpoint=res.checkpoint,
                model=f"{MODEL_PREFIX}{safe_segment(label)}",
                steps=len(res.steps),
                first_mean_reward=rewards[0] if rewards else None,
                last_mean_reward=rewards[-1] if rewards else None,
                metrics_uri=metrics_uri,
            )
            await self._record(label, "grpo", out)
            return out

    @activity.defn(name=RL_HACKING_CHECKS)
    async def run_rl_hacking_checks(self, inp: HackingInput) -> HackingOutcome:
        with run_context(stage="rl"):
            label = inp.training.label
            cached = await self._cached(label, "hacking", HackingOutcome)
            if cached is not None:
                return cached
            from ..evals.finetune import GRPOConfig, render_chat, tinker_types
            from ..evals.rl_reward import RolloutOutput

            service = self._service()
            prompts = await self._prompts(inp.export)
            postprocess = self.stages.get("rl_postprocess")
            cfg = GRPOConfig(**{k: v for k, v in inp.args.config.items() if k in GRPOConfig.model_fields})
            candidate_client = service.create_sampling_client(model_path=inp.training.checkpoint)
            reference_client = service.create_sampling_client(model_path=inp.training.reference_checkpoint)
            tok = getattr(candidate_client, "get_tokenizer", None)
            tokenizer = tok() if tok else service.create_lora_training_client(base_model=inp.training.base_model).get_tokenizer()
            types = tinker_types()

            def rollouts(client: Any) -> list[RolloutOutput]:
                outs = []
                for p in prompts:
                    toks = tokenizer.encode(render_chat(p.messages))
                    res = client.sample(
                        prompt=types.ModelInput.from_ints(toks),
                        num_samples=1,
                        sampling_params=types.SamplingParams(max_tokens=cfg.max_tokens, temperature=cfg.temperature),
                    ).result()
                    seq = next(iter(res.sequences if hasattr(res, "sequences") else res["sequences"]))
                    text = tokenizer.decode(seq.tokens)
                    outs.append(
                        RolloutOutput(prompt_id=p.prompt_id, call_id=p.call_id, group=p.group, facts=postprocess(text, p), text=text)
                    )
                return outs

            with tempfile.TemporaryDirectory() as tmp:
                sheet = Path(tmp) / "review_sheet.jsonl"
                async with heartbeating("hacking-checks"):
                    candidate = await invoke(rollouts, candidate_client)
                    reference = await invoke(rollouts, reference_client)
                    report = await invoke(
                        self.stages.get("hacking_checks"),
                        candidate,
                        reference,
                        {p.prompt_id: p.reviewed for p in prompts},
                        review_path=sheet,
                        n_review=inp.args.n_review_samples,
                    )
                sheet_uri = (
                    await self.objects.put_bytes(
                        self._key(label, "review_sheet.jsonl"), sheet.read_bytes(), content_type="application/x-ndjson"
                    )
                    if sheet.exists()
                    else None
                )
            await self.objects.put_json(self._key(label, "hacking_report.json"), dump(report))
            out = HackingOutcome(
                label=label,
                passed=bool(report.passed),
                failures=[f"{r.key}: {r.note or 'failed'}" for r in report.failures()],
                not_discussed={r.key: [r.candidate, r.reference] for r in report.not_discussed},
                flagged_samples=int(report.flagged_samples),
                review_sheet_uri=sheet_uri,
                human_signed_off=inp.args.human_signed_off,
            )
            await self._record(label, "hacking", out)
            return out


# --------------------------------------------------------------------------- routing change and pull request


def _group_score(row: dict[str, Any], group: str) -> float | None:
    for d in row.get("decisions") or []:
        if d.get("group") == group:
            return composite_score(d.get("metrics") or {})
    return None


def group_routing_text(ev: CandidateEvalOutcome, label: str, at: datetime, current: str | None = None) -> str:
    """The routing file with the switched slot groups routed to the candidate (``extraction_groups``), with history.

    ``current`` is the routing file as it stands (the checkout's ``routing.yaml`` by default); the roles section and
    earlier history are kept."""
    from ..evals.finetune import GroupPromotion, write_group_routing
    from ..evals.model_swap import ROUTING_FILE

    if current is None and ROUTING_FILE.exists():
        current = ROUTING_FILE.read_text()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "routing.yaml"
        if current:
            path.write_text(current)
        promotions = [
            GroupPromotion(group=d.group, switch=d.switch, reasons=d.reasons, metrics={m: (v[0], v[1]) for m, v in d.metrics.items()})
            for d in ev.decisions
        ]
        write_group_routing(promotions, ev.candidate_model, path)
        data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("history", []).append(
        {
            "extraction_groups": list(ev.switched),
            "from": ev.incumbent_model,
            "to": ev.candidate_model,
            "at": at.isoformat(),
            "training_run": label,
            "reason": "per-group gold experiment (§15.8): quality no worse, ECE no worse, lower cost per call-minute",
        }
    )
    return (
        "# Model routing (spec §13.8): roles from the model-swap protocol (§15.4), extraction_groups from the fine-tuning\n"
        "# and RL tracks (§15.8, §15.9). Changed only through pull requests; the calibrator of a switched group is retrained.\n"
        + yaml.safe_dump(data, sort_keys=False)
    )


def default_pull_requests() -> Callable[[CandidateEvalOutcome, str], str | None] | None:
    """The GitHub pull-request opener of the model-swap protocol, when a repository and token are configured."""
    from ..evals.model_swap import GitHubPullRequests

    gh = GitHubPullRequests()
    if not gh.configured:
        return None

    def open_pr(ev: CandidateEvalOutcome, routing: str) -> str | None:
        return gh(swap_decision(ev), routing)

    return open_pr


def swap_decision(ev: CandidateEvalOutcome) -> Any:
    """The per-group decisions as the model-swap protocol's decision record, for its pull-request body."""
    from ..evals.model_swap import SwapDecision, SwapRow

    rows = []
    for d in ev.decisions:
        for m, (inc, cand) in d.metrics.items():
            better = inc is not None and cand is not None and ((cand < inc) if m == "ece" else (cand > inc))
            worse = inc is not None and cand is not None and ((cand > inc) if m == "ece" else (cand < inc))
            rows.append(SwapRow(metric=f"{d.group}.{m}", current=inc, candidate=cand, better=better, worse=worse))
    return SwapDecision(
        role="extraction",
        current_model=ev.incumbent_model,
        candidate_model=ev.candidate_model,
        wins=bool(ev.switched),
        reason=f"slot groups switched: {', '.join(ev.switched)} (quality no worse, ECE no worse, lower cost per call-minute)",
        rows=rows,
        cost_current=ev.cost_incumbent,
        cost_candidate=ev.cost_candidate,
        decided_at=datetime.now(UTC).isoformat(),
    )


__all__ = [
    "TrainingActivities",
    "composite_score",
    "default_pull_requests",
    "gold_experiment",
    "group_routing_text",
    "live_group_routing",
    "tinker_service_client",
    "training_unavailable",
]
