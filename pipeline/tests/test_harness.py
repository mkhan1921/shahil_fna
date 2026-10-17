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
from advisor_intel.extract.reducer import apply_state_ops, check_profile, make_ops
from advisor_intel.harness.checkpoint import InMemoryCheckpointStore
from advisor_intel.harness.deps import AgentInputs, HarnessServices, ServiceResult
from advisor_intel.harness.run import run_agent
from advisor_intel.models import Entity, Evidence, Hypothesis, Point, Quantity
from advisor_intel.models.extraction import ExtractionResult
from advisor_intel.models.facts import Provenance
from advisor_intel.models.profile import Profile
from advisor_intel.models.runs import VersionPins
from advisor_intel.testing.calls import SAMPLE_CALL
from advisor_intel.testing.transcripts import transcript_from_turns

T = transcript_from_turns(SAMPLE_CALL)
DISC = asyncio.run(run_discourse(T))


def inputs(pins: VersionPins | None = None, budgets: Budgets | None = None) -> AgentInputs:
    prof = Profile(
        call_id=T.call_id,
        run_id="wf-h",
        schema_version="profile_v1",
        anchor_date=T.anchor_date,
        entities={"person.spouse": Entity(entity_id="person.spouse", type="person", canonical_name="Lerato")},
    )
    return AgentInputs(
        call_id=T.call_id,
        run_id="wf-h",
        transcript=T,
        discourse=DISC,
        profile=prof,
        pins=pins or VersionPins(),
        budgets=budgets or Budgets(),
    )


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
        payload = {
            "slot_id": slot.slot_id,
            "instance_key": inst,
            "fna_path": slot.fna_path_for(inst),
            "hypotheses": [Hypothesis(value=Quantity(dist=Point(value="x"), unit="none"), weight=1, rationale="")],
            "evidence": [
                Evidence(
                    segment_id=seg.segment_id,
                    char_start=0,
                    char_end=5,
                    quote=seg.text[:5],
                    speaker_role=seg.speaker_role,
                    t_start=seg.t_start,
                    t_end=seg.t_end,
                    asr_mean_conf=0.95,
                    role="supports",
                )
            ],
            "provenance": Provenance(run_id="wf-h"),
        }
        res = apply_state_ops(deps.profile, make_ops(deps.profile, [("ADD", fid, seg.segment_id, payload, "extractor")]))
        return res.profile, ExtractionResult(
            task_id=task.task_id,
            ops_applied=len(res.applied),
            new_fact_ids=res.touched,
            usage={"requests": 2, "input_tokens": 1000, "output_tokens": 200},
        )

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
    assert store.saves == res.outcome.turns + 1 and res.state["subagent_usage"]["requests"] >= 2 * len(
        calls
    )  # per turn, plus the finished one
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


async def test_resume_continues_the_runs_budgets_and_wall_clock():
    """A retry resumes the run, not a new one: requests, tokens and wall clock carry over (§13.6)."""
    calls: list[str] = []
    whole = await run_agent(inputs(), services=fake_services(calls), checkpoints=InMemoryCheckpointStore())
    calls = []
    store = InMemoryCheckpointStore()
    beats = {"n": 0}

    def heartbeat(_):
        beats["n"] += 1
        if beats["n"] == 9:
            raise RuntimeError("worker lost")

    with pytest.raises(RuntimeError):
        await run_agent(inputs(), services=fake_services(calls), checkpoints=store, heartbeat=heartbeat)
    cp = store.data["wf-h"]
    first_requests = cp.harness_state["orchestrator_usage"]["requests"]
    first_elapsed = cp.harness_state["elapsed_s"]
    assert first_requests >= 1
    res = await run_agent(inputs(), services=fake_services(calls), checkpoints=store)
    assert not res.outcome.partial
    # The resumed attempt's figures are the whole run's, not the second attempt's alone.
    assert res.outcome.usage["orchestrator_requests"] == whole.outcome.usage["orchestrator_requests"]
    assert res.outcome.usage["tool_calls"] == whole.outcome.usage["tool_calls"]
    assert res.state["orchestrator_usage"]["requests"] == whole.outcome.usage["orchestrator_requests"] > first_requests
    assert res.state["elapsed_s"] >= first_elapsed and res.outcome.usage["wall_clock_s"] >= first_elapsed
    # A budget the whole run exceeds ends the resumed run as partial, however little the retry itself spent.
    store = InMemoryCheckpointStore()
    beats["n"] = 0
    limit = int(whole.outcome.usage["orchestrator_requests"]) - 2
    with pytest.raises(RuntimeError):
        await run_agent(
            inputs(budgets=Budgets(orchestrator_requests=limit)), services=fake_services([]), checkpoints=store, heartbeat=heartbeat
        )
    res = await run_agent(inputs(budgets=Budgets(orchestrator_requests=limit)), services=fake_services([]), checkpoints=store)
    assert res.outcome.partial and "request_limit" in (res.outcome.reason or "")
    assert res.outcome.usage["orchestrator_requests"] >= limit


