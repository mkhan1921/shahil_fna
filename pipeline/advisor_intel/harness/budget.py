"""Budgets and limits (spec §13.6): requests, tool calls, tokens, cost and wall clock.

Pydantic AI's ``UsageLimits`` caps the orchestrator's own requests, tool calls and tokens; the
``BudgetGuard`` adds the sub-agents' usage (which those limits cannot see), the USD cap across all
models and the wall clock (soft: tell the agent to wrap up; hard: stop). The Temporal
``start_to_close_timeout`` is the last line on wall clock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.usage import UsageLimits

from .. import llm
from ..config import Budgets
from .state import SubagentUsage


class BudgetExceeded(Exception):
    """The run spent a budget; it ends as AGENT_PARTIAL and still goes to review (§13.6)."""


def usage_limits(b: Budgets) -> UsageLimits:
    return UsageLimits(request_limit=b.orchestrator_requests, tool_calls_limit=b.tool_calls, total_tokens_limit=b.total_tokens)


@dataclass
class BudgetGuard:
    budgets: Budgets
    model_name: str
    started: float = field(default_factory=time.monotonic)
    soft_hit: bool = False

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def orchestrator_cost(self, usage: Any) -> float:
        reported = getattr(usage, "cost", None)
        return llm.usage_cost_usd(self.model_name, input_tokens=getattr(usage, "input_tokens", 0) or 0, output_tokens=getattr(usage, "output_tokens", 0) or 0,
                                  cache_read_tokens=getattr(usage, "cache_read_tokens", 0) or 0, reported=float(reported) if reported else None)

    def check(self, run_usage: Any, sub: SubagentUsage) -> None:
        tokens = (getattr(run_usage, "input_tokens", 0) or 0) + (getattr(run_usage, "output_tokens", 0) or 0) + sub.total_tokens
        if tokens > self.budgets.total_tokens:
            raise BudgetExceeded(f"token budget spent: {tokens:,} > {self.budgets.total_tokens:,}")
        cost = self.orchestrator_cost(run_usage) + sub.cost_usd
        if cost > self.budgets.usd_cap:
            raise BudgetExceeded(f"cost budget spent: ${cost:.2f} > ${self.budgets.usd_cap:.2f}")
        t = self.elapsed()
        if t > self.budgets.wall_clock_hard_s:
            raise BudgetExceeded(f"wall clock spent: {t:.0f}s > {self.budgets.wall_clock_hard_s}s")
        if t > self.budgets.wall_clock_soft_s:
            self.soft_hit = True

    def spent(self, run_usage: Any, sub: SubagentUsage) -> dict[str, float]:
        return {
            "orchestrator_requests": float(getattr(run_usage, "requests", 0) or 0),
            "tool_calls": float(getattr(run_usage, "tool_calls", 0) or 0),
            "tokens": float((getattr(run_usage, "input_tokens", 0) or 0) + (getattr(run_usage, "output_tokens", 0) or 0) + sub.total_tokens),
            "usd": round(self.orchestrator_cost(run_usage) + sub.cost_usd, 4),
            "wall_clock_s": round(self.elapsed(), 1),
        }
