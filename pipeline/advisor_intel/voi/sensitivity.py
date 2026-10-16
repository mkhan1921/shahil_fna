"""Sensitivity runs — ``sensitivity(profile, slots)`` (spec §10.2).

For each candidate fact (tier A and B, plus any ``contradicted`` or constraint-failing fact, plus tier A
fields on the expected-fact list that the call left without a fact):

- **PoS view** (the injected ``pos_runs``, the forecast engine's reduced runs with common random numbers):
  ``I_pos = |PoS(f = q90) − PoS(f = q10)|`` and ``I_extract = (1 − p_correct) · |PoS(f) − PoS(f ~ prior)|``;
- **FNA view** (the injected ``analyse``, the deterministic FNA engine): the median-materialised document
  with f moved to each end; for every need k (life cover, disability, income protection, severe illness,
  funeral, estate liquidity, education, emergency fund, retirement) ``I_fna = max_k |S_k(q90) − S_k(q10)| /
  max(N_k(q50), 1)``, and a status flip (covered ↔ partial ↔ shortfall) between the ends sets
  ``forced = 1``;
- ``I_content = max(I_pos, I_fna)``.

"Ends" are q10 and q90 of the fact's distribution (its slot's population prior for a field with no
value); for categorical and boolean facts they are the outcomes that carry at least 10% of the mass,
the two most different outcomes standing in for q10/q90. A caller can pass the forecast engine's own
``fact_extremes`` so both views move the fact to the same ends.

``analyse(doc)`` may return the engine's ``Analysis`` model or a plain dict of the same shape. Bind
``today`` before passing it in: ``functools.partial(fna_engine.analysis.analyse, today=anchor)``.
"""

from __future__ import annotations

import copy
import inspect
import logging
import math
from typing import Any, Awaitable, Callable, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel

from .. import binding, stats
from ..models.dists import Point
from ..models.facts import Fact, Provenance
from ..models.fna import FnaDocument
from ..models.profile import Profile
from ..schema.slots import Catalog, SlotDef, load_catalog
from ..verify.coverage import catalog_list_item, expected_facts

log = logging.getLogger(__name__)

MIN_OUTCOME_MASS = 0.1  # categorical / boolean ends: outcomes inside the central 80%, like q10–q90
N_PATHS = 1000
PERSON_NEEDS: tuple[tuple[str, str], ...] = (
    ("life", "death"),
    ("disability", "disability"),
    ("income-protection", "incomeProtection"),
    ("severe-illness", "severeIllness"),
    ("funeral", "funeral"),
)
NEED_LABELS: dict[str, str] = {
    "life": "life cover",
    "disability": "disability cover",
    "income-protection": "income protection",
    "severe-illness": "severe illness cover",
    "funeral": "funeral cover",
    "estate": "estate liquidity",
    "education": "education funding",
    "emergency": "emergency fund",
    "retirement": "retirement",
}
Status = Literal["covered", "partial", "shortfall", "na"]
Analyse = Callable[[FnaDocument], Any]
PosRuns = Callable[..., "Mapping[str, Any] | Awaitable[Mapping[str, Any]]"]
Extremes = Callable[[Fact, SlotDef], tuple[Any, Any]]


# --------------------------------------------------------------------------- reading the engine's result


class NeedPoint(BaseModel):
    shortfall: float
    need: float
    status: Status


def get_field(obj: Any, key: str, default: Any = None) -> Any:
    """A field of the engine result, whether it is the ``Analysis`` model or a plain dict of the same shape."""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def status_for(funded: float, need: float) -> Status:
    """The FNA app's ``statusFor`` (lib/fna/analysis.ts)."""
    if need <= 0.5:
        return "na"
    if funded >= 0.95:
        return "covered"
    if funded >= 0.6:
        return "partial"
    return "shortfall"


