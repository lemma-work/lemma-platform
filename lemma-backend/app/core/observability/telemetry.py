from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import time
from typing import Any

from fastapi import FastAPI
from opentelemetry._logs import set_logger_provider
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter as GrpcOTLPLogExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter as GrpcOTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcOTLPSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http._log_exporter import (
    OTLPLogExporter as HttpOTLPLogExporter,
)
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter as HttpOTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpOTLPSpanExporter,
)
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Status, StatusCode
from opentelemetry.util.types import Attributes
from openinference.instrumentation.pydantic_ai import OpenInferenceSpanProcessor
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from pydantic_ai.agent import Agent

from app.core.log.log import get_logger

logger = get_logger(__name__)

_telemetry_initialized = False
_libraries_instrumented = False
_instrumented_app_ids: set[int] = set()
_instrumented_engine_ids: set[int] = set()
_logs_initialized = False
_agent_run_context: ContextVar[dict[str, str]] = ContextVar(
    "agent_run_context",
    default={},
)

_PHOENIX_KINDS = {
    OpenInferenceSpanKindValues.AGENT.value,
    OpenInferenceSpanKindValues.CHAIN.value,
    OpenInferenceSpanKindValues.EMBEDDING.value,
    OpenInferenceSpanKindValues.GUARDRAIL.value,
    OpenInferenceSpanKindValues.LLM.value,
    OpenInferenceSpanKindValues.PROMPT.value,
    OpenInferenceSpanKindValues.RERANKER.value,
    OpenInferenceSpanKindValues.RETRIEVER.value,
    OpenInferenceSpanKindValues.TOOL.value,
}


class FilteringSpanExporter(SpanExporter):
    """Delegate exporter that only forwards spans matching a predicate."""

    def __init__(
        self,
        delegate: SpanExporter,
        span_filter: Callable[[ReadableSpan], bool],
    ) -> None:
        self._delegate = delegate
        self._span_filter = span_filter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        filtered = tuple(span for span in spans if self._span_filter(span))
        if not filtered:
            return SpanExportResult.SUCCESS
        return self._delegate.export(filtered)

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        force_flush = getattr(self._delegate, "force_flush", None)
        if callable(force_flush):
            return bool(force_flush(timeout_millis))
        return True


class AgentRunSpanEnricher(SpanProcessor):
    """Attach conversation/run metadata to spans created during an agent run."""

    def on_start(self, span: Any, parent_context: Any | None = None) -> None:
        del parent_context
        attributes = _agent_run_context.get()
        if not attributes:
            return
        for key, value in attributes.items():
            span.set_attribute(key, value)

    def on_end(self, span: ReadableSpan) -> None:
        del span

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        del timeout_millis
        return True


@contextmanager
def agent_run_telemetry_context(
    *,
    conversation_id: Any,
    agent_run_id: Any,
    agent_id: Any | None = None,
    pod_id: Any | None = None,
    organization_id: Any | None = None,
    user_id: Any | None = None,
    agent_name: str | None = None,
    harness_kind: str | None = None,
    model_name: str | None = None,
):
    attributes = {
        "lemma.conversation_id": str(conversation_id),
        "lemma.agent_run_id": str(agent_run_id),
    }
    optional_attributes = {
        "lemma.agent_id": agent_id,
        "lemma.pod_id": pod_id,
        "lemma.organization_id": organization_id,
        "lemma.user_id": user_id,
        "lemma.agent_name": agent_name,
        "lemma.harness_kind": harness_kind,
        "lemma.model_name": model_name,
    }
    for key, value in optional_attributes.items():
        if value is not None:
            attributes[key] = str(value)

    token = _agent_run_context.set(attributes)
    try:
        yield attributes
    finally:
        _agent_run_context.reset(token)


def _get_settings():
    from app.core.config import settings

    return settings


def _resolve_service_name(default_service_name: str) -> str:
    settings = _get_settings()
    return settings.otel_service_name or default_service_name


def _build_resource(service_name: str) -> Resource:
    settings = _get_settings()
    attributes: dict[str, str] = {"service.name": service_name}
    if settings.otel_service_namespace:
        attributes["service.namespace"] = settings.otel_service_namespace
    if settings.environment:
        attributes["deployment.environment"] = settings.environment
    return Resource.create(attributes)


