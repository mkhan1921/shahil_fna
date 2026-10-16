"""Span attribute names, in one place (spec §14.4).

Spans follow the OpenTelemetry GenAI semantic conventions at a pinned schema version. Dashboards,
online scorers and our own code read attributes only through this module: a convention rename is
a change to the constants and the ``LEGACY`` table here, nowhere else.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

# --------------------------------------------------------------------------- schema

# GenAI conventions as of semantic-conventions 1.37.0: gen_ai.provider.name replaces gen_ai.system,
# and messages travel as gen_ai.input.messages / gen_ai.output.messages / gen_ai.system_instructions,
# which is what Pydantic AI's instrumentation (format version 5) emits.
GENAI_SCHEMA_VERSION: Final = "1.37.0"
SCHEMA_URL: Final = f"https://opentelemetry.io/schemas/{GENAI_SCHEMA_VERSION}"
SCHEMA_URL_ATTR: Final = "schema_url"

# --------------------------------------------------------------------------- run attributes (every span)

CALL_ID: Final = "call_id"
WORKFLOW_ID: Final = "workflow_id"
STAGE: Final = "stage"
PROMPT_VERSION: Final = "prompt_version"
LEXICON_VERSION: Final = "lexicon_version"
CALIBRATOR_VERSION: Final = "calibrator_version"
MODEL: Final = "model"
RUN_ATTRIBUTES: Final = (CALL_ID, WORKFLOW_ID, STAGE, PROMPT_VERSION, LEXICON_VERSION, CALIBRATOR_VERSION, MODEL)

# Run attributes cross process boundaries (API → Temporal → activities) as W3C baggage under this prefix.
BAGGAGE_PREFIX: Final = "advisor_intel."

# --------------------------------------------------------------------------- GenAI semantic conventions

GEN_AI_OPERATION_NAME: Final = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME: Final = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL: Final = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL: Final = "gen_ai.response.model"
GEN_AI_RESPONSE_ID: Final = "gen_ai.response.id"
GEN_AI_RESPONSE_FINISH_REASONS: Final = "gen_ai.response.finish_reasons"
GEN_AI_AGENT_NAME: Final = "gen_ai.agent.name"
GEN_AI_CONVERSATION_ID: Final = "gen_ai.conversation.id"
GEN_AI_TOOL_NAME: Final = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID: Final = "gen_ai.tool.call.id"
GEN_AI_TOOL_DEFINITIONS: Final = "gen_ai.tool.definitions"
GEN_AI_INPUT_MESSAGES: Final = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES: Final = "gen_ai.output.messages"
GEN_AI_SYSTEM_INSTRUCTIONS: Final = "gen_ai.system_instructions"
GEN_AI_OUTPUT_TYPE: Final = "gen_ai.output.type"
GEN_AI_USAGE_INPUT_TOKENS: Final = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS: Final = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: Final = "gen_ai.usage.cache_read.input_tokens"
GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS: Final = "gen_ai.usage.cache_creation.input_tokens"
# Not a semantic convention: Pydantic AI's run-level totals and cost.
RUN_INPUT_TOKENS: Final = "gen_ai.aggregated_usage.input_tokens"
RUN_OUTPUT_TOKENS: Final = "gen_ai.aggregated_usage.output_tokens"
OPERATION_COST: Final = "operation.cost"

# Older names still found on spans from earlier conventions or SDKs → the current name.
LEGACY: Final[dict[str, str]] = {
    "gen_ai.system": GEN_AI_PROVIDER_NAME,
    "gen_ai.usage.prompt_tokens": GEN_AI_USAGE_INPUT_TOKENS,
    "gen_ai.usage.completion_tokens": GEN_AI_USAGE_OUTPUT_TOKENS,
    "gen_ai.prompt": GEN_AI_INPUT_MESSAGES,
    "gen_ai.completion": GEN_AI_OUTPUT_MESSAGES,
    "agent_name": GEN_AI_AGENT_NAME,
    "model_name": GEN_AI_REQUEST_MODEL,
}

# --------------------------------------------------------------------------- logical fields for readers

# What dashboards and online scorers ask for → attribute names to look under, best first.
FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "operation": (GEN_AI_OPERATION_NAME,),
    "provider": (GEN_AI_PROVIDER_NAME, "gen_ai.system"),
    "request_model": (GEN_AI_REQUEST_MODEL, "model_name"),
    "response_model": (GEN_AI_RESPONSE_MODEL,),
    "agent": (GEN_AI_AGENT_NAME, "agent_name"),
    "tool": (GEN_AI_TOOL_NAME,),
    "input_tokens": (GEN_AI_USAGE_INPUT_TOKENS, "gen_ai.usage.prompt_tokens", RUN_INPUT_TOKENS),
    "output_tokens": (GEN_AI_USAGE_OUTPUT_TOKENS, "gen_ai.usage.completion_tokens", RUN_OUTPUT_TOKENS),
    "cache_read_tokens": (GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,),
    "cost_usd": (OPERATION_COST,),
    "finish_reasons": (GEN_AI_RESPONSE_FINISH_REASONS,),
    "call_id": (CALL_ID,),
    "workflow_id": (WORKFLOW_ID,),
    "stage": (STAGE,),
    "prompt_version": (PROMPT_VERSION,),
    "lexicon_version": (LEXICON_VERSION,),
    "calibrator_version": (CALIBRATOR_VERSION,),
    "model": (MODEL, GEN_AI_RESPONSE_MODEL, GEN_AI_REQUEST_MODEL),
    "schema_url": (SCHEMA_URL_ATTR,),
}


def read(attrs: Mapping[str, Any], field: str, default: Any = None) -> Any:
    """One logical field from a span's attributes, whichever convention name it was recorded under."""
    for name in FIELDS[field]:
        if name in attrs and attrs[name] not in (None, ""):
            return attrs[name]
    return default


def view(attrs: Mapping[str, Any]) -> dict[str, Any]:
    """Every logical field present on a span: the record a dashboard row or online scorer consumes."""
    out = {}
    for field in FIELDS:
        v = read(attrs, field)
        if v is not None:
            out[field] = v
    return out


def normalise(attrs: Mapping[str, Any]) -> dict[str, Any]:
    """Attributes with legacy names renamed to the pinned conventions (current names win on conflict)."""
    out = dict(attrs)
    for old, new in LEGACY.items():
        if old in out:
            v = out.pop(old)
            out.setdefault(new, v)
    return out


# --------------------------------------------------------------------------- stages

# Temporal activity type → pipeline stage, for spans created by the Temporal interceptor.
ACTIVITY_STAGE: Final[dict[str, str]] = {
    "ingest_audio": "ingest",
    "run_discourse": "discourse",
    "run_agent": "agent",
    "run_voi_and_followups": "voi",
    "run_forecast": "forecast",
    "apply_review": "review",
    "notify_advisor": "notify",
    "send_review_reminder": "notify",
    "intake_followup_answer": "followup_intake",
    "render_meeting_note": "note",
    "build_calibration_labels": "nightly",
    "retrain_calibrator": "nightly",
    "refit_conformal": "nightly",
    "propose_lexicon_fit": "nightly",
    "sync_error_dataset": "nightly",
    "sync_gold_candidates": "nightly",
    "run_model_swap_eval": "model_swap",
}

# The model role that does a stage's main work (§13.8); stages with no model report "none".
STAGE_MODEL_ROLE: Final[dict[str, str]] = {
    "discourse": "frontier",
    "agent": "frontier",
    "voi": "frontier",
    "followup_intake": "reasoning",
    "note": "frontier",
}
