"""Gold calls: the format, annotation import, adjudication and inter-annotator agreement (spec §15.1–15.2).

A gold call is what two annotators and a senior adviser agree the client said: the adjudicated ``FnaDocument``
plus one ``GoldFact`` per slot they filled (status, attribution, the evidence spans, and the p10 / p50 / p90 the
client meant, averaged over the two annotators), the entities with their FnaDocument rows (the entity mapping),
and the labels the discourse metrics need (mentions, topic index).

Annotation file format
----------------------
The FNA sheet's annotate mode exports one ``AnnotationFile`` per annotator per call (the app-side shape lives in
``lib/review/`` once the UI ships it; this model is the contract until then)::

    {
      "format": "advisor-intel-annotation", "version": 1,
      "call_id": "c-123", "client_id": "crm-9", "annotator_id": "ann-1", "role": "annotator",
      "anchor_date": "2026-10-15", "transcript": "human",
      "document": { …the annotator's completed FnaDocument (schema v2)… },
      "facts": [
        {"fna_path": "retirement.client.retirementAge", "status": "stated", "attribution": "client",
         "p10": 60, "p50": 62, "p90": 65,
         "spans": [{"segment_id": "s0042", "char_start": 10, "char_end": 31, "quote": "somewhere around sixty"}]},
        {"fna_path": "dependants[<row id>].tertiary", "status": "stated", "value": true, "spans": […]},
        {"slot_id": "event[].occurs", "instance_key": "event.offshore_study", "status": "stated", "p50": 0.4}
      ],
      "entities": [{"row_id": "<row id>", "fna_list": "dependants", "entity_id": "person.thandi_1", "names": ["Thandi"]}],
      "mentions": [{"segment_id": "s0042", "char_start": 0, "char_end": 3, "surface": "she", "entity_id": "person.thandi_1"}],
      "topic_labels": {"s0042": ["retirement"]}
    }

``fna_path`` is the concrete FnaDocument path the annotator edited; forecast-only slots, which have no document
field, give ``slot_id`` and ``instance_key`` instead. Dates may be ISO strings. A categorical value is its label (a
risk answer may also give the option index). An adjudicator's file (``role="adjudicator"``) settles disagreements.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Iterable, Literal, Sequence

import numpy as np
from pydantic import BaseModel, field_validator, model_validator
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

from .. import stats
from ..binding import get_path, parse_path, set_path
from ..models.facts import Attribution, FactStatus
from ..models.fna import FnaDocument
from ..schema.slots import Catalog, SlotDef, fact_id_for, load_catalog, person_of

STATUSES: tuple[str, ...] = ("stated", "implied", "explicitly_unknown", "not_discussed", "contradicted")
VALUED_STATUSES = frozenset({"stated", "implied"})
KAPPA_REWRITE_THRESHOLD = 0.6  # §15.2: slots below this get their schema question rewritten

FactKey = tuple[str, str | None]

# --------------------------------------------------------------------------- gold format


class Span(BaseModel):
    """A transcript span: character offsets within one segment's 1-best text, with times when known."""

    segment_id: str
    char_start: int
    char_end: int
    quote: str = ""
    t_start: float | None = None
    t_end: float | None = None
    transcript: str = "asr"  # which transcript the offsets refer to: "asr", "human" or "script"


def _num(x: Any) -> float | None:
    """A number from an annotated value: numbers as-is, ISO dates as decimal years."""
    if x is None or x == "":
        return None
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    try:
        return float(s)
    except ValueError:
        return stats.iso_to_year(s)


class GoldValue(BaseModel):
    """What the client meant. Numeric values carry the adjudicated p10 / p50 / p90 (§15.2)."""

    kind: Literal["numeric", "categorical", "boolean", "text"]
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None
    label: str | None = None  # categorical label, free text, or "true" / "false"
    vague: bool = False  # the client's words were vague (a range or a hedge): counts for interval coverage

    @field_validator("p10", "p50", "p90", mode="before")
    @classmethod
    def _numbers(cls, v: Any) -> float | None:
        return _num(v)

    @model_validator(mode="after")
    def _fill(self) -> "GoldValue":
        if self.kind == "numeric":
            if self.p50 is None:
                raise ValueError("a numeric gold value needs p50")
            lo = self.p10 if self.p10 is not None else self.p50
            hi = self.p90 if self.p90 is not None else self.p50
            self.p10, self.p90 = min(lo, self.p50), max(hi, self.p50)
        elif self.label is None:
            raise ValueError(f"a {self.kind} gold value needs a label")
        return self

    @property
    def truth(self) -> bool | None:
        return None if self.kind != "boolean" else self.label == "true"


