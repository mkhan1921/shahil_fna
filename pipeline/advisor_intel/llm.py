"""Models per role (spec §13.8), built in one place so every agent gets the same provider settings.

A role resolves to a Pydantic AI model name, pinned per run in ``VersionPins``. The name ``"test"``
gives a schema-driven fake model and ``"local:<name>"`` a registered in-process model, so offline
runs and unit tests never reach a provider. Anthropic models get
adaptive thinking with an explicit effort level when the role thinks (extraction, §7.2), and the
server-side refusal fallback, so a declined request is re-run on Anthropic's recommended model
instead of failing the stage. A refusal that survives the fallback still arrives as a
``content_filter`` finish reason and is treated as a retryable failure (§4.4 rule 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.settings import ModelSettings

from .config import ModelRouting, get_settings
from .models.runs import VersionPins

Role = Literal["frontier", "reasoning", "verifier", "small", "secondary"]
Effort = Literal["low", "medium", "high"]

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per million tokens (input, output, cache read), used when the provider response carries no
# cost. Anthropic figures are current list prices; others are estimates for the budget guard only.
PRICES_PER_MTOK: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "gpt-5": (1.25, 10.0, 0.125),
    "gemini-2.5-pro": (1.25, 10.0, 0.31),
}
_DEFAULT_PRICE = (5.0, 25.0, 0.5)  # deliberately pessimistic for unknown models


# Named in-process models (FunctionModel scripts, oracles) that pins can refer to as "local:<name>".
# Used by tests, offline runs and the red-team harness; never by production pins.
_LOCAL_MODELS: dict[str, Model] = {}


def register_local_model(name: str, model: Model) -> str:
    """Register a model under ``local:<name>`` and return that pin."""
    _LOCAL_MODELS[name] = model
    return f"local:{name}"


def unregister_local_model(name: str) -> None:
    _LOCAL_MODELS.pop(name, None)


def is_test(name: str) -> bool:
    return name in ("", "test") or name.startswith(("test:", "local:"))


def is_stub(name: str) -> bool:
    """A fake that returns schema-valid filler: stages skip LLM passes whose output would only be noise."""
    return name in ("", "test") or name.startswith("test:")


def provider_of(name: str) -> str:
    return name.split(":", 1)[0] if ":" in name else ""


def model_id(name: str) -> str:
    """'anthropic:claude-opus-5-5' → 'claude-opus-5-5'."""
    return name.split(":", 1)[1] if ":" in name else name


def role_model_name(role: Role, pins: VersionPins | None = None, routing: ModelRouting | None = None) -> str:
    """The model name for a role: the run's pin when set, else the deployment's routing."""
    routing = routing or get_settings().models
    if pins is not None:
        pinned = {
            "frontier": pins.frontier_model,
            "reasoning": pins.reasoning_model,
            "verifier": pins.verifier_model,
            "small": pins.small_model,
            "secondary": routing.secondary,
        }[role]
        if pinned:
            return pinned
    return getattr(routing, role)


def build_model(name: str, *, secondary: str | None = None) -> Model | str:
    """A Pydantic AI model for ``name``; wrapped in a ``FallbackModel`` when a secondary provider is set (§13.7)."""
    if name.startswith("local:"):
        key = name.split(":", 1)[1]
        if key not in _LOCAL_MODELS:
            raise KeyError(f"no local model registered as {key!r}")
        return _LOCAL_MODELS[key]
    if is_test(name):
        from .testing.fakes import schema_fake_model  # TestModel cannot produce native structured output

        return schema_fake_model()
    if secondary and not is_test(secondary) and provider_of(secondary) != provider_of(name):
        return FallbackModel(name, secondary)
    return name


def model_settings(
    name: str,
    *,
    thinking: bool = False,
    effort: Effort | None = None,
    max_tokens: int | None = None,
    refusal_fallbacks: bool | None = None,
) -> ModelSettings:
    """Provider settings for one call.

    Thinking uses the unified ``thinking`` setting, which Pydantic AI maps to adaptive thinking plus
    an effort level on Anthropic models and to reasoning effort elsewhere. Sampling temperature is
    left to the provider: current reasoning models reject a non-default temperature, and
    determinism comes from fixed prompts and validators instead (§13.7).
    """
    routing = get_settings().models
    settings: dict[str, Any] = {}
    if max_tokens:
        settings["max_tokens"] = max_tokens
    if thinking:
        settings["thinking"] = effort or routing.extraction_effort
    elif effort:
        settings["thinking"] = effort
    if provider_of(name) == "anthropic":
        settings["cache"] = True  # stage prompts share the context pack and window (§7.2)
        if refusal_fallbacks if refusal_fallbacks is not None else routing.refusal_fallbacks:
            settings["anthropic_betas"] = [REFUSAL_FALLBACK_BETA]
            settings["extra_body"] = {"fallbacks": "default"}
    return ModelSettings(**settings)  # type: ignore[typeddict-item]


@dataclass(frozen=True)
class RoleModel:
    """A resolved role: the model to pass to ``Agent.run(model=...)`` and the settings that go with it."""

    role: Role
    name: str
    model: Model | str
    settings: ModelSettings


def for_role(role: Role, pins: VersionPins | None = None, *, thinking: bool = False, effort: Effort | None = None) -> RoleModel:
    routing = get_settings().models
    name = role_model_name(role, pins, routing)
    secondary = None if role == "secondary" else routing.secondary
    return RoleModel(
        role=role, name=name, model=build_model(name, secondary=secondary), settings=model_settings(name, thinking=thinking, effort=effort)
    )


CACHE_WRITE_MULTIPLIER = 1.25  # a cache write costs 1.25× the input price (5-minute cache)


def usage_cost_usd(
    name: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    reported: float | None = None,
) -> float:
    """USD for one model's usage: the provider-reported cost when present, else the price table.

    ``input_tokens`` is Pydantic AI's total, which includes the cache reads and writes; those are priced
    at the cache-read price and the cache-write multiple, the rest at the input price.
    """
    if reported:
        return float(reported)
    if is_test(name):
        return 0.0
    p_in, p_out, p_cache = PRICES_PER_MTOK.get(model_id(name), _DEFAULT_PRICE)
    fresh_in = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
    return (
        fresh_in * p_in + output_tokens * p_out + cache_read_tokens * p_cache + cache_write_tokens * p_in * CACHE_WRITE_MULTIPLIER
    ) / 1e6
