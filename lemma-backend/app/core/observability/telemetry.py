from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import logging
from pathlib import Path
import re
import time
import traceback
from typing import Any

from fastapi import FastAPI
from opentelemetry._logs import NoOpLoggerProvider, set_logger_provider
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
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics._internal.exemplar.exemplar_filter import (
    AlwaysOffExemplarFilter,
)
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBased,
    Sampler,
    TraceIdRatioBased,
)
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Status, StatusCode
from opentelemetry.util.types import Attributes
from openinference.instrumentation.pydantic_ai import OpenInferenceSpanProcessor
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from pydantic_ai import Agent, InstrumentationSettings

from app.core.log.log import get_logger
from app.core.observability.span_sanitizer import (
    METRIC_ATTRIBUTE_KEYS,
    SanitizingSpanExporter,
)

logger = get_logger(__name__)

_telemetry_initialized = False
_libraries_instrumented = False
_instrumented_app_ids: set[int] = set()
_instrumented_engine_ids: set[int] = set()
_logs_initialized = False
_trace_provider: TracerProvider | None = None
_llm_trace_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None
_logger_provider: LoggerProvider | None = None
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
    configured = settings.otel_service_name or default_service_name
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", configured):
        return configured
    return default_service_name


def _build_resource(service_name: str) -> Resource:
    settings = _get_settings()
    attributes: dict[str, str] = {"service.name": service_name}
    if settings.otel_service_namespace and re.fullmatch(
        r"[A-Za-z0-9_.-]{1,128}", settings.otel_service_namespace
    ):
        attributes["service.namespace"] = settings.otel_service_namespace
    if settings.environment:
        attributes["deployment.environment"] = settings.environment
    # Release identity on the OTLP resource (mirrors the log context fields).
    # Present even when exporters are disabled so Phase 3 trace enablement is a
    # config flip, not a code change.
    from app.core.log.log import release_sha_for_resource

    attributes["service.version"] = release_sha_for_resource()
    return Resource.create(attributes)


def _build_sampler(
    settings,
    *,
    strategy: str | None = None,
    ratio: float | None = None,
) -> Sampler:
    """Build an exact standard OTel sampler; invalid values fail closed."""
    selected = (
        strategy if strategy is not None else settings.otel_traces_sampler
    ) or "parentbased_traceidratio"
    selected = selected.strip().lower()
    selected_ratio = float(settings.otel_traces_sampler_arg if ratio is None else ratio)
    if not 0.0 <= selected_ratio <= 1.0:
        raise ValueError("OTEL trace sampler ratio must be between 0 and 1")
    samplers: dict[str, Sampler] = {
        "always_on": ALWAYS_ON,
        "always_off": ALWAYS_OFF,
        "traceidratio": TraceIdRatioBased(selected_ratio),
        "parentbased_always_on": ParentBased(ALWAYS_ON),
        "parentbased_always_off": ParentBased(ALWAYS_OFF),
        "parentbased_traceidratio": ParentBased(TraceIdRatioBased(selected_ratio)),
    }
    try:
        return samplers[selected]
    except KeyError as exc:
        raise ValueError(f"unsupported OTEL trace sampler: {selected}") from exc


def _endpoint_is_insecure(endpoint: str) -> bool:
    return endpoint.startswith("http://") or "://" not in endpoint


def _normalize_otlp_protocol(protocol: str | None) -> str:
    if not protocol:
        return "grpc"
    normalized = protocol.strip().lower()
    if normalized in {"http", "http/protobuf"}:
        return "http/protobuf"
    if normalized == "grpc":
        return "grpc"
    raise ValueError(f"unsupported OTLP protocol: {normalized}")


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


_SIGNALS = frozenset({"traces", "metrics", "logs"})


def _legacy_enabled_signals(raw: str | None) -> set[str]:
    selected = {part.strip().lower() for part in (raw or "").split(",") if part.strip()}
    if not selected:
        return {"traces"}
    invalid = selected - _SIGNALS
    if invalid:
        raise ValueError("OTEL_SIGNALS contains unsupported signals")
    return selected


