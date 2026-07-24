"""Thin, privacy-safe OpenTelemetry integration for AgentBox.

Official FastAPI and HTTPX instrumentors own spans and W3C propagation. The
default-deny exporter prevents framework or dependency upgrades from exporting
paths, identifiers, provider responses, or customer payloads.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcOTLPSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpOTLPSpanExporter,
)
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBased,
    Sampler,
    TraceIdRatioBased,
)
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import (
    Link,
    SpanContext,
    Status,
    TraceState,
)
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)

from agentbox.config import settings
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SAFE_IDENTITY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_HEALTH_PATHS = "/health,/health/live,/health/ready,/livez"

_trace_provider: TracerProvider | None = None


def _environment() -> str:
    raw = str(settings.environment).strip().lower()
    return "production" if raw in {"prod", "production"} else "development"


def _release_sha() -> str:
    raw = (settings.release_sha or "").strip().lower()
    return raw if _SHA_RE.fullmatch(raw) else "unknown"


def _service_name() -> str:
    configured = settings.otel_service_name.strip()
    if configured != "lemma-agentbox":
        raise ValueError("AgentBox OTEL service name must be exactly 'lemma-agentbox'")
    return configured


def _resource() -> Resource:
    namespace = settings.otel_service_namespace or "lemma"
    if namespace != "lemma":
        raise ValueError("AgentBox OTEL service namespace must be exactly 'lemma'")
    return Resource.create(
        {
            "service.namespace": namespace,
            "service.name": _service_name(),
            "service.version": _release_sha(),
            "deployment.environment.name": _environment(),
            "cloud.provider": "azure",
            "cloud.platform": "azure_container_apps",
        }
    )


def _normalize_protocol(raw: str | None) -> str:
    protocol = (raw or "grpc").strip().lower()
    if protocol == "grpc":
        return protocol
    if protocol in {"http", "http/protobuf"}:
        return "http/protobuf"
    raise ValueError(f"unsupported OTLP protocol: {protocol}")


def _trace_protocol() -> str:
    return _normalize_protocol(
        settings.otel_exporter_otlp_traces_protocol
        or settings.otel_exporter_otlp_protocol
    )


def _trace_endpoint() -> str | None:
    if settings.otel_exporter_otlp_traces_endpoint:
        return settings.otel_exporter_otlp_traces_endpoint
    endpoint = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        return None
    if _trace_protocol() == "grpc":
        return endpoint
    return f"{endpoint.rstrip('/')}/v1/traces"


def _parse_headers(raw: str | None) -> dict[str, str] | None:
    if not raw:
        return None
    headers: dict[str, str] = {}
    for item in raw.split(","):
        key, separator, value = item.partition("=")
        if separator and key.strip() and value.strip():
            headers[key.strip()] = value.strip()
    return headers or None


def _trace_headers() -> dict[str, str] | None:
    return _parse_headers(
        settings.otel_exporter_otlp_traces_headers
        or settings.otel_exporter_otlp_headers
    )


def _sampler() -> Sampler:
    ratio = float(settings.otel_traces_sampler_arg)
    selected = settings.otel_traces_sampler.strip().lower()
    samplers: dict[str, Sampler] = {
        "always_on": ALWAYS_ON,
        "always_off": ALWAYS_OFF,
        "traceidratio": TraceIdRatioBased(ratio),
        "parentbased_always_on": ParentBased(ALWAYS_ON),
        "parentbased_always_off": ParentBased(ALWAYS_OFF),
        "parentbased_traceidratio": ParentBased(TraceIdRatioBased(ratio)),
    }
    try:
        return samplers[selected]
    except KeyError as exc:
        raise ValueError(f"unsupported OTEL trace sampler: {selected}") from exc


def _span_exporter(endpoint: str) -> SpanExporter:
    headers = _trace_headers()
    if _trace_protocol() == "http/protobuf":
        return HttpOTLPSpanExporter(endpoint=endpoint, headers=headers)
    return GrpcOTLPSpanExporter(
        endpoint=endpoint,
        headers=headers,
        insecure=endpoint.startswith("http://") or "://" not in endpoint,
    )


RESOURCE_ATTRIBUTE_KEYS = frozenset(
    {
        "service.namespace",
        "service.name",
        "service.version",
        "deployment.environment.name",
        "cloud.provider",
        "cloud.platform",
        "telemetry.sdk.language",
        "telemetry.sdk.name",
        "telemetry.sdk.version",
    }
)

SPAN_ATTRIBUTE_KEYS = frozenset(
    {
        "http.request.method",
        "http.response.status_code",
        "http.route",
        "http.method",
        "http.status_code",
        "network.protocol.name",
        "network.protocol.version",
        "error.type",
        "error.code",
        "lemma.request.id",
        "agentbox.operation",
        "agentbox.workload.kind",
        "agentbox.provider",
        "agentbox.profile",
        "agentbox.phase",
        "agentbox.outcome",
    }
)


def _safe_value(value: Any) -> str | bool | int | float | None:
    if isinstance(value, str):
        return " ".join(value.splitlines())[:128]
    if isinstance(value, bool | int | float):
        return value
    return None


def _sanitize_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        key: safe
        for key, value in (attributes or {}).items()
        if key in SPAN_ATTRIBUTE_KEYS
        if (safe := _safe_value(value)) is not None
    }


def _safe_context(context: SpanContext | None) -> SpanContext | None:
    if context is None:
        return None
    return SpanContext(
        trace_id=context.trace_id,
        span_id=context.span_id,
        is_remote=context.is_remote,
        trace_flags=context.trace_flags,
        trace_state=TraceState(),
    )


def _safe_scope(scope: InstrumentationScope | None) -> InstrumentationScope | None:
    if scope is None:
        return None
    name = scope.name if _SAFE_IDENTITY_RE.fullmatch(scope.name) else "unknown"
    version = scope.version
    if version is not None and not re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}", version):
        version = None
    return InstrumentationScope(name=name, version=version)


def _safe_span_name(span: ReadableSpan) -> str:
    scope = (getattr(span.instrumentation_scope, "name", None) or "").lower()
    if "fastapi" in scope or "asgi" in scope:
        return "http.server"
    if "httpx" in scope:
        return "http.client"
    if scope == "agentbox.telemetry" and _SAFE_NAME_RE.fullmatch(span.name):
        return span.name[:128]
    return "dependency.operation"


def _sanitize_span(span: ReadableSpan) -> ReadableSpan:
    resource = Resource(
        {
            key: value
            for key, value in span.resource.attributes.items()
            if key in RESOURCE_ATTRIBUTE_KEYS
        },
        schema_url=span.resource.schema_url,
    )
    events = tuple(
        Event(
            event.name if _SAFE_NAME_RE.fullmatch(event.name) else "span.event",
            attributes={
                key: value
                for key, value in _sanitize_attributes(event.attributes).items()
                if key in {"error.type", "error.code", "agentbox.outcome"}
            },
            timestamp=event.timestamp,
        )
        for event in span.events
    )
    links = tuple(
        Link(_safe_context(link.context), _sanitize_attributes(link.attributes))
        for link in span.links
    )
    return ReadableSpan(
        name=_safe_span_name(span),
        context=_safe_context(span.context),
        parent=_safe_context(span.parent),
        resource=resource,
        attributes=_sanitize_attributes(span.attributes),
        events=events,
        links=links,
        kind=span.kind,
        instrumentation_scope=_safe_scope(span.instrumentation_scope),
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
    )


class SanitizingSpanExporter(SpanExporter):
    """Drop every span field that is not explicitly reviewed above."""

    def __init__(self, delegate: SpanExporter) -> None:
        self._delegate = delegate

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        safe: list[ReadableSpan] = []
        for span in spans:
            try:
                safe.append(_sanitize_span(span))
            except Exception:
                # An unknown SDK span shape is safer to drop than export unsanitized.
                continue
        if not safe:
            return SpanExportResult.SUCCESS
        return self._delegate.export(tuple(safe))

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._delegate.force_flush(timeout_millis)


def setup_telemetry() -> None:
    """Configure tracing once; official instrumentors own HTTP behavior."""

    global _trace_provider

    if (
        _trace_provider is not None
        or not settings.observability_enabled
        or settings.otel_sdk_disabled
    ):
        return
    if settings.otel_propagators.strip().lower() != "tracecontext":
        raise ValueError("AgentBox supports only the W3C tracecontext propagator")
    set_global_textmap(TraceContextTextMapPropagator())

    selector = settings.otel_traces_exporter.strip().lower()
    if selector not in {"none", "otlp"}:
        raise ValueError(f"unsupported OTEL_TRACES_EXPORTER: {selector}")
    if selector == "none":
        return
    endpoint = _trace_endpoint()
    if not endpoint:
        raise RuntimeError(
            "OTEL trace export is enabled but the managed OTLP endpoint is missing"
        )

    provider = TracerProvider(resource=_resource(), sampler=_sampler())
    provider.add_span_processor(
        BatchSpanProcessor(
            SanitizingSpanExporter(_span_exporter(endpoint)),
            max_queue_size=2048,
            max_export_batch_size=512,
            export_timeout_millis=5_000,
        )
    )
    trace.set_tracer_provider(provider)
    _trace_provider = provider
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)


def shutdown_telemetry(timeout_millis: int = 5_000) -> None:
    global _trace_provider

    provider = _trace_provider
    if provider is None:
        return
    try:
        provider.force_flush(timeout_millis=timeout_millis)
    finally:
        try:
            provider.shutdown()
        finally:
            _trace_provider = None


def instrument_fastapi_app(app: FastAPI) -> None:
    """Let the official FastAPI instrumentor handle routing and idempotency."""

    if _trace_provider is None:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_trace_provider,
        excluded_urls=_HEALTH_PATHS,
        exclude_spans=["receive", "send"],
    )


def current_trace_fields() -> dict[str, str]:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {
        "trace_id": f"{context.trace_id:032x}",
        "span_id": f"{context.span_id:016x}",
    }


def enrich_current_span(*, request_id: str) -> None:
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("lemma.request.id", request_id[:128])
