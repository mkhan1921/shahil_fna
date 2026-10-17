"""Compile slot groups to strict structured-output schemas (spec §4.4).

Each extraction stage gets small, slot-specific Pydantic models sent through Pydantic AI
``NativeOutput``, so the provider's constrained decoding guarantees valid JSON:

1. Every field is required; absence is a status or a sentinel, never an optional field.
2. Value-type unions are ``anyOf`` with a literal ``type`` tag. Tags are emitted as one-value
   ``enum``s because Anthropic's strict mode drops ``const``.
3. The schemas of one request stay within the tightest provider limits (Anthropic: 16 union-typed
   parameters, 24 optional parameters, 20 strict tools), checked by ``tests/test_schema_limits.py``.
4. No recursive types: the LLM returns flat intermediate forms (§8.1); mixtures are built by code.
5. Numeric bounds go into descriptions; Pydantic validators enforce them after decoding.

The compiled models are specialised per slot: a categorical slot's ``CATEGORY`` form carries an
enum of the slot's categories, and each slot only admits the intermediate forms it allows.
Compiled outputs convert back to the shared ``ObjectRef`` / ``FieldStatus`` / ``FieldValue`` models.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, create_model

from ..models.entities import EntityType
from ..models.extraction import FORM_TYPES, FieldStatus, FieldValue, ObjectRef, RawHypothesis
from .slots import Catalog, SlotDef

STATUSES: tuple[str, ...] = ("stated", "implied", "explicitly_unknown", "not_discussed", "contradicted")
ATTRIBUTIONS: tuple[str, ...] = ("client", "advisor_suggested", "advisor_confirmed_by_client", "mixed")
NO_INSTANCE = "-"

# Entity type of each list entity in the registry (§3.2).
LIST_ENTITY_TYPE: dict[str, EntityType] = {
    "dependant": "person",
    "fund": "account",
    "asset": "property",
    "liability": "liability",
    "policy": "policy",
    "goal": "goal",
    "expense": "account",
    "event": "goal",
    "income_change": "goal",
}
# Asset rows can be accounts or businesses too; the registry keeps the finer type.
LIST_ENTITY_TYPES: dict[str, tuple[str, ...]] = {
    "asset": ("property", "account", "business"),
    "fund": ("account",),
}


def _enum(*values: str) -> Any:
    """A Literal whose JSON schema is an ``enum`` (never ``const``, which strict mode drops)."""
    lit = Literal[values]  # type: ignore[valid-type]
    return Annotated[lit, WithJsonSchema({"type": "string", "enum": list(values)})]


def _key(slot_id: str) -> str:
    """A JSON property name for a slot: 'dependants[].tertiaryStartAge' → 'dependants_tertiaryStartAge'."""
    return re.sub(r"[^A-Za-z0-9]+", "_", slot_id.replace("{p}", "p")).strip("_")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


_CURRENCY = ("ZAR", "USD", "GBP", "EUR")
_PERIOD = ("once", "monthly", "annual", "none")
_BASIS = ("gross", "net", "unknown", "na")


def _bounds_note(slot: SlotDef) -> str:
    if not slot.bounds:
        return ""
    lo, hi = slot.bounds
    return f" Plausible values lie between {lo:g} and {hi:g} in the slot's unit; outside that the value is dropped."


def _form_model(form_type: str, slot: SlotDef) -> type[BaseModel]:
    """The strict model for one intermediate form, specialised to the slot."""
    name = f"{form_type.title().replace('_', '')}__{_key(slot.slot_id)}"
    tag = (_enum(form_type), ...)
    amount_common: dict[str, Any] = {
        "currency": (_enum(*_CURRENCY), ...),
        "period": (_enum(*_PERIOD), Field(..., description="as said; code converts to the field's unit")),
        "basis": (_enum(*_BASIS), ...),
        "priced_year": (int, Field(..., description="the calendar year the amount is priced in when the speaker dated it ('R400,000 in 2030'); 0 when not said")),
    }
    fields: dict[str, Any]
    if form_type == "AMOUNT_EXACT":
        fields = {"type": tag, "value": (float, Field(..., description="the number as said, scale words applied")), **amount_common}
    elif form_type == "AMOUNT_APPROX":
        fields = {"type": tag, "value": (float, ...), "approx_word": (str, Field(..., description="'about', 'roughly', 'give or take'")), **amount_common}
    elif form_type == "AMOUNT_RANGE":
        fields = {"type": tag, "low": (float, ...), "high": (float, ...), **amount_common}
    elif form_type == "AMOUNT_BOUND":
        fields = {"type": tag, "bound": (float, ...), "direction": (_enum("at_least", "at_most"), ...), **amount_common}
    elif form_type == "AMOUNT_RELATIVE":
        fields = {
            "type": tag,
            "reference_slot": (str, Field(..., description="slot id the amount is relative to, e.g. 'income.client.grossMonthly'")),
            "relation_word": (str, Field(..., description="'about the same', 'a bit less', 'half'")),
        }
    elif form_type == "AMOUNT_VAGUE":
        fields = {"type": tag, "vague_word": (str, Field(..., description="'a few hundred K', 'six figures'")), **amount_common}
    elif form_type == "DATE_ABS":
        fields = {"type": tag, "year": (int, ...), "month": (int, Field(..., description="1-12, or 0 when not said")), "day": (int, Field(..., description="1-31, or 0 when not said"))}
    elif form_type == "DURATION_FROM_ANCHOR":
        fields = {
            "type": tag,
            "form": (_enum("value", "vague"), ...),
            "value": (float, Field(..., description="0 when form is vague")),
            "vague_word": (str, Field(..., description="'' when form is value")),
            "unit": (_enum("years", "months"), ...),
            "anchor": (str, Field(..., description="'call' for time from the call date, or the slot id it counts from")),
        }
    elif form_type == "AGE":
        fields = {
            "type": tag,
            "form": (_enum("value", "range", "vague"), ...),
            "value": (float, Field(..., description="0 unless form is value")),
            "low": (float, Field(..., description="0 unless form is range")),
            "high": (float, Field(..., description="0 unless form is range")),
            "vague_word": (str, Field(..., description="'' unless form is vague, e.g. 'early sixties'")),
        }
    elif form_type == "RATE":
        fields = {
            "type": tag,
            "form": (_enum("value", "vague"), ...),
            "value": (float, Field(..., description="a decimal (0.115 for 11.5%); 0 when form is vague")),
            "vague_word": (str, Field(..., description="'' when form is value, e.g. 'growing a bit', 'prime plus one'")),
            "real_or_nominal": (_enum("real", "nominal", "na"), ...),
        }
    elif form_type == "PROBABILITY_EVENT":
        fields = {"type": tag, "event": (str, ...), "likelihood_word": (str, Field(..., description="'might', 'probably', 'definitely'"))}
    elif form_type == "CATEGORY":
        cats = tuple(slot.categories or ())
        label: Any = _enum(*cats) if cats else str
        fields = {"type": tag, "label": (label, ...), "certainty_word": (str, Field(..., description="'fairly', 'I think', or ''"))}
    elif form_type == "BOOLEAN":
        fields = {"type": tag, "value": (bool, ...), "certainty_word": (str, Field(..., description="'I think', 'definitely', or ''"))}
    elif form_type == "TEXT":
        fields = {"type": tag, "text": (str, Field(..., description="the client's words, short"))}
    else:  # pragma: no cover - FORM_TYPES is closed
        raise ValueError(form_type)
    return create_model(name, __base__=_Strict, **fields)


def _hypothesis_model(slot: SlotDef) -> type[BaseModel]:
    forms = [_form_model(f, slot) for f in slot.allowed_forms]
    inter: Any = forms[0] if len(forms) == 1 else Union[tuple(forms)]  # noqa: UP007 - a union built from a runtime tuple; anyOf, not a discriminated oneOf
    return create_model(
        f"Hypothesis__{_key(slot.slot_id)}",
        __base__=_Strict,
        intermediate=(inter, Field(..., description="the value as spoken, never converted or guessed" + _bounds_note(slot))),
        weight=(float, Field(..., description="the client's own weight 0..1, or 0 when none was given")),
        rationale=(str, Field(..., description="one line for the reviewer")),
        revises=(bool, Field(..., description="true when a later statement changes an earlier one")),
    )


def _value_item_model(slot: SlotDef, instances: tuple[str, ...]) -> type[BaseModel]:
    inst: Any = _enum(*instances) if instances else str
    return create_model(
        f"Value__{_key(slot.slot_id)}",
        __base__=_Strict,
        instance=(inst, Field(..., description="who or what the value is about: an entity id, client/spouse, or '-'")),
        hypotheses=(list[_hypothesis_model(slot)], Field(..., description="one per option the client gave")),  # type: ignore[misc]
        hedges=(list[str], Field(..., description="hedge words as said: 'maybe', 'I think'")),
        speaker_attribution=(_enum(*ATTRIBUTIONS), ...),
        said=(str, Field(..., description="the speaker's words, verbatim and short")),
        interpretation=(str, Field(..., description="one line: what you take the words to mean, and why")),
        self_score=(float, Field(..., description="your confidence 0..1 that this is what the client meant")),
    )


# --------------------------------------------------------------------------- stage models


@dataclass
class TaskSchemas:
    """The compiled models for one extraction task (§7.2)."""

    group: str
    slots: list[SlotDef]
    instances: dict[str, tuple[str, ...]] = field(default_factory=dict)  # slot id → allowed instance keys
    objects: type[BaseModel] | None = None
    statuses: type[BaseModel] | None = None
    values: type[BaseModel] | None = None
    combined: type[BaseModel] | None = None  # stages 2+3 merged for small single-instance groups


def compile_objects(group: str, catalog: Catalog, *, registry_ids: tuple[str, ...] = ()) -> type[BaseModel]:
    """Stage 1: every object of the group's list entity mentioned in the window."""
    g = catalog.groups[group]
    if not g.list_entity:
        raise ValueError(f"group {group} has no list entity")
    types = LIST_ENTITY_TYPES.get(g.list_entity, (LIST_ENTITY_TYPE[g.list_entity],))
    fields: dict[str, Any] = {
        "entity_id": (str, Field(..., description="an existing registry id" + (f" ({', '.join(registry_ids[:40])})" if registry_ids else "") + ", or 'new:<short name>'")),
        "entity_type": (_enum(*types), ...),
        "name": (str, Field(..., description="short canonical name, e.g. 'Thandi' or 'Discovery life policy'")),
        "segment_ids": (list[str], Field(..., description="segments that mention this object")),
    }
    if g.list_entity == "expense":
        keys = tuple(e.key for e in catalog.expense_items) + ("custom",)
        fields["catalog_key"] = (_enum(*keys), Field(..., description="the expense-catalog line this budget item belongs to, or 'custom'"))
    item = create_model(f"Object__{group}", __base__=_Strict, **fields)
    return create_model(f"Objects__{group}", __base__=_Strict, objects=(list[item], ...))  # type: ignore[valid-type]


