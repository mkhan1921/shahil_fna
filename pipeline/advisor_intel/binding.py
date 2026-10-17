"""Binding the probabilistic profile to the point-valued FnaDocument (spec §3.6).

- ``load_prior(fna, crm)`` seeds a run from the client's existing document: every non-empty field
  becomes a fact (``source="prior_fna"``, ``status="not_discussed"``, a ``Point`` value), entered
  through the reducer like any other op, and every list row seeds the entity registry with its FNA
  id, so "the Discovery policy" can resolve to an existing row.
- ``materialise(profile, mode)`` produces one FnaDocument: ``median`` for review and export, or
  ``draw`` for one simulation path (honouring the extraction-risk mixture of §12.2).
- ``sidecar(profile)`` carries what schema v2 has no place for — probabilities, evidence, status,
  change — keyed by ``fna_path``; ``export_bundle`` writes the reviewed median document as a
  ``shahil-fna`` file the app imports unchanged.

Concrete FnaDocument paths: ``client.dateOfBirth``, ``income.spouse.grossMonthly``,
``dependants[<row id>].tertiaryStartAge``, ``expenses.items[groceries]``,
``expenses.custom[<row id>].amount``, ``riskProfile.answers[horizon]``.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import numpy as np
from numpy.random import Generator

from . import stats
from .extract.reducer import apply_state_ops, make_ops
from .models import fna as F
from .models.crm import CrmRecord
from .models.dists import Dist, Point, Quantity
from .models.entities import Entity, EntityType
from .models.facts import Fact, Hypothesis, Provenance
from .models.profile import Profile
from .schema.slots import Catalog, SlotDef, fact_id_for, load_catalog

# --------------------------------------------------------------------------- paths

_TOKEN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[([^\]]*)\])?")
DICT_CONTAINERS = {"expenses.items", "riskProfile.answers"}
LIST_CONTAINERS = {"dependants", "retirementFunds", "assets", "liabilities", "policies", "goals", "expenses.custom"}
LIST_ROW_MODEL: dict[str, type[F._FnaModel]] = {
    "dependants": F.Dependant,
    "retirementFunds": F.RetirementFund,
    "assets": F.Asset,
    "liabilities": F.Liability,
    "policies": F.Policy,
    "goals": F.OtherGoal,
    "expenses.custom": F.CustomExpense,
}
# The FnaDocument list of each catalog list entity.
ENTITY_LIST: dict[str, str] = {
    "dependant": "dependants",
    "fund": "retirementFunds",
    "asset": "assets",
    "liability": "liabilities",
    "policy": "policies",
    "goal": "goals",
    "expense": "expenses.custom",
}
# The slot that identifies a row: a reviewer-removed identity fact removes the row (§3.6 "removed").
IDENTITY_FIELD: dict[str, str] = {
    "dependants": "relationship",
    "retirementFunds": "type",
    "assets": "type",
    "liabilities": "type",
    "policies": "type",
    "goals": "name",
    "expenses.custom": "label",
}


@dataclass(frozen=True)
class PathParts:
    container: str  # "client", "income.client", "dependants", "expenses.items" …
    key: str | None  # row id or dict key, when the container is a list or dict
    field: str | None  # the field within the row (None for dict entries)


def parse_path(path: str) -> PathParts:
    """Split a concrete FnaDocument path into container, row id or dict key, and field."""
    m = re.match(r"^(.*?)\[([^\]]*)\](?:\.(\w+))?$", path)
    if m:
        container, key, field = m.group(1), m.group(2), m.group(3)
        return PathParts(container, key, field)
    head, _, field = path.rpartition(".")
    return PathParts(head, None, field)


def get_path(doc: dict[str, Any], path: str) -> Any:
    pp = parse_path(path)
    node = _container(doc, pp.container)
    if pp.key is None:
        return node.get(pp.field) if isinstance(node, dict) else None
    if pp.container in DICT_CONTAINERS:
        return node.get(pp.key)
    row = next((r for r in node if r.get("id") == pp.key), None)
    return None if row is None else row.get(pp.field)


def set_path(doc: dict[str, Any], path: str, value: Any, *, anchor: date | None = None) -> None:
    """Set a value, creating a list row (with the container's defaults) when the id is new."""
    pp = parse_path(path)
    node = _container(doc, pp.container)
    if pp.key is None:
        node[pp.field] = value
        return
    if pp.container in DICT_CONTAINERS:
        node[pp.key] = value
        return
    row = next((r for r in node if r.get("id") == pp.key), None)
    if row is None:
        row = new_row(pp.container, pp.key, anchor)
        node.append(row)
    row[pp.field] = value


def new_row(container: str, key: str, anchor: date | None = None) -> dict[str, Any]:
    """A list row with the app's defaults. Defaults the app takes from the clock (a goal five years out) are
    taken from the call date instead, so a run's output does not depend on the day it runs."""
    row = LIST_ROW_MODEL[container]().model_dump(mode="json")
    row["id"] = key
    if container == "goals" and anchor is not None:
        row["targetYear"] = anchor.year + 5
    return row


def empty_document(anchor: date) -> dict[str, Any]:
    """``FnaDocument()`` as the app creates it, dated by the call rather than by the clock (§1.5: the meeting
    date comes from the call metadata; a pipeline run must give the same document whenever it runs)."""
    doc = F.FnaDocument().model_dump(mode="json")
    doc["engagement"]["meetingDate"] = anchor.isoformat()
    return doc


def _container(doc: dict[str, Any], dotted: str) -> Any:
    node: Any = doc
    for part in dotted.split("."):
        node = node[part]
    return node


# --------------------------------------------------------------------------- value conversion


def quantity_unit(slot: SlotDef) -> tuple[str, str]:
    """(Quantity.unit, Quantity.period) for a slot's FnaDocument unit (the lexicon's mapping)."""
    from .lexicon.normalize import quantity_unit as unit_of

    return unit_of(slot)


def to_quantity(slot: SlotDef, raw: Any) -> Quantity:
    """A point Quantity from an FnaDocument value (risk answers store the option index)."""
    unit, period = quantity_unit(slot)
    if slot.store_as == "index" and slot.categories:
        idx = int(raw)
        raw = slot.categories[idx] if 0 <= idx < len(slot.categories) else str(raw)
    elif slot.store_as == "int" or slot.value_type == "count":
        raw = str(int(raw)) if slot.categories else int(raw)
    if isinstance(raw, float) and raw.is_integer() and slot.value_type in ("age", "years", "months", "year", "count"):
        raw = int(raw)
    return Quantity(dist=Point(value=raw), unit=unit, period=period)  # type: ignore[arg-type]


def is_empty(slot: SlotDef, raw: Any) -> bool:
    """Prior fields that carry no information: empty text, missing values, zero amounts."""
    if raw is None or raw == "":
        return True
    return bool(slot.value_type == "amount" and isinstance(raw, (int, float)) and raw == 0)


def _round(slot: SlotDef, x: float) -> float | int:
    if slot.value_type in ("age", "years", "months", "count", "year"):
        return round(x)
    if slot.value_type == "amount":
        return round(x, 2)
    if slot.value_type == "rate":
        return round(x, 6)
    return x


def to_fna_value(slot: SlotDef, dist: Dist, mode: Literal["median", "draw"], rng: Generator | None = None) -> Any:
    """One FnaDocument value from a distribution: median/mode/p≥0.5, or one draw."""
    if mode == "draw" and rng is None:
        raise ValueError("draw needs an rng")
    if slot.value_type == "text":
        if isinstance(dist, Point):
            return str(dist.value)
        # Two readings of a text field ("teacher" / "lecturer"): the likelier one, never an empty string.
        return str(stats.mode_label(dist)) if stats.is_categorical(dist) else ""
    if slot.value_type in ("boolean", "probability") or stats.is_boolean(dist):
        p = stats.p_true(dist)
        return bool(rng.random() < p) if mode == "draw" else p >= 0.5  # type: ignore[union-attr]
    if slot.value_type in ("category", "count") or stats.is_categorical(dist):
        if mode == "draw":
            label = str(stats.sample(dist, rng, 1)[0])  # type: ignore[arg-type]
        else:
            label = stats.mode_label(dist)
        if slot.store_as == "index" and slot.categories:
            return slot.categories.index(label) if label in slot.categories else None
        if slot.store_as == "int":
            return int(float(label))
        return label
    if isinstance(dist, Point) and isinstance(dist.value, str):
        return dist.value  # an exact ISO date
    x = float(stats.sample(dist, rng, 1)[0]) if mode == "draw" else stats.median(dist)  # type: ignore[arg-type]
    if slot.value_type == "date":
        return stats.year_to_iso(x)
    if isinstance(dist, Point):
        return dist.value  # a certain value is written as it is: a prior 25.5 or a stated "sixty-two and a half" is never rounded
    return _round(slot, x)


# --------------------------------------------------------------------------- load_prior


_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    return _SLUG.sub("_", s.lower()).strip("_")[:24] or "item"


_ASSET_ENTITY: dict[str, EntityType] = {
    "primary-residence": "property",
    "residential-property": "property",
    "commercial-property": "property",
    "vehicle": "property",
    "personal-effects": "property",
    "business-interest": "business",
    "loan-account": "business",
    "other": "property",
}


def _row_entity(list_name: str, row: dict[str, Any], n: int) -> Entity:
    """The registry entity for one FnaDocument list row."""
    if list_name == "dependants":
        rel = row.get("relationship") or "child"
        name = row.get("name") or rel
        return Entity(
            entity_id=f"person.{_slug(name)}_{n}",
            type="person",
            canonical_name=name,
            aliases=[a for a in {row.get("name") or ""} if a],
            attributes={"relation": rel, "date_of_birth": row.get("dateOfBirth") or ""},
            fna_list=list_name,
            fna_id=row["id"],
        )
    if list_name == "retirementFunds":
        label = " ".join(x for x in (row.get("provider"), row.get("name") or row.get("type")) if x)
        return Entity(entity_id=f"account.{_slug(row.get('provider') or row.get('type') or 'fund')}_{n}", type="account", canonical_name=label or "retirement fund",
                      aliases=[a for a in (row.get("name"), row.get("provider")) if a], attributes={"fund_type": row.get("type"), "owner": row.get("owner")},
                      fna_list=list_name, fna_id=row["id"])
    if list_name == "assets":
        t = row.get("type") or "other"
        etype = _ASSET_ENTITY.get(t, "account")
        label = row.get("description") or t
        return Entity(entity_id=f"{etype}.{_slug(label)}_{n}", type=etype, canonical_name=label, aliases=[a for a in (row.get("description"), row.get("provider")) if a],
                      attributes={"asset_type": t, "owner": row.get("owner")}, fna_list=list_name, fna_id=row["id"])
    if list_name == "liabilities":
        label = row.get("description") or row.get("type") or "debt"
        return Entity(entity_id=f"liability.{_slug(label)}_{n}", type="liability", canonical_name=label, aliases=[a for a in (row.get("description"), row.get("lender")) if a],
                      attributes={"liability_type": row.get("type"), "owner": row.get("owner"), "linked_asset_row": row.get("linkedAssetId") or ""},
                      fna_list=list_name, fna_id=row["id"])
    if list_name == "policies":
        label = " ".join(x for x in (row.get("insurer"), row.get("type")) if x) or "policy"
        return Entity(entity_id=f"policy.{_slug(label)}_{n}", type="policy", canonical_name=label, aliases=[a for a in (row.get("insurer"), row.get("policyNumber")) if a],
                      attributes={"policy_type": row.get("type"), "life_assured": row.get("lifeAssured")}, fna_list=list_name, fna_id=row["id"])
    if list_name == "goals":
        label = row.get("name") or "goal"
        return Entity(entity_id=f"goal.{_slug(label)}_{n}", type="goal", canonical_name=label, aliases=[label], attributes={"owner": row.get("owner")},
                      fna_list=list_name, fna_id=row["id"])
    if list_name == "expenses.custom":
        label = row.get("label") or "expense"
        return Entity(entity_id=f"account.expense_{_slug(label)}_{n}", type="account", canonical_name=label, aliases=[label],
                      attributes={"category": row.get("category"), "essential": row.get("essential")}, fna_list=list_name, fna_id=row["id"])
    raise ValueError(list_name)


def _persons(doc: F.FnaDocument, crm: CrmRecord | None) -> dict[str, Entity]:
    out: dict[str, Entity] = {}
    cname = " ".join(x for x in (doc.client.firstName, doc.client.surname) if x) or "Client"
    out["person.client_primary"] = Entity(entity_id="person.client_primary", type="person", canonical_name=cname,
                                          aliases=[a for a in (doc.client.firstName,) if a], attributes={"person_key": "client"})
    if doc.household.includeSpouse or doc.spouse.firstName or doc.household.maritalStatus in ("married", "life-partner"):
        sname = " ".join(x for x in (doc.spouse.firstName, doc.spouse.surname) if x) or "Spouse"
        out["person.spouse"] = Entity(entity_id="person.spouse", type="person", canonical_name=sname,
                                      aliases=[a for a in (doc.spouse.firstName,) if a], attributes={"person_key": "spouse"})
    out["person.advisor"] = Entity(entity_id="person.advisor", type="person", canonical_name=(crm.adviser_name if crm else "") or "Adviser", attributes={"role": "advisor"})
    if crm:
        for p in crm.household:
            key = {"client_primary": "person.client_primary", "spouse": "person.spouse", "advisor": "person.advisor"}.get(p.role)
            if key and key in out:
                out[key] = out[key].model_copy(update={"crm_match": [(p.crm_id, 1.0)], "aliases": sorted(set(out[key].aliases) | {p.name.split()[0]} if p.name else set(out[key].aliases))})
    return out


def _crm_links(entities: dict[str, Entity], crm: CrmRecord) -> dict[str, Entity]:
    """Link CRM household members and products to registry entities; seed the rest (§6.1 step 1)."""
    from rapidfuzz import fuzz

    out = dict(entities)
    for p in crm.household:
        if p.role in ("client_primary", "spouse", "advisor"):
            continue
        best = max(
            (e for e in out.values() if e.type == "person" and e.fna_list == "dependants"),
            key=lambda e: fuzz.token_set_ratio(p.name, e.canonical_name),
            default=None,
        )
        if best is not None and fuzz.token_set_ratio(p.name, best.canonical_name) >= 85:
            out[best.entity_id] = best.model_copy(update={"crm_match": [(p.crm_id, 1.0)]})
        else:
            eid = f"person.{_slug(p.name)}_crm"
            out[eid] = Entity(entity_id=eid, type="person", canonical_name=p.name, aliases=[p.name.split()[0]] if p.name else [],
                              attributes={"relation": p.role, "date_of_birth": p.date_of_birth}, crm_match=[(p.crm_id, 1.0)])
    kind_types: dict[str, EntityType] = {"policy": "policy", "fund": "account", "investment": "account", "loan": "liability", "bank": "account", "property": "property", "other": "account"}
    for prod in crm.products:
        etype = kind_types[prod.kind]
        label = " ".join(x for x in (prod.provider, prod.product) if x) or prod.kind
        cands = [e for e in out.values() if e.type == etype and e.fna_id]
        scored = sorted(((fuzz.token_set_ratio(label, " ".join([e.canonical_name, *e.aliases])) / 100.0, e) for e in cands), key=lambda t: -t[0])
        if prod.number:
            scored = [(1.0 if prod.number in e.aliases else s, e) for s, e in scored]
            scored.sort(key=lambda t: -t[0])
        if scored and scored[0][0] >= 0.85:
            e = scored[0][1]
            out[e.entity_id] = e.model_copy(update={"crm_match": [(prod.crm_id, round(scored[0][0], 3))]})
        else:
            eid = f"{etype}.{_slug(label)}_crm"
            out[eid] = Entity(entity_id=eid, type=etype, canonical_name=label, aliases=[a for a in (prod.provider, prod.product, prod.number) if a],
                              attributes={"crm_kind": prod.kind, "fna_type": prod.fna_type, "owner": prod.owner, "last_value": prod.last_value},
                              crm_match=[(prod.crm_id, 1.0)])
    return out


def person_instances(doc: F.FnaDocument | None, entities: dict[str, Entity]) -> list[str]:
    return ["client"] + (["spouse"] if "person.spouse" in entities else [])


def load_prior(
    fna: F.FnaDocument | None,
    crm: CrmRecord | None = None,
    *,
    call_id: str,
    run_id: str,
    anchor_date: date,
    catalog: Catalog | None = None,
    provenance: Provenance | None = None,
) -> Profile:
    """Seed a run from the client's existing FNA document and CRM record (§3.6)."""
    catalog = catalog or load_catalog()
    doc = fna or F.FnaDocument()
    prov = provenance or Provenance(run_id=run_id, schema_version=catalog.version)
    entities = _persons(doc, crm)
    data = doc.model_dump(mode="json")
    row_entity: dict[tuple[str, str], str] = {}
    for list_name in LIST_CONTAINERS:
        rows = _container(data, list_name)
        for n, row in enumerate(rows, start=1):
            e = _row_entity(list_name, row, n)
            entities[e.entity_id] = e
            row_entity[(list_name, row["id"])] = e.entity_id
    if crm:
        entities = _crm_links(entities, crm)
    profile = Profile(call_id=call_id, run_id=run_id, schema_version=catalog.version, anchor_date=anchor_date, entities=entities)

    specs: list[tuple[str, str, str | None, dict[str, Any], str]] = []
    persons = person_instances(doc, entities) if fna is not None else []

    def seed(slot: SlotDef, instance: str | None, path: str, raw: Any, refs: list[str]) -> None:
        if is_empty(slot, raw):
            return
        q = to_quantity(slot, raw)
        specs.append(
            (
                "ADD",
                fact_id_for(slot.slot_id, instance),
                None,
                {
                    "slot_id": slot.slot_id,
                    "instance_key": instance,
                    "fna_path": path,
                    "source": "prior_fna",
                    "status": "not_discussed",
                    "hypotheses": [Hypothesis(value=q, weight=1.0, rationale="from the client's existing FNA")],
                    "prior_value": q,
                    "entity_refs": refs,
                    "provenance": prov,
                },
                "loader",
            )
        )

    if fna is not None:
        for slot in catalog.slots:
            if not slot.exported:
                continue
            if slot.slot_id == "expenses.items[]":
                for key, amount in data["expenses"]["items"].items():
                    seed(slot, key, f"expenses.items[{key}]", amount, [])
                continue
            if slot.slot_id.startswith("riskProfile.answers["):
                qid = slot.slot_id[len("riskProfile.answers[") : -1]
                if qid in data["riskProfile"]["answers"]:
                    seed(slot, None, slot.slot_id, data["riskProfile"]["answers"][qid], [])
                continue
            if slot.per_person:
                for person in persons:
                    path = slot.fna_path_for(person)
                    seed(slot, person, path, get_path(data, path), [f"person.{'client_primary' if person == 'client' else 'spouse'}"])
                continue
            list_name = ENTITY_LIST.get(slot.entity_type or "")
            if list_name and slot.slot_id.startswith(f"{list_name}[]"):
                for row in _container(data, list_name):
                    eid = row_entity[(list_name, row["id"])]
                    path = slot.fna_path_for(None, row["id"])
                    seed(slot, eid, path, row.get(slot.field), [eid])
                continue
            if slot.cardinality == "one":
                seed(slot, None, slot.fna_path, get_path(data, slot.fna_path), [])
    if not specs:
        return profile
    result = apply_state_ops(profile, make_ops(profile, specs))
    if result.rejected:
        raise ValueError(f"load_prior produced rejected ops: {result.rejected[0].reason}")
    return result.profile


# --------------------------------------------------------------------------- materialise


def row_id_for(profile: Profile, entity_id: str) -> str:
    """The FnaDocument row id for an entity: its own, or a stable id derived from the call and entity."""
    e = profile.entities.get(entity_id)
    if e is not None and e.fna_id:
        return e.fna_id
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"advisor-intel/{profile.call_id}/{entity_id}"))


