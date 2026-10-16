"""Entity registry and reference resolution (spec §6.1, §6.2).

Two passes. **Seed**: the registry starts from the prior profile's entities (the client's FNA rows
and CRM products, linked by ``load_prior``) plus ``person.client_primary`` and ``person.advisor``.
**Discover**: windows of ~40 segments with a 10-segment overlap go to the frontier model with the
current registry; each window returns new entities, mentions and merges. Mentions are resolved
jointly per window, speaker-relative words by speaker role, and anything below 0.7 confidence
stays unresolved and marks the segment as an ambiguous reference. A final merge pass merges
same-type entities with overlapping aliases or matching attributes when the model agrees with
confidence ≥ 0.8; otherwise both are kept and a possible duplicate goes to review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, WithJsonSchema
from pydantic_ai import Agent, NativeOutput
from rapidfuzz import fuzz

from .. import llm
from ..embeddings import Embedder, cosine, get_embedder
from ..models.crm import CrmRecord
from ..models.entities import Entity, EntityType, Mention
from ..models.runs import VersionPins
from ..models.transcript import Segment, Transcript
from ..prompts import pinned
from .text import render_segment

WINDOW = 40
OVERLAP = 10
RESOLVE_MIN = 0.7  # below this a mention stays unresolved (§6.2)
MERGE_MIN = 0.8  # merges need this much agreement (§6.1)
CRM_TOP_K = 3

ENTITY_TYPES: tuple[str, ...] = ("person", "account", "policy", "property", "business", "liability", "goal", "institution")
_EntityTypeOut = Annotated[Literal[ENTITY_TYPES], WithJsonSchema({"type": "string", "enum": list(ENTITY_TYPES)})]  # type: ignore[valid-type]


class KeyValue(BaseModel):
    key: str
    value: str


class NewEntityOut(BaseModel):
    ref: str = Field(description="'new:<short name>', used by this window's mentions")
    type: _EntityTypeOut
    canonical_name: str
    relation: str = Field(description="relation to the client ('daughter', 'mother'), or ''")
    attributes: list[KeyValue] = Field(description="other attributes as key/value pairs: age, provider, product type, amount")
    first_segment_id: str


class MentionOut(BaseModel):
    segment_id: str
    surface: str = Field(description="the exact words in the segment text")
    char_start: int
    char_end: int
    entity_id: str = Field(description="a registry id, a 'new:' ref from this window, or '' when unresolved")
    confidence: float


class MergeOut(BaseModel):
    keep_id: str
    merge_id: str
    confidence: float


class WindowOut(BaseModel):
    new_entities: list[NewEntityOut]
    mentions: list[MentionOut]
    merges: list[MergeOut]


class MergeDecision(BaseModel):
    a: str
    b: str
    same: bool
    confidence: float


class MergeDecisions(BaseModel):
    decisions: list[MergeDecision]


window_agent: Agent[None, WindowOut] = Agent(None, output_type=NativeOutput(WindowOut), name="discourse_entities")
merge_agent: Agent[None, MergeDecisions] = Agent(None, output_type=NativeOutput(MergeDecisions), name="discourse_merges")


# --------------------------------------------------------------------------- deterministic rules

_SPOUSE_WORDS = re.compile(r"\b(my|our)\s+(wife|husband|partner|spouse|hubby|vrou|man)\b", re.I)


def speaker_relative_mentions(seg: Segment, entities: dict[str, Entity]) -> list[Mention]:
    """'my wife' said by the primary client is the spouse; said by the secondary client, the primary (§6.2)."""
    out: list[Mention] = []
    for m in _SPOUSE_WORDS.finditer(seg.text):
        target = None
        if seg.speaker_role == "client_primary" and "person.spouse" in entities:
            target = "person.spouse"
        elif seg.speaker_role == "client_secondary":
            target = "person.client_primary"
        out.append(Mention(segment_id=seg.segment_id, char_start=m.start(), char_end=m.end(), surface=m.group(0), entity_id=target,
                           resolution_conf=0.95 if target else 0.0))
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:24] or "item"


def new_entity_id(entities: dict[str, Entity], etype: str, name: str) -> str:
    base = f"{etype}.{_slug(name)}"
    n = 1
    while f"{base}_{n}" in entities:
        n += 1
    return f"{base}_{n}"


def _fix_offsets(seg: Segment, m: MentionOut) -> tuple[int, int] | None:
    text = seg.text
    if 0 <= m.char_start < m.char_end <= len(text) and text[m.char_start : m.char_end].lower() == m.surface.lower():
        return m.char_start, m.char_end
    if not m.surface:
        return None
    i = text.lower().find(m.surface.lower())
    return (i, i + len(m.surface)) if i >= 0 else None


# --------------------------------------------------------------------------- the registry


@dataclass
class Registry:
    entities: dict[str, Entity]
    mentions: list[Mention] = field(default_factory=list)
    ambiguous: set[str] = field(default_factory=set)
    possible_duplicates: list[tuple[str, str]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0})

    def summary(self, limit: int = 60) -> str:
        lines = []
        for e in list(self.entities.values())[:limit]:
            attrs = ", ".join(f"{k}={v}" for k, v in e.attributes.items() if v not in ("", None))
            aliases = f" aka {', '.join(e.aliases[:4])}" if e.aliases else ""
            lines.append(f"- {e.entity_id} ({e.type}): {e.canonical_name}{aliases}{' [' + attrs + ']' if attrs else ''}")
        return "\n".join(lines)

    def add_mention(self, m: Mention) -> None:
        key = (m.segment_id, m.char_start, m.char_end)
        for i, old in enumerate(self.mentions):
            if (old.segment_id, old.char_start, old.char_end) == key:
                if m.resolution_conf > old.resolution_conf:
                    self.mentions[i] = m
                return
        self.mentions.append(m)
        if m.entity_id is None:
            self.ambiguous.add(m.segment_id)
        elif m.entity_id in self.entities:
            e = self.entities[m.entity_id]
            if m.surface and m.surface.lower() not in {a.lower() for a in e.aliases} and len(m.surface) <= 40:
                self.entities[m.entity_id] = e.model_copy(update={"aliases": [*e.aliases, m.surface]})
            if e.first_mention is None:
                self.entities[m.entity_id] = self.entities[m.entity_id].model_copy(update={"first_mention": m.segment_id})

    def merge(self, keep: str, drop: str) -> None:
        if keep not in self.entities or drop not in self.entities or keep == drop:
            return
        k, d = self.entities[keep], self.entities[drop]
        if k.type != d.type:
            return
        merged = k.model_copy(
            update={
                "aliases": sorted({*k.aliases, *d.aliases, d.canonical_name} - {k.canonical_name}),
                "attributes": {**d.attributes, **k.attributes},
                "crm_match": k.crm_match or d.crm_match,
                "fna_list": k.fna_list or d.fna_list,
                "fna_id": k.fna_id or d.fna_id,
                "first_mention": k.first_mention or d.first_mention,
            }
        )
        self.entities[keep] = merged
        del self.entities[drop]
        self.mentions = [m.model_copy(update={"entity_id": keep}) if m.entity_id == drop else m for m in self.mentions]


def seed_registry(prior_entities: dict[str, Entity] | None, crm: CrmRecord | None) -> Registry:
    ents = dict(prior_entities or {})
    ents.setdefault("person.client_primary", Entity(entity_id="person.client_primary", type="person", canonical_name="Client", attributes={"person_key": "client"}))
    ents.setdefault("person.advisor", Entity(entity_id="person.advisor", type="person", canonical_name=(crm.adviser_name if crm else "") or "Adviser",
                                             attributes={"role": "advisor"}))
    return Registry(entities=ents)


def windows(segments: list[Segment], size: int = WINDOW, overlap: int = OVERLAP) -> list[list[Segment]]:
    if not segments:
        return []
    step = max(1, size - overlap)
    out = []
    for start in range(0, len(segments), step):
        out.append(segments[start : start + size])
        if start + size >= len(segments):
            break
    return out


def _track(reg: Registry, usage: Any) -> None:
    reg.usage["requests"] += getattr(usage, "requests", 0) or 0
    reg.usage["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
    reg.usage["output_tokens"] += getattr(usage, "output_tokens", 0) or 0


def apply_window(reg: Registry, window: list[Segment], out: WindowOut) -> None:
    """Fold one window's model output into the registry, validating every id and offset."""
    by_id = {s.segment_id: s for s in window}
    refs: dict[str, str] = {}
    for ne in out.new_entities:
        if ne.type not in ENTITY_TYPES or not ne.canonical_name.strip():
            continue
        existing = next(
            (e for e in reg.entities.values() if e.type == ne.type and fuzz.token_set_ratio(e.canonical_name, ne.canonical_name) >= 95),
            None,
        )
        if existing is not None:
            refs[ne.ref] = existing.entity_id
            continue
        eid = new_entity_id(reg.entities, ne.type, ne.canonical_name)
        attrs: dict[str, Any] = {kv.key: kv.value for kv in ne.attributes if kv.key}
        if ne.relation:
            attrs["relation"] = ne.relation
        reg.entities[eid] = Entity(entity_id=eid, type=ne.type, canonical_name=ne.canonical_name.strip(), aliases=[],
                                   attributes=attrs, first_mention=ne.first_segment_id if ne.first_segment_id in by_id else window[0].segment_id)
        refs[ne.ref] = eid
    for mo in out.mentions:
        seg = by_id.get(mo.segment_id)
        if seg is None:
            continue
        span = _fix_offsets(seg, mo)
        if span is None:
            continue
        target = refs.get(mo.entity_id, mo.entity_id) if mo.entity_id else None
        conf = max(0.0, min(1.0, mo.confidence))
        if target is not None and (target not in reg.entities or conf < RESOLVE_MIN):
            target = None
        reg.add_mention(Mention(segment_id=seg.segment_id, char_start=span[0], char_end=span[1], surface=seg.text[span[0] : span[1]],
                                entity_id=target, resolution_conf=conf))
    for mg in out.merges:
        keep, drop = refs.get(mg.keep_id, mg.keep_id), refs.get(mg.merge_id, mg.merge_id)
        if mg.confidence >= MERGE_MIN:
            reg.merge(keep, drop)