class GoldFact(BaseModel):
    slot_id: str  # generic slot id, as in the catalog
    instance_key: str | None = None  # "client" / "spouse", a gold entity id, an expense key, or None
    fna_path: str = ""  # concrete path in the gold document, when the slot is exported
    status: FactStatus
    attribution: Attribution = "client"
    value: GoldValue | None = None
    spans: list[Span] = []
    disputed: bool = False  # the annotators disagreed and nobody adjudicated: left out of fine-tuning data
    note: str = ""

    @property
    def key(self) -> FactKey:
        return (self.slot_id, self.instance_key)

    @property
    def fact_id(self) -> str:
        return fact_id_for(self.slot_id, self.instance_key)


class GoldEntity(BaseModel):
    entity_id: str
    type: str = "person"
    fna_list: str | None = None  # "dependants", "policies", …
    fna_id: str | None = None  # the row id in the gold document
    names: list[str] = []
    relation: str = ""


class GoldMention(BaseModel):
    segment_id: str
    char_start: int
    char_end: int
    surface: str = ""
    entity_id: str | None = None


class GoldCall(BaseModel):
    """One adjudicated gold call (§15.1). Synthetic calls use the same format with ``source="synthetic"``."""

    format: Literal["advisor-intel-gold"] = "advisor-intel-gold"
    version: Literal[1] = 1
    call_id: str
    client_id: str = ""
    anchor_date: date
    source: Literal["real", "synthetic"] = "real"
    tags: list[str] = []
    split: Literal["dev", "heldout"] = "dev"  # the 20 held-out calls GEPA and fine-tuning never see (§15.6, §15.8)
    transcript: str = "human"  # which transcript the spans refer to
    document: FnaDocument | None = None  # the adjudicated gold FnaDocument
    prior_document: FnaDocument | None = None  # the client's FNA before the call
    facts: list[GoldFact] = []
    entities: list[GoldEntity] = []
    entity_map: dict[str, str] = {}  # gold entity id → pipeline registry id, when known (prior rows, CRM)
    mentions: list[GoldMention] = []
    topic_labels: dict[str, list[str]] = {}  # segment id → slot groups the segment is about
    note_fact_ids: list[str] = []  # gold facts a meeting note must mention; empty: every tier A stated/implied fact
    pos: float | None = None  # PoS of the gold profile, when precomputed (§15.3 forecast metric)
    annotators: list[str] = []
    adjudicator: str | None = None

    def fact_index(self) -> dict[FactKey, GoldFact]:
        return {f.key: f for f in self.facts}


# --------------------------------------------------------------------------- annotation files


class AnnotatedSpan(BaseModel):
    segment_id: str
    char_start: int
    char_end: int
    quote: str = ""
    t_start: float | None = None
    t_end: float | None = None


class AnnotatedFact(BaseModel):
    fna_path: str = ""
    slot_id: str | None = None
    instance_key: str | None = None
    status: FactStatus
    attribution: Attribution = "client"
    p10: float | str | None = None
    p50: float | str | None = None
    p90: float | str | None = None
    value: bool | int | float | str | None = None  # categorical label, boolean, text, or a point number
    spans: list[AnnotatedSpan] = []
    note: str = ""

    @model_validator(mode="after")
    def _addressed(self) -> "AnnotatedFact":
        if not self.fna_path and not self.slot_id:
            raise ValueError("an annotated fact needs fna_path or slot_id")
        return self


