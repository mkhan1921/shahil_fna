"""What one pipeline run looks like to the scorers (spec §15.3–15.4).

An experiment's task returns a ``SystemCallOutput``: a JSON-friendly digest of the in-process pipeline's result
(``advisor_intel.pipeline.process_cached_call(call, pins)`` → an object with ``.profile``, ``.forecast``,
``.followups``, ``.flags`` and ``.note``). It keeps what the §15.3 metrics need — each fact's status, p10 / p50 /
p90 or label, evidence spans, attribution, ``p_correct`` and auto-accept flag; the entities; blocking flags; the
meeting note; PoS; the median FnaDocument; usage — so Braintrust logs stay small and scorers stay pure.

``summarise_result`` accepts any object of that shape (Pydantic model, dataclass or dict) so the eval code never
depends on the runner's class.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel

from .. import stats
from ..models.dists import Quantity
from ..models.facts import Attribution, FactStatus
from ..models.fna import FnaDocument
from ..models.transcript import Transcript
from ..schema.slots import Catalog, SlotDef, load_catalog
from ._runtime import get
from .gold import GoldCall, GoldMention, Span

CALL_SOURCES = frozenset({"call", "derived", "followup"})  # facts the pipeline extracted (not prior seeds or defaults)
FOUND_STATUSES = frozenset({"stated", "implied", "contradicted"})
# Intermediate forms (§8.1) whose distribution comes from vague words; they count for interval coverage (§15.3).
VAGUE_FORMS = frozenset({"AMOUNT_APPROX", "AMOUNT_RANGE", "AMOUNT_BOUND", "AMOUNT_VAGUE", "AMOUNT_RELATIVE", "PROBABILITY_EVENT"})
VAGUE_SUBFORMS = frozenset({"vague", "range"})  # AGE, DURATION_FROM_ANCHOR and RATE with these forms are vague too
RANGE_FORMS = frozenset({"AMOUNT_RANGE"})


class ValueSummary(BaseModel):
    """A fact's value, reduced to what the scorers compare."""

    kind: Literal["numeric", "categorical", "boolean", "probability", "text"]
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None
    label: str | None = None  # categorical mode or free text
    probs: dict[str, float] = {}  # categorical distribution
    p_true: float | None = None  # boolean and probability slots


class SystemFact(BaseModel):
    fact_id: str
    slot_id: str
    instance_key: str | None = None
    fna_path: str = ""
    group: str = ""
    tier: str = "C"
    status: FactStatus
    source: str = "call"
    attribution: Attribution = "client"
    value: ValueSummary | None = None
    spans: list[Span] = []  # supporting and revising evidence
    p_correct: float | None = None
    p_raw: float | None = None
    auto_accepted: bool = False
    self_score: float | None = None
    features: dict[str, float | None] = {}
    hedges: list[str] = []
    form_type: str | None = None  # the intermediate form (§8.1), "AGE:vague" style for sub-forms
    review_status: str = "pending"
    entity_name: str = ""  # for feedback text: "dependants[Thandi].tertiaryStartAge"

    @property
    def found(self) -> bool:
        return self.status in FOUND_STATUSES and self.value is not None

    @property
    def extracted(self) -> bool:
        return self.source in CALL_SOURCES

    @property
    def vague(self) -> bool:
        if self.form_type:
            base, _, sub = self.form_type.partition(":")
            return base in VAGUE_FORMS or sub in VAGUE_SUBFORMS
        return bool(self.hedges)

    @property
    def range_like(self) -> bool:
        return bool(self.form_type) and (self.form_type in RANGE_FORMS or self.form_type.endswith(":range"))  # type: ignore[union-attr]


class SystemEntity(BaseModel):
    entity_id: str
    type: str = "person"
    name: str = ""
    aliases: list[str] = []
    fna_list: str | None = None
    fna_id: str | None = None
    relation: str = ""


class SystemFlag(BaseModel):
    kind: str
    fact_id: str | None = None
    cleared: bool = False


class NoteLine(BaseModel):
    text: str
    fact_ids: list[str] = []


class SystemCallOutput(BaseModel):
    call_id: str
    transcript_kind: str = "asr"  # which transcript the run used: "asr", "human" or "script"
    facts: list[SystemFact] = []
    entities: list[SystemEntity] = []
    flags: list[SystemFlag] = []
    note: list[NoteLine] = []
    followups: list[str] = []
    pos: float | None = None
    forecast_seed: int | None = None  # the preview forecast's seed: the gold profile is forecast on the same draws
    document: dict[str, Any] | None = None  # the median-materialised FnaDocument (§3.6)
    mentions: list[GoldMention] = []
    topic_index: dict[str, list[str]] = {}  # slot group → segment ids (§6.5)
    usage: dict[str, float] = {}  # cost_usd, input_tokens, output_tokens, requests
    call_minutes: float | None = None
    transcript_metrics: dict[str, float | None] = {}  # numeric WER, DER, role accuracy against a reference
    tool_calls: list[str] = []
    partial: bool = False
    error: str | None = None

    def fact_index(self) -> dict[str, SystemFact]:
        return {f.fact_id: f for f in self.facts}