def _window_prompt(reg: Registry, window: list[Segment], rewrites: dict[str, str]) -> str:
    segs = "\n".join(render_segment(s, rewrite=rewrites.get(s.segment_id)) for s in window)
    return f"## Registry\n{reg.summary()}\n\n## Segments\n{segs}"


async def discover(reg: Registry, transcript: Transcript, pins: VersionPins, *, use_llm: bool) -> Registry:
    """Run the discovery windows (§6.1 step 2) and the speaker-relative rules (§6.2)."""
    for seg in transcript.segments:
        for m in speaker_relative_mentions(seg, reg.entities):
            reg.add_mention(m)
    if not use_llm:
        return reg
    rm = llm.for_role("frontier", pins)
    prompt = pinned("discourse_entities", pins.prompt_versions)
    for win in windows(transcript.segments):
        result = await window_agent.run(_window_prompt(reg, win, {}), model=rm.model, model_settings=rm.settings, instructions=prompt.text)
        _track(reg, result.usage)
        apply_window(reg, win, result.output)
    return reg


def merge_candidates(reg: Registry) -> list[tuple[str, str]]:
    """Same-type pairs with alias overlap, or the same relation and an age within two years (§6.1)."""
    ents = list(reg.entities.values())
    pairs: list[tuple[str, str]] = []
    for i, a in enumerate(ents):
        for b in ents[i + 1 :]:
            if a.type != b.type or (a.fna_id and b.fna_id and a.fna_id != b.fna_id):
                continue
            names_a = {x.lower() for x in [a.canonical_name, *a.aliases] if len(x) > 2}
            names_b = {x.lower() for x in [b.canonical_name, *b.aliases] if len(x) > 2}
            alias_overlap = bool(names_a & names_b) or fuzz.token_set_ratio(a.canonical_name, b.canonical_name) >= 90
            rel_a, rel_b = a.attributes.get("relation"), b.attributes.get("relation")
            age_match = False
            if rel_a and rel_a == rel_b:
                try:
                    age_match = abs(float(a.attributes.get("age", "nan")) - float(b.attributes.get("age", "nan"))) <= 2
                except (TypeError, ValueError):
                    age_match = False
            if alias_overlap or age_match:
                pairs.append((a.entity_id, b.entity_id))
    return pairs


