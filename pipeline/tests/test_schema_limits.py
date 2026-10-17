"""Every slot group compiles to strict schemas within Anthropic, OpenAI and Gemini limits (§4.4)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from advisor_intel.models.extraction import AmountApprox, CategoryForm
from advisor_intel.schema.compiler import (
    _key,
    anthropic_schema,
    check_limits,
    compile_task,
    google_schema,
    openai_schema,
    schema_stats,
    to_field_statuses,
    to_field_values,
)
from advisor_intel.schema.slots import load_catalog

CAT = load_catalog()
MAX_INSTANCES = 10  # a household with ten rows in one list is already extreme


def _worst_case(group: str):
    slots = CAT.in_group(group)
    g = CAT.groups[group]
    if g.list_entity:
        inst = tuple(f"{g.list_entity}.item_{i}" for i in range(MAX_INSTANCES))
    elif any(s.per_person for s in slots):
        inst = ("client", "spouse")
    else:
        inst = ("-",)
    instances = {s.slot_id: inst for s in slots}
    paths = [f"{s.slot_id}#{i}" for s in slots for i in inst]
    registry = tuple(f"{g.list_entity}.item_{i}" for i in range(MAX_INSTANCES)) if g.list_entity else ()
    return compile_task(group, CAT, instances=instances, concrete_paths=paths, registry_ids=registry)


@pytest.mark.parametrize("group", list(CAT.groups))
@pytest.mark.parametrize("provider,to_schema", [("anthropic", anthropic_schema), ("openai", openai_schema), ("google", google_schema)])
def test_stage_schemas_within_limits(group, provider, to_schema):
    ts = _worst_case(group)
    for model in (ts.objects, ts.statuses, ts.values, ts.combined):
        if model is None:
            continue
        problems = check_limits([schema_stats(to_schema(model))], provider)
        assert not problems, f"{group}/{model.__name__}: {problems}"


@pytest.mark.parametrize("group", list(CAT.groups))
def test_no_optional_fields_or_dicts(group):
    ts = _worst_case(group)
    for model in (ts.objects, ts.statuses, ts.values, ts.combined):
        if model is None:
            continue
        schema = anthropic_schema(model)
        st = schema_stats(schema)
        assert st.optional_params == 0
        assert st.leftover_constraints == 0
        assert '"additionalProperties": {' not in str(schema)


def test_category_labels_are_enums_and_round_trip():
    slots = CAT.in_group("household")
    ts = compile_task("household", CAT, concrete_paths=[s.slot_id for s in slots])
    key = _key("household.maritalRegime")
    payload = {k: [] for k in ts.values.model_fields}
    payload[key] = [
        {
            "instance": "-",
            "hypotheses": [
                {
                    "intermediate": {"type": "CATEGORY", "label": "anc-accrual", "certainty_word": "I think"},
                    "weight": 0,
                    "rationale": "said ANC with accrual",
                    "revises": False,
                }
            ],
            "hedges": ["I think"],
            "speaker_attribution": "client",
            "said": "we signed an ANC with accrual I think",
            "interpretation": "antenuptial contract with accrual",
            "self_score": 0.8,
        }
    ]
    out = ts.values.model_validate(payload)
    vals = to_field_values(out, slots, lambda s, i: s.fna_path)
    assert len(vals) == 1 and isinstance(vals[0].hypotheses[0].intermediate, CategoryForm)
    bad = dict(payload)
    bad[key] = [
        dict(
            payload[key][0],
            hypotheses=[
                {
                    "intermediate": {"type": "CATEGORY", "label": "married-ish", "certainty_word": ""},
                    "weight": 0,
                    "rationale": "",
                    "revises": False,
                }
            ],
        )
    ]
    with pytest.raises(ValidationError):
        ts.values.model_validate(bad)


def test_amount_forms_round_trip_and_statuses():
    slots = [CAT.slot("income.{p}.grossMonthly")]
    ts = compile_task(
        "income",
        CAT,
        slot_ids=[s.slot_id for s in slots],
        instances={"income.{p}.grossMonthly": ("client", "spouse")},
        concrete_paths=["income.client.grossMonthly"],
    )
    out = ts.values.model_validate(
        {
            _key("income.{p}.grossMonthly"): [
                {
                    "instance": "client",
                    "hypotheses": [
                        {
                            "intermediate": {
                                "type": "AMOUNT_APPROX",
                                "value": 42000,
                                "approx_word": "about",
                                "currency": "ZAR",
                                "period": "monthly",
                                "basis": "net",
                                "priced_year": 0,
                            },
                            "weight": 0,
                            "rationale": "take-home",
                            "revises": False,
                        }
                    ],
                    "hedges": [],
                    "speaker_attribution": "client",
                    "said": "I take home about forty-two thousand a month",
                    "interpretation": "net monthly salary about R42,000",
                    "self_score": 0.9,
                }
            ]
        }
    )
    vals = to_field_values(out, slots, lambda s, i: s.fna_path_for(i))
    assert vals[0].fna_path == "income.client.grossMonthly"
    assert isinstance(vals[0].hypotheses[0].intermediate, AmountApprox)
    st = ts.statuses.model_validate({"fields": [{"fna_path": "income.client.grossMonthly", "status": "stated", "segment_ids": ["s0012"]}]})
    assert to_field_statuses(st)[0].status == "stated"
