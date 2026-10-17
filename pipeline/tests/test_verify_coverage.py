"""Omission check (§9.1d): expected-fact list, closed questions batched 20 per call, reopen, disagree twice."""

from __future__ import annotations

import json
import re

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from advisor_intel.extract.reducer import check_profile
from advisor_intel.models.facts import Hypothesis
from advisor_intel.models.profile import StateOp
from advisor_intel.models.runs import VersionPins
from advisor_intel.schema.compiler import anthropic_schema, check_limits, schema_stats
from advisor_intel.verify.coverage import OFFLINE_SKIP, CoverageAnswers, check_coverage, coverage_agent, expected_facts, is_missing

from .verify_factories import CATALOG, PROV, ProfileBuilder, ev, point, segment, transcript


def _household():
    b = ProfileBuilder(spouse=True)
    b.entity("person.neo", "person", "Neo", relation="child").entity("person.gogo", "person", "Mmabatho", relation="parent")
    b.entity("policy.disc", "policy", "Discovery life").entity("account.la", "account", "Sygnia living annuity")
    b.add("household.maritalStatus", None, point("married"))
    b.add("dependants[].relationship", "person.neo", point("child"))
    b.add("dependants[].dateOfBirth", "person.neo", point("2016-05-01", "date"))
    b.add("dependants[].relationship", "person.gogo", point("parent"))
    b.add("policies[].type", "policy.disc", point("life"))
    b.add("retirementFunds[].type", "account.la", point("living-annuity"))
    b.add("retirement.{p}.targetMode", "client", point("amount"))
    b.seed("{p}.employer", "client", point("Eskom"))
    return b.build()


def _ids(items):
    return {it.fact_id: it for it in items}


def test_expected_list_covers_tier_a_persons_entity_attributes_and_prior_fields():
    p = _household()
    items = _ids(expected_facts(p, CATALOG))
    for person in ("client", "spouse"):
        for slot in (
            "{p}.dateOfBirth",
            "{p}.employmentType",
            "income.{p}.grossMonthly",
            "retirement.{p}.retirementAge",
            "estate.{p}.hasWill",
        ):
            assert f"{slot}#{person}" in items, (slot, person)
    assert "retirement.{p}.targetMonthly#client" in items and "retirement.{p}.targetPercent#client" not in items  # target mode is amount
    assert "retirement.{p}.targetPercent#spouse" in items
    assert "estate.{p}.guardianNominated#client" in items  # there is a child
    assert "household.maritalRegime" in items
    # what the FNA engine needs per entity
    assert {
        "dependants[].dateOfBirth#person.neo",
        "dependants[].tertiary#person.neo",
        "dependants[].tertiaryStartAge#person.neo",
        "dependants[].tertiaryCostAnnual#person.neo",
    } <= set(items)
    assert "dependants[].tertiary#person.gogo" not in items  # no education plan for a parent
    assert {"policies[].cover#policy.disc", "policies[].lifeAssured#policy.disc", "policies[].beneficiary#policy.disc"} <= set(items)
    assert "retirementFunds[].drawdownRate#account.la" in items and "retirementFunds[].employeeMonthly#account.la" not in items
    assert items["{p}.employer#client"].reasons == ["prior_field"]
    assert items["{p}.dateOfBirth#client"].label == "the client's date of birth"
    assert items["policies[].cover#policy.disc"].label == "the cover amount of the policy 'Discovery life'"
    # already-known facts are on the list but are not asked
    assert not is_missing(p, items["dependants[].relationship#person.neo"])
    assert is_missing(p, items["{p}.employer#client"]) and is_missing(p, items["{p}.dateOfBirth#client"])


def test_single_client_skips_spouse_regime_and_guardian():
    b = ProfileBuilder()
    b.add("household.maritalStatus", None, point("single"))
    items = _ids(expected_facts(b.build(), CATALOG))
    assert not any(fid.endswith("#spouse") for fid in items)
    assert "household.maritalRegime" not in items and "household.includeSpouse" not in items
    assert "estate.{p}.guardianNominated#client" not in items


def test_topic_indexed_groups_without_facts_become_items():
    p = _household()
    items = _ids(expected_facts(p, CATALOG, topic_index={"goals": ["s0005"], "estate_needs": ["s0006"], "policies": ["s0007"]}))
    assert items["goals[].name"].reasons == ["topic_index"] and items["goals[].name"].instance is None
    assert "any other savings goals" in items["goals[].name"].label
    assert "estate.{p}.additionalCapitalNeed#client" in items  # a per-person group: its tier A/B slots
    assert not any(it.reasons == ["topic_index"] and it.group == "policies" for it in items.values())  # policies produced a fact