class AnnotatedEntity(BaseModel):
    row_id: str  # the FnaDocument row id the annotator kept or created
    fna_list: str  # "dependants", "retirementFunds", …
    entity_id: str | None = None  # a stable id shared across annotators (prior rows keep their registry ids)
    names: list[str] = []
    relation: str = ""


class AnnotationFile(BaseModel):
    format: Literal["advisor-intel-annotation"] = "advisor-intel-annotation"
    version: Literal[1] = 1
    call_id: str
    client_id: str = ""
    annotator_id: str
    role: Literal["annotator", "adjudicator"] = "annotator"
    annotated_at: datetime | None = None
    anchor_date: date
    transcript: Literal["human", "asr"] = "human"
    document: FnaDocument
    facts: list[AnnotatedFact] = []
    entities: list[AnnotatedEntity] = []
    mentions: list[GoldMention] = []
    topic_labels: dict[str, list[str]] = {}
    seconds_spent: float | None = None


_LIST_ENTITY_TYPE = {
    "dependants": "person",
    "retirementFunds": "account",
    "assets": "property",
    "liabilities": "liability",
    "policies": "policy",
    "goals": "goal",
    "expenses.custom": "account",
}


def slot_for(catalog: Catalog, slot_or_path: str) -> SlotDef:
    """The catalog slot for a generic id, a concrete id, or a concrete FnaDocument path."""
    try:
        return catalog.slot(slot_or_path)
    except KeyError:
        generic = re.sub(r"\[[^\]]*\]", "[]", slot_or_path)
        return catalog.slot(generic)


def key_for_path(path: str, catalog: Catalog, row_entities: dict[str, str] | None = None) -> FactKey:
    """(slot id, instance) for a concrete FnaDocument path; list rows map to their entity through ``row_entities``."""
    row_entities = row_entities or {}
    pp = parse_path(path)
    if pp.key is None:
        slot = slot_for(catalog, path)
        return slot.slot_id, (person_of(path) if slot.per_person else None)
    if pp.container == "expenses.items":
        return "expenses.items[]", pp.key
    if pp.container == "riskProfile.answers":
        return path, None
    slot = slot_for(catalog, f"{pp.container}[].{pp.field}")
    return slot.slot_id, row_entities.get(pp.key, f"row:{pp.key}")


def gold_value(slot: SlotDef, *, p10: Any = None, p50: Any = None, p90: Any = None, value: Any = None) -> GoldValue | None:
    """A ``GoldValue`` from annotated quantiles or a point value, typed by the slot."""
    if slot.value_type == "probability":
        p = _num(p50 if p50 is not None else value)
        if p is None:
            return None
        lo, hi = _num(p10), _num(p90)
        gv = GoldValue(kind="numeric", p10=lo if lo is not None else p, p50=p, p90=hi if hi is not None else p)
        return gv.model_copy(update={"vague": bool(gv.p90 is not None and gv.p10 is not None and gv.p90 > gv.p10)})
    if slot.value_type == "boolean":
        v = value if value is not None else p50
        if v is None:
            return None
        truth = v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1", "1.0")
        return GoldValue(kind="boolean", label="true" if truth else "false")
    if slot.value_type in ("category", "count"):
        v = value if value is not None else p50
        if v is None:
            return None
        if slot.store_as == "index" and slot.categories and isinstance(v, (int, float)) and not isinstance(v, bool):
            idx = int(v)
            v = slot.categories[idx] if 0 <= idx < len(slot.categories) else str(v)
        label = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
        return GoldValue(kind="categorical", label=label)
    if slot.value_type == "text":
        v = value if value is not None else p50
        return None if v in (None, "") else GoldValue(kind="text", label=str(v))
    mid = p50 if p50 is not None else value
    if mid is None:
        return None
    lo, hi = (p10 if p10 is not None else mid), (p90 if p90 is not None else mid)
    gv = GoldValue(kind="numeric", p10=lo, p50=mid, p90=hi)
    return gv.model_copy(update={"vague": bool(gv.p90 is not None and gv.p10 is not None and gv.p90 > gv.p10)})


# --------------------------------------------------------------------------- adjudication


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:24] or "item"


