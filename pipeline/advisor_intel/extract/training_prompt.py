"""The production stage prompts as training data (spec §15.8), and the RL rollout post-processing (§15.9).

``render_stage_prompt`` renders exactly what the extraction sub-agent sees for one task and stage (the same
``ExtractionDeps`` prompt parts, instruction blocks and pinned prompt versions as ``extract/subagent.py``), so a
fine-tuned extractor is trained on the prompts it will be served. ``tasks_from_profile`` rebuilds a run's extraction
tasks from its reviewed profile and cached discourse output (the agent's plan is not kept after the run: a task per
slot group that produced facts, with the window the topic index and the facts' evidence give). ``rl_postprocess``
turns a sampled combined-stage output into the ``RolloutFact``s the §15.9 reward scores, through the schema
compiler's conversion and the lexicon normaliser (§8.3).
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..discourse.run import with_rewrites
from ..lexicon.loader import load_lexicon
from ..models.entities import DiscourseOutput
from ..models.extraction import ExtractionTask
from ..models.profile import Profile
from ..models.runs import VersionPins
from ..prompts import pinned
from ..schema import compiler as C
from ..schema.slots import Catalog, load_catalog
from .context import ExtractionDeps, field_key
from .subagent import FoundObject, _instructions, field_keys

if TYPE_CHECKING:
    from ..evals.finetune import ReviewedCall, RLPrompt
    from ..evals.rl_reward import RolloutFact

MAX_WINDOW_SEGMENTS = 40  # the retrieval tool's cap (§13.3)
COMBINED = "combined"  # the RL stage: status and value in one output (the small-group path of §7.2)


def tasks_from_profile(
    profile: Profile, discourse: DiscourseOutput | None, *, catalog: Catalog | None = None, groups: list[str] | None = None
) -> list[ExtractionTask]:
    """One task per slot group with call-extracted facts: its generic slots, instance keys and an evidence window."""
    catalog = catalog or load_catalog(profile.schema_version)
    slots: dict[str, set[str]] = defaultdict(set)
    instances: dict[str, set[str | None]] = defaultdict(set)
    segments: dict[str, set[str]] = defaultdict(set)
    for f in profile.facts.values():
        if f.source not in ("call", "followup"):
            continue
        slot = catalog.slot(f.slot_id)
        if groups and slot.group not in groups:
            continue
        slots[slot.group].add(slot.slot_id)
        instances[slot.group].add(f.instance_key)
        segments[slot.group].update(e.segment_id for e in f.evidence)
    tasks = []
    for group in sorted(slots):
        topic = (discourse.topic_index.get(group, []) if discourse else [])[:MAX_WINDOW_SEGMENTS]
        window = set(segments[group]) | set(topic)
        tasks.append(
            ExtractionTask(
                task_id=f"t-{group}",
                slot_group=group,
                slot_ids=sorted(slots[group]),
                instance_keys=sorted(instances[group], key=lambda x: x or ""),
                segment_ids=sorted(window)[:MAX_WINDOW_SEGMENTS],  # segment ids are zero-padded, so sorted is call order
                status="done",
            )
        )
    return tasks


def _deps(call: ReviewedCall, task: ExtractionTask) -> ExtractionDeps:
    if call.transcript is None:
        raise ValueError(f"call {call.call_id}: the transcript is needed to render its prompts")
    discourse = call.discourse or DiscourseOutput(entities={})
    return ExtractionDeps(
        task=task,
        catalog=load_catalog(call.profile.schema_version),
        profile=call.profile,
        transcript=with_rewrites(call.transcript, discourse),
        discourse=discourse,
        pins=VersionPins(schema_version=call.profile.schema_version, prompt_versions=dict(call.prompt_versions)),
    )


def _objects(deps: ExtractionDeps) -> list[FoundObject]:
    """The task's rows as the objects stage would have returned them (per-entity instance keys are entity ids)."""
    if not deps.list_entity:
        return []
    return [FoundObject(entity_id=k, catalog_key=None, segment_ids=list(deps.task.segment_ids)) for k in deps.task.instance_keys if k]


def task_keys(call: ReviewedCall, task: ExtractionTask) -> dict[str, tuple[Any, str | None]]:
    """The field keys (``slot#instance``) of a task, as the stages list them."""
    deps = _deps(call, task)
    return field_keys(deps, _objects(deps))