def _segments(n: int = 6):
    return transcript(
        [segment(f"s{i:04d}", f"segment number {i} about money and retirement", "client_primary", t0=i * 5.0) for i in range(1, n + 1)]
    )


def _responder(rule):
    """FunctionModel answering each question by rule(question line) → (answer, segment ids); records batches."""
    batches: list[list[str]] = []

    def fn(messages, info: AgentInfo):
        prompt = str(messages[-1].parts[-1].content)
        qs = re.findall(r"^- (q\d+): (.*)$", prompt, flags=re.MULTILINE)
        batches.append([q for q, _ in qs])
        answers = []
        for qid, line in qs:
            ans, segs = rule(line)
            answers.append({"question_id": qid, "answer": ans, "segment_ids": segs, "reason": "r"})
        return ModelResponse(parts=[TextPart(json.dumps({"answers": answers}))])

    return FunctionModel(fn), batches


async def _no_reopen(slot_id, instance, segs):
    return []


async def test_questions_are_batched_twenty_per_call():
    b = ProfileBuilder(spouse=True)
    for i in range(12):
        b.entity(f"policy.p{i}", "policy", f"Policy {i}")
        b.add("policies[].type", f"policy.p{i}", point("life"))
    p = b.build()
    model, batches = _responder(lambda line: ("absent", []))
    with coverage_agent.override(model=model):
        res = await check_coverage(p, transcript=_segments(), pins=VersionPins(), catalog=CATALOG, reopen=_no_reopen)
    sizes = [len(x) for x in batches]
    n = len(res.asked)
    assert n > 40 and sizes == [20] * (n // 20) + ([n % 20] if n % 20 else [])
    assert res.rounds == 1 and res.ops == [] and res.profile == p


async def test_present_reopens_the_field_through_the_reducer():
    p = _household()
    calls = []

    async def reopen(slot_id, instance, segs):
        calls.append((slot_id, instance, segs))
        if slot_id != "retirement.{p}.retirementAge" or instance != "client":
            return []
        payload = {
            "slot_id": slot_id,
            "instance_key": "client",
            "fna_path": "retirement.client.retirementAge",
            "status": "stated",
            "hypotheses": [Hypothesis(value=point(62, "age"), weight=1, rationale="said").model_dump(mode="json")],
            "evidence": [ev("s0003", "segment number 3").model_dump(mode="json")],
            "entity_refs": ["person.client_primary"],
            "provenance": PROV.model_dump(mode="json"),
        }
        return [
            StateOp(op="ADD", fact_id="retirement.{p}.retirementAge#client", segment_id="s0003", payload=payload, actor="extractor", seq=1)
        ]

    model, _ = _responder(lambda line: ("present", ["s0003"]) if "client's target retirement age" in line else ("absent", []))
    with coverage_agent.override(model=model):
        res = await check_coverage(p, transcript=_segments(), pins=VersionPins(), catalog=CATALOG, reopen=reopen)
    assert calls == [("retirement.{p}.retirementAge", "client", ["s0003"])]
    f = res.profile.facts["retirement.{p}.retirementAge#client"]
    assert f.status == "stated" and f.reopened_by_coverage
    assert f.verification["coverage"]["reopened"] and f.verification["coverage"]["segments"] == ["s0003"]
    assert res.reopened == ["retirement.{p}.retirementAge#client"] and res.contradicted == []
    assert res.ops[0].seq == p.next_seq()  # renumbered after the profile's own ops
    assert check_profile(res.profile) == []


async def test_disagreeing_twice_marks_the_field_contradicted():
    p = _household()
    target = {"the client's gross salary", "the client's employer", "the client's occupation"}  # occupation: not expected, never asked
    asked: list[str] = []

    def rule(line):
        hit = any(t in line for t in target)
        if hit:
            asked.append(line)
        return ("present", ["s0002"]) if hit else ("absent", [])

    model, batches = _responder(rule)
    with coverage_agent.override(model=model):
        res = await check_coverage(p, transcript=_segments(), pins=VersionPins(), catalog=CATALOG, reopen=_no_reopen)
    assert res.rounds == 2 and len(batches) >= 2 and len(asked) == 4  # salary and employer, asked twice each
    inc = res.profile.facts["income.{p}.grossMonthly#client"]  # missing before: added with the population prior to show
    assert inc.status == "contradicted" and inc.source == "call" and inc.evidence[0].role == "context"
    assert inc.value.dist == CATALOG.slot("income.{p}.grossMonthly").population_prior
    emp = res.profile.facts["{p}.employer#client"]  # seeded from the prior FNA: keeps the prior value, now contradicted
    assert emp.status == "contradicted" and emp.value == point("Eskom")
    assert set(res.contradicted) == {"income.{p}.grossMonthly#client", "{p}.employer#client"}
    assert all(op.actor == "verifier" for op in res.ops)
    assert check_profile(res.profile) == []


async def test_offline_pins_ask_nothing_of_a_provider():
    p = _household()
    res = await check_coverage(p, transcript=_segments(), pins=VersionPins(), catalog=CATALOG, reopen=_no_reopen)
    assert res.asked and res.answers == {} and res.ops == [] and res.profile == p
    assert res.skipped == OFFLINE_SKIP and res.errors == [] and res.unanswered == []  # "did not run", not "nothing missing"


async def test_a_failed_verifier_batch_is_reported_not_treated_as_absent():
    p = _household()

    def never_valid(messages, info: AgentInfo):  # answers for question ids that do not exist: the retries run out
        return ModelResponse(
            parts=[TextPart(json.dumps({"answers": [{"question_id": "q99", "answer": "absent", "segment_ids": [], "reason": "r"}]}))]
        )

    with coverage_agent.override(model=FunctionModel(never_valid)):
        res = await check_coverage(p, transcript=_segments(), pins=VersionPins(), catalog=CATALOG, reopen=_no_reopen)
    expected_missing = {it.fact_id for it in res.expected if is_missing(p, it)}
    assert res.skipped is None and res.answers == {} and res.profile == p
    assert res.errors and all(e.startswith("omission check batch not answered") and "UnexpectedModelBehavior" in e for e in res.errors)
    assert set(res.unanswered) == expected_missing and res.usage["failures"] == len(res.errors)


async def test_a_reopen_that_raises_is_reported_and_the_check_carries_on():
    p = _household()
    calls = []

    async def reopen(slot_id, instance, segs):
        calls.append(slot_id)
        if slot_id == "{p}.dateOfBirth":
            raise RuntimeError("extractor down")
        return []

    model, _ = _responder(lambda line: ("present", ["s0002"]) if ("date of birth" in line or "gross salary" in line) else ("absent", []))
    with coverage_agent.override(model=model):
        res = await check_coverage(p, transcript=_segments(), pins=VersionPins(), catalog=CATALOG, reopen=reopen)
    assert "{p}.dateOfBirth" in calls and "income.{p}.grossMonthly" in calls  # the failure did not stop the other reopenings
    assert any(e.startswith("reopening {p}.dateOfBirth#") and "RuntimeError" in e for e in res.errors)
    assert "income.{p}.grossMonthly#client" in res.contradicted  # disagreed twice as before
    assert "{p}.dateOfBirth#client" not in res.contradicted  # a failed reopen is not a disagreement
    assert check_profile(res.profile) == []


async def test_on_profile_sees_every_applied_batch():
    p = _household()
    seen: list = []

    async def reopen(slot_id, instance, segs):
        if slot_id != "retirement.{p}.retirementAge":
            return []
        payload = {
            "slot_id": slot_id,
            "instance_key": "client",
            "fna_path": "retirement.client.retirementAge",
            "status": "stated",
            "hypotheses": [Hypothesis(value=point(62, "age"), weight=1, rationale="said").model_dump(mode="json")],
            "evidence": [ev("s0003", "segment number 3").model_dump(mode="json")],
            "provenance": PROV.model_dump(mode="json"),
        }
        return [
            StateOp(op="ADD", fact_id="retirement.{p}.retirementAge#client", segment_id="s0003", payload=payload, actor="extractor", seq=1)
        ]

    model, _ = _responder(lambda line: ("present", ["s0003"]) if "client's target retirement age" in line else ("absent", []))
    with coverage_agent.override(model=model):
        res = await check_coverage(p, transcript=_segments(), pins=VersionPins(), catalog=CATALOG, reopen=reopen, on_profile=seen.append)
    assert seen and "retirement.{p}.retirementAge#client" in seen[0].facts and seen[-1] is res.profile


def test_answer_schema_fits_strict_limits():
    assert check_limits([schema_stats(anthropic_schema(CoverageAnswers))], "anthropic") == []
