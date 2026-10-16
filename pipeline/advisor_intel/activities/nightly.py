"""Nightly feedback activities (spec §11.4) and the monthly model-swap run (§15.4).

The learning code lives in ``verify/``, ``lexicon/`` and ``evals/``; these activities gather its
inputs from Postgres and object storage, call it, and store what it returns:

- ``build_calibration_labels``: each review submitted in the window → §9.3 labels, from the profile as
  the reviewer first saw it, the reviewer's ops and the reviewed profile; one JSONL file per night;
- ``retrain_calibrator``: every label so far and the promoted calibrator → a candidate when one is due;
  the calibrator code decides that, and whether the candidate is promoted (§9.4). The artifact goes to
  object storage and a registry row to ``calibrators``;
- ``refit_conformal``: τ per slot family (§9.5) for the promoted calibrator, from the reviewed calls it
  scored (their scores are held out from its training). The thresholds go to object storage
  (``metrics.thresholds_uri`` on the calibrator's pooled row) and τ to its registry rows;
- ``propose_lexicon_fit``: the edit pairs of every reviewed vague value → a proposed lexicon version,
  YAML and diff, in object storage (§11.4 step 2);
- ``sync_error_dataset`` / ``sync_gold_candidates``: Braintrust datasets (skipped without an API key).
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError
from temporalio import activity

from ..config import Settings
from ..models.dists import Quantity
from ..models.facts import Fact
from ..models.profile import Profile, StateOp
from ..models.runs import VersionPins
from ..storage.objects import ObjectStore, safe_segment
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
from .stages import StageFunctions, heartbeat, heartbeating, invoke

log = logging.getLogger(__name__)

LABELS_PREFIX = "calibration/labels/"
COLD_START = "cold-start"  # the calibrator version before any labels (VersionPins.calibrator_version)
ERROR_ACTIONS = ("reject", "reattribute", "add")


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """A field of a stage's result, whether a model, a dataclass or a dict."""
    value = obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)
    return default if value is None else value


