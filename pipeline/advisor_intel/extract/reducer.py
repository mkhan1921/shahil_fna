"""The reducer: the only way a value enters a profile (spec §3.5, §7.5).

``apply_state_ops(profile, ops)`` is pure and deterministic: it returns a new profile and never
mutates its input. Every op is appended to ``ops_log``, so replaying the log from an empty profile
reproduces the facts exactly; ``check_profile`` verifies that and the other invariants of §3.5.

Op semantics (payload models below):

- ``ADD`` creates the fact if missing. On an existing call fact it merges hypotheses (union, then
  renormalise) and appends evidence. On a fact seeded from the prior FNA, CRM or a default, a call
  statement replaces the seed's hypotheses and keeps the seed as ``prior_value``.
- ``UPDATE`` sets hypothesis weights (optionally appending revising hypotheses) and appends
  evidence with ``role="revises"``; reviewer edits replace the hypotheses and keep the original.
- ``RETRACT`` from a change of mind sets ``explicitly_unknown``; from an extraction error
  (verifier) removes the fact, or one hypothesis; from a reviewer it marks the fact rejected.
- ``CONFIRM`` from the extractor sets ``advisor_confirmed_by_client`` and appends evidence; from a
  reviewer it accepts the fact.
- ``MARK_UNKNOWN`` sets ``explicitly_unknown``, clears hypotheses and keeps evidence.
- Ops that name an unknown fact (UPDATE, RETRACT, CONFIRM) are rejected and logged.

After each op the reducer recomputes the fact's ``value`` as the mixture of its hypotheses and
its ``change`` against the prior value, so the next op (and a replay) sees the same fact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .. import stats
from ..models.dists import Point, Quantity
from ..models.facts import Attribution, Evidence, Fact, FactSource, FactStatus, Hedge, Hypothesis, Provenance, ReviewState
from ..models.profile import Profile, StateOp

WEIGHT_TOL = 1e-6
CONFIRM_REL_TOL = 0.02  # §3.6: equal within 2% → confirmed

# Fact fields that verification, calibration and VOI set after extraction. They are annotations
# rather than values, so they are not part of the replay comparison (§3.5).
SCORING_FIELDS = frozenset({"p_correct", "p_raw", "features", "verification", "review_priority", "voi", "auto_accepted"})

SEED_SOURCES: frozenset[str] = frozenset({"prior_fna", "crm", "fna_default"})


# --------------------------------------------------------------------------- payloads


class AddPayload(BaseModel):
    slot_id: str
    instance_key: str | None = None
    fna_path: str
    source: FactSource = "call"
    status: FactStatus = "stated"
    hypotheses: list[Hypothesis] = []
    evidence: list[Evidence] = []
    hedges: list[Hedge] = []
    speaker_attribution: Attribution = "client"
    interpretation: str = ""
    said: str = ""
    entity_refs: list[str] = []
    normalization: dict[str, Any] | None = None
    derivation: str | None = None
    term_resolution: dict[str, Any] | None = None
    self_score: float | None = None
    field_retries: int = 0
    reopened_by_coverage: bool = False
    prior_value: Quantity | None = None
    provenance: Provenance


class UpdatePayload(BaseModel):
    add_hypotheses: list[Hypothesis] = []  # appended before weights apply (a revising statement)
    weights: list[float] | None = None  # one per hypothesis after appending
    replace_hypotheses: list[Hypothesis] | None = None  # reviewer edit: the new value
    evidence: list[Evidence] = []
    status: FactStatus | None = None
    speaker_attribution: Attribution | None = None
    instance_key: str | None = None  # re-attribution to another entity
    fna_path: str | None = None
    entity_refs: list[str] | None = None
    interpretation: str | None = None
    normalization: dict[str, Any] | None = None
    review: ReviewState | None = None
    reason: str | None = None


RetractReason = Literal[
    "change_of_mind",
    "extraction_error",
    "entailment_contradicted",
    "wrong_person",
    "misheard",
    "advisor_said_it",
    "not_what_they_meant",
    "wrong_entity",
    "removed",
    "other",
]


class RetractPayload(BaseModel):
    reason: RetractReason = "extraction_error"
    hypothesis_index: int | None = None  # retract one hypothesis instead of the fact
    evidence: list[Evidence] = []
    review: ReviewState | None = None
    note: str | None = None


class ConfirmPayload(BaseModel):
    evidence: list[Evidence] = []
    review: ReviewState | None = None


class MarkUnknownPayload(BaseModel):
    reason: str = ""
    evidence: list[Evidence] = []
    # Needed only when the fact does not exist yet.
    slot_id: str | None = None
    instance_key: str | None = None
    fna_path: str | None = None
    source: FactSource = "call"
    speaker_attribution: Attribution = "client"
    provenance: Provenance | None = None
    review: ReviewState | None = None


PAYLOADS: dict[str, type[BaseModel]] = {
    "ADD": AddPayload,
    "UPDATE": UpdatePayload,
    "RETRACT": RetractPayload,
    "CONFIRM": ConfirmPayload,
    "MARK_UNKNOWN": MarkUnknownPayload,
}


class RejectedOp(BaseModel):
    op: StateOp
    reason: str


class ReducerResult(BaseModel):
    profile: Profile
    applied: list[StateOp] = []
    rejected: list[RejectedOp] = []
    touched: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- value and change


def merged_value(hypotheses: list[Hypothesis]) -> Quantity | None:
    """The weighted mixture of the hypotheses' values (§3.5: recomputed, never hand-set)."""
    valued = [h for h in hypotheses if h.value is not None and h.weight > 0]
    if not valued:
        return None
    top = max(valued, key=lambda h: h.weight).value
    assert top is not None
    dist = stats.mixture([(h.weight, h.value.dist) for h in valued])  # type: ignore[union-attr]
    bases = {h.value.basis for h in valued}  # type: ignore[union-attr]
    basis = top.basis if len(bases) == 1 else "unknown"
    return Quantity(dist=dist, unit=top.unit, period=top.period, basis=basis, real_or_nominal=top.real_or_nominal)


