"""Context management (spec §13.5).

``compact_history`` is a Pydantic AI history processor. When the history passes 60% of the model's
context window it keeps the system prompt and brief, the latest ``view_plan`` and ``view_profile``
results, every ``note`` and the last six turns, and replaces the older turns with a summary from the
small model (a deterministic digest for stub pins). The database is the source of truth: after
compaction the agent re-reads plan and profile through tools.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart

from .. import llm
from ..prompts import pinned
from .deps import HarnessDeps

KEEP_TURNS = 6
THRESHOLD = 0.6
KEEP_TOOLS = ("view_plan", "view_profile")

summary_agent: Agent[None, str] = Agent(None, output_type=str, name="compaction")


def estimate_tokens(messages: list[ModelMessage]) -> int:
    chars = 0
    for m in messages:
        for p in m.parts:
            c = getattr(p, "content", None)
            if c is None and isinstance(p, ToolCallPart):
                c = p.args
            chars += len(c if isinstance(c, str) else json.dumps(c, default=str)) if c is not None else 0
    return chars // 4


def _turns(messages: list[ModelMessage]) -> tuple[list[ModelMessage], list[list[ModelMessage]]]:
    """The head (first request) and the turns: each a model response plus the request that answers it."""
    head = messages[:1]
    turns: list[list[ModelMessage]] = []
    for m in messages[1:]:
        if isinstance(m, ModelResponse) or not turns:
            turns.append([m])
        else:
            turns[-1].append(m)
    return head, turns


def _tool_names(turn: list[ModelMessage]) -> set[str]:
    return {p.tool_name for m in turn for p in m.parts if isinstance(p, (ToolCallPart, ToolReturnPart))}


def digest(turns: list[list[ModelMessage]]) -> str:
    lines = []
    for turn in turns:
        for m in turn:
            for p in m.parts:
                if isinstance(p, ToolCallPart):
                    lines.append(f"- called {p.tool_name}({json.dumps(p.args, default=str)[:160] if not isinstance(p.args, str) else p.args[:160]})")
                elif isinstance(p, ToolReturnPart):
                    c = p.content if isinstance(p.content, str) else json.dumps(p.content, default=str)
                    lines.append(f"  → {c[:200]}")
    return "\n".join(lines)


async def compact_history(ctx: RunContext[HarnessDeps], messages: list[ModelMessage]) -> list[ModelMessage]:
    deps = ctx.deps
    if estimate_tokens(messages) <= THRESHOLD * deps.context_window_tokens:
        return messages
    head, turns = _turns(messages)
    if len(turns) <= KEEP_TURNS:
        return messages
    recent = turns[-KEEP_TURNS:]
    older = turns[:-KEEP_TURNS]
    keep_idx: set[int] = set()
    for name in KEEP_TOOLS:
        for i in range(len(older) - 1, -1, -1):
            if name in _tool_names(older[i]):
                keep_idx.add(i)
                break
    for i, t in enumerate(older):
        if "note" in _tool_names(t):
            keep_idx.add(i)
    dropped = [t for i, t in enumerate(older) if i not in keep_idx]
    kept = [t for i, t in enumerate(older) if i in keep_idx]
    text = digest(dropped)
    rm = llm.for_role("small", deps.pins)
    if not llm.is_stub(rm.name) and text:
        prompt = pinned("compaction", deps.pins.prompt_versions)
        res = await summary_agent.run(text[:60_000], model=rm.model, model_settings=rm.settings, instructions=prompt.text)
        deps.add_usage({"requests": res.usage.requests, "input_tokens": res.usage.input_tokens or 0, "output_tokens": res.usage.output_tokens or 0})
        text = res.output
    notes = "\n".join(f"- {n}" for n in deps.state.notes)
    summary = ModelRequest(parts=[UserPromptPart(content=f"Summary of earlier turns (compacted):\n{text}\n\nYour notes:\n{notes or '(none)'}\n"
                                                          "Re-read the plan and profile with view_plan and view_profile before continuing.")])
    first = head[0]
    if isinstance(first, ModelRequest):
        first = replace(first, parts=list(first.parts))
    out: list[ModelMessage] = [first, summary]
    for t in kept + recent:
        out.extend(t)
    return out


def last_tool_result(messages: list[ModelMessage], tool: str) -> Any:
    for m in reversed(messages):
        for p in m.parts:
            if isinstance(p, ToolReturnPart) and p.tool_name == tool:
                return p.content
    return None
