"""Export-boundary allowlists for OpenTelemetry spans and metrics.

Instrumentation libraries change independently and may add new attributes.  The
policy here is deliberately default-deny so an upgrade cannot start exporting
content, credentials, SQL, URLs, or unbounded identifiers by accident.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import (
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import (
    Link,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    TraceState,
)


_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SAFE_ROUTE_SEGMENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SAFE_ROUTE_PARAMETER_RE = re.compile(
    r"^\{[A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z_][A-Za-z0-9_]*)?\}$"
)
_UUID_ROUTE_SEGMENT_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_ULID_ROUTE_SEGMENT_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_HEX_ROUTE_SEGMENT_RE = re.compile(r"^[0-9a-fA-F]{8,}$")
_TOKEN_ROUTE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]{16,}$")
_MAX_STRING = 256
_MAX_SEQUENCE = 16
_MAX_ROUTE_SEGMENTS = 32
_REDUNDANT_INTERNAL_SCOPE_NAMES = frozenset(
    {
        "opentelemetry.instrumentation.asgi",
        "opentelemetry.instrumentation.fastapi",
    }
)

RESOURCE_ATTRIBUTE_KEYS = frozenset(
    {
        "service.name",
        "service.namespace",
        "service.version",
        # Which process emitted this. Without it every replica exports an
        # identical resource, so a backend that keys series off the resource
        # sees N writers claiming one series and rejects the batch.
        "service.instance.id",
        "deployment.environment",
        "telemetry.sdk.language",
        "telemetry.sdk.name",
        "telemetry.sdk.version",
    }
)

GENERAL_SPAN_ATTRIBUTE_KEYS = frozenset(
    {
        # Stable HTTP semantic conventions. Raw paths, URLs, query strings,
        # hosts, headers, and bodies are intentionally absent.
        "http.request.method",
        "http.response.status_code",
        "http.route",
        "http.method",
        "http.status_code",
        "network.protocol.name",
        "network.protocol.version",
        # Stable dependency dimensions. SQL statements, Redis commands/keys,
        # database names, and peer addresses are intentionally absent.
        "db.system",
        "db.system.name",
        "db.operation",
        "db.operation.name",
        "rpc.system",
        "rpc.service",
        "rpc.method",
        "messaging.system",
        "messaging.operation",
        "messaging.operation.name",
        # Bounded error and Lemma business context.
        "error.type",
        "exception.type",
        "error.code",
        "error.retryable",
        "lemma.request_id",
        "lemma.correlation_id",
        "lemma.event_id",
        "lemma.job_id",
        # Spans only, deliberately: this is what turns "the app is slow" into
        # "it is slow for this tenant". It is absent from the metric allowlist
        # below on purpose -- as a label it multiplies every series by the
        # customer count.
        "lemma.organization_id",
        "lemma.pod_id",
        "lemma.conversation_id",
        "lemma.agent_run_id",
        "lemma.task_name",
        "lemma.consumer",
        "lemma.event_type",
        "lemma.attempt",
        "lemma.outcome",
        "lemma.execution_mode",
        "lemma.runtime_profile",
        "lemma.cold",
    }
)

LLM_SPAN_ATTRIBUTE_KEYS = GENERAL_SPAN_ATTRIBUTE_KEYS | frozenset(
    {
        "openinference.span.kind",
        "gen_ai.system",
        "gen_ai.provider.name",
        "gen_ai.operation.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.response.finish_reasons",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.total_tokens",
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.cache_creation.input_tokens",
        "gen_ai.usage.cost",
    }
)

# One catch-all metric view uses this set. It is intentionally free of request,
# event, job, conversation, URL, SQL, Redis-key, and other identifier labels.
METRIC_ATTRIBUTE_KEYS = frozenset(
    {
        "http.request.method",
        "http.response.status_code",
        "http.route",
        "http.method",
        "http.status_code",
        "network.protocol.name",
        "network.protocol.version",
        # Which third party a client call went to. Without it every outbound
        # dependency -- model providers, sandboxes, mail -- aggregates into one
        # undifferentiated series and "which one is slow" has no answer. The
        # host is bounded by the set of services we call; the URL, path and
        # query that would not be bounded are still absent.
        "server.address",
        "db.system",
        "db.system.name",
        "db.operation",
        "db.operation.name",
        # Connection-pool identity and slot state. Both are required together:
        # the pool metric emits one point per (pool, state) per export, so
        # stripping them collapsed every point onto a single label-less series
        # and made the export look like a duplicate write. ``pool.name`` is
        # only safe because the engines set an explicit ``pool_logging_name``
        # -- the instrumentation's fallback is the DSN, host and all.
        "pool.name",
        "state",
        "rpc.system",
        "rpc.service",
        "rpc.method",
        "messaging.system",
        "messaging.operation",
        "messaging.operation.name",
        "gen_ai.system",
        "gen_ai.provider.name",
        "gen_ai.operation.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.token.type",
        "event_type",
        "consumer",
        "dependency",
        "provider",
        "operation",
        "outcome",
        "status_code",
        "method",
        "route",
        "task_name",
        # Which worker queue a depth reading belongs to. Bounded by the Lane
        # enum.
        "lane",
        "execution_mode",
        "runtime_profile",
        "cold",
    }
)


def _safe_scalar(value: Any) -> str | bool | int | float | None:
    if isinstance(value, str):
        return " ".join(value.splitlines())[:_MAX_STRING]
    if isinstance(value, bool | int | float):
        return value
    return None


def _safe_value(value: Any) -> Any | None:
    scalar = _safe_scalar(value)
    if scalar is not None:
        return scalar
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        values = []
        for item in value[:_MAX_SEQUENCE]:
            safe = _safe_scalar(item)
            if safe is not None:
                values.append(safe)
        return tuple(values) if values else None
    return None


def sanitize_http_route(value: Any) -> str | None:
    """Keep bounded routes while rejecting common concrete identifier forms.

    Instrumentation normally supplies the declared framework route (for example
    ``/pods/{pod_id}``), but compatibility fallbacks have returned request paths
    containing UUIDs and other high-cardinality identifiers. Route attributes
    are labels, so UUID, ULID, numeric, digest, token-like, URL, and malformed
    segments are omitted at the final export boundary.
    """
    if not isinstance(value, str) or not value.startswith("/"):
        return None
    if len(value) > _MAX_STRING or any(
        marker in value for marker in ("?", "#", "%", "\\", "@", "://")
    ):
        return None
    segments = [segment for segment in value.split("/") if segment]
    if len(segments) > _MAX_ROUTE_SEGMENTS:
        return None
    for segment in segments:
        if _SAFE_ROUTE_PARAMETER_RE.fullmatch(segment):
            continue
        if (
            segment.isdecimal()
            or _UUID_ROUTE_SEGMENT_RE.fullmatch(segment)
            or _ULID_ROUTE_SEGMENT_RE.fullmatch(segment)
            or _HEX_ROUTE_SEGMENT_RE.fullmatch(segment)
            or (
                _TOKEN_ROUTE_SEGMENT_RE.fullmatch(segment)
                and any(character.isalpha() for character in segment)
                and any(character.isdigit() for character in segment)
            )
        ):
            return None
        if not _SAFE_ROUTE_SEGMENT_RE.fullmatch(segment):
            return None
    return value


def sanitize_attributes(
    attributes: Mapping[str, Any] | None,
    *,
    llm: bool,
) -> dict[str, Any]:
    allowed = LLM_SPAN_ATTRIBUTE_KEYS if llm else GENERAL_SPAN_ATTRIBUTE_KEYS
    safe: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if key not in allowed:
            continue
        sanitized = (
            sanitize_http_route(value) if key == "http.route" else _safe_value(value)
        )
        if sanitized is not None:
            safe[key] = sanitized
    return safe


def _safe_span_name(span: ReadableSpan, *, llm: bool) -> str:
    scope_name = (getattr(span.instrumentation_scope, "name", None) or "").lower()
    if llm or "pydantic" in scope_name or "openinference" in scope_name:
        kind = str((span.attributes or {}).get("openinference.span.kind", "")).lower()
        return f"gen_ai.{kind}" if kind.isidentifier() else "gen_ai.operation"
    if "fastapi" in scope_name or "asgi" in scope_name:
        return "http.server"
    if "aiohttp" in scope_name or "httpx" in scope_name or "urllib" in scope_name:
        return "http.client"
    if "sqlalchemy" in scope_name:
        return "db.operation"
    if "redis" in scope_name:
        return "redis.operation"
    if scope_name.startswith("app.") and _SAFE_NAME_RE.fullmatch(span.name):
        return span.name[:128]
    return "dependency.operation"


def _safe_event(event: Event, *, llm: bool) -> Event:
    name = event.name if _SAFE_NAME_RE.fullmatch(event.name) else "span.event"
    attributes = sanitize_attributes(event.attributes, llm=llm)
    if event.name == "exception":
        name = "exception"
        attributes = {
            key: value
            for key, value in attributes.items()
            if key in {"exception.type", "error.type", "error.code", "error.retryable"}
        }
    return Event(name, attributes=attributes, timestamp=event.timestamp)


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
    name = (
        scope.name if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", scope.name) else "unknown"
    )
    version = scope.version
    if version is not None and not re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}", version):
        version = None
    return InstrumentationScope(name=name, version=version)


def sanitize_span(span: ReadableSpan, *, llm: bool) -> ReadableSpan:
    resource = Resource(
        {
            key: value
            for key, value in span.resource.attributes.items()
            if key in RESOURCE_ATTRIBUTE_KEYS
        },
        schema_url=span.resource.schema_url,
    )
    status = Status(
        StatusCode.ERROR
        if span.status.status_code is StatusCode.ERROR
        else span.status.status_code
    )
    return ReadableSpan(
        name=_safe_span_name(span, llm=llm),
        context=_safe_context(span.context),
        parent=_safe_context(span.parent),
        resource=resource,
        attributes=sanitize_attributes(span.attributes, llm=llm),
        events=tuple(_safe_event(event, llm=llm) for event in span.events),
        links=tuple(
            Link(
                _safe_context(link.context),
                sanitize_attributes(link.attributes, llm=llm),
            )
            for link in span.links
        ),
        kind=span.kind,
        instrumentation_scope=_safe_scope(span.instrumentation_scope),
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
    )


class SanitizingSpanExporter(SpanExporter):
    """Sanitize every span immediately before it crosses the process boundary."""

    def __init__(self, delegate: SpanExporter, *, llm: bool = False) -> None:
        self._delegate = delegate
        self._llm = llm

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        safe_spans: list[ReadableSpan] = []
        for span in spans:
            scope_name = (
                getattr(span.instrumentation_scope, "name", None) or ""
            ).lower()
            if (
                span.kind is SpanKind.INTERNAL
                and scope_name in _REDUNDANT_INTERNAL_SCOPE_NAMES
            ):
                continue
            try:
                safe_spans.append(sanitize_span(span, llm=self._llm))
            except Exception:
                # An instrumentation-library edge case must not leak the unsafe
                # original span or affect the application executing it.
                continue
        if not safe_spans:
            return SpanExportResult.SUCCESS
        try:
            return self._delegate.export(tuple(safe_spans))
        except Exception:
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self._delegate.shutdown()
        except Exception:
            return

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        force_flush = getattr(self._delegate, "force_flush", None)
        if not callable(force_flush):
            return True
        try:
            return bool(force_flush(timeout_millis))
        except Exception:
            return False
