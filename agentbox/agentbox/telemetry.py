"""Privacy-safe OpenTelemetry bootstrap and AgentBox operation instruments.

The module owns only standard OpenTelemetry APIs. Export destinations and
credentials remain deployment configuration. All exported spans are sanitized
through a default-deny allow-list so framework upgrades cannot start exporting
raw paths, sandbox identifiers, provider responses, or customer payloads.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
import re
import threading
import time
from typing import Any, ParamSpec, TypeVar

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter as GrpcOTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcOTLPSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter as HttpOTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpOTLPSpanExporter,
)
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.metrics import Observation
from opentelemetry.propagate import set_global_textmap
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
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
    Sampler,
    TraceIdRatioBased,
)
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import (
    Link,
    SpanContext,
    Status,
    StatusCode,
    TraceState,
)

from agentbox.config import settings
from agentbox.domain import AgentBoxError, ErrorCode, SandboxProfileRef, WorkloadKind


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SAFE_IDENTITY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SAFE_PROVIDERS = frozenset({"docker", "e2b"})
_SAFE_OPERATIONS = frozenset({"ensure", "inspect", "release", "destroy"})
_SAFE_PHASES = frozenset({"admission_wait", "sandbox_create", "sandbox_readiness"})
_SAFE_CONTROL_OPERATIONS = frozenset({"cleanup", "reconcile"})
_HEALTH_PATHS = "/health,/health/live,/health/ready,/livez"
_INSTRUMENTATION_VERSION = "1"
_DURATION_BOUNDARIES_MS = (
    1,
    5,
    10,
    25,
    50,
    100,
    250,
    500,
    1_000,
    2_500,
    5_000,
    10_000,
    30_000,
    60_000,
)

_initialized = False
_httpx_instrumented = False
_instrumented_app_ids: set[int] = set()
_trace_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None
_tracer = trace.get_tracer("agentbox.telemetry", _INSTRUMENTATION_VERSION)
_instruments: _AgentBoxInstruments | None = None
_terminal_operation_error: ContextVar[bool] = ContextVar(
    "agentbox_terminal_operation_error",
    default=False,
)


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


def _signal_protocol(signal: str) -> str:
    specific = getattr(settings, f"otel_exporter_otlp_{signal}_protocol")
    return _normalize_protocol(specific or settings.otel_exporter_otlp_protocol)


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


def _parse_headers(raw: str | None) -> dict[str, str] | None:
    if not raw:
        return None
    headers: dict[str, str] = {}
    for item in raw.split(","):
        key, separator, value = item.partition("=")
        if separator and key.strip() and value.strip():
            headers[key.strip()] = value.strip()
    return headers or None


def _signal_headers(signal: str) -> dict[str, str] | None:
    specific = getattr(settings, f"otel_exporter_otlp_{signal}_headers")
    return _parse_headers(specific or settings.otel_exporter_otlp_headers)


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


def _endpoint_is_insecure(endpoint: str) -> bool:
    return endpoint.startswith("http://") or "://" not in endpoint


def _span_exporter(endpoint: str) -> SpanExporter:
    protocol = _signal_protocol("traces")
    headers = _signal_headers("traces")
    if protocol == "http/protobuf":
        return HttpOTLPSpanExporter(endpoint=endpoint, headers=headers)
    return GrpcOTLPSpanExporter(
        endpoint=endpoint,
        headers=headers,
        insecure=_endpoint_is_insecure(endpoint),
    )


def _metric_exporter(endpoint: str):
    protocol = _signal_protocol("metrics")
    headers = _signal_headers("metrics")
    if protocol == "http/protobuf":
        return HttpOTLPMetricExporter(endpoint=endpoint, headers=headers)
    return GrpcOTLPMetricExporter(
        endpoint=endpoint,
        headers=headers,
        insecure=_endpoint_is_insecure(endpoint),
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

METRIC_ATTRIBUTE_KEYS = frozenset(
    {
        "operation",
        "workload_kind",
        "provider",
        "profile",
        "phase",
        "outcome",
        "reason",
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
    def __init__(self, delegate: SpanExporter) -> None:
        self._delegate = delegate

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        safe: list[ReadableSpan] = []
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

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        flush = getattr(self._delegate, "force_flush", None)
        if not callable(flush):
            return True
        try:
            return bool(flush(timeout_millis))
        except Exception:
            return False


class _AgentBoxInstruments:
    def __init__(self, meter: Any) -> None:
        self.operations = meter.create_counter(
            "lemma.agentbox.operations",
            unit="{operation}",
            description="Completed AgentBox operations",
        )
        self.operation_duration = meter.create_histogram(
            "lemma.agentbox.operation.duration",
            unit="ms",
            description="AgentBox operation duration",
            explicit_bucket_boundaries_advisory=_DURATION_BOUNDARIES_MS,
        )
        self.active = meter.create_up_down_counter(
            "lemma.agentbox.active",
            unit="{operation}",
            description="AgentBox operations currently active",
        )
        self.admission_wait = meter.create_histogram(
            "lemma.agentbox.admission_wait.duration",
            unit="ms",
            description="Provider admission decision latency",
            explicit_bucket_boundaries_advisory=_DURATION_BOUNDARIES_MS,
        )
        self.sandbox_start = meter.create_histogram(
            "lemma.agentbox.sandbox_start.duration",
            unit="ms",
            description="Sandbox create and readiness phase latency",
            explicit_bucket_boundaries_advisory=_DURATION_BOUNDARIES_MS,
        )
        self.rejections = meter.create_counter(
            "lemma.agentbox.rejections",
            unit="{rejection}",
            description="Rejected AgentBox operations",
        )
        self.timeouts = meter.create_counter(
            "lemma.agentbox.timeouts",
            unit="{timeout}",
            description="Timed out AgentBox operations",
        )
        self.cleanup = meter.create_counter(
            "lemma.agentbox.cleanup.operations",
            unit="{operation}",
            description="AgentBox cleanup pass outcomes",
        )
        self.reconcile = meter.create_counter(
            "lemma.agentbox.reconcile.operations",
            unit="{operation}",
            description="AgentBox reconciliation pass outcomes",
        )
        self._capacity_lock = threading.Lock()
        self._capacity: dict[str, tuple[int, int]] = {}
        meter.create_observable_gauge(
            "lemma.agentbox.capacity.limit",
            callbacks=[self._observe_capacity_limit],
            unit="{sandbox}",
            description="Configured provider allocation limit",
        )
        meter.create_observable_gauge(
            "lemma.agentbox.capacity.available",
            callbacks=[self._observe_capacity_available],
            unit="{sandbox}",
            description="Provider allocation capacity currently available",
        )

    def record_capacity(
        self, *, provider: str, limit: int, active: int, reserved: int
    ) -> None:
        with self._capacity_lock:
            self._capacity[_bounded_provider(provider)] = (
                max(0, limit),
                max(0, limit - active - reserved),
            )

    def _capacity_snapshot(self) -> tuple[tuple[str, tuple[int, int]], ...]:
        with self._capacity_lock:
            return tuple(self._capacity.items())

    def _observe_capacity_limit(self, _options: Any) -> Iterator[Observation]:
        for provider, (limit, _available) in self._capacity_snapshot():
            yield Observation(limit, {"provider": provider})

    def _observe_capacity_available(self, _options: Any) -> Iterator[Observation]:
        for provider, (_limit, available) in self._capacity_snapshot():
            yield Observation(available, {"provider": provider})


def setup_telemetry() -> None:
    """Install providers before framework/client modules create instruments."""

    global _httpx_instrumented
    global _initialized
    global _instruments
    global _meter_provider
    global _trace_provider
    global _tracer

    if _initialized:
        return
    _initialized = True
    if not settings.observability_enabled or settings.otel_sdk_disabled:
        return

    if settings.otel_propagators.strip().lower() != "tracecontext":
        raise ValueError("AgentBox supports only the W3C tracecontext propagator")
    set_global_textmap(TraceContextTextMapPropagator())
    resource = _resource()
    trace_selector = settings.otel_traces_exporter.strip().lower()
    if trace_selector not in {"none", "otlp"}:
        raise ValueError(f"unsupported OTEL_TRACES_EXPORTER: {trace_selector}")
    trace_endpoint = _signal_endpoint("traces")
    if trace_selector == "otlp" and not trace_endpoint:
        raise RuntimeError(
            "OTEL trace export is enabled but the managed OTLP endpoint is missing"
        )
    if trace_selector == "otlp" and trace_endpoint:
        provider = TracerProvider(resource=resource, sampler=_sampler())
        provider.add_span_processor(
            BatchSpanProcessor(
                SanitizingSpanExporter(_span_exporter(trace_endpoint)),
                max_queue_size=2048,
                max_export_batch_size=512,
                export_timeout_millis=5_000,
            )
        )
        trace.set_tracer_provider(provider)
        _trace_provider = provider
        _tracer = provider.get_tracer("agentbox.telemetry", _INSTRUMENTATION_VERSION)
        if not _httpx_instrumented:
            HTTPXClientInstrumentor().instrument(tracer_provider=provider)
            _httpx_instrumented = True

    metric_selector = settings.otel_metrics_exporter.strip().lower()
    if metric_selector not in {"none", "otlp"}:
        raise ValueError(f"unsupported OTEL_METRICS_EXPORTER: {metric_selector}")
    metric_endpoint = _signal_endpoint("metrics")
    if metric_selector == "otlp" and not metric_endpoint:
        raise RuntimeError(
            "OTEL metric export is enabled but the managed OTLP endpoint is missing"
        )
    if metric_selector == "otlp" and metric_endpoint:
        reader = PeriodicExportingMetricReader(
            _metric_exporter(metric_endpoint),
            export_interval_millis=settings.otel_metric_export_interval,
            export_timeout_millis=5_000,
        )
        provider = MeterProvider(
            resource=resource,
            metric_readers=(reader,),
            views=(View(instrument_name="*", attribute_keys=METRIC_ATTRIBUTE_KEYS),),
        )
        metrics.set_meter_provider(provider)
        _meter_provider = provider
        _instruments = _AgentBoxInstruments(
            provider.get_meter("agentbox.telemetry", _INSTRUMENTATION_VERSION)
        )


def shutdown_telemetry(timeout_millis: int = 5_000) -> None:
    global _instruments
    global _meter_provider
    global _trace_provider
    global _tracer
    for provider in (_trace_provider, _meter_provider):
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
    _trace_provider = None
    _meter_provider = None
    _instruments = None
    _tracer = trace.get_tracer("agentbox.telemetry", _INSTRUMENTATION_VERSION)


def instrument_fastapi_app(app: FastAPI) -> None:
    if (
        not settings.observability_enabled
        or settings.otel_sdk_disabled
        or _trace_provider is None
        or id(app) in _instrumented_app_ids
    ):
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_trace_provider,
        meter_provider=_meter_provider,
        excluded_urls=_HEALTH_PATHS,
        exclude_spans=["receive", "send"],
    )
    _instrumented_app_ids.add(id(app))


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


def reset_terminal_operation_error() -> None:
    _terminal_operation_error.set(False)


def terminal_operation_error_emitted() -> bool:
    return _terminal_operation_error.get()


def _bounded_provider(provider: str) -> str:
    return provider if provider in _SAFE_PROVIDERS else "other"


def _bounded_profile(profile: SandboxProfileRef | None) -> str:
    if profile is None:
        return "none"
    reviewed = {
        settings.agentbox_workspace_profile_name,
        settings.agentbox_function_profile_name,
    }
    return profile.name if profile.name in reviewed else "other"


def _outcome(exc: BaseException | None) -> tuple[str, str | None]:
    if exc is None:
        return "success", None
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled", None
    if isinstance(exc, AgentBoxError):
        if exc.code == ErrorCode.DEADLINE_EXCEEDED:
            return "timeout", exc.code.value
        if exc.code in {
            ErrorCode.CAPACITY_EXHAUSTED,
            ErrorCode.RATE_LIMITED,
            ErrorCode.OPERATION_CONFLICT,
        }:
            return "rejected", exc.code.value
        return "failure", exc.code.value
    if isinstance(exc, TimeoutError):
        return "timeout", "TIMEOUT"
    return "failure", None


def _operation_attributes(
    *,
    operation: str,
    workload_kind: WorkloadKind,
    provider: str,
    profile: SandboxProfileRef | None,
) -> dict[str, str]:
    return {
        "operation": operation if operation in _SAFE_OPERATIONS else "other",
        "workload_kind": workload_kind.value,
        "provider": _bounded_provider(provider),
        "profile": _bounded_profile(profile),
    }


def _span_attributes(attributes: Mapping[str, str]) -> dict[str, str]:
    return {
        f"agentbox.{key.replace('_', '.')}": value for key, value in attributes.items()
    }


@contextmanager
def observe_agentbox_operation(
    *,
    operation: str,
    workload_kind: WorkloadKind,
    provider: str,
    profile: SandboxProfileRef | None = None,
) -> Iterator[None]:
    attributes = _operation_attributes(
        operation=operation,
        workload_kind=workload_kind,
        provider=provider,
        profile=profile,
    )
    active_attributes = dict(attributes)
    started_at = time.perf_counter()
    if _instruments is not None:
        _instruments.active.add(1, active_attributes)
    caught: BaseException | None = None
    with _tracer.start_as_current_span(
        f"agentbox.{attributes['operation']}",
        attributes=_span_attributes(attributes),
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield
        except BaseException as exc:
            caught = exc
            raise
        finally:
            outcome, error_code = _outcome(caught)
            duration_ms = max(0.0, (time.perf_counter() - started_at) * 1000)
            terminal_attributes = {**attributes, "outcome": outcome}
            span.set_attribute("agentbox.outcome", outcome)
            if caught is not None:
                span.set_attribute("error.type", type(caught).__name__)
                if error_code:
                    span.set_attribute("error.code", error_code)
                if outcome in {"failure", "timeout"}:
                    span.set_status(Status(StatusCode.ERROR))
            if _instruments is not None:
                _instruments.active.add(-1, active_attributes)
                _instruments.operations.add(1, terminal_attributes)
                _instruments.operation_duration.record(duration_ms, terminal_attributes)
                if outcome == "rejected":
                    _instruments.rejections.add(
                        1,
                        {
                            **attributes,
                            "reason": error_code or "OTHER",
                        },
                    )
                elif outcome == "timeout":
                    _instruments.timeouts.add(1, attributes)
            _log_operation_terminal(
                attributes=attributes,
                outcome=outcome,
                duration_ms=duration_ms,
                caught=caught,
                error_code=error_code,
            )


P = ParamSpec("P")
R = TypeVar("R")


def observed_lifecycle_operation(
    operation: str,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    if operation not in _SAFE_OPERATIONS:
        raise ValueError(f"unsupported AgentBox operation: {operation}")

    def decorate(function: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(function)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            service = args[0]
            key = args[1] if len(args) > 1 else kwargs["key"]
            profile = (
                (args[2] if len(args) > 2 else kwargs.get("profile"))
                if operation == "ensure"
                else None
            )
            with observe_agentbox_operation(
                operation=operation,
                workload_kind=key.workload_kind,
                provider=service._provider.name,
                profile=profile,
            ):
                try:
                    result = await function(*args, **kwargs)
                except asyncio.CancelledError:
                    raise
                except BaseException:
                    if operation != "inspect":
                        await service.refresh_capacity_telemetry()
                    raise
                if operation != "inspect":
                    await service.refresh_capacity_telemetry()
                return result

        return wrapped

    return decorate


def _log_operation_terminal(
    *,
    attributes: Mapping[str, str],
    outcome: str,
    duration_ms: float,
    caught: BaseException | None,
    error_code: str | None,
) -> None:
    from agentbox.observability import get_logger

    logger = get_logger("agentbox.lifecycle")
    common: dict[str, Any] = {
        **attributes,
        "outcome": outcome,
        "duration_ms": round(duration_ms, 1),
    }
    if outcome == "success":
        logger.info("agentbox.operation.completed", **common)
        return
    if outcome == "timeout":
        event = "agentbox.operation.timed_out"
    elif outcome == "cancelled":
        event = "agentbox.operation.cancelled"
    elif outcome == "rejected":
        event = "agentbox.operation.rejected"
    else:
        event = "agentbox.operation.failed"
    logger_method = (
        logger.warning if outcome in {"cancelled", "rejected"} else logger.error
    )
    error_type = type(caught).__name__ if caught is not None else "UnknownError"
    bounded_error_code = error_code or (
        "CANCELLED" if outcome == "cancelled" else "INTERNAL"
    )
    fingerprint_source = ":".join(
        ("lemma-agentbox", attributes["operation"], error_type, bounded_error_code)
    )
    logger_method(
        event,
        **common,
        error_type=error_type,
        error_code=bounded_error_code,
        error_fingerprint=hashlib.sha256(fingerprint_source.encode()).hexdigest(),
    )
    if outcome in {"failure", "timeout"}:
        _terminal_operation_error.set(True)


async def observe_phase(
    awaitable: Awaitable[R],
    *,
    phase: str,
    workload_kind: WorkloadKind,
    provider: str,
    profile: SandboxProfileRef | None = None,
) -> R:
    bounded_phase = phase if phase in _SAFE_PHASES else "other"
    attributes = {
        "workload_kind": workload_kind.value,
        "provider": _bounded_provider(provider),
        "profile": _bounded_profile(profile),
        "phase": bounded_phase,
    }
    started_at = time.perf_counter()
    caught: BaseException | None = None
    with _tracer.start_as_current_span(
        f"agentbox.{bounded_phase}",
        attributes=_span_attributes(attributes),
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            return await awaitable
        except BaseException as exc:
            caught = exc
            raise
        finally:
            outcome, error_code = _outcome(caught)
            duration_ms = max(0.0, (time.perf_counter() - started_at) * 1000)
            span.set_attribute("agentbox.outcome", outcome)
            if caught is not None:
                span.set_attribute("error.type", type(caught).__name__)
                if error_code:
                    span.set_attribute("error.code", error_code)
                if outcome in {"failure", "timeout"}:
                    span.set_status(Status(StatusCode.ERROR))
            if _instruments is not None:
                metric_attributes = {**attributes, "outcome": outcome}
                if bounded_phase == "admission_wait":
                    _instruments.admission_wait.record(duration_ms, metric_attributes)
                else:
                    _instruments.sandbox_start.record(duration_ms, metric_attributes)


def record_capacity(*, provider: str, limit: int, active: int, reserved: int) -> None:
    if _instruments is not None:
        _instruments.record_capacity(
            provider=provider,
            limit=limit,
            active=active,
            reserved=reserved,
        )


@contextmanager
def observe_control_operation(operation: str) -> Iterator[dict[str, int]]:
    bounded = operation if operation in _SAFE_CONTROL_OPERATIONS else "other"
    result = {"count": 0}
    started_at = time.perf_counter()
    caught: BaseException | None = None
    with _tracer.start_as_current_span(
        f"agentbox.{bounded}",
        attributes={"agentbox.operation": bounded},
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield result
        except BaseException as exc:
            caught = exc
            raise
        finally:
            outcome, error_code = _outcome(caught)
            span.set_attribute("agentbox.outcome", outcome)
            if caught is not None:
                span.set_attribute("error.type", type(caught).__name__)
                if error_code:
                    span.set_attribute("error.code", error_code)
                if outcome in {"failure", "timeout"}:
                    span.set_status(Status(StatusCode.ERROR))
            if _instruments is not None:
                instrument = (
                    _instruments.cleanup
                    if bounded == "cleanup"
                    else _instruments.reconcile
                )
                instrument.add(
                    max(1, result["count"]),
                    {"operation": bounded, "outcome": outcome},
                )
            _log_control_terminal(
                operation=bounded,
                outcome=outcome,
                duration_ms=max(0.0, (time.perf_counter() - started_at) * 1000),
                count=result["count"],
                caught=caught,
            )


def observed_control_operation(
    operation: str,
) -> Callable[[Callable[P, Awaitable[int]]], Callable[P, Awaitable[int]]]:
    if operation not in _SAFE_CONTROL_OPERATIONS:
        raise ValueError(f"unsupported AgentBox control operation: {operation}")

    def decorate(
        function: Callable[P, Awaitable[int]],
    ) -> Callable[P, Awaitable[int]]:
        @wraps(function)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> int:
            with observe_control_operation(operation) as result:
                completed = await function(*args, **kwargs)
                result["count"] = completed
                return completed

        return wrapped

    return decorate


def _log_control_terminal(
    *,
    operation: str,
    outcome: str,
    duration_ms: float,
    count: int,
    caught: BaseException | None,
) -> None:
    from agentbox.observability import get_logger

    logger = get_logger(f"agentbox.{operation}")
    fields: dict[str, Any] = {
        "outcome": outcome,
        "duration_ms": round(duration_ms, 1),
        "count": max(0, count),
    }
    if outcome == "success":
        logger.debug(f"agentbox.{operation}.completed", **fields)
        return
    logger.warning(
        f"agentbox.{operation}.failed",
        **fields,
        error_type=type(caught).__name__ if caught is not None else "UnknownError",
    )
