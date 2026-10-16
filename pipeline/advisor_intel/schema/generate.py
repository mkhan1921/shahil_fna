"""Generate ``profile_v1.yaml`` from the FNA app's sources and ``annotations.yaml`` (spec §4).

    python -m advisor_intel.schema.generate            # write profile_v1.yaml
    python -m advisor_intel.schema.generate --check    # exit 1 if the committed file is stale

Field types, union values, the expense catalog and the risk questions come from
``lib/fna/types.ts``, ``catalog.ts`` and ``riskProfile.ts``; defaults come from the FnaDocument
mirror (whose defaults are tested against ``lib/fna/defaults.ts``). Everything people decide —
questions, tiers, priors, bounds, hints, outputs — comes from ``annotations.yaml``. The check fails
when the app gains a field that is neither a slot nor listed under ``excluded``.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter

from ..config import REPO_DIR
from ..models import fna as F
from ..models.dists import Categorical, Dist
from .slots import SCHEMA_DIR, Catalog, ExpenseItem, RiskQuestion, SlotDef, SlotGroup
from .ts_types import AppSources, TsModule, read_app_sources

ANNOTATIONS = SCHEMA_DIR / "annotations.yaml"
VERSION = "profile_v1"

# Path prefix → (TS interface, Python mirror class, cardinality, list entity).
_CONTAINERS: list[tuple[str, str, type | None, str, str | None]] = [
    ("{p}.", "Person", F.Person, "per_person", None),
    ("household.", "Household", F.Household, "one", None),
    ("dependants[].", "Dependant", F.Dependant, "per_entity", "dependant"),
    ("income.{p}.expectedChange[].", "", None, "list", "income_change"),
    ("income.{p}.", "Income", F.Income, "per_person", None),
    ("expenses.custom[].", "CustomExpense", F.CustomExpense, "list", "expense"),
    ("expenses.items[]", "", None, "list", "expense"),
    ("retirementFunds[].", "RetirementFund", F.RetirementFund, "per_entity", "fund"),
    ("assets[].", "Asset", F.Asset, "per_entity", "asset"),
    ("liabilities[].", "Liability", F.Liability, "per_entity", "liability"),
    ("policies[].", "Policy", F.Policy, "per_entity", "policy"),
    ("retirement.{p}.", "RetirementGoal", F.RetirementGoal, "per_person", None),
    ("goals[].", "OtherGoal", F.OtherGoal, "list", "goal"),
    ("estate.{p}.", "EstateProfile", F.EstateProfile, "per_person", None),
    ("riskProfile.answers[]", "", None, "one", None),
    ("engagement.", "Engagement", F.Engagement, "one", None),
    ("event[].", "", None, "list", "event"),
]

_OPEN_LIST_GROUPS = {"goals", "events", "income_changes"}

_MONTHLY_NAMES = {"monthlySupport", "monthlyContribution", "monthlyIncome", "monthlyBenefit", "monthlyRepayment"}

FORMS_BY_TYPE: dict[str, list[str]] = {
    "amount": ["AMOUNT_EXACT", "AMOUNT_APPROX", "AMOUNT_RANGE", "AMOUNT_BOUND", "AMOUNT_VAGUE"],
    "age": ["AGE", "DURATION_FROM_ANCHOR"],
    "years": ["DURATION_FROM_ANCHOR"],
    "months": ["DURATION_FROM_ANCHOR"],
    "date": ["DATE_ABS", "AGE"],
    "year": ["DATE_ABS", "DURATION_FROM_ANCHOR"],
    "rate": ["RATE"],
    "count": ["CATEGORY"],
    "category": ["CATEGORY"],
    "boolean": ["BOOLEAN"],
    "probability": ["PROBABILITY_EVENT"],
    "text": ["TEXT"],
}

DISTS_BY_TYPE: dict[str, list[str]] = {
    "amount": ["point", "triangular", "lognormal", "discrete", "mixture"],
    "age": ["point", "discrete", "triangular", "mixture"],
    "years": ["point", "discrete", "triangular", "mixture"],
    "months": ["point", "discrete", "triangular", "mixture"],
    "date": ["point", "discrete", "triangular", "mixture"],
    "year": ["point", "discrete", "triangular", "mixture"],
    "rate": ["point", "normal", "triangular", "mixture"],
    "count": ["point", "categorical", "mixture"],
    "category": ["point", "categorical", "mixture"],
    "boolean": ["point", "bernoulli", "mixture"],
    "probability": ["point", "bernoulli", "mixture"],
    "text": ["point"],
}

UNIT_BY_TYPE = {
    "age": "age",
    "years": "years",
    "months": "months",
    "date": "date",
    "year": "year",
    "rate": "decimal",
    "count": "count",
    "category": "category",
    "boolean": "boolean",
    "probability": "probability",
    "text": "text",
}

POLICY_BY_TYPE = {
    "category": "categorical",
    "count": "categorical",
    "boolean": "boolean",
    "text": "text",
}

_DIST = TypeAdapter(Dist)


def _container(path: str) -> tuple[str, str, type | None, str, str | None]:
    for prefix, iface, cls, card, ent in _CONTAINERS:
        if path.startswith(prefix):
            return prefix, iface, cls, card, ent
    raise ValueError(f"no FnaDocument container for slot path {path!r}")


def _field_name(path: str, prefix: str) -> str:
    rest = path[len(prefix):]
    return rest.strip(".") if rest else ""


def infer_value_type(name: str, ts_type: str, mod: TsModule) -> str:
    if ts_type == "boolean":
        return "boolean"
    if mod.literal_values(ts_type) is not None:
        return "category"
    if ts_type == "string":
        if name == "dateOfBirth" or name.endswith("Date") or name.startswith("dateOf"):
            return "date"
        return "text"
    if ts_type.startswith("number"):
        if name == "targetYear":
            return "year"
        if name.endswith("Age"):
            return "age"
        if name.endswith("Years"):
            return "years"
        if name.endswith("Months"):
            return "months"
        if name.endswith("Rate") or name.endswith("Percent") or name.endswith("Escalation"):
            return "rate"
        if name.endswith("Members"):
            return "count"
        return "amount"
    raise ValueError(f"cannot infer a value type for {name}: {ts_type}")


def infer_amount_unit(name: str) -> str:
    if name.endswith("Monthly") or name in _MONTHLY_NAMES:
        return "rand_monthly"
    if name.endswith("Annual") or name == "annualBonus":
        return "rand_annual"
    return "rand"


def _plain(v: Any) -> Any:
    """YAML-friendly default values."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _label(path: str) -> str:
    name = re.sub(r"\[.*?\]|\{p\}\.?", "", path).strip(".").split(".")[-1] or path
    words = re.sub(r"(?<!^)(?=[A-Z])", " ", name).lower()
    return words[:1].upper() + words[1:]