def _enabled_signals() -> set[str]:
    """Resolve standard per-signal selectors over the legacy OTEL_SIGNALS alias."""
    settings = _get_settings()
    fields_set = getattr(settings, "model_fields_set", set())
    legacy_configured = settings.otel_signals is not None
    selected = (
        _legacy_enabled_signals(settings.otel_signals)
        if legacy_configured
        else {"traces"}
    )
    for signal in _SIGNALS:
        field = f"otel_{signal}_exporter"
        # Standard selectors always win when explicitly supplied. Otherwise a
        # configured legacy selector controls all signals; with neither, the
        # safe defaults are traces=otlp and metrics/logs=none.
        if field in fields_set or not legacy_configured:
            exporter = str(getattr(settings, field)).strip().lower()
            if exporter == "otlp":
                selected.add(signal)
            elif exporter == "none":
                selected.discard(signal)
            else:
                raise ValueError(f"unsupported {field}: {exporter}")
    return selected


def _signal_enabled(signal: str) -> bool:
    return signal in _enabled_signals()


def _signal_protocol(signal: str) -> str:
    settings = _get_settings()
    specific = getattr(settings, f"otel_exporter_otlp_{signal}_protocol")
    return _normalize_otlp_protocol(specific or settings.otel_exporter_otlp_protocol)


def _otlp_signal_endpoint(signal: str) -> str | None:
    """Resolve standard signal endpoints without adding HTTP paths to gRPC."""
    settings = _get_settings()
    specific = getattr(settings, f"otel_exporter_otlp_{signal}_endpoint")
    if specific:
        return specific
    base = settings.otel_exporter_otlp_endpoint
    if not base:
        return None
    if _signal_protocol(signal) == "grpc":
        return base
    return f"{base.rstrip('/')}/v1/{signal}"


def _otlp_signal_headers(signal: str) -> dict[str, str] | None:
    settings = _get_settings()
    specific = getattr(settings, f"otel_exporter_otlp_{signal}_headers")
    return _parse_otlp_headers(specific or settings.otel_exporter_otlp_headers)


def _llm_otlp_headers() -> dict[str, str] | None:
    settings = _get_settings()
    return _parse_otlp_headers(settings.llm_otel_exporter_otlp_headers)


def _setup_tracing(service_name: str) -> TracerProvider | None:
    settings = _get_settings()
    if not _signal_enabled("traces"):
        return None
    traces_endpoint = _otlp_signal_endpoint("traces")
    if not traces_endpoint:
        return None
    provider = TracerProvider(
        resource=_build_resource(service_name),
        sampler=_build_sampler(settings),
    )

    provider.add_span_processor(AgentRunSpanEnricher())

    provider.add_span_processor(
        BatchSpanProcessor(
            SanitizingSpanExporter(
                _build_span_exporter(
                    traces_endpoint,
                    protocol=_signal_protocol("traces"),
                    headers=_otlp_signal_headers("traces"),
                ),
            )
        )
    )
    trace.set_tracer_provider(provider)
    return provider


def _setup_llm_tracing(service_name: str) -> TracerProvider | None:
    settings = _get_settings()
    endpoint = settings.llm_otel_exporter_otlp_endpoint
    if not settings.llm_otel_enabled or not endpoint:
        return None
    provider = TracerProvider(
        resource=_build_resource(service_name),
        sampler=_build_sampler(
            settings,
            strategy=settings.llm_otel_traces_sampler,
            ratio=settings.llm_otel_traces_sampler_arg,
        ),
    )
    provider.add_span_processor(AgentRunSpanEnricher())
    provider.add_span_processor(OpenInferenceSpanProcessor(span_filter=_is_llm_span))
    provider.add_span_processor(
        BatchSpanProcessor(
            FilteringSpanExporter(
                SanitizingSpanExporter(
                    _build_span_exporter(
                        endpoint,
                        protocol=settings.llm_otel_exporter_otlp_protocol,
                        headers=_llm_otlp_headers(),
                    ),
                    llm=True,
                ),
                _is_llm_span,
            )
        )
    )
    Agent.instrument_all(
        InstrumentationSettings(
            tracer_provider=provider,
            meter_provider=NoOpMeterProvider(),
            logger_provider=NoOpLoggerProvider(),
            include_content=False,
            include_binary_content=False,
            version=2,
            event_mode="attributes",
            use_aggregated_usage_attribute_names=True,
        )
    )
    return provider