def _entity_ids(ann: AnnotationFile) -> dict[str, str]:
    """row id → gold entity id for one annotator's rows."""
    out: dict[str, str] = {}
    for e in ann.entities:
        out[e.row_id] = e.entity_id or f"{_LIST_ENTITY_TYPE.get(e.fna_list, 'item')}.{_slug(e.names[0] if e.names else e.row_id)}"
    return out


def align_annotator_entities(a: AnnotationFile, b: AnnotationFile, threshold: float = 80.0) -> dict[str, str]:
    """b's gold entity ids → a's: same row id or entity id first, then fuzzy name match within a list (one-to-one)."""
    ids_a, ids_b = _entity_ids(a), _entity_ids(b)
    ent_a = {e.row_id: e for e in a.entities}
    out: dict[str, str] = {}
    unmatched_b = []
    taken: set[str] = set()
    for e in b.entities:
        if e.row_id in ent_a:
            out[ids_b[e.row_id]] = ids_a[e.row_id]
            taken.add(ids_a[e.row_id])
        elif e.entity_id and e.entity_id in ids_a.values():
            out[ids_b[e.row_id]] = e.entity_id
            taken.add(e.entity_id)
        else:
            unmatched_b.append(e)
    for lst in {e.fna_list for e in unmatched_b}:
        bs = [e for e in unmatched_b if e.fna_list == lst]
        as_ = [e for e in a.entities if e.fna_list == lst and ids_a[e.row_id] not in taken]
        if not as_:
            continue
        score = np.zeros((len(bs), len(as_)))
        for i, eb in enumerate(bs):
            for j, ea in enumerate(as_):
                s = max((fuzz.token_set_ratio(x, y) for x in eb.names or [""] for y in ea.names or [""]), default=0.0)
                score[i, j] = s + (5.0 if eb.relation and eb.relation == ea.relation else 0.0)
        rows, cols = linear_sum_assignment(-score)
        for i, j in zip(rows, cols):
            if score[i, j] >= threshold:
                out[ids_b[bs[i].row_id]] = ids_a[as_[j].row_id]
    return out


def _facts_by_key(ann: AnnotationFile, catalog: Catalog, rename: dict[str, str] | None = None) -> dict[FactKey, AnnotatedFact]:
    rows = _entity_ids(ann)
    rename = rename or {}
    out: dict[FactKey, AnnotatedFact] = {}
    for f in ann.facts:
        if f.slot_id and not f.fna_path:
            key: FactKey = (slot_for(catalog, f.slot_id).slot_id, f.instance_key)
        else:
            key = key_for_path(f.fna_path, catalog, rows)
        inst = rename.get(key[1], key[1]) if key[1] else key[1]
        out[(key[0], inst)] = f
    return out


def _to_span(s: AnnotatedSpan, transcript: str) -> Span:
    return Span(
        segment_id=s.segment_id,
        char_start=s.char_start,
        char_end=s.char_end,
        quote=s.quote,
        t_start=s.t_start,
        t_end=s.t_end,
        transcript=transcript,
    )


def _annotated_value(slot: SlotDef, f: AnnotatedFact | None) -> GoldValue | None:
    if f is None or f.status not in ("stated", "implied", "contradicted"):
        return None
    return gold_value(slot, p10=f.p10, p50=f.p50, p90=f.p90, value=f.value)


def _average(values: list[GoldValue]) -> GoldValue:
    """§15.2: the adjudicated distribution averages the annotators' quantiles."""
    if values[0].kind != "numeric":
        return values[0]
    p10 = float(np.mean([v.p10 for v in values]))  # type: ignore[arg-type]
    p50 = float(np.mean([v.p50 for v in values]))  # type: ignore[arg-type]
    p90 = float(np.mean([v.p90 for v in values]))  # type: ignore[arg-type]
    return GoldValue(kind="numeric", p10=p10, p50=p50, p90=p90, vague=any(v.vague for v in values))


