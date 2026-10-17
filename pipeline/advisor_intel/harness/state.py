"""Harness state that survives a Temporal retry: checkpointed after every turn (spec §13.2)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..models.extraction import Plan
from ..models.profile import FollowUp


class SubagentUsage(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, d: dict[str, Any]) -> None:
        for k in ("requests", "input_tokens", "output_tokens", "cache_read_tokens"):
            setattr(self, k, getattr(self, k) + int(d.get(k, 0) or 0))
        self.cost_usd += float(d.get("cost_usd", 0.0) or 0.0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ToolEvent(BaseModel):
    """One tool call as the policy scorer and the loop detector see it (§13.4, §13.7)."""

    turn: int
    tool: str
    args_hash: str
    ok: bool
    note: str = ""


class HarnessState(BaseModel):
    turn: int = 0
    plan: Plan | None = None
    notes: list[str] = []
    extraction_attempts: dict[str, int] = {}  # slot group → extract_slot_group runs
    verified_groups: list[str] = []
    tool_errors: dict[str, int] = {}
    disabled_tools: list[str] = []
    events: list[ToolEvent] = []
    subagent_usage: SubagentUsage = Field(default_factory=SubagentUsage)
    followups: list[FollowUp] = []
    sensitivity: list[dict[str, Any]] = []
    constraint_violations: list[dict[str, Any]] = []
    soft_budget_warned: bool = False
    profile_version: int = 0
    # The orchestrator's own usage (requests, tool calls, tokens) and the wall clock spent by earlier
    # attempts: a resumed attempt continues the run's budgets instead of getting fresh ones (§13.6).
    orchestrator_usage: dict[str, int] = {}
    elapsed_s: float = 0.0
    finished: bool = False
    summary: str = ""


ORCHESTRATOR_USAGE_FIELDS = ("requests", "tool_calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def usage_snapshot(usage: Any) -> dict[str, int]:
    """The orchestrator's ``RunUsage`` as plain counts, for the checkpoint."""
    return {k: int(getattr(usage, k, 0) or 0) for k in ORCHESTRATOR_USAGE_FIELDS}