def compile_statuses(group: str, paths: list[str]) -> type[BaseModel]:
    """Stage 2: each field's status and the segments that discuss it."""
    path_t: Any = _enum(*paths) if paths else str
    item = create_model(
        f"Status__{group}",
        __base__=_Strict,
        fna_path=(path_t, ...),
        status=(_enum(*STATUSES), ...),
        segment_ids=(list[str], Field(..., description="empty when not_discussed")),
    )
    return create_model(f"Statuses__{group}", __base__=_Strict, fields=(list[item], ...))  # type: ignore[valid-type]


def compile_values(group: str, slots: list[SlotDef], instances: dict[str, tuple[str, ...]] | None = None) -> type[BaseModel]:
    """Stage 3: one property per slot holding a value item per instance."""
    instances = instances or {}
    props = {
        _key(s.slot_id): (list[_value_item_model(s, instances.get(s.slot_id, ()))], Field(..., description=s.question))  # type: ignore[misc]
        for s in slots
    }
    return create_model(f"Values__{group}", __base__=_Strict, **props)


def compile_combined(group: str, slots: list[SlotDef], instances: dict[str, tuple[str, ...]] | None = None) -> type[BaseModel]:
    """Stages 2 and 3 merged, for single-instance groups with fewer than six slots (§7.2)."""
    instances = instances or {}
    props: dict[str, Any] = {}
    for s in slots:
        base = _value_item_model(s, instances.get(s.slot_id, ()))
        item = create_model(
            f"Combined__{_key(s.slot_id)}",
            __base__=base,
            status=(_enum(*STATUSES), ...),
            segment_ids=(list[str], Field(..., description="empty when not_discussed")),
        )
        props[_key(s.slot_id)] = (list[item], Field(..., description=s.question))  # type: ignore[valid-type]
    return create_model(f"Combined__{group}", __base__=_Strict, **props)


