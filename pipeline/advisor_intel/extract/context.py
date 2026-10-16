"""What an extraction sub-agent sees for one task (spec §7.2): the context pack and the window.

``ExtractionDeps`` assembles, per task and capped at about 8,000 tokens by embedding retrieval:
the CRM record, the prior FnaDocument fields in the task's groups, the product catalogue rules,
the practice glossary and the registry excerpt; then the already-known facts the task depends on;
then the transcript window, each segment as ``[s0123 | client_primary | 14:02] text ⟨rewrite: …⟩``
with low-ASR words marked ``{word?0.42}``. Prompts never include the extractor's own earlier
reasoning about other groups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..binding import describe_value, row_id_for
from ..embeddings import cosine, get_embedder, nearest
from ..models.crm import CrmRecord
from ..models.entities import DiscourseOutput, Entity
from ..models.extraction import ExtractionTask
from ..models.profile import Profile
from ..models.runs import VersionPins
from ..models.transcript import Segment, Transcript
from ..schema.slots import Catalog, SlotDef, fact_id_for, generic_slot_id
from ..discourse.text import render_segment

CONTEXT_CHARS = 32_000  # ≈ 8,000 tokens (§7.2)
LIST_FNA: dict[str, str] = {
    "dependant": "dependants",
    "fund": "retirementFunds",
    "asset": "assets",
    "liability": "liabilities",
    "policy": "policies",
    "goal": "goals",
    "expense": "expenses.custom",
    "event": "event",
    "income_change": "expectedChange",
}

PRODUCT_RULES = """\
- Retirement funds: pension, provident and preservation funds, retirement annuities (RAs), living annuities and life annuities. A retirement annuity is a retirement fund, not an asset.
- Tax-free savings accounts (TFSA) are assets of type tfsa; endowments are assets of type endowment; unit trusts and ETFs held directly are assets.
- Policies that pay on disability: lump-sum disability (disability-lump), income protection (monthly benefit) and severe illness; life cover pays on death; funeral cover pays a small lump sum on death.
- Group cover through an employer is a policy with isGroup true and usually ends when the person leaves.
- A bond is a home loan liability; vehicle finance is a liability linked to the vehicle asset.
- The two-pot savings component is part of a retirement fund's value, not a separate fund.
- Amounts are in rand unless another currency is said; 'a bar' is R1,000; 'K' is thousand; 'mil' is million."""


def field_key(slot: SlotDef, instance: str | None) -> str:
    """The key a field goes by in prompts and stage outputs: the fact id (``slot_id#instance``)."""
    return fact_id_for(slot.slot_id, instance)


def concrete_path(profile: Profile, slot: SlotDef, instance: str | None, entities: dict[str, Entity] | None = None) -> str:
    """The FnaDocument path of a slot instance (stable row ids for rows created during the call)."""
    ents = entities if entities is not None else profile.entities
    if slot.slot_id == "expenses.items[]":
        return f"expenses.items[{instance}]"
    if slot.per_person and not slot.per_entity:
        return slot.fna_path_for(instance)
    if slot.per_entity:
        person = None
        if slot.per_person and instance in ents:
            person = ents[instance].attributes.get("person_key") or "client"
        rid = ents[instance].fna_id if instance in ents and ents[instance].fna_id else row_id_for(profile, instance or "")
        return slot.fna_path_for(person, rid)
    return slot.fna_path


@dataclass
class ExtractionDeps:
    task: ExtractionTask
    catalog: Catalog
    profile: Profile
    transcript: Transcript
    discourse: DiscourseOutput
    pins: VersionPins
    crm: CrmRecord | None = None
    glossary: list[str] = field(default_factory=list)  # confirmed practice terms, one line each (§8.5)
    entities: dict[str, Entity] = field(default_factory=dict)  # the working registry (discourse + new objects)

    def __post_init__(self) -> None:
        if not self.entities:
            self.entities = {**self.profile.entities, **self.discourse.entities}

    # ----------------------------------------------------------------- slots and instances

    @property
    def group(self) -> str:
        return self.task.slot_group

    @property
    def person(self) -> str | None:
        return self.task.instance_keys[0] if self.task.instance_keys else None

    @property
    def slots(self) -> list[SlotDef]:
        seen: dict[str, SlotDef] = {}
        for sid in self.task.slot_ids:
            s = self.catalog.slot(sid)
            seen.setdefault(s.slot_id, s)
        return list(seen.values())

    @property
    def list_entity(self) -> str | None:
        return self.catalog.groups[self.group].list_entity

    def window(self) -> list[Segment]:
        idx = self.transcript.index()
        return [idx[s] for s in self.task.segment_ids if s in idx]

    def window_ids(self) -> set[str]:
        return set(self.task.segment_ids)

    def instances_for(self, slot: SlotDef, objects: list[str]) -> list[str | None]:
        if slot.per_entity:
            if slot.slot_id == "expenses.items[]":
                return [o for o in objects if not o.startswith(("account.", "new:"))]
            if slot.slot_id.startswith("expenses.custom[]"):
                return [o for o in objects if o.startswith("account.")]
            return list(objects)
        if slot.per_person:
            return [self.person]
        return [None]

    # ----------------------------------------------------------------- prompt parts

    def render_window(self) -> str:
        return "\n".join(render_segment(s, rewrite=self.discourse.rewrites.get(s.segment_id)) for s in self.window()) or "(no segments)"

    def schema_excerpt(self, slots: list[SlotDef] | None = None) -> str:
        out = []
        for s in slots or self.slots:
            line = [f"### {s.slot_id} — {s.label}", f"Question: {s.question}", f"Policy: {s.policy}; unit: {s.fna_unit}; allowed forms: {', '.join(s.allowed_forms)}"]
            if s.categories:
                line.append(f"Categories: {', '.join(s.categories)}")
            if s.bounds:
                line.append(f"Plausible range: {s.bounds[0]:g} to {s.bounds[1]:g}")
            for h in s.extraction_hints:
                line.append(f"Hint: {h}")
            out.append("\n".join(line))
        if self.group == "expenses" and self.catalog.expense_items:
            out.append("### Expense catalog (key — label [category, essential])\n" + "\n".join(
                f"- {e.key} — {e.label} [{e.category}{', essential' if e.essential else ''}]{' — ' + e.hint if e.hint else ''}" for e in self.catalog.expense_items))
            hints = self.catalog_hints()
            if hints:
                out.append("### Nearest catalog lines per segment\n" + "\n".join(f"- {sid}: {', '.join(keys)}" for sid, keys in hints.items()))
        return "\n\n".join(out)

    def catalog_hints(self) -> dict[str, list[str]]:
        """Three nearest expense-catalog keys per money-mentioning segment (§8.3 embedding hints)."""
        keys = [f"{e.key}: {e.label}" for e in self.catalog.expense_items]
        emb = get_embedder(self.pins.embedding_model)
        out: dict[str, list[str]] = {}
        for seg in self.window():
            if not any(ch.isdigit() for ch in seg.text) and not any(w in seg.text.lower() for w in ("thousand", "hundred", "bar", "grand", "rand")):
                continue
            out[seg.segment_id] = [k.split(":", 1)[0] for k, _ in nearest(seg.text, keys, emb, k=3)]
        return out

    def known_facts(self) -> str:
        """Facts the task depends on (value + p), and existing objects of the group's kind."""
        lines = []
        wanted: set[str] = set()
        for s in self.slots:
            for dep in s.depends_on:
                wanted.add(dep.replace("{p}", self.person) if self.person else dep)
        for f in self.profile.facts.values():
            concrete = f.slot_id.replace("{p}", f.instance_key) if "{p}" in f.slot_id and f.instance_key else f.slot_id
            if concrete in wanted or generic_slot_id(concrete) in wanted:
                lines.append(f"- {f.fact_id}: {describe_value(f.value)} (status {f.status}, p={f.p_correct:.2f}, source {f.source})")
        if self.list_entity:
            fna_list = LIST_FNA.get(self.list_entity)
            for e in self.entities.values():
                if e.fna_list == fna_list or (e.attributes.get("list_entity") == self.list_entity):
                    lines.append(f"- existing {self.list_entity}: {e.entity_id} — {e.canonical_name}" + (f" (aka {', '.join(e.aliases[:3])})" if e.aliases else ""))
        return "\n".join(lines) or "(none)"

    def context_pack(self) -> str:
        blocks: list[tuple[str, str]] = []
        if self.crm:
            hh = "\n".join(f"- {p.role}: {p.name}" + (f", born {p.date_of_birth}" if p.date_of_birth else "") for p in self.crm.household)
            prods = "\n".join(
                f"- {p.kind}: {p.provider} {p.product}".rstrip() + (f" #{p.number}" if p.number else "") + (f", last value R{p.last_value:,.0f}" if p.last_value else "") + (f" ({p.fna_type})" if p.fna_type else "")
                for p in self.crm.products
            )
            blocks.append(("CRM record", "\n".join(x for x in (hh, prods, f"Adviser notes: {self.crm.adviser_notes}" if self.crm.adviser_notes else "") if x)))
        prior_lines = []
        groups_slots = {s.slot_id for s in self.slots}
        for f in self.profile.facts.values():
            if f.source == "prior_fna" and f.slot_id in groups_slots and (not self.person or f.instance_key in (self.person, None) or f.instance_key in self.entities):
                prior_lines.append(f"- {f.fact_id}: {describe_value(f.prior_value or f.value)}")
        if prior_lines:
            blocks.append(("Client's existing FNA (before this call)", "\n".join(prior_lines[:120])))
        blocks.append(("Product catalogue rules", PRODUCT_RULES))
        if self.glossary:
            blocks.append(("Practice glossary (confirmed terms)", "\n".join(self.glossary)))
        reg = self.registry_excerpt()
        if reg:
            blocks.append(("Entity registry", reg))
        return self._cap(blocks)

    def registry_excerpt(self) -> str:
        in_window = {m.entity_id for m in self.discourse.mentions if m.segment_id in self.window_ids() and m.entity_id}
        fna_list = LIST_FNA.get(self.list_entity or "")
        lines = []
        for e in self.entities.values():
            if e.entity_id in in_window or (fna_list and e.fna_list == fna_list) or e.entity_id in ("person.client_primary", "person.spouse"):
                aliases = f" aka {', '.join(e.aliases[:4])}" if e.aliases else ""
                attrs = ", ".join(f"{k}={v}" for k, v in e.attributes.items() if v not in ("", None))
                lines.append(f"- {e.entity_id} ({e.type}): {e.canonical_name}{aliases}{' [' + attrs + ']' if attrs else ''}")
        return "\n".join(lines)

    def _cap(self, blocks: list[tuple[str, str]]) -> str:
        """Keep the pack within budget, dropping the least window-relevant lines of the largest blocks."""
        text = "\n\n".join(f"## {t}\n{b}" for t, b in blocks)
        if len(text) <= CONTEXT_CHARS:
            return text
        emb = get_embedder(self.pins.embedding_model)
        query = emb.embed_queries([" ".join(s.text for s in self.window())[:4000]])
        out_blocks = []
        budget = CONTEXT_CHARS
        for title, body in blocks:
            lines = body.splitlines()
            if len(body) > budget // max(1, len(blocks)) and len(lines) > 5:
                sims = cosine(query, emb.embed_documents(lines))[0]
                order = np.argsort(-sims)
                kept, size = [], 0
                for i in order:
                    if size + len(lines[i]) > budget // max(1, len(blocks)):
                        break
                    kept.append(i)
                    size += len(lines[i]) + 1
                body = "\n".join(lines[i] for i in sorted(kept))
            out_blocks.append(f"## {title}\n{body}")
        return "\n\n".join(out_blocks)[:CONTEXT_CHARS]

    def prompt(self, stage: str, extra: str = "") -> str:
        """The user prompt for a stage: schema excerpt, context pack, known facts, window (§7.2 parts 2–5)."""
        return (
            f"# Task {self.task.task_id} — {self.group}"
            + (f" for {self.person}" if self.person else "")
            + f"\n\n# Fields\n{self.schema_excerpt()}"
            + f"\n\n# Context\n{self.context_pack()}"
            + f"\n\n# Already known\n{self.known_facts()}"
            + f"\n\n# Transcript window\n{self.render_window()}"
            + (f"\n\n# {stage}\n{extra}" if extra else "")
        )

    def usage_role(self) -> dict[str, Any]:
        return {"task": self.task.task_id, "group": self.group}
