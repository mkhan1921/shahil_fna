"""Follow-up answer intake, extraction side (spec §10.6).

A recorded answer becomes a one-segment transcript attributed to ``client_primary``; each fact the
follow-up asked about is re-extracted from it by the same value stage, grounded, normalised and
turned into ops with ``source="followup"``. An answer narrows a vague value, so it replaces the
fact's hypotheses (the earlier evidence stays) rather than mixing with them.
"""

from __future__ import annotations

from datetime import datetime

from ..lexicon.loader import load_lexicon
from ..models.entities import DiscourseOutput
from ..models.facts import Provenance
from ..models.profile import FollowUp, Profile, StateOp
from ..models.runs import VersionPins
from ..models.transcript import Segment, Transcript, Word
from ..schema.slots import Catalog
from .align import align_values
from .ops import draft_fact
from .probes import ProbeContext, _one_field
from .reducer import make_ops


def answer_transcript(profile: Profile, followup: FollowUp, text: str, *, call_id: str) -> Transcript:
    words = [Word(text=w, t_start=0.0, t_end=0.0, conf=1.0) for w in text.split()]
    seg = Segment(segment_id=f"fu-{followup.followup_id}", speaker_label="followup", speaker_role="client_primary", role_conf=1.0, diarization_conf=1.0,
                  t_start=0.0, t_end=0.0, words=words, text=text)
    return Transcript(call_id=call_id, anchor_date=profile.anchor_date, segments=[seg], asr_provider="followup", asr_model_version="text")


async def extract_followup_answer(
    profile: Profile,
    followup: FollowUp,
    text: str,
    *,
    catalog: Catalog,
    pins: VersionPins,
    run_id: str,
    answered_at: datetime | None = None,
) -> tuple[Transcript, list[StateOp]]:
    """Ops that apply a follow-up answer to the facts it was asked about (not applied here)."""
    t = answer_transcript(profile, followup, text, call_id=profile.call_id)
    ctx = ProbeContext(catalog=catalog, transcript=t, discourse=DiscourseOutput(entities=dict(profile.entities)), pins=pins, run_id=run_id)
    lex = load_lexicon(pins.lexicon_version)
    seg_id = t.segments[0].segment_id
    specs = []
    for fid in followup.fact_ids:
        slot_id, _, instance = fid.partition("#")
        slot = catalog.slot(slot_id)
        out, deps, key = await _one_field(ctx, profile, slot, instance or None, [seg_id], role="reasoning", variant=0)
        if key not in out.values:
            continue
        al = await align_values(deps, out.values, {key: [seg_id]}, {key: slot})
        if key not in al.aligned:
            continue
        draft = draft_fact(al.aligned[key], out.statuses[key], slot, key, instance or None, profile, lex, t)
        if not draft.hypotheses:
            continue
        evidence = [e.model_copy(update={"role": "revises"}) for e in draft.evidence]
        if fid in profile.facts:
            specs.append(("UPDATE", fid, seg_id, {"replace_hypotheses": draft.hypotheses, "status": "stated", "source": "followup", "evidence": evidence,
                                                  "interpretation": draft.value.interpretation if draft.value else None}, "extractor"))
        else:
            prov = Provenance(run_id=run_id, extractor_model=pins.reasoning_model, lexicon_version=lex.version, schema_version=catalog.version)
            specs.append(("ADD", fid, seg_id, {"slot_id": slot.slot_id, "instance_key": instance or None, "fna_path": draft.fna_path, "source": "followup",
                                               "status": "stated", "hypotheses": draft.hypotheses, "evidence": draft.evidence, "hedges": draft.hedges,
                                               "said": draft.value.said if draft.value else "", "provenance": prov}, "extractor"))
    return t, make_ops(profile, specs)