def _need(obj: Any) -> NeedPoint | None:
    if obj is None:
        return None
    return NeedPoint(
        shortfall=float(get_field(obj, "shortfall", 0.0) or 0.0),
        need=float(get_field(obj, "need", 0.0) or 0.0),
        status=get_field(obj, "status", "na"),
    )


def need_table(analysis: Any) -> dict[str, NeedPoint]:
    """The nine needs per person (``"life:client"``) and per household (``"education"``) from ``analyse()``.

    Estate liquidity has no status in the engine's result, so it gets the app's rule on cash available
    against cash required; retirement uses the capital shortfall against the capital required.
    """
    out: dict[str, NeedPoint] = {}
    persons = get_field(analysis, "persons", {}) or {}
    for key in ("client", "spouse"):
        p = get_field(persons, key)
        if p is None:
            continue
        for area, attr in PERSON_NEEDS:
            n = _need(get_field(p, attr))
            if n is not None:
                out[f"{area}:{key}"] = n
        est = get_field(p, "estate")
        if est is not None:
            required = float(get_field(est, "cashRequired", 0.0) or 0.0)
            available = float(get_field(est, "cashAvailable", 0.0) or 0.0)
            funded = min(1.0, available / required) if required > 0 else 1.0
            out[f"estate:{key}"] = NeedPoint(
                shortfall=float(get_field(est, "liquidityShortfall", 0.0) or 0.0), need=required, status=status_for(funded, required)
            )
        ret = get_field(p, "retirement")
        if ret is not None:
            out[f"retirement:{key}"] = NeedPoint(
                shortfall=float(get_field(ret, "shortfall", 0.0) or 0.0),
                need=float(get_field(ret, "requiredCapital", 0.0) or 0.0),
                status=get_field(ret, "status", "na"),
            )
    for key, attr in (("education", "educationTotal"), ("emergency", "emergency")):
        n = _need(get_field(analysis, attr))
        if n is not None:
            out[key] = n
    return out


def need_label(key: str) -> str:
    """'life:client' → "the client's life cover"; 'education' → "the household's education funding"."""
    area, _, person = key.partition(":")
    label = NEED_LABELS.get(area, area)
    return f"the {person}'s {label}" if person else f"the household's {label}"


def _settled(status: str) -> str:
    """'na' (no need) and 'covered' both leave nothing to recommend, so moving between them is no flip."""
    return "covered" if status == "na" else status


# --------------------------------------------------------------------------- candidates and ends


class SensitivityRow(BaseModel):
    fact_id: str
    slot_id: str
    tier: str
    reasons: list[str]  # why it is a candidate
    virtual: bool = False  # an expected tier A field with no fact in the profile
    ends: list[Any] = []  # the values the fact was moved to
    pos_base: float | None = None
    pos_q10: float | None = None
    pos_q90: float | None = None
    pos_prior: float | None = None
    p_correct: float = 1.0
    i_pos: float = 0.0
    i_fna: float = 0.0
    i_content: float = 0.0
    i_extract: float = 0.0
    status_flip: bool = False
    flips: list[str] = []  # "life:client partial→shortfall"
    top_need: str | None = None  # the need the FNA view moved most
    top_need_change: float = 0.0  # |ΔS| of that need, in rand (or rand a month for monthly needs)
    note: str = ""