def compile_task(
    group: str,
    catalog: Catalog,
    *,
    slot_ids: list[str] | None = None,
    instances: dict[str, tuple[str, ...]] | None = None,
    concrete_paths: list[str] | None = None,
    registry_ids: tuple[str, ...] = (),
) -> TaskSchemas:
    slots = [catalog.slot(i) for i in slot_ids] if slot_ids else catalog.in_group(group)
    ts = TaskSchemas(group=group, slots=slots, instances=instances or {})
    g = catalog.groups[group]
    if g.list_entity:
        ts.objects = compile_objects(group, catalog, registry_ids=registry_ids)
    ts.statuses = compile_statuses(group, concrete_paths or [])
    ts.values = compile_values(group, slots, instances)
    if not g.list_entity and len(slots) < 6:
        ts.combined = compile_combined(group, slots, instances)
    return ts


# --------------------------------------------------------------------------- conversion back to shared models


def to_object_refs(out: BaseModel) -> list[tuple[ObjectRef, str | None]]:
    """Objects → (ObjectRef, expense catalog key or None)."""
    refs = []
    for o in out.objects:  # type: ignore[attr-defined]
        refs.append((ObjectRef(entity_id=o.entity_id, entity_type=o.entity_type, name=o.name, segment_ids=list(o.segment_ids)), getattr(o, "catalog_key", None)))
    return refs


