"""Entry points into the stage code that activities call, resolved lazily (spec §13.1, §14.1).

Activities do the I/O around each stage (load inputs from storage, heartbeat, save outputs); the
stages themselves live in their own packages and are imported only when an activity runs. This
table is the single place where those names are bound, and tests (or a deployment) can override
any entry with a callable.

Expected signatures:

====================== =======================================================================
name                   callable
====================== =======================================================================
ingest_call            ``async (audio: Path, meta: CallMeta, *, asr, diarizer, role_agent_pins, second_pass=None,
                       prior_fna=None, crm_names: CrmNames | None = None) -> Transcript`` (§5)
ingest_script          ``async (script: ScriptedCall, anchor_date, *, call_id, roles, role_agent_pins, prior_fna,
                       crm_names) -> Transcript``: a scripted call through the stage 1 back half (no audio)
run_discourse          ``async (transcript, *, prior: Profile | None, crm: CrmRecord | None, pins, catalog)
                       -> DiscourseOutput`` (§6)
agent_inputs           the harness's ``AgentInputs`` (call_id, run_id, transcript, discourse, profile, pins, budgets,
                       quality, crm, glossary, base_document, call_minutes)
agent_services         ``(*, glossary_store) -> HarnessServices``: every installed stage behind the agent's tools
run_agent              ``async (inputs, *, services, checkpoints: CheckpointStore, heartbeat) -> AgentResult``
                       with ``.outcome: AgentOutcome``, ``.profile: Profile``, ``.followups`` (§13.2)
verify_after_agent     ``async (profile, *, transcript, discourse, pins, catalog, run_id, calibrator, conformal, settings)
                       -> Stage5Result`` with ``.profile``: the checks after the agent, then scoring (§9)
conformal_thresholds   the conformal thresholds model (``model_validate`` of stored thresholds, §9.5)
run_voi_and_followups  ``async (profile, *, catalog, pins, pos_runs, analyse, transcript, settings, base_document,
                       seed) -> VoiOutput`` with ``.profile``, ``.followups``, ``.flags`` (§10)
sensitivity_runs       ``(profile, fact_ids, *, n_paths, seed, base_document) -> {fact_id: row}``: the forecast
                       engine's reduced runs, VOI's ``pos_runs`` (§10.2)
fna_analyse            the FNA engine's ``analyse(doc, *, today)`` (§12.7)
run_forecast           ``(profile, *, base_document=None, mode="preview"|"final", seed=None) -> ForecastResult``
extract_followup       ``async (profile, transcript, *, followup: FollowUp, pins, catalog) -> list[StateOp]``:
                       the extraction over a one-segment answer transcript (§10.6)
build_labels           ``(before: Profile, ops: list[StateOp], *, after: Profile, spot_check: bool) -> LabelBatch``
                       with ``.labels`` and ``.recall_misses``: §9.3 labels from one review
calibration_label      the label model (``model_validate`` of a stored label row)
retrain_calibrator     ``(labels, *, incumbent: Calibrator | None, pins) -> RetrainReport`` with ``.candidate``
                       (``.version``, ``.metrics``, ``.to_bytes()``), ``.promoted``, ``.reason`` (§9.4)
calibrator_type        the calibrator class (``from_bytes`` of a stored artifact)
refit_conformal        ``(labels, *, alpha, min_calls, calibrator_version, settings) -> ConformalThresholds`` with
                       ``.tau_by_family``, ``.tau_pooled``, ``.n_calls``, ``.enabled``, ``.reason`` (§9.5)
load_lexicon           ``(version) -> Lexicon``
lexicon_edit_pair      the lexicon's ``EditPair`` (``from_fact(fact, edited)``: the pairs one reviewed fact gives)
propose_lexicon_fit    ``(pairs: list[EditPair], lex) -> LexiconDiff`` with ``.changes`` (section, key),
                       ``.new_version``, ``.yaml_text``, ``.diff_text`` (§11.4)
sync_error_dataset     ``(rows: list[dict], *, settings) -> int`` rows inserted into ``production-errors``
sync_gold_candidates   ``(rows: list[dict], *, settings) -> int`` rows inserted (§15.6)
run_model_swap         ``(candidates: dict[str, str], *, settings) -> dict`` with ``results``, ``pull_requests``
training_tasks         ``(profile, discourse, *, catalog, groups) -> list[ExtractionTask]``: a reviewed run's tasks (§15.8)
training_task_keys     ``(call: ReviewedCall, task) -> {field key: (SlotDef, instance)}``: the keys a task's prompts list
render_stage_prompt    ``(call: ReviewedCall, task, stage) -> list[{role, content}]``: the production stage prompt (§15.8)
export_training_data   ``evals.finetune.export_training_data``; ``sft_train``, ``grpo_train``, ``promotion_check`` and
                       ``write_group_routing`` likewise (§15.8, §15.9)
reviewed_facts         ``(profile, *, catalog) -> list[GoldFact]``: the reviewed truth the §15.9 reward scores against
rl_postprocess         ``(text, prompt: RLPrompt) -> list[RolloutFact]``: a sampled output through the production
                       post-processing (schema conversion, lexicon normalisation)
hacking_checks         ``evals.rl_reward.hacking_checks`` (§15.9)
dataset_store          ``(*, settings) -> DatasetStore`` (the gold dataset: held-out and gold call ids)
current_version_pins   ``() -> VersionPins``: the pins the deployment runs with (the gold experiment's incumbent)
gold_experiment        ``(*, settings, heldout_only) -> (pins) -> EvalReport``: the gold suite with the pins given
====================== =======================================================================

Sync callables run in a worker thread, so heartbeats keep flowing during CPU-bound stages.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

ENTRYPOINTS: dict[str, str] = {
    "ingest_call": "advisor_intel.ingest.run:ingest_call",
    "ingest_script": "advisor_intel.ingest.run:transcript_from_script_async",
    "run_discourse": "advisor_intel.discourse.run:run_discourse",
    "agent_inputs": "advisor_intel.harness.deps:AgentInputs",
    "agent_services": "advisor_intel.harness.services:default_services",
    "run_agent": "advisor_intel.harness.run:run_agent",
    "verify_after_agent": "advisor_intel.verify.services:verify_after_agent",
    "conformal_thresholds": "advisor_intel.verify.conformal:ConformalThresholds",
    "run_voi_and_followups": "advisor_intel.voi.run:run_voi_and_followups",
    "sensitivity_runs": "advisor_intel.forecast.sensitivity:sensitivity_runs",
    "fna_analyse": "advisor_intel.fna_engine.analysis:analyse",
    "run_forecast": "advisor_intel.forecast.run:run_forecast",
    "extract_followup": "advisor_intel.extract.followup:extract_followup_ops",
    "build_labels": "advisor_intel.verify.labels:build_labels",
    "calibration_label": "advisor_intel.verify.labels:Label",
    "retrain_calibrator": "advisor_intel.verify.calibrator:retrain",
    "calibrator_type": "advisor_intel.verify.calibrator:Calibrator",
    "refit_conformal": "advisor_intel.verify.conformal:fit_conformal",
    "load_lexicon": "advisor_intel.lexicon.loader:load_lexicon",
    "lexicon_edit_pair": "advisor_intel.lexicon.fit:EditPair",
    "propose_lexicon_fit": "advisor_intel.lexicon.fit:propose_lexicon_fit",
    "sync_error_dataset": "advisor_intel.evals.datasets:sync_error_dataset",
    "sync_gold_candidates": "advisor_intel.evals.datasets:sync_gold_candidates",
    "run_model_swap": "advisor_intel.evals.model_swap:run_model_swap",
    "training_tasks": "advisor_intel.extract.training_prompt:tasks_from_profile",
    "training_task_keys": "advisor_intel.extract.training_prompt:task_keys",
    "render_stage_prompt": "advisor_intel.extract.training_prompt:render_stage_prompt",
    "export_training_data": "advisor_intel.evals.finetune:export_training_data",
    "sft_train": "advisor_intel.evals.finetune:sft_train",
    "grpo_train": "advisor_intel.evals.finetune:grpo_train",
    "promotion_check": "advisor_intel.evals.finetune:promotion_check",
    "write_group_routing": "advisor_intel.evals.finetune:write_group_routing",
    "reviewed_facts": "advisor_intel.evals.rl_reward:reviewed_facts",
    "rl_postprocess": "advisor_intel.extract.training_prompt:rl_postprocess",
    "hacking_checks": "advisor_intel.evals.rl_reward:hacking_checks",
    "dataset_store": "advisor_intel.evals.datasets:get_store",
    "current_version_pins": "advisor_intel.evals.extraction_eval:current_version_pins",
    "gold_experiment": "advisor_intel.activities.training:gold_experiment",
}


class StageUnavailable(ApplicationError):
    """A stage's code is not installed in this worker: not retried, since another attempt cannot fix it."""

    def __init__(self, message: str) -> None:
        super().__init__(message, type="StageUnavailable", non_retryable=True)


