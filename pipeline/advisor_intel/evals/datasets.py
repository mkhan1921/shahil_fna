"""Datasets, the nightly error and gold-candidate syncs, and the Stage 1–2 output cache (spec §11.4, §15.1).

Datasets live in Braintrust (``gold-calls``, ``gold-transcripts``, ``synthetic-calls``, ``production-errors``, plus
``gold-candidates`` and ``followup-edits`` for gold expansion and GEPA feedback, and ``eval-baselines`` for the CI
gate). With ``BRAINTRUST_API_KEY`` set the Braintrust SDK is used; otherwise every dataset is a local JSONL file
under ``pipeline/.data/datasets`` with the same records, so the syncs, the evals and the tests run offline.

Stage 1 and Stage 2 outputs for every dataset call are cached (object storage, or ``.data/cache`` locally) under
a key built only from the pins those stages depend on, so an experiment that changes only extraction prompts,
models or the lexicon starts from the cache (§15.1).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Protocol, Sequence

from pydantic import BaseModel, ValidationError

from ..config import get_settings
from ..models.crm import CrmRecord
from ..models.entities import DiscourseOutput
from ..models.fna import FnaDocument
from ..models.profile import Profile, StateOp
from ..models.review import FollowupEdit, ReviewResult
from ..models.runs import VersionPins
from ..models.transcript import Transcript
from ..schema.slots import Catalog, load_catalog
from ._runtime import braintrust_enabled, data_dir, stable_hash, to_jsonable
from .gold import GoldCall
from .synthetic import SYNTHETIC_DATASET, SYNTHETIC_TAG, SyntheticCall, assert_not_synthetic, script_transcript

log = logging.getLogger(__name__)

GOLD_CALLS = "gold-calls"
GOLD_TRANSCRIPTS = "gold-transcripts"
PRODUCTION_ERRORS = "production-errors"
GOLD_CANDIDATES = "gold-candidates"
FOLLOWUP_EDITS = "followup-edits"
EVAL_BASELINES = "eval-baselines"
# Braintrust project for datasets and experiments (the spec's Eval("advisor-extraction", …)); traces use settings.braintrust_project.
EVAL_PROJECT = os.environ.get("ADVISOR_INTEL_EVAL_PROJECT", "advisor-extraction")
DATASETS: tuple[str, ...] = (
    GOLD_CALLS,
    GOLD_TRANSCRIPTS,
    SYNTHETIC_DATASET,
    PRODUCTION_ERRORS,
    GOLD_CANDIDATES,
    FOLLOWUP_EDITS,
    EVAL_BASELINES,
)


class DatasetRecord(BaseModel):
    """One dataset row, shaped like a Braintrust dataset event."""

    id: str
    input: Any = None
    expected: Any = None
    metadata: dict[str, Any] = {}
    tags: list[str] = []


class DatasetStore(Protocol):
    def insert(self, dataset: str, records: Sequence[DatasetRecord]) -> list[str]: ...

    def fetch(self, dataset: str) -> list[DatasetRecord]: ...


class LocalStore:
    """Datasets as JSONL files (upsert by id), for offline runs and tests."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (data_dir() / "datasets")

    def path(self, dataset: str) -> Path:
        return self.root / f"{dataset}.jsonl"

    def fetch(self, dataset: str) -> list[DatasetRecord]:
        p = self.path(dataset)
        if not p.exists():
            return []
        return [DatasetRecord.model_validate_json(line) for line in p.read_text().splitlines() if line.strip()]

    def insert(self, dataset: str, records: Sequence[DatasetRecord]) -> list[str]:
        existing = {r.id: r for r in self.fetch(dataset)}
        for r in records:
            existing[r.id] = r
        p = self.path(dataset)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(to_jsonable(r.model_dump(mode="json")), sort_keys=True) + "\n" for r in existing.values()))
        tmp.replace(p)
        return [r.id for r in records]