def to_field_statuses(out: BaseModel) -> list[FieldStatus]:
    return [FieldStatus(fna_path=f.fna_path, status=f.status, segment_ids=list(f.segment_ids)) for f in out.fields]  # type: ignore[attr-defined]


def to_field_values(out: BaseModel, slots: list[SlotDef], resolve_path) -> list[FieldValue]:
    """Values → shared ``FieldValue``s. ``resolve_path(slot, instance)`` gives the concrete fna_path."""
    by_key = {_key(s.slot_id): s for s in slots}
    values: list[FieldValue] = []
    for key, items in out:
        slot = by_key[key]
        for it in items:
            hyps = [
                RawHypothesis(
                    intermediate=FORM_TYPES[h.intermediate.type].model_validate(h.intermediate.model_dump()),
                    weight=h.weight,
                    rationale=h.rationale,
                    revises=h.revises,
                )
                for h in it.hypotheses
            ]
            values.append(
                FieldValue(
                    fna_path=resolve_path(slot, it.instance),
                    hypotheses=hyps,
                    hedges=list(it.hedges),
                    speaker_attribution=it.speaker_attribution,
                    said=it.said,
                    interpretation=it.interpretation,
                    self_score=it.self_score,
                )
            )
    return values


def to_combined(out: BaseModel, slots: list[SlotDef], resolve_path) -> tuple[list[FieldStatus], list[FieldValue]]:
    by_key = {_key(s.slot_id): s for s in slots}
    statuses: list[FieldStatus] = []
    values: list[FieldValue] = []
    for key, items in out:
        slot = by_key[key]
        for it in items:
            path = resolve_path(slot, it.instance)
            statuses.append(FieldStatus(fna_path=path, status=it.status, segment_ids=list(it.segment_ids)))
            if it.status in ("stated", "implied") and it.hypotheses:
                values.append(
                    FieldValue(
                        fna_path=path,
                        hypotheses=[
                            RawHypothesis(
                                intermediate=FORM_TYPES[h.intermediate.type].model_validate(h.intermediate.model_dump()),
                                weight=h.weight,
                                rationale=h.rationale,
                                revises=h.revises,
                            )
                            for h in it.hypotheses
                        ],
                        hedges=list(it.hedges),
                        speaker_attribution=it.speaker_attribution,
                        said=it.said,
                        interpretation=it.interpretation,
                        self_score=it.self_score,
                    )
                )
    return statuses, values