async def test_usd_cap_counts_every_sub_agent_even_without_a_reported_cost():
    """§13.6: the cost cap covers all models. A sub-agent that reports tokens but no cost is priced from its role's model."""
    calls: list[str] = []
    services = fake_services(calls)
    verify_calls = {"n": 0}

    async def verify(profile, ids, deps):
        verify_calls["n"] += 1
        return ServiceResult(data={"verified": len(ids)}, usage={"requests": 1, "input_tokens": 40_000, "output_tokens": 2_000})

    async def followups(profile, deps):
        return ServiceResult(data=[], usage={"requests": 1, "input_tokens": 10_000, "output_tokens": 500})

    services.verify, services.followups = verify, followups
    pins = VersionPins(verifier_model="openai:gpt-5", frontier_model="test")  # the orchestrator stays on the scripted policy
    res = await run_agent(inputs(pins=pins), services=services)
    assert not res.outcome.partial, res.outcome.reason
    per_verify = llm.usage_cost_usd("openai:gpt-5", input_tokens=40_000, output_tokens=2_000)
    assert per_verify > 0 and res.state["subagent_usage"]["cost_usd"] == pytest.approx(per_verify * verify_calls["n"], rel=1e-6)
    assert res.outcome.usage["usd"] == pytest.approx(
        per_verify * verify_calls["n"], rel=1e-6
    )  # the frontier role is a free test model here
    # The same run with a cap below that spend ends partial on the cost budget.
    cap = per_verify * 3
    res = await run_agent(inputs(pins=pins, budgets=Budgets(usd_cap=cap)), services=services)
    assert res.outcome.partial and "cost budget" in (res.outcome.reason or "") and res.outcome.usage["usd"] > cap


async def test_retry_after_the_loop_finished_returns_the_same_result_without_running_again():
    """The attempt died after the loop ended but before its caller recorded the outcome: the retry hands back
    the finished result from the checkpoint, with no model call and no re-derived profile."""
    calls: list[str] = []
    store = InMemoryCheckpointStore()
    first = await run_agent(inputs(), services=fake_services(calls), checkpoints=store)
    n_calls, n_saves = len(calls), store.saves
    assert store.data["wf-h"].harness_state["finished"] is True
    again = await run_agent(inputs(), services=fake_services(calls), checkpoints=store)
    assert len(calls) == n_calls and store.saves == n_saves  # nothing ran, nothing was checkpointed
    assert again.outcome == first.outcome and again.summary == first.summary
    assert again.profile.facts.keys() == first.profile.facts.keys() and [o.seq for o in again.profile.ops_log] == [
        o.seq for o in first.profile.ops_log
    ]
    assert [t.status for t in again.plan.tasks] == [t.status for t in first.plan.tasks]


class WorkerDied(BaseException):
    """A process death: not an Exception, so no tool guard turns it into a tool error."""


async def test_crash_after_a_tools_side_effect_does_not_double_apply_ops():
    """The worker dies after extraction changed the profile but before the next checkpoint: the retry
    re-runs that one tool call on the checkpointed profile, so nothing is applied twice (§13.2)."""
    calls: list[str] = []
    store = InMemoryCheckpointStore()
    seen = {"n": 0}

    async def sink(profile):
        seen["n"] += 1
        if seen["n"] == 4:
            raise WorkerDied()

    with pytest.raises(WorkerDied):
        await run_agent(inputs(), services=fake_services(calls), checkpoints=store, on_profile=sink)
    interrupted = calls[-1]
    res = await run_agent(inputs(), services=fake_services(calls), checkpoints=store)
    assert not res.outcome.partial
    assert calls.count(interrupted) == 2 and all(calls.count(t) == 1 for t in set(calls) - {interrupted})
    assert check_profile(res.profile) == []
    seqs = [op.seq for op in res.profile.ops_log]
    assert seqs == sorted(set(seqs))
    adds = [(op.op, op.fact_id) for op in res.profile.ops_log if op.op == "ADD"]
    assert len(adds) == len(set(adds)), "an ADD was applied twice"
    assert all(t.attempts == 1 for t in res.plan.tasks if t.status == "done")


