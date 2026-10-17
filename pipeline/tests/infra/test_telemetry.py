"""OpenTelemetry: attribute mapping, run attributes on every span, Pydantic AI and FastAPI spans (spec §14.4)."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry import propagate, trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from advisor_intel import telemetry
from advisor_intel import telemetry_attrs as A
from advisor_intel.config import Settings
from advisor_intel.models.runs import VersionPins

PINS = VersionPins(prompt_versions={"agent": "orchestrator-v3"}, frontier_model="anthropic:claude-opus-5-5", calibrator_version="cal-7")


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    prev = Agent._instrument_default
    telemetry.setup_telemetry(Settings(), exporter=exporter, simple_export=True, set_global=False, force=True)
    yield exporter
    telemetry.shutdown_telemetry()
    Agent.instrument_all(prev)


def test_mapping_reads_current_and_legacy_names():
    legacy = {
        "gen_ai.system": "anthropic",
        "gen_ai.usage.prompt_tokens": 120,
        "gen_ai.usage.completion_tokens": 30,
        "agent_name": "extractor",
    }
    current = {
        A.GEN_AI_PROVIDER_NAME: "anthropic",
        A.GEN_AI_USAGE_INPUT_TOKENS: 120,
        A.GEN_AI_USAGE_OUTPUT_TOKENS: 30,
        A.GEN_AI_AGENT_NAME: "extractor",
    }
    for attrs in (legacy, current):
        v = A.view(attrs)
        assert (v["provider"], v["input_tokens"], v["output_tokens"], v["agent"]) == ("anthropic", 120, 30, "extractor")
    assert A.normalise(legacy) == current
    assert A.read({}, "cost_usd", 0.0) == 0.0
    assert A.SCHEMA_URL == "https://opentelemetry.io/schemas/1.37.0"


def test_run_attributes_from_pins():
    attrs = telemetry.run_attributes(call_id="c1", workflow_id="c1-run-a", stage="agent", pins=PINS)
    assert attrs == {
        "call_id": "c1",
        "workflow_id": "c1-run-a",
        "stage": "agent",
        "lexicon_version": PINS.lexicon_version,
        "calibrator_version": "cal-7",
        "prompt_version": "orchestrator-v3",
        "model": "anthropic:claude-opus-5-5",
    }
    assert telemetry.run_attributes(stage="ingest", pins=PINS)["model"] == f"{PINS.asr_provider}:{PINS.asr_model_version}"
    assert telemetry.run_attributes(stage="forecast", pins=PINS)["model"] == "none"


def test_every_span_carries_run_attributes_and_schema_url(spans):
    tracer = telemetry.get_tracer()
    with (
        tracer.start_as_current_span("activity"),
        telemetry.run_context(call_id="c1", workflow_id="c1-run-a", stage="agent", pins=PINS),
        tracer.start_as_current_span("tool call"),
        tracer.start_as_current_span("model request"),
    ):
        pass
    done = {s.name: s for s in spans.get_finished_spans()}
    for name in ("tool call", "model request"):
        a = done[name].attributes
        assert a[A.SCHEMA_URL_ATTR] == A.SCHEMA_URL
        assert {k: a[k] for k in A.RUN_ATTRIBUTES} == telemetry.run_attributes(
            call_id="c1", workflow_id="c1-run-a", stage="agent", pins=PINS
        )
    # The span that was current when the context was entered is annotated too.
    assert done["activity"].attributes[A.CALL_ID] == "c1"
    assert done["model request"].resource.schema_url == A.SCHEMA_URL
    assert done["model request"].instrumentation_scope.schema_url == A.SCHEMA_URL


def test_pydantic_ai_spans_are_instrumented_and_carry_run_attributes(spans):
    agent = Agent(TestModel(), name="note_writer")
    with telemetry.run_context(call_id="c9", workflow_id="c9-run-a", stage="note", pins=PINS):
        agent.run_sync("Write one sentence.")
    finished = spans.get_finished_spans()
    assert finished, "Pydantic AI produced no spans"
    for s in finished:
        assert s.attributes[A.CALL_ID] == "c9" and s.attributes[A.STAGE] == "note" and s.attributes[A.SCHEMA_URL_ATTR] == A.SCHEMA_URL
    views = [A.view(s.attributes) for s in finished]
    assert any(v.get("operation") == "chat" and v.get("request_model") for v in views)
    assert any(v.get("agent") == "note_writer" for v in views)


def test_stage_is_derived_for_temporal_spans(spans):
    tracer = telemetry.get_tracer()
    with tracer.start_as_current_span("RunActivity:ingest_audio"):
        pass
    with tracer.start_as_current_span("RunWorkflow:CallPipelineWorkflow"):
        pass
    by = {s.name: s.attributes.get(A.STAGE) for s in spans.get_finished_spans()}
    assert by == {"RunActivity:ingest_audio": "ingest", "RunWorkflow:CallPipelineWorkflow": "workflow"}


def test_baggage_carries_run_attributes_across_processes(spans):
    carrier: dict[str, str] = {}
    with telemetry.run_context(call_id="c1", workflow_id="c1-run-a", pins=PINS):
        propagate.inject(carrier)
    assert "baggage" in carrier
    remote = propagate.extract(carrier)
    got = telemetry.current_run_attributes(remote)
    assert got["call_id"] == "c1" and got["workflow_id"] == "c1-run-a" and got["calibrator_version"] == "cal-7"


async def test_fastapi_middleware_creates_server_spans(spans):
    app = FastAPI()
    assert telemetry.instrument_fastapi(app) in ("middleware", "instrumentor")

    @app.get("/calls/{call_id}")
    async def get_call(call_id: str):
        return {"call_id": call_id}

    parent_tracer = telemetry.get_tracer("client")
    headers: dict[str, str] = {}
    with parent_tracer.start_as_current_span("client request") as parent:
        propagate.inject(headers)
        trace_id = parent.get_span_context().trace_id
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/calls/c42", headers=headers)
    assert r.status_code == 200
    server = [s for s in spans.get_finished_spans() if s.kind == trace.SpanKind.SERVER]
    assert server, "no server span"
    s = server[0]
    assert s.context.trace_id == trace_id
    assert s.attributes.get("http.route") == "/calls/{call_id}" and s.attributes.get(A.CALL_ID) == "c42"
    assert s.attributes.get(A.SCHEMA_URL_ATTR) == A.SCHEMA_URL


def test_otlp_exporter_targets_braintrust(monkeypatch):
    import opentelemetry.exporter.otlp.proto.http.trace_exporter as otlp

    assert telemetry.otlp_exporter(Settings()) is None
    seen = {}

    class Recorder:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(otlp, "OTLPSpanExporter", Recorder)
    telemetry.otlp_exporter(
        Settings(braintrust_api_key="bt-key", braintrust_project="advisor-intel", otlp_endpoint="https://api.braintrust.dev/otel")
    )
    assert seen["endpoint"] == "https://api.braintrust.dev/otel/v1/traces"
    assert seen["headers"] == {"Authorization": "Bearer bt-key", "x-bt-parent": "project_name:advisor-intel"}


def test_temporal_interceptor_is_the_contrib_tracing_interceptor(spans):
    from temporalio.contrib.opentelemetry import TracingInterceptor

    [i] = telemetry.temporal_interceptors()
    assert isinstance(i, TracingInterceptor)
