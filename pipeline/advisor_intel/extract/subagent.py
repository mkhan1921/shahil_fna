"""``extract_slot_group``: three validated sub-agent stages per task (spec §7.2).

1. **Objects** (list groups): every object of the group's kind in the window — a registry id or a
   new one — validated against the registry, duplicates merged, unknown ids rejected.
2. **Fields**: each field's status and the segments that discuss it — every tier A field must have
   a status, and stated or implied fields need segment ids in the window.
3. **Values**: for each field with a value, the intermediate form as spoken, hedges, attribution,
   the speaker's words and the model's reading — validated per field.

Each stage is a small typed call on the reasoning model with thinking on, sent as a strict schema
compiled for exactly the task's fields (§4.4). Only what fails validation is retried: the retry
call's schema holds just the failing fields, so a retry cannot break fields that passed. After two
failed retries a field becomes ``contradicted`` with the failure reason, for review. Single-instance
groups with fewer than six slots merge stages 2 and 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.usage import UsageLimits

from .. import llm
from ..discourse.registry import new_entity_id
from ..models.entities import Entity
from ..models.extraction import AgeForm, AmountBound, AmountRange, DateAbs, DurationFromAnchor, FieldStatus, FieldValue, RateForm
from ..prompts import pinned
from ..schema import compiler as C
from ..schema.slots import SlotDef
from .context import LIST_FNA, ExtractionDeps, concrete_path, field_key

MAX_RETRIES = 2
SUBAGENT_LIMITS = UsageLimits(request_limit=4, total_tokens_limit=400_000)

objects_agent: Agent[None, Any] = Agent(None, output_type=str, name="extract_objects", retries=2)
fields_agent: Agent[None, Any] = Agent(None, output_type=str, name="extract_fields", retries=2)
values_agent: Agent[None, Any] = Agent(None, output_type=str, name="extract_values", retries=2)


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, run_usage: Any, model_name: str) -> None:
        self.requests += getattr(run_usage, "requests", 0) or 0
        inp = getattr(run_usage, "input_tokens", 0) or 0
        out = getattr(run_usage, "output_tokens", 0) or 0
        cache = getattr(run_usage, "cache_read_tokens", 0) or 0
        self.input_tokens += inp
        self.output_tokens += out
        self.cache_read_tokens += cache
        reported = getattr(run_usage, "cost", None)
        self.cost_usd += llm.usage_cost_usd(model_name, input_tokens=inp, output_tokens=out, cache_read_tokens=cache, reported=float(reported) if reported else None)

    def merge(self, other: "Usage") -> None:
        for k in ("requests", "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd"):
            setattr(self, k, getattr(self, k) + getattr(other, k))

    def as_dict(self) -> dict[str, float]:
        return {k: getattr(self, k) for k in ("requests", "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd")}


@dataclass
class FoundObject:
    entity_id: str
    catalog_key: str | None
    segment_ids: list[str]
    is_new: bool = False
    name: str = ""


@dataclass
class GroupExtraction:
    """Everything the three stages produced for one task, before alignment and op generation."""

    objects: list[FoundObject] = field(default_factory=list)
    statuses: dict[str, FieldStatus] = field(default_factory=dict)  # field key → status
    values: dict[str, FieldValue] = field(default_factory=dict)  # field key → value (fna_path is concrete)
    retries: dict[str, int] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)  # field key → reason after the last retry
    new_entities: dict[str, Entity] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    warnings: list[str] = field(default_factory=list)
    prompt_versions: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- helpers


def _instructions(deps: ExtractionDeps, stage: str) -> tuple[str, str]:
    stage_prompt = pinned(stage, deps.pins.prompt_versions)
    block = pinned(f"extract_slot_group/{deps.group}", deps.pins.prompt_versions)
    return f"{stage_prompt.text}\n\n## Slot group: {deps.group}\n{block.text}", f"{stage}:{stage_prompt.version},{deps.group}:{block.version}"


async def _run(agent: Agent, deps: ExtractionDeps, model_type: type[BaseModel], prompt: str, instructions: str, usage: Usage, out: "GroupExtraction | None" = None) -> Any:
    """One stage call. Output that still fails schema validation after the agent's own retries, a
    refusal or a truncated response comes back as ``None`` (a retryable failure, §4.4 rule 6)."""
    rm = llm.for_role(deps.role, deps.pins, thinking=deps.role == "reasoning")  # type: ignore[arg-type]
    try:
        result = await agent.run(prompt, model=rm.model, model_settings=rm.settings, instructions=instructions,
                                 output_type=NativeOutput(model_type), usage_limits=SUBAGENT_LIMITS)
    except UnexpectedModelBehavior as e:
        usage.requests += 1
        if out is not None:
            out.warnings.append(f"{agent.name}: {str(e)[:200]}")
        return None
    usage.add(result.usage, rm.name)
    return result.output


def _slot_by_key(deps: ExtractionDeps) -> dict[str, SlotDef]:
    return {s.slot_id: s for s in deps.slots}


# --------------------------------------------------------------------------- stage 1: objects


def validate_objects(found: list[FoundObject], deps: ExtractionDeps, types: dict[str, str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    fna_list = LIST_FNA.get(deps.list_entity or "")
    for o in found:
        if o.entity_id.startswith("new:") or o.catalog_key and o.catalog_key != "custom":
            continue
        e = deps.entities.get(o.entity_id)
        if e is None:
            errors[o.entity_id] = f"'{o.entity_id}' is not in the registry: use an existing id or 'new:<short name>'"
        elif e.fna_list not in (None, fna_list) and e.attributes.get("list_entity") != deps.list_entity:
            errors[o.entity_id] = f"'{o.entity_id}' is a {e.fna_list} row, not a {fna_list} row"
    return errors


async def stage_objects(deps: ExtractionDeps, out: GroupExtraction) -> list[FoundObject]:
    window = deps.window_ids()
    registry_ids = tuple(
        e.entity_id for e in deps.entities.values() if e.fna_list == LIST_FNA.get(deps.list_entity or "") or e.attributes.get("list_entity") == deps.list_entity
    )
    model = C.compile_objects(deps.group, deps.catalog, registry_ids=registry_ids)
    instructions, version = _instructions(deps, "extract_objects")
    out.prompt_versions["objects"] = version
    raw = await _run(objects_agent, deps, model, deps.prompt("Objects", "List every object of this kind mentioned in the window."), instructions, out.usage, out)
    found = _collect_objects(raw, window) if raw is not None else []
    for attempt in range(MAX_RETRIES):
        errors = validate_objects(found, deps, {})
        if not errors:
            break
        listing = "\n".join(f"- {k}: {v}" for k, v in errors.items())
        raw = await _run(objects_agent, deps, model, deps.prompt("Fix these objects", f"Return corrected entries for only these objects:\n{listing}"), instructions, out.usage, out)
        fixed = _collect_objects(raw, window) if raw is not None else []
        found = [o for o in found if o.entity_id not in errors] + fixed
    errors = validate_objects(found, deps, {})
    if errors:
        out.warnings += [f"object rejected: {v}" for v in errors.values()]
        found = [o for o in found if o.entity_id not in errors]
    return _register_objects(found, deps, out)


def _collect_objects(raw: Any, window: set[str]) -> list[FoundObject]:
    merged: dict[tuple[str, str | None], FoundObject] = {}
    for o in raw.objects:
        key_ = getattr(o, "catalog_key", None)
        eid = o.entity_id.strip() if o.entity_id.strip() else f"new:{o.name}"
        k = (key_ if key_ and key_ != "custom" else eid, key_ if key_ and key_ != "custom" else None)
        segs = [s for s in o.segment_ids if s in window]
        if k in merged:
            merged[k].segment_ids = sorted(set(merged[k].segment_ids) | set(segs))
        else:
            if key_ and key_ != "custom":
                merged[k] = FoundObject(entity_id=key_, catalog_key=key_, segment_ids=segs, name=o.name)
            else:
                merged[k] = FoundObject(entity_id=eid, catalog_key="custom" if key_ == "custom" else None, segment_ids=segs,
                                        is_new=eid.startswith("new:"), name=o.name)
    return list(merged.values())


def _register_objects(found: list[FoundObject], deps: ExtractionDeps, out: GroupExtraction) -> list[FoundObject]:
    """Give new objects registry ids and entities (rows created during the call, §3.6)."""
    fna_list = LIST_FNA.get(deps.list_entity or "")
    etype = C.LIST_ENTITY_TYPE.get(deps.list_entity or "", "account")
    result: list[FoundObject] = []
    for o in found:
        if o.catalog_key and o.catalog_key != "custom":
            result.append(o)
            continue
        if o.entity_id.startswith("new:"):
            name = o.name or o.entity_id[4:] or deps.group
            # Reuse a near-identical new object found by an earlier task (e.g. dependants then education).
            existing = next((e for e in deps.entities.values() if e.fna_list == fna_list and e.canonical_name.lower() == name.lower()), None)
            if existing is None:
                eid = new_entity_id(deps.entities, etype, f"expense {name}" if deps.list_entity == "expense" else name)
                attrs: dict[str, Any] = {"list_entity": deps.list_entity}
                if deps.person:
                    attrs["person_key"] = deps.person
                ent = Entity(entity_id=eid, type=etype, canonical_name=name, aliases=[name], attributes=attrs,
                             first_mention=o.segment_ids[0] if o.segment_ids else None, fna_list=fna_list)
                deps.entities[eid] = ent
                out.new_entities[eid] = ent
                o = FoundObject(entity_id=eid, catalog_key=o.catalog_key, segment_ids=o.segment_ids, is_new=True, name=name)
            else:
                o = FoundObject(entity_id=existing.entity_id, catalog_key=o.catalog_key, segment_ids=o.segment_ids, name=name)
        result.append(o)
    return result


# --------------------------------------------------------------------------- stage 2: field statuses


def field_keys(deps: ExtractionDeps, objects: list[FoundObject]) -> dict[str, tuple[SlotDef, str | None]]:
    keys: dict[str, tuple[SlotDef, str | None]] = {}
    obj_ids = [o.entity_id for o in objects]
    for s in deps.slots:
        for inst in deps.instances_for(s, obj_ids) if s.per_entity else ([deps.person] if s.per_person else [None]):
            keys[field_key(s, inst)] = (s, inst)
    return keys


def validate_statuses(statuses: dict[str, FieldStatus], keys: dict[str, tuple[SlotDef, str | None]], window: set[str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for k, (slot, _) in keys.items():
        st = statuses.get(k)
        if st is None:
            if slot.sensitivity_tier == "A":
                errors[k] = "tier A field has no status: decide stated, implied, explicitly_unknown, not_discussed or contradicted"
            continue
        if st.status in ("stated", "implied") and not [s for s in st.segment_ids if s in window]:
            errors[k] = "a stated or implied field needs the ids of the window segments that discuss it"
    return errors


async def stage_statuses(deps: ExtractionDeps, keys: dict[str, tuple[SlotDef, str | None]], out: GroupExtraction) -> dict[str, FieldStatus]:
    window = deps.window_ids()
    if not window:
        return {k: FieldStatus(fna_path=k, status="not_discussed", segment_ids=[]) for k in keys}
    instructions, version = _instructions(deps, "extract_fields")
    out.prompt_versions["fields"] = version
    listing = "\n".join(f"- {k}" for k in keys)
    model = C.compile_statuses(deps.group, list(keys))
    raw = await _run(fields_agent, deps, model, deps.prompt("Fields to decide", listing), instructions, out.usage, out)
    statuses = {f.fna_path: f for f in C.to_field_statuses(raw)} if raw is not None else {}
    for _ in range(MAX_RETRIES):
        errors = validate_statuses(statuses, keys, window)
        if not errors:
            break
        fix_keys = list(errors)
        retry_model = C.compile_statuses(deps.group, fix_keys)
        msg = "\n".join(f"- {k}: {v}" for k, v in errors.items())
        raw = await _run(fields_agent, deps, retry_model, deps.prompt("Fix these fields", msg), instructions, out.usage, out)
        for f in C.to_field_statuses(raw) if raw is not None else []:
            statuses[f.fna_path] = f
            out.retries[f.fna_path] = out.retries.get(f.fna_path, 0) + 1
    for k, msg in validate_statuses(statuses, keys, window).items():
        out.failures[k] = msg
        statuses[k] = FieldStatus(fna_path=k, status="contradicted", segment_ids=[s for s in (statuses.get(k).segment_ids if statuses.get(k) else []) if s in window])
    for k in keys:
        statuses.setdefault(k, FieldStatus(fna_path=k, status="not_discussed", segment_ids=[]))
    for k, st in statuses.items():
        statuses[k] = st.model_copy(update={"segment_ids": [s for s in st.segment_ids if s in window]})
    return {k: v for k, v in statuses.items() if k in keys}


# --------------------------------------------------------------------------- stage 3: values


def form_problems(fv: FieldValue, slot: SlotDef) -> list[str]:
    """Shape checks on the intermediate forms; normalisation and bounds are checked by the caller."""
    problems: list[str] = []
    if not fv.hypotheses:
        problems.append("no hypotheses for a stated or implied field")
    for h in fv.hypotheses:
        f = h.intermediate
        if f.type not in slot.allowed_forms:
            problems.append(f"form {f.type} is not allowed for this field (allowed: {', '.join(slot.allowed_forms)})")
        if isinstance(f, AmountRange) and f.low > f.high:
            problems.append("range low is above high")
        if isinstance(f, AgeForm):
            if f.form == "range" and f.low > f.high:
                problems.append("age range low is above high")
            if f.form == "vague" and not f.vague_word.strip():
                problems.append("a vague age needs the words as said")
        if isinstance(f, DateAbs) and not (0 <= f.month <= 12 and 0 <= f.day <= 31 and 1800 <= f.year <= 2200):
            problems.append("date parts out of range (month 0-12, day 0-31, a four-digit year)")
        if isinstance(f, RateForm) and f.form == "value" and abs(f.value) > 1.5:
            problems.append(f"rate {f.value} looks like a percentage: give it as a decimal (0.115 for 11.5%)")
        if isinstance(f, DurationFromAnchor) and f.form == "vague" and not f.vague_word.strip():
            problems.append("a vague duration needs the words as said")
        if isinstance(f, AmountBound) and f.bound < 0:
            problems.append("a bound must be positive")
        if not (0 <= h.weight <= 1):
            problems.append("weights are between 0 and 1 (0 when the client gave none)")
    if not fv.said.strip():
        problems.append("quote the speaker's words in `said`")
    if not (0 <= fv.self_score <= 1):
        problems.append("self_score is between 0 and 1")
    return problems


Validator = Callable[[FieldValue, SlotDef, str | None], list[str]]


async def stage_values(
    deps: ExtractionDeps,
    keys: dict[str, tuple[SlotDef, str | None]],
    statuses: dict[str, FieldStatus],
    out: GroupExtraction,
    *,
    validator: Validator | None = None,
    only: list[str] | None = None,
    variant: int = 0,
) -> dict[str, FieldValue]:
    """Values for the stated and implied fields (or ``only`` these keys), with field-level retries."""
    wanted = [k for k in (only or keys) if k in keys and statuses.get(k) and statuses[k].status in ("stated", "implied", "contradicted")]
    if not wanted:
        return {}
    instructions, version = _instructions(deps, "extract_values")
    out.prompt_versions["values"] = version
    if variant:
        instructions += f"\n\n(Probe {variant}: answer the same questions independently; the wording and order of the window may differ.)"

    def model_for(ks: list[str]) -> tuple[type[BaseModel], list[SlotDef], dict[str, tuple[str, ...]]]:
        slots: dict[str, SlotDef] = {}
        inst: dict[str, list[str]] = {}
        for k in ks:
            slot, i = keys[k]
            slots[slot.slot_id] = slot
            inst.setdefault(slot.slot_id, []).append(i or C.NO_INSTANCE)
        instances = {sid: tuple(dict.fromkeys(v)) for sid, v in inst.items()}
        return C.compile_values(deps.group, list(slots.values()), instances), list(slots.values()), instances

    def resolve(slot: SlotDef, instance: str) -> str:
        return field_key(slot, None if instance == C.NO_INSTANCE else instance)

    def segments_note(ks: list[str]) -> str:
        return "\n".join(f"- {k}: {statuses[k].status}; segments {', '.join(statuses[k].segment_ids) or '-'}" for k in ks)

    model, slots, _ = model_for(wanted)
    raw = await _run(values_agent, deps, model, deps.prompt("Fields with values", segments_note(wanted)), instructions, out.usage, out)
    values: dict[str, FieldValue] = {}
    for fv in C.to_field_values(raw, slots, resolve) if raw is not None else []:
        if fv.fna_path in wanted:
            values[fv.fna_path] = fv

    def check() -> dict[str, str]:
        errs: dict[str, str] = {}
        for k in wanted:
            slot, inst = keys[k]
            fv = values.get(k)
            if fv is None:
                errs[k] = "no value returned for a field whose status says it was discussed"
                continue
            problems = form_problems(fv, slot) + (validator(fv, slot, inst) if validator else [])
            if problems:
                errs[k] = "; ".join(dict.fromkeys(problems))
        return errs

    for _ in range(MAX_RETRIES):
        errors = check()
        if not errors:
            break
        retry_model, rslots, _ = model_for(list(errors))
        msg = "\n".join(f"- {k}: {v}" for k, v in errors.items())
        raw = await _run(values_agent, deps, retry_model, deps.prompt("Fix these fields", msg + "\n\n" + segments_note(list(errors))), instructions, out.usage, out)
        for fv in C.to_field_values(raw, rslots, resolve) if raw is not None else []:
            if fv.fna_path in errors:
                values[fv.fna_path] = fv
                out.retries[fv.fna_path] = out.retries.get(fv.fna_path, 0) + 1
    for k, msg in check().items():
        out.failures[k] = msg
    # Field values carry the concrete FnaDocument path from here on.
    return {k: v.model_copy(update={"fna_path": concrete_path(deps.profile, keys[k][0], keys[k][1], deps.entities)}) for k, v in values.items()}


async def stage_combined(deps: ExtractionDeps, keys: dict[str, tuple[SlotDef, str | None]], out: GroupExtraction, *, validator: Validator | None = None) -> None:
    """Stages 2 and 3 in one call for small single-instance groups (§7.2)."""
    window = deps.window_ids()
    if not window:
        out.statuses = {k: FieldStatus(fna_path=k, status="not_discussed", segment_ids=[]) for k in keys}
        return
    instructions, version = _instructions(deps, "extract_values")
    fields_prompt = pinned("extract_fields", deps.pins.prompt_versions)
    instructions = fields_prompt.text + "\n\n" + instructions
    out.prompt_versions["combined"] = version
    slots = [keys[k][0] for k in keys]
    instances = {keys[k][0].slot_id: (keys[k][1] or C.NO_INSTANCE,) for k in keys}
    model = C.compile_combined(deps.group, slots, instances)
    listing = "\n".join(f"- {k}" for k in keys)
    raw = await _run(values_agent, deps, model, deps.prompt("Fields (status and value)", listing), instructions, out.usage, out)
    statuses, values = C.to_combined(raw, slots, lambda s, i: field_key(s, None if i == C.NO_INSTANCE else i)) if raw is not None else ([], [])
    out.statuses = {s.fna_path: s for s in statuses if s.fna_path in keys}
    for k in keys:
        out.statuses.setdefault(k, FieldStatus(fna_path=k, status="not_discussed", segment_ids=[]))
    for k, st in list(out.statuses.items()):
        out.statuses[k] = st.model_copy(update={"segment_ids": [s for s in st.segment_ids if s in window]})
    vals = {v.fna_path: v for v in values if v.fna_path in keys}
    # Validate like the separate stages; failing fields go through the stage-3 retry path.
    errs = {k: m for k, m in validate_statuses(out.statuses, keys, window).items()}
    bad_values = [k for k, st in out.statuses.items() if st.status in ("stated", "implied") and (k not in vals or form_problems(vals[k], keys[k][0])
                                                                                                  or (validator and validator(vals[k], keys[k][0], keys[k][1])))]
    good = {k: v for k, v in vals.items() if k not in bad_values and k not in errs}
    out.values = {k: v.model_copy(update={"fna_path": concrete_path(deps.profile, keys[k][0], keys[k][1], deps.entities)}) for k, v in good.items()}
    retry = [k for k in bad_values if k not in errs]
    if retry:
        out.values.update(await stage_values(deps, keys, out.statuses, out, validator=validator, only=retry))
    for k, msg in errs.items():
        out.failures[k] = msg
        out.statuses[k] = out.statuses[k].model_copy(update={"status": "contradicted"})


# --------------------------------------------------------------------------- the task


async def run_stages(deps: ExtractionDeps, *, validator: Validator | None = None) -> GroupExtraction:
    """Objects → fields → values for one task (alignment and op generation happen after)."""
    out = GroupExtraction()
    objects: list[FoundObject] = []
    if deps.list_entity:
        if deps.window_ids():
            objects = await stage_objects(deps, out)
        out.objects = objects
    keys = field_keys(deps, objects)
    if not keys:
        return out
    if deps.catalog.groups[deps.group].list_entity is None and len(deps.slots) < 6:
        await stage_combined(deps, keys, out, validator=validator)
    else:
        out.statuses = await stage_statuses(deps, keys, out)
        out.values = await stage_values(deps, keys, out.statuses, out, validator=validator)
    for k in out.failures:
        if k in out.statuses and out.statuses[k].status in ("stated", "implied"):
            out.statuses[k] = out.statuses[k].model_copy(update={"status": "contradicted"})
    return out
