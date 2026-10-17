"""From grounded field values to state ops (spec §7.3 steps 4–8, §7.4).

For each aligned field value: scale-resolve spoken amounts against the grammar readings of the
numeric spans under its evidence (the model never picks the scale), normalise every intermediate
form through the versioned lexicon into the slot's FnaDocument unit, drop values outside the slot's
bounds, map hedges to lexicon strengths, weight the hypotheses (client weights, else equal; then
recency for revisions), and emit ``ADD`` for new facts, ``UPDATE`` for revisions of facts already
extracted from this call, and ``MARK_UNKNOWN`` for explicitly unknown fields.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..lexicon.bounds import filter_bounds
from ..lexicon.hedges import lookup_hedges
from ..lexicon.loader import Lexicon
from ..lexicon.normalize import Normalized, normalize_value
from ..lexicon.weighting import build_hypotheses
from ..models.extraction import FieldStatus, FieldValue
from ..models.facts import Evidence, FactStatus, Hedge, Hypothesis, Provenance
from ..models.fna import Assumptions
from ..models.profile import Profile
from ..models.transcript import NumericSpan, Transcript
from ..schema.slots import SlotDef, person_of
from .align import Aligned, evidence_for
from .scale import ScaleContext, resolve_hypotheses

OpSpec = tuple[str, str, str | None, dict[str, Any], str]


@dataclass
class FactDraft:
    key: str
    slot: SlotDef
    instance: str | None
    fna_path: str
    status: FactStatus
    value: FieldValue | None
    hypotheses: list[Hypothesis]
    hedges: list[Hedge]
    evidence: list[Evidence]
    normalization: dict[str, Any] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    aligner_score: float | None = None
    retries: int = 0
    failure: str | None = None


def spans_for(transcript: Transcript, segment_ids: list[str]) -> list[NumericSpan]:
    ids = set(segment_ids)
    return [s for s in transcript.numeric_spans if s.segment_id in ids]


def normalise_value(
    fv: FieldValue,
    slot: SlotDef,
    key: str,
    profile: Profile,
    lex: Lexicon,
    transcript: Transcript,
    segment_ids: list[str],
    *,
    assumptions: Assumptions | None = None,
    consistency: Callable[[float], float] | None = None,
) -> tuple[list[Hypothesis], dict[str, Any], list[str], list[str]]:
    """(hypotheses, normalization record, flags, dropped log) for one field value."""
    person = person_of(key)
    scaled = resolve_hypotheses(fv.hypotheses, spans_for(transcript, segment_ids), slot, context=ScaleContext.for_fact(slot, profile, key, consistency=consistency))
    values: list[Normalized | None] = []
    for it in scaled.items:
        try:
            values.append(normalize_value(it.raw.intermediate, slot, profile, lex, anchor_date=profile.anchor_date, fact_id=key, person=person, assumptions=assumptions,
                                          priced_year=getattr(it.raw.intermediate, "priced_year", 0) or None))
        except (ValueError, TypeError, KeyError) as e:  # a form the normaliser cannot read: dropped, logged
            values.append(None)
            scaled.flags.append(f"normalisation_error:{type(e).__name__}")
    bounds = filter_bounds(values, slot, fact_id=key)
    hyps = build_hypotheses([it.raw for it in scaled.items], bounds.values, lex, groups=scaled.groups, scale_weights=scaled.scale_weights)
    flags = list(dict.fromkeys([*scaled.flags, *(f for v in values if v is not None for f in v.flags)]))
    record = {
        "forms": [it.raw.intermediate.model_dump(mode="json") for it in scaled.items],
        "lexicon_version": lex.version,
        "values": [v.normalization if v is not None else None for v in values],
        "scale": scaled.features,
        "flags": flags,
    }
    return hyps, record, flags, list(bounds.dropped)


def field_problems(fv: FieldValue, slot: SlotDef, key: str, profile: Profile, lex: Lexicon, transcript: Transcript, segment_ids: list[str],
                   assumptions: Assumptions | None = None) -> list[str]:
    """Validation for stage 3 (§7.2): forms that cannot be normalised, or values all outside the slot's bounds."""
    try:
        hyps, _rec, flags, dropped = normalise_value(fv, slot, key, profile, lex, transcript, segment_ids, assumptions=assumptions)
    except Exception as e:  # noqa: BLE001 - every failure becomes a retry message for the model
        return [f"the value could not be read: {type(e).__name__}: {str(e)[:160]}"]
    problems = []
    if not hyps:
        if dropped:
            lo, hi = slot.bounds or (None, None)
            problems.append(f"every value is outside the plausible range {lo:g}–{hi:g} for this field; check the scale and the unit" if slot.bounds else "the value is not usable")
        elif any(f in flags for f in ("form_slot_mismatch", "form_not_allowed", "label_unmatched")):
            problems.append("the form or label does not fit this field: use one of the allowed forms and labels")
        else:
            problems.append("no usable value: return the value as spoken in an allowed form")
    return problems


