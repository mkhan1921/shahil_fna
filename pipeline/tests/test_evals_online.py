"""Online scorers (§13.4, §14.4): the policy check, schema-valid, grounding-retry and budget rates, the trace readers,
the wrong-auto-accept monitor and the Braintrust scorer definitions."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)

from advisor_intel.evals import online as O


def plan(*tasks):
    """A plan view as make_plan returns it: (task_id, slot_group, depends_on)."""
    return {
        "rationale": "test",
        "tasks": [{"task_id": t, "slot_group": g, "status": "todo", "depends_on": list(d), "priority": 10} for t, g, d in tasks],
    }


PLAN = plan(("income:client", "income", ()), ("retirement:client", "retirement", ("income:client",)), ("risk_profile", "risk_profile", ()))


def call(tool, ok=True, result=None, **args):
    return O.ToolCall(tool=tool, args=args, ok=ok, result=result)


def good_calls():
    return [
        call("make_plan", result=PLAN),
        call("extract_slot_group", task_id="income:client", result={"tier_a_gaps": []}),
        call("verify_facts", slot_group="income"),
        call("extract_slot_group", task_id="retirement:client", result={"tier_a_gaps": []}),
        call("verify_facts", slot_group="retirement"),
        call("extract_slot_group", task_id="risk_profile", result={"tier_a_gaps": []}),
        call("verify_facts", slot_group="risk_profile"),
        call("check_constraints"),
        call("run_sensitivity"),
        call("draft_followups"),
    ]


def rules(calls, **kw):
    rep = O.check_policy(O.RunTrace(tool_calls=calls, **kw))
    return [v.rule for v in rep.violations], rep


def test_the_expected_behaviour_passes():
    found, rep = rules(good_calls(), finished=True)
    assert found == [] and rep.passed and rep.score == 1.0 and set(rep.checked) == set(O.RULES) - {"gap_not_followed_up"}


def test_make_plan_first_and_a_resumed_run():
    calls = good_calls()
    assert rules([calls[1], *calls])[0] == ["make_plan_first"]
    resumed = [call("view_plan", result=PLAN), *calls[1:]]
    assert rules(resumed)[0] == []  # a resumed run re-reads the plan it already made
    assert rules([])[0] == ["make_plan_first", "finish_sequence"]


def test_tier_a_first_and_dependencies():
    calls = good_calls()
    early = [
        calls[0],
        call("extract_slot_group", task_id="risk_profile"),
        call("verify_facts", slot_group="risk_profile"),
        *calls[1:5],
        *calls[7:],
    ]
    found, rep = rules(early)
    assert found == ["tier_a_first"] and "income:client" in rep.violations[0].detail
    # a tier B task that a tier A task depends on may run first
    dep_plan = plan(("risk_profile", "risk_profile", ()), ("income:client", "income", ("risk_profile",)))
    ok = [
        call("make_plan", result=dep_plan),
        call("extract_slot_group", task_id="risk_profile"),
        call("verify_facts", slot_group="risk_profile"),
        call("extract_slot_group", task_id="income:client"),
        call("verify_facts", slot_group="income"),
        *calls[7:],
    ]
    assert rules(ok)[0] == []
    out_of_order = [
        calls[0],
        call(
            "extract_slot_group",
            ok=False,
            result="Task retirement:client depends on income:client: run those first.",
            task_id="retirement:client",
        ),
        *calls[1:],
    ]
    assert rules(out_of_order)[0] == ["dependency_order"]


def test_verify_after_each_group():
    calls = good_calls()
    skipped = [c for i, c in enumerate(calls) if i != 2]  # never verified income before moving on
    found, rep = rules(skipped)
    assert found == ["verify_after_group"] and "moved on to retirement before verify_facts on income" in rep.violations[0].detail
    late = calls[:5] + calls[5:6] + calls[7:]  # risk_profile extracted, then straight to the final checks
    assert rules(late)[0] == ["verify_after_group"]
    by_ids = calls[:2] + [call("verify_facts", fact_ids=["income.{p}.grossMonthly#client"])] + calls[3:]
    assert rules(by_ids)[0] == []
    partial = calls[:2]  # a budget-stopped run: a tier A group never verified is still a violation
    assert rules(partial, partial=True)[0] == ["verify_after_group"]
    assert "never verified" in rules(partial, partial=True)[1].violations[0].detail


def test_tier_a_gaps_need_a_follow_up_as_a_warning():
    calls = good_calls()
    gap = (
        calls[:1]
        + [call("extract_slot_group", task_id="income:client", result={"tier_a_gaps": ["income.{p}.grossMonthly#client"]})]
        + calls[2:]
    )
    found, rep = rules(gap)
    assert found == [] and [w.rule for w in rep.warnings] == ["gap_not_followed_up"]
    searched = gap[:3] + [call("search_transcript", query="salary")] + gap[3:]
    assert rules(searched)[1].warnings == []


def test_finish_sequence_tools_and_ops():
    calls = good_calls()
    assert rules(calls[:-1])[0] == ["finish_sequence"]  # no draft_followups
    swapped = calls[:7] + [calls[8], calls[7], calls[9]]
    assert rules(swapped)[0] == ["finish_sequence"]
    assert rules(calls, finished=False)[0] == ["finish_sequence"]
    assert rules(calls[:7], partial=True)[0] == []  # a partial run is not held to the finish sequence
    rogue = (
        calls[:3]
        + [
            call("delete_everything"),
            call("apply_state_ops", ops=[{"op": "ADD", "fact_id": "x#client"}, {"op": "CONFIRM", "fact_id": "y"}]),
        ]
        + calls[3:]
    )
    found, rep = rules(rogue)
    assert found == ["tool_outside_policy", "op_outside_policy"] and "ADD" in rep.violations[1].detail
    names_only = O.RunTrace(tool_calls=[O.ToolCall(tool=c.tool) for c in calls], args_known=False)
    rep = O.check_policy(names_only)
    assert rep.passed and "verify_after_group" not in rep.checked


def test_rates():
    run = O.RunTrace(
        agent_runs=[
            O.AgentRun(agent="extract_values"),
            O.AgentRun(agent="extract_fields", error="UnexpectedModelBehavior: Exceeded maximum retries (2) for output validation"),
            O.AgentRun(agent="aligner"),
            O.AgentRun(agent="aligner"),
            O.AgentRun(agent="orchestrator", error="UsageLimitExceeded: The next request would exceed the request_limit of 80"),
        ],
        tool_calls=[
            call(
                "extract_slot_group",
                task_id="income:client",
                result={
                    "new_fact_ids": ["a", "b"],
                    "dropped": [
                        "income.{p}.x#client: the aligner found no span stating or implying the value",
                        "y: no usable value after normalisation and bounds",
                    ],
                },
            )
        ],
        facts=[O.RunFact(fact_id=f"f{i}", slot_id="s", grounded=True) for i in range(7)]
        + [O.RunFact(fact_id="p", slot_id="s", source="prior_fna")],
        facts_known=True,
    )
    sv = O.schema_valid_rate(run)
    assert (sv.hits, sv.total) == (3, 4) and "extract_fields" in sv.detail[0]  # the budget stop is not a schema failure
    retry, drop = O.grounding_retry_rate(run)
    assert (retry.hits, retry.total, drop.hits) == (2, 8, 1) and retry.value == pytest.approx(0.25)
    assert O.budget_exceeded(run) == 1.0
    assert O.budget_exceeded(O.RunTrace(partial=True, reason="cost budget spent: $5.10 > $5.00")) == 1.0
    assert O.budget_exceeded(O.RunTrace(partial=False, agents_known=False)) == 0.0
    assert O.budget_exceeded(O.RunTrace(agents_known=False)) is None
    assert O.schema_valid_rate(O.RunTrace(agents_known=False)).value is None
    scores = [O.score_run(run), O.score_run(O.RunTrace(tool_calls=good_calls()))]
    summary = O.summarise_runs(scores)
    assert summary["n_runs"] == 2 and summary["budget_exceeded_rate"] == 0.5 and summary["policy_violation_rate"] == 0.5
    assert summary["policy_violations"]["finish_sequence"] == 1


def test_run_from_message_history():
    msgs = [
        ModelRequest.user_text_prompt("brief"),
        ModelResponse(parts=[ToolCallPart(tool_name="make_plan", args={}, tool_call_id="1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="make_plan", content=json.dumps(PLAN), tool_call_id="1")]),
        ModelResponse(parts=[ToolCallPart(tool_name="extract_slot_group", args={"task_id": "retirement:client"}, tool_call_id="2")]),
        ModelRequest(
            parts=[
                RetryPromptPart(tool_name="extract_slot_group", content="Task retirement:client depends on income:client", tool_call_id="2")
            ]
        ),
        ModelResponse(parts=[ToolCallPart(tool_name="extract_slot_group", args='{"task_id": "income:client"}', tool_call_id="3")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="extract_slot_group", content={"tier_a_gaps": []}, tool_call_id="3")]),
        ModelResponse(parts=[TextPart(content='{"summary": "done", "unresolved": []}')]),
    ]
    for source in (msgs, ModelMessagesTypeAdapter.dump_python(msgs, mode="json")):  # live messages, or the checkpointed JSON
        run = O.RunTrace.from_messages(source)
        assert [(c.tool, c.ok) for c in run.tool_calls] == [
            ("make_plan", True),
            ("extract_slot_group", False),
            ("extract_slot_group", True),
        ]
        assert run.finished is True and run.tool_calls[0].result == PLAN
        found, _ = rules(run.tool_calls, finished=True)
        assert found == ["dependency_order", "verify_after_group", "finish_sequence"]


def test_braintrust_spans_and_the_scorer_handlers():
    rows = [
        {
            "span_id": "r",
            "span_parents": [],
            "span_attributes": {"name": "run_agent", "type": "task"},
            "metadata": {"call_id": "c9"},
            "metrics": {"start": 0},
        },
        {
            "span_id": "a",
            "span_parents": ["r"],
            "span_attributes": {"name": "invoke_agent orchestrator", "type": "task"},
            "metrics": {"start": 1},
        },
        *[
            {
                "span_id": f"t{i}",
                "span_parents": ["a"],
                "span_attributes": {"name": f"execute_tool {c.tool}", "type": "tool"},
                "input": c.args,
                "output": c.result,
                "metrics": {"start": 2 + i, "end": 2.5 + i},
            }
            for i, c in enumerate(good_calls())
        ],
        {
            "span_id": "x",
            "span_parents": ["a"],
            "span_attributes": {"name": "invoke_agent extract_values"},
            "metadata": {"gen_ai.agent.name": "extract_values"},
            "error": "UnexpectedModelBehavior: Exceeded maximum retries (2) for output validation",
            "metrics": {"start": 3.1},
        },
    ]
    spans = O.spans_from_braintrust(rows)
    run = O.RunTrace.from_spans(spans)
    assert run.call_id == "c9" and [c.tool for c in run.tool_calls] == [c.tool for c in good_calls()]
    assert run.tool_calls[1].args == {"task_id": "income:client"} and O.check_policy(run).passed
    assert [a.agent for a in run.agent_runs] == ["orchestrator", "extract_values"] and O.schema_valid_rate(run).value == 0.5

    class Trace:  # Braintrust's Trace: spans fetched asynchronously
        async def get_spans(self, span_type=None, *, filters=None, include_scorers=False):
            return [SimpleNamespace(**r) for r in rows]

    out = O.policy_scorer(output={"outcome": {"partial": False}}, metadata={"call_id": "c9"}, trace=Trace())
    assert out == {"name": "policy", "score": 1.0, "metadata": {"violations": [], "checked": out["metadata"]["checked"]}}
    assert O.schema_valid_scorer(trace=Trace())["score"] == 0.5
    assert (
        O.budget_exceeded_scorer(
            output={"outcome": {"partial": True, "reason": "token budget spent: 3,000,000 > 2,750,000"}}, trace=Trace()
        )["score"]
        == 1.0
    )
    record = O.RunTrace(tool_calls=good_calls()[:1]).model_dump(mode="json")
    assert O.policy_scorer(output=record)["score"] == 0.0  # a root span that carries its run record
    assert O.policy_scorer(output="free text")["metadata"]["checked"] == ["make_plan_first", "finish_sequence", "tool_outside_policy"]
    assert O.wrong_auto_accept_scorer(output={"call_id": "c", "auto_accepted": ["a", "b"], "wrong": ["b"]})["score"] == 1.0
    assert O.wrong_auto_accept_scorer(output={"call_id": "c"}) is None


def test_scorers_register_as_braintrust_code_functions():
    import braintrust

    fns = O.register_online_scorers("advisor-intel-test", projects=braintrust.projects)
    assert [f.name for f in fns] == list(O.ONLINE_SCORERS) and all(f.type_ == "scorer" and f.parameters is O.OnlineScorerArgs for f in fns)
    assert fns[0].slug == "advisor-intel-policy" and fns[0].project.name == "advisor-intel-test" and fns[0].handler is O.policy_scorer


def test_spot_checks_and_the_auto_accept_monitor(tmp_path):
    events = [
        {"fact_id": "a", "action": "accept"},
        {"fact_id": "b", "action": "edit"},
        {"fact_id": "c", "action": "reject"},
        {"fact_id": "z", "action": "reject"},
    ]
    sc = O.spot_check("c1", ["a", "b", "c"], events)
    assert sc.wrong == ["b", "c"] and sc.any_wrong  # z was not auto-accepted
    lenient = O.spot_check("c1", ["a", "b", "c"], events, is_correct=lambda e: True if e["action"] == "edit" else None)
    assert lenient.wrong == ["c"]
    alerts = []
    mon = O.AutoAcceptMonitor(alpha=0.1, window=100, path=tmp_path / "checks.jsonl", on_alert=alerts.append)
    for i in range(95):
        st = mon.record(O.SpotCheck(call_id=f"ok{i}", auto_accepted=["a"]))
    for i in range(10):
        st = mon.record(O.SpotCheck(call_id=f"bad{i}", auto_accepted=["a"], wrong=["a"]))
    assert st.n_calls == 100 and st.n_wrong == 10 and not st.alert and st.auto_accept_enabled  # 10 of the last 100: exactly α
    st = mon.record(O.SpotCheck(call_id="bad10", auto_accepted=["a"], wrong=["a"]))
    assert st.alert and not st.auto_accept_enabled and st.rate == pytest.approx(0.11) and len(alerts) == 1
    assert "auto-accept is off" in st.message and not O.FileSwitch(tmp_path / "auto_accept.json").enabled()
    assert mon.record(O.SpotCheck(call_id="bad10", auto_accepted=["a"], wrong=["a"])).n_calls == 100 and len(alerts) == 1  # idempotent
    O.FileSwitch(tmp_path / "auto_accept.json").enable()
    early = O.AutoAcceptMonitor(path=tmp_path / "early.jsonl")
    for i in range(11):
        st = early.record(O.SpotCheck(call_id=f"w{i}", wrong=["x"], auto_accepted=["x"]))
    assert st.alert and st.n_calls == 11  # eleven wrong calls already put any 100-call window above α
    assert O.wrong_auto_accept_score(sc) == 1.0 and O.wrong_auto_accept_score(O.SpotCheck(call_id="x")) == 0.0


async def test_the_reference_run_of_the_real_harness_passes(tmp_path, monkeypatch):
    """The harness's deterministic policy (stub pins) is the reference trace: its OpenTelemetry spans pass every rule."""
    pipeline = pytest.importorskip("advisor_intel.pipeline")
    from advisor_intel.evals.datasets import DatasetRecord, LocalBlobStore, Stage12Cache
    from advisor_intel.models.runs import VersionPins
    from advisor_intel.testing.calls import SAMPLE_CALL
    from advisor_intel.testing.transcripts import ingested_transcript

    from tests.evals_fakes import small_forecast

    small_forecast(monkeypatch)
    t = await ingested_transcript(SAMPLE_CALL, call_id="call-online")
    rec = DatasetRecord(
        id="call-online", input={"call_id": "call-online", "transcript": t.model_dump(mode="json")}, metadata={"dataset": "gold-calls"}
    )
    cached = Stage12Cache(LocalBlobStore(tmp_path)).load_call(rec, VersionPins())
    with O.captured_spans() as spans:
        res = await pipeline.process_cached_call(cached, VersionPins())
    run = O.RunTrace.from_result(res, spans=spans())
    assert run.tool_calls[0].tool == "make_plan" and run.tool_calls[1].args.get("task_id")
    rep = O.check_policy(run)
    assert rep.passed, rep.violations
    scores = O.score_run(run)
    assert scores.schema_valid_rate == 1.0 and scores.budget_exceeded == 0.0 and scores.policy == 1.0 and run.facts_known
    names_only = O.score_run(O.RunTrace.from_result(res))
    assert names_only.policy == 1.0 and names_only.schema_valid_rate is None
    from pydantic_ai import Agent

    assert Agent._instrument_default is False  # the capture restored the process default