async def merge_pass(reg: Registry, pins: VersionPins, *, use_llm: bool) -> Registry:
    pairs = merge_candidates(reg)
    if not pairs:
        return reg
    if not use_llm:
        reg.possible_duplicates.extend(pairs)
        return reg
    rm = llm.for_role("frontier", pins)
    listing = "\n".join(f"- {a} vs {b}" for a, b in pairs)
    result = await merge_agent.run(
        f"## Registry\n{reg.summary(200)}\n\n## Candidate pairs\n{listing}\n\nFor each pair: are they the same real-world thing? Give a confidence.",
        model=rm.model,
        model_settings=rm.settings,
        instructions="You decide whether two registry entries from a financial adviser's call are the same person or thing.",
    )
    _track(reg, result.usage)
    decided = {(d.a, d.b): d for d in result.output.decisions}
    for a, b in pairs:
        d = decided.get((a, b)) or decided.get((b, a))
        if d is not None and d.same and d.confidence >= MERGE_MIN:
            keep, drop = (a, b) if (reg.entities.get(a) and reg.entities[a].fna_id) or not (reg.entities.get(b) and reg.entities[b].fna_id) else (b, a)
            reg.merge(keep, drop)
        elif a in reg.entities and b in reg.entities:
            reg.possible_duplicates.append((a, b))
    return reg


