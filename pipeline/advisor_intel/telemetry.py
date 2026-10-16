"""OpenTelemetry setup and span attributes (spec §14.4).

``setup_telemetry()`` runs once per process and wires:

- a tracer provider whose resource carries the pinned GenAI semantic-conventions schema URL;
- ``RunAttributesProcessor``, which stamps ``call_id``, ``workflow_id``, ``stage``,
  ``prompt_version``, ``lexicon_version``, ``calibrator_version``, ``model`` and the schema URL on
  every span from the current run context;
- OTLP over HTTP to Braintrust (``Settings.otlp_endpoint`` + ``/v1/traces``, API key and project
  headers) when an API key is configured;
- Pydantic AI instrumentation for every agent, model request and tool call.

``temporal_interceptors()`` gives the Temporal OpenTelemetry interceptor for clients and workers,
and ``instrument_fastapi(app)`` the FastAPI instrumentation (the OpenTelemetry instrumentor when
installed, else an equivalent ASGI middleware).

Run context: ``run_context(call_id=…, workflow_id=…, stage=…, pins=…)`` sets the run attributes
as W3C baggage, so they reach every child span in this process and, through the Temporal
interceptor's header propagation, the workflow and its activities in the worker.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from opentelemetry import baggage, context as otel_context, propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode

from . import __version__
from . import telemetry_attrs as A

if TYPE_CHECKING:
    from .config import Settings
    from .models.runs import VersionPins

log = logging.getLogger(__name__)

_lock = threading.Lock()
_provider: TracerProvider | None = None
TRACER_NAME = "advisor_intel"


# --------------------------------------------------------------------------- run attributes


def run_attributes(
    *,
    call_id: str | None = None,
    workflow_id: str | None = None,
    stage: str | None = None,
    pins: "VersionPins | None" = None,
    model: str | None = None,
    prompt_version: str | None = None,
) -> dict[str, str]:
    """The run attributes for a stage, derived from the run's version pins (§13.8)."""
    out: dict[str, str] = {}
    if call_id:
        out[A.CALL_ID] = call_id
    if workflow_id:
        out[A.WORKFLOW_ID] = workflow_id
    if stage:
        out[A.STAGE] = stage
    if pins is not None:
        out[A.LEXICON_VERSION] = pins.lexicon_version
        out[A.CALIBRATOR_VERSION] = pins.calibrator_version
        if prompt_version is None:
            pv = pins.prompt_versions
            prompt_version = pv.get(stage or "") or (",".join(f"{k}={v}" for k, v in sorted(pv.items())) if pv else "default")
        if model is None:
            role = A.STAGE_MODEL_ROLE.get(stage or "")
            model = {
                "frontier": pins.frontier_model,
                "reasoning": pins.reasoning_model,
                "verifier": pins.verifier_model,
                "small": pins.small_model,
            }.get(role or "", "none")
            if stage == "ingest":
                model = f"{pins.asr_provider}:{pins.asr_model_version}"
    if prompt_version:
        out[A.PROMPT_VERSION] = prompt_version
    if model:
        out[A.MODEL] = model
    return out


def current_run_attributes(ctx: otel_context.Context | None = None) -> dict[str, str]:
    """Run attributes carried as baggage in ``ctx`` (default: the current context)."""
    out: dict[str, str] = {}
    for name in A.RUN_ATTRIBUTES:
        v = baggage.get_baggage(A.BAGGAGE_PREFIX + name, ctx)
        if v not in (None, ""):
            out[name] = str(v)
    return out


@contextlib.contextmanager
def run_context(**kwargs: Any) -> Iterator[dict[str, str]]:
    """Attach run attributes for everything inside the block (child spans, outgoing Temporal calls).

    Accepts the arguments of ``run_attributes``; the attributes are also set on the current span.
    """
    attrs = run_attributes(**kwargs)
    ctx = otel_context.get_current()
    for k, v in attrs.items():
        ctx = baggage.set_baggage(A.BAGGAGE_PREFIX + k, v, ctx)
    token = otel_context.attach(ctx)
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attributes(attrs)
    try:
        yield attrs
    finally:
        otel_context.detach(token)


class RunAttributesProcessor(SpanProcessor):
    """Stamps the run attributes and the GenAI schema URL on every span when it starts."""

    def on_start(self, span: Span, parent_context: otel_context.Context | None = None) -> None:
        span.set_attribute(A.SCHEMA_URL_ATTR, A.SCHEMA_URL)
        attrs = current_run_attributes(parent_context)
        name = span.name or ""
        if A.STAGE not in attrs:
            kind, _, activity = name.partition(":")
            if kind in ("RunActivity", "StartActivity") and activity:
                attrs[A.STAGE] = A.ACTIVITY_STAGE.get(activity, activity)
            elif kind in ("RunWorkflow", "StartWorkflow", "SignalWorkflow", "QueryWorkflow", "HandleSignal", "HandleQuery"):
                attrs[A.STAGE] = "workflow"
        if attrs:
            span.set_attributes(attrs)

    def on_end(self, span: ReadableSpan) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