def candidates(profile: Profile, catalog: Catalog, *, include_missing: bool = True) -> list[tuple[Fact, list[str], bool]]:
    """(fact, reasons, virtual) for every fact §10.2 looks at, plus missing tier A fields as virtual facts."""
    out: list[tuple[Fact, list[str], bool]] = []
    for f in profile.facts.values():
        if f.review.status == "rejected":
            continue
        slot = catalog.slot(f.slot_id)
        reasons = []
        if slot.sensitivity_tier in ("A", "B") and slot.value_type != "text":
            reasons.append(f"tier {slot.sensitivity_tier}")
        if f.status == "contradicted":
            reasons.append("contradicted")
        if f.verification.get("constraint") == "fail":
            reasons.append("hard constraint")
        if reasons:
            out.append((f, reasons, False))
    if include_missing:
        for item in expected_facts(profile, catalog):
            if item.tier != "A" or item.fact_id in profile.facts or catalog_list_item(item):
                continue
            slot = catalog.slot(item.slot_id)
            if slot.value_type == "text":
                continue
            person = item.instance if slot.per_person else None
            virtual = Fact(
                fact_id=item.fact_id,
                slot_id=slot.slot_id,
                instance_key=item.instance,
                fna_path=slot.fna_path_for(person),
                source="call",
                status="not_discussed",
                p_correct=1.0,
                provenance=Provenance(run_id=profile.run_id),
            )
            out.append((virtual, ["tier A", "missing"], True))
    return out


def fact_ends(fact: Fact, slot: SlotDef, *, min_mass: float = MIN_OUTCOME_MASS) -> list[Any]:
    """q10 and q90 (numeric), or the outcomes with at least ``min_mass`` (categorical, boolean); [] for no spread."""
    dist = fact.value.dist if fact.value is not None else slot.population_prior
    if dist is None or slot.value_type == "text":
        return []
    try:
        if slot.value_type in ("boolean", "probability") or stats.is_boolean(dist):
            p = stats.p_true(dist)
            outs = [v for v, mass in ((False, 1 - p), (True, p)) if mass >= min_mass]
            return outs if len(outs) == 2 else []
        if slot.value_type in ("category", "count") or stats.is_categorical(dist):
            probs = sorted(stats.categorical_probs(dist).items(), key=lambda kv: -kv[1])
            outs = [k for k, p in probs if p >= min_mass]
            return outs if len(outs) >= 2 else []
        lo, hi = stats.quantile(dist, 0.1), stats.quantile(dist, 0.9)
    except (TypeError, ValueError):
        return []
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi - lo <= 1e-9 * max(1.0, abs(hi)):
        return []
    return [lo, hi]


def doc_path(profile: Profile, fact: Fact, slot: SlotDef) -> str | None:
    """The concrete FnaDocument path the median document holds this fact at (rows by their stable row id)."""
    if not slot.exported:
        return None
    if fact.slot_id == "expenses.items[]":
        return f"expenses.items[{fact.instance_key}]"
    if slot.per_person:
        return slot.fna_path_for(fact.instance_key or "client")
    if slot.per_entity and fact.instance_key:
        return slot.fna_path_for(None, binding.row_id_for(profile, fact.instance_key))
    return slot.fna_path if "[]" not in slot.fna_path else None


def _with_value(doc: dict[str, Any], path: str, slot: SlotDef, value: Any) -> FnaDocument:
    d = copy.deepcopy(doc)
    binding.set_path(d, path, binding.to_fna_value(slot, Point(value=value), "median"))
    return FnaDocument.model_validate(d)


# --------------------------------------------------------------------------- the runs


async def _call_pos_runs(pos_runs: PosRuns, profile: Profile, fact_ids: list[str], n_paths: int, seed: int) -> Mapping[str, Any]:
    out = pos_runs(profile, fact_ids, n_paths=n_paths, seed=seed)
    if inspect.isawaitable(out):
        out = await out
    return out or {}


def _num(row: Any, key: str) -> float | None:
    v = get_field(row, key)
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def fna_view(base: Mapping[str, NeedPoint], ends: Sequence[Mapping[str, NeedPoint]]) -> tuple[float, bool, list[str], str | None, float]:
    """(I_fna, status flip, flips, top need, |ΔS| of the top need) over every pair of ends."""
    best, best_need, best_delta = 0.0, None, 0.0
    flips: list[str] = []
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            a, b = ends[i], ends[j]
            for k in set(a) & set(b):
                delta = abs(b[k].shortfall - a[k].shortfall)
                n50 = base[k].need if k in base else max(a[k].need, b[k].need)
                score = delta / max(n50, 1.0)
                if score > best:
                    best, best_need, best_delta = score, k, delta
                if _settled(a[k].status) != _settled(b[k].status):
                    flips.append(f"{k} {a[k].status}→{b[k].status}")
    return best, bool(flips), sorted(set(flips)), best_need, best_delta


