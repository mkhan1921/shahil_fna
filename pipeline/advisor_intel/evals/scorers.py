"""Every §15.3 metric as a plain function, plus Braintrust scorer adapters (spec §15.3–15.4).

Per-item metrics compare one ``SystemCallOutput`` with its ``GoldCall`` and return a ``MetricResult`` (value, the
counts behind it, and readable misses that become GEPA feedback and report rows). Summary metrics take a list of
``EvalRecord``s. The Braintrust adapters (``ITEM_SCORERS``) wrap the per-item functions with the
``(input, output, expected)`` signature Braintrust calls them with; summary scores are computed by the eval's
reporter (``extraction_eval``), because the Python SDK has no summary-scorer hook.

Matching system facts to gold facts: per-person slots match by person, expense lines by catalog key, and list rows
by entity — system entities are aligned to gold entities one-to-one by FnaDocument row id, the gold entity map,
entity id, then fuzzy name match (Hungarian assignment). A system list fact whose entity matches no gold entity is
an invented row.

Definitions (all fractions in [0, 1] unless noted):

- status accuracy: over gold facts and every system fact with a status other than not_discussed, the share whose
  statuses agree (a missing side counts as not_discussed).
- slot recall: gold stated or implied facts the system found (stated, implied or contradicted, with a value).
- value coverage: of those found, the share whose value covers gold: numeric gold p50 inside system p10–p90;
  categorical modes equal; booleans on the same side of 0.5; event probabilities inside gold p10–p90 (±0.05);
  text fuzzy-equal.
- grounding: system facts found by extraction whose evidence overlaps a gold span (same transcript: characters in the
  same segment; human vs ASR transcripts: time); facts with no gold counterpart count as ungrounded.
- span F1: character (or time) overlap F1 between the fact's evidence and its gold spans, averaged over facts.
- attribution: client vs advisor (advisor_confirmed_by_client counts as the client's) on facts both sides found.
- the calibration label of a fact is §9.3's against gold: status right and, when valued, value covered.
"""

from __future__ import annotations

import inspect
import math
import re
import threading
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

from ..models.fna import FnaDocument
from ..schema.slots import Catalog, SlotDef, load_catalog
from ._runtime import get, lazy_attr
from .gold import VALUED_STATUSES, FactKey, GoldCall, GoldFact, GoldValue, Span, default_note_facts
from .records import FOUND_STATUSES, EvalRecord, SystemCallOutput, SystemEntity, SystemFact, ValueSummary

N_ECE_BINS = 15
COARSE_ATTRIBUTION = {"client": "client", "advisor_confirmed_by_client": "client", "advisor_suggested": "advisor", "mixed": "mixed"}
LIST_TYPES: dict[str, frozenset[str]] = {
    "dependants": frozenset({"person"}),
    "retirementFunds": frozenset({"account"}),
    "assets": frozenset({"property", "account", "business"}),
    "liabilities": frozenset({"liability"}),
    "policies": frozenset({"policy"}),
    "goals": frozenset({"goal"}),
    "expenses.custom": frozenset({"account"}),
}
HOUSEHOLD_ENTITIES = frozenset({"person.client_primary", "person.spouse", "person.advisor"})


@dataclass
class MetricResult:
    """A metric value with the counts behind it; ``misses`` are readable failures for feedback and reports."""

    value: float | None
    hits: float = 0.0
    total: float = 0.0
    misses: list[str] = field(default_factory=list)


def ratio(hits: float, total: float, misses: list[str] | None = None) -> MetricResult:
    return MetricResult(value=(hits / total) if total else None, hits=hits, total=total, misses=misses or [])


def pooled(results: Iterable[MetricResult]) -> MetricResult:
    """Micro-average several results (sum hits over sum totals)."""
    rs = [r for r in results if r.total]
    return ratio(sum(r.hits for r in rs), sum(r.total for r in rs), [m for r in rs for m in r.misses])


# --------------------------------------------------------------------------- alignment


@dataclass
class FactPair:
    key: FactKey
    slot: SlotDef
    gold: GoldFact | None
    system: SystemFact | None

    @property
    def tier(self) -> str:
        return self.slot.sensitivity_tier

    @property
    def group(self) -> str:
        return self.slot.group

    @property
    def label(self) -> str:
        if self.system is not None and self.system.entity_name:
            return self.system.entity_name
        inst = self.key[1]
        if self.slot.per_person and inst:
            return self.slot.expand(inst)
        return self.slot.slot_id.replace("[]", f"[{inst}]", 1) if inst else self.slot.slot_id

    @property
    def gold_status(self) -> str:
        return self.gold.status if self.gold else "not_discussed"

    @property
    def system_status(self) -> str:
        return self.system.status if self.system else "not_discussed"

    @property
    def system_found(self) -> bool:
        return self.system is not None and self.system.found

    @property
    def gold_valued(self) -> bool:
        return self.gold is not None and self.gold.status in VALUED_STATUSES


@dataclass
class Alignment:
    pairs: list[FactPair]
    entity_map: dict[str, str]  # system entity id → gold entity id

    def scoped(self, tier: str | None = None, groups: Iterable[str] | None = None) -> list[FactPair]:
        gs = set(groups) if groups is not None else None
        return [p for p in self.pairs if (tier is None or p.tier == tier) and (gs is None or p.group in gs)]

    def key_of(self, fact_id: str, catalog: Catalog) -> FactKey | None:
        """A system fact id ('slot#instance') as an alignment key."""
        slot_id, _, inst = fact_id.partition("#")
        try:
            slot = catalog.slot(slot_id)
        except KeyError:
            return None
        return (slot.slot_id, _normalise_instance(slot, inst or None, self.entity_map))


def _normalise_instance(slot: SlotDef, inst: str | None, emap: Mapping[str, str]) -> str | None:
    if inst is None or inst in ("client", "spouse") or slot.slot_id == "expenses.items[]":
        return inst
    return emap.get(inst, f"sys:{inst}")


def _compatible(s: SystemEntity, g: Any) -> bool:
    if s.entity_id in HOUSEHOLD_ENTITIES:
        return False
    if s.fna_list and g.fna_list:
        return s.fna_list == g.fna_list
    if g.fna_list:
        return s.type in LIST_TYPES.get(g.fna_list, frozenset({s.type}))
    return s.type == g.type


