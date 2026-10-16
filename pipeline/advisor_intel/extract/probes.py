"""Single-field re-extraction for verification (spec §9.1b, §9.1d).

``make_reextract`` builds the consistency-probe callable: the same slot re-extracted with a
paraphrased question and a reordered window on the small model, normalised like the main extraction,
returning the probe's status and value. Probes vary the prompt wording, never the sampling (§13.7).
``make_reopen`` builds the omission check's callable: one targeted value extraction over the
segments the coverage check pointed at, aligned and turned into ops (not applied: the caller runs
them through the reducer).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..lexicon.loader import load_lexicon
from ..models.entities import DiscourseOutput
from ..models.extraction import ExtractionTask, FieldStatus
from ..models.facts import Fact, Provenance
from ..models.profile import Profile, StateOp
from ..models.runs import VersionPins
from ..models.transcript import Transcript
from ..schema.slots import Catalog, SlotDef, person_of
from .align import align_values
from .context import ExtractionDeps, concrete_path, field_key
from .ops import draft_fact, normalise_value, to_state_ops
from .planner import _with_context
from .reducer import make_ops
from .subagent import GroupExtraction, stage_values

PARAPHRASES = (
    "{question}",
    "What did the client say about {label} for {who}? Use only their words in the segments below.",
    "From the conversation below, find how the speakers describe {label} ({who}), including ranges, hedges and corrections.",
    "Read the segments and report {label} for {who}, exactly as discussed; if it is not discussed, say so.",
)


@dataclass
class ProbeContext:
    catalog: Catalog
    transcript: Transcript
    discourse: DiscourseOutput
    pins: VersionPins
    run_id: str

    def task_for(self, slot: SlotDef, instance: str | None, segment_ids: list[str], tag: str) -> ExtractionTask:
        person = person_of(slot.expand(instance)) if slot.per_person and instance in ("client", "spouse") else None
        return ExtractionTask(task_id=f"{tag}:{slot.slot_id}#{instance or '-'}", slot_group=slot.group, slot_ids=[slot.expand(person) if person else slot.slot_id],
                              instance_keys=[person or instance], segment_ids=segment_ids)


def _order(ids: list[str], variant: int) -> list[str]:
    if variant % 3 == 1:
        return list(reversed(ids))
    if variant % 3 == 2:
        return ids[1::2] + ids[0::2]
    return list(ids)


def _who(instance: str | None, profile: Profile) -> str:
    if instance in ("client", "spouse"):
        return f"the {instance}"
    if instance and instance in profile.entities:
        return profile.entities[instance].canonical_name
    return instance or "the household"


async def _one_field(ctx: ProbeContext, profile: Profile, slot: SlotDef, instance: str | None, segment_ids: list[str], *, role: str, variant: int,
                     status_hint: str = "stated") -> tuple[GroupExtraction, ExtractionDeps, str]:
    window = _with_context([s.segment_id for s in ctx.transcript.segments], set(segment_ids))
    task = ctx.task_for(slot, instance, window, f"probe{variant}" if role == "small" else "reopen")
    phr = PARAPHRASES[variant % len(PARAPHRASES)].format(question=slot.question, label=slot.label.lower(), who=_who(instance, profile))
    probe_slot = slot.model_copy(update={"question": phr})
    deps = ExtractionDeps(task=task, catalog=ctx.catalog, profile=profile, transcript=ctx.transcript, discourse=ctx.discourse, pins=ctx.pins,
                          entities={**ctx.discourse.entities, **profile.entities}, role=role, segment_order=_order(window, variant))
    key = field_key(slot, instance)
    keys = {key: (probe_slot, instance)}
    out = GroupExtraction()
    statuses = {key: FieldStatus(fna_path=key, status=status_hint, segment_ids=[s for s in segment_ids if s in set(window)])}  # type: ignore[arg-type]
    out.statuses = statuses
    out.values = await stage_values(deps, keys, statuses, out, variant=variant)
    return out, deps, key


Reextract = Callable[[Fact, int], Awaitable[Any]]
Reopen = Callable[[str, "str | None", list[str]], Awaitable[list[StateOp]]]


def make_reextract(ctx: ProbeContext, get_profile: Callable[[], Profile]) -> Reextract:
    """The consistency-probe callable for ``verify.consistency`` (§9.1b)."""
    from ..verify.consistency import ProbeResult

    lex = load_lexicon(ctx.pins.lexicon_version)

    async def reextract(fact: Fact, variant: int) -> ProbeResult:
        profile = get_profile()
        slot = ctx.catalog.slot(fact.slot_id)
        segs = list(dict.fromkeys(e.segment_id for e in fact.evidence))
        if not segs:
            return ProbeResult(status="not_discussed")
        out, deps, key = await _one_field(ctx, profile, slot, fact.instance_key, segs, role="small", variant=variant + 1)
        fv = out.values.get(key)
        if fv is None or key in out.failures:
            return ProbeResult(status="not_discussed")
        hyps, _, _, _ = normalise_value(fv.model_copy(update={"fna_path": key}), slot, key, profile, lex, ctx.transcript, segs)
        from .reducer import merged_value

        return ProbeResult(status="stated", value=merged_value(hyps))

    return reextract


def make_reopen(ctx: ProbeContext, get_profile: Callable[[], Profile]) -> Reopen:
    """The omission check's callable for ``verify.coverage`` (§9.1d): a targeted value extraction, as ops."""
    lex = load_lexicon(ctx.pins.lexicon_version)

    async def reopen(slot_id: str, instance: str | None, segment_ids: list[str]) -> list[StateOp]:
        profile = get_profile()
        slot = ctx.catalog.slot(slot_id)
        out, deps, key = await _one_field(ctx, profile, slot, instance, segment_ids, role="reasoning", variant=0)
        if key not in out.values:
            return []
        al = await align_values(deps, out.values, {key: segment_ids}, {key: slot})
        if key not in al.aligned:
            return []
        draft = draft_fact(al.aligned[key], out.statuses[key], slot, key, instance, profile, lex, ctx.transcript, retries=out.retries.get(key, 0))
        prov = Provenance(run_id=ctx.run_id, extractor_model=ctx.pins.reasoning_model, lexicon_version=lex.version, schema_version=ctx.catalog.version)
        specs, _ = to_state_ops([draft], [], profile, prov, {})
        for spec in specs:
            if spec[0] == "ADD":
                spec[3]["reopened_by_coverage"] = True
                spec[3]["fna_path"] = concrete_path(profile, slot, instance, deps.entities)
        return make_ops(profile, specs)

    return reopen