def _usable(f: Fact) -> bool:
    return f.review.status != "rejected" and f.value is not None


def materialise(
    profile: Profile,
    mode: Literal["median", "draw"] = "median",
    rng: Generator | None = None,
    *,
    base: F.FnaDocument | None = None,
    catalog: Catalog | None = None,
) -> F.FnaDocument:
    """One point-valued FnaDocument from the profile (§3.6).

    median: each fact's median (numeric), mode (categorical), p ≥ 0.5 (boolean events).
    draw: one sample per fact; an unreviewed fact is replaced by a draw from its slot's population
    prior with probability 1 − p_correct (the extraction-risk mixture, §12.2).
    Facts with no value keep the base document's value (the prior FNA), else the app default.
    """
    catalog = catalog or load_catalog(profile.schema_version)
    doc = base.model_dump(mode="json") if base is not None else empty_document(profile.anchor_date)
    _ensure_rows(doc, profile, catalog)
    removed_rows: set[tuple[str, str]] = set()
    for f in sorted(profile.facts.values(), key=lambda x: x.fact_id):
        slot = catalog.slot(f.slot_id)
        if not slot.exported:
            continue
        pp = parse_path(f.fna_path)
        if f.change == "removed" and pp.container in IDENTITY_FIELD and pp.field == IDENTITY_FIELD[pp.container] and pp.key:
            removed_rows.add((pp.container, pp.key))
            continue
        if not _usable(f):
            continue
        dist = f.value.dist  # type: ignore[union-attr]
        if mode == "draw":
            trusted = f.review.status in ("accepted", "edited") or f.auto_accepted or f.source in ("prior_fna", "reviewer")
            q = 1.0 if trusted else f.p_correct
            if slot.population_prior is not None and rng is not None and rng.random() >= q:
                dist = slot.population_prior
        value = to_fna_value(slot, dist, mode, rng)
        if value is None:
            continue
        set_path(doc, _row_path(profile, f), value, anchor=profile.anchor_date)
    for container, key in removed_rows:
        rows = _container(doc, container)
        rows[:] = [r for r in rows if r.get("id") != key]
    return F.FnaDocument.model_validate(doc)


