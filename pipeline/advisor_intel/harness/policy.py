"""A deterministic orchestrator for runs without a frontier model (stub pins, offline tests).

It follows the expected behaviour of §13.4 exactly, through the same tools and the same harness
code path as the model: make_plan first; each todo task in plan order (tier A and dependencies
first) followed by verify_facts on its group; then check_constraints, run_sensitivity and
draft_followups; then Finish. It also serves as the reference trace for the policy scorer.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

FINAL_SEQUENCE = ("check_constraints", "run_sensitivity", "draft_followups")


def _calls(messages: list[ModelMessage]) -> list[ToolCallPart]:
    return [p for m in messages if isinstance(m, ModelResponse) for p in m.parts if isinstance(p, ToolCallPart)]


def _latest_plan(messages: list[ModelMessage]) -> dict[str, Any] | None:
    for m in reversed(messages):
        for p in m.parts:
            if isinstance(p, ToolReturnPart) and p.tool_name in ("make_plan", "view_plan", "update_plan"):
                c = p.content
                if isinstance(c, str):
                    try:
                        c = json.loads(c)
                    except ValueError:
                        continue
                if isinstance(c, dict) and "tasks" in c:
                    return c
    return None


def _args(p: ToolCallPart) -> dict[str, Any]:
    return p.args if isinstance(p.args, dict) else (json.loads(p.args) if p.args else {})


def policy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    calls = _calls(messages)
    names = [c.tool_name for c in calls]
    if "make_plan" not in names:
        return ModelResponse(parts=[ToolCallPart(tool_name="make_plan", args={})])
    plan = _latest_plan(messages) or {"tasks": []}
    extracted = [_args(c).get("task_id") for c in calls if c.tool_name == "extract_slot_group"]
    verified = [_args(c).get("slot_group") for c in calls if c.tool_name == "verify_facts"]
    groups = {t["task_id"]: t["slot_group"] for t in plan["tasks"]}
    if extracted:
        last = extracted[-1]
        g = groups.get(last)
        if g and verified.count(g) < extracted.count(last) and names[-1] == "extract_slot_group":
            return ModelResponse(parts=[ToolCallPart(tool_name="verify_facts", args={"slot_group": g})])
    for t in plan["tasks"]:
        if t["status"] == "todo" and t["task_id"] not in extracted:
            return ModelResponse(parts=[ToolCallPart(tool_name="extract_slot_group", args={"task_id": t["task_id"]})])
    for name in FINAL_SEQUENCE:
        if name not in names:
            return ModelResponse(parts=[ToolCallPart(tool_name=name, args={})])
    done = sum(1 for t in plan["tasks"] if t["task_id"] in extracted)
    skipped = [t["task_id"] for t in plan["tasks"] if t["status"] == "skipped"]
    summary = {
        "summary": f"Extracted {done} tasks in plan order with verification after each slot group; ran constraints, sensitivity and follow-ups.",
        "unresolved": [f"{t}: skipped (no indexed segments)" for t in skipped],
    }
    return ModelResponse(parts=[TextPart(content=json.dumps(summary))])


def policy_model() -> FunctionModel:
    return FunctionModel(policy, model_name="policy")