def _endpoint_is_insecure(endpoint: str) -> bool:
    return endpoint.startswith("http://") or "://" not in endpoint


def _normalize_otlp_protocol(protocol: str | None) -> str:
    if not protocol:
        return "grpc"
    normalized = protocol.strip().lower()
    if normalized in {"http", "http/protobuf", "http_proto", "http-protobuf"}:
        return "http/protobuf"
    return "grpc"


def _build_span_exporter(
    endpoint: str,
    *,
    protocol: str,
    headers: dict[str, str] | None = None,
) -> SpanExporter:
    normalized_protocol = _normalize_otlp_protocol(protocol)
    if normalized_protocol == "http/protobuf":
        return HttpOTLPSpanExporter(endpoint=endpoint, headers=headers)
    return GrpcOTLPSpanExporter(
        endpoint=endpoint,
        headers=headers,
        insecure=_endpoint_is_insecure(endpoint),
    )


def _build_metric_exporter(
    endpoint: str,
    *,
    protocol: str,
    headers: dict[str, str] | None = None,
):
    normalized_protocol = _normalize_otlp_protocol(protocol)
    if normalized_protocol == "http/protobuf":
        return HttpOTLPMetricExporter(endpoint=endpoint, headers=headers)
    return GrpcOTLPMetricExporter(
        endpoint=endpoint,
        headers=headers,
        insecure=_endpoint_is_insecure(endpoint),
    )


def _build_log_exporter(
    endpoint: str,
    *,
    protocol: str,
    headers: dict[str, str] | None = None,
):
    normalized_protocol = _normalize_otlp_protocol(protocol)
    if normalized_protocol == "http/protobuf":
        return HttpOTLPLogExporter(endpoint=endpoint, headers=headers)
    return GrpcOTLPLogExporter(
        endpoint=endpoint,
        headers=headers,
        insecure=_endpoint_is_insecure(endpoint),
    )


def _is_llm_span(span: ReadableSpan) -> bool:
    kind = span.attributes.get(SpanAttributes.OPENINFERENCE_SPAN_KIND)
    return isinstance(kind, str) and kind in _PHOENIX_KINDS


def _parse_otlp_headers(raw_headers: str | None) -> dict[str, str] | None:
    if not raw_headers:
        return None
    headers: dict[str, str] = {}
    for raw_header in raw_headers.split(","):
        if "=" not in raw_header:
            continue
        key, value = raw_header.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            headers[key] = value
    return headers or None


def _otlp_headers() -> dict[str, str] | None:
    """OTLP headers applied to every signal (traces, metrics, logs)."""
    return _parse_otlp_headers(_get_settings().otel_exporter_otlp_headers)


def _otlp_endpoint() -> str | None:
    """Single OTLP endpoint shared by traces, metrics, and logs."""
    return _get_settings().otel_exporter_otlp_endpoint


def _otlp_signal_endpoint(signal: str) -> str | None:
    """Return the full OTLP endpoint for a signal by appending its path to the base URL.

    The HTTP exporters do not auto-append signal paths when endpoint is passed
    explicitly, so we must include the path ourselves.
    """
    base = _otlp_endpoint()
    if not base:
        return None
    path = {"traces": "/v1/traces", "metrics": "/v1/metrics", "logs": "/v1/logs"}.get(
        signal, f"/v1/{signal}"
    )
    return base.rstrip("/") + path


def _enabled_signals() -> set[str]:
    raw = _get_settings().otel_signals or ""
    selected = {part.strip().lower() for part in raw.split(",") if part.strip()}
    # Empty/unset means export everything — the default once an endpoint is
    # given. A non-empty subset (e.g. "traces") narrows what gets exported.
    return selected or {"traces", "metrics", "logs"}


def _signal_enabled(signal: str) -> bool:
    return signal in _enabled_signals()


def _llm_otlp_headers() -> dict[str, str] | None:
    settings = _get_settings()
    return _parse_otlp_headers(settings.llm_otel_exporter_otlp_headers)


