"""Implied facts computed by code (spec §7.6), run after extraction.

A derived fact has ``status="implied"`` (``not_discussed`` when it comes from records rather than the
call), ``derivation`` set to the rule's name, and evidence pointing at its inputs' evidence. Rules
never override what the call stated: they fill gaps or, for net-to-gross income, re-express a stated
figure in the field's basis. Each rule returns op specs for the reducer (actor ``deriver``).

Rules here: date of birth from an ID number; tertiary start "after matric" (±1 year); gross income
from a net take-home figure (the FNA tax module solved in reverse, ±3% for unknown deductions);
the risk questions' horizon, emergency and income-stability answers from other facts; and a home
loan or vehicle finance linked to its asset. Stated ages, grades and "in N years" durations are
turned into dates and ages by the normaliser (§8.3) when the value is extracted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from .. import stats
from ..binding import row_id_for
from ..extract.reducer import apply_state_ops, make_ops
from ..models.dists import Bernoulli, Categorical, Point, Quantity, Triangular
from ..models.facts import Evidence, Fact, Hypothesis, Provenance
from ..models.fna import FnaDocument
from ..models.profile import Profile
from .slots import Catalog, fact_id_for, load_catalog, person_of

OpSpec = tuple[str, str, str | None, dict[str, Any], str]
LIQUID = ("cash", "money-market", "unit-trust", "shares", "tfsa", "endowment", "offshore", "crypto")


@dataclass
class DeriveContext:
    profile: Profile
    catalog: Catalog
    base: FnaDocument | None
    anchor: date
    provenance: Provenance
    seed: int = 0


def _fact(profile: Profile, slot_id: str, instance: str | None = None) -> Fact | None:
    return profile.facts.get(fact_id_for(slot_id, instance))


def _usable(f: Fact | None) -> bool:
    return f is not None and f.value is not None and f.review.status != "rejected" and f.status in ("stated", "implied", "not_discussed")


def _said(f: Fact | None) -> bool:
    return (
        f is not None
        and f.source in ("call", "followup", "reviewer")
        and f.status in ("stated", "implied", "explicitly_unknown", "contradicted")
    )


def _evidence(*facts: Fact | None) -> list[Evidence]:
    out: list[Evidence] = []
    for f in facts:
        if f is None:
            continue
        for e in f.evidence:
            if e.role in ("supports", "revises"):
                out.append(e.model_copy(update={"role": "supports"}))
    return out[:6]


def _add(
    ctx: DeriveContext,
    slot_id: str,
    instance: str | None,
    fna_path: str,
    dist: Any,
    unit: str,
    rule: str,
    inputs: list[Fact | None],
    *,
    period: str = "none",
    status: str = "implied",
) -> OpSpec | None:
    ev = _evidence(*inputs)
    if status == "implied" and not ev:
        return None  # an implied fact must point at the call's evidence (§3.5)
    payload = {
        "slot_id": slot_id,
        "instance_key": instance,
        "fna_path": fna_path,
        "source": "derived",
        "status": status,
        "hypotheses": [Hypothesis(value=Quantity(dist=dist, unit=unit, period=period), weight=1.0, rationale=f"derived: {rule}")],
        "evidence": ev,
        "derivation": rule,
        "entity_refs": sorted({r for f in inputs if f is not None for r in f.entity_refs}),
        "provenance": ctx.provenance,
    }
    return ("ADD", fact_id_for(slot_id, instance), ev[0].segment_id if ev else None, payload, "deriver")


def _persons(profile: Profile) -> list[str]:
    return ["client"] + (["spouse"] if "person.spouse" in profile.entities else [])


# --------------------------------------------------------------------------- rules


def dob_from_id(ctx: DeriveContext) -> list[OpSpec]:
    """{p}.dateOfBirth from the ID number in the prior FNA (Luhn check and century rules, idNumber.ts)."""
    try:
        from ..fna_engine.id_number import parse_id
    except ImportError:  # the engine port is optional for derivations
        return []
    out: list[OpSpec] = []
    for p in _persons(ctx.profile):
        if _usable(_fact(ctx.profile, "{p}.dateOfBirth", p)) or ctx.base is None:
            continue
        idn = getattr(getattr(ctx.base, p), "idNumber", "")
        parsed = parse_id(idn) if idn else None
        if not parsed:
            continue
        dob = parsed[0] if isinstance(parsed, tuple) else getattr(parsed, "date_of_birth", None)
        if dob is None:
            continue
        spec = _add(
            ctx,
            "{p}.dateOfBirth",
            p,
            f"{p}.dateOfBirth",
            Point(value=dob.isoformat() if hasattr(dob, "isoformat") else str(dob)),
            "date",
            "dob_from_id_number",
            [],
            status="not_discussed",
        )
        if spec:
            out.append(spec)
    return out


def tertiary_after_matric(ctx: DeriveContext) -> list[OpSpec]:
    """'Straight after matric' with nothing else: 18 with ±1 year triangular."""
    out: list[OpSpec] = []
    for f in ctx.profile.facts.values():
        if f.slot_id != "dependants[].tertiaryStartAge" or not _said(f) or f.derivation:
            continue
        if (
            "matric" in (f.said + " " + f.interpretation).lower()
            and f.value is not None
            and isinstance(f.value.dist, Point)
            and float(f.value.dist.value) == 18
        ):
            out.append(
                (
                    "UPDATE",
                    f.fact_id,
                    f.evidence[0].segment_id if f.evidence else None,
                    {
                        "replace_hypotheses": [
                            Hypothesis(
                                value=Quantity(dist=Triangular(low=17, mode=18, high=19), unit="age"),
                                weight=1.0,
                                rationale="after matric: 18 ± 1",
                            )
                        ],
                        "status": "implied",
                        "reason": "tertiary_after_matric",
                    },
                    "deriver",
                )
            )
    return out


def gross_from_net(ctx: DeriveContext) -> list[OpSpec]:
    """A net take-home figure grossed up through the FNA tax module, widened ±3% for unknown deductions."""
    try:
        from ..fna_engine import tables, tax
    except ImportError:
        return []
    out: list[OpSpec] = []
    table = tables.get_table()
    for p in _persons(ctx.profile):
        f = _fact(ctx.profile, "income.{p}.grossMonthly", p)
        if not _said(f) or f.value is None or f.value.basis != "net" or f.derivation == "gross_from_net":
            continue
        dob = _fact(ctx.profile, "{p}.dateOfBirth", p)
        age = 45.0
        if _usable(dob):
            try:
                age = ctx.anchor.year + (ctx.anchor.timetuple().tm_yday / 365.25) - stats.median(dob.value.dist)  # type: ignore[union-attr]
            except (TypeError, ValueError):
                age = 45.0
        income = ctx.base.income.get(p) if ctx.base is not None else None
        kw = {}
        if income is not None:
            kw = {
                "medical_members": int(income.medicalSchemeMembers or 0),
                "medical_payroll_monthly": float(income.medicalAidPayrollMonthly or 0),
                "other_payroll_monthly": float(income.otherPayrollDeductionsMonthly or 0),
                "uif": bool(income.uifApplies),
            }
        rng = np.random.default_rng(ctx.seed)
        nets = stats.sample(f.value.dist, rng, 400).astype(float)
        grossed = np.array([tax.gross_up_monthly(float(n), age=age, table=table, **kw) for n in nets])
        widened = grossed * rng.uniform(0.97, 1.03, size=grossed.size)  # unknown payroll deductions
        dist = stats.fit_quantile_mixture(widened)
        out.append(
            (
                "UPDATE",
                f.fact_id,
                f.evidence[0].segment_id if f.evidence else None,
                {
                    "replace_hypotheses": [
                        Hypothesis(
                            value=Quantity(dist=dist, unit="ZAR", period="monthly", basis="gross"),
                            weight=1.0,
                            rationale=f"grossed up from a net take-home of {stats.median(f.value.dist):,.0f}",
                        )
                    ],
                    "status": "implied",
                    "reason": "gross_from_net",
                    "normalization": {**(f.normalization or {}), "derivation": "gross_from_net", "net": f.value.model_dump(mode="json")},
                },
                "deriver",
            )
        )
        out += _rescale_relatives(ctx.profile, f, stats.median(dist) / max(1e-9, stats.median(f.value.dist)))
    return out


def _rescale_relatives(profile: Profile, ref: Fact, ratio: float) -> list[OpSpec]:
    """Values read relative to a net figure ('a thirteenth cheque') follow it to the gross basis."""
    out: list[OpSpec] = []
    for g in profile.facts.values():
        if g.fact_id == ref.fact_id or g.value is None or g.value.basis != "net" or g.derivation == "gross_from_net":
            continue
        values = (g.normalization or {}).get("values") or []
        if not any(isinstance(v, dict) and (v.get("reference") or {}).get("fact_id") == ref.fact_id for v in values):
            continue
        hyps = [
            h.model_copy(update={"value": h.value.model_copy(update={"dist": stats.scaled(h.value.dist, ratio), "basis": "gross"})})
            if h.value is not None
            else h
            for h in g.hypotheses
        ]
        out.append(
            (
                "UPDATE",
                g.fact_id,
                g.evidence[0].segment_id if g.evidence else None,
                {
                    "replace_hypotheses": hyps,
                    "reason": "gross_from_net",
                    "normalization": {**(g.normalization or {}), "derivation": "gross_from_net", "gross_ratio": ratio},
                },
                "deriver",
            )
        )
    return out


def _risk_slot(ctx: DeriveContext, qid: str):
    return ctx.catalog.slot(f"riskProfile.answers[{qid}]")


def _band_probs(xs: np.ndarray, edges: list[float], labels: list[str]) -> dict[str, float]:
    idx = np.digitize(xs, edges)
    probs = {labels[i]: float(np.mean(idx == i)) for i in range(len(labels))}
    probs = {k: v for k, v in probs.items() if v > 0}
    s = sum(probs.values())
    return {k: v / s for k, v in probs.items()}


def horizon_answer(ctx: DeriveContext) -> list[OpSpec]:
    """riskProfile horizon from the years to the client's retirement, mapped to the question's bands."""
    slot = _risk_slot(ctx, "horizon")
    if _said(_fact(ctx.profile, slot.slot_id)):
        return []
    ra, dob = _fact(ctx.profile, "retirement.{p}.retirementAge", "client"), _fact(ctx.profile, "{p}.dateOfBirth", "client")
    if not (_usable(ra) and _usable(dob)) or not (_said(ra) or _said(dob)):
        return []
    rng = np.random.default_rng(ctx.seed)
    try:
        ages = stats.sample(ra.value.dist, rng, 2000).astype(float)  # type: ignore[union-attr]
        births = stats.sample(dob.value.dist, rng, 2000).astype(float)  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return []
    now = ctx.anchor.year + ctx.anchor.timetuple().tm_yday / 365.25
    years = births + ages - now
    probs = _band_probs(years, [1, 3, 5, 10], list(slot.categories or []))
    spec = _add(ctx, slot.slot_id, None, slot.fna_path, Categorical(probs=probs), "none", "horizon_from_retirement", [ra, dob])
    return [spec] if spec else []


def emergency_answer(ctx: DeriveContext, median_doc: FnaDocument | None) -> list[OpSpec]:
    """riskProfile emergency from liquid assets ÷ essential monthly outgoings (FNA cash-flow logic).

    Needs both sides of the ratio to be known: an expense or debt line from the call and the household's
    assets (an asset discussed on the call, or the prior file's asset rows). Without any asset
    information the answer is not derived — the absence of a mention is not "no savings"."""
    slot = _risk_slot(ctx, "emergency")
    if _said(_fact(ctx.profile, slot.slot_id)) or median_doc is None:
        return []
    inputs = [f for f in ctx.profile.facts.values() if _said(f) and (f.slot_id.startswith("assets[]") or f.slot_id.startswith("expenses."))]
    assets_known = any(f.slot_id.startswith("assets[]") for f in inputs) or bool(median_doc.assets)
    if not inputs or not assets_known:
        return []
    essential_keys = {e.key for e in ctx.catalog.expense_items if e.essential}
    essential = sum(v for k, v in median_doc.expenses.items.items() if k in essential_keys)
    essential += sum(c.amount for c in median_doc.expenses.custom if c.essential)
    essential += sum(debt.monthlyRepayment for debt in median_doc.liabilities)
    liquid = sum(a.value for a in median_doc.assets if a.type in LIQUID)
    if essential <= 0:
        return []
    months = liquid / essential
    probs = _band_probs(np.asarray([months * f for f in (0.8, 0.9, 1.0, 1.1, 1.2)]), [1, 3, 6, 12], list(slot.categories or []))
    spec = _add(ctx, slot.slot_id, None, slot.fna_path, Categorical(probs=probs), "none", "emergency_from_cash_flow", inputs[:4])
    return [spec] if spec else []


_STABILITY: dict[str, dict[int, float]] = {  # employment type → option index (score − 1) → probability
    "salaried": {3: 1.0},
    "commission": {1: 1.0},
    "contract": {1: 1.0},
    "self-employed": {1: 0.5, 2: 0.5},
    "retired": {3: 0.6, 2: 0.4},
    "unemployed": {0: 1.0},
    "student": {0: 0.7, 1: 0.3},
}


def income_stability_answer(ctx: DeriveContext) -> list[OpSpec]:
    """riskProfile income-stability from the client's employment type unless answered directly."""
    slot = _risk_slot(ctx, "income-stability")
    if _said(_fact(ctx.profile, slot.slot_id)):
        return []
    et = _fact(ctx.profile, "{p}.employmentType", "client")
    if not (_usable(et) and _said(et)):
        return []
    labels = slot.categories or []
    probs: dict[str, float] = {}
    for emp, p_emp in stats.categorical_probs(et.value.dist).items():  # type: ignore[union-attr]
        for idx, p in _STABILITY.get(emp, {2: 1.0}).items():
            if idx < len(labels):
                probs[labels[idx]] = probs.get(labels[idx], 0.0) + p_emp * p
    if not probs:
        return []
    s = sum(probs.values())
    spec = _add(
        ctx,
        slot.slot_id,
        None,
        slot.fna_path,
        Categorical(probs={k: v / s for k, v in probs.items()}),
        "none",
        "income_stability_from_employment",
        [et],
    )
    return [spec] if spec else []


def linked_assets(ctx: DeriveContext) -> list[OpSpec]:
    """Home loan ↔ primary residence, vehicle finance ↔ vehicle, when the registry has exactly one candidate."""
    out: list[OpSpec] = []
    pairs = {"home-loan": "primary-residence", "vehicle-finance": "vehicle"}
    asset_types: dict[str, list[str]] = {}
    for f in ctx.profile.facts.values():
        if f.slot_id == "assets[].type" and _usable(f) and f.instance_key:
            asset_types.setdefault(stats.mode_label(f.value.dist), []).append(f.instance_key)  # type: ignore[union-attr]
    for f in ctx.profile.facts.values():
        if f.slot_id != "liabilities[].type" or not _usable(f) or not f.instance_key:
            continue
        target = pairs.get(stats.mode_label(f.value.dist))  # type: ignore[union-attr]
        if not target:
            continue
        link = _fact(ctx.profile, "liabilities[].linkedAssetId", f.instance_key)
        if _usable(link):
            continue
        cands = asset_types.get(target, [])
        if len(cands) != 1:
            continue
        path = f.fna_path.rsplit(".", 1)[0] + ".linkedAssetId"
        spec = _add(
            ctx,
            "liabilities[].linkedAssetId",
            f.instance_key,
            path,
            Point(value=row_id_for(ctx.profile, cands[0])),
            "none",
            "linked_asset_from_registry",
            [f, _fact(ctx.profile, "assets[].type", cands[0])],
        )
        if spec:
            out.append(spec)
    return out


def partner_on_call(ctx: DeriveContext) -> list[OpSpec]:
    """A spouse whose own facts come from the call is a partner in the plan (both persons are analysed only then):
    household.includeSpouse true, and household.maritalStatus married or life partner (which one stays open)."""
    if "person.spouse" not in ctx.profile.entities:
        return []
    spouse = [
        x
        for x in sorted(ctx.profile.facts.values(), key=lambda x: x.fact_id)
        if x.source == "call" and x.status in ("stated", "implied") and person_of(x.fact_id) == "spouse"
    ][:3]
    if not spouse:
        return []
    out: list[OpSpec] = []
    inc = _fact(ctx.profile, "household.includeSpouse")
    if not _said(inc) and not (_usable(inc) and stats.p_true(inc.value.dist) >= 0.5):  # type: ignore[union-attr]
        spec = _add(ctx, "household.includeSpouse", None, "household.includeSpouse", Bernoulli(p=0.95), "none", "partner_on_call", spouse)
        out += [spec] if spec else []
    ms = _fact(ctx.profile, "household.maritalStatus")
    if not _said(ms) and not (_usable(ms) and stats.mode_label(ms.value.dist) in ("married", "life-partner")):  # type: ignore[union-attr]
        spec = _add(
            ctx,
            "household.maritalStatus",
            None,
            "household.maritalStatus",
            Categorical(probs={"married": 0.75, "life-partner": 0.25}),
            "none",
            "partner_on_call",
            spouse,
        )
        out += [spec] if spec else []
    return out


def target_mode_from_target(ctx: DeriveContext) -> list[OpSpec]:
    """retirement.{p}.targetMode from the target the client gave (§7.6): a monthly amount, or one read
    relative to current spending ("about the same as now"), implies ``amount``; a share of salary
    ("70% of what I earn") implies ``percent``. Only when the mode itself was not said."""
    out: list[OpSpec] = []
    for p in _persons(ctx.profile):
        mode = _fact(ctx.profile, "retirement.{p}.targetMode", p)
        if _said(mode):
            continue
        monthly = _fact(ctx.profile, "retirement.{p}.targetMonthly", p)
        percent = _fact(ctx.profile, "retirement.{p}.targetPercent", p)
        said_monthly = _said(monthly) and _usable(monthly)
        said_percent = _said(percent) and _usable(percent)
        if said_monthly == said_percent:
            continue  # neither, or both (the review decides)
        label, src = ("amount", monthly) if said_monthly else ("percent", percent)
        slot = ctx.catalog.slot("retirement.{p}.targetMode")
        if slot.categories and label not in slot.categories:
            continue
        spec = _add(
            ctx,
            "retirement.{p}.targetMode",
            p,
            f"retirement.{p}.targetMode",
            Categorical(probs={label: 1.0}),
            "none",
            "target_mode_from_target",
            [src],
        )
        if spec:
            out.append(spec)
    return out


RULES: list[Callable[[DeriveContext], list[OpSpec]]] = [
    dob_from_id,
    tertiary_after_matric,
    gross_from_net,
    horizon_answer,
    income_stability_answer,
    linked_assets,
    partner_on_call,
    target_mode_from_target,
]


def derive_all(profile: Profile, *, base: FnaDocument | None = None, run_id: str | None = None, seed: int = 0) -> Profile:
    """Apply every rule once, in order (later rules see earlier results)."""
    from ..binding import materialise

    catalog = load_catalog(profile.schema_version)
    ctx = DeriveContext(
        profile=profile,
        catalog=catalog,
        base=base,
        anchor=profile.anchor_date,
        provenance=Provenance(run_id=run_id or profile.run_id, schema_version=catalog.version),
        seed=seed,
    )
    for rule in RULES:
        specs = rule(ctx)
        if specs:
            ctx.profile = apply_state_ops(ctx.profile, make_ops(ctx.profile, specs)).profile
    median = materialise(ctx.profile, "median", base=base, catalog=catalog)
    specs = emergency_answer(ctx, median)
    if specs:
        ctx.profile = apply_state_ops(ctx.profile, make_ops(ctx.profile, specs)).profile
    return ctx.profile
