"""Stages 3–4: planner, three validated extraction stages and span alignment (§7.1–7.3)."""

from __future__ import annotations

import asyncio

from advisor_intel import llm
from advisor_intel.discourse.run import run_discourse
from advisor_intel.extract.align import align_values, canon
from advisor_intel.extract.context import ExtractionDeps
from advisor_intel.extract.planner import build_plan
from advisor_intel.extract.subagent import run_stages
from advisor_intel.models import Entity
from advisor_intel.models.extraction import AmountApprox, ExtractionTask
from advisor_intel.models.profile import Profile
from advisor_intel.models.runs import VersionPins
from advisor_intel.schema import load_catalog
from advisor_intel.testing.calls import SAMPLE_CALL
from advisor_intel.testing.fakes import scripted_model
from advisor_intel.testing.transcripts import transcript_from_turns

CAT = load_catalog()
T = transcript_from_turns(SAMPLE_CALL)
DISC = asyncio.run(run_discourse(T))
DISC.entities["person.spouse"] = Entity(
    entity_id="person.spouse", type="person", canonical_name="Lerato", attributes={"person_key": "spouse"}
)
PROFILE = Profile(call_id=T.call_id, run_id="wf-test", schema_version=CAT.version, anchor_date=T.anchor_date, entities=dict(DISC.entities))


def sid(text_part: str) -> str:
    return next(s.segment_id for s in T.segments if text_part in s.text)


def task_for(task_id: str) -> ExtractionTask:
    plan = build_plan(CAT, DISC, T, PROFILE)
    return next(t for t in plan.tasks if t.task_id == task_id).model_copy(deep=True)


def deps_for(task: ExtractionTask, pins: VersionPins) -> ExtractionDeps:
    return ExtractionDeps(task=task, catalog=CAT, profile=PROFILE, transcript=T, discourse=DISC, pins=pins)


def test_canon_numbers():
    assert canon("I take home about forty-two thousand a month") == "i take home about 42000 a month"
    assert canon("R45 000") == "r45000"


def _value_item(instance, intermediate, said, revises=False, attribution="client"):
    return {
        "instance": instance,
        "hypotheses": [{"intermediate": intermediate, "weight": 0, "rationale": "as said", "revises": revises}],
        "hedges": ["about"],
        "speaker_attribution": attribution,
        "said": said,
        "interpretation": "monthly take-home",
        "self_score": 0.85,
    }


def income_handler(bad_rate_first: list[int]):
    take_home, correction, bonus = sid("I take home about forty-two"), sid("Actually no, sorry"), sid("thirteenth cheque")

    def handler(prompt: str, schema: dict, info):
        props = schema.get("properties", {})
        if "fields" in props:
            keys = props["fields"]["items"]
            enum = (
                schema["$defs"][keys["$ref"].rsplit("/", 1)[-1]]["properties"]["fna_path"]["enum"]
                if "$ref" in keys
                else keys["properties"]["fna_path"]["enum"]
            )
            out = []
            for k in enum:
                if k.startswith("income.{p}.grossMonthly"):
                    out.append({"fna_path": k, "status": "stated", "segment_ids": [take_home, correction]})
                elif k.startswith("income.{p}.annualBonus"):
                    out.append({"fna_path": k, "status": "stated", "segment_ids": [bonus]})
                else:
                    out.append({"fna_path": k, "status": "not_discussed", "segment_ids": []})
            return {"fields": out}
        out = {k: [] for k in props}
        if "income_p_grossMonthly" in props:
            out["income_p_grossMonthly"] = [
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
                            "rationale": "first figure",
                            "revises": False,
                        },
                        {
                            "intermediate": {
                                "type": "AMOUNT_APPROX",
                                "value": 45000,
                                "approx_word": "more like",
                                "currency": "ZAR",
                                "period": "monthly",
                                "basis": "net",
                                "priced_year": 0,
                            },
                            "weight": 0,
                            "rationale": "corrected",
                            "revises": True,
                        },
                    ],
                    "hedges": ["about", "more like"],
                    "speaker_attribution": "client",
                    "said": "I take home about forty-two thousand a month",
                    "interpretation": "net monthly pay, corrected upward to about 45,000",
                    "self_score": 0.8,
                }
            ]
        if "income_p_annualBonus" in props:
            out["income_p_annualBonus"] = [
                _value_item(
                    "client",
                    {
                        "type": "AMOUNT_RELATIVE",
                        "reference_slot": "income.client.grossMonthly",
                        "relation_word": "one month's salary (a thirteenth cheque)",
                    },
                    "There's a thirteenth cheque in December",
                )
            ]
        return out

    return handler


async def test_income_task_three_stages_and_alignment():
    pins = VersionPins(reasoning_model=llm.register_local_model("income-oracle", scripted_model(income_handler([]))))
    deps = deps_for(task_for("income:client"), pins)
    out = await run_stages(deps)
    gm = "income.{p}.grossMonthly#client"
    assert out.statuses[gm].status == "stated"
    assert out.statuses["income.{p}.uifApplies#client"].status == "not_discussed"
    fv = out.values[gm]
    assert fv.fna_path == "income.client.grossMonthly"
    assert isinstance(fv.hypotheses[0].intermediate, AmountApprox) and fv.hypotheses[1].revises
    assert out.usage.requests == 2  # statuses + values, no retries
    slots = {k: CAT.slot(k.split("#")[0]) for k in out.values}
    al = await align_values(deps, out.values, {k: s.segment_ids for k, s in out.statuses.items()}, slots)
    assert gm in al.aligned and al.aligned[gm].method == "deterministic"
    ev = al.aligned[gm].evidence[0]
    assert ev.segment_id == sid("I take home about forty-two") and ev.quote.lower().startswith("i take home")
    assert 0 < ev.t_start < ev.t_end and ev.asr_mean_conf > 0.9


