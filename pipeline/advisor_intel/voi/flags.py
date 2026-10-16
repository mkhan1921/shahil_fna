"""Blocking flags (spec §10.3, §11.2 "Clear flag").

An average hides the one fact that matters, so each call carries flags, each raised by one rule for one
fact. Any open flag blocks review submission and the forecast export until the reviewer clears it,
however good the call's average ``p_correct``:

- ``tier_a_missing`` / ``tier_a_contradicted``: a tier A field on the expected-fact list (§9.1d) is missing
  (no fact, or not discussed with no prior value) or ``contradicted``;
- ``status_flip``: moving a fact across its range flips an FNA need's status (§10.2);
- ``scale_ambiguous``: a tier A amount is still scale-ambiguous, with its top reading below 0.9 (§7.4);
- ``second_pass_disagrees``: the audio re-check disagrees with the chosen reading of a tier A amount (§5.6);
- ``hard_constraint``: a hard constraint fails (§9.1c), one flag per rule and fact;
- ``unresolved_term``: a tier A fact depends on a term the lookup could not resolve (§8.5);
- ``role_review``: the quality report asks the advisor to confirm speaker roles (§5.5), one flag per call.

Recomputing keeps what the reviewer decided: a flag cleared earlier stays cleared, and an open flag whose
condition is gone is closed by the system with the fact's review outcome as its resolution.
"""

from __future__ import annotations

from typing import Iterable

from ..models.facts import Fact
from ..models.profile import BlockingFlag, Profile
from ..models.review import FlagClearance
from ..models.transcript import Transcript
from ..schema.constraints import Violation, check_constraints
from ..schema.slots import Catalog, load_catalog
from ..verify.coverage import catalog_list_item, expected_facts
from ..verify.features import EvidenceIndex, primary_evidence, normalization_flag, second_pass
from .sensitivity import SensitivityRow, need_label

SCALE_TOP_WEIGHT = 0.9
ROLE_CONF_MIN = 0.8


def _flag(kind: str, fact_id: str | None, message: str, rule: str | None = None) -> BlockingFlag:
    parts = [kind] + ([rule] if rule else []) + [fact_id or "call"]
    return BlockingFlag(flag_id=":".join(parts), kind=kind, fact_id=fact_id, message=message)  # type: ignore[arg-type]


def _label(catalog: Catalog, fact_id: str) -> str:
    slot_id, _, inst = fact_id.partition("#")
    try:
        label = catalog.slot(slot_id).label or slot_id
    except KeyError:
        label = slot_id
    return f"{label} ({inst})" if inst else label


def _unresolved(fact: Fact) -> bool:
    t = fact.term_resolution or {}
    return bool(t) and (t.get("status") == "unresolved" or t.get("unresolved") is True or not t.get("candidate"))