def _setup_tracing(service_name: str) -> None:
    settings = _get_settings()
    provider = TracerProvider(resource=_build_resource(service_name))

    provider.add_span_processor(AgentRunSpanEnricher())

    traces_endpoint = _otlp_signal_endpoint("traces")
    if traces_endpoint and _signal_enabled("traces"):
        provider.add_span_processor(
            BatchSpanProcessor(
                _build_span_exporter(
                    traces_endpoint,
                    protocol=settings.otel_exporter_otlp_protocol,
                    headers=_otlp_headers(),
                )
            )
        )
        logger.info(
            "Starting OTEL trace export",
            endpoint=traces_endpoint,
            protocol=_normalize_otlp_protocol(settings.otel_exporter_otlp_protocol),
        )

    if (
        settings.llm_otel_enabled
        and settings.llm_otel_exporter_otlp_endpoint
    ):
        provider.add_span_processor(
            OpenInferenceSpanProcessor(span_filter=_is_llm_span)
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                FilteringSpanExporter(
                    _build_span_exporter(
                        settings.llm_otel_exporter_otlp_endpoint,
                        protocol=settings.llm_otel_exporter_otlp_protocol,
                        headers=_llm_otlp_headers(),
                    ),
                    _is_llm_span,
                )
            )
        )
        logger.info(
            "Starting LLM OTEL trace export",
            endpoint=settings.llm_otel_exporter_otlp_endpoint,
            protocol=_normalize_otlp_protocol(
                settings.llm_otel_exporter_otlp_protocol
            ),
        )

    trace.set_tracer_provider(provider)


def _setup_metrics(service_name: str) -> None:
    settings = _get_settings()
    readers: list[PeriodicExportingMetricReader] = []

    metrics_endpoint = _otlp_signal_endpoint("metrics")
    if metrics_endpoint and _signal_enabled("metrics"):
        readers.append(
            PeriodicExportingMetricReader(
                _build_metric_exporter(
                    metrics_endpoint,
                    protocol=settings.otel_exporter_otlp_protocol,
                    headers=_otlp_headers(),
                ),
                export_interval_millis=settings.observability_metrics_export_interval_millis,
            )
        )
        logger.info(
            "Starting OTEL metric export",
            endpoint=metrics_endpoint,
            protocol=_normalize_otlp_protocol(settings.otel_exporter_otlp_protocol),
        )

    if not readers:
        return

    provider = MeterProvider(
        resource=_build_resource(service_name),
        metric_readers=readers,
    )
    metrics.set_meter_provider(provider)


def _setup_logs(service_name: str) -> None:
    global _logs_initialized
    if _logs_initialized:
        return

    settings = _get_settings()
    logs_endpoint = _otlp_signal_endpoint("logs")
    if not logs_endpoint or not _signal_enabled("logs"):
        return
    provider = LoggerProvider(resource=_build_resource(service_name))
    provider.add_log_record_processor(
        BatchLogRecordProcessor(
            _build_log_exporter(
                logs_endpoint,
                protocol=settings.otel_exporter_otlp_protocol,
                headers=_otlp_headers(),
            )
        )
    )
    set_logger_provider(provider)
    logging.getLogger().addHandler(
        LoggingHandler(level=logging.NOTSET, logger_provider=provider)
    )
    _logs_initialized = True
    logger.info(
        "Starting OTEL log export",
        endpoint=logs_endpoint,
        protocol=_normalize_otlp_protocol(settings.otel_exporter_otlp_protocol),
    )


def _instrument_libraries() -> None:
    global _libraries_instrumented
    if _libraries_instrumented:
        return

    from opentelemetry.instrumentation.aiohttp_client import (
        AioHttpClientInstrumentor,
    )
    from opentelemetry.instrumentation.redis import RedisInstrumentor

    AioHttpClientInstrumentor().instrument()
    RedisInstrumentor().instrument()
    Agent.instrument_all(True)
    _libraries_instrumented = True


class _RateLimitedLogFilter(logging.Filter):
    """Collapse repeated OTLP exporter failures to one line per interval.

    The OTLP exporters log on every failed/retried export; when a collector is
    down or not yet serving this floods the dev logs. We keep the first
    occurrence of each distinct message, then suppress repeats for `interval`.
    """

    def __init__(self, interval_seconds: float = 60.0) -> None:
        super().__init__()
        self._interval = interval_seconds
        self._last_emit: dict[str, float] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        key = f"{record.name}:{message[:48]}"
        now = time.monotonic()
        last = self._last_emit.get(key)
        if last is not None and (now - last) < self._interval:
            return False
        self._last_emit[key] = now
        return True