def build_catalog(src: AppSources, annotations: dict[str, Any]) -> Catalog:
    mod = src.types
    groups = {name: SlotGroup(name=name, **(g or {})) for name, g in annotations["groups"].items()}
    slots: list[SlotDef] = []
    risk_questions = [
        RiskQuestion(id=q.id, dimension=q.dimension, question=q.question, options=[o for o, _ in q.options], scores=[s for _, s in q.options])  # type: ignore[arg-type]
        for q in src.risk_questions
    ]
    for ann in annotations["slots"]:
        path: str = ann["path"]
        group = ann["group"]
        if group not in groups:
            raise ValueError(f"slot {path} names unknown group {group}")
        prefix, iface, cls, cardinality, entity = _container(path)
        name = _field_name(path, prefix)
        forecast_only = bool(ann.get("forecast_only", False))

        if path == "riskProfile.answers[]":
            for q in risk_questions:
                slots.append(_risk_slot(q, ann, group))
            continue

        ts_type = "number" if path == "expenses.items[]" else None
        if iface and not forecast_only:
            fields = mod.interfaces.get(iface)
            if fields is None or name not in fields:
                raise ValueError(f"slot {path}: {iface}.{name} is not in lib/fna/types.ts")
            ts_type = fields[name].type
        value_type = ann.get("value_type") or infer_value_type(name, ts_type or "string", mod)
        if value_type == "amount":
            fna_unit = ann.get("fna_unit") or ("rand_monthly" if path == "expenses.items[]" else infer_amount_unit(name))
        else:
            fna_unit = ann.get("fna_unit") or UNIT_BY_TYPE[value_type]
        categories = ann.get("categories")
        if categories is None and value_type == "category" and ts_type:
            categories = mod.literal_values(ts_type)
        if value_type == "category" and not categories:
            raise ValueError(f"slot {path}: categorical with no categories")
        policy = ann.get("policy") or POLICY_BY_TYPE.get(value_type, "span_normalise")
        if (group in _OPEN_LIST_GROUPS or path.startswith("expenses.custom[]")) and "policy" not in ann:
            policy = "open_list"
        forms = list(ann.get("forms") or FORMS_BY_TYPE[value_type])
        if ann.get("relative") and value_type == "amount":
            forms.append("AMOUNT_RELATIVE")
        default = None
        if cls is not None and name and name in cls.model_fields and not forecast_only:
            default = _plain(cls().model_dump(mode="json").get(name))
        elif path == "expenses.items[]":
            default = 0
        prior = _DIST.validate_python(ann["prior"]) if ann.get("prior") else None
        if prior is not None and categories and isinstance(prior, Categorical):
            missing = set(prior.probs) - set(categories)
            if missing:
                raise ValueError(f"slot {path}: prior has categories {missing} not in {categories}")
        slot_cardinality = "per_person" if "{p}" in path and cardinality != "list" else cardinality
        if "{p}" in path and cardinality == "list":
            slot_cardinality = "list"
        slots.append(
            SlotDef(
                slot_id=path,
                fna_path=path,
                group=group,
                cardinality=slot_cardinality,  # type: ignore[arg-type]
                entity_type=entity,  # type: ignore[arg-type]
                policy=policy,
                value_type=value_type,
                fna_unit=fna_unit,
                allowed_dists=DISTS_BY_TYPE[value_type],
                allowed_forms=forms,
                categories=categories,
                bounds=tuple(ann["bounds"]) if ann.get("bounds") else None,
                question=" ".join(str(ann["question"]).split()),
                extraction_hints=list(ann.get("hints") or []),
                depends_on=list(ann.get("depends_on") or []),
                fna_default=default,
                population_prior=prior,
                sensitivity_tier=ann.get("tier", "C"),
                fna_outputs=list(ann.get("outputs") or []),
                forecast_binding=path if ann.get("forecast") else None,
                exported=not forecast_only,
                store_as="int" if value_type == "count" else "value",
                label=ann.get("label") or _label(path),
            )
        )
    _check_coverage(mod, slots, annotations.get("excluded") or {})
    ids = [s.slot_id for s in slots]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate slot ids: {sorted(dupes)}")
    hashes = dict(src.hashes)
    hashes["pipeline/advisor_intel/schema/annotations.yaml"] = hashlib.sha256(ANNOTATIONS.read_bytes()).hexdigest()[:16]
    return Catalog(
        version=VERSION,
        generated_from=hashes,
        groups=groups,
        slots=slots,
        expense_items=[ExpenseItem(key=e.key, label=e.label, category=e.category, essential=e.essential, hint=e.hint) for e in src.expense_items],
        risk_questions=risk_questions,
        excluded=annotations.get("excluded") or {},
    )