async def test_reextraction_budget_counts_tasks_added_for_the_same_group():
    """§13.6: at most two re-extractions per slot group, whether the agent re-runs a task or adds new ones."""
    turn = {"n": 0}
    added: list[str] = []

    def model(messages, info):
        out = info.model_request_parameters.output_object
        if out is not None and "order" in out.json_schema.get("properties", {}):
            return ModelResponse(parts=[TextPart(content=json.dumps({"order": [], "rationale": "test plan"}))])
        turn["n"] += 1
        n = turn["n"]
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if n == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name="make_plan", args={})])
        if n == 2:
            return ModelResponse(parts=[ToolCallPart(tool_name="extract_slot_group", args={"task_id": "income:client"})])
        if n <= 8 and n % 2 == 1:  # add another task for the same group and person …
            tid = f"income:client:again{n}"
            added.append(tid)
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="update_plan",
                        args={
                            "action": "add",
                            "task_id": tid,
                            "reason": "look again",
                            "slot_group": "income",
                            "instance": "client",
                            "segment_ids": [T.segments[3].segment_id],
                        },
                    )
                ]
            )
        if n <= 8:  # … and extract it
            return ModelResponse(parts=[ToolCallPart(tool_name="extract_slot_group", args={"task_id": added[-1]})])
        plan = next(
            p.content for p in reversed(returns) if p.tool_name in ("make_plan", "view_plan", "update_plan") and isinstance(p.content, dict)
        )
        todo = [t["task_id"] for t in plan["tasks"] if t["status"] == "todo"]
        if todo:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="update_plan", args={"action": "skip", "task_id": tid, "reason": "test skip"}, tool_call_id=f"c{i}"
                    )
                    for i, tid in enumerate(todo)
                ]
            )
        return ModelResponse(parts=[TextPart(content=json.dumps({"summary": "done", "unresolved": []}))])

    calls: list[str] = []
    pins = VersionPins(frontier_model=llm.register_local_model("reextract", FunctionModel(model)))
    try:
        res = await run_agent(inputs(pins=pins), services=fake_services(calls))
    finally:
        llm.unregister_local_model("reextract")
    assert res.summary == "done"
    # Three extractions of income#client ran (one plus two re-extractions); the fourth was refused.
    assert calls.count("income:client") == 1 and len([c for c in calls if c.startswith("income:client")]) == 3
    assert res.state["extraction_attempts"]["income#client"] == 3
    refused = [e for e in res.state["events"] if e["tool"] == "extract_slot_group" and e["note"] == "failed"]
    assert len(refused) == 1
    refused_task = next(t for t in res.plan.tasks if t.task_id == added[-1])
    assert refused_task.status == "skipped" and refused_task.attempts == 0  # never ran; the script then skipped it with its own reason


async def test_compaction_twice_keeps_what_the_first_summary_held():
    """§13.5: compaction may summarise, never lose. The second compaction drops the first summary's turn, so
    its content must be carried into the new summary."""
    from types import SimpleNamespace

    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from advisor_intel.harness.budget import BudgetGuard
    from advisor_intel.harness.compaction import compact_history
    from advisor_intel.harness.deps import HarnessDeps
    from advisor_intel.harness.loop import LoopDetector
    from advisor_intel.harness.state import HarnessState
    from advisor_intel.schema.slots import load_catalog

    inp = inputs()
    state = HarnessState(notes=["remember the spouse's RA"])
    deps = HarnessDeps(
        inputs=inp,
        catalog=load_catalog(inp.pins.schema_version),
        services=fake_services([]),
        state=state,
        budget=BudgetGuard(budgets=Budgets(), model_name="test"),
        loop=LoopDetector(),
        profile=inp.profile,
        context_window_tokens=400,
    )
    ctx = SimpleNamespace(deps=deps)

    def turns(lo: int, hi: int) -> list:
        out = []
        for i in range(lo, hi):
            out.append(ModelResponse(parts=[ToolCallPart(tool_name="get_segments", args={"segment_ids": [f"s{i}"]}, tool_call_id=f"c{i}")]))
            out.append(
                ModelRequest(
                    parts=[ToolReturnPart(tool_name="get_segments", content=f"FACT-{i} said at length " * 6, tool_call_id=f"c{i}")]
                )
            )
        return out

    msgs = [ModelRequest(parts=[UserPromptPart(content="the brief " * 40)]), *turns(0, 10)]
    once = await compact_history(ctx, msgs)
    assert len(once) < len(msgs) and once[0] is not None and "the brief" in str(once[0].parts[0].content)
    texts = [str(p.content) for m in once for p in m.parts if isinstance(p, UserPromptPart)]
    assert any("FACT-0" in t and "remember the spouse's RA" in t for t in texts)
    twice = await compact_history(ctx, [*once, *turns(10, 20)])
    texts = [str(p.content) for m in twice for p in m.parts if isinstance(p, UserPromptPart)]
    assert sum("Summary of earlier turns" in t for t in texts) == 1  # one summary, not a stack of them
    assert any("FACT-0" in t for t in texts), "the first compaction's facts were lost"
    assert any("FACT-12" in t for t in texts)
    assert twice[0] is once[0] or str(twice[0].parts[0].content) == str(once[0].parts[0].content)  # the brief is kept
    # The last six turns stay verbatim.
    kept = [p for m in twice for p in m.parts if isinstance(p, ToolReturnPart)]
    assert [p.tool_call_id for p in kept][-6:] == [f"c{i}" for i in range(14, 20)]


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
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="update_plan", args={"action": "skip", "task_id": tid, "reason": "test skip"}, tool_call_id=f"c{i}"
                    )
                    for i, tid in enumerate(todo)
                ]
            )
        return ModelResponse(parts=[TextPart(content=json.dumps({"summary": "skipped everything", "unresolved": []}))])

    pins = VersionPins(frontier_model=llm.register_local_model("loopy", FunctionModel(model)))
    res = await run_agent(inputs(pins=pins), services=fake_services([]))
    events = res.state["events"]
    blocked = [e for e in events if e["tool"] == "view_plan" and e["note"] == "repeated"]
    assert len(blocked) == 1  # the third identical call in ten is blocked
    assert res.summary == "skipped everything"
    assert all(t.status == "skipped" for t in res.plan.tasks)
