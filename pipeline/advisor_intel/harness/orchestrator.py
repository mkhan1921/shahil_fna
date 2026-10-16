"""The orchestrator agent (spec §13.2): it only chooses what to do next. Its output is ``Finish``,
which the harness refuses while any todo task lacks a skip reason; values enter the profile only
through the extraction tool and the reducer."""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput, RunContext
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.exceptions import ModelRetry

from .compaction import compact_history
from .deps import HarnessDeps
from .tools import TOOLSETS


class Finish(BaseModel):
    summary: str = Field(description="what was extracted, what is uncertain, and what the adviser should check")
    unresolved: list[str] = Field(description="slot groups or fact ids left unresolved, each with a short reason")


orchestrator: Agent[HarnessDeps, Finish] = Agent(
    None,
    deps_type=HarnessDeps,
    output_type=NativeOutput(Finish),
    toolsets=TOOLSETS,
    capabilities=[ProcessHistory(compact_history)],
    name="orchestrator",
    retries={"tools": 5, "output": 3},
)


@orchestrator.output_validator
async def _finish_only_when_done(ctx: RunContext[HarnessDeps], output: Finish) -> Finish:
    plan = ctx.deps.state.plan
    if plan is None:
        raise ModelRetry("Call make_plan before finishing.")
    open_tasks = [t.task_id for t in plan.tasks if t.status in ("todo", "running") and not t.skip_reason]
    if open_tasks:
        raise ModelRetry(f"These tasks are still todo: {', '.join(open_tasks[:20])}. Run them, or skip each with update_plan and a reason.")
    return output
