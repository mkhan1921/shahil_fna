"""Fakes for the evaluation tests: a pipeline runner of the ``process_cached_call(call, pins)`` shape that builds a
real ``Profile`` (through the shared reducer and binding) from a gold call, with optional injected errors."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np

from advisor_intel import binding
from advisor_intel.extract.reducer import apply_state_ops, make_ops
from advisor_intel.models.dists import Bernoulli, Mixture, Point, Quantity, Triangular
from advisor_intel.models.entities import Entity
from advisor_intel.models.facts import Evidence, Hypothesis, Provenance
from advisor_intel.models.profile import BlockingFlag
from advisor_intel.schema.slots import SlotDef, load_catalog

CATALOG = load_catalog()


def quantity_from_gold(slot: SlotDef, gv: Any, *, shift: float = 1.0, contradicted: bool = False) -> Quantity:
    """A Quantity for a gold value: a triangular over p10–p90 (a point when they coincide; two equally weighted
    points for a contradiction, as the reducer's mixture of both statements), labels as points."""
    unit, period = binding.quantity_unit(slot)
    if gv.kind == "numeric":
        if slot.value_type == "probability":
            return Quantity(dist=Bernoulli(p=min(1.0, gv.p50 * shift)), unit="none")
        lo, mid, hi = gv.p10 * shift, gv.p50 * shift, gv.p90 * shift
        if contradicted and hi > lo:
            dist = Mixture(components=[(0.5, Point(value=lo)), (0.5, Point(value=hi))])
        else:
            dist = Point(value=mid) if hi - lo <= 1e-12 else Triangular(low=lo, mode=mid, high=hi)
        return Quantity(dist=dist, unit=unit, period=period)  # type: ignore[arg-type]
    if gv.kind == "boolean":
        return Quantity(dist=Point(value=gv.label == "true"), unit="none")
    return Quantity(dist=Point(value=gv.label), unit="none")


@dataclass
class FakePipeline:
    """Gold facts in, a PipelineResult-shaped object out. Error knobs are fractions of valued facts."""

    gold_for: Callable[[str], Any]
    drop: float = 0.0  # facts the extractor misses
    wrong: float = 0.0  # values scaled by 1.5 (outside any sensible range)
    flip_attribution: float = 0.0
    p_correct: float = 0.9
    auto_accept_rate: float = 0.0
    pos: float | None = 0.8
    seed: int = 0
    note: bool = True
    policy: Callable[[Any, Any, Any], dict[str, Any]] | None = None  # (gold fact, call, pins) → {"drop": bool, "attribution": str}
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def __call__(self, call: Any, pins: Any) -> Any:
        self.calls.append((call.call_id, pins))
        gold = self.gold_for(call.call_id)
        rng = np.random.default_rng([self.seed, abs(hash(call.call_id)) % (2**32)])
        prof = binding.load_prior(call.prior_document, call_id=call.call_id, run_id=f"run-{call.call_id}", anchor_date=gold.anchor_date)
        ents = dict(prof.entities)
        by_row = {e.fna_id: eid for eid, e in ents.items() if e.fna_id}
        emap: dict[str, str] = {}
        for ge in gold.entities:
            if ge.fna_id and ge.fna_id in by_row:
                emap[ge.entity_id] = by_row[ge.fna_id]
            else:
                ents[ge.entity_id] = Entity(entity_id=ge.entity_id, type=ge.type, canonical_name=ge.names[0] if ge.names else ge.entity_id,
                                            aliases=ge.names[1:], attributes={"relation": ge.relation}, fna_list=ge.fna_list, fna_id=ge.fna_id)
                emap[ge.entity_id] = ge.entity_id
        prof = prof.model_copy(update={"entities": ents})
        seg_index = call.transcript.index()
        specs = []
        prov = Provenance(run_id=f"run-{call.call_id}")
        for gf in gold.facts:
            if gf.status == "not_discussed":
                continue
            slot = CATALOG.slot(gf.slot_id)
            inst = emap.get(gf.instance_key, gf.instance_key) if gf.instance_key else None
            fid = f"{gf.slot_id}#{inst}" if inst else gf.slot_id
            path = gf.fna_path or (slot.fna_path_for(inst) if slot.per_person else gf.slot_id)
            ev = []
            for s in gf.spans:
                seg = seg_index.get(s.segment_id)
                if seg is None:
                    continue
                ev.append(Evidence(segment_id=s.segment_id, char_start=s.char_start, char_end=s.char_end, quote=s.quote, speaker_role=seg.speaker_role,
                                   t_start=seg.t_start, t_end=seg.t_end, asr_mean_conf=0.95, role="supports"))
            if gf.status == "explicitly_unknown":
                specs.append(("MARK_UNKNOWN", fid, ev[0].segment_id if ev else None,
                              {"slot_id": gf.slot_id, "instance_key": inst, "fna_path": path, "provenance": prov, "evidence": ev}, "extractor"))
                continue
            decision = self.policy(gf, call, pins) if self.policy else {}
            if gf.value is None or not ev or rng.random() < self.drop or decision.get("drop"):
                continue
            shift = 1.5 if rng.random() < self.wrong else 1.0
            attribution = gf.attribution
            if rng.random() < self.flip_attribution:
                attribution = "advisor_suggested" if attribution != "advisor_suggested" else "client"
            attribution = decision.get("attribution", attribution)
            hyp = Hypothesis(value=quantity_from_gold(slot, gf.value, shift=shift, contradicted=gf.status == "contradicted"), weight=1.0, rationale="fake")
            specs.append(("ADD", fid, ev[0].segment_id, {
                "slot_id": gf.slot_id, "instance_key": inst, "fna_path": path, "status": gf.status, "hypotheses": [hyp], "evidence": ev,
                "speaker_attribution": attribution, "entity_refs": [inst] if inst and inst in ents else [], "provenance": prov,
                "normalization": {"form": {"type": "AMOUNT_RANGE" if gf.value.vague else "AMOUNT_EXACT"}} if gf.value.kind == "numeric" else None,
            }, "extractor"))
        res = apply_state_ops(prof, make_ops(prof, specs))
        assert not res.rejected, res.rejected[:2]
        prof = res.profile
        facts = {}
        for fid, f in prof.facts.items():
            if f.source == "call":
                f = f.model_copy(update={"p_correct": self.p_correct, "p_raw": self.p_correct, "auto_accepted": bool(rng.random() < self.auto_accept_rate),
                                         "features": {"entail_score": 0.9, "consistency": 0.8, "self_score": 0.85, "asr_mean_conf": 0.95}})
            facts[fid] = f
        prof = prof.model_copy(update={"facts": facts})
        note = []
        if self.note:
            for f in sorted(prof.facts.values(), key=lambda x: x.fact_id):
                if f.source == "call" and f.status == "stated" and CATALOG.slot(f.slot_id).sensitivity_tier == "A":
                    note.append(SimpleNamespace(text=f"Recorded {CATALOG.slot(f.slot_id).label.lower()}.", fact_ids=[f.fact_id]))
        flags = [BlockingFlag(flag_id=f"flag-{i}", kind="tier_a_contradicted", fact_id=f.fact_id, message="contradicted")
                 for i, f in enumerate(prof.facts.values()) if f.status == "contradicted"]
        return SimpleNamespace(profile=prof, forecast=SimpleNamespace(pos=self.pos), followups=[], flags=flags, note=note,
                               usage={"cost_usd": 0.5, "input_tokens": 1000.0}, discourse=None)