# --------------------------------------------------------------------------- CRM linking (§6.1)

_PRODUCT_WORDS: dict[str, tuple[str, ...]] = {
    "policy": ("life", "cover", "policy", "disability", "funeral", "income protection", "severe illness"),
    "account": ("ra", "retirement annuity", "pension", "provident", "preservation", "tfsa", "unit trust", "account", "savings", "fund"),
    "liability": ("bond", "loan", "finance", "card", "overdraft"),
    "property": ("house", "flat", "property", "car", "vehicle"),
}


def link_crm(reg: Registry, crm: CrmRecord | None, transcript: Transcript, embedder: Embedder | None = None) -> Registry:
    """Top-3 CRM candidates for discovered entities, scored on four signals (§6.1)."""
    if crm is None or not crm.products:
        return reg
    emb = embedder or get_embedder()
    seg_text = {s.segment_id: s.text for s in transcript.segments}
    descs = [" ".join(x for x in (p.provider, p.product, p.fna_type, p.kind) if x) for p in crm.products]
    doc_vecs = emb.embed_documents(descs)
    for eid, e in list(reg.entities.items()):
        if e.crm_match or e.type == "person":
            continue
        context = " ".join(seg_text.get(m.segment_id, "") for m in reg.mentions if m.entity_id == eid)[:2000] or e.canonical_name
        sims = cosine(emb.embed_queries([context]), doc_vecs)[0]
        scored: list[tuple[str, float]] = []
        for i, p in enumerate(crm.products):
            name_score = fuzz.token_set_ratio(" ".join([e.canonical_name, *e.aliases]), f"{p.provider} {p.product}") / 100.0
            type_words = _PRODUCT_WORDS.get(e.type, ())
            type_score = 1.0 if (p.fna_type and p.fna_type in str(e.attributes.values())) or any(w in descs[i].lower() for w in type_words) else 0.0
            amount_score = 0.0
            try:
                amt = float(e.attributes.get("amount", "nan"))
                if p.last_value and amt == amt:
                    amount_score = max(0.0, 1.0 - abs(amt - p.last_value) / max(p.last_value, 1.0))
            except (TypeError, ValueError):
                pass
            score = 0.45 * name_score + 0.3 * float(max(0.0, sims[i])) + 0.15 * type_score + 0.1 * amount_score
            scored.append((p.crm_id, round(score, 4)))
        scored.sort(key=lambda t: -t[1])
        reg.entities[eid] = e.model_copy(update={"crm_match": scored[:CRM_TOP_K]})
    return reg