def compute_flags(
    profile: Profile,
    *,
    catalog: Catalog | None = None,
    transcript: Transcript | None = None,
    sensitivity: Iterable[SensitivityRow] | None = None,
    violations: list[Violation] | None = None,
) -> list[BlockingFlag]:
    """Every flag the rules raise now, one per rule and fact, in a stable order."""
    catalog = catalog or load_catalog(profile.schema_version)
    flags: dict[str, BlockingFlag] = {}

    def add(fl: BlockingFlag) -> None:
        flags.setdefault(fl.flag_id, fl)

    # Tier A fields on the expected-fact list: missing or contradicted.
    for item in expected_facts(profile, catalog):
        if item.tier != "A":
            continue
        f = profile.facts.get(item.fact_id)
        if catalog_list_item(item):
            add(_flag("tier_a_missing", item.fact_id, f"The call seems to discuss {item.label}, but no such item was extracted."))
        elif f is None or (f.status == "not_discussed" and f.value is None and f.review.status != "rejected"):
            add(_flag("tier_a_missing", item.fact_id, f"No value for {item.label}: ask, or mark it unknown."))
        elif f.status == "contradicted":
            add(_flag("tier_a_contradicted", item.fact_id, f"Conflicting statements about {item.label}."))
    for fid, f in profile.facts.items():
        if f.status == "contradicted" and catalog.slot(f.slot_id).sensitivity_tier == "A" and f.review.status != "rejected":
            add(_flag("tier_a_contradicted", fid, f"Conflicting statements about {_label(catalog, fid)}."))

    # Status flips from the sensitivity runs.
    for row in sensitivity or []:
        if row.status_flip and not row.virtual and row.fact_id in profile.facts:
            first = row.flips[0] if row.flips else ""
            key, _, change = first.partition(" ")
            what = f"{need_label(key)} would go from {change.replace('→', ' to ')}" if change else "an FNA need changes status"
            add(_flag("status_flip", row.fact_id, f"Across its range, {_label(catalog, row.fact_id)} flips a need: {what}."))

    # Tier A amounts: scale ambiguity and the audio second pass.
    index = EvidenceIndex(transcript) if transcript is not None else None
    for fid, f in profile.facts.items():
        if f.review.status == "rejected" or f.status not in ("stated", "implied", "contradicted"):
            continue
        slot = catalog.slot(f.slot_id)
        if slot.sensitivity_tier != "A" or slot.value_type != "amount":
            continue
        top = max((h.weight for h in f.hypotheses), default=1.0)
        if normalization_flag(f, "scale_ambiguous") and top < SCALE_TOP_WEIGHT:
            add(_flag("scale_ambiguous", fid, f"{_label(catalog, fid)} could be heard at another scale (top reading {top:.0%})."))
        if index is not None:
            ran, agrees = second_pass(f, index.overlapping_spans(primary_evidence(f)))
            if ran and agrees is False:
                add(_flag("second_pass_disagrees", fid, f"The audio re-check heard a different amount for {_label(catalog, fid)}."))

    # Hard constraints, one flag per rule and fact.
    if violations is None:
        violations = check_constraints(profile, catalog)
    for v in violations:
        if v.severity != "hard":
            continue
        for fid in v.blamed:
            if fid in profile.facts and profile.facts[fid].review.status != "rejected":
                add(_flag("hard_constraint", fid, f"Fails a check: {v.message}.", rule=v.rule_id))

    # Unresolved terms behind tier A facts (the fact's own, or its entity's type).
    for fid, f in profile.facts.items():
        if f.review.status == "rejected" or catalog.slot(f.slot_id).sensitivity_tier != "A":
            continue
        own = _unresolved(f)
        entity = False
        if not own and f.instance_key and "[]" in f.slot_id:
            prefix = f.slot_id.split("[]", 1)[0] + "[]"
            entity = any(
                _unresolved(g) for g in profile.facts.values() if g.instance_key == f.instance_key and g.slot_id.startswith(prefix)
            )
        if own or entity:
            term = (f.term_resolution or {}).get("term") or "a product or provider name"
            add(_flag("unresolved_term", fid, f"{_label(catalog, fid)} depends on {term!r}, which could not be resolved."))

    # Speaker roles.
    q = transcript.quality if transcript is not None else None
    if q is not None and (q.role_review_needed or q.role_conf_min < ROLE_CONF_MIN):
        add(_flag("role_review", None, f"Confirm who is speaking: the lowest speaker-role confidence is {q.role_conf_min:.0%}."))
    return list(flags.values())


def _resolution(profile: Profile, fact_id: str | None) -> str:
    f = profile.facts.get(fact_id or "")
    if f is None:
        return "dismissed"
    if f.status == "explicitly_unknown":
        return "marked_unknown"
    if f.review.status in ("accepted", "edited"):
        return f.review.status
    return "dismissed"


def merge_flags(existing: Iterable[BlockingFlag], fresh: Iterable[BlockingFlag], profile: Profile) -> list[BlockingFlag]:
    """Recomputed flags with the reviewer's decisions kept (see the module docstring)."""
    old = {f.flag_id: f for f in existing}
    new = {f.flag_id: f for f in fresh}
    out: list[BlockingFlag] = []
    for fid, fl in new.items():
        prev = old.get(fid)
        out.append(
            fl.model_copy(update={"cleared": True, "cleared_by": prev.cleared_by, "resolution": prev.resolution})
            if prev and prev.cleared
            else fl
        )
    for fid, prev in old.items():
        if fid in new:
            continue
        out.append(
            prev
            if prev.cleared
            else prev.model_copy(update={"cleared": True, "cleared_by": "system", "resolution": _resolution(profile, prev.fact_id)})
        )
    return out


def clear_flags(profile: Profile, clearances: Iterable[FlagClearance]) -> Profile:
    """Apply the reviewer's flag clearances (§11.2 'Clear flag')."""
    by_id = {c.flag_id: c for c in clearances}
    flags = [
        f.model_copy(update={"cleared": True, "cleared_by": by_id[f.flag_id].reviewer_id, "resolution": by_id[f.flag_id].resolution})
        if f.flag_id in by_id
        else f
        for f in profile.blocking_flags
    ]
    return profile.model_copy(update={"blocking_flags": flags})


def blocks_submission(profile: Profile) -> bool:
    """Any open flag blocks review submission and the forecast export."""
    return bool(profile.open_flags())
