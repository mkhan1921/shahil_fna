"""Nightly feedback activities (spec §11.4) and the monthly model-swap run (§15.4).

The learning code lives in ``verify/``, ``lexicon/`` and ``evals/``; these activities gather its
inputs from the day's review events, call it, and store what it returns:

- ``build_calibration_labels``: review events → §9.3 labels, one JSONL file per night in object storage;
- ``retrain_calibrator``: every label so far → a calibrator artifact in object storage and a registry
  row in ``calibrators`` (promotion is the calibrator's decision, §9.4); skipped until 200 new labels;
- ``refit_conformal``: τ per slot family for the current calibrator (§9.5), recorded on its rows;
- ``propose_lexicon_fit``: (phrase, intermediate form, adviser-edited value) pairs → a lexicon proposal;
- ``sync_error_dataset`` / ``sync_gold_candidates``: Braintrust datasets (skipped without an API key).
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Any

from temporalio import activity

from ..config import Settings
from ..models.runs import VersionPins
from ..storage.objects import ObjectStore
from ..storage.records import CalibratorRecord, ReviewEvent
from ..storage.store import Store
from ..telemetry import run_context
from ..workflows.contracts import (
    BUILD_LABELS,
    PROPOSE_LEXICON_FIT,
    REFIT_CONFORMAL,
    RETRAIN_CALIBRATOR,
    RUN_MODEL_SWAP,
    SYNC_ERROR_DATASET,
    SYNC_GOLD_CANDIDATES,
    CalibratorOutcome,
    ConformalOutcome,
    DatasetSyncOutcome,
    LabelsOutcome,
    LexiconFitOutcome,
    ModelSwapArgs,
    ModelSwapOutcome,
    NightlyWindow,
)
from .keys import digest
from .pipeline import dump
from .stages import StageFunctions, heartbeating, invoke

log = logging.getLogger(__name__)

LABELS_PREFIX = "calibration/labels/"
RETRAIN_EVERY = 200  # §9.4: retrain every 200 new labels (or on a version change)
LABEL_ACTIONS = ("accept", "edit", "change_status", "reject", "reattribute", "mark_unknown", "remove")
ERROR_ACTIONS = ("reject", "reattribute", "add")


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    data = dump(obj)
    if isinstance(data, dict):
        for n in names:
            if n in data and data[n] is not None:
                return data[n]
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return default


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(r, sort_keys=True, default=str) for r in rows) + "\n").encode()


class NightlyActivities:
    def __init__(self, *, store: Store, objects: ObjectStore, settings: Settings, stages: StageFunctions | None = None) -> None:
        self.store = store
        self.objects = objects
        self.settings = settings
        self.stages = stages or StageFunctions()

    def activities(self) -> list[Any]:
        return [
            self.build_calibration_labels,
            self.retrain_calibrator,
            self.refit_conformal,
            self.propose_lexicon_fit,
            self.sync_error_dataset,
            self.sync_gold_candidates,
            self.run_model_swap_eval,
        ]

    async def _all_labels(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key in await self.objects.list(LABELS_PREFIX):
            for line in (await self.objects.get_bytes(key)).decode().splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    # ------------------------------------------------------------------ calibration (§9.3–9.5)

    @activity.defn(name=BUILD_LABELS)
    async def build_calibration_labels(self, w: NightlyWindow) -> LabelsOutcome:
        with run_context(stage="nightly"):
            events = await self.store.review_events(since=w.since, until=w.until)
            rows = [e.model_dump(mode="json") for e in events if e.fact_id and e.action in LABEL_ACTIONS]
            out = LabelsOutcome(since=w.since, until=w.until, n_events=len(events))
            if not rows:
                return out
            labels = [dump(x) for x in await invoke(self.stages.get("build_labels"), rows)]
            if not labels:
                return out
            key = f"{LABELS_PREFIX}{w.until:%Y/%m/%d}/{digest(w.since.isoformat(), w.until.isoformat())}.jsonl"
            uri = await self.objects.put_bytes(key, _jsonl(labels), content_type="application/x-ndjson")
            calls = {lbl.get("call_id") or lbl.get("workflow_id") or lbl.get("run_id") for lbl in labels}
            return out.model_copy(update={"n_labels": len(labels), "n_calls": len(calls - {None}), "labels_uri": uri})

    @activity.defn(name=RETRAIN_CALIBRATOR)
    async def retrain_calibrator(self, labels: LabelsOutcome) -> CalibratorOutcome:
        with run_context(stage="nightly"):
            all_labels = await self._all_labels()
            if not all_labels:
                return CalibratorOutcome(skipped_reason="no labels yet")
            current = next(iter(await self.store.calibrators(promoted_only=True)), None)
            trained_on = int(current.metrics.get("n_labels", 0)) if current else 0
            if current is not None and len(all_labels) - trained_on < RETRAIN_EVERY:
                return CalibratorOutcome(
                    version=current.version, n_labels=len(all_labels), skipped_reason=f"{len(all_labels) - trained_on} new labels; retrain at {RETRAIN_EVERY}"
                )
            previous = await self.objects.get_bytes(current.artifact_uri) if current and current.artifact_uri else None
            async with heartbeating("retrain_calibrator"):
                res = await invoke(self.stages.get("retrain_calibrator"), all_labels, previous=previous, settings=self.settings)
            version = str(_get(res, "version"))
            artifact = _get(res, "artifact")
            metrics = {**dict(_get(res, "metrics", default={})), "n_labels": len(all_labels)}
            promoted = bool(_get(res, "promote", "promoted", default=False))
            uri = None
            if artifact is not None:
                data = artifact if isinstance(artifact, bytes) else json.dumps(artifact, default=str).encode()
                uri = await self.objects.put_bytes(f"calibration/calibrators/{version}.bin", data, content_type="application/octet-stream")
            await self.store.register_calibrator(CalibratorRecord(version=version, slot_group="*", artifact_uri=uri, metrics=metrics, promoted=promoted))
            return CalibratorOutcome(version=version, promoted=promoted, n_labels=len(all_labels), metrics=metrics, artifact_uri=uri)

    @activity.defn(name=REFIT_CONFORMAL)
    async def refit_conformal(self, cal: CalibratorOutcome) -> ConformalOutcome:
        with run_context(stage="nightly"):
            alpha = self.settings.conformal_alpha
            version = cal.version or await self.store.current_calibrator_version()
            if version is None:
                return ConformalOutcome(alpha=alpha, skipped_reason="no calibrator")
            recs = await self.store.calibrators(version=version)
            pooled = next((r for r in recs if r.slot_group == "*"), None)
            artifact = await self.objects.get_bytes(pooled.artifact_uri) if pooled and pooled.artifact_uri else None
            labels = await self._all_labels()
            async with heartbeating("refit_conformal"):
                res = await invoke(self.stages.get("refit_conformal"), labels, calibrator=artifact, alpha=alpha, min_calls=self.settings.min_conformal_calls)
            tau = {str(k): float(v) for k, v in dict(_get(res, "tau", default={})).items()}
            n_calls = int(_get(res, "n_calib_calls", default=0))
            enabled = bool(_get(res, "enabled", default=n_calls >= self.settings.min_conformal_calls))
            for family, t in tau.items():
                await self.store.register_calibrator(
                    CalibratorRecord(
                        version=version, slot_group=family, tau=t, alpha=alpha, n_calib_calls=n_calls, metrics={"auto_accept": enabled}, promoted=bool(pooled and pooled.promoted)
                    )
                )
            return ConformalOutcome(calibrator_version=version, alpha=alpha, tau=tau, n_calib_calls=n_calls, auto_accept_enabled=enabled)

    # ------------------------------------------------------------------ lexicon (§8.2, §11.4)

    @activity.defn(name=PROPOSE_LEXICON_FIT)
    async def propose_lexicon_fit(self, w: NightlyWindow) -> LexiconFitOutcome:
        with run_context(stage="nightly"):
            events = await self.store.review_events(until=w.until)
            pairs = [p for e in events if e.action == "edit" for p in [lexicon_pair(e)] if p is not None]
            if not pairs:
                return LexiconFitOutcome()
            version = os.environ.get("ADVISOR_INTEL_LEXICON_VERSION") or VersionPins().lexicon_version
            lex = await invoke(self.stages.get("load_lexicon"), version)
            async with heartbeating("propose_lexicon_fit"):
                proposal = await invoke(self.stages.get("propose_lexicon_fit"), pairs, lex)
            keys = list(_get(proposal, "keys_fitted", "keys", default=[]) or [])
            text = _get(proposal, "yaml", "diff", default=None)
            uri = None
            if text:
                uri = await self.objects.put_bytes(f"lexicon/proposals/{w.until:%Y-%m-%d}-{version}.yaml", str(text).encode(), content_type="text/yaml")
            return LexiconFitOutcome(n_pairs=len(pairs), keys_fitted=[str(k) for k in keys], pull_request=_get(proposal, "pull_request", "pr_url"), proposal_uri=uri)

    # ------------------------------------------------------------------ Braintrust datasets (§11.4, §15.5, §15.6)

    @activity.defn(name=SYNC_ERROR_DATASET)
    async def sync_error_dataset(self, w: NightlyWindow) -> DatasetSyncOutcome:
        with run_context(stage="nightly"):
            if not self.settings.braintrust_api_key:
                return DatasetSyncOutcome(dataset="production-errors", skipped_reason="no Braintrust API key")
            events = await self.store.review_events(since=w.since, until=w.until)
            rows = [error_row(e) for e in events if e.action in ERROR_ACTIONS]
            if not rows:
                return DatasetSyncOutcome(dataset="production-errors")
            n = await invoke(self.stages.get("sync_error_dataset"), rows, settings=self.settings)
            return DatasetSyncOutcome(dataset="production-errors", inserted=int(n or 0))

    @activity.defn(name=SYNC_GOLD_CANDIDATES)
    async def sync_gold_candidates(self, w: NightlyWindow) -> DatasetSyncOutcome:
        with run_context(stage="nightly"):
            if not self.settings.braintrust_api_key:
                return DatasetSyncOutcome(dataset="gold-candidates", skipped_reason="no Braintrust API key")
            events = await self.store.review_events(since=w.since, until=w.until)
            by_run: dict[str, list[ReviewEvent]] = defaultdict(list)
            for e in events:
                by_run[e.workflow_id].append(e)
            rows = []
            for run_id, evs in sorted(by_run.items()):
                corrections = [e for e in evs if e.action in ("edit", "change_status", "reject", "reattribute", "add", "mark_unknown", "remove")]
                followup_edits = [e.detail.get("edit") for e in evs if e.action == "edit_followup"]
                if not corrections and not followup_edits:
                    continue
                run = await self.store.get_run(run_id)
                rows.append(
                    {
                        "run_id": run_id,
                        "call_id": run.call_id if run else None,
                        "versions": run.versions.model_dump(mode="json") if run else {},
                        "corrections": [error_row(e) for e in corrections],
                        "followup_edits": followup_edits,
                        "reviewed_profile": f"profiles/{run_id}/",
                    }
                )
            if not rows:
                return DatasetSyncOutcome(dataset="gold-candidates")
            n = await invoke(self.stages.get("sync_gold_candidates"), rows, settings=self.settings)
            return DatasetSyncOutcome(dataset="gold-candidates", inserted=int(n or 0))

    # ------------------------------------------------------------------ model swap (§15.4)

    @activity.defn(name=RUN_MODEL_SWAP)
    async def run_model_swap_eval(self, args: ModelSwapArgs) -> ModelSwapOutcome:
        with run_context(stage="model_swap"):
            candidates = dict(args.candidates) or json.loads(os.environ.get("ADVISOR_INTEL_MODEL_SWAP_CANDIDATES", "{}") or "{}")
            if not candidates:
                return ModelSwapOutcome(results=[{"skipped": "no candidate models configured"}])
            async with heartbeating("model_swap"):
                res = await invoke(self.stages.get("run_model_swap"), candidates, settings=self.settings)
            return ModelSwapOutcome(results=list(_get(res, "results", default=[])), pull_requests=list(_get(res, "pull_requests", default=[])))


def lexicon_pair(e: ReviewEvent) -> dict[str, Any] | None:
    """(phrase, intermediate form, adviser-edited value) from an edit of a normalised vague value (§11.4 step 2)."""
    before, after = e.detail.get("fact_before") or {}, e.detail.get("fact_after") or {}
    norm = before.get("normalization") or {}
    key = norm.get("lexicon_key") or norm.get("key")
    if not key or not after.get("value"):
        return None
    hedges = [h.get("term") for h in before.get("hedges") or [] if h.get("term")]
    return {
        "lexicon_key": key,
        "phrase": norm.get("phrase") or before.get("said") or (hedges[0] if hedges else ""),
        "intermediate": norm.get("intermediate") or norm.get("form"),
        "lexicon_version": norm.get("lexicon_version"),
        "original_value": before.get("value"),
        "edited_value": after.get("value"),
        "slot_id": before.get("slot_id"),
        "fact_id": e.fact_id,
        "run_id": e.workflow_id,
    }


def error_row(e: ReviewEvent) -> dict[str, Any]:
    """A reason-coded rejection or a missed slot for ``production-errors`` (§11.4 step 3)."""
    fact = e.detail.get("fact_before") or e.detail.get("fact_after") or {}
    return {
        "run_id": e.workflow_id,
        "fact_id": e.fact_id,
        "slot_id": fact.get("slot_id"),
        "action": e.action,
        "reason": e.reason or ("missed_slot" if e.action == "add" else None),
        "quotes": [ev.get("quote") for ev in fact.get("evidence") or []],
        "value": fact.get("value"),
        "interpretation": fact.get("interpretation"),
        "note": e.detail.get("note"),
        "reviewer_id": e.reviewer_id,
        "at": e.at.isoformat(),
        "tags": [t for t in ((fact.get("slot_id") or "").split(".")[0], e.reason or e.action) if t],
    }