def _setup_metrics(service_name: str) -> MeterProvider | None:
    settings = _get_settings()
    readers: list[PeriodicExportingMetricReader] = []

    if not _signal_enabled("metrics"):
        return None
    metrics_endpoint = _otlp_signal_endpoint("metrics")
    if metrics_endpoint:
        readers.append(
            PeriodicExportingMetricReader(
                _build_metric_exporter(
                    metrics_endpoint,
                    protocol=_signal_protocol("metrics"),
                    headers=_otlp_signal_headers("metrics"),
                ),
                export_interval_millis=settings.observability_metrics_export_interval_millis,
            )
        )

    if not readers:
        return None

    provider = MeterProvider(
        resource=_build_resource(service_name),
        metric_readers=readers,
        exemplar_filter=AlwaysOffExemplarFilter(),
        views=[View(instrument_name="*", attribute_keys=set(METRIC_ATTRIBUTE_KEYS))],
    )
    metrics.set_meter_provider(provider)
    return provider


_SAFE_OTEL_LOG_FIELDS = frozenset(
    {
        "request_id",
        "correlation_id",
        "event_id",
        "causation_id",
        "job_id",
        "event_type",
        "consumer",
        "task_name",
        "job_attempt",
        "attempt",
        "outcome",
        "duration_ms",
        "incident_duration_ms",
        "failure_count",
        "count",
        "method",
        "route",
        "status_code",
        "latency_kind",
        "error_type",
        "error_code",
        "error_stack_hash",
        "retryable",
    }
)


class SanitizingLoggingHandler(LoggingHandler):
    """Translate only bounded structured fields into OTLP log records."""

    def emit(self, record: logging.LogRecord) -> None:
        candidate = record.msg if isinstance(record.msg, Mapping) else {}
        event = candidate.get("event") if isinstance(candidate, Mapping) else None
        if not isinstance(event, str) or len(event) > 128:
            event = (
                str(record.msg)
                if isinstance(record.msg, str) and "." in record.msg
                else "dependency.reported"
            )
        safe_record = logging.LogRecord(
            name=record.name,
            level=record.levelno,
            pathname="",
            lineno=0,
            msg=event[:128],
            args=(),
            exc_info=None,
        )
        source_fields: dict[str, Any] = {}
        if isinstance(candidate, Mapping):
            source_fields.update(candidate)
        lemma_fields = getattr(record, "lemma_fields", None)
        if isinstance(lemma_fields, Mapping):
            source_fields.update(lemma_fields)
        for key, value in source_fields.items():
            if key not in _SAFE_OTEL_LOG_FIELDS:
                continue
            if isinstance(value, str):
                setattr(safe_record, key, " ".join(value.splitlines())[:256])
            elif isinstance(value, bool | int | float):
                setattr(safe_record, key, value)
        super().emit(safe_record)


def _setup_logs(service_name: str) -> LoggerProvider | None:
    global _logs_initialized
    if _logs_initialized:
        return _logger_provider

    if not _signal_enabled("logs"):
        return None
    logs_endpoint = _otlp_signal_endpoint("logs")
    if not logs_endpoint:
        return None
    provider = LoggerProvider(resource=_build_resource(service_name))
    provider.add_log_record_processor(
        BatchLogRecordProcessor(
            _build_log_exporter(
                logs_endpoint,
                protocol=_signal_protocol("logs"),
                headers=_otlp_signal_headers("logs"),
            )
        )
    )
    set_logger_provider(provider)
    logging.getLogger().addHandler(
        SanitizingLoggingHandler(level=logging.NOTSET, logger_provider=provider)
    )
    _logs_initialized = True
    return provider


def _instrument_libraries() -> None:
    global _libraries_instrumented
    if _libraries_instrumented:
        return

    from opentelemetry.instrumentation.aiohttp_client import (
        AioHttpClientInstrumentor,
    )
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    AioHttpClientInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
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