# --------------------------------------------------------------------------- setup


def otlp_exporter(settings: "Settings") -> SpanExporter | None:
    """OTLP/HTTP to Braintrust, or None when no API key is configured (local runs, tests)."""
    if not settings.braintrust_api_key:
        return None
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    endpoint = settings.otlp_endpoint.rstrip("/")
    if not endpoint.endswith("/v1/traces"):
        endpoint += "/v1/traces"
    headers = {"Authorization": f"Bearer {settings.braintrust_api_key}", "x-bt-parent": f"project_name:{settings.braintrust_project}"}
    return OTLPSpanExporter(endpoint=endpoint, headers=headers)


def setup_telemetry(
    settings: "Settings | None" = None,
    *,
    service_name: str = "advisor-intel",
    exporter: SpanExporter | None = None,
    simple_export: bool = False,
    set_global: bool = True,
    instrument_agents: bool = True,
    include_content: bool | None = None,
    force: bool = False,
) -> TracerProvider:
    """Configure tracing once per process; later calls return the same provider unless ``force``."""
    global _provider
    with _lock:
        if _provider is not None and not force:
            return _provider
        if settings is None:
            from .config import get_settings

            settings = get_settings()
        resource = Resource.create(
            {"service.name": service_name, "service.version": __version__, "deployment.environment": settings.env}, schema_url=A.SCHEMA_URL
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(RunAttributesProcessor())
        exp = exporter if exporter is not None else otlp_exporter(settings)
        if exp is not None:
            provider.add_span_processor(SimpleSpanProcessor(exp) if simple_export else BatchSpanProcessor(exp))
        if set_global:
            trace.set_tracer_provider(provider)
        if instrument_agents:
            _instrument_pydantic_ai(provider, include_content)
        _provider = provider
        return provider


def _instrument_pydantic_ai(provider: TracerProvider, include_content: bool | None) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.models.instrumented import InstrumentationSettings

    if include_content is None:
        include_content = os.environ.get("ADVISOR_INTEL_TRACE_CONTENT", "true").strip().lower() not in ("0", "false", "no", "off")
    Agent.instrument_all(InstrumentationSettings(tracer_provider=provider, include_content=include_content, version=5))


def get_tracer(name: str = TRACER_NAME) -> trace.Tracer:
    provider = _provider
    if provider is not None:
        return provider.get_tracer(name, __version__, schema_url=A.SCHEMA_URL)
    return trace.get_tracer(name, __version__, schema_url=A.SCHEMA_URL)


def shutdown_telemetry() -> None:
    global _provider
    with _lock:
        if _provider is not None:
            _provider.force_flush()
            _provider.shutdown()
        _provider = None


# --------------------------------------------------------------------------- Temporal and FastAPI


def temporal_interceptors() -> list[Any]:
    """The Temporal OpenTelemetry interceptor, for both the client and the worker (§14.4)."""
    from temporalio.contrib.opentelemetry import TracingInterceptor

    return [TracingInterceptor(tracer=get_tracer("temporalio"))]


def instrument_fastapi(app: Any) -> str:
    """Instrument a FastAPI app. Returns which mechanism was used."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore[import-not-found]
    except ImportError:
        app.add_middleware(TracingMiddleware)
        return "middleware"
    FastAPIInstrumentor.instrument_app(app, tracer_provider=_provider or trace.get_tracer_provider(), excluded_urls="/healthz")
    return "instrumentor"


class TracingMiddleware:
    """A server span per HTTP request, continuing the caller's trace (W3C traceparent and baggage)."""

    def __init__(self, app: Any, excluded_paths: tuple[str, ...] = ("/healthz",)) -> None:
        self.app = app
        self.excluded_paths = excluded_paths

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") in self.excluded_paths:
            await self.app(scope, receive, send)
            return
        carrier = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        parent = propagate.extract(carrier)
        method = scope.get("method", "GET")
        tracer = get_tracer("advisor_intel.api")
        attrs = {"http.request.method": method, "url.path": scope.get("path", ""), "url.scheme": scope.get("scheme", "http")}
        status: dict[str, int] = {}
        with tracer.start_as_current_span(
            f"{method} {scope.get('path', '')}", context=parent, kind=SpanKind.SERVER, attributes=attrs
        ) as span:

            async def send_wrapper(message: Mapping[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    status["code"] = int(message["status"])
                    span.set_attribute("http.response.status_code", status["code"])
                await send(message)

            try:
                await self.app(scope, receive, send_wrapper)
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, type(e).__name__))
                raise
            finally:
                route = scope.get("route")
                path = getattr(route, "path", None)
                if path:
                    span.update_name(f"{method} {path}")
                    span.set_attribute("http.route", path)
                call_id = (scope.get("path_params") or {}).get("call_id")
                if call_id:
                    span.set_attribute(A.CALL_ID, str(call_id))
                if status.get("code", 500) >= 500:
                    span.set_status(Status(StatusCode.ERROR))