def _renormalise(hyps: list[Hypothesis]) -> list[Hypothesis]:
    total = sum(h.weight for h in hyps)
    if not hyps:
        return []
    if total <= 0:
        w = 1.0 / len(hyps)
        return [h.model_copy(update={"weight": w}) for h in hyps]
    return [h.model_copy(update={"weight": h.weight / total}) for h in hyps]


def _merge_hypotheses(existing: list[Hypothesis], new: list[Hypothesis]) -> list[Hypothesis]:
    """Union of two hypothesis sets; identical values pool their weight, then renormalise."""
    out = [h.model_copy() for h in existing]
    for h in new:
        for i, e in enumerate(out):
            if e.value == h.value:
                out[i] = e.model_copy(update={"weight": e.weight + h.weight, "revises": e.revises or h.revises})
                break
        else:
            out.append(h)
    return _renormalise(out)


def _same_value(a: Quantity, b: Quantity) -> bool:
    if a.unit != b.unit:
        return False
    if stats.is_categorical(a.dist) or stats.is_categorical(b.dist):
        try:
            return stats.mode_label(a.dist) == stats.mode_label(b.dist)
        except TypeError:
            return False
    if stats.is_boolean(a.dist) or stats.is_boolean(b.dist):
        try:
            return (stats.p_true(a.dist) >= 0.5) == (stats.p_true(b.dist) >= 0.5)
        except TypeError:
            return False
    if isinstance(a.dist, Point) and isinstance(b.dist, Point) and isinstance(a.dist.value, str) and isinstance(b.dist.value, str):
        if not (stats._looks_like_date(a.dist.value) and stats._looks_like_date(b.dist.value)):  # noqa: SLF001
            return a.dist.value.strip().lower() == b.dist.value.strip().lower()
    try:
        ma, mb = stats.median(a.dist), stats.median(b.dist)
    except TypeError:
        return a == b
    if a.unit == "date":
        return abs(ma - mb) < 1 / 365 + 1e-9
    scale = max(abs(ma), abs(mb))
    return abs(ma - mb) <= CONFIRM_REL_TOL * scale + 1e-9


def compute_change(f: Fact) -> str:
    """§3.6 change status against the prior FNA value."""
    if f.change == "removed":
        return "removed"
    if f.source in SEED_SOURCES and f.status == "not_discussed":
        return "unchanged_not_discussed" if f.prior_value is not None else "new"
    if f.prior_value is None:
        return "new"
    if f.value is None:
        return "unchanged_not_discussed" if f.status in ("not_discussed", "explicitly_unknown") else "changed"
    return "confirmed" if _same_value(f.value, f.prior_value) else "changed"


def _finalise(f: Fact) -> Fact:
    hyps = _renormalise(f.hypotheses) if f.hypotheses else []
    f = f.model_copy(update={"hypotheses": hyps})
    f = f.model_copy(update={"value": merged_value(hyps)})
    return f.model_copy(update={"change": compute_change(f)})


# --------------------------------------------------------------------------- apply