def draft_fact(
    aligned: Aligned,
    status: FieldStatus,
    slot: SlotDef,
    key: str,
    instance: str | None,
    profile: Profile,
    lex: Lexicon,
    transcript: Transcript,
    *,
    assumptions: Assumptions | None = None,
    consistency: Callable[[float], float] | None = None,
    retries: int = 0,
) -> FactDraft:
    fv = aligned.value
    seg_ids = list(dict.fromkeys([e.segment_id for e in aligned.evidence] + status.segment_ids))
    hyps, record, flags, dropped = normalise_value(fv, slot, key, profile, lex, transcript, seg_ids, assumptions=assumptions, consistency=consistency)
    hedges = lookup_hedges(fv.hedges, lex)
    record["hedge_lookups"] = hedges.lookups
    for f in hedges.flags:
        if f not in flags:
            flags.append(f)
    return FactDraft(key=key, slot=slot, instance=instance, fna_path=fv.fna_path, status=status.status, value=fv, hypotheses=hyps, hedges=hedges.hedges,
                     evidence=aligned.evidence, normalization=record, dropped=dropped, flags=flags, aligner_score=aligned.aligner_score, retries=retries)


def _entity_refs(profile: Profile, draft: FactDraft, mentions_by_segment: dict[str, set[str]]) -> list[str]:
    refs: set[str] = set()
    if draft.instance and draft.instance in profile.entities:
        refs.add(draft.instance)
    person = person_of(draft.key)
    if person == "client" and "person.client_primary" in profile.entities:
        refs.add("person.client_primary")
    if person == "spouse" and "person.spouse" in profile.entities:
        refs.add("person.spouse")
    for e in draft.evidence:
        for ent in mentions_by_segment.get(e.segment_id, set()):
            if ent in profile.entities:
                refs.add(ent)
    return sorted(refs)


def to_state_ops(
    drafts: list[FactDraft],
    unknown: list[tuple[str, SlotDef, str | None, str, list[Evidence]]],
    profile: Profile,
    provenance: Provenance,
    mentions_by_segment: dict[str, set[str]],
    lex: Lexicon | None = None,
) -> tuple[list[OpSpec], list[str]]:
    """Op specs (for ``reducer.make_ops``) and a log of facts dropped for lack of a usable value (§7.3 step 8).

    ``lex`` supplies the recency weights for an UPDATE that revises an earlier extraction; without it
    the pinned lexicon of the drafts' provenance is loaded."""
    if lex is None:
        from ..lexicon.loader import CURRENT_VERSION, load_lexicon

        lex = load_lexicon(provenance.lexicon_version or CURRENT_VERSION)
    specs: list[OpSpec] = []
    dropped: list[str] = []
    for d in drafts:
        if not d.hypotheses and d.status != "contradicted":
            dropped.append(f"{d.key}: no usable value after normalisation and bounds ({'; '.join(d.dropped) or ', '.join(d.flags) or 'empty'})")
            continue
        hyps = d.hypotheses or [Hypothesis(value=None, weight=1.0, rationale=d.failure or "the value could not be read; see the quote")]
        seg = d.evidence[0].segment_id if d.evidence else None
        existing = profile.facts.get(d.key)
        payload: dict[str, Any] = {
            "slot_id": d.slot.slot_id,
            "instance_key": d.instance,
            "fna_path": d.fna_path,
            "source": "call",
            "status": d.status,
            "hypotheses": hyps,
            "evidence": d.evidence,
            "hedges": d.hedges,
            "speaker_attribution": d.value.speaker_attribution if d.value else "client",
            "interpretation": d.value.interpretation if d.value else "",
            "said": d.value.said if d.value else "",
            "entity_refs": _entity_refs(profile, d, mentions_by_segment),
            "normalization": {**d.normalization, "aligner_score": d.aligner_score, "failure": d.failure},
            "self_score": d.value.self_score if d.value else None,
            "field_retries": d.retries,
            "provenance": provenance,
        }
        if existing is not None and existing.source == "call" and any(h.revises for h in hyps):
            # A later extraction revises what this call already said: append and re-weight (§7.5 UPDATE),
            # the revising statement taking the lexicon's recency share (§7.3 step 7).
            add = [h for h in hyps if h.value not in {x.value for x in existing.hypotheses}]
            n_old, n_new = len(existing.hypotheses), len(add)
            if n_new:
                rw = lex.recency_weights()
                old_total = max(1e-9, sum(h.weight for h in existing.hypotheses))
                old_w = [h.weight * rw.revised / old_total for h in existing.hypotheses]
                new_w = [h.weight * rw.revising / max(1e-9, sum(x.weight for x in add)) for h in add]
                specs.append(("UPDATE", d.key, seg, {"add_hypotheses": add, "weights": old_w + new_w, "evidence": d.evidence}, "extractor"))
                continue
            if n_old:
                specs.append(("UPDATE", d.key, seg, {"evidence": d.evidence}, "extractor"))
                continue
        specs.append(("ADD", d.key, seg, payload, "extractor"))
    for key, slot, instance, fna_path, evidence in unknown:
        specs.append(("MARK_UNKNOWN", key, evidence[0].segment_id if evidence else None,
                      {"reason": "the client said they don't know or haven't decided", "evidence": evidence, "slot_id": slot.slot_id, "instance_key": instance,
                       "fna_path": fna_path, "provenance": provenance}, "extractor"))
    return specs, dropped


def status_evidence(transcript: Transcript, segment_ids: list[str]) -> list[Evidence]:
    """Whole-segment evidence for a status without a value (explicitly unknown, contradicted)."""
    idx = transcript.index()
    return [evidence_for(idx[s], 0, len(idx[s].text)) for s in segment_ids if s in idx]