def _ensure_rows(doc: dict[str, Any], profile: Profile, catalog: Catalog) -> None:
    """Create list rows for entities the call added, in registry order, so row order is stable."""
    order = {eid: i for i, eid in enumerate(profile.entities)}
    wanted: dict[tuple[str, str], int] = {}
    for f in profile.facts.values():
        if not f.instance_key or not _usable(f) or not catalog.slot(f.slot_id).exported:
            continue
        pp = parse_path(_row_path(profile, f))
        if pp.key is not None and pp.container in LIST_ROW_MODEL:
            wanted.setdefault((pp.container, pp.key), order.get(f.instance_key, len(order)))
    for (container, key), _ in sorted(wanted.items(), key=lambda kv: (kv[1], kv[0])):
        rows = _container(doc, container)
        if not any(r.get("id") == key for r in rows):
            rows.append(new_row(container, key, profile.anchor_date))


def _row_path(profile: Profile, f: Fact) -> str:
    """The fact's path with the entity's row id (rows created during the call get a stable id)."""
    pp = parse_path(f.fna_path)
    if pp.key is None or pp.container in DICT_CONTAINERS or not f.instance_key:
        return f.fna_path
    if pp.key and pp.key != "" and not pp.key.startswith("new:") and pp.key == row_id_for(profile, f.instance_key):
        return f.fna_path
    rid = row_id_for(profile, f.instance_key)
    return f"{pp.container}[{rid}].{pp.field}"