# OTLP exporter modules that emit the noisy "Transient error ... retrying" and
# "Failed to export ..." lines when a collector is unreachable.
_OTLP_EXPORTER_LOGGERS = (
    "opentelemetry.exporter.otlp.proto.grpc.exporter",
    "opentelemetry.exporter.otlp.proto.grpc._log_exporter",
    "opentelemetry.exporter.otlp.proto.grpc.metric_exporter",
    "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    "opentelemetry.exporter.otlp.proto.http._log_exporter",
    "opentelemetry.exporter.otlp.proto.http.metric_exporter",
)

_otlp_log_filter = _RateLimitedLogFilter()
_otlp_logs_quieted = False


def _quiet_otlp_export_logs() -> None:
    """Rate-limit OTLP exporter failure logs so a down collector can't spam."""
    global _otlp_logs_quieted
    if _otlp_logs_quieted:
        return
    for name in _OTLP_EXPORTER_LOGGERS:
        logging.getLogger(name).addFilter(_otlp_log_filter)
    _otlp_logs_quieted = True


def init_telemetry(service_name: str = "lemma-api") -> None:
    global _telemetry_initialized
    if _telemetry_initialized:
        return

    settings = _get_settings()
    if not settings.observability_enabled:
        logger.info("Observability disabled, skipping OTEL setup")
        return

    resolved_service_name = _resolve_service_name(service_name)
    try:
        _quiet_otlp_export_logs()
        _setup_tracing(resolved_service_name)
        _setup_metrics(resolved_service_name)
        _setup_logs(resolved_service_name)
        _instrument_libraries()
    except Exception as exc:
        logger.warning(
            "Observability setup failed; continuing without OTEL",
            error_type=type(exc).__name__,
        )
    _telemetry_initialized = True


_fastapi_route_details_patched = False


def _patch_fastapi_route_details() -> None:
    """Make ``opentelemetry-instrumentation-fastapi`` tolerate FastAPI 0.137+.

    FastAPI 0.137+ adds ``_IncludedRouter`` entries (which have no ``.path``) to
    ``app.routes``. The instrumentation's ``_get_route_details`` reads
    ``route.path`` on a ``Match.PARTIAL`` without guarding, so any request that
    partial-matches such a route — notably CORS ``OPTIONS`` preflights — raises
    ``AttributeError`` and 500s (instrumentation ≤0.63b1). Wrap the module-level
    helper to swallow that error (the span just loses its route template) until
    the fix lands upstream. ``_get_default_span_details`` calls this via the
    module global, so patching the module attribute covers every call site.
    """
    global _fastapi_route_details_patched
    if _fastapi_route_details_patched:
        return
    from opentelemetry.instrumentation import fastapi as _otel_fastapi

    original = _otel_fastapi._get_route_details

    def _safe_get_route_details(scope: Any):
        try:
            return original(scope)
        except AttributeError:
            return None

    _otel_fastapi._get_route_details = _safe_get_route_details
    _fastapi_route_details_patched = True


def instrument_fastapi_app(app: FastAPI) -> None:
    app_id = id(app)
    if app_id in _instrumented_app_ids:
        return

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    _patch_fastapi_route_details()
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=trace.get_tracer_provider(),
        meter_provider=metrics.get_meter_provider(),
        excluded_urls="/health",
    )
    _instrumented_app_ids.add(app_id)


def instrument_database_engine(engine: Any) -> None:
    engine_to_instrument = getattr(engine, "sync_engine", engine)
    engine_id = id(engine_to_instrument)
    if engine_id in _instrumented_engine_ids:
        return

    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(
        engine=engine_to_instrument,
        tracer_provider=trace.get_tracer_provider(),
        meter_provider=metrics.get_meter_provider(),
    )
    _instrumented_engine_ids.add(engine_id)


def record_exception_on_current_span(
    exc: BaseException,
    *,
    attributes: Attributes | None = None,
    mark_span_as_error: bool = True,
) -> None:
    span = trace.get_current_span()
    if not span or not span.is_recording():
        return

    span.record_exception(exc, attributes=attributes)
    if attributes:
        for key, value in attributes.items():
            span.set_attribute(key, value)
    if mark_span_as_error:
        span.set_status(Status(StatusCode.ERROR, str(exc)))


def get_current_trace_context() -> dict[str, str]:
    span = trace.get_current_span()
    span_context = span.get_span_context() if span else None
    if not span_context or not span_context.is_valid:
        return {}
    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
    }