class BraintrustStore:
    """Datasets in Braintrust through ``braintrust.init_dataset`` (inserts upsert by id)."""

    def __init__(self, project: str | None = None, *, init_dataset: Callable[..., Any] | None = None, api_key: str | None = None) -> None:
        self.project = project or EVAL_PROJECT
        if init_dataset is None:
            from braintrust import init_dataset as _init

            init_dataset = _init
        self._init = init_dataset
        self._api_key = api_key
        self._open: dict[str, Any] = {}

    def _dataset(self, name: str) -> Any:
        if name not in self._open:
            kw = {"api_key": self._api_key} if self._api_key else {}
            self._open[name] = self._init(project=self.project, name=name, **kw)
        return self._open[name]

    def insert(self, dataset: str, records: Sequence[DatasetRecord]) -> list[str]:
        ds = self._dataset(dataset)
        ids = [
            ds.insert(
                input=to_jsonable(r.input), expected=to_jsonable(r.expected), metadata=to_jsonable(r.metadata), tags=r.tags or None, id=r.id
            )
            for r in records
        ]
        ds.flush()
        return ids

    def fetch(self, dataset: str) -> list[DatasetRecord]:
        out = []
        for row in self._dataset(dataset).fetch():
            out.append(
                DatasetRecord(
                    id=str(row.get("id")),
                    input=row.get("input"),
                    expected=row.get("expected"),
                    metadata=dict(row.get("metadata") or {}),
                    tags=list(row.get("tags") or []),
                )
            )
        return out


def get_store(*, project: str | None = None, root: Path | None = None, settings: Any = None) -> DatasetStore:
    """Braintrust when ``BRAINTRUST_API_KEY`` is set (or ``settings.braintrust_api_key``), else local JSONL."""
    key = getattr(settings, "braintrust_api_key", None) if settings is not None else None
    return BraintrustStore(project, api_key=key) if (key or braintrust_enabled()) else LocalStore(root)


# --------------------------------------------------------------------------- dataset records


def gold_call_record(gold: GoldCall, *, transcript_kind: str = "asr") -> DatasetRecord:
    """A ``gold-calls`` row: one per call and transcript kind (the gold suite runs on human and ASR transcripts)."""
    dataset = SYNTHETIC_DATASET if gold.source == "synthetic" else GOLD_CALLS
    return DatasetRecord(
        id=f"{gold.call_id}:{transcript_kind}",
        input={
            "call_id": gold.call_id,
            "client_id": gold.client_id,
            "anchor_date": gold.anchor_date.isoformat(),
            "transcript_kind": transcript_kind,
            "prior_document": gold.prior_document.model_dump(mode="json") if gold.prior_document else None,
        },
        expected=gold.model_dump(mode="json"),
        metadata={
            "dataset": dataset,
            "source": gold.source,
            "split": gold.split,
            "transcript_kind": transcript_kind,
            "tags": list(gold.tags),
        },
        tags=sorted({*gold.tags, f"split:{gold.split}", f"transcript:{transcript_kind}"}),
    )


def transcript_record(transcript: Transcript, *, kind: str = "human") -> DatasetRecord:
    """A ``gold-transcripts`` row: the professional verbatim transcript of a gold call."""
    return DatasetRecord(
        id=f"{transcript.call_id}:{kind}",
        input={"call_id": transcript.call_id, "transcript_kind": kind},
        expected=transcript.model_dump(mode="json"),
        metadata={"dataset": GOLD_TRANSCRIPTS, "asr_provider": transcript.asr_provider, "segments": len(transcript.segments)},
        tags=[f"transcript:{kind}"],
    )


def synthetic_record(call: SyntheticCall) -> DatasetRecord:
    """A ``synthetic-calls`` row: the script (Stage 1 input) and the gold call, tagged ``synthetic``."""
    rec = gold_call_record(call.gold, transcript_kind="script")
    rec.input["script"] = [list(t) for t in call.script]
    rec.metadata.update(
        {"archetype": call.archetype, "phenomena": call.phenomena, "writer": call.writer, "seed": call.seed, "index": call.index}
    )
    rec.tags = sorted({*rec.tags, SYNTHETIC_TAG, call.archetype})
    return rec


def sync_gold(
    golds: Iterable[GoldCall], *, store: DatasetStore | None = None, transcript_kinds: Sequence[str] = ("human", "asr")
) -> list[str]:
    store = store or get_store()
    return store.insert(GOLD_CALLS, [gold_call_record(g, transcript_kind=k) for g in golds for k in transcript_kinds])


def sync_transcripts(transcripts: Iterable[Transcript], *, store: DatasetStore | None = None, kind: str = "human") -> list[str]:
    """Gold-call transcripts to ``gold-transcripts``: the professional ``human`` transcripts, or ``asr`` ones produced
    by the pinned ASR for the gold suite's ASR run."""
    store = store or get_store()
    return store.insert(GOLD_TRANSCRIPTS, [transcript_record(t, kind=kind) for t in transcripts])


