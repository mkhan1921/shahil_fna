"""Shared plumbing for the Stage 5–6 sub-agents: model resolution, guarded runs, usage, transcript excerpts.

Every sub-agent here returns a strict-friendly ``NativeOutput`` model (§4.4): all fields required, no
dict-valued fields, no recursion. Models come from ``llm.for_role``. A stub pin (``"test"``, see
``llm.is_stub``) would only answer schema-valid filler, so the pass is skipped: an offline stand-in
returns the output type's empty value and the checks report "not run". ``"local:<name>"`` pins (registered
in-process models) run for real, and unit tests can also inject a ``FunctionModel`` with
``agent.override``. Provider failures and outputs that still fail validation after the retries degrade
the same way: the stage carries on and the fact keeps NaN features (§13.7: the task is marked failed and
the agent is told).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic_ai import Agent
from pydantic_ai.exceptions import FallbackExceptionGroup, ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

from .. import llm
from ..models.runs import VersionPins
from ..models.transcript import Segment

log = logging.getLogger(__name__)

T = TypeVar("T")
LOW_ASR = 0.6  # §5.2 step 7: words below this confidence are marked {word?0.42}


@dataclass
class Usage:
    """Sub-agent usage, returned with each tool result and added to the harness budget (§13.3)."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failures: int = 0

    def add(self, run_usage: Any) -> None:
        self.requests += int(getattr(run_usage, "requests", 0) or 0)
        self.input_tokens += int(getattr(run_usage, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(run_usage, "output_tokens", 0) or 0)

    def merge(self, other: Usage) -> Usage:
        return Usage(
            self.requests + other.requests,
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.failures + other.failures,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "failures": self.failures,
        }


@dataclass
class Resolved:
    """A role's model, ready for ``agent.run(model=..., model_settings=...)``."""

    name: str
    model: Model | str
    settings: ModelSettings
    offline: bool = False


OFFLINE = "offline"


def offline_model(empty_json: str) -> FunctionModel:
    """A model that always answers with ``empty_json`` (used in place of a stub pin's filler model)."""

    def answer(messages: list[Any], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(empty_json)])

    return FunctionModel(answer, model_name=OFFLINE)


def is_offline(ctx: Any) -> bool:
    """True inside a run on the offline stand-in, whose empty answers output validators accept as 'not run'."""
    return getattr(getattr(ctx, "model", None), "model_name", "") == OFFLINE


def resolve(
    role: llm.Role, pins: VersionPins | None, *, empty_json: str, thinking: bool = False, extra_settings: dict[str, Any] | None = None
) -> Resolved:
    rm = llm.for_role(role, pins, thinking=thinking)
    settings: dict[str, Any] = dict(rm.settings or {})
    if extra_settings:
        settings.update(extra_settings)
    if llm.is_stub(rm.name):
        return Resolved(name=rm.name, model=offline_model(empty_json), settings=ModelSettings(**settings), offline=True)  # type: ignore[typeddict-item]
    return Resolved(name=rm.name, model=rm.model, settings=ModelSettings(**settings))  # type: ignore[typeddict-item]


@dataclass
class Outcome[T]:
    output: T | None
    response: ModelResponse | None = None
    error: str | None = None
    usage: Usage = field(default_factory=Usage)


async def run_guarded[T](
    agent: Agent[Any, T], prompt: str, resolved: Resolved, *, deps: Any = None, instructions: str | None = None
) -> Outcome[T]:
    """Run a sub-agent; a provider failure or an output that never validates returns an empty outcome.

    ``instructions`` are the run's pinned prompt (``prompts.pinned``), passed per run so a version pin or a GEPA
    candidate reaches the model without rebuilding the agent.
    """
    usage = Usage()
    try:
        result = await agent.run(prompt, model=resolved.model, model_settings=resolved.settings, deps=deps, instructions=instructions)
    except (UnexpectedModelBehavior, ModelAPIError, FallbackExceptionGroup) as e:
        usage.failures += 1
        log.warning("%s failed on %s: %s", agent.name, resolved.name, e)
        return Outcome(output=None, error=f"{type(e).__name__}: {e}", usage=usage)
    usage.add(result.usage)
    try:
        response = result.response
    except ValueError:  # pragma: no cover - a run always ends with a response
        response = None
    return Outcome(output=result.output, response=response, usage=usage)


# --------------------------------------------------------------------------- transcript excerpts


def clock(t: float) -> str:
    t = max(0, int(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def segment_text(seg: Segment, *, mark_low_asr: bool = True) -> str:
    """1-best text, with low-confidence words marked ``{word?0.42}`` as in the extraction prompts (§7.2)."""
    if not mark_low_asr or not seg.words or all(w.conf >= LOW_ASR for w in seg.words):
        return seg.text
    return " ".join(f"{{{w.text}?{w.conf:.2f}}}" if w.conf < LOW_ASR else w.text for w in seg.words)


def format_segment(seg: Segment, *, rewrite: bool = True) -> str:
    """``[s0123 | client_primary | 14:02] text ⟨rewrite: …⟩``."""
    line = f"[{seg.segment_id} | {seg.speaker_role} | {clock(seg.t_start)}] {segment_text(seg)}"
    if rewrite and seg.rewritten_text and seg.rewritten_text.strip() != seg.text.strip():
        line += f" ⟨rewrite: {seg.rewritten_text}⟩"
    return line
