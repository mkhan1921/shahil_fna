"""Slot definitions (spec §4.1) and the catalog loader.

Slot ids use ``{p}`` for a person and ``[]`` for a list row, as in the spec:
``retirement.{p}.retirementAge`` expands to ``retirement.client.retirementAge``;
``dependants[].tertiaryStartAge`` is one slot whose facts are keyed by entity
(``dependants[].tertiaryStartAge#person.daughter_1``) and resolve to an FnaDocument row
(``dependants[3f2a…].tertiaryStartAge``).
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr

from ..models.dists import Dist

SCHEMA_DIR = Path(__file__).parent
CURRENT_VERSION = "profile_v1"

Cardinality = Literal["one", "per_person", "per_entity", "list"]
ListEntity = Literal["dependant", "fund", "asset", "liability", "policy", "goal", "expense", "event", "income_change"]
SlotPolicy = Literal["categorical", "span_normalise", "open_list", "boolean", "text"]
ValueType = Literal["amount", "age", "years", "months", "date", "year", "rate", "count", "category", "boolean", "text", "probability"]
FnaUnit = Literal[
    "rand",  # lump sum
    "rand_monthly",
    "rand_annual",
    "years",
    "months",
    "age",
    "year",  # calendar year (goal target year)
    "decimal",  # rates and percentages as decimals
    "date",  # ISO yyyy-mm-dd
    "count",
    "category",
    "boolean",
    "text",
    "probability",
]
Tier = Literal["A", "B", "C"]

# The FnaDocument list each per-entity slot lives in.
LIST_OF_ENTITY: dict[str, str] = {
    "dependant": "dependants",
    "fund": "retirementFunds",
    "asset": "assets",
    "liability": "liabilities",
    "policy": "policies",
    "goal": "goals",
    "expense": "expenses.custom",
    "event": "event",
    "income_change": "expectedChange",
}

PERSONS = ("client", "spouse")


class SlotDef(BaseModel):
    slot_id: str
    fna_path: str
    group: str
    cardinality: Cardinality
    entity_type: ListEntity | None = None
    policy: SlotPolicy
    value_type: ValueType
    fna_unit: FnaUnit
    allowed_dists: list[str] = ["point"]
    allowed_forms: list[str] = []  # intermediate form types the extractor may return (§8.1)
    categories: list[str] | None = None
    bounds: tuple[float, float] | None = None
    question: str
    extraction_hints: list[str] = []
    depends_on: list[str] = []
    fna_default: Any = None
    population_prior: Dist | None = None
    sensitivity_tier: Tier = "C"
    fna_outputs: list[str] = []
    forecast_binding: str | None = None
    exported: bool = True  # forecast-only slots (event[], expectedChange[]) are not exported
    store_as: Literal["value", "index", "int"] = "value"  # risk answers store the option index; counts store an int
    label: str = ""  # short human label for the review UI

    @property
    def per_person(self) -> bool:
        return "{p}" in self.slot_id

    @property
    def per_entity(self) -> bool:
        return self.cardinality in ("per_entity", "list")

    @property
    def field(self) -> str:
        """The FnaDocument field name at the end of the path ('tertiaryStartAge')."""
        return self.fna_path.rsplit(".", 1)[-1]

    def expand(self, person: str | None = None) -> str:
        """The slot id with ``{p}`` filled in."""
        return self.slot_id.replace("{p}", person) if person else self.slot_id

    def fna_path_for(self, person: str | None = None, fna_id: str | None = None) -> str:
        path = self.fna_path.replace("{p}", person) if person else self.fna_path
        if fna_id is not None:
            path = path.replace("[]", f"[{fna_id}]", 1)
        return path


def fact_id_for(slot_id: str, instance: str | None = None) -> str:
    return f"{slot_id}#{instance}" if instance else slot_id


_PERSON_RE = re.compile(r"\b(client|spouse)\b")


def generic_slot_id(concrete: str) -> str:
    """'retirement.client.retirementAge' → 'retirement.{p}.retirementAge'; strips '#instance'."""
    base = concrete.split("#", 1)[0]
    return _PERSON_RE.sub("{p}", base, count=1)


def person_of(concrete: str) -> str | None:
    m = _PERSON_RE.search(concrete.split("#", 1)[0])
    return m.group(1) if m else None


class SlotGroup(BaseModel):
    name: str
    description: str = ""
    list_entity: ListEntity | None = None
    instructions: str = ""  # the slot group's instruction block: the component GEPA optimises (§15.6)
    family: str = ""  # Mondrian family for conformal (§9.5)


class ExpenseItem(BaseModel):
    """One line of the FNA expense catalog (lib/fna/catalog.ts EXPENSE_ITEMS)."""

    key: str
    label: str
    category: str
    essential: bool
    hint: str = ""


class RiskQuestion(BaseModel):
    """One investor risk question (lib/fna/riskProfile.ts); answers store the option index."""

    id: str
    dimension: Literal["tolerance", "capacity"]
    question: str
    options: list[str]
    scores: list[int]


class Catalog(BaseModel):
    version: str
    generated_from: dict[str, str] = {}  # source file → content hash
    groups: dict[str, SlotGroup] = Field(default_factory=dict)
    slots: list[SlotDef]
    expense_items: list[ExpenseItem] = []
    risk_questions: list[RiskQuestion] = []
    excluded: dict[str, dict[str, str] | str] = {}
    _idx: dict[str, SlotDef] | None = PrivateAttr(default=None)

    def by_id(self) -> dict[str, SlotDef]:
        return {s.slot_id: s for s in self.slots}

    def slot(self, slot_id: str) -> SlotDef:
        """Look up a slot by its generic id, or by a concrete id ('retirement.client.retirementAge')."""
        index = _index(self)
        if slot_id in index:
            return index[slot_id]
        generic = generic_slot_id(slot_id)
        if generic in index:
            return index[generic]
        raise KeyError(slot_id)

    def in_group(self, group: str) -> list[SlotDef]:
        return [s for s in self.slots if s.group == group]

    def tier_a(self) -> list[SlotDef]:
        return [s for s in self.slots if s.sensitivity_tier == "A"]

    def family_of(self, slot_id: str) -> str:
        s = self.slot(slot_id)
        g = self.groups.get(s.group)
        return g.family if g and g.family else s.group

    def expense_item(self, key: str) -> ExpenseItem | None:
        return next((e for e in self.expense_items if e.key == key), None)

    def group_names(self) -> list[str]:
        return list(self.groups)


def _index(cat: Catalog) -> dict[str, SlotDef]:
    if cat._idx is None:
        cat._idx = cat.by_id()
    return cat._idx


@functools.lru_cache(maxsize=8)
def load_catalog(version: str = CURRENT_VERSION) -> Catalog:
    path = SCHEMA_DIR / f"{version}.yaml"
    data = yaml.safe_load(path.read_text())
    return Catalog.model_validate(data)
