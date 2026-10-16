"""The agent harness (§13): policy order, tools, budgets, loop detection, checkpoints and resume."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from advisor_intel import llm
from advisor_intel.config import Budgets
from advisor_intel.discourse.run import run_discourse
from advisor_intel.extract.reducer import check_profile, make_ops
from advisor_intel.harness.checkpoint import InMemoryCheckpointStore
from advisor_intel.harness.deps import AgentInputs, HarnessServices, ServiceResult
from advisor_intel.harness.run import run_agent
from advisor_intel.models import Entity, Evidence, Hypothesis, Point, Quantity
from advisor_intel.models.extraction import ExtractionResult
from advisor_intel.models.facts import Provenance
from advisor_intel.models.profile import Profile
from advisor_intel.models.runs import VersionPins
from advisor_intel.extract.reducer import apply_state_ops
from advisor_intel.testing.calls import SAMPLE_CALL
from advisor_intel.testing.transcripts import transcript_from_turns

T = transcript_from_turns(SAMPLE_CALL)
DISC = asyncio.run(run_discourse(T))


def inputs(pins: VersionPins | None = None, budgets: Budgets | None = None) -> AgentInputs:
    prof = Profile(call_id=T.call_id, run_id="wf-h", schema_version="profile_v1", anchor_date=T.anchor_date,
                   entities={"person.spouse": Entity(entity_id="person.spouse", type="person", canonical_name="Lerato")})
    return AgentInputs(call_id=T.call_id, run_id="wf-h", transcript=T, discourse=DISC, profile=prof, pins=pins or VersionPins(), budgets=budgets or Budgets())


def fake_services(calls: list[str], fail_on: str | None = None) -> HarnessServices:
    async def extract(task, deps):
        calls.append(task.task_id)
        if fail_on and task.task_id == fail_on:
            raise RuntimeError("boom")
        seg = T.segments[3]
        slot = deps.catalog.in_group(task.slot_group)[0]
        inst = task.instance_keys[0] if task.instance_keys and task.instance_keys[0] and slot.per_person else None
        fid = f"{slot.slot_id}#{inst}" if inst else slot.slot_id
        if slot.per_entity:
            return deps.profile, ExtractionResult(task_id=task.task_id, usage={"requests": 2, "input_tokens": 1000, "output_tokens": 200})
        payload = {"slot_id": slot.slot_id, "instance_key": inst, "fna_path": slot.fna_path_for(inst), "hypotheses": [Hypothesis(value=Quantity(dist=Point(value="x"), unit="none"), weight=1, rationale="")],
                   "evidence": [Evidence(segment_id=seg.segment_id, char_start=0, char_end=5, quote=seg.text[:5], speaker_role=seg.speaker_role, t_start=seg.t_start, t_end=seg.t_end, asr_mean_conf=0.95, role="supports")],
                   "provenance": Provenance(run_id="wf-h")}
        res = apply_state_ops(deps.profile, make_ops(deps.profile, [("ADD", fid, seg.segment_id, payload, "extractor")]))
        return res.profile, ExtractionResult(task_id=task.task_id, ops_applied=len(res.applied), new_fact_ids=res.touched, usage={"requests": 2, "input_tokens": 1000, "output_tokens": 200})

    async def verify(profile, ids, deps):
        return ServiceResult(data={"verified": len(ids)})

    async def constraints(profile, deps):
        return ServiceResult(data=[])

    async def sensitivity(profile, deps):
        return ServiceResult(data=[{"fact_id": "x", "priority": 0.1}])

    async def followups(profile, deps):
        return ServiceResult(data=[])

    return HarnessServices(extract=extract, verify=verify, constraints=constraints, sensitivity=sensitivity, followups=followups)


def tool_sequence(state: dict) -> list[str]:
    return [e["tool"] for e in state["events"]]


async def test_policy_run_follows_expected_behaviour():
    calls: list[str] = []
    store = InMemoryCheckpointStore()
    res = await run_agent(inputs(), services=fake_services(calls), checkpoints=store)
    assert not res.outcome.partial, res.outcome.reason
    seq = tool_sequence(res.state)
    assert seq[0] == "make_plan"
    # Every extraction is followed by verification of its group (§13.4 step 3).
    for i, t in enumerate(seq):
        if t == "extract_slot_group":
            assert seq[i + 1] == "verify_facts"
    assert seq[-3:] == ["check_constraints", "run_sensitivity", "draft_followups"]
    todo = [t for t in res.plan.tasks if t.status not in ("done", "skipped")]
    assert not todo
    assert calls.index("people:client") < calls.index("retirement:client")
    assert check_profile(res.profile) == []
    assert store.saves == res.outcome.turns and res.state["subagent_usage"]["requests"] >= 2 * len(calls)
    assert "Extracted" in res.summary


async def test_tool_failure_marks_task_failed_and_run_continues():
    calls: list[str] = []
    res = await run_agent(inputs(), services=fake_services(calls, fail_on="income:client"))
    t = next(t for t in res.plan.tasks if t.task_id == "income:client")
    assert t.status == "failed" and res.state["tool_errors"]["extract_slot_group"] == 1
    assert not res.outcome.partial


async def test_budget_exhaustion_is_partial_with_unfinished_groups():
    calls: list[str] = []
    res = await run_agent(inputs(budgets=Budgets(orchestrator_requests=4)), services=fake_services(calls))
    assert res.outcome.partial and res.outcome.unfinished_groups
    assert all(t.status in ("done", "skipped", "failed") for t in res.plan.tasks)
    assert any((t.skip_reason or "").startswith("not reached") for t in res.plan.tasks)


async def test_resume_from_checkpoint_does_not_redo_work():
    calls: list[str] = []
    store = InMemoryCheckpointStore()
    beats = {"n": 0}

    def heartbeat(_):
        beats["n"] += 1
        if beats["n"] == 9:
            raise RuntimeError("worker lost")

    with pytest.raises(RuntimeError):
        await run_agent(inputs(), services=fake_services(calls), checkpoints=store, heartbeat=heartbeat)
    first = list(calls)
    res = await run_agent(inputs(), services=fake_services(calls), checkpoints=store)
    assert not res.outcome.partial
    redone = [t for t in first if calls.count(t) > 1]
    assert redone == [], redone


async def test_loop_detection_and_finish_refused_while_tasks_open():
    turn = {"n": 0}

    def model(messages, info):
        out = info.model_request_parameters.output_object
        if out is not None and "order" in out.json_schema.get("properties", {}):  # the planner shares the frontier model
            return ModelResponse(parts=[TextPart(content=json.dumps({"order": [], "rationale": "test plan"}))])
        turn["n"] += 1
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        n = turn["n"]
        if n == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name="make_plan", args={})])
        if n <= 4:
            return ModelResponse(parts=[ToolCallPart(tool_name="view_plan", args={})])
        if n == 5:
            return ModelResponse(parts=[TextPart(content=json.dumps({"summary": "done", "unresolved": []}))])
        plan = next(p.content for p in reversed(returns) if p.tool_name in ("make_plan", "view_plan") and isinstance(p.content, dict))
        todo = [t["task_id"] for t in plan["tasks"] if t["status"] == "todo"]
        if n == 6:
            return ModelResponse(parts=[ToolCallPart(tool_name="update_plan", args={"action": "skip", "task_id": tid, "reason": "test skip"}, tool_call_id=f"c{i}") for i, tid in enumerate(todo)])
        return ModelResponse(parts=[TextPart(content=json.dumps({"summary": "skipped everything", "unresolved": []}))])

    pins = VersionPins(frontier_model=llm.register_local_model("loopy", FunctionModel(model)))
    res = await run_agent(inputs(pins=pins), services=fake_services([]))
    events = res.state["events"]
    blocked = [e for e in events if e["tool"] == "view_plan" and e["note"] == "repeated"]
    assert len(blocked) == 1  # the third identical call in ten is blocked
    assert res.summary == "skipped everything"
    assert all(t.status == "skipped" for t in res.plan.tasks)
