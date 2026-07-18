"""Independent OpenTelemetry runtime for the AgentBox manager."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
import re
from typing import Any

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter as GrpcLogExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter as GrpcMetricExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http._log_exporter import (
    OTLPLogExporter as HttpLogExporter,
)
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter as HttpMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpSpanExporter,
)
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics._internal.exemplar.exemplar_filter import (
    AlwaysOffExemplarFilter,
)
from opentelemetry.sdk.metrics.view import View
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
    TraceIdRatioBased,
)
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import Link, SpanContext, Status, StatusCode, TraceState

from agentbox.config import settings


_SIGNALS = frozenset({"traces", "metrics", "logs"})
_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_TRACE_ATTRIBUTES = frozenset(
    {
        "http.request.method",
        "http.response.status_code",
        "http.route",
        "http.method",
        "http.status_code",
        "network.protocol.name",
        "network.protocol.version",
        "rpc.system",
        "rpc.service",
        "rpc.method",
        "error.type",
        "lemma.request_id",
        "lemma.correlation_id",
        "lemma.event_id",
        "lemma.job_id",
        "lemma.provider",
        "lemma.operation",
        "lemma.outcome",
    }
)
_METRIC_ATTRIBUTES = frozenset(
    {
        "http.request.method",
        "http.response.status_code",
        "http.route",
        "http.method",
        "http.status_code",
        "network.protocol.name",
        "network.protocol.version",
        "provider",
        "operation",
        "outcome",
    }
)
_RESOURCE_ATTRIBUTES = frozenset(
    {
        "service.name",
        "service.namespace",
        "service.version",
        "deployment.environment",
        "telemetry.sdk.language",
        "telemetry.sdk.name",
        "telemetry.sdk.version",
    }
)
_SAFE_LOG_FIELDS = frozenset(
    {
        "request_id",
        "correlation_id",
        "event_id",
        "job_id",
        "provider",
        "operation",
        "outcome",
        "duration_ms",
        "failure_count",
        "incident_duration_ms",
        "method",
        "route",
        "status_code",
        "error_type",
        "error_code",
        "error_stack_hash",
        "retryable",
    }
)

_initialized = False
_instrumented_apps: set[int] = set()
_httpx_instrumented = False
_trace_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None
_logger_provider: LoggerProvider | None = None


def _protocol(value: str | None) -> str:
    selected = (value or "grpc").strip().lower()
    if selected in {"http", "http/protobuf"}:
        return "http/protobuf"
    if selected == "grpc":
        return selected
    raise ValueError(f"unsupported OTLP protocol: {selected}")


def _headers(value: str | None) -> dict[str, str] | None:
    parsed: dict[str, str] = {}
    for item in (value or "").split(","):
        if "=" not in item:
            continue
        key, content = item.split("=", 1)
        if key.strip() and content.strip():
            parsed[key.strip()] = content.strip()
    return parsed or None


def _enabled_signals() -> set[str]:
    legacy_configured = settings.otel_signals is not None
    selected = {
        item.strip().lower()
        for item in (settings.otel_signals or "traces").split(",")
        if item.strip()
    }
    selected = selected or {"traces"}
    if selected - _SIGNALS:
        raise ValueError("OTEL_SIGNALS contains unsupported signals")
    fields_set = settings.model_fields_set
    for signal in _SIGNALS:
        field = f"otel_{signal}_exporter"
        if field in fields_set or not legacy_configured:
            exporter = str(getattr(settings, field)).strip().lower()
            if exporter == "otlp":
                selected.add(signal)
            elif exporter == "none":
                selected.discard(signal)
            else:
                raise ValueError(f"unsupported {field}: {exporter}")
    return selected


def _signal_protocol(signal: str) -> str:
    return _protocol(
        getattr(settings, f"otel_exporter_otlp_{signal}_protocol")
        or settings.otel_exporter_otlp_protocol
    )


def _signal_endpoint(signal: str) -> str | None:
    specific = getattr(settings, f"otel_exporter_otlp_{signal}_endpoint")
    if specific:
        return specific
    endpoint = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        return None
    if _signal_protocol(signal) == "grpc":
        return endpoint
    return f"{endpoint.rstrip('/')}/v1/{signal}"


def _signal_headers(signal: str) -> dict[str, str] | None:
    return _headers(
        getattr(settings, f"otel_exporter_otlp_{signal}_headers")
        or settings.otel_exporter_otlp_headers
    )


def _sampler():
    selected = settings.otel_traces_sampler.strip().lower()
    ratio = float(settings.otel_traces_sampler_arg)
    samplers = {
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


def _resource() -> Resource:
    release = settings.release_sha or ""
    if not re.fullmatch(r"[0-9a-f]{40}", release):
        release = "unknown"
    service_name = settings.otel_service_name
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", service_name):
        service_name = "lemma-agentbox"
    environment = (
        "production"
        if settings.environment.strip().lower() in {"prod", "production"}
        else "development"
    )
    attributes = {
        "service.name": service_name,
        "service.version": release,
        "deployment.environment": environment,
    }
    if settings.otel_service_namespace and re.fullmatch(
        r"[A-Za-z0-9_.-]{1,128}", settings.otel_service_namespace
    ):
        attributes["service.namespace"] = settings.otel_service_namespace
    return Resource.create(attributes)


def _safe_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if key not in _TRACE_ATTRIBUTES:
            continue
        if isinstance(value, str):
            safe[key] = " ".join(value.splitlines())[:256]
        elif isinstance(value, bool | int | float):
            safe[key] = value
    return safe


def _safe_context(context: SpanContext | None) -> SpanContext | None:
    if context is None:
        return None
    return SpanContext(
        context.trace_id,
        context.span_id,
        context.is_remote,
        context.trace_flags,
        TraceState(),
    )


def _safe_scope(scope: InstrumentationScope | None) -> InstrumentationScope | None:
    if scope is None:
        return None
    name = (
        scope.name if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", scope.name) else "unknown"
    )
    version = scope.version
    if version is not None and not re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}", version):
        version = None
    return InstrumentationScope(name=name, version=version)


def _sanitize_span(span: ReadableSpan) -> ReadableSpan:
    scope = (getattr(span.instrumentation_scope, "name", None) or "").lower()
    if "fastapi" in scope or "asgi" in scope:
        name = "http.server"
    elif "httpx" in scope:
        name = "http.client"
    elif scope.startswith("agentbox.") and _SAFE_NAME_RE.fullmatch(span.name):
        name = span.name[:128]
    else:
        name = "dependency.operation"
    resource = Resource(
        {
            key: value
            for key, value in span.resource.attributes.items()
            if key in _RESOURCE_ATTRIBUTES
        },
        schema_url=span.resource.schema_url,
    )
    status = Status(
        StatusCode.ERROR
        if span.status.status_code is StatusCode.ERROR
        else span.status.status_code
    )
    return ReadableSpan(
        name=name,
        context=_safe_context(span.context),
        parent=_safe_context(span.parent),
        resource=resource,
        attributes=_safe_attributes(span.attributes),
        events=tuple(
            Event(
                event.name if _SAFE_NAME_RE.fullmatch(event.name) else "span.event",
                attributes=_safe_attributes(event.attributes),
                timestamp=event.timestamp,
            )
            for event in span.events
        ),
        links=tuple(
            Link(_safe_context(link.context), _safe_attributes(link.attributes))
            for link in span.links
        ),
        kind=span.kind,
        instrumentation_scope=_safe_scope(span.instrumentation_scope),
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
    )


class SanitizingSpanExporter(SpanExporter):
    def __init__(self, delegate: SpanExporter) -> None:
        self._delegate = delegate

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        safe = []
        for span in spans:
            try:
                safe.append(_sanitize_span(span))
            except Exception:
                continue
        if not safe:
            return SpanExportResult.SUCCESS
        try:
            return self._delegate.export(tuple(safe))
        except Exception:
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self._delegate.shutdown()
        except Exception:
            return None


class SanitizingOtelLoggingHandler(LoggingHandler):
    def emit(self, record: logging.LogRecord) -> None:
        fields = getattr(record, "lemma_fields", {})
        event = (
            str(record.msg) if isinstance(fields, Mapping) else "dependency.reported"
        )
        if not _SAFE_NAME_RE.fullmatch(event):
            event = "dependency.reported"
        safe = logging.LogRecord(record.name, record.levelno, "", 0, event, (), None)
        if isinstance(fields, Mapping):
            for key, value in fields.items():
                if key not in _SAFE_LOG_FIELDS:
                    continue
                if isinstance(value, str):
                    setattr(safe, key, " ".join(value.splitlines())[:256])
                elif isinstance(value, bool | int | float):
                    setattr(safe, key, value)
        super().emit(safe)


def _span_exporter(signal: str) -> SpanExporter:
    endpoint = _signal_endpoint(signal)
    if endpoint is None:
        raise ValueError(f"{signal} exporter selected without an OTLP endpoint")
    if _signal_protocol(signal) == "http/protobuf":
        return HttpSpanExporter(endpoint=endpoint, headers=_signal_headers(signal))
    return GrpcSpanExporter(
        endpoint=endpoint,
        headers=_signal_headers(signal),
        insecure=endpoint.startswith("http://") or "://" not in endpoint,
    )


def _metric_exporter():
    endpoint = _signal_endpoint("metrics")
    if endpoint is None:
        raise ValueError("metrics exporter selected without an OTLP endpoint")
    if _signal_protocol("metrics") == "http/protobuf":
        return HttpMetricExporter(endpoint=endpoint, headers=_signal_headers("metrics"))
    return GrpcMetricExporter(
        endpoint=endpoint,
        headers=_signal_headers("metrics"),
        insecure=endpoint.startswith("http://") or "://" not in endpoint,
    )


def _log_exporter():
    endpoint = _signal_endpoint("logs")
    if endpoint is None:
        raise ValueError("logs exporter selected without an OTLP endpoint")
    if _signal_protocol("logs") == "http/protobuf":
        return HttpLogExporter(endpoint=endpoint, headers=_signal_headers("logs"))
    return GrpcLogExporter(
        endpoint=endpoint,
        headers=_signal_headers("logs"),
        insecure=endpoint.startswith("http://") or "://" not in endpoint,
    )


def init_telemetry() -> None:
    global _initialized, _logger_provider, _meter_provider, _trace_provider
    if _initialized or not settings.observability_enabled or settings.otel_sdk_disabled:
        return
    selected = _enabled_signals()
    for signal in selected:
        if _signal_endpoint(signal) is None:
            raise ValueError(f"{signal} exporter selected without an OTLP endpoint")
    if "traces" in selected:
        _trace_provider = TracerProvider(resource=_resource(), sampler=_sampler())
        _trace_provider.add_span_processor(
            BatchSpanProcessor(SanitizingSpanExporter(_span_exporter("traces")))
        )
        trace.set_tracer_provider(_trace_provider)
    if "metrics" in selected:
        reader = PeriodicExportingMetricReader(
            _metric_exporter(),
            export_interval_millis=settings.otel_metric_export_interval,
        )
        _meter_provider = MeterProvider(
            resource=_resource(),
            metric_readers=[reader],
            exemplar_filter=AlwaysOffExemplarFilter(),
            views=[View(instrument_name="*", attribute_keys=set(_METRIC_ATTRIBUTES))],
        )
        metrics.set_meter_provider(_meter_provider)
    if "logs" in selected:
        _logger_provider = LoggerProvider(resource=_resource())
        _logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(_log_exporter())
        )
        set_logger_provider(_logger_provider)
        logging.getLogger().addHandler(
            SanitizingOtelLoggingHandler(
                level=logging.NOTSET,
                logger_provider=_logger_provider,
            )
        )
    _initialized = True


def instrument_app(app: FastAPI) -> None:
    global _httpx_instrumented
    if not settings.observability_enabled or settings.otel_sdk_disabled:
        return
    init_telemetry()
    if id(app) not in _instrumented_apps:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=trace.get_tracer_provider(),
            meter_provider=metrics.get_meter_provider(),
            excluded_urls="/health,/health/live,/health/ready,/livez",
        )
        _instrumented_apps.add(id(app))
    if not _httpx_instrumented:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument(
            tracer_provider=trace.get_tracer_provider(),
            meter_provider=metrics.get_meter_provider(),
        )
        _httpx_instrumented = True


def shutdown_telemetry(timeout_millis: int = 5_000) -> None:
    for provider in (_trace_provider, _meter_provider, _logger_provider):
        if provider is None:
            continue
        try:
            provider.force_flush(timeout_millis=timeout_millis)
        except Exception:
            pass
        try:
            provider.shutdown()
        except Exception:
            pass