def sync_synthetic(calls: Iterable[SyntheticCall], *, store: DatasetStore | None = None) -> list[str]:
    store = store or get_store()
    return store.insert(SYNTHETIC_DATASET, [synthetic_record(c) for c in calls])


def fetch_for_calibration(store: DatasetStore, dataset: str) -> list[DatasetRecord]:
    """Rows that may fit the calibrator or the conformal threshold: never synthetic ones (§15.1)."""
    if dataset == SYNTHETIC_DATASET:
        raise ValueError("synthetic-calls cannot be used for calibration or conformal fitting (§15.1)")
    rows = store.fetch(dataset)
    assert_not_synthetic(rows, purpose=f"calibration ({dataset})")
    return rows


# --------------------------------------------------------------------------- review events (§11.2, §11.4)


ReviewAction = Literal["accept", "edit", "change_status", "reject", "reattribute", "remove", "add", "mark_unknown"]


class ReviewEvent(BaseModel):
    """One reviewer action, as the ``review_events`` table and the nightly syncs see it."""

    call_id: str
    run_id: str = ""
    fact_id: str
    action: ReviewAction
    reason: str | None = None
    note: str | None = None
    reviewer_id: str = ""
    at: datetime | None = None
    seq: int = 0
    payload: dict[str, Any] = {}


def _action(op: StateOp) -> ReviewAction:
    p = op.payload
    if op.op == "CONFIRM":
        return "accept"
    if op.op == "ADD":
        return "add"
    if op.op == "MARK_UNKNOWN":
        return "mark_unknown"
    if op.op == "RETRACT":
        reason = p.get("reason")
        return "reattribute" if reason == "wrong_entity" else ("remove" if reason == "removed" else "reject")
    return "change_status" if p.get("status") else "edit"


def events_from_review(result: ReviewResult) -> list[ReviewEvent]:
    """Reviewer ops → review events, following the action table in ``models/review.py``."""
    out = []
    for op in result.ops:
        if op.actor != "reviewer":
            continue
        review = op.payload.get("review") or {}
        at = review.get("reviewed_at") or (result.submitted_at.isoformat() if result.submitted_at else None)
        out.append(
            ReviewEvent(
                call_id=result.call_id,
                run_id=result.run_id,
                fact_id=op.fact_id,
                action=_action(op),
                reason=op.payload.get("reason") or review.get("reason"),
                note=op.payload.get("note") or review.get("note"),
                reviewer_id=review.get("reviewer_id") or result.reviewer_id,
                at=at,
                seq=op.seq,
                payload=op.payload,
            )
        )
    return out


def _slot_of(fact_id: str, catalog: Catalog) -> Any:
    try:
        return catalog.slot(fact_id.split("#", 1)[0])
    except KeyError:
        return None


def _value_text(fact: Any) -> str:
    from ..binding import describe_value

    return describe_value(fact.value) if fact is not None and fact.value is not None else ""


def _row_value_text(value: Any) -> str:
    """A value from a nightly error row (a dumped ``Quantity``) as review text."""
    from ..binding import describe_value
    from ..models.dists import Quantity

    if value in (None, "", {}):
        return ""
    try:
        return describe_value(Quantity.model_validate(value))
    except ValueError:
        return str(value)