def _new_fact(fact_id: str, p: AddPayload) -> Fact:
    return Fact(
        fact_id=fact_id,
        slot_id=p.slot_id,
        instance_key=p.instance_key,
        fna_path=p.fna_path,
        source=p.source,
        prior_value=p.prior_value,
        change="new",
        status=p.status,
        hypotheses=list(p.hypotheses),
        hedges=list(p.hedges),
        interpretation=p.interpretation,
        said=p.said,
        speaker_attribution=p.speaker_attribution,
        evidence=list(p.evidence),
        entity_refs=list(p.entity_refs),
        normalization=p.normalization,
        derivation=p.derivation,
        term_resolution=p.term_resolution,
        self_score=p.self_score,
        field_retries=p.field_retries,
        reopened_by_coverage=p.reopened_by_coverage,
        provenance=p.provenance,
    )


def _append_evidence(f: Fact, ev: list[Evidence], role: str | None = None) -> list[Evidence]:
    out = list(f.evidence)
    seen = {(e.segment_id, e.char_start, e.char_end, e.role) for e in out}
    for e in ev:
        e2 = e.model_copy(update={"role": role}) if role else e
        key = (e2.segment_id, e2.char_start, e2.char_end, e2.role)
        if key not in seen:
            out.append(e2)
            seen.add(key)
    return out


