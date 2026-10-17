"""Builders for Stage 5–6 tests: profiles through the reducer, small transcripts, quantities."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from advisor_intel.extract.reducer import apply_state_ops, make_ops
from advisor_intel.models.dists import Dist, Point, Quantity
from advisor_intel.models.entities import Entity
from advisor_intel.models.facts import Evidence, Hedge, Hypothesis, Provenance
from advisor_intel.models.profile import Profile
from advisor_intel.models.transcript import NumericSpan, QualityReport, Reading, Segment, Transcript, Word
from advisor_intel.schema import fact_id_for, load_catalog, person_of

PROV = Provenance(run_id="wf-1", lexicon_version="1.3", schema_version="profile_v1", calibrator_version="cold-start")
ANCHOR = date(2026, 10, 15)
CATALOG = load_catalog()


def money(x: float, period: str = "monthly") -> Quantity:
    return Quantity(dist=Point(value=x), unit="ZAR", period=period)  # type: ignore[arg-type]


def qty(dist: Dist, unit: str, period: str = "none") -> Quantity:
    return Quantity(dist=dist, unit=unit, period=period)  # type: ignore[arg-type]


def point(v: Any, unit: str = "none", period: str = "none") -> Quantity:
    return Quantity(dist=Point(value=v), unit=unit, period=period)  # type: ignore[arg-type]


def ev(
    seg: str = "s0010",
    quote: str = "about sixty",
    role: str = "supports",
    conf: float = 0.93,
    speaker: str = "client_primary",
    start: int = 0,
) -> Evidence:
    return Evidence(
        segment_id=seg,
        char_start=start,
        char_end=start + len(quote),
        quote=quote,
        speaker_role=speaker,
        t_start=10.0,
        t_end=12.0,
        asr_mean_conf=conf,
        role=role,
        aligner_score=0.9,  # type: ignore[arg-type]
    )


def unit_for(slot_id: str) -> tuple[str, str]:
    from advisor_intel.binding import quantity_unit

    return quantity_unit(CATALOG.slot(slot_id))


class ProfileBuilder:
    """Adds facts with ADD ops through the reducer, so every built profile replays."""

    def __init__(self, *, anchor: date = ANCHOR, spouse: bool = False, call_id: str = "c1") -> None:
        ents = {
            "person.client_primary": Entity(
                entity_id="person.client_primary", type="person", canonical_name="Thabo", attributes={"person_key": "client"}
            ),
            "person.advisor": Entity(entity_id="person.advisor", type="person", canonical_name="Adviser"),
        }
        if spouse:
            ents["person.spouse"] = Entity(
                entity_id="person.spouse", type="person", canonical_name="Lerato", attributes={"person_key": "spouse"}
            )
        self.profile = Profile(call_id=call_id, run_id="wf-1", schema_version="profile_v1", anchor_date=anchor, entities=ents)
        self.specs: list[tuple[str, str, str | None, dict[str, Any], str]] = []

    def entity(self, entity_id: str, etype: str, name: str, **attrs: Any) -> ProfileBuilder:
        ents = dict(self.profile.entities)
        ents[entity_id] = Entity(entity_id=entity_id, type=etype, canonical_name=name, attributes=attrs)  # type: ignore[arg-type]
        self.profile = self.profile.model_copy(update={"entities": ents})
        return self

    def add(
        self,
        slot_id: str,
        instance: str | None,
        values: Quantity | list[tuple[Quantity, float]] | None,
        *,
        status: str = "stated",
        source: str = "call",
        evidence: list[Evidence] | None = None,
        attribution: str = "client",
        prior: Quantity | None = None,
        self_score: float | None = 0.8,
        hedges: Iterable[Hedge] = (),
        **extra: Any,
    ) -> str:
        slot = CATALOG.slot(slot_id)
        person = instance if slot.per_person else None
        row = instance if slot.per_entity and slot_id != "expenses.items[]" else None
        path = slot.fna_path_for(person, row)
        if slot_id == "expenses.items[]":
            path = f"expenses.items[{instance}]"
        if values is None:
            hyps: list[Hypothesis] = []
        elif isinstance(values, Quantity):
            hyps = [Hypothesis(value=values, weight=1.0, rationale="said")]
        else:
            hyps = [Hypothesis(value=v, weight=w, rationale="said") for v, w in values]
        refs = []
        if person:
            refs = ["person.client_primary" if person == "client" else "person.spouse"]
        elif instance and instance in self.profile.entities:
            refs = [instance]
        if evidence is None:
            evidence = [ev()] if status in ("stated", "implied") and source != "prior_fna" else []
        fid = fact_id_for(slot_id, instance)
        payload: dict[str, Any] = {
            "slot_id": slot_id,
            "instance_key": instance,
            "fna_path": path,
            "source": source,
            "status": status,
            "hypotheses": hyps,
            "evidence": evidence,
            "speaker_attribution": attribution,
            "entity_refs": refs,
            "prior_value": prior,
            "self_score": self_score,
            "hedges": list(hedges),
            "provenance": PROV,
            **extra,
        }
        actor = "loader" if source == "prior_fna" else "extractor"
        self.specs.append(("ADD", fid, evidence[0].segment_id if evidence else None, payload, actor))
        return fid

    def seed(self, slot_id: str, instance: str | None, value: Quantity) -> str:
        """A prior-FNA field not discussed in the call (as load_prior seeds it)."""
        return self.add(slot_id, instance, value, status="not_discussed", source="prior_fna", prior=value, evidence=[], self_score=None)

    def build(self) -> Profile:
        if not self.specs:
            return self.profile
        r = apply_state_ops(self.profile, make_ops(self.profile, self.specs))
        assert not r.rejected, [x.reason for x in r.rejected]
        self.profile = r.profile
        self.specs = []
        return self.profile


def segment(
    seg_id: str,
    text: str,
    role: str = "client_primary",
    *,
    t0: float = 0.0,
    conf: float = 0.95,
    confs: list[float] | None = None,
    rewritten: str | None = None,
    role_conf: float = 0.95,
    diar: float = 0.97,
) -> Segment:
    toks = text.split()
    confs = confs or [conf] * len(toks)
    words = [Word(text=t, t_start=t0 + i * 0.4, t_end=t0 + i * 0.4 + 0.35, conf=c) for i, (t, c) in enumerate(zip(toks, confs))]
    return Segment(
        segment_id=seg_id,
        speaker_label="spk_0" if role == "advisor" else "spk_1",
        speaker_role=role,
        role_conf=role_conf,  # type: ignore[arg-type]
        diarization_conf=diar,
        t_start=t0,
        t_end=t0 + 0.4 * max(1, len(toks)),
        words=words,
        text=text,
        rewritten_text=rewritten,
    )


def transcript(
    segments: list[Segment], *, spans: list[NumericSpan] | None = None, quality: QualityReport | None = None, call_id: str = "c1"
) -> Transcript:
    return Transcript(
        call_id=call_id,
        anchor_date=ANCHOR,
        segments=segments,
        asr_provider="fake",
        asr_model_version="fake-1",
        numeric_spans=spans or [],
        quality=quality,
    )


def span(
    span_id: str,
    seg_id: str,
    w0: int,
    w1: int,
    readings: list[tuple[float, float]],
    *,
    second: float | None = None,
    agree: bool | None = None,
) -> NumericSpan:
    return NumericSpan(
        span_id=span_id,
        segment_id=seg_id,
        kind="amount",
        word_start=w0,
        word_end=w1,
        surface="x",
        readings=[Reading(value=v, weight=w) for v, w in readings],
        second_pass_reading=Reading(value=second, weight=1.0) if second is not None else None,
        second_pass_agreement=agree,
    )


__all__ = [
    "ANCHOR",
    "CATALOG",
    "PROV",
    "ProfileBuilder",
    "ev",
    "money",
    "person_of",
    "point",
    "qty",
    "segment",
    "span",
    "transcript",
    "unit_for",
]