async def sensitivity(
    profile: Profile,
    *,
    catalog: Catalog | None = None,
    analyse: Analyse | None = None,
    pos_runs: PosRuns | None = None,
    base_document: FnaDocument | None = None,
    extremes: Extremes | None = None,
    n_paths: int = N_PATHS,
    seed: int = 0,
    include_missing: bool = True,
    fact_ids: Iterable[str] | None = None,
) -> list[SensitivityRow]:
    """The §10.2 sensitivity table for the candidate facts (or ``fact_ids``)."""
    catalog = catalog or load_catalog(profile.schema_version)
    cands = candidates(profile, catalog, include_missing=include_missing)
    if fact_ids is not None:
        wanted = set(fact_ids)
        cands = [c for c in cands if c[0].fact_id in wanted]
    rows: dict[str, SensitivityRow] = {}
    for f, reasons, virtual in cands:
        rows[f.fact_id] = SensitivityRow(
            fact_id=f.fact_id,
            slot_id=f.slot_id,
            tier=catalog.slot(f.slot_id).sensitivity_tier,
            reasons=reasons,
            virtual=virtual,
            p_correct=1.0 if virtual else f.p_correct,
        )

    # PoS view: the forecast engine's reduced runs (common random numbers across facts).
    real = [f.fact_id for f, _, virtual in cands if not virtual]
    if pos_runs is not None and real:
        try:
            table = await _call_pos_runs(pos_runs, profile, real, n_paths, seed)
        except Exception as e:  # noqa: BLE001 - the FNA view still ranks if the forecast fails
            log.warning("sensitivity runs failed: %s", e)
            table = {}
        for fid in real:
            r, row = rows[fid], table.get(fid)
            if row is None:
                continue
            r.pos_base, r.pos_q10, r.pos_q90, r.pos_prior = (_num(row, k) for k in ("pos_base", "pos_q10", "pos_q90", "pos_prior"))
            if r.pos_q10 is not None and r.pos_q90 is not None:
                r.i_pos = abs(r.pos_q90 - r.pos_q10)
            if r.pos_base is not None and r.pos_prior is not None:
                r.i_extract = (1.0 - r.p_correct) * abs(r.pos_base - r.pos_prior)

    # FNA view: the median document with the fact moved to each end.
    if analyse is not None and cands:
        median = binding.materialise(profile, "median", base=base_document, catalog=catalog)
        doc = median.model_dump(mode="json")
        try:
            base_needs = need_table(analyse(median))
        except Exception as e:  # noqa: BLE001 - a document the engine cannot analyse leaves the PoS view
            log.warning("FNA analysis of the median document failed: %s", e)
            base_needs = None
        for f, _, _ in cands:
            if base_needs is None:
                break
            r = rows[f.fact_id]
            slot = catalog.slot(f.slot_id)
            path = doc_path(profile, f, slot)
            if path is None:
                r.note = "not in the FnaDocument: PoS view only"
                continue
            ends = list(extremes(f, slot)) if extremes is not None else fact_ends(f, slot)
            if len(ends) < 2:
                r.note = "no spread to move across"
                continue
            r.ends = ends
            try:
                tables = [need_table(analyse(_with_value(doc, path, slot, v))) for v in ends]
            except Exception as e:  # noqa: BLE001
                log.warning("FNA analysis failed for %s: %s", f.fact_id, e)
                r.note = f"FNA analysis failed: {e}"
                continue
            r.i_fna, r.status_flip, r.flips, r.top_need, r.top_need_change = fna_view(base_needs, tables)
    for r in rows.values():
        r.i_content = max(r.i_pos, r.i_fna)
    return list(rows.values())