def align_entities(system: SystemCallOutput, gold: GoldCall, *, threshold: float = 70.0) -> dict[str, str]:
    """System entity id → gold entity id, one-to-one (row id, gold entity map, entity id, then fuzzy names)."""
    used_sys = {f.instance_key for f in system.facts if f.instance_key} | {m.entity_id for m in system.mentions if m.entity_id}
    sys_ents = [e for e in system.entities if e.entity_id in used_sys or e.fna_id]
    out: dict[str, str] = {}
    taken: set[str] = set()
    hinted = {v: k for k, v in gold.entity_map.items()}
    for e in sys_ents:
        g = None
        if e.entity_id in hinted:
            g = hinted[e.entity_id]
        elif e.fna_id:
            g = next((ge.entity_id for ge in gold.entities if ge.fna_id and ge.fna_id == e.fna_id), None)
        if g is None:
            g = next((ge.entity_id for ge in gold.entities if ge.entity_id == e.entity_id), None)
        if g is not None and g not in taken:
            out[e.entity_id] = g
            taken.add(g)
    rest_s = [e for e in sys_ents if e.entity_id not in out and e.entity_id not in HOUSEHOLD_ENTITIES]
    rest_g = [g for g in gold.entities if g.entity_id not in taken]
    if rest_s and rest_g:
        score = np.full((len(rest_s), len(rest_g)), -1.0)
        for i, s in enumerate(rest_s):
            names_s = [n for n in [s.name, *s.aliases] if n] or [s.entity_id]
            for j, g in enumerate(rest_g):
                if not _compatible(s, g):
                    continue
                names_g = [n for n in g.names if n] or [g.entity_id]
                best = max(fuzz.token_set_ratio(a, b) for a in names_s for b in names_g)
                score[i, j] = best + (10.0 if s.relation and s.relation == g.relation else 0.0)
        rows, cols = linear_sum_assignment(-score)
        for i, j in zip(rows, cols):
            if score[i, j] >= threshold:
                out[rest_s[i].entity_id] = rest_g[j].entity_id
    return out


def align(system: SystemCallOutput, gold: GoldCall, *, catalog: Catalog | None = None) -> Alignment:
    """Pair every gold fact with the system fact for the same slot and instance."""
    catalog = catalog or load_catalog()
    emap = align_entities(system, gold)
    by_key: dict[FactKey, list[Any]] = {}
    for g in gold.facts:
        by_key.setdefault(g.key, [None, None])[0] = g
    for f in system.facts:
        slot = catalog.slot(f.slot_id)
        key = (slot.slot_id, _normalise_instance(slot, f.instance_key, emap))
        entry = by_key.setdefault(key, [None, None])
        if entry[1] is None or _prefer(f, entry[1]):
            entry[1] = f
    pairs = [FactPair(key=k, slot=catalog.slot(k[0]), gold=g, system=s) for k, (g, s) in by_key.items()]
    pairs.sort(key=lambda p: (p.key[0], p.key[1] or ""))
    return Alignment(pairs=pairs, entity_map=emap)


def _prefer(a: SystemFact, b: SystemFact) -> bool:
    """When two system facts land on one key, keep the one with a value and the higher p_correct."""
    return (a.found, a.p_correct or 0.0) > (b.found, b.p_correct or 0.0)


class _AlignCache:
    """Alignments for repeated scoring of the same (system, gold) pair."""

    def __init__(self) -> None:
        self._d: dict[tuple[int, int], Alignment] = {}

    def get(self, system: SystemCallOutput, gold: GoldCall, catalog: Catalog | None = None) -> Alignment:
        k = (id(system), id(gold))
        if k not in self._d:
            self._d[k] = align(system, gold, catalog=catalog)
        return self._d[k]


# --------------------------------------------------------------------------- value comparison and labels


def _norm_label(x: str | None) -> str:
    s = (x or "").strip().lower()
    try:
        f = float(s)
        return str(int(f)) if f.is_integer() else s
    except ValueError:
        return s


def covers(sv: ValueSummary | None, gv: GoldValue | None) -> bool:
    """Whether a system value covers the gold value (§15.3 value accuracy; §9.3 label against gold)."""
    if sv is None or gv is None:
        return False
    if gv.kind == "numeric":
        assert gv.p10 is not None and gv.p50 is not None and gv.p90 is not None
        if sv.kind == "probability":
            return sv.p_true is not None and gv.p10 - 0.05 <= sv.p_true <= gv.p90 + 0.05
        if sv.kind != "numeric" or sv.p10 is None or sv.p90 is None:
            return False
        tol = 1e-9 * max(1.0, abs(gv.p50))
        return sv.p10 - tol <= gv.p50 <= sv.p90 + tol
    if gv.kind == "boolean":
        p = sv.p_true if sv.p_true is not None else (1.0 if _norm_label(sv.label) in ("true", "yes", "1") else 0.0 if sv.label else None)
        return p is not None and (p >= 0.5) == bool(gv.truth)
    if gv.kind == "categorical":
        return _norm_label(sv.label) == _norm_label(gv.label)
    return bool(sv.label) and fuzz.token_set_ratio(sv.label or "", gv.label or "") >= 80


def fact_label(pair: FactPair) -> int | None:
    """§9.3's label against gold: 1 when the status is right and, for a valued fact, the value covers gold."""
    if pair.system is None:
        return None
    if pair.system_status != pair.gold_status:
        return 0
    if pair.system.status in FOUND_STATUSES and pair.system.value is not None:
        if pair.gold is None or pair.gold.value is None:
            return 1
        return int(covers(pair.system.value, pair.gold.value))
    return 1


def value_text(v: ValueSummary | GoldValue | None) -> str:
    if v is None:
        return "no value"
    if v.kind in ("numeric",):
        if v.p10 is not None and v.p90 is not None and abs(v.p90 - v.p10) > 1e-9:
            return f"{v.p10:,.4g}–{v.p90:,.4g}"
        return f"{v.p50:,.4g}" if v.p50 is not None else "no value"
    if v.kind == "probability":
        return f"P={v.p_true:.2f}" if isinstance(v, ValueSummary) and v.p_true is not None else "P=?"
    if isinstance(v, ValueSummary) and v.kind == "boolean":
        return f"P(true)={v.p_true:.2f}" if v.p_true is not None else "?"
    return str(v.label)


# --------------------------------------------------------------------------- per-item extraction metrics