async def test_field_level_retry_fixes_only_failing_fields():
    calls = {"values": 0}
    base = income_handler([])

    def handler(prompt, schema, info):
        props = schema.get("properties", {})
        out = base(prompt, schema, info)
        if "income_p_grossMonthly" in props:
            calls["values"] += 1
            if calls["values"] == 1:
                out["income_p_grossMonthly"][0]["said"] = ""  # invalid: no quote
        return out

    pins = VersionPins(reasoning_model=llm.register_local_model("retry-oracle", scripted_model(handler)))
    deps = deps_for(task_for("income:client"), pins)
    out = await run_stages(deps)
    gm = "income.{p}.grossMonthly#client"
    assert out.retries.get(gm) == 1 and not out.failures
    assert calls["values"] == 2  # one retry call whose schema held only the failing field


async def test_persistent_failure_becomes_contradicted():
    base = income_handler([])

    def handler(prompt, schema, info):
        out = base(prompt, schema, info)
        if "income_p_annualBonus" in schema.get("properties", {}):
            for it in out["income_p_annualBonus"]:
                it["said"] = ""
        return out

    pins = VersionPins(reasoning_model=llm.register_local_model("fail-oracle", scripted_model(handler)))
    out = await run_stages(deps_for(task_for("income:client"), pins))
    k = "income.{p}.annualBonus#client"
    assert k in out.failures and out.statuses[k].status == "contradicted"


async def test_list_group_objects_create_entities():
    thandi, lwazi = sid("Thandi is fourteen"), sid("Thandi is fourteen")

    def handler(prompt, schema, info):
        props = schema.get("properties", {})
        if "objects" in props:
            return {
                "objects": [
                    {
                        "entity_id": "new:thandi",
                        "entity_type": "person",
                        "name": "Thandi",
                        "segment_ids": [thandi, sid("Thandi wants to do medicine")],
                    },
                    {"entity_id": "new:lwazi", "entity_type": "person", "name": "Lwazi", "segment_ids": [lwazi, "s9999"]},
                    {"entity_id": "new:thandi", "entity_type": "person", "name": "Thandi", "segment_ids": [thandi]},
                ]
            }
        if "fields" in props:
            item = props["fields"]["items"]
            enum = schema["$defs"][item["$ref"].rsplit("/", 1)[-1]]["properties"]["fna_path"]["enum"]
            return {
                "fields": [
                    {
                        "fna_path": k,
                        "status": "stated" if "dateOfBirth" in k or "relationship" in k else "not_discussed",
                        "segment_ids": [thandi] if ("dateOfBirth" in k or "relationship" in k) else [],
                    }
                    for k in enum
                ]
            }
        out = {k: [] for k in props}
        for k, v in props.items():
            inst_schema = schema["$defs"][v["items"]["$ref"].rsplit("/", 1)[-1]]["properties"]["instance"]
            for inst in inst_schema.get("enum", []):
                if k.endswith("dateOfBirth"):
                    age = 14 if "thandi" in inst else 9
                    out[k].append(
                        _value_item(
                            inst,
                            {"type": "AGE", "form": "value", "value": age, "low": 0, "high": 0, "vague_word": ""},
                            "Thandi is fourteen" if age == 14 else "Lwazi is nine",
                        )
                    )
                elif k.endswith("relationship"):
                    out[k].append(_value_item(inst, {"type": "CATEGORY", "label": "child", "certainty_word": ""}, "Thandi is fourteen"))
        return out

    pins = VersionPins(reasoning_model=llm.register_local_model("dep-oracle", scripted_model(handler)))
    deps = deps_for(task_for("dependants"), pins)
    out = await run_stages(deps)
    names = sorted(e.canonical_name for e in out.new_entities.values())
    assert names == ["Lwazi", "Thandi"] and len(out.objects) == 2
    assert all(e.fna_list == "dependants" for e in out.new_entities.values())
    dob = [k for k in out.values if k.startswith("dependants[].dateOfBirth")]
    assert len(dob) == 2
    assert all(v.fna_path.startswith("dependants[") and v.fna_path.endswith("].dateOfBirth") for k, v in out.values.items() if k in dob)
    assert all("s9999" not in o.segment_ids for o in out.objects)


async def test_offline_stub_pins_produce_no_grounded_values():
    deps = deps_for(task_for("income:client"), VersionPins())
    out = await run_stages(deps)
    slots = {k: CAT.slot(k.split("#")[0]) for k in out.values}
    al = await align_values(deps, out.values, {k: s.segment_ids for k, s in out.statuses.items()}, slots)
    assert not al.aligned  # filler output never grounds, so nothing can reach the profile