def _error_event(item: ReviewEvent | Mapping[str, Any], profiles: Mapping[str, Profile]) -> dict[str, Any]:
    """A review event — our ``ReviewEvent``, or a row the nightly activity built from ``review_events`` (run id,
    fact id, slot id, action, reason, quotes, value, interpretation, note, reviewer, time) — as one flat record,
    with the run's profile filling in what the event does not carry."""
    if isinstance(item, Mapping) and "call_id" in item and "quotes" not in item:
        item = ReviewEvent.model_validate(item)
    if isinstance(item, ReviewEvent):
        e: dict[str, Any] = {
            "call_id": item.call_id,
            "run_id": item.run_id,
            "fact_id": item.fact_id,
            "action": item.action,
            "reason": item.reason,
            "note": item.note,
            "reviewer_id": item.reviewer_id,
            "at": item.at.isoformat() if item.at else None,
            "seq": item.seq,
            "payload": item.payload,
            "slot_id": None,
            "quotes": [],
            "said": "",
            "interpretation": "",
            "extracted": "",
            "status": "",
            "original_status": None,
            "attribution": "",
            "features": {},
        }
    else:
        at = item.get("at")
        e = {
            "call_id": item.get("call_id") or "",
            "run_id": item.get("run_id") or "",
            "fact_id": item.get("fact_id") or "",
            "action": item.get("action"),
            "reason": item.get("reason"),
            "note": item.get("note"),
            "reviewer_id": item.get("reviewer_id") or "",
            "at": at.isoformat() if isinstance(at, datetime) else at,
            "seq": int(item.get("seq") or 0),
            "payload": {},
            "slot_id": item.get("slot_id"),
            "quotes": [q for q in item.get("quotes") or [] if q],
            "said": item.get("said") or "",
            "interpretation": item.get("interpretation") or "",
            "extracted": _row_value_text(item.get("value")),
            "status": item.get("status") or "",
            "original_status": item.get("original_status"),
            "attribution": item.get("attribution") or "",
            "features": dict(item.get("features") or {}),
        }
    profile = profiles.get(e["call_id"]) or profiles.get(e["run_id"])
    fact = profile.facts.get(e["fact_id"]) if profile else None
    if fact is not None:
        e["quotes"] = e["quotes"] or [ev.quote for ev in fact.evidence]
        for key, val in (
            ("said", fact.said),
            ("interpretation", fact.interpretation),
            ("extracted", _value_text(fact)),
            ("status", fact.status),
            ("attribution", fact.speaker_attribution),
        ):
            e[key] = e[key] or val or ""
        e["original_status"] = e["original_status"] or fact.review.original_status or fact.status
        e["features"] = e["features"] or dict(fact.features)
    e["quotes"] = e["quotes"] or [ev.get("quote", "") for ev in e["payload"].get("evidence", []) if isinstance(ev, dict)]
    return e


def error_record(
    item: ReviewEvent | Mapping[str, Any],
    *,
    profiles: Mapping[str, Profile] | None = None,
    pins: VersionPins | None = None,
    catalog: Catalog | None = None,
) -> DatasetRecord | None:
    """One ``production-errors`` row for a reason-coded rejection or a missed slot, else None (§11.4 step 3).

    A rejection is a reject or re-attribute action; a missed slot is a reviewer-added fact or a status change away
    from not_discussed. Rows are tagged by slot, reason, slot group and tier.
    """
    catalog = catalog or load_catalog()
    e = _error_event(item, profiles or {})
    action = e["action"]
    if action in ("reject", "reattribute"):
        kind, reason = "rejected", e["reason"] or "other"
    elif action == "add" or (action == "change_status" and (e["original_status"] or "not_discussed") == "not_discussed"):
        kind, reason = "missed", "missed_slot"
    else:
        return None
    slot = _slot_of(e["fact_id"], catalog) or (_slot_of(e["slot_id"], catalog) if e["slot_id"] else None)
    group = slot.group if slot else "unknown"
    tier = slot.sensitivity_tier if slot else "C"
    slot_id = slot.slot_id if slot else (e["slot_id"] or e["fact_id"].split("#", 1)[0])
    return DatasetRecord(
        id=stable_hash([e["call_id"] or e["run_id"], e["fact_id"], action, e["seq"], e["at"], reason, e["quotes"], e["note"]]),
        input={
            "call_id": e["call_id"],
            "run_id": e["run_id"],
            "fact_id": e["fact_id"],
            "slot_id": slot_id,
            "quotes": e["quotes"],
            "said": e["said"],
            "interpretation": e["interpretation"],
            "extracted": e["extracted"] if kind == "rejected" else "",
            "status": e["status"] or "not_discussed",
            "attribution": e["attribution"],
        },
        expected={"action": action, "reason": reason, "note": e["note"] or "", "correction": e["payload"] if kind == "missed" else {}},
        metadata={
            "dataset": PRODUCTION_ERRORS,
            "kind": kind,
            "reason": reason,
            "slot_id": slot_id,
            "group": group,
            "tier": tier,
            "reviewer_id": e["reviewer_id"],
            "at": e["at"],
            "features": e["features"],
            "versions": pins.model_dump(mode="json") if pins else {},
        },
        tags=[f"slot:{slot_id}", f"reason:{reason}", f"group:{group}", f"tier:{tier}", kind],
    )