# --------------------------------------------------------------------------- provider schemas and limits


def anthropic_schema(model: type[BaseModel]) -> dict[str, Any]:
    from anthropic import transform_schema

    return transform_schema(model)


def openai_schema(model: type[BaseModel]) -> dict[str, Any]:
    """OpenAI strict mode: every property required, ``additionalProperties: false``, ``anyOf`` allowed."""
    schema = model.model_json_schema()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return schema


def google_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Gemini's response schema: references inlined (its OpenAPI subset has no ``$ref``)."""
    return _inline_refs(model.model_json_schema())


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                return resolve(defs[name])
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


@dataclass
class SchemaStats:
    union_params: int = 0
    optional_params: int = 0
    object_properties: int = 0
    max_depth: int = 0
    enum_values: int = 0
    string_size: int = 0  # property names + enum values, as OpenAI counts them
    leftover_constraints: int = 0  # keywords the strict transform moved into descriptions (e.g. const)


def schema_stats(schema: dict[str, Any]) -> SchemaStats:
    """Counts over the schema with references expanded, so a definition used twice counts twice."""
    inlined = _inline_refs(schema)
    st = SchemaStats()

    def walk(node: Any, depth: int) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                st.max_depth = max(st.max_depth, depth)
                props = node["properties"]
                required = set(node.get("required", []))
                st.object_properties += len(props)
                for name, p in props.items():
                    st.string_size += len(name)
                    if name not in required:
                        st.optional_params += 1
                    if isinstance(p, dict) and ("anyOf" in p or "oneOf" in p or (isinstance(p.get("type"), list) and "null" in p["type"])):
                        st.union_params += 1
                    walk(p, depth + 1)
                return
            if "enum" in node:
                st.enum_values += len(node["enum"])
                st.string_size += sum(len(str(v)) for v in node["enum"])
            desc = node.get("description")
            if isinstance(desc, str) and desc.rstrip().endswith("}") and "{" in desc and "const:" in desc:
                st.leftover_constraints += 1
            for k, v in node.items():
                if k in ("anyOf", "oneOf", "allOf"):
                    for variant in v:
                        walk(variant, depth)
                elif k == "items" or isinstance(v, (dict, list)) and k not in ("properties",):
                    walk(v, depth)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth)

    walk(inlined, 1)
    return st


# Documented limits per provider. Anthropic's are the tightest and set the slot-group size (§4.4).
LIMITS: dict[str, dict[str, int]] = {
    "anthropic": {"union_params": 16, "optional_params": 24, "strict_tools": 20},
    "openai": {"object_properties": 5000, "max_depth": 10, "enum_values": 1000, "string_size": 120_000, "optional_params": 0},
    "google": {"max_depth": 10, "object_properties": 1000},
}


def check_limits(stats: list[SchemaStats], provider: str, *, strict_tools: int = 0) -> list[str]:
    """Problems for one request carrying ``stats`` schemas (empty when within limits)."""
    lim = LIMITS[provider]
    total = SchemaStats(
        union_params=sum(s.union_params for s in stats),
        optional_params=sum(s.optional_params for s in stats),
        object_properties=sum(s.object_properties for s in stats),
        max_depth=max((s.max_depth for s in stats), default=0),
        enum_values=sum(s.enum_values for s in stats),
        string_size=sum(s.string_size for s in stats),
        leftover_constraints=sum(s.leftover_constraints for s in stats),
    )
    problems = []
    for key, cap in lim.items():
        val = strict_tools if key == "strict_tools" else getattr(total, key)
        if val > cap:
            problems.append(f"{provider}: {key} = {val} exceeds {cap}")
    if provider == "anthropic" and total.leftover_constraints:
        problems.append(f"anthropic: {total.leftover_constraints} constraint(s) would be dropped into descriptions")
    return problems