def _alignment(system: SystemCallOutput, gold: GoldCall, alignment: Alignment | None) -> Alignment:
    return alignment if alignment is not None else align(system, gold)


def status_accuracy(
    system: SystemCallOutput,
    gold: GoldCall,
    *,
    tier: str | None = None,
    groups: Iterable[str] | None = None,
    alignment: Alignment | None = None,
) -> MetricResult:
    hits, total, misses = 0, 0, []
    for p in _alignment(system, gold, alignment).scoped(tier, groups):
        if p.gold is None and p.system_status == "not_discussed":
            continue
        total += 1
        if p.system_status == p.gold_status:
            hits += 1
        elif p.gold is None:
            misses.append(f"invented {p.label} (marked {p.system_status}; not discussed)")
        elif p.system is None or p.system_status == "not_discussed":
            misses.append(f"missed {p.label} (gold {p.gold_status})")
        else:
            misses.append(f"marked {p.label} {p.system_status}, gold {p.gold_status}")
    return ratio(hits, total, misses)


def slot_recall(
    system: SystemCallOutput,
    gold: GoldCall,
    *,
    tier: str | None = None,
    groups: Iterable[str] | None = None,
    alignment: Alignment | None = None,
) -> MetricResult:
    hits, total, misses = 0, 0, []
    for p in _alignment(system, gold, alignment).scoped(tier, groups):
        if not p.gold_valued:
            continue
        total += 1
        if p.system_found:
            hits += 1
        else:
            misses.append(f"missed {p.label}")
    return ratio(hits, total, misses)


def value_coverage(
    system: SystemCallOutput,
    gold: GoldCall,
    *,
    tier: str | None = None,
    groups: Iterable[str] | None = None,
    alignment: Alignment | None = None,
    vague_only: bool = False,
) -> MetricResult:
    hits, total, misses = 0, 0, []
    for p in _alignment(system, gold, alignment).scoped(tier, groups):
        if not (p.gold_valued and p.system_found and p.gold is not None and p.gold.value is not None):
            continue
        assert p.system is not None
        if vague_only and not (p.gold.value.vague or p.system.vague):
            continue
        if p.gold.value.kind == "numeric" and p.system.value is not None and p.system.value.kind not in ("numeric", "probability"):
            continue
        total += 1
        if covers(p.system.value, p.gold.value):
            hits += 1
        else:
            misses.append(f"{p.label}: extracted {value_text(p.system.value)}, gold {value_text(p.gold.value)}")
    return ratio(hits, total, misses)


def _overlaps(a: Span, b: Span) -> bool:
    same_transcript = a.transcript == b.transcript
    if same_transcript or a.t_start is None or b.t_start is None or a.t_end is None or b.t_end is None:
        return a.segment_id == b.segment_id and a.char_start < b.char_end and b.char_start < a.char_end
    return a.t_start < b.t_end and b.t_start < a.t_end


def grounding(
    system: SystemCallOutput,
    gold: GoldCall,
    *,
    tier: str | None = None,
    groups: Iterable[str] | None = None,
    alignment: Alignment | None = None,
) -> MetricResult:
    hits, total, misses = 0, 0, []
    for p in _alignment(system, gold, alignment).scoped(tier, groups):
        s = p.system
        if s is None or not s.extracted or s.status not in ("stated", "implied"):
            continue
        if p.gold is not None and not p.gold.spans:
            continue  # the annotators marked no span: nothing to compare with
        total += 1
        if p.gold is not None and any(_overlaps(x, y) for x in s.spans for y in p.gold.spans):
            hits += 1
        elif p.gold is None or p.gold.status not in VALUED_STATUSES:
            misses.append(f"{p.label}: evidence for a fact that was not discussed")
        else:
            misses.append(f"{p.label}: evidence {', '.join(repr(x.quote) for x in s.spans[:2]) or 'none'} misses the gold span")
    return ratio(hits, total, misses)


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for lo, hi in sorted(intervals):
        if hi <= lo:
            continue
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _measure(intervals: list[tuple[float, float]]) -> float:
    return sum(hi - lo for lo, hi in intervals)


