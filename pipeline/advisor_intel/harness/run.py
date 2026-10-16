"""``run_agent``: the whole agent loop as one heartbeating activity body (spec §13.2).

The harness checkpoints the message history and its own state after every turn, so a Temporal
retry resumes from the last turn instead of starting over. Budget exhaustion (Pydantic AI's
``UsageLimits`` or the ``BudgetGuard``) ends the run as partial: it still goes to review with the
unfinished groups marked (§13.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage

from .. import llm
from ..models.extraction import Plan
from ..models.profile import FollowUp, Profile
from ..models.runs import AgentOutcome
from ..prompts import pinned
from ..schema.slots import load_catalog
from .budget import BudgetExceeded, BudgetGuard, usage_limits
from .checkpoint import CheckpointStore, InMemoryCheckpointStore, dump_messages, load_messages
from .deps import AgentInputs, HarnessDeps, HarnessServices, ProfileSink
from .loop import LoopDetector
from .orchestrator import Finish, orchestrator
from .policy import policy_model
from .state import HarnessState

RESUME_PROMPT = "You were interrupted and are resuming. Re-read the plan with view_plan and continue where you left off."
SOFT_BUDGET_NOTE = "The time budget is nearly spent: finish the remaining tier A tasks, skip the rest with a reason, then run the final checks and finish."


class AgentResult(BaseModel):
    outcome: AgentOutcome
    profile: Profile
    plan: Plan | None = None
    notes: list[str] = []
    followups: list[FollowUp] = []
    sensitivity: list[dict[str, Any]] = []
    summary: str = ""
    state: dict[str, Any] = {}


@dataclass
class _Run:
    deps: HarnessDeps
    messages: list[ModelMessage] = field(default_factory=list)


def _merge_registry(inputs: AgentInputs) -> Profile:
    ents = {**inputs.discourse.entities, **inputs.profile.entities}
    for eid, e in inputs.discourse.entities.items():  # discourse knows aliases and mentions the prior registry lacked
        if eid in ents and ents[eid] is not e:
            base = ents[eid]
            ents[eid] = base.model_copy(update={"aliases": sorted(set(base.aliases) | set(e.aliases)), "first_mention": base.first_mention or e.first_mention,
                                                "crm_match": base.crm_match or e.crm_match})
    return inputs.profile.model_copy(update={"entities": ents})


async def run_agent(
    inputs: AgentInputs,
    *,
    services: HarnessServices,
    checkpoints: CheckpointStore | None = None,
    heartbeat: Callable[[Any], None] | None = None,
    on_profile: ProfileSink | None = None,
    checkpoint_profile: bool = True,
) -> AgentResult:
    """Run the orchestrator to Finish (or to a spent budget). With ``checkpoint_profile=False`` the
    caller persists the profile through ``on_profile`` and passes the latest one in ``inputs`` on resume."""
    checkpoints = checkpoints or InMemoryCheckpointStore()
    catalog = load_catalog(inputs.pins.schema_version)
    budgets = inputs.budgets.scaled(inputs.call_minutes or inputs.transcript.duration / 60)
    inputs = inputs.model_copy(update={"budgets": budgets})
    rm = llm.for_role("frontier", inputs.pins, thinking=True)
    model = policy_model() if llm.is_stub(rm.name) else rm.model
    state = HarnessState()
    history: list[ModelMessage] | None = None
    profile = _merge_registry(inputs)
    cp = await checkpoints.load(inputs.run_id)
    if cp is not None:
        state = HarnessState.model_validate(cp.harness_state)
        history = load_messages(cp.messages)
        if "profile" in cp.harness_state.get("_extra", {}):
            profile = Profile.model_validate(cp.harness_state["_extra"]["profile"])
    loop = LoopDetector([e.args_hash for e in state.events[-10:]])
    deps = HarnessDeps(inputs=inputs, catalog=catalog, services=services, state=state, budget=BudgetGuard(budgets=budgets, model_name=rm.name),
                       loop=loop, profile=profile, on_profile=on_profile)
    instructions = pinned("orchestrator", inputs.pins.prompt_versions).text
    # On resume the history may end with tool calls that never ran: run them first, then tell the agent.
    prompt = None if history else deps.brief()
    outcome = AgentOutcome()
    finish: Finish | None = None
    try:
        async with orchestrator.iter(prompt, deps=deps, message_history=history, usage_limits=usage_limits(budgets), model=model,
                                     model_settings=rm.settings if model is rm.model else None, instructions=instructions) as run:
            if history:
                run.enqueue(RESUME_PROMPT)
            async for _node in run:
                # The previous node has run: checkpoint first, so a crash from here on loses nothing done.
                state.turn += 1
                extra = {"profile": deps.profile.model_dump(mode="json")} if checkpoint_profile else {}
                await checkpoints.save(inputs.run_id, state.turn, dump_messages(run.all_messages()), {**state.model_dump(mode="json"), "_extra": extra})
                if heartbeat is not None:
                    heartbeat({"turn": state.turn, "profile_version": deps.profile.version})
                deps.budget.check(run.usage, state.subagent_usage)
                if deps.budget.soft_hit and not state.soft_budget_warned:
                    state.soft_budget_warned = True
                    state.notes.append(SOFT_BUDGET_NOTE)
                    run.enqueue(SOFT_BUDGET_NOTE)
            finish = run.result.output if run.result else None
            outcome.usage = deps.budget.spent(run.usage, state.subagent_usage)
    except (UsageLimitExceeded, BudgetExceeded) as e:
        outcome.partial = True
        outcome.reason = str(e)
        outcome.usage = {"usd": round(state.subagent_usage.cost_usd, 4), "wall_clock_s": round(deps.budget.elapsed(), 1)}
    plan = state.plan
    if plan is not None:
        unfinished = sorted({t.slot_group for t in plan.tasks if t.status in ("todo", "running")})
        if unfinished:
            outcome.unfinished_groups = unfinished
            outcome.partial = outcome.partial or not finish
            for t in plan.tasks:
                if t.status in ("todo", "running"):
                    t.status, t.skip_reason = "skipped", t.skip_reason or (f"not reached: {outcome.reason}" if outcome.reason else "not reached")
    if services.derive is not None:
        await deps.set_profile(await services.derive(deps.profile, deps))
    outcome.turns = state.turn
    state.finished = True
    state.summary = finish.summary if finish else (outcome.reason or "")
    return AgentResult(outcome=outcome, profile=deps.profile, plan=plan, notes=state.notes, followups=state.followups, sensitivity=state.sensitivity,
                       summary=state.summary, state=state.model_dump(mode="json"))