def _write_value(doc: dict[str, Any], slot: SlotDef, path: str, v: GoldValue) -> None:
    if not path or not slot.exported:
        return
    if v.kind == "numeric":
        x = v.p50
        if slot.value_type == "date":
            set_path(doc, path, stats.year_to_iso(x))  # type: ignore[arg-type]
        elif slot.value_type in ("age", "years", "months", "year", "count"):
            set_path(doc, path, int(round(x)))  # type: ignore[arg-type]
        else:
            set_path(doc, path, round(float(x), 6))  # type: ignore[arg-type]
    elif v.kind == "boolean":
        set_path(doc, path, v.label == "true")
    elif v.kind == "categorical":
        if slot.store_as == "index" and slot.categories and v.label in slot.categories:
            set_path(doc, path, slot.categories.index(v.label))
        elif slot.store_as == "int":
            set_path(doc, path, int(float(v.label)))  # type: ignore[arg-type]
        else:
            set_path(doc, path, v.label)
    else:
        set_path(doc, path, v.label)


def adjudicate(
    a: AnnotationFile,
    b: AnnotationFile,
    *,
    adjudicator: AnnotationFile | None = None,
    catalog: Catalog | None = None,
    tags: Sequence[str] = ("gold",),
    split: Literal["dev", "heldout"] = "dev",
) -> GoldCall:
    """Two annotators' files (and optionally a senior adviser's) → one ``GoldCall`` (§15.1–15.2).

    Agreement is kept; quantiles are averaged; a disagreement on status, attribution or a label goes to the
    adjudicator's file, and without one the fact is kept from the first annotator and marked ``disputed``.
    """
    if a.call_id != b.call_id:
        raise ValueError(f"annotation files are for different calls: {a.call_id} vs {b.call_id}")
    catalog = catalog or load_catalog()
    rename_b = align_annotator_entities(a, b)
    fa, fb = _facts_by_key(a, catalog), _facts_by_key(b, catalog, rename_b)
    fc: dict[FactKey, AnnotatedFact] = {}
    if adjudicator is not None:
        fc = _facts_by_key(adjudicator, catalog, align_annotator_entities(a, adjudicator))
    rows_a = {v: k for k, v in _entity_ids(a).items()}  # gold entity id → row id in a's document

    doc = (adjudicator.document if adjudicator is not None else a.document).model_dump(mode="json")
    facts: list[GoldFact] = []
    for key in sorted(set(fa) | set(fb) | set(fc), key=lambda k: (k[0], k[1] or "")):
        slot = catalog.slot(key[0])
        x, y, z = fa.get(key), fb.get(key), fc.get(key)
        sx = x.status if x else "not_discussed"
        sy = y.status if y else "not_discussed"
        disputed = False
        if z is not None:
            status, attribution, src = z.status, z.attribution, z
        elif sx == sy:
            status = sx
            ax, ay = (x.attribution if x else "client"), (y.attribution if y else "client")
            attribution = ax if (ax == ay or not y) else (ay if not x else ax)
            disputed = bool(x and y and ax != ay)
            src = x or y
        else:
            status, attribution, src, disputed = sx, (x.attribution if x else "client"), x or y, True
        value: GoldValue | None = None
        if status in ("stated", "implied", "contradicted"):
            if z is not None:
                value = _annotated_value(slot, z)
            else:
                vals = [v for v in (_annotated_value(slot, x), _annotated_value(slot, y)) if v is not None]
                if vals and vals[0].kind == "numeric":
                    value = _average([v for v in vals if v.kind == "numeric"])
                elif vals:
                    labels = {v.label for v in vals}
                    value = vals[0]
                    disputed = disputed or len(labels) > 1
        spans: list[Span] = []
        seen: set[tuple[str, int, int]] = set()
        for f, tr in ((x, a.transcript), (y, b.transcript), (z, adjudicator.transcript if adjudicator else a.transcript)):
            for s in f.spans if f else []:
                if (s.segment_id, s.char_start, s.char_end) not in seen:
                    seen.add((s.segment_id, s.char_start, s.char_end))
                    spans.append(_to_span(s, tr))
        path = ""
        if src is not None and src.fna_path:
            path = src.fna_path
            pp = parse_path(path)
            if pp.key is not None and key[1] and key[1] in rows_a and pp.container not in ("expenses.items", "riskProfile.answers"):
                path = f"{pp.container}[{rows_a[key[1]]}].{pp.field}"
        facts.append(
            GoldFact(
                slot_id=key[0],
                instance_key=key[1],
                fna_path=path,
                status=status,
                attribution=attribution,
                value=value,
                spans=spans,
                disputed=disputed and z is None,
            )
        )
        if value is not None and adjudicator is None and path:
            try:
                _write_value(doc, slot, path, value)
            except (KeyError, TypeError, ValueError):
                pass  # a path into a row the first annotator never created stays as annotated

    entities = []
    ids_a = _entity_ids(a)
    for e in a.entities:
        etype = _LIST_ENTITY_TYPE.get(e.fna_list, "person")
        entities.append(
            GoldEntity(entity_id=ids_a[e.row_id], type=etype, fna_list=e.fna_list, fna_id=e.row_id, names=e.names, relation=e.relation)
        )
    entity_map = {ids_a[e.row_id]: e.entity_id for e in a.entities if e.entity_id}
    return GoldCall(
        call_id=a.call_id,
        client_id=a.client_id or b.client_id,
        anchor_date=a.anchor_date,
        source="real",
        tags=list(tags),
        split=split,
        transcript=a.transcript,
        document=FnaDocument.model_validate(doc),
        facts=facts,
        entities=entities,
        entity_map=entity_map,
        mentions=a.mentions or b.mentions,
        topic_labels=_union_topics(a.topic_labels, b.topic_labels),
        annotators=[a.annotator_id, b.annotator_id],
        adjudicator=adjudicator.annotator_id if adjudicator else None,
    )


