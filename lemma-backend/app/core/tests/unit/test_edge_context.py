"""The edge's trace, location and caller reach the request's server span."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import propagate
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind

from app.core.domain.events import DomainEvent
from app.core.observability.edge import (
    annotate_server_span,
    caller_kind,
    install_propagators,
    parse_cloud_trace,
)

pytestmark = pytest.mark.unit

_LB_TRACE = "105445aa7843bc8bf206b12000100000"
_W3C_TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


@pytest.fixture
def propagators() -> Iterator[None]:
    previous = propagate.get_global_textmap()
    install_propagators()
    yield
    propagate.set_global_textmap(previous)


async def _served(headers: list[tuple[bytes, bytes]]) -> ReadableSpan:
    """One request through the ASGI instrumentation production wraps the app in."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    await OpenTelemetryMiddleware(app, tracer_provider=provider)(
        {
            "type": "http",
            "method": "GET",
            "path": "/pods",
            "headers": headers,
            "scheme": "https",
            "server": ("api", 443),
            "query_string": b"",
            "http_version": "1.1",
        },
        receive,
        send,
    )
    [server] = [s for s in exporter.get_finished_spans() if s.kind is SpanKind.SERVER]
    return server


@pytest.mark.asyncio
async def test_a_load_balancer_trace_is_continued(propagators) -> None:
    span = await _served(
        [(b"x-cloud-trace-context", f"{_LB_TRACE}/12345;o=1".encode())]
    )
    assert format(span.context.trace_id, "032x") == _LB_TRACE
    assert span.parent is not None and span.parent.span_id == 12345


@pytest.mark.asyncio
async def test_traceparent_is_continued_and_wins_over_the_cloud_header(
    propagators,
) -> None:
    span = await _served(
        [
            (b"traceparent", f"00-{_W3C_TRACE}-00f067cc0cd902b7-01".encode()),
            (b"x-cloud-trace-context", f"{_LB_TRACE}/1;o=1".encode()),
        ]
    )
    assert format(span.context.trace_id, "032x") == _W3C_TRACE


def test_an_outbox_event_still_carries_only_traceparent(propagators) -> None:
    provider = TracerProvider()
    with provider.get_tracer("t").start_as_current_span("publisher"):
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
    assert set(carrier) == {"traceparent"}
    assert "traceparent" in DomainEvent.model_fields


@pytest.mark.parametrize(
    "value", ["", "nonsense", f"{'0' * 32}/1;o=1", f"{_LB_TRACE}/0", "abc/1"]
)
def test_a_malformed_cloud_trace_header_is_ignored(value: str) -> None:
    assert parse_cloud_trace(value) is None


def test_the_server_span_gets_the_lb_trace_and_caller_and_nothing_the_lb_logs() -> None:
    span = TracerProvider().get_tracer("t").start_span("s", kind=SpanKind.SERVER)
    annotate_server_span(
        span,
        {"path": "/pods", "state": {}},
        headers=[
            (b"x-cloud-trace-context", f"{_LB_TRACE}/7;o=1".encode()),
            (b"x-client-geo-region", b"IN"),
            (b"user-agent", b"python-httpx/0.28"),
        ],
    )
    # The user agent decides the kind without being recorded; location and
    # the agent itself are in the load balancer's request log.
    assert dict(span.attributes) == {
        "gcp.lb.trace_id": _LB_TRACE,
        "lemma.caller.kind": "sdk",
    }


_NOBODY: dict[str, str | bool] = {
    "user_agent": "",
    "lemma_client": "",
    "has_session_cookie": False,
    "has_bearer": False,
    "is_webhook_route": False,
    "is_delegated_workload": False,
}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "unknown"),
        ({"is_delegated_workload": True, "lemma_client": "lemma-cli/1.0"}, "agent-sandbox"),
        ({"is_webhook_route": True, "user_agent": "Slackbot 1.0"}, "webhook"),
        ({"lemma_client": "lemma-cli/2.3.0", "has_bearer": True}, "cli"),
        ({"lemma_client": "lemma-sdk-py/1.0", "has_bearer": True}, "sdk"),
        ({"lemma_client": "lemma-web/1.0"}, "browser"),
        ({"has_session_cookie": True}, "browser"),
        ({"user_agent": "Mozilla/5.0 (Macintosh)"}, "browser"),
        ({"user_agent": "Mozilla/5.0", "has_bearer": True}, "sdk"),
        ({"user_agent": "python-httpx/0.28"}, "sdk"),
        ({"has_bearer": True}, "sdk"),
    ],
)  # fmt: skip
def test_caller_kind_mapping(overrides, expected) -> None:
    assert caller_kind(**{**_NOBODY, **overrides}) == expected