class StageFunctions:
    """Resolves entry points on first use; ``overrides`` replace any entry (tests, custom deployments)."""

    def __init__(self, overrides: Mapping[str, Any] | None = None, table: Mapping[str, str] | None = None) -> None:
        self.table = dict(table or ENTRYPOINTS)
        self.overrides = dict(overrides or {})
        self._cache: dict[str, Any] = {}

    def get(self, name: str) -> Any:
        if name in self.overrides:
            return self.overrides[name]
        if name not in self._cache:
            target = self.table.get(name)
            if target is None:
                raise StageUnavailable(f"no entry point named {name!r}")
            module, _, attr = target.partition(":")
            try:
                self._cache[name] = getattr(importlib.import_module(module), attr)
            except (ImportError, AttributeError) as e:
                raise StageUnavailable(f"{name}: cannot load {target} ({e})") from e
        return self._cache[name]

    def optional(self, name: str) -> Any | None:
        """The entry, or None when its stage is not installed (for steps that run without it)."""
        try:
            return self.get(name)
        except StageUnavailable:
            return None

    def available(self, name: str) -> bool:
        return self.optional(name) is not None


async def invoke(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call a stage function: await it when async, else run it in a thread (keeps heartbeats flowing)."""
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    out = await asyncio.to_thread(fn, *args, **kwargs)
    if inspect.isawaitable(out):
        out = await out
    return out


def in_activity() -> bool:
    try:
        activity.info()
        return True
    except RuntimeError:
        return False


def heartbeat(*details: Any) -> None:
    if in_activity():
        activity.heartbeat(*details)


@contextlib.asynccontextmanager
async def heartbeating(detail: str, *, every_s: float | None = None) -> AsyncIterator[None]:
    """Heartbeat in the background while the block runs (a third of the heartbeat timeout by default)."""
    if not in_activity():
        yield
        return
    timeout = activity.info().heartbeat_timeout
    interval = every_s or (max(1.0, timeout.total_seconds() / 3) if timeout else 10.0)

    async def beat() -> None:
        while True:
            activity.heartbeat(detail)
            await asyncio.sleep(interval)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