def _validate_telemetry_config() -> None:
    settings = _get_settings()
    if settings.observability_enabled:
        selected = _enabled_signals()
        for signal in selected:
            _signal_protocol(signal)
            if not _otlp_signal_endpoint(signal):
                raise ValueError(f"{signal} exporter selected without an OTLP endpoint")
        if "traces" in selected:
            _build_sampler(settings)
    if settings.llm_otel_enabled:
        if not settings.llm_otel_exporter_otlp_endpoint:
            raise ValueError("LLM OTEL is enabled without an OTLP endpoint")
        _normalize_otlp_protocol(settings.llm_otel_exporter_otlp_protocol)
        _build_sampler(
            settings,
            strategy=settings.llm_otel_traces_sampler,
            ratio=settings.llm_otel_traces_sampler_arg,
        )


def init_telemetry(service_name: str = "lemma-api") -> None:
    global _logger_provider
    global _llm_trace_provider
    global _meter_provider
    global _telemetry_initialized
    global _trace_provider
    if _telemetry_initialized:
        return

    settings = _get_settings()
    if settings.otel_sdk_disabled or not (
        settings.observability_enabled or settings.llm_otel_enabled
    ):
        return

    resolved_service_name = _resolve_service_name(service_name)
    _validate_telemetry_config()
    try:
        _quiet_otlp_export_logs()
        if settings.observability_enabled:
            _trace_provider = _setup_tracing(resolved_service_name)
            _meter_provider = _setup_metrics(resolved_service_name)
            _logger_provider = _setup_logs(resolved_service_name)
        _llm_trace_provider = _setup_llm_tracing(resolved_service_name)
        if _trace_provider is not None or _meter_provider is not None:
            _instrument_libraries()
    except Exception as exc:
        logger.debug(
            "observability.telemetry.observability_setup_continuing_without_otel.diagnostic",
            error_type=type(exc).__name__,
        )
    _telemetry_initialized = True


def shutdown_telemetry(timeout_millis: int = 5_000) -> None:
    """Flush and stop every owned provider without delaying process shutdown."""
    global _logger_provider
    global _llm_trace_provider
    global _meter_provider
    global _trace_provider
    providers = (
        _llm_trace_provider,
        _trace_provider,
        _meter_provider,
        _logger_provider,
    )
    for provider in providers:
        if provider is None:
            continue
        try:
            force_flush = getattr(provider, "force_flush", None)
            if callable(force_flush):
                force_flush(timeout_millis=timeout_millis)
        except Exception:
            pass
        try:
            provider.shutdown()
        except Exception:
            pass
    _llm_trace_provider = None
    _trace_provider = None
    _meter_provider = None
    _logger_provider = None


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
    settings = _get_settings()
    if not settings.observability_enabled or settings.otel_sdk_disabled:
        return
    app_id = id(app)
    if app_id in _instrumented_app_ids:
        return

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    _patch_fastapi_route_details()
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=trace.get_tracer_provider(),
        meter_provider=metrics.get_meter_provider(),
        excluded_urls="/health,/health/live,/health/ready,/livez",
    )
    _instrumented_app_ids.add(app_id)


def instrument_database_engine(engine: Any) -> None:
    settings = _get_settings()
    if not settings.observability_enabled or settings.otel_sdk_disabled:
        return
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

    frames = traceback.extract_tb(exc.__traceback__)[-8:] if exc.__traceback__ else []
    descriptors = [
        f"{Path(frame.filename).stem}:{frame.name}:{frame.lineno}" for frame in frames
    ]
    fingerprint = "|".join([type(exc).__name__, *descriptors])
    safe_attributes: dict[str, Any] = {
        "error.type": type(exc).__name__,
        "error.stack_hash": hashlib.sha256(fingerprint.encode()).hexdigest(),
    }
    if descriptors:
        safe_attributes["error.frames"] = descriptors
    if attributes:
        safe_attributes.update(attributes)
    span.add_event("exception", attributes=safe_attributes)
    if attributes:
        for key, value in attributes.items():
            span.set_attribute(key, value)
    if mark_span_as_error:
        span.set_status(Status(StatusCode.ERROR, type(exc).__name__))


def get_current_trace_context() -> dict[str, str]:
    span = trace.get_current_span()
    span_context = span.get_span_context() if span else None
    if not span_context or not span_context.is_valid:
        return {}
    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
    }
