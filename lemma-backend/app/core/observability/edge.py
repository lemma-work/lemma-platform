"""What the edge tells a request's server span: the load balancer's trace,
and what kind of caller it is. Everything else about the edge -- client IP,
user agent, location, latency, requests refused at the edge -- is in the
load balancer's own request log, joined to the app through the trace id.

Google's external HTTPS load balancer starts a trace for every request and
sends it as ``X-Cloud-Trace-Context: TRACE_ID/SPAN_ID;o=1``. Without an
extractor for that format the API started a trace of its own, so the trace id
in a load-balancer log line resolved to nothing. W3C ``traceparent`` still
wins when both are present; the Cloud Trace header is read only when no
``traceparent`` produced a context.

Every header here can be sent by any caller, and nothing here makes an
authorization decision from them. What a forged trace header
can do is choose which trace id a request joins -- and, because the general
sampler is parent-based, ask for its own request to be sampled, as a forged
``traceparent`` always could.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
import re

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.propagators.textmap import (
    CarrierT,
    Getter,
    Setter,
    TextMapPropagator,
    default_getter,
)
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    SpanKind,
    TraceFlags,
)
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)

from app.core.origin import OriginKind, origin_for_path

CLOUD_TRACE_HEADER = "x-cloud-trace-context"
_CLOUD_TRACE_RE = re.compile(r"^([0-9a-fA-F]{32})/(\d{1,20})(?:;o=([01]))?$")


def parse_cloud_trace(value: str | None) -> tuple[int, int, bool] | None:
    """``(trace_id, span_id, sampled)`` from an ``X-Cloud-Trace-Context`` value."""
    match = _CLOUD_TRACE_RE.match((value or "").strip())
    if match is None:
        return None
    trace_id, span_id = int(match.group(1), 16), int(match.group(2))
    if trace_id == 0 or not 0 < span_id < 2**64:
        return None
    return trace_id, span_id, match.group(3) == "1"


class CloudTraceContextExtractor(TextMapPropagator):
    """Reads Google's trace header. Extract only: nothing we call wants it."""

    def extract(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        getter: Getter[CarrierT] = default_getter,
    ) -> Context:
        context = context if context is not None else Context()
        if trace.get_current_span(context).get_span_context().is_valid:
            return context  # a traceparent already won
        values = getter.get(carrier, CLOUD_TRACE_HEADER) or []
        parsed = parse_cloud_trace(values[0] if values else None)
        if parsed is None:
            return context
        trace_id, span_id, sampled = parsed
        span_context = SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED if sampled else 0),
        )
        return trace.set_span_in_context(NonRecordingSpan(span_context), context)

    def inject(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        setter: Setter[CarrierT] = None,  # type: ignore[assignment]
    ) -> None:
        # Outbound calls and outbox events keep carrying only `traceparent`.
        return None

    @property
    def fields(self) -> set[str]:
        return {CLOUD_TRACE_HEADER}


def install_propagators() -> None:
    """W3C trace context first, then baggage, then the Cloud Trace header."""
    set_global_textmap(
        CompositePropagator(
            [
                TraceContextTextMapPropagator(),
                W3CBaggagePropagator(),
                CloudTraceContextExtractor(),
            ]
        )
    )


#: ``X-Lemma-Client`` names, by caller kind.
_CLIENT_KINDS = {
    "lemma-web": "browser",
    "lemma-desktop": "browser",
    "lemma-app": "browser",
    "lemma-cli": "cli",
    "lemma-sdk-py": "sdk",
    "lemma-sdk-ts": "sdk",
}
_PROGRAMMATIC_AGENTS = ("python-httpx", "python-requests", "node", "axios", "curl")


def caller_kind(
    *,
    user_agent: str,
    lemma_client: str,
    has_session_cookie: bool,
    has_bearer: bool,
    is_webhook_route: bool,
    is_delegated_workload: bool,
) -> str:
    """``browser | sdk | cli | webhook | agent-sandbox | unknown``.

    In order: what authentication proved (a workload token is a sandbox, a
    webhook route is a webhook), then what the client said it is, then the
    session cookie (only a browser holds one), then the user agent.
    """
    if is_delegated_workload:
        return "agent-sandbox"
    if is_webhook_route:
        return "webhook"
    client = lemma_client.split("/", 1)[0].strip().lower()
    if client in _CLIENT_KINDS:
        return _CLIENT_KINDS[client]
    if has_session_cookie:
        return "browser"
    agent = user_agent.lower()
    if agent.startswith("mozilla/") and not has_bearer:
        return "browser"
    if has_bearer or agent.startswith(_PROGRAMMATIC_AGENTS):
        return "sdk"
    return "unknown"


def _is_webhook_route(path: str) -> bool:
    origin = origin_for_path(path)
    return origin is not None and origin.kind in (
        OriginKind.SURFACE,
        OriginKind.CONNECTOR,
    )


def _header(headers: Sequence[tuple[bytes, bytes]], name: bytes) -> str:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1", "replace")
    return ""


def annotate_server_span(
    span: Span,
    scope: Mapping[str, object],
    *,
    headers: Sequence[tuple[bytes, bytes]],
) -> None:
    """Put what only the app knows on the request's server span.

    Client IP, user agent, location and latency are in the load balancer's
    own request log, joined to this span through the continued trace id.
    """
    if not span.is_recording() or getattr(span, "kind", None) is not SpanKind.SERVER:
        return
    raw_state = scope.get("state")
    state: Mapping[str, object] = raw_state if isinstance(raw_state, Mapping) else {}
    path = str(scope.get("path") or "")
    attributes: MutableMapping[str, str] = {}
    parsed = parse_cloud_trace(_header(headers, CLOUD_TRACE_HEADER.encode()))
    if parsed is not None:
        attributes["gcp.lb.trace_id"] = format(parsed[0], "032x")
    authorization = _header(headers, b"authorization").lower()
    attributes["lemma.caller.kind"] = caller_kind(
        user_agent=_header(headers, b"user-agent"),
        lemma_client=_header(headers, b"x-lemma-client"),
        has_session_cookie="saccesstoken=" in _header(headers, b"cookie").lower(),
        has_bearer=authorization.startswith("bearer "),
        is_webhook_route=_is_webhook_route(path),
        is_delegated_workload=(
            state.get("delegation_claims") is not None
            or state.get("function_run_claims") is not None
        ),
    )
    span.set_attributes(attributes)


__all__ = [
    "CloudTraceContextExtractor",
    "annotate_server_span",
    "caller_kind",
    "install_propagators",
    "parse_cloud_trace",
]