def sync_error_dataset(
    review_events: Iterable[ReviewEvent | Mapping[str, Any]],
    *,
    settings: Any = None,
    profiles: Mapping[str, Profile] | None = None,
    pins: VersionPins | None = None,
    store: DatasetStore | None = None,
    catalog: Catalog | None = None,
) -> int:
    """Insert reason-coded rejections and missed slots into ``production-errors``; returns the rows written (§11.4).

    Accepts ``ReviewEvent`` models or the nightly activity's error rows. Inserts upsert by a hash of the event, so
    the nightly run can repeat. ``profiles`` (call or run id → the run's profile) supplies evidence quotes and the
    extracted value when the events do not carry them.
    """
    catalog = catalog or load_catalog()
    rows = [r for r in (error_record(e, profiles=profiles, pins=pins, catalog=catalog) for e in review_events) if r is not None]
    if not rows:
        return 0
    return len((store or get_store(settings=settings)).insert(PRODUCTION_ERRORS, rows))


def _candidate_from_result(r: ReviewResult, profile: Profile | None) -> DatasetRecord | None:
    from ..binding import summarise_value

    corrections = [e for e in events_from_review(r) if e.action != "accept"]
    if not corrections or profile is None:
        return None
    reviewed = {
        fid: {
            "status": f.status,
            "value": summarise_value(f.value),
            "review": f.review.status,
            "attribution": f.speaker_attribution,
            "evidence": [ev.quote for ev in f.evidence],
        }
        for fid, f in sorted(profile.facts.items())
        if f.source not in ("prior_fna", "fna_default") or f.review.status != "pending"
    }
    counts: dict[str, int] = {}
    for e in corrections:
        counts[e.action] = counts.get(e.action, 0) + 1
    return DatasetRecord(
        id=f"{r.call_id}:{r.run_id}",
        input={"call_id": r.call_id, "run_id": r.run_id, "profile_version": r.base_profile_version},
        expected={"facts": reviewed, "corrections": [e.model_dump(mode="json") for e in corrections]},
        metadata={
            "dataset": GOLD_CANDIDATES,
            "reviewer_id": r.reviewer_id,
            "corrections": counts,
            "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
        },
        tags=["gold-candidate", *sorted(f"correction:{k}" for k in counts)],
    )


def _candidate_from_row(row: Mapping[str, Any]) -> DatasetRecord | None:
    """A gold candidate from the nightly activity's row (run, call, versions, error rows, reviewed-profile location)."""
    corrections = [dict(c) for c in row.get("corrections") or []]
    if not corrections:
        return None
    counts: dict[str, int] = {}
    for c in corrections:
        if c.get("action"):
            counts[str(c["action"])] = counts.get(str(c["action"]), 0) + 1
    run_id, call_id = str(row.get("run_id") or ""), str(row.get("call_id") or "")
    return DatasetRecord(
        id=f"{call_id or run_id}:{run_id}",
        input={"call_id": call_id, "run_id": run_id, "versions": dict(row.get("versions") or {})},
        expected={"corrections": corrections, "reviewed_profile": row.get("reviewed_profile")},
        metadata={
            "dataset": GOLD_CANDIDATES,
            "corrections": counts,
            "reviewer_id": next((c.get("reviewer_id") for c in corrections), None),
        },
        tags=["gold-candidate", *sorted(f"correction:{k}" for k in counts)],
    )


def sync_gold_candidates(
    results: Iterable[ReviewResult | Mapping[str, Any]],
    *,
    settings: Any = None,
    profiles: Mapping[str, Profile] | None = None,
    store: DatasetStore | None = None,
) -> int:
    """Reviewer-corrected calls → ``gold-candidates``; edited follow-up questions → ``followup-edits`` (§11.4).

    Accepts ``ReviewResult``s (with ``profiles``: call id → the reviewed profile) or the nightly activity's rows
    (``run_id``, ``call_id``, ``versions``, ``corrections``, ``followup_edits``, ``reviewed_profile``). Returns the
    rows written across both datasets.
    """
    profiles = profiles or {}
    candidates, edits = [], []
    for r in results:
        if isinstance(r, ReviewResult):
            profile = profiles.get(r.call_id)
            cand = _candidate_from_result(r, profile)
            questions = {q.followup_id: q.question for q in (profile.open_questions if profile else [])}
            call_id, followup_edits = r.call_id, list(r.followup_edits)
        else:
            cand = _candidate_from_row(r)
            questions, call_id = {}, str(r.get("call_id") or r.get("run_id") or "")
            followup_edits = _followup_edits(call_id, r.get("followup_edits") or [])
        if cand is not None:
            candidates.append(cand)
        edits.extend(_followup_record(call_id, fe, questions) for fe in followup_edits)
    if not candidates and not edits:
        return 0
    store = store or get_store(settings=settings)
    n = len(store.insert(GOLD_CANDIDATES, candidates)) if candidates else 0
    return n + (len(store.insert(FOLLOWUP_EDITS, edits)) if edits else 0)