def _risk_slot(q: RiskQuestion, ann: dict[str, Any], group: str) -> SlotDef:
    n = len(q.options)
    centre = [0.1, 0.2, 0.4, 0.2, 0.1] if n == 5 else [1 / n] * n
    path = f"riskProfile.answers[{q.id}]"
    options = "; ".join(f"{o}" for o in q.options)
    return SlotDef(
        slot_id=path,
        fna_path=path,
        group=group,
        cardinality="one",
        policy="categorical",
        value_type="category",
        fna_unit="category",
        allowed_dists=DISTS_BY_TYPE["category"],
        allowed_forms=["CATEGORY"],
        categories=list(q.options),
        question=f"{q.question} Options: {options}.",
        extraction_hints=list(ann.get("hints") or []),
        fna_default=None,
        population_prior=Categorical(probs=dict(zip(q.options, centre))),
        sensitivity_tier=ann.get("tier", "B"),
        fna_outputs=list(ann.get("outputs") or []),
        store_as="index",
        label=f"Risk: {q.id.replace('-', ' ')}",
    )


def _check_coverage(mod: TsModule, slots: list[SlotDef], excluded: dict[str, Any]) -> None:
    """Every field of every FnaDocument interface is a slot or excluded with a reason."""
    covered: dict[str, set[str]] = {}
    for s in slots:
        if not s.exported:
            continue
        if s.slot_id.startswith("riskProfile.answers["):
            covered.setdefault("RiskProfile", set()).add("answers")
            continue
        if s.slot_id == "expenses.items[]":
            continue
        prefix, iface, _, _, _ = _container(s.slot_id)
        if iface:
            covered.setdefault(iface, set()).add(_field_name(s.slot_id, prefix))
    problems: list[str] = []
    for iface, fields in mod.interfaces.items():
        rule = excluded.get(iface)
        if isinstance(rule, str):
            continue  # whole interface excluded
        reasons = rule or {}
        if iface not in covered and not reasons:
            problems.append(f"interface {iface} is neither covered by slots nor excluded")
            continue
        for fname in fields:
            if fname not in covered.get(iface, set()) and fname not in reasons:
                problems.append(f"{iface}.{fname} is neither a slot nor excluded in annotations.yaml")
        for fname in reasons:
            if fname not in fields:
                problems.append(f"annotations.yaml excludes {iface}.{fname}, which types.ts does not have")
    if problems:
        raise ValueError("slot catalog does not cover lib/fna/types.ts:\n- " + "\n- ".join(problems))


def render(cat: Catalog) -> str:
    data = cat.model_dump(mode="json", exclude_none=True)
    for s in data["slots"]:
        for key in ("allowed_dists",):
            s.pop(key, None) if s.get(key) == ["point"] and s.get("value_type") == "text" else None
    header = (
        "# GENERATED by `python -m advisor_intel.schema.generate` from lib/fna/types.ts, catalog.ts,\n"
        "# riskProfile.ts and schema/annotations.yaml. Do not edit by hand: edit annotations.yaml and regenerate.\n"
    )
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)


def generate(repo_dir: Path = REPO_DIR) -> str:
    src = read_app_sources(repo_dir)
    annotations = yaml.safe_load(ANNOTATIONS.read_text())
    return render(build_catalog(src, annotations))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed catalog is stale")
    args = ap.parse_args(argv)
    text = generate()
    out = SCHEMA_DIR / f"{VERSION}.yaml"
    if args.check:
        if not out.exists() or out.read_text() != text:
            print(f"{out} is stale: run python -m advisor_intel.schema.generate", file=sys.stderr)
            return 1
        print(f"{out} is up to date")
        return 0
    out.write_text(text)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