def _union_topics(x: dict[str, list[str]], y: dict[str, list[str]]) -> dict[str, list[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for d in (x, y):
        for seg, groups in d.items():
            out[seg].update(groups)
    return {k: sorted(v) for k, v in sorted(out.items())}


def gold_from_document(doc: FnaDocument, *, call_id: str, anchor_date: date, catalog: Catalog | None = None) -> list[GoldFact]:
    """Point-valued gold facts for every non-empty exported field of a document (used for list rows' identity checks)."""
    catalog = catalog or load_catalog()
    data = doc.model_dump(mode="json")
    out: list[GoldFact] = []
    for slot in catalog.slots:
        if not slot.exported or slot.per_entity or slot.slot_id.startswith("riskProfile."):
            continue
        persons = ("client", "spouse") if slot.per_person else (None,)
        for p in persons:
            path = slot.fna_path_for(p)
            raw = get_path(data, path)
            if raw in (None, "") or (slot.value_type == "amount" and raw == 0):
                continue
            v = gold_value(slot, value=raw)
            if v is not None:
                out.append(GoldFact(slot_id=slot.slot_id, instance_key=p, fna_path=path, status="stated", value=v))
    return out


# --------------------------------------------------------------------------- inter-annotator agreement


def cohens_kappa(pairs: Sequence[tuple[str, str]], categories: Sequence[str] = STATUSES) -> float:
    """Cohen's κ for two raters over the given categories; 1.0 when both used a single category throughout."""
    if not pairs:
        return float("nan")
    idx = {c: i for i, c in enumerate(categories)}
    m = np.zeros((len(categories), len(categories)))
    for x, y in pairs:
        m[idx[x], idx[y]] += 1
    n = m.sum()
    po = np.trace(m) / n
    pe = float((m.sum(axis=1) / n) @ (m.sum(axis=0) / n))
    if pe >= 1 - 1e-12:
        return 1.0 if po >= 1 - 1e-12 else 0.0
    return float((po - pe) / (1 - pe))


def interval_overlap(a: GoldValue | None, b: GoldValue | None) -> float | None:
    """p10–p90 overlap of two numeric values: intersection over union; points agree within 2%."""
    if a is None or b is None or a.kind != "numeric" or b.kind != "numeric":
        return None
    lo_a, hi_a, lo_b, hi_b = a.p10, a.p90, b.p10, b.p90
    assert lo_a is not None and hi_a is not None and lo_b is not None and hi_b is not None
    if hi_a - lo_a <= 0 and hi_b - lo_b <= 0:
        scale = max(abs(lo_a), abs(lo_b), 1e-9)
        return 1.0 if abs(lo_a - lo_b) <= 0.02 * scale else 0.0
    inter = max(0.0, min(hi_a, hi_b) - max(lo_a, lo_b))
    union = max(hi_a, hi_b) - min(lo_a, lo_b)
    return float(inter / union) if union > 0 else 1.0


class GroupAgreement(BaseModel):
    group: str
    kappa: float | None
    n: int
    value_overlap: float | None  # mean p10–p90 overlap where both annotators gave a numeric value
    n_values: int


class SlotAgreement(BaseModel):
    slot_id: str
    group: str
    kappa: float | None
    n: int


class RewriteItem(BaseModel):
    slot_id: str
    group: str
    kappa: float
    n: int
    question: str  # the current schema question, to be rewritten before system work on the slot continues


class AgreementReport(BaseModel):
    by_group: dict[str, GroupAgreement]
    by_slot: dict[str, SlotAgreement]
    rewrite: list[RewriteItem]


def agreement(
    pairs: Iterable[tuple[AnnotationFile, AnnotationFile]],
    *,
    catalog: Catalog | None = None,
    threshold: float = KAPPA_REWRITE_THRESHOLD,
    min_items: int = 10,
) -> AgreementReport:
    """Inter-annotator agreement over double-annotated calls (§15.2): κ on status per slot group and per slot,
    p10–p90 overlap on values per group, and the list of slots with κ below 0.6 whose question needs rewriting."""
    catalog = catalog or load_catalog()
    status_by_group: dict[str, list[tuple[str, str]]] = defaultdict(list)
    status_by_slot: dict[str, list[tuple[str, str]]] = defaultdict(list)
    overlap_by_group: dict[str, list[float]] = defaultdict(list)
    for a, b in pairs:
        fa, fb = _facts_by_key(a, catalog), _facts_by_key(b, catalog, align_annotator_entities(a, b))
        for key in set(fa) | set(fb):
            slot = catalog.slot(key[0])
            x, y = fa.get(key), fb.get(key)
            pair = (x.status if x else "not_discussed", y.status if y else "not_discussed")
            status_by_group[slot.group].append(pair)
            status_by_slot[slot.slot_id].append(pair)
            ov = interval_overlap(_annotated_value(slot, x), _annotated_value(slot, y))
            if ov is not None:
                overlap_by_group[slot.group].append(ov)
    by_group = {}
    for g in sorted(set(status_by_group) | set(overlap_by_group)):
        ps, ovs = status_by_group.get(g, []), overlap_by_group.get(g, [])
        k = cohens_kappa(ps) if ps else None
        by_group[g] = GroupAgreement(
            group=g,
            kappa=None if k is None or math.isnan(k) else k,
            n=len(ps),
            value_overlap=float(np.mean(ovs)) if ovs else None,
            n_values=len(ovs),
        )
    by_slot = {}
    rewrite = []
    for s in sorted(status_by_slot):
        ps = status_by_slot[s]
        k = cohens_kappa(ps)
        slot = catalog.slot(s)
        by_slot[s] = SlotAgreement(slot_id=s, group=slot.group, kappa=None if math.isnan(k) else k, n=len(ps))
        if not math.isnan(k) and k < threshold and len(ps) >= min_items:
            rewrite.append(RewriteItem(slot_id=s, group=slot.group, kappa=k, n=len(ps), question=slot.question))
    rewrite.sort(key=lambda r: r.kappa)
    return AgreementReport(by_group=by_group, by_slot=by_slot, rewrite=rewrite)


def default_note_facts(gold: GoldCall, catalog: Catalog | None = None) -> list[GoldFact]:
    """The gold facts a meeting note should mention: the listed ones, else every tier A stated or implied fact."""
    catalog = catalog or load_catalog()
    if gold.note_fact_ids:
        wanted = set(gold.note_fact_ids)
        return [f for f in gold.facts if f.fact_id in wanted]
    return [f for f in gold.facts if f.status in VALUED_STATUSES and catalog.slot(f.slot_id).sensitivity_tier == "A"]