def _plain(value: Any) -> Any:
    """JSON-able data (numpy scalars and arrays become numbers and lists) for Postgres and payloads."""
    return json.loads(json.dumps(value, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)))


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
        """Every label so far, in arrival order (one file per night, keyed by date)."""
        rows: list[dict[str, Any]] = []
        for key in sorted(await self.objects.list(LABELS_PREFIX)):
            for line in (await self.objects.get_bytes(key)).decode().splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    def _labels(self, rows: list[dict[str, Any]]) -> list[Any]:
        label = self.stages.optional("calibration_label")
        return [label.model_validate(r) for r in rows] if label is not None else rows

    async def _reviewed(self, run_id: str, submit: ReviewEvent) -> tuple[Profile, list[StateOp], Profile] | None:
        """The profile as the reviewer first saw it, the reviewer's ops, and the reviewed profile (§9.3)."""
        after = await self.store.load_profile(run_id, submit.profile_version) if submit.profile_version else None
        after = after or await self.store.load_profile(run_id)
        ops = [op for op in after.ops_log if op.actor == "reviewer"] if after is not None else []
        if after is None or not ops:
            return None
        first = min(op.seq for op in ops)
        earlier = [v.version for v in await self.store.profile_versions(run_id) if v.last_seq < first]
        before = await self.store.load_profile(run_id, max(earlier)) if earlier else None
        return (before, ops, after) if before is not None else None

    async def _pipeline_pins(self, rows: list[dict[str, Any]]) -> VersionPins:
        """The versions the pipeline runs now: the latest labelled run's (a change triggers a retrain, §9.4)."""
        for r in reversed(rows):
            run = await self.store.get_run(r["run_id"]) if r.get("run_id") else None
            if run is not None:
                return run.versions
        return VersionPins()

    # ------------------------------------------------------------------ calibration (§9.3–9.5)

    @activity.defn(name=BUILD_LABELS)
    async def build_calibration_labels(self, w: NightlyWindow) -> LabelsOutcome:
        with run_context(stage="nightly"):
            events = await self.store.review_events(since=w.since, until=w.until)
            out = LabelsOutcome(since=w.since, until=w.until, n_events=len(events))
            submits: dict[str, ReviewEvent] = {}
            for e in events:
                if e.action == "submit":
                    submits.setdefault(e.workflow_id, e)  # the first submission is the review (§13.1)
            if not submits:
                return out
            build = self.stages.get("build_labels")
            labels: list[dict[str, Any]] = []
            misses = calls = 0
            for run_id, submit in sorted(submits.items()):
                reviewed = await self._reviewed(run_id, submit)
                if reviewed is None:
                    continue
                before, ops, after = reviewed
                heartbeat(run_id)
                batch = await invoke(build, before, ops, after=after, spot_check=bool(submit.detail.get("spot_check")))
                got = [_plain(dump(lb)) for lb in (batch if isinstance(batch, list) else _attr(batch, "labels", []))]
                misses += len(_attr(batch, "recall_misses", [])) if not isinstance(batch, list) else 0
                calls += bool(got)
                labels += got
            if not labels:
                return out.model_copy(update={"n_recall_misses": misses})
            key = f"{LABELS_PREFIX}{w.until:%Y/%m/%d}/{digest(w.since.isoformat(), w.until.isoformat())}.jsonl"
            uri = await self.objects.put_bytes(key, _jsonl(labels), content_type="application/x-ndjson")
            return out.model_copy(update={"n_labels": len(labels), "n_calls": calls, "n_recall_misses": misses, "labels_uri": uri})

    @activity.defn(name=RETRAIN_CALIBRATOR)
    async def retrain_calibrator(self, labels: LabelsOutcome) -> CalibratorOutcome:
        with run_context(stage="nightly"):
            rows = await self._all_labels()
            if not rows:
                return CalibratorOutcome(skipped_reason="no labels yet")
            current = next((r for r in await self.store.calibrators(promoted_only=True) if r.slot_group == "*"), None)
            incumbent = None
            if current is not None and current.artifact_uri:
                incumbent = self.stages.get("calibrator_type").from_bytes(await self.objects.get_bytes(current.artifact_uri))
            pins = await self._pipeline_pins(rows)
            async with heartbeating("retrain_calibrator"):
                report = await invoke(self.stages.get("retrain_calibrator"), self._labels(rows), incumbent=incumbent, pins=pins)
            candidate = _attr(report, "candidate")
            reason = str(_attr(report, "reason", ""))
            if candidate is None:  # not due: fewer than 200 new labels and no version change
                return CalibratorOutcome(
                    version=current.version if current else None, n_labels=len(rows), skipped_reason=reason or "not due"
                )
            version = str(_attr(candidate, "version"))
            promoted = bool(_attr(report, "promoted", False))
            metrics = _plain(
                {
                    **dict(_attr(candidate, "metrics", {})),
                    "n_labels": len(rows),
                    "trigger": _attr(report, "trigger"),
                    "reason": reason,
                    "candidate_ece": _attr(report, "candidate_ece"),
                    "incumbent_ece": _attr(report, "incumbent_ece"),
                }
            )
            uri = await self.objects.put_bytes(
                f"calibration/calibrators/{safe_segment(version)}.bin", candidate.to_bytes(), content_type="application/octet-stream"
            )
            if hasattr(candidate, "experiment"):  # the record for the Braintrust experiment calibration/<version> (§9.4)
                await self.objects.put_json(f"calibration/experiments/{safe_segment(version)}.json", _plain(candidate.experiment()))
            await self.store.register_calibrator(
                CalibratorRecord(version=version, slot_group="*", artifact_uri=uri, metrics=metrics, promoted=promoted)
            )
            return CalibratorOutcome(
                version=version,
                promoted=promoted,
                n_labels=len(rows),
                metrics=metrics,
                artifact_uri=uri,
                skipped_reason=None if promoted else f"not promoted: {reason}",
            )

    @activity.defn(name=REFIT_CONFORMAL)
    async def refit_conformal(self, cal: CalibratorOutcome) -> ConformalOutcome:
        with run_context(stage="nightly"):
            alpha = self.settings.conformal_alpha
            version = (cal.version if cal.promoted else None) or await self.store.current_calibrator_version() or COLD_START
            # §9.5: the calibration calls are the reviewed calls this calibrator scored at run time.
            rows = [r for r in await self._all_labels() if (r.get("calibrator_version") or COLD_START) == version]
            async with heartbeating("refit_conformal"):
                th = await invoke(
                    self.stages.get("refit_conformal"),
                    self._labels(rows),
                    alpha=alpha,
                    min_calls=self.settings.min_conformal_calls,
                    calibrator_version=version,
                    settings=self.settings,
                )
            data = _plain(dump(th))
            name = safe_segment(str(data.get("version") or "thresholds"))
            uri = await self.objects.put_json(f"calibration/conformal/{safe_segment(version)}/{name}.json", data)
            enabled = bool(data.get("enabled"))
            pooled = data.get("tau_pooled")
            tau = {str(k): float(v) for k, v in (data.get("tau_by_family") or {}).items() if v is not None}
            n_calls = int(data.get("n_calls") or 0)
            promoted = any(r.promoted for r in await self.store.calibrators(version=version))
            meta = {"auto_accept": enabled, "thresholds_uri": uri, "conformal_version": data.get("version")}
            await self.store.register_calibrator(
                CalibratorRecord(
                    version=version, slot_group="*", tau=pooled, alpha=alpha, n_calib_calls=n_calls, metrics=meta, promoted=promoted
                )
            )
            for family, t in tau.items():
                await self.store.register_calibrator(
                    CalibratorRecord(
                        version=version,
                        slot_group=family,
                        tau=t,
                        alpha=float(data.get("family_alpha") or alpha),
                        n_calib_calls=int((data.get("n_by_family") or {}).get(family, n_calls)),
                        metrics={"auto_accept": enabled, "source": (data.get("source_by_family") or {}).get(family)},
                        promoted=promoted,
                    )
                )
            return ConformalOutcome(
                calibrator_version=version,
                alpha=alpha,
                tau={**({"*": float(pooled)} if pooled is not None else {}), **tau},
                n_calib_calls=n_calls,
                auto_accept_enabled=enabled,
                thresholds_uri=uri,
                skipped_reason=None if enabled else str(data.get("reason") or "auto-accept is off"),
            )

    # ------------------------------------------------------------------ lexicon (§8.2, §11.4)

    @activity.defn(name=PROPOSE_LEXICON_FIT)
    async def propose_lexicon_fit(self, w: NightlyWindow) -> LexiconFitOutcome:
        with run_context(stage="nightly"):
            edits = [e for e in await self.store.review_events(until=w.until) if e.action == "edit"]
            pair_type = self.stages.get("lexicon_edit_pair") if edits else None
            pairs = [p for e in edits for p in lexicon_pairs(e, pair_type)]
            if not pairs:
                return LexiconFitOutcome()
            version = os.environ.get("ADVISOR_INTEL_LEXICON_VERSION") or VersionPins().lexicon_version
            lex = await invoke(self.stages.get("load_lexicon"), version)
            async with heartbeating("propose_lexicon_fit"):
                diff = await invoke(self.stages.get("propose_lexicon_fit"), pairs, lex)
            changes = list(_attr(diff, "changes", []))
            out = LexiconFitOutcome(
                n_pairs=len(pairs),
                keys_fitted=[f"{_attr(c, 'section')}: {_attr(c, 'key')}" for c in changes],
                base_version=str(_attr(diff, "base_version", version)),
                pull_request=_attr(diff, "pull_request"),
            )
            if not changes:
                return out
            new_version = str(_attr(diff, "new_version", ""))
            stem = f"lexicon/proposals/{w.until:%Y-%m-%d}-v{safe_segment(new_version)}"
            yaml_uri = await self.objects.put_bytes(f"{stem}.yaml", str(_attr(diff, "yaml_text", "")).encode(), content_type="text/yaml")
            diff_text = _attr(diff, "diff_text")
            diff_uri = (
                await self.objects.put_bytes(f"{stem}.diff", str(diff_text).encode(), content_type="text/x-diff") if diff_text else None
            )
            return out.model_copy(update={"new_version": new_version, "proposal_uri": yaml_uri, "diff_uri": diff_uri})

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
                corrections = [
                    e for e in evs if e.action in ("edit", "change_status", "reject", "reattribute", "add", "mark_unknown", "remove")
                ]
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
            return ModelSwapOutcome(results=_plain(list(_attr(res, "results", []))), pull_requests=list(_attr(res, "pull_requests", [])))


def lexicon_pairs(e: ReviewEvent, pair_type: Any) -> list[Any]:
    """The (phrase, intermediate form, adviser-edited value) pairs one edit of a vague value gives (§11.4 step 2):
    ``EditPair.from_fact`` on the fact before the edit, which records its lexicon lookups, and the edited value."""
    before, after = e.detail.get("fact_before") or {}, e.detail.get("fact_after") or {}
    if not before.get("normalization") or not after.get("value"):
        return []
    try:
        fact, edited = Fact.model_validate(before), Quantity.model_validate(after["value"])
    except ValidationError as err:
        log.warning("review event %s: cannot read the edited fact %s: %s", e.id, e.fact_id, err)
        return []
    return list(pair_type.from_fact(fact, edited))


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