def render_stage_prompt(call: ReviewedCall, task: ExtractionTask, stage: str) -> list[dict[str, str]]:
    """The exact production prompt of one stage for one task: ``[system (instructions), user (stage prompt)]``."""
    from ..evals.finetune import stage_targets

    deps = _deps(call, task)
    keys = field_keys(deps, _objects(deps))
    listing = "\n".join(f"- {k}" for k in keys)
    if stage == "objects":
        instructions, _ = _instructions(deps, "extract_objects")
        user = deps.prompt("Objects", "List every object of this kind mentioned in the window.")
    elif stage == "statuses":
        instructions, _ = _instructions(deps, "extract_fields")
        user = deps.prompt("Fields to decide", listing)
    elif stage == "values":
        instructions, _ = _instructions(deps, "extract_values")
        # The values stage lists the fields the statuses stage found, with their segments: here the reviewed statuses.
        try:
            statuses = (stage_targets(call, task, catalog=deps.catalog) or {}).get("statuses", [])
        except Exception:  # noqa: BLE001 - an unreviewed or disputed task (the export skips it) still renders its prompt
            statuses = []
        by_path = {s["fna_path"]: s for s in statuses}
        found = []
        for k, (slot, inst) in keys.items():
            st = by_path.get(slot.fna_path_for(inst if slot.per_person else None)) if not slot.per_entity else None
            if st and st["status"] in ("stated", "implied", "contradicted"):
                found.append(f"- {k}: {st['status']}; segments {', '.join(st['segment_ids']) or '-'}")
        user = deps.prompt("Fields with values", "\n".join(found) or listing)
    elif stage == COMBINED:
        instructions, _ = _instructions(deps, "extract_values")
        instructions = pinned("extract_fields", deps.pins.prompt_versions).text + "\n\n" + instructions
        user = deps.prompt("Fields (status and value)", listing)
    else:
        raise ValueError(f"unknown extraction stage {stage!r}")
    return [{"role": "system", "content": instructions}, {"role": "user", "content": user}]


# --------------------------------------------------------------------------- RL rollouts (§15.9)


def rl_postprocess(text: str, prompt: RLPrompt, *, catalog: Catalog | None = None, lexicon_version: str | None = None) -> list[RolloutFact]:
    """A sampled combined-stage output → ``RolloutFact``s: the strict schema's conversion, then the lexicon normaliser
    for each value (the spoken form in the slot's FnaDocument unit, §8.3). Output the schema rejects scores as empty."""
    from ..evals.rl_reward import RolloutFact

    catalog = catalog or load_catalog()
    keys = [tuple(k) for k in (prompt.slots or [])]
    if not keys:
        return []
    slots = {k[0]: catalog.slot(k[0]) for k in keys}
    instances: dict[str, tuple[str, ...]] = {}
    for sid, inst in keys:
        instances[sid] = (*instances.get(sid, ()), inst or C.NO_INSTANCE)
    model = C.compile_combined(prompt.group, list(slots.values()), instances)
    try:
        out = model.model_validate(json.loads(text))
    except (ValueError, ValidationError):
        return []
    statuses, values = C.to_combined(out, list(slots.values()), lambda s, i: field_key(s, None if i == C.NO_INSTANCE else i))
    lex = load_lexicon(lexicon_version) if lexicon_version else load_lexicon()
    by_key = {v.fna_path: v for v in values}
    facts: list[RolloutFact] = []
    for st in statuses:
        slot_id, _, inst = st.fna_path.partition("#")
        slot = slots.get(slot_id)
        if slot is None:
            continue
        fv = by_key.get(st.fna_path)
        value, range_like, attribution, self_score, interpretation = None, False, "client", None, ""
        quotes: list[tuple[str, str]] = []
        if fv is not None and fv.hypotheses:
            form = fv.hypotheses[0].intermediate
            range_like = getattr(form, "type", "") == "AMOUNT_RANGE" or (
                getattr(form, "type", "") == "AGE" and getattr(form, "form", "") == "range"
            )
            try:
                value = normalize_form(form, slot, lex, person=inst or None)
            except (ValueError, TypeError, KeyError):
                value = None
            attribution, self_score, interpretation = fv.speaker_attribution, fv.self_score, fv.interpretation
            quotes = [(seg, fv.said) for seg in st.segment_ids if fv.said]
        facts.append(
            RolloutFact(
                slot_id=slot_id,
                instance_key=inst or None,
                status=st.status,
                value=value,
                quotes=quotes,
                attribution=attribution,
                self_score=self_score,
                range_like=range_like,
                interpretation=interpretation,
            )
        )
    return facts


def normalize_form(form: Any, slot: Any, lex: Any, *, person: str | None = None) -> Any:
    """The lexicon normaliser on one intermediate form without a profile (no prior to fall back on)."""
    from ..lexicon.normalize import normalize_value

    return normalize_value(form, slot, None, lex, person=person).quantity
