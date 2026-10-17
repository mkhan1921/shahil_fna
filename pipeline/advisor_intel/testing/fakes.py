"""Fake models for offline runs and tests.

``schema_fake_model`` answers any structured-output request with minimal data that fits the output
schema (like Pydantic AI's ``TestModel``, which cannot produce native structured output), so a run
pinned to ``"test"`` exercises every code path without a provider. ``scripted_model`` wraps a
handler that receives the rendered prompt and the output schema and returns the output as a dict:
the building block for oracles that answer from known ground truth.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


def _gen(schema: dict[str, Any], defs: dict[str, Any], seed: int, depth: int = 0) -> Any:
    if "$ref" in schema:
        return _gen(defs[schema["$ref"].rsplit("/", 1)[-1]], defs, seed, depth)
    if "enum" in schema:
        return schema["enum"][seed % len(schema["enum"])]
    if "const" in schema:
        return schema["const"]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"] or schema[key]
            return _gen(options[seed % len(options)], defs, seed, depth)
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "null")
    if t == "object":
        props = schema.get("properties", {})
        return {k: _gen(v, defs, seed, depth + 1) for k, v in props.items()}
    if t == "array":
        if depth > 6:
            return []
        return [_gen(schema.get("items", {}), defs, seed, depth + 1)] if seed % 2 == 0 else []
    if t == "string":
        return "a"
    if t == "integer":
        return 0
    if t == "number":
        return 0.0
    if t == "boolean":
        return False
    return None


def fake_output(schema: dict[str, Any], seed: int = 0) -> Any:
    """Minimal data that validates against a JSON schema."""
    return _gen(schema, schema.get("$defs", {}), seed)


def last_user_text(messages: list[ModelMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart):
                    return p.content if isinstance(p.content, str) else " ".join(str(c) for c in p.content)
    return ""


def schema_fake_model(seed: int = 0) -> FunctionModel:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out = info.model_request_parameters.output_object
        if out is not None:
            return ModelResponse(parts=[TextPart(content=json.dumps(fake_output(out.json_schema, seed)))])
        return ModelResponse(parts=[TextPart(content="ok")])

    return FunctionModel(fn, model_name="schema-fake")


Handler = Callable[[str, dict[str, Any], AgentInfo], Any]


def scripted_model(handler: Handler, name: str = "scripted") -> FunctionModel:
    """A model whose structured output comes from ``handler(prompt_text, output_schema, info)``."""

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out = info.model_request_parameters.output_object
        schema = out.json_schema if out is not None else {}
        result = handler(last_user_text(messages), schema, info)
        text = result if isinstance(result, str) else json.dumps(result)
        return ModelResponse(parts=[TextPart(content=text)])

    return FunctionModel(fn, model_name=name)
