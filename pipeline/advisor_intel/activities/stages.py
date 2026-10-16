"""Entry points into the stage code that activities call, resolved lazily (spec §13.1, §14.1).

Activities do the I/O around each stage (load inputs from storage, heartbeat, save outputs); the
stages themselves live in their own packages and are imported only when an activity runs. This
table is the single place where those names are bound, and tests (or a deployment) can override
any entry with a callable.

Expected signatures:

====================== =======================================================================
name                   callable
====================== =======================================================================
ingest_call            ``async (audio: Path, meta, *, asr, diarizer, role_agent_pins, second_pass=None,
                       prior_fna=None, crm_names=None) -> Transcript`` (§5)
ingest_meta            optional: the type ``meta`` is built from (keyword arguments call_id, advisor_id,
                       client_ids, started_at, anchor_date, channel_layout); a dict when absent
run_discourse          ``async (transcript, *, prior: Profile | None, crm: CrmRecord | None, pins, catalog)
                       -> DiscourseOutput`` (§6)
agent_inputs           the harness's ``AgentInputs`` type (call_id, run_id, transcript, discourse, profile,
                       pins, budgets, quality)
run_agent              ``async (inputs, *, checkpoints: CheckpointStore, heartbeat) -> AgentResult``
                       with ``.outcome: AgentOutcome`` and ``.profile: Profile`` (§13.2)
run_voi_and_followups  ``(profile, *, catalog, pins, pos_runs, analyse, transcript, settings) -> VoiOutput``
                       with ``.profile``, ``.followups``, ``.flags``, ``.priorities`` (§10)
sensitivity_runs       the forecast engine's reduced runs, passed to VOI as ``pos_runs`` (§10.2)
fna_analyse            the FNA engine's ``analyse(doc)`` (§12.7); dumped to a dict for VOI and review
run_forecast           ``(profile, *, base_document=None, mode="preview"|"final", seed=None) -> ForecastResult``
extract_followup       ``async (profile, transcript, *, followup: FollowUp, pins, catalog) -> list[StateOp]``:
                       the extraction sub-agent over a one-segment answer (§10.6)
build_labels           ``(events: list[dict]) -> list[dict]``: §9.3 labels from review events
retrain_calibrator     ``(labels: list[dict], *, previous: bytes | None, settings) -> dict`` with ``version``,
                       ``artifact`` (bytes), ``metrics``, ``promote`` (§9.4)
refit_conformal        ``(labels: list[dict], *, calibrator: bytes | None, alpha, min_calls) -> dict`` with
                       ``tau`` (family → threshold), ``n_calib_calls``, ``enabled`` (§9.5)
load_lexicon           ``(version) -> Lexicon``
propose_lexicon_fit    ``(pairs: list[dict], lex) -> dict | model`` with ``keys``/``keys_fitted``,
                       optional ``yaml``/``diff`` and ``pull_request`` (§11.4)
sync_error_dataset     ``(rows: list[dict], *, settings) -> int`` rows inserted into ``production-errors``
sync_gold_candidates   ``(rows: list[dict], *, settings) -> int`` rows inserted (§15.6)
run_model_swap         ``(candidates: dict[str, str], *, settings) -> dict`` with ``results``, ``pull_requests``
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

ENTRYPOINTS: dict[str, str] = {
    "ingest_call": "advisor_intel.ingest.run:ingest_call",
    "ingest_meta": "advisor_intel.ingest.run:CallMeta",
    "run_discourse": "advisor_intel.discourse.run:run_discourse",
    "agent_inputs": "advisor_intel.harness.run:AgentInputs",
    "run_agent": "advisor_intel.harness.run:run_agent",
    "run_voi_and_followups": "advisor_intel.voi.run:run_voi_and_followups",
    "sensitivity_runs": "advisor_intel.forecast.sensitivity:sensitivity_runs",
    "fna_analyse": "advisor_intel.fna_engine.analysis:analyse",
    "run_forecast": "advisor_intel.forecast.run:run_forecast",
    "extract_followup": "advisor_intel.extract.followup:extract_followup_answer",
    "build_labels": "advisor_intel.verify.labels:build_labels",
    "retrain_calibrator": "advisor_intel.verify.calibrator:retrain",
    "refit_conformal": "advisor_intel.verify.conformal:refit",
    "load_lexicon": "advisor_intel.lexicon.loader:load_lexicon",
    "propose_lexicon_fit": "advisor_intel.lexicon.fit:propose_lexicon_fit",
    "sync_error_dataset": "advisor_intel.evals.datasets:sync_error_dataset",
    "sync_gold_candidates": "advisor_intel.evals.datasets:sync_gold_candidates",
    "run_model_swap": "advisor_intel.evals.model_swap:run_model_swap",
}


class StageUnavailable(RuntimeError):
    """A stage's code is not installed in this worker."""


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

    def available(self, name: str) -> bool:
        try:
            self.get(name)
            return True
        except StageUnavailable:
            return False


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
