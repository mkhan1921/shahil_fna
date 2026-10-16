"""Defaults: a port of ``lib/fna/defaults.ts`` (§12.7).

The ``FnaDocument`` mirror in :mod:`advisor_intel.models.fna` carries the app's defaults (tested against
``newDocument()``); this module adds the named factories and ``normaliseDocument`` for partial or older files.
The practice profile is app-side and not mirrored.
"""

from __future__ import annotations

from typing import Any

from ..models.fna import (
    SCHEMA_VERSION,
    AdviceRecord,
    Asset,
    Assumptions,
    Dependant,
    Engagement,
    EstateProfile,
    FnaDocument,
    Income,
    Liability,
    OtherGoal,
    Owner,
    Person,
    PersonKey,
    Policy,
    Recommendation,
    RetirementFund,
    RetirementGoal,
)

DEFAULT_ASSUMPTIONS = Assumptions()


def new_document(assumptions: Assumptions | None = None) -> FnaDocument:
    doc = FnaDocument()
    if assumptions is not None:
        doc.assumptions = assumptions.model_copy()
    return doc


def empty_person() -> Person:
    return Person()


def empty_income() -> Income:
    return Income()


def default_retirement_goal() -> RetirementGoal:
    return RetirementGoal()


def default_estate() -> EstateProfile:
    return EstateProfile()


def default_engagement() -> Engagement:
    return Engagement()


def default_advice() -> AdviceRecord:
    return AdviceRecord()


def new_dependant() -> Dependant:
    return Dependant()


def new_retirement_fund(owner: PersonKey = "client") -> RetirementFund:
    return RetirementFund(owner=owner)


def new_asset(owner: Owner = "client") -> Asset:
    return Asset(owner=owner)


def new_liability(owner: Owner = "client") -> Liability:
    return Liability(owner=owner)


def new_policy(life_assured: PersonKey = "client") -> Policy:
    return Policy(lifeAssured=life_assured)


def new_goal() -> OtherGoal:
    return OtherGoal()


def new_recommendation(**partial: Any) -> Recommendation:
    """``newRecommendation(partial)``: the defaults overlaid with ``partial`` (camelCase field names)."""
    return Recommendation(**partial)


_SECTIONS = ("client", "spouse", "household", "riskProfile", "assumptions", "engagement", "advice")
_PER_PERSON = ("income", "retirement", "estate")
_ROWS = {
    "dependants": Dependant,
    "retirementFunds": RetirementFund,
    "assets": Asset,
    "liabilities": Liability,
    "policies": Policy,
    "goals": OtherGoal,
}


def normalise_document(raw: dict[str, Any]) -> FnaDocument:
    """``normaliseDocument``: bring an older or partial document up to the current schema without losing data.

    Sections and list rows are merged over the defaults one level deep, as the app does."""
    base = FnaDocument().model_dump(mode="json")
    doc: dict[str, Any] = {**base, **raw}
    for key in _SECTIONS:
        doc[key] = {**base[key], **(raw.get(key) or {})}
    for key in _PER_PERSON:
        section = raw.get(key) or {}
        doc[key] = {k: {**base[key][k], **(section.get(k) or {})} for k in ("client", "spouse")}
    expenses = raw.get("expenses") or {}
    doc["expenses"] = {"items": dict(expenses.get("items") or {}), "custom": expenses.get("custom") or []}
    for key, model in _ROWS.items():
        defaults = model().model_dump(mode="json")
        doc[key] = [{**defaults, "id": model().id, **row} for row in raw.get(key) or []]
    doc["schemaVersion"] = SCHEMA_VERSION
    doc["advice"]["recommendations"] = [
        Recommendation(**r).model_dump(mode="json") for r in (raw.get("advice") or {}).get("recommendations") or []
    ]
    return FnaDocument.model_validate(doc)
