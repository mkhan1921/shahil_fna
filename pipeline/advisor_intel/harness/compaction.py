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


SUMMARY_HEADER = "Summary of earlier turns (compacted):"


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        return "\n".join(_text(c) for c in content)
    return json.dumps(content, default=str)


def earlier_summaries(turns: list[list[ModelMessage]]) -> list[str]:
    """The summaries an earlier compaction wrote into these turns: nothing in them may be lost again."""
    out = []
    for turn in turns:
        for m in turn:
            if isinstance(m, ModelRequest):
                for p in m.parts:
                    if isinstance(p, UserPromptPart) and _text(p.content).startswith(SUMMARY_HEADER):
                        body = _text(p.content)[len(SUMMARY_HEADER) :].split("\n\nYour notes:", 1)[0].strip()
                        if body:
                            out.append(body)
    return out


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
                elif isinstance(p, UserPromptPart) and isinstance(m, ModelRequest):
                    text = _text(p.content)
                    if not text.startswith(SUMMARY_HEADER):  # earlier summaries are carried whole, see earlier_summaries
                        lines.append(f"- user: {text[:300]}")
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
    # A second compaction drops the first one's summary with the turns around it: carry it into the new
    # summary whole, so a fact that survived one compaction survives the next.
    previous = earlier_summaries(dropped)
    text = digest(dropped)
    rm = llm.for_role("small", deps.pins)
    if not llm.is_stub(rm.name) and (text or previous):
        prompt = pinned("compaction", deps.pins.prompt_versions)
        earlier = "\n\n".join(f"Earlier summary (keep every fact, task and id in it):\n{s}" for s in previous)
        res = await summary_agent.run((earlier + "\n\nNew turns:\n" + text)[:60_000] if earlier else text[:60_000], model=rm.model,
                                      model_settings=rm.settings, instructions=prompt.text)
        deps.add_usage({"requests": res.usage.requests, "input_tokens": res.usage.input_tokens or 0, "output_tokens": res.usage.output_tokens or 0,
                        "cache_read_tokens": res.usage.cache_read_tokens or 0,
                        "cost_usd": llm.usage_cost_usd(rm.name, input_tokens=res.usage.input_tokens or 0, output_tokens=res.usage.output_tokens or 0,
                                                       cache_read_tokens=res.usage.cache_read_tokens or 0,
                                                       cache_write_tokens=res.usage.cache_write_tokens or 0)})
        text = res.output
    else:
        text = "\n".join([*previous, text]) if previous else text
    notes = "\n".join(f"- {n}" for n in deps.state.notes)
    summary = ModelRequest(parts=[UserPromptPart(content=f"{SUMMARY_HEADER}\n{text}\n\nYour notes:\n{notes or '(none)'}\n"
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