def _apply_one(facts: dict[str, Fact], op: StateOp) -> str | None:
    """Apply one op in place on ``facts``; return a rejection reason or None."""
    try:
        payload = PAYLOADS[op.op].model_validate(op.payload)
    except ValidationError as e:
        return f"invalid {op.op} payload: {e.errors()[0]['msg']}"
    f = facts.get(op.fact_id)

    if op.op == "ADD":
        assert isinstance(payload, AddPayload)
        if op.fact_id.split("#", 1)[0] != payload.slot_id:
            return f"fact id {op.fact_id} does not match slot {payload.slot_id}"
        if f is None:
            facts[op.fact_id] = _new_fact(op.fact_id, payload)
            return None
        if f.source in SEED_SOURCES and payload.source not in SEED_SOURCES:
            # A call statement about a seeded field: the seed becomes the prior.
            prior = f.prior_value if f.prior_value is not None else f.value
            new = _new_fact(op.fact_id, payload).model_copy(
                update={"prior_value": prior, "entity_refs": sorted(set(f.entity_refs) | set(payload.entity_refs)), "review": f.review}
            )
            facts[op.fact_id] = new
            return None
        if payload.source in SEED_SOURCES and f.source not in SEED_SOURCES:
            return None  # a seed never overrides what the call said
        updates: dict[str, Any] = {
            "hypotheses": _merge_hypotheses(f.hypotheses, payload.hypotheses),
            "evidence": _append_evidence(f, payload.evidence),
            "hedges": f.hedges + [h for h in payload.hedges if h not in f.hedges],
            "entity_refs": sorted(set(f.entity_refs) | set(payload.entity_refs)),
        }
        if f.status in ("not_discussed", "explicitly_unknown") and payload.status in ("stated", "implied", "contradicted"):
            updates["status"] = payload.status
        elif f.status != payload.status and payload.status == "contradicted":
            updates["status"] = "contradicted"
        if payload.speaker_attribution != f.speaker_attribution and f.hypotheses:
            updates["speaker_attribution"] = "mixed"
        elif not f.hypotheses:
            updates["speaker_attribution"] = payload.speaker_attribution
        if payload.interpretation and not f.interpretation:
            updates["interpretation"] = payload.interpretation
        facts[op.fact_id] = f.model_copy(update=updates)
        return None

    if op.op == "MARK_UNKNOWN":
        assert isinstance(payload, MarkUnknownPayload)
        if f is None:
            if not (payload.slot_id and payload.fna_path and payload.provenance):
                return "MARK_UNKNOWN on a new fact needs slot_id, fna_path and provenance"
            f = Fact(
                fact_id=op.fact_id,
                slot_id=payload.slot_id,
                instance_key=payload.instance_key,
                fna_path=payload.fna_path,
                source=payload.source,
                status="explicitly_unknown",
                speaker_attribution=payload.speaker_attribution,
                provenance=payload.provenance,
            )
        facts[op.fact_id] = f.model_copy(
            update={
                "status": "explicitly_unknown",
                "hypotheses": [],
                "evidence": _append_evidence(f, payload.evidence),
                "review": payload.review or f.review,
            }
        )
        return None

    if f is None:
        return f"{op.op} names unknown fact {op.fact_id}"

    if op.op == "UPDATE":
        assert isinstance(payload, UpdatePayload)
        updates = {}
        if payload.replace_hypotheses is not None:
            if op.actor == "reviewer" and f.value is not None and f.review.original_value is None:
                updates["review"] = f.review.model_copy(update={"original_value": f.value})
            hyps = list(payload.replace_hypotheses)
        else:
            hyps = list(f.hypotheses) + list(payload.add_hypotheses)
        if payload.weights is not None:
            if len(payload.weights) != len(hyps):
                return f"UPDATE gives {len(payload.weights)} weights for {len(hyps)} hypotheses"
            hyps = [h.model_copy(update={"weight": w}) for h, w in zip(hyps, payload.weights)]
        updates["hypotheses"] = hyps
        if payload.evidence:
            role = None if op.actor == "reviewer" else "revises"
            updates["evidence"] = _append_evidence(f, payload.evidence, role)
        if payload.status is not None:
            if payload.status != f.status and f.review.original_status is None:
                rv = updates.get("review", f.review)
                updates["review"] = rv.model_copy(update={"original_status": f.status})
            updates["status"] = payload.status
            if payload.status in ("stated", "implied") and f.source in SEED_SOURCES and op.actor == "reviewer":
                updates["source"] = "reviewer"
        for k in ("speaker_attribution", "instance_key", "fna_path", "entity_refs", "interpretation", "normalization"):
            v = getattr(payload, k)
            if v is not None:
                updates[k] = v
        if payload.review is not None:
            base = updates.get("review", f.review)
            keep = {k: getattr(base, k) for k in ("original_value", "original_status") if getattr(base, k) is not None}
            updates["review"] = payload.review.model_copy(update=keep)
        facts[op.fact_id] = f.model_copy(update=updates)
        return None

    if op.op == "RETRACT":
        assert isinstance(payload, RetractPayload)
        if payload.hypothesis_index is not None:
            if not (0 <= payload.hypothesis_index < len(f.hypotheses)):
                return f"RETRACT names hypothesis {payload.hypothesis_index} of {len(f.hypotheses)}"
            hyps = [h for i, h in enumerate(f.hypotheses) if i != payload.hypothesis_index]
            if hyps:
                facts[op.fact_id] = f.model_copy(update={"hypotheses": hyps})
            elif op.actor == "reviewer":
                facts[op.fact_id] = f.model_copy(update={"hypotheses": [], "status": "explicitly_unknown"})
            else:
                del facts[op.fact_id]
            return None
        if op.actor == "reviewer":
            review = payload.review or ReviewState(status="rejected")
            review = review.model_copy(update={"status": "rejected", "reason": payload.reason, "note": payload.note or review.note})
            updates = {"review": review}
            if payload.reason == "removed":
                updates["change"] = "removed"
            facts[op.fact_id] = f.model_copy(update=updates)
            return None
        if payload.reason == "change_of_mind":
            facts[op.fact_id] = f.model_copy(
                update={"status": "explicitly_unknown", "hypotheses": [], "evidence": _append_evidence(f, payload.evidence, "revises")}
            )
            return None
        if f.source in SEED_SOURCES:
            return f"{op.actor} cannot remove a seeded fact ({f.source}); only a reviewer can"
        del facts[op.fact_id]
        return None

    if op.op == "CONFIRM":
        assert isinstance(payload, ConfirmPayload)
        if op.actor == "reviewer":
            review = payload.review or ReviewState(status="accepted")
            keep = {k: getattr(f.review, k) for k in ("original_value", "original_status") if getattr(f.review, k) is not None}
            review = review.model_copy(update={"status": review.status if review.status in ("accepted", "edited") else "accepted", **keep})
            facts[op.fact_id] = f.model_copy(update={"review": review, "evidence": _append_evidence(f, payload.evidence)})
            return None
        facts[op.fact_id] = f.model_copy(
            update={"speaker_attribution": "advisor_confirmed_by_client", "evidence": _append_evidence(f, payload.evidence)}
        )
        return None

    return f"unknown op {op.op}"  # pragma: no cover