def _intersection(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    total, i, j = 0.0, 0, 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            total += hi - lo
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def span_overlap_f1(sys_spans: Sequence[Span], gold_spans: Sequence[Span]) -> float | None:
    """Character-overlap F1 between two span sets (time overlap when they are on different transcripts)."""
    if not sys_spans or not gold_spans:
        return None if not gold_spans else 0.0
    by_time = any(s.transcript != g.transcript for s in sys_spans for g in gold_spans) and all(
        x.t_start is not None and x.t_end is not None for x in [*sys_spans, *gold_spans]
    )
    if by_time:
        a = _merge([(float(s.t_start), float(s.t_end)) for s in sys_spans])  # type: ignore[arg-type]
        b = _merge([(float(g.t_start), float(g.t_end)) for g in gold_spans])  # type: ignore[arg-type]
        inter, total = _intersection(a, b), _measure(a) + _measure(b)
    else:
        inter, total = 0.0, 0.0
        segs = {s.segment_id for s in sys_spans} | {g.segment_id for g in gold_spans}
        for seg in segs:
            a = _merge([(s.char_start, s.char_end) for s in sys_spans if s.segment_id == seg])
            b = _merge([(g.char_start, g.char_end) for g in gold_spans if g.segment_id == seg])
            inter += _intersection(a, b)
            total += _measure(a) + _measure(b)
    return 2 * inter / total if total else 0.0


def span_f1(
    system: SystemCallOutput,
    gold: GoldCall,
    *,
    tier: str | None = None,
    groups: Iterable[str] | None = None,
    alignment: Alignment | None = None,
) -> MetricResult:
    scores, misses = [], []
    for p in _alignment(system, gold, alignment).scoped(tier, groups):
        s = p.system
        if s is None or not s.extracted or s.status not in ("stated", "implied") or p.gold is None or not p.gold.spans:
            continue
        f1 = span_overlap_f1(s.spans, p.gold.spans)
        if f1 is None:
            continue
        scores.append(f1)
        if f1 < 0.5:
            misses.append(f"{p.label}: span F1 {f1:.2f}")
    return MetricResult(
        value=float(np.mean(scores)) if scores else None, hits=float(np.sum(scores)) if scores else 0.0, total=len(scores), misses=misses
    )


def attribution_accuracy(
    system: SystemCallOutput,
    gold: GoldCall,
    *,
    tier: str | None = None,
    groups: Iterable[str] | None = None,
    alignment: Alignment | None = None,
    coarse: bool = True,
) -> MetricResult:
    hits, total, misses = 0, 0, []
    m = COARSE_ATTRIBUTION if coarse else {k: k for k in COARSE_ATTRIBUTION}
    for p in _alignment(system, gold, alignment).scoped(tier, groups):
        if not (p.gold_valued and p.system_found):
            continue
        assert p.system is not None and p.gold is not None
        total += 1
        if m[p.system.attribution] == m[p.gold.attribution]:
            hits += 1
        elif m[p.gold.attribution] == "advisor":
            misses.append(f"{p.label}: marked advisor suggestion as the client's")
        elif m[p.system.attribution] == "advisor":
            misses.append(f"{p.label}: marked the client's statement as an advisor suggestion")
        else:
            misses.append(f"{p.label}: attributed to {p.system.attribution}, gold {p.gold.attribution}")
    return ratio(hits, total, misses)


# --------------------------------------------------------------------------- omissions, flags and the meeting note


def critical_misses(system: SystemCallOutput, gold: GoldCall, *, alignment: Alignment | None = None) -> list[FactPair]:
    """Tier A facts missing or wrong after the pipeline (§15.3 omissions): missed, wrong status, value not covered,
    or a tier A value the call never gave."""
    out = []
    for p in _alignment(system, gold, alignment).scoped("A"):
        if p.gold_valued:
            assert p.gold is not None
            if not p.system_found or p.system_status != p.gold_status:
                out.append(p)
            elif p.gold.value is not None and not covers(p.system.value if p.system else None, p.gold.value):
                out.append(p)
        elif p.system is not None and p.system.extracted and p.system.status in ("stated", "implied") and p.gold_status != p.system_status:
            out.append(p)
    return out


def flagged_keys(system: SystemCallOutput, alignment: Alignment, catalog: Catalog | None = None) -> set[FactKey]:
    catalog = catalog or load_catalog()
    out = set()
    for fl in system.flags:
        if fl.fact_id:
            k = alignment.key_of(fl.fact_id, catalog)
            if k is not None:
                out.add(k)
    return out


_NUM = re.compile(r"(?<![\w.])R?\s?(\d{1,3}(?:[ ,]\d{3})+|\d+(?:\.\d+)?)\s*(million|mil|bn|billion|thousand|k|m|%)?(?![\w])", re.IGNORECASE)
_SCALE = {"million": 1e6, "mil": 1e6, "m": 1e6, "bn": 1e9, "billion": 1e9, "thousand": 1e3, "k": 1e3}


def numbers_in(text: str) -> list[list[float]]:
    """Each number in a sentence as its candidate readings ('R2.5m' → [2_500_000]; '75%' → [0.75, 75])."""
    out = []
    for m in _NUM.finditer(text):
        x = float(m.group(1).replace(",", "").replace(" ", ""))
        unit = (m.group(2) or "").lower()
        if unit == "%":
            out.append([x / 100.0, x])
        elif unit in _SCALE:
            out.append([x * _SCALE[unit]])
        else:
            out.append([x])
    return out


def _anchors(f: SystemFact) -> list[float]:
    """The numbers a fact supports in a note sentence: its quantiles or P(true), the calendar year of a date, a
    categorical fact's label (as a number, or the numbers inside it: 'Less than 1 month') and its option
    probabilities (the note's '(100%)' certainty), and P(false) for a boolean said the other way round."""
    v = f.value
    if v is None:
        return []
    xs = [x for x in (v.p10, v.p50, v.p90, v.p_true) if x is not None]
    if v.kind == "numeric" and v.p50 is not None and 1000 < v.p50 < 3000:
        xs.append(math.floor(v.p50))  # dates as decimal years: the calendar year
    if v.kind in ("boolean", "probability") and v.p_true is not None:
        xs.append(1.0 - v.p_true)
    if v.kind == "categorical":
        if v.label:
            try:
                xs.append(float(v.label))
            except ValueError:
                xs.extend(c for cands in numbers_in(v.label) for c in cands)
        xs.extend(p for p in v.probs.values() if p is not None)
    return xs


def unsupported_sentences(system: SystemCallOutput) -> list[str]:
    """Note sentences that cite no fact, cite a fact the profile does not have, or carry a number none of their
    cited facts supports (§11.6, §15.3)."""
    facts = system.fact_index()
    bad = []
    for line in system.note:
        cited = [facts[i] for i in line.fact_ids if i in facts]
        if not line.fact_ids or len(cited) != len(line.fact_ids):
            bad.append(line.text)
            continue
        anchors = [a for f in cited for a in _anchors(f)]
        for cands in numbers_in(line.text):
            if not any(abs(c - a) <= 0.02 * max(abs(a), 1.0) for c in cands for a in anchors):
                bad.append(line.text)
                break
    return bad


def note_quality(
    system: SystemCallOutput, gold: GoldCall, *, alignment: Alignment | None = None, catalog: Catalog | None = None
) -> tuple[MetricResult, MetricResult]:
    """(completeness: gold note facts the note cites; supported: share of sentences that are supported).

    A run that produced no note (the eval task skips the note stage) is not measured, rather than scored 0."""
    if not system.note:
        return MetricResult(value=None, misses=["no meeting note in this run"]), MetricResult(value=None)
    catalog = catalog or load_catalog()
    al = _alignment(system, gold, alignment)
    cited: set[FactKey] = set()
    for line in system.note:
        for fid in line.fact_ids:
            k = al.key_of(fid, catalog)
            if k is not None:
                cited.add(k)
    wanted = default_note_facts(gold, catalog)
    hits = [f for f in wanted if f.key in cited]
    complete = ratio(len(hits), len(wanted), [f"note does not mention {f.fact_id}" for f in wanted if f.key not in cited])
    bad = unsupported_sentences(system)
    supported = ratio(len(system.note) - len(bad), len(system.note), [f"unsupported: {t}" for t in bad])
    return complete, supported


# --------------------------------------------------------------------------- discourse and ASR


def mention_accuracy(system: SystemCallOutput, gold: GoldCall, *, alignment: Alignment | None = None) -> MetricResult:
    al = _alignment(system, gold, alignment)
    hits, total, misses = 0, 0, []
    for gm in gold.mentions:
        total += 1
        sm = next(
            (m for m in system.mentions if m.segment_id == gm.segment_id and m.char_start < gm.char_end and gm.char_start < m.char_end),
            None,
        )
        if sm is None:
            misses.append(f"{gm.segment_id} '{gm.surface}': no mention")
            continue
        mapped = al.entity_map.get(sm.entity_id or "", sm.entity_id)
        if mapped == gm.entity_id or sm.entity_id == gm.entity_id or (gm.entity_id and gold.entity_map.get(gm.entity_id) == sm.entity_id):
            hits += 1
        else:
            misses.append(f"{gm.segment_id} '{gm.surface}': resolved to {sm.entity_id}, gold {gm.entity_id}")
    return ratio(hits, total, misses)


def topic_recall(system: SystemCallOutput, gold: GoldCall) -> MetricResult:
    hits, total, misses = 0, 0, []
    tagged = {g: set(segs) for g, segs in system.topic_index.items()}
    for seg, groups in gold.topic_labels.items():
        for g in groups:
            total += 1
            if seg in tagged.get(g, set()):
                hits += 1
            else:
                misses.append(f"{seg} not indexed under {g}")
    return ratio(hits, total, misses)


_ASR_METRIC_NAMES: dict[str, tuple[str, ...]] = {
    "numeric_wer": ("numeric_wer", "wer_numeric", "numeric_token_wer"),
    "der": ("der", "diarization_error_rate"),
    "role_accuracy": ("role_accuracy", "speaker_role_accuracy"),
}


def asr_metrics(reference: Any, hypothesis: Any) -> dict[str, float | None]:
    """Numeric-token WER, DER and speaker-role accuracy, passed through from ``advisor_intel.ingest.metrics``
    (§15.3 ASR and diarization rows). Empty when ingestion is not installed."""
    import importlib

    try:
        mod = importlib.import_module("advisor_intel.ingest.metrics")
    except ImportError:
        return {}
    out: dict[str, float | None] = {}
    for name, candidates in _ASR_METRIC_NAMES.items():
        fn = next((getattr(mod, c) for c in candidates if callable(getattr(mod, c, None))), None)
        if fn is None:
            out[name] = None
            continue
        v = fn(reference, hypothesis)
        out[name] = None if v is None else float(v)
    return out


# --------------------------------------------------------------------------- calibration and conformal summaries


@dataclass
class LabelRow:
    call_id: str
    group: str
    slot_id: str
    tier: str
    p: float
    y: int
    features: dict[str, float | None]
    auto_accepted: bool


def label_rows(records: Sequence[EvalRecord], *, cache: _AlignCache | None = None) -> list[LabelRow]:
    """One (p_correct, label) row per fact the pipeline extracted, labelled against gold (§9.3)."""
    cache = cache or _AlignCache()
    rows = []
    for r in records:
        for p in cache.get(r.system, r.gold).pairs:
            s = p.system
            if s is None or not s.extracted or s.p_correct is None or s.status == "not_discussed":
                continue
            y = fact_label(p)
            if y is None:
                continue
            rows.append(
                LabelRow(
                    call_id=r.gold.call_id,
                    group=p.group,
                    slot_id=p.slot.slot_id,
                    tier=p.tier,
                    p=float(s.p_correct),
                    y=y,
                    features=dict(s.features) | {"self_score": s.self_score},
                    auto_accepted=s.auto_accepted,
                )
            )
    return rows


def ece(p: Sequence[float], y: Sequence[int], n_bins: int = N_ECE_BINS) -> float | None:
    """Expected calibration error over equal-mass bins (§9.4: 15 bins)."""
    if len(p) == 0:
        return None
    order = np.argsort(np.asarray(p, dtype=float), kind="stable")
    ps, ys = np.asarray(p, dtype=float)[order], np.asarray(y, dtype=float)[order]
    n = len(ps)
    return float(sum(len(b) / n * abs(ps[b].mean() - ys[b].mean()) for b in np.array_split(np.arange(n), min(n_bins, n)) if len(b)))


def brier(p: Sequence[float], y: Sequence[int]) -> float | None:
    if len(p) == 0:
        return None
    a, b = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    return float(np.mean((a - b) ** 2))


COLD_START_WEIGHTS = {"entail_score": 0.4, "consistency": 0.3, "self_score": 0.2, "asr_mean_conf": 0.1}  # §9.4


def cold_start_scores(rows: Sequence[LabelRow]) -> np.ndarray:
    """§9.4's cold-start score per row; missing features take their slot group's mean (else the overall mean, else 0.5)."""
    out = np.zeros(len(rows))
    for feat, w in COLD_START_WEIGHTS.items():
        vals = np.array([np.nan if r.features.get(feat) is None else float(r.features[feat]) for r in rows], dtype=float)  # type: ignore[arg-type]
        overall = float(np.nanmean(vals)) if np.isfinite(vals).any() else 0.5
        by_group: dict[str, float] = {}
        for g in {r.group for r in rows}:
            gv = vals[[i for i, r in enumerate(rows) if r.group == g]]
            by_group[g] = float(np.nanmean(gv)) if np.isfinite(gv).any() else overall
        filled = np.array([v if np.isfinite(v) else by_group[r.group] for v, r in zip(vals, rows)])
        out += w * filled
    return np.clip(out, 0.0, 1.0)


def cold_start_baseline(rows: Sequence[LabelRow], *, folds: int = 5) -> np.ndarray:
    """The cold-start calibrator's predictions: isotonic regression on the cold-start score, cross-fitted by call
    so no call's labels calibrate its own facts (§9.4: split by call, never by fact)."""
    from sklearn.isotonic import IsotonicRegression

    s = cold_start_scores(rows)
    y = np.array([r.y for r in rows], dtype=float)
    calls = sorted({r.call_id for r in rows})
    if len(calls) < 2 or len(set(y.tolist())) < 2:
        return s
    k = min(folds, len(calls))
    fold_of = {c: i % k for i, c in enumerate(calls)}
    out = np.empty(len(rows))
    for f in range(k):
        test = np.array([fold_of[r.call_id] == f for r in rows])
        train = ~test
        if not train.any() or len(set(y[train].tolist())) < 2:
            out[test] = s[test]
            continue
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(s[train], y[train])
        out[test] = iso.predict(s[test])
    return out


def calibration_summary(rows: Sequence[LabelRow], *, min_group: int = 1) -> dict[str, Any]:
    """ECE pooled and per slot group, Brier, and the improvement over the cold-start baseline (§15.3)."""
    p = [r.p for r in rows]
    y = [r.y for r in rows]
    by_group: dict[str, float | None] = {}
    counts: dict[str, int] = {}
    for g in sorted({r.group for r in rows}):
        gr = [r for r in rows if r.group == g]
        counts[g] = len(gr)
        if len(gr) >= min_group:
            by_group[g] = ece([r.p for r in gr], [r.y for r in gr])
    b = brier(p, y)
    b0 = brier(cold_start_baseline(rows).tolist(), y) if rows else None
    return {
        "ece": ece(p, y),
        "ece_by_group": by_group,
        "ece_counts": counts,
        "brier": b,
        "brier_cold_start": b0,
        "brier_improvement": (1.0 - b / b0) if (b is not None and b0) else None,
        "n_labelled_facts": len(rows),
    }


def wrong_auto_accept_share(records: Sequence[EvalRecord], *, cache: _AlignCache | None = None) -> MetricResult:
    """Share of calls with any wrong auto-accepted fact (§9.5 guarantee; target ≤ α)."""
    cache = cache or _AlignCache()
    hits, misses = 0, []
    for r in records:
        pairs = cache.get(r.system, r.gold).pairs
        wrong = [p.label for p in pairs if p.system is not None and p.system.auto_accepted and fact_label(p) == 0]
        if wrong:
            hits += 1
            misses.append(f"{r.gold.call_id}: wrong auto-accepted {', '.join(wrong[:3])}")
    return ratio(hits, len(records), misses)


def auto_accept_rate(records: Sequence[EvalRecord]) -> MetricResult:
    """Share of extracted stated or implied facts the conformal filter auto-accepted (target ≥ 40%)."""
    facts = [f for r in records for f in r.system.facts if f.extracted and f.status in ("stated", "implied")]
    return ratio(sum(f.auto_accepted for f in facts), len(facts))


# --------------------------------------------------------------------------- forecast and FNA outputs


def _takes_seed(fn: Callable[..., Any]) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "seed" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def pos_error(records: Sequence[EvalRecord], *, gold_pos: Callable[..., float | None] | None = None) -> dict[str, float | None]:
    """|PoS(system profile) − PoS(gold profile)| in PoS points: median and p90 over calls (§15.3). The gold PoS is the
    gold call's own, else ``gold_pos(gold)`` — given the system run's forecast seed when it takes ``seed``, so both
    forecasts use the same market draws and the difference is the extraction's."""
    errs = []
    with_seed = gold_pos is not None and _takes_seed(gold_pos)
    for r in records:
        if r.system.pos is None:
            continue
        g = r.gold.pos
        if g is None and gold_pos is not None:
            g = gold_pos(r.gold, seed=r.system.forecast_seed) if with_seed else gold_pos(r.gold)
        if g is None:
            continue
        errs.append(abs(r.system.pos - g) * 100.0)
    if not errs:
        return {"pos_error_median": None, "pos_error_p90": None, "pos_error_n": 0}
    return {"pos_error_median": float(np.median(errs)), "pos_error_p90": float(np.quantile(errs, 0.9)), "pos_error_n": float(len(errs))}


def forecast_gold_pos(*, mode: str = "preview") -> Callable[..., float | None] | None:
    """The PoS of a gold call's profile through the installed forecast engine (§15.3's forecast metric): the gold
    FnaDocument seeded as a profile (``binding.load_prior``) and forecast like the system's preview run, on the
    system run's seed when given. None when the forecast is not installed."""
    run = lazy_attr("advisor_intel.forecast.run", "run_forecast")
    if run is None:
        return None
    cache: dict[tuple[str, int | None], float | None] = {}

    def gold_pos(gold: GoldCall, seed: int | None = None) -> float | None:
        if gold.document is None:
            return None
        key = (gold.call_id, seed)
        if key not in cache:
            from ..binding import load_prior
            from ._runtime import run_sync

            profile = load_prior(gold.document, None, call_id=gold.call_id, run_id=f"gold-{gold.call_id}", anchor_date=gold.anchor_date)
            p = get(run_sync(run(profile, base_document=gold.document, mode=mode, seed=seed)), "pos")
            cache[key] = float(p) if isinstance(p, (int, float)) else None
        return cache[key]

    return gold_pos


_PERSON_NEEDS = (
    ("life", "death"),
    ("disability", "disability"),
    ("income-protection", "incomeProtection"),
    ("severe-illness", "severeIllness"),
    ("funeral", "funeral"),
)


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(f"cannot read an analysis of type {type(obj).__name__}")


def _need_row(need: Any, shortfall: Any, status: Any) -> dict[str, Any]:
    return {"need": float(need or 0), "shortfall": float(shortfall or 0), "status": status}


def need_table(analysis: Any) -> dict[str, dict[str, Any]]:
    """Per-need {need, shortfall, status} from an ``analyse()`` result (TypeScript JSON or the Python port)."""
    a = _as_dict(analysis)
    out: dict[str, dict[str, Any]] = {}
    for person, pr in (a.get("persons") or {}).items():
        if not isinstance(pr, dict):
            continue
        for area, field_name in _PERSON_NEEDS:
            n = pr.get(field_name)
            if isinstance(n, dict) and "shortfall" in n:
                out[f"{area}:{person}"] = _need_row(n.get("need"), n.get("shortfall"), n.get("status"))
        e = pr.get("estate")
        if isinstance(e, dict) and "liquidityShortfall" in e:
            sf = float(e.get("liquidityShortfall") or 0)
            out[f"estate:{person}"] = _need_row(e.get("cashRequired"), sf, "shortfall" if sf > 0.5 else "covered")
        ret = pr.get("retirement")
        if isinstance(ret, dict) and "shortfall" in ret:
            out[f"retirement:{person}"] = _need_row(ret.get("requiredCapital"), ret.get("shortfall"), ret.get("status"))
    for key, field_name in (("education", "educationTotal"), ("emergency", "emergency")):
        n = a.get(field_name)
        if isinstance(n, dict) and "shortfall" in n:
            out[key] = _need_row(n.get("need"), n.get("shortfall"), n.get("status"))
    return out


def default_analyse() -> Callable[[FnaDocument], Any] | None:
    """The ported FNA engine's ``analyse`` (§12.7), when installed."""
    return lazy_attr("advisor_intel.fna_engine.analysis", "analyse")


def _analyse_on(analyse: Callable[..., Any], doc: FnaDocument, day: Any) -> Any:
    """``analyse(doc)`` as of the call date when the engine takes one (the Python port's ``today=``): ages, tax years
    and horizons are the call's, not the day the eval runs."""
    try:
        params = inspect.signature(analyse).parameters
    except (TypeError, ValueError):
        params = {}  # type: ignore[assignment]
    if "today" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return analyse(doc, today=day)
    return analyse(doc)


def fna_agreement(records: Sequence[EvalRecord], *, analyse: Callable[[FnaDocument], Any] | None = None) -> dict[str, Any]:
    """Need-status agreement and median relative shortfall error per need, system vs gold FnaDocument run through
    the FNA engine as of the call date (§15.3). The error is |S_system − S_gold| / max(N_gold, 1), as in §10.2."""
    analyse = analyse or default_analyse()
    if analyse is None:
        return {}
    agree, total = 0, 0
    errors: dict[str, list[float]] = defaultdict(list)
    for r in records:
        if r.gold.document is None or r.system.document is None:
            continue
        gt = need_table(_analyse_on(analyse, r.gold.document, r.gold.anchor_date))
        st = need_table(_analyse_on(analyse, FnaDocument.model_validate(r.system.document), r.gold.anchor_date))
        for k, g in gt.items():
            s = st.get(k)
            total += 1
            if s is not None and s["status"] == g["status"]:
                agree += 1
            if s is not None:
                errors[k.split(":")[0]].append(abs(s["shortfall"] - g["shortfall"]) / max(g["need"], 1.0))
    if not total:
        return {}
    all_err = [e for es in errors.values() for e in es]
    return {
        "need_status_agreement": agree / total,
        "shortfall_error_median": float(np.median(all_err)) if all_err else None,
        "shortfall_error_by_need": {k: float(np.median(v)) for k, v in sorted(errors.items())},
        "n_needs": total,
    }


# --------------------------------------------------------------------------- operations


def review_minutes_per_hour(reviews: Iterable[tuple[float, float]]) -> MetricResult:
    """Median adviser review time normalised to a 60-minute call, from (review_seconds, call_minutes) pairs (target ≤ 10)."""
    xs = [sec / 60.0 * (60.0 / mins) for sec, mins in reviews if mins and mins > 0 and sec is not None]
    return MetricResult(value=float(np.median(xs)) if xs else None, total=len(xs))


def cost_per_call_minute(records: Sequence[EvalRecord] | Sequence[SystemCallOutput]) -> float | None:
    """Total USD over total call minutes (§15.3 operations; Braintrust's per-run cost is the production source)."""
    cost, minutes = 0.0, 0.0
    for r in records:
        s = r.system if isinstance(r, EvalRecord) else r
        if "cost_usd" in s.usage and s.call_minutes:
            cost += s.usage["cost_usd"]
            minutes += s.call_minutes
    return cost / minutes if minutes else None


def voi_usefulness(ratings: Iterable[float]) -> float | None:
    """Mean adviser rating (1–5) of the top five review items (§15.3 VOI, human review)."""
    xs = [float(r) for r in ratings]
    return float(np.mean(xs)) if xs else None


# --------------------------------------------------------------------------- summary over a whole experiment


def summary_scores(
    records: Sequence[EvalRecord],
    *,
    analyse: Callable[[FnaDocument], Any] | None = None,
    gold_pos: Callable[[GoldCall], float | None] | None = None,
) -> dict[str, Any]:
    """Every §15.3 summary metric over an experiment (flat keys; nested dicts for per-group and per-need values)."""
    cache = _AlignCache()
    rows = label_rows(records, cache=cache)
    out: dict[str, Any] = calibration_summary(rows)
    out["wrong_auto_accept_share"] = wrong_auto_accept_share(records, cache=cache).value
    out["auto_accept_rate"] = auto_accept_rate(records).value
    out.update(pos_error(records, gold_pos=gold_pos))
    out.update(fna_agreement(records, analyse=analyse))
    crit_calls, crit_total, crit_flagged = 0, 0, 0
    catalog = load_catalog()
    for r in records:
        al = cache.get(r.system, r.gold)
        misses = critical_misses(r.system, r.gold, alignment=al)
        if misses:
            crit_calls += 1
            flagged = flagged_keys(r.system, al, catalog)
            crit_total += len(misses)
            crit_flagged += sum(p.key in flagged for p in misses)
    out["critical_miss_rate"] = crit_calls / len(records) if records else None
    out["flag_recall"] = crit_flagged / crit_total if crit_total else None
    out["interval_coverage_vague"] = pooled(
        value_coverage(r.system, r.gold, alignment=cache.get(r.system, r.gold), vague_only=True) for r in records
    ).value
    by_kind: dict[str, list[MetricResult]] = defaultdict(list)
    for r in records:
        by_kind[r.transcript_kind].append(value_coverage(r.system, r.gold, alignment=cache.get(r.system, r.gold)))
    cov_h, cov_a = pooled(by_kind.get("human", [])).value, pooled(by_kind.get("asr", [])).value
    out["asr_human_gap"] = (cov_h - cov_a) if (cov_h is not None and cov_a is not None) else None
    out["cost_per_call_minute"] = cost_per_call_minute(records)
    out["note_unsupported_sentences"] = float(sum(len(unsupported_sentences(r.system)) for r in records))
    groups: dict[str, dict[str, list[MetricResult]]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        al = cache.get(r.system, r.gold)
        for g in {p.group for p in al.pairs}:
            groups[g]["status_accuracy"].append(status_accuracy(r.system, r.gold, groups=[g], alignment=al))
            groups[g]["slot_recall"].append(slot_recall(r.system, r.gold, groups=[g], alignment=al))
            groups[g]["value_coverage"].append(value_coverage(r.system, r.gold, groups=[g], alignment=al))
    out["by_group"] = {
        g: {m: pooled(rs).value for m, rs in ms.items()} | {"n_facts": float(sum(x.total for x in ms["status_accuracy"]))}
        for g, ms in sorted(groups.items())
    }
    return out


# --------------------------------------------------------------------------- per-item bundles and Braintrust adapters


def item_metrics(system: SystemCallOutput, gold: GoldCall, *, catalog: Catalog | None = None) -> dict[str, MetricResult]:
    """Every per-item metric for one call, keyed by its score name (tier A variants end in ``_tier_a``)."""
    catalog = catalog or load_catalog()
    al = align(system, gold, catalog=catalog)
    out: dict[str, MetricResult] = {}
    for name, fn in (
        ("status_accuracy", status_accuracy),
        ("slot_recall", slot_recall),
        ("value_coverage", value_coverage),
        ("grounding", grounding),
        ("span_f1", span_f1),
        ("attribution", attribution_accuracy),
    ):
        out[name] = fn(system, gold, alignment=al)
        out[f"{name}_tier_a"] = fn(system, gold, tier="A", alignment=al)
    out["note_completeness"], out["note_supported"] = note_quality(system, gold, alignment=al, catalog=catalog)
    out["mention_accuracy"] = mention_accuracy(system, gold, alignment=al)
    out["topic_recall"] = topic_recall(system, gold)
    tm = system.transcript_metrics
    if tm.get("numeric_wer") is not None:
        out["numeric_word_accuracy"] = MetricResult(value=max(0.0, 1.0 - float(tm["numeric_wer"])), total=1)  # type: ignore[arg-type]
    if tm.get("der") is not None:
        out["diarization_accuracy"] = MetricResult(value=max(0.0, 1.0 - float(tm["der"])), total=1)  # type: ignore[arg-type]
    if tm.get("role_accuracy") is not None:
        out["role_accuracy"] = MetricResult(value=float(tm["role_accuracy"]), total=1)  # type: ignore[arg-type]
    return out


def _parse(output: Any, expected: Any) -> tuple[SystemCallOutput, GoldCall]:
    system = output if isinstance(output, SystemCallOutput) else SystemCallOutput.model_validate(output)
    gold = expected if isinstance(expected, GoldCall) else GoldCall.model_validate(expected)
    return system, gold


class _ItemCache:
    """``item_metrics`` per (output, expected) object pair, shared by the scorers of one item (Braintrust runs every
    scorer on the same objects, possibly in threads); identity-checked so a reused ``id`` never returns stale data."""

    def __init__(self, size: int = 64) -> None:
        self._d: OrderedDict[tuple[int, int], tuple[Any, Any, dict[str, MetricResult]]] = OrderedDict()
        self._lock = threading.Lock()
        self.size = size

    def get(
        self, output: Any, expected: Any, fn: Callable[[SystemCallOutput, GoldCall], dict[str, MetricResult]]
    ) -> dict[str, MetricResult]:
        key = (id(output), id(expected))
        with self._lock:
            hit = self._d.get(key)
            if hit is not None and hit[0] is output and hit[1] is expected:
                return hit[2]
        system, gold = _parse(output, expected)
        res = fn(system, gold)
        with self._lock:
            self._d[key] = (output, expected, res)
            while len(self._d) > self.size:
                self._d.popitem(last=False)
        return res


_ITEM_CACHE = _ItemCache()


class ItemScorer:
    """A Braintrust per-item scorer over one or more metrics; returns one ``Score`` per metric (None skips)."""

    def __init__(
        self, name: str, metrics: Sequence[str], fn: Callable[[SystemCallOutput, GoldCall], dict[str, MetricResult]] | None = None
    ) -> None:
        self.name = name
        self.metrics = list(metrics)
        self._fn = fn
        self.__name__ = name

    def _name(self) -> str:  # Braintrust reads the scorer name from here
        return self.name

    def compute(self, output: Any, expected: Any) -> dict[str, MetricResult]:
        if self._fn is not None:
            system, gold = _parse(output, expected)
            allm = self._fn(system, gold)
        else:
            allm = _ITEM_CACHE.get(output, expected, lambda s, g: item_metrics(s, g))
        return {m: allm[m] for m in self.metrics if m in allm}

    def __call__(self, input: Any, output: Any, expected: Any = None, **kwargs: Any) -> list[Any]:
        from braintrust import Score

        if expected is None or output is None:
            return [Score(name=m, score=None) for m in self.metrics]
        res = self.compute(output, expected)
        scores = []
        for m in self.metrics:
            r = res.get(m)
            v = None if r is None or r.value is None else min(1.0, max(0.0, float(r.value)))
            scores.append(Score(name=m, score=v, metadata={"hits": r.hits, "total": r.total, "misses": r.misses[:20]} if r else {}))
        return scores


status_accuracy_scorer = ItemScorer("status_accuracy", ["status_accuracy", "status_accuracy_tier_a"])
slot_recall_scorer = ItemScorer("slot_recall", ["slot_recall", "slot_recall_tier_a"])
value_coverage_scorer = ItemScorer("value_coverage", ["value_coverage", "value_coverage_tier_a"])
grounding_scorer = ItemScorer("grounding", ["grounding", "grounding_tier_a", "span_f1", "span_f1_tier_a"])
attribution_scorer = ItemScorer("attribution", ["attribution", "attribution_tier_a"])
note_scorer = ItemScorer("meeting_note", ["note_completeness", "note_supported"])
discourse_scorer = ItemScorer("discourse", ["mention_accuracy", "topic_recall"])
asr_scorer = ItemScorer("asr", ["numeric_word_accuracy", "diarization_accuracy", "role_accuracy"])

ITEM_SCORERS: list[ItemScorer] = [
    status_accuracy_scorer,
    slot_recall_scorer,
    value_coverage_scorer,
    grounding_scorer,
    attribution_scorer,
    note_scorer,
    discourse_scorer,
    asr_scorer,
]

ITEM_SCORE_NAMES: list[str] = [m for s in ITEM_SCORERS for m in s.metrics]
TIER_A_METRICS: list[str] = [m for m in ITEM_SCORE_NAMES if m.endswith("_tier_a")]


def records_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[EvalRecord]:
    """EvalRecords from logged rows ({output, expected, metadata}), e.g. Braintrust ``EvalResult``s or report items."""
    out = []
    for row in rows:
        output, expected = get(row, "output"), get(row, "expected")
        if output is None or expected is None or get(row, "error") is not None:
            continue
        system, gold = _parse(output, expected)
        out.append(EvalRecord(system=system, gold=gold, metadata=dict(get(row, "metadata") or {})))
    return out