def _followup_edits(call_id: str, raw: Iterable[Any]) -> list[FollowupEdit]:
    """The nightly row's follow-up edits (``FollowupEdit`` dumps); one that does not parse is logged and skipped, so
    a malformed review event cannot stop the night's sync."""
    out = []
    for fe in raw:
        if not fe:
            continue
        try:
            out.append(fe if isinstance(fe, FollowupEdit) else FollowupEdit.model_validate(fe))
        except ValidationError as e:
            log.warning("call %s: skipping a follow-up edit that does not parse: %s", call_id, e.errors()[:2])
    return out


def _followup_record(call_id: str, fe: FollowupEdit, questions: Mapping[str, str]) -> DatasetRecord:
    return DatasetRecord(
        id=stable_hash([call_id, fe.followup_id, fe.action, fe.at.isoformat()]),
        input={"call_id": call_id, "followup_id": fe.followup_id, "original": questions.get(fe.followup_id or "", "")},
        expected={"action": fe.action, "question": fe.question},
        metadata={"dataset": FOLLOWUP_EDITS, "reviewer_id": fe.reviewer_id, "at": fe.at.isoformat()},
        tags=[f"followup:{fe.action}"],
    )


# --------------------------------------------------------------------------- the Stage 1–2 cache (§15.1)


# Prompt components Stage 1 and Stage 2 run on, by name prefix ("discourse_entities", "discourse/rewrite", …).
STAGE12_PROMPT_PREFIXES = ("ingest", "role_assignment", "discourse", "entities", "coreference", "rewrite", "revisions", "topic_index")


def stage12_key(pins: VersionPins, transcript_kind: str = "asr") -> str:
    """A cache key from the pins Stage 1 and Stage 2 depend on, and nothing else (extraction changes keep the key)."""
    relevant = {
        "kind": transcript_kind,
        "asr": [pins.asr_provider, pins.asr_model_version] if transcript_kind == "asr" else [],
        "diarization": pins.diarization_model if transcript_kind == "asr" else "",
        "grammar": pins.grammar_version,
        "frontier": pins.frontier_model,
        "small": pins.small_model,
        "embedding": pins.embedding_model,
        "lexicon": pins.lexicon_version,
        "prompts": {k: v for k, v in sorted(pins.prompt_versions.items()) if k.startswith(STAGE12_PROMPT_PREFIXES)},
    }
    return stable_hash(relevant)


class BlobStore(Protocol):
    def read(self, key: str) -> bytes | None: ...

    def write(self, key: str, data: bytes) -> None: ...


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, key: str) -> bytes | None:
        p = self.root / key
        return p.read_bytes() if p.exists() else None

    def write(self, key: str, data: bytes) -> None:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)