class EvalRecord(BaseModel):
    """One scored item: the system's output, the gold call and the item's metadata (transcript kind, dataset…)."""

    system: SystemCallOutput
    gold: GoldCall
    metadata: dict[str, Any] = {}

    @property
    def transcript_kind(self) -> str:
        return str(self.metadata.get("transcript_kind") or self.system.transcript_kind)


# --------------------------------------------------------------------------- values


def summarise_quantity(q: Quantity | None, slot: SlotDef) -> ValueSummary | None:
    """p10 / p50 / p90 for numbers and dates, the mode for categories, P(true) for booleans and events."""
    if q is None:
        return None
    d = q.dist
    try:
        if slot.value_type == "probability":
            p = stats.p_true(d) if stats.is_boolean(d) else stats.mean(d)
            return ValueSummary(kind="probability", p_true=float(p))
        if slot.value_type == "boolean" or stats.is_boolean(d):
            return ValueSummary(kind="boolean", p_true=float(stats.p_true(d)))
        if slot.value_type in ("category", "count") or stats.is_categorical(d):
            probs = {str(k): float(v) for k, v in stats.categorical_probs(d).items()}
            return ValueSummary(kind="categorical", label=stats.mode_label(d), probs=probs)
        if slot.value_type == "text":
            return ValueSummary(kind="text", label=str(getattr(d, "value", "")))
        s = stats.summary(d)
        return ValueSummary(kind="numeric", p10=s["p10"], p50=s["p50"], p90=s["p90"])
    except (TypeError, ValueError):
        return None


def _form_type(normalization: dict[str, Any] | None) -> str | None:
    """The intermediate form type recorded by the normaliser (§8), e.g. 'AMOUNT_RANGE' or 'AGE:vague'."""
    if not normalization:
        return None
    form = normalization.get("form") or normalization.get("intermediate") or normalization
    if not isinstance(form, dict):
        return None
    t = form.get("type")
    if not isinstance(t, str):
        return None
    sub = form.get("form")
    return f"{t}:{sub}" if isinstance(sub, str) and sub else t


def _entity_label(profile: Any, f: Any, slot: SlotDef) -> str:
    """'retirement.client.retirementAge', 'dependants[Thandi].tertiaryStartAge', 'expenses.items[groceries]'."""
    inst = get(f, "instance_key")
    if slot.per_person and inst:
        return slot.expand(inst)
    if not inst:
        return slot.slot_id
    ents = get(profile, "entities", {}) or {}
    e = ents.get(inst) if isinstance(ents, dict) else None
    name = get(e, "canonical_name") or inst
    return slot.slot_id.replace("[]", f"[{name}]", 1)


def system_fact(f: Any, profile: Any, catalog: Catalog, transcript_kind: str) -> SystemFact | None:
    """One profile ``Fact`` as a ``SystemFact``; None for a fact whose slot the catalog does not know."""
    try:
        slot = catalog.slot(get(f, "slot_id"))
    except KeyError:
        return None
    review = get(f, "review")
    spans = []
    for e in get(f, "evidence", []) or []:
        if get(e, "role", "supports") not in ("supports", "revises"):
            continue
        spans.append(
            Span(
                segment_id=get(e, "segment_id"),
                char_start=int(get(e, "char_start", 0)),
                char_end=int(get(e, "char_end", 0)),
                quote=get(e, "quote", "") or "",
                t_start=get(e, "t_start"),
                t_end=get(e, "t_end"),
                transcript=transcript_kind,
            )
        )
    value = get(f, "value")
    if isinstance(value, dict):
        value = Quantity.model_validate(value)
    raw_features = get(f, "features", {}) or {}
    features = {k: (None if v is None else float(v)) for k, v in raw_features.items() if isinstance(v, (int, float)) or v is None}
    return SystemFact(
        fact_id=get(f, "fact_id"),
        slot_id=slot.slot_id,
        instance_key=get(f, "instance_key"),
        fna_path=get(f, "fna_path", "") or "",
        group=slot.group,
        tier=slot.sensitivity_tier,
        status=get(f, "status"),
        source=get(f, "source", "call") or "call",
        attribution=get(f, "speaker_attribution", "client") or "client",
        value=summarise_quantity(value, slot),
        spans=spans,
        p_correct=get(f, "p_correct"),
        p_raw=get(f, "p_raw"),
        auto_accepted=bool(get(f, "auto_accepted", False)),
        self_score=get(f, "self_score"),
        features=features,
        hedges=[get(h, "term", "") for h in get(f, "hedges", []) or []],
        form_type=_form_type(get(f, "normalization")),
        review_status=get(review, "status", "pending") or "pending",
        entity_name=_entity_label(profile, f, slot),
    )