def materialise_draws(profile: Profile, k: int, seed: int = 0, *, base: F.FnaDocument | None = None) -> list[F.FnaDocument]:
    """K draw documents for the FNA engine's uncertainty bands (§12.7, use 2)."""
    rng = np.random.default_rng(seed)
    catalog = load_catalog(profile.schema_version)
    return [materialise(profile, "draw", rng, base=base, catalog=catalog) for _ in range(k)]


# --------------------------------------------------------------------------- sidecar and export


def summarise_value(q: Quantity | None) -> dict[str, Any] | None:
    """A JSON summary of a value for the sidecar and the review overlay."""
    if q is None:
        return None
    d = q.dist
    out: dict[str, Any] = {"unit": q.unit, "period": q.period, "basis": q.basis, "kind": d.kind}
    if isinstance(d, Point):
        out["point"] = d.value
        return out
    if stats.is_boolean(d):
        out["p_true"] = round(stats.p_true(d), 4)
        return out
    if stats.is_categorical(d):
        out["probs"] = {k: round(v, 4) for k, v in sorted(stats.categorical_probs(d).items(), key=lambda kv: -kv[1])}
        return out
    s = stats.summary(d)
    out.update({k: _finite(v) for k, v in s.items()})
    return out


def _finite(x: float) -> float | None:
    return None if (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(x, 6)


def describe_value(q: Quantity | None, *, money: bool | None = None) -> str:
    """A short phrase for the review popover: '60–65, most likely 62' (§11.1)."""
    if q is None:
        return "not known"
    money = q.unit in ("ZAR", "USD", "GBP", "EUR") if money is None else money
    sym = {"ZAR": "R", "USD": "$", "GBP": "£", "EUR": "€"}.get(q.unit, "")
    per = {"monthly": " a month", "annual": " a year"}.get(q.period, "")

    def fmt(x: float) -> str:
        if q.unit == "date":
            return stats.year_to_iso(x)[:7] if abs(x - round(x)) > 1e-9 else str(round(x))
        if q.unit == "pct":
            return f"{x * 100:.1f}%"
        if money:
            return f"{sym}{x:,.0f}"
        return f"{x:g}" if abs(x) < 1000 else f"{x:,.0f}"

    d = q.dist
    if isinstance(d, Point):
        v = d.value
        if isinstance(v, bool):
            return "yes" if v else "no"
        if isinstance(v, str):
            return v
        return fmt(float(v)) + per
    if stats.is_boolean(d):
        return f"{stats.p_true(d):.0%} likely"
    if stats.is_categorical(d):
        probs = sorted(stats.categorical_probs(d).items(), key=lambda kv: -kv[1])
        return ", ".join(f"{k} ({p:.0%})" for k, p in probs[:3])
    s = stats.summary(d)
    if abs(s["p90"] - s["p10"]) <= 1e-9:
        return fmt(s["p50"]) + per
    return f"{fmt(s['p10'])}–{fmt(s['p90'])}, most likely {fmt(s['p50'])}{per}"


def _quantity(q: Quantity | None) -> dict[str, Any] | None:
    """The full distribution, beside its summary: the overlay draws it, edits it and re-adds it on re-attribution."""
    return None if q is None else q.model_dump(mode="json")


def sidecar(profile: Profile, *, catalog: Catalog | None = None) -> dict[str, Any]:
    """Probabilities and evidence keyed by concrete fna_path (§3.6, §17 default).

    ``models/sidecar.py`` describes the result; the review overlay's TypeScript types are checked against it.
    """
    catalog = catalog or load_catalog(profile.schema_version)
    fields: dict[str, Any] = {}
    for f in sorted(profile.facts.values(), key=lambda x: x.fact_id):
        slot = catalog.slot(f.slot_id)
        path = _row_path(profile, f) if slot.exported else f.fna_path
        fields[path] = {
            "fact_id": f.fact_id,
            "slot_id": f.slot_id,
            "label": slot.label,
            "group": slot.group,
            "tier": slot.sensitivity_tier,
            "exported": slot.exported,
            "status": f.status,
            "change": f.change,
            "source": f.source,
            "value": summarise_value(f.value),
            "quantity": _quantity(f.value),
            "value_text": describe_value(f.value),
            "prior_value": summarise_value(f.prior_value),
            "prior_text": describe_value(f.prior_value) if f.prior_value is not None else None,
            "hypotheses": [
                {"value": summarise_value(h.value), "quantity": _quantity(h.value), "text": describe_value(h.value), "weight": round(h.weight, 4), "rationale": h.rationale, "revises": h.revises}
                for h in f.hypotheses
            ],
            "p_correct": round(f.p_correct, 4),
            "auto_accepted": f.auto_accepted,
            "review_priority": round(f.review_priority, 6),
            "why": f.voi.get("why_text") if isinstance(f.voi.get("why_text"), str) else None,
            "speaker_attribution": f.speaker_attribution,
            "said": f.said,
            "interpretation": f.interpretation,
            "hedges": [h.term for h in f.hedges],
            "evidence": [e.model_dump(mode="json") for e in f.evidence],
            "entity_refs": f.entity_refs,
            "review": f.review.model_dump(mode="json"),
            "derivation": f.derivation,
            "term_resolution": f.term_resolution,
        }
    return {
        "format": "advisor-intel-sidecar",
        "version": 1,
        "call_id": profile.call_id,
        "run_id": profile.run_id,
        "profile_version": profile.version,
        # Reviewer ops are numbered after this (the reducer rejects a seq that is not after the log's last).
        "last_seq": profile.ops_log[-1].seq if profile.ops_log else 0,
        "schema_version": profile.schema_version,
        "anchor_date": profile.anchor_date.isoformat(),
        "fields": fields,
        "blocking_flags": [b.model_dump(mode="json") for b in profile.blocking_flags],
        "followups": [q.model_dump(mode="json") for q in profile.open_questions],
    }


def export_bundle(profile: Profile, *, base: F.FnaDocument | None = None) -> tuple[F.ExportFile, dict[str, Any]]:
    """The reviewed median document as a ``shahil-fna`` file, plus its sidecar (§3.6, §11.5)."""
    doc = materialise(profile, "median", base=base)
    doc = doc.model_copy(update={"updatedAt": F._now_iso()})
    return F.ExportFile(document=doc), sidecar(profile)


def iter_entity_facts(profile: Profile, entity_id: str) -> Iterable[Fact]:
    return (f for f in profile.facts.values() if f.instance_key == entity_id or entity_id in f.entity_refs)