class S3BlobStore:
    """Object storage through a boto3-style client (``get_object`` / ``put_object``)."""

    def __init__(self, bucket: str, prefix: str = "", *, client: Any = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("s3")
        self.client, self.bucket, self.prefix = client, bucket, prefix.strip("/")

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def read(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()
        except Exception as e:  # noqa: BLE001 - boto3 raises NoSuchKey through a dynamic exception class
            if type(e).__name__ in ("NoSuchKey", "ClientError") or "NoSuchKey" in str(e) or "404" in str(e):
                return None
            raise

    def write(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data)


def default_blob_store() -> BlobStore:
    uri = get_settings().object_store_uri
    if uri.startswith("s3://"):
        bucket, _, prefix = uri.removeprefix("s3://").partition("/")
        return S3BlobStore(bucket, prefix)
    return LocalBlobStore(data_dir() / "cache")


class CachedCall(BaseModel):
    """What ``process_cached_call`` receives: one dataset call with its cached Stage 1–2 output."""

    call_id: str
    client_id: str = ""
    anchor_date: date
    transcript_kind: str = "asr"
    transcript: Transcript
    discourse: DiscourseOutput | None = None
    prior_document: FnaDocument | None = None
    crm: CrmRecord | None = None
    stage12_key: str = ""
    metadata: dict[str, Any] = {}

    @property
    def call_minutes(self) -> float:
        return self.transcript.duration / 60.0


Stage12Builder = Callable[[DatasetRecord, VersionPins], tuple[Transcript, DiscourseOutput | None]]


class Stage12Cache:
    """Stage 1 (transcript) and Stage 2 (discourse) outputs per dataset call, keyed by ``stage12_key``."""

    def __init__(self, blobs: BlobStore | None = None) -> None:
        self.blobs = blobs or default_blob_store()

    @staticmethod
    def _keys(dataset: str, call_id: str, key: str) -> tuple[str, str]:
        base = f"stage12/{dataset}/{call_id}/{key}"
        return f"{base}/transcript.json", f"{base}/discourse.json"

    def get(self, dataset: str, call_id: str, key: str) -> tuple[Transcript, DiscourseOutput | None] | None:
        tk, dk = self._keys(dataset, call_id, key)
        t = self.blobs.read(tk)
        if t is None:
            return None
        d = self.blobs.read(dk)
        return Transcript.model_validate_json(t), (DiscourseOutput.model_validate_json(d) if d else None)

    def put(self, dataset: str, call_id: str, key: str, transcript: Transcript, discourse: DiscourseOutput | None) -> None:
        tk, dk = self._keys(dataset, call_id, key)
        self.blobs.write(tk, transcript.model_dump_json().encode())
        if discourse is not None:
            self.blobs.write(dk, discourse.model_dump_json().encode())

    def load_call(self, record: DatasetRecord, pins: VersionPins, *, build: Stage12Builder | None = None) -> CachedCall:
        """The dataset call with its Stage 1–2 output: from the cache, else built (and cached), else from the record."""
        inp = dict(record.input or {})
        call_id = inp["call_id"]
        kind = inp.get("transcript_kind", "asr")
        dataset = record.metadata.get("dataset", GOLD_CALLS)
        key = stage12_key(pins, kind)
        hit = self.get(dataset, call_id, key)
        if hit is None:
            if build is not None:
                hit = build(record, pins)
            elif inp.get("transcript"):
                hit = (Transcript.model_validate(inp["transcript"]), None)
            elif inp.get("script"):
                hit = (script_transcript([tuple(t) for t in inp["script"]], date.fromisoformat(inp["anchor_date"]), call_id), None)  # type: ignore[misc]
            else:
                raise KeyError(f"no cached Stage 1–2 output for {call_id} ({kind}, key {key}) and no builder")
            self.put(dataset, call_id, key, *hit)
        transcript, discourse = hit
        prior = inp.get("prior_document")
        crm = inp.get("crm")
        return CachedCall(
            call_id=call_id,
            client_id=inp.get("client_id", ""),
            anchor_date=date.fromisoformat(inp.get("anchor_date") or transcript.anchor_date.isoformat()),
            transcript_kind=kind,
            transcript=transcript,
            discourse=discourse,
            prior_document=FnaDocument.model_validate(prior) if prior else None,
            crm=CrmRecord.model_validate(crm) if crm else None,
            stage12_key=key,
            metadata=dict(record.metadata),
        )


class DatasetStage12Builder:
    """Stage 1–2 for a dataset call the cache does not hold yet (§15.1): the transcript embedded in the row, built
    from a synthetic call's script (stage 1's back half), or the call's transcript of that kind from
    ``gold-transcripts``; then stage 2 (``advisor_intel.discourse.run.run_discourse``) seeded with the client's prior
    file, so the cache holds both and an experiment that changes only extraction starts from it."""

    def __init__(self, *, store: DatasetStore | None = None, discourse: bool = True) -> None:
        self.store = store
        self.discourse = discourse
        self._transcripts: dict[str, DatasetRecord] | None = None

    def transcript(self, record: DatasetRecord) -> Transcript:
        inp = record.input or {}
        call_id, kind = inp["call_id"], inp.get("transcript_kind", "asr")
        if inp.get("transcript"):
            return Transcript.model_validate(inp["transcript"])
        if inp.get("script"):
            return script_transcript([tuple(t) for t in inp["script"]], date.fromisoformat(inp["anchor_date"]), call_id)  # type: ignore[misc]
        if self._transcripts is None:
            self._transcripts = {r.id: r for r in (self.store or get_store()).fetch(GOLD_TRANSCRIPTS)}
        row = self._transcripts.get(f"{call_id}:{kind}")
        if row is None:
            raise KeyError(f"no {kind} transcript for {call_id} in {GOLD_TRANSCRIPTS}; sync it with sync_transcripts(kind={kind!r})")
        return Transcript.model_validate(row.expected)

    def stage2(self, transcript: Transcript, record: DatasetRecord, pins: VersionPins) -> DiscourseOutput | None:
        """Stage 2 as the pipeline runs it: the discourse stage on the transcript, seeded with the prior file."""
        from ..binding import load_prior
        from ..models.facts import Provenance
        from ._runtime import lazy_attr, run_sync

        run = lazy_attr("advisor_intel.discourse.run", "run_discourse")
        if run is None or not self.discourse:
            return None
        inp = record.input or {}
        catalog = load_catalog(pins.schema_version)
        doc = FnaDocument.model_validate(inp["prior_document"]) if inp.get("prior_document") else None
        crm = CrmRecord.model_validate(inp["crm"]) if inp.get("crm") else None
        run_id = f"stage12-{transcript.call_id}-{stage12_key(pins, inp.get('transcript_kind', 'asr'))}"
        prov = Provenance(run_id=run_id, schema_version=catalog.version, lexicon_version=pins.lexicon_version)
        prior = load_prior(
            doc, crm, call_id=transcript.call_id, run_id=run_id, anchor_date=transcript.anchor_date, catalog=catalog, provenance=prov
        )
        return run_sync(run(transcript, prior=prior, crm=crm, pins=pins, catalog=catalog))

    def __call__(self, record: DatasetRecord, pins: VersionPins) -> tuple[Transcript, DiscourseOutput | None]:
        t = self.transcript(record)
        return t, self.stage2(t, record, pins)


def stage12_builder(*, store: DatasetStore | None = None, discourse: bool = True) -> Stage12Builder:
    """The Stage 1–2 builder experiments use (``DatasetStage12Builder``)."""
    return DatasetStage12Builder(store=store, discourse=discourse)


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    """``python -m advisor_intel.evals.datasets``: sync synthetic calls, gold calls and transcripts (§15.1)."""
    import argparse

    from .gold import adjudicate, load_annotation

    ap = argparse.ArgumentParser(description="Sync the evaluation datasets (Braintrust with BRAINTRUST_API_KEY, else local JSONL).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    syn = sub.add_parser("sync-synthetic", help="generate synthetic calls and sync them to synthetic-calls")
    syn.add_argument("--n", type=int, default=300)
    syn.add_argument("--seed", type=int, default=0)
    syn.add_argument("--start", type=int, default=0)
    gold = sub.add_parser("sync-gold", help="adjudicate two annotators' files per call and sync the gold call")
    gold.add_argument("pairs", nargs="+", help="each call's annotation files as A.json,B.json[,ADJUDICATOR.json] (the app's annotate mode)")
    gold.add_argument("--heldout", action="store_true", help="the held-out test calls GEPA and fine-tuning never see")
    gold.add_argument(
        "--prior",
        type=Path,
        action="append",
        default=[],
        help="a client's prior FnaDocument JSON (the app's export keeps only its id); repeat per call, matched by id",
    )
    tr = sub.add_parser("sync-transcripts", help="sync Transcript JSON files to gold-transcripts")
    tr.add_argument("files", nargs="+", type=Path)
    tr.add_argument("--kind", choices=["human", "asr"], default="human")
    args = ap.parse_args(argv)
    if args.cmd == "sync-synthetic":
        from .synthetic import generate_calls

        ids = sync_synthetic(generate_calls(args.n, seed=args.seed, start=args.start))
        print(f"{len(ids)} synthetic calls synced to {SYNTHETIC_DATASET}")
    elif args.cmd == "sync-gold":
        priors = {d.id: d for d in (FnaDocument.model_validate_json(f.read_text()) for f in args.prior)}
        golds = []
        for pair in args.pairs:
            raws = [json.loads(Path(f).read_text()) for f in pair.split(",") if f]
            files = [load_annotation(r, prior_document=priors.get(r.get("prior_document_id") or "")) for r in raws]
            if len(files) < 2:
                ap.error(f"{pair}: two annotators' files are needed")
            golds.append(
                adjudicate(files[0], files[1], adjudicator=files[2] if len(files) > 2 else None, split="heldout" if args.heldout else "dev")
            )
        ids = sync_gold(golds)
        print(f"{len(golds)} gold calls synced to {GOLD_CALLS} ({len(ids)} rows)")
    else:
        ids = sync_transcripts([Transcript.model_validate_json(f.read_text()) for f in args.files], kind=args.kind)
        print(f"{len(ids)} {args.kind} transcripts synced to {GOLD_TRANSCRIPTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