def _note_lines(note: Any) -> list[NoteLine]:
    if note is None:
        return []
    sentences = get(note, "sentences", None) if not isinstance(note, (list, tuple, str)) else note
    if isinstance(sentences, str):
        return [NoteLine(text=s.strip()) for s in re.split(r"(?<=[.!?])\s+", sentences) if s.strip()]
    out = []
    for s in sentences or []:
        if isinstance(s, str):
            out.append(NoteLine(text=s))
        else:
            out.append(NoteLine(text=get(s, "text", "") or "", fact_ids=list(get(s, "fact_ids", []) or [])))
    return out


def _usage(result: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    usage = get(result, "usage")
    if usage is not None:
        items = usage.items() if isinstance(usage, dict) else ((k, getattr(usage, k)) for k in dir(usage) if not k.startswith("_"))
        for k, v in items:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = float(v)
    cost = get(result, "cost_usd")
    if isinstance(cost, (int, float)):
        out["cost_usd"] = float(cost)
    if "cost_usd" not in out:
        for alt in ("usd", "cost", "total_cost_usd"):
            if alt in out:
                out["cost_usd"] = out[alt]
                break
    return out


def _int_or_none(x: Any) -> int | None:
    return int(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _tool_calls(result: Any) -> list[str]:
    calls = get(result, "tool_calls") or get(get(result, "trace"), "tool_calls") or []
    names = []
    for c in calls:
        names.append(c if isinstance(c, str) else str(get(c, "name") or get(c, "tool_name") or ""))
    return [n for n in names if n]


def summarise_result(
    result: Any,
    *,
    call: Any = None,
    transcript_kind: str = "asr",
    catalog: Catalog | None = None,
    materialise_document: bool = True,
) -> SystemCallOutput:
    """A ``SystemCallOutput`` from a PipelineResult-shaped object (and the ``CachedCall`` it ran on)."""
    catalog = catalog or load_catalog()
    profile = get(result, "profile")
    call_id = get(profile, "call_id") or get(call, "call_id") or ""
    facts = []
    for f in (get(profile, "facts", {}) or {}).values():
        sf = system_fact(f, profile, catalog, transcript_kind)
        if sf is not None:
            facts.append(sf)
    entities = []
    for e in (get(profile, "entities", {}) or {}).values():
        attrs = get(e, "attributes", {}) or {}
        entities.append(
            SystemEntity(
                entity_id=get(e, "entity_id"),
                type=get(e, "type", "person"),
                name=get(e, "canonical_name", "") or "",
                aliases=list(get(e, "aliases", []) or []),
                fna_list=get(e, "fna_list"),
                fna_id=get(e, "fna_id"),
                relation=str(attrs.get("relation", "") or ""),
            )
        )
    flags_src = get(result, "flags")
    if flags_src is None:
        flags_src = get(profile, "blocking_flags", []) or []
    flags = [
        SystemFlag(kind=str(get(fl, "kind", "")), fact_id=get(fl, "fact_id"), cleared=bool(get(fl, "cleared", False))) for fl in flags_src
    ]
    followups_src = get(result, "followups")
    if followups_src is None:
        followups_src = get(profile, "open_questions", []) or []
    followups = [q if isinstance(q, str) else str(get(q, "question", "")) for q in followups_src]
    forecast = get(result, "forecast")
    pos = get(forecast, "pos")
    document = None
    if materialise_document and profile is not None and not isinstance(profile, dict):
        document = _median_document(profile, get(call, "prior_document"))
    discourse = get(result, "discourse") or get(call, "discourse")
    mentions = []
    for m in get(discourse, "mentions", []) or []:
        mentions.append(
            GoldMention(
                segment_id=get(m, "segment_id"),
                char_start=int(get(m, "char_start", 0)),
                char_end=int(get(m, "char_end", 0)),
                surface=get(m, "surface", "") or "",
                entity_id=get(m, "entity_id"),
            )
        )
    topic_index = {str(k): list(v) for k, v in (get(discourse, "topic_index", {}) or {}).items()}
    transcript = get(call, "transcript")
    minutes = None
    if isinstance(transcript, Transcript) and transcript.segments:
        minutes = transcript.duration / 60.0
    elif get(call, "call_minutes") is not None:
        minutes = float(get(call, "call_minutes"))
    outcome = get(result, "outcome")
    return SystemCallOutput(
        call_id=call_id,
        transcript_kind=transcript_kind,
        facts=facts,
        entities=entities,
        flags=flags,
        note=_note_lines(get(result, "note")),
        followups=followups,
        pos=float(pos) if isinstance(pos, (int, float)) else None,
        forecast_seed=_int_or_none(get(forecast, "seed")),
        document=document,
        mentions=mentions,
        topic_index=topic_index,
        usage=_usage(result),
        call_minutes=minutes,
        tool_calls=_tool_calls(result),
        partial=bool(get(result, "partial", False) or get(outcome, "partial", False)),
    )


def _median_document(profile: Any, prior: Any) -> dict[str, Any] | None:
    from ..binding import materialise  # local import: binding pulls in the reducer

    try:
        base = prior if isinstance(prior, FnaDocument) else (FnaDocument.model_validate(prior) if isinstance(prior, dict) else None)
        return materialise(profile, "median", base=base).model_dump(mode="json")
    except (KeyError, ValueError, TypeError):
        return None