def apply_state_ops(profile: Profile, ops: list[StateOp], *, validate: bool = False) -> ReducerResult:
    """Apply a batch of ops to a copy of ``profile``. Rejected ops are reported, not raised."""
    facts = {k: v.model_copy() for k, v in profile.facts.items()}
    applied: list[StateOp] = []
    rejected: list[RejectedOp] = []
    touched: list[str] = []
    last_seq = profile.ops_log[-1].seq if profile.ops_log else 0
    for op in ops:
        if op.seq <= last_seq:
            rejected.append(RejectedOp(op=op, reason=f"seq {op.seq} is not after {last_seq}"))
            continue
        reason = _apply_one(facts, op)
        if reason:
            rejected.append(RejectedOp(op=op, reason=reason))
            continue
        applied.append(op)
        last_seq = op.seq
        if op.fact_id in facts:
            facts[op.fact_id] = _finalise(facts[op.fact_id])  # each op sees a consistent fact
        if op.fact_id not in touched:
            touched.append(op.fact_id)
    new = profile.model_copy(update={"facts": facts, "ops_log": list(profile.ops_log) + applied, "version": profile.version + (1 if applied else 0)})
    if validate:
        new.validate(replay=False)
    return ReducerResult(profile=new, applied=applied, rejected=rejected, touched=touched)


def make_ops(profile: Profile, specs: list[tuple[str, str, str | None, dict[str, Any], str]]) -> list[StateOp]:
    """Number a batch of ``(op, fact_id, segment_id, payload, actor)`` tuples after the profile's last op."""
    seq = profile.next_seq()
    out = []
    for i, (kind, fid, seg, payload, actor) in enumerate(specs):
        out.append(StateOp(op=kind, fact_id=fid, segment_id=seg, payload=_jsonable(payload), actor=actor, seq=seq + i))  # type: ignore[arg-type]
    return out


def _jsonable(payload: dict[str, Any]) -> dict[str, Any]:
    """Payloads are stored as JSON (Postgres jsonb, Temporal payloads): dump nested models."""
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, BaseModel):
            out[k] = v.model_dump(mode="json")
        elif isinstance(v, list):
            out[k] = [x.model_dump(mode="json") if isinstance(x, BaseModel) else x for x in v]
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------- invariants


def _content(f: Fact) -> dict[str, Any]:
    return f.model_dump(mode="json", exclude=set(SCORING_FIELDS))


def replay_facts(profile: Profile) -> dict[str, Fact]:
    """Facts from replaying ``ops_log`` on an empty profile."""
    empty = profile.model_copy(update={"facts": {}, "ops_log": [], "version": 0})
    return apply_state_ops(empty, list(profile.ops_log)).profile.facts


def check_profile(profile: Profile, replay: bool = True) -> list[str]:
    problems: list[str] = []
    for fid, f in profile.facts.items():
        if f.fact_id != fid:
            problems.append(f"{fid}: keyed under a different id ({f.fact_id})")
        if f.status in ("stated", "implied") and f.source != "reviewer" and not any(e.role in ("supports", "revises") for e in f.evidence):
            problems.append(f"{fid}: {f.status} with no supporting evidence")
        if f.hypotheses:
            total = sum(h.weight for h in f.hypotheses)
            if abs(total - 1) > WEIGHT_TOL:
                problems.append(f"{fid}: hypothesis weights sum to {total}")
        expected = merged_value(f.hypotheses)
        if expected != f.value:
            problems.append(f"{fid}: value is not the mixture of its hypotheses")
        for ref in f.entity_refs:
            if ref not in profile.entities:
                problems.append(f"{fid}: entity {ref} is not in the registry")
        if f.status != "not_discussed" and f.status != "explicitly_unknown" and not f.hypotheses and f.review.status != "rejected":
            problems.append(f"{fid}: {f.status} with no hypotheses")
    seqs = [op.seq for op in profile.ops_log]
    if any(b <= a for a, b in zip(seqs, seqs[1:])):
        problems.append("ops_log seq is not strictly increasing")
    if replay:
        replayed = replay_facts(profile)
        if set(replayed) != set(profile.facts):
            missing = set(profile.facts) ^ set(replayed)
            problems.append(f"replaying ops_log gives different fact ids: {sorted(missing)[:5]}")
        else:
            for fid, f in profile.facts.items():
                if _content(replayed[fid]) != _content(f):
                    problems.append(f"{fid}: replaying ops_log does not reproduce the fact")
    return problems


def annotate(profile: Profile, fact_id: str, **fields: Any) -> Profile:
    """Set scoring annotations (p_correct, features, verification, voi…) on a fact. Values never go this way."""
    bad = set(fields) - SCORING_FIELDS
    if bad:
        raise ValueError(f"annotate() only sets scoring fields, not {sorted(bad)}")
    f = profile.facts[fact_id]
    facts = dict(profile.facts)
    facts[fact_id] = f.model_copy(update=fields)
    return profile.model_copy(update={"facts": facts})
