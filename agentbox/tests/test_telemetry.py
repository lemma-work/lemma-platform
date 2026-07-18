from __future__ import annotations

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags

from agentbox.config import settings
from agentbox import telemetry


class _Capture(SpanExporter):
    def __init__(self) -> None:
        self.spans = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def test_signals_are_independently_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(settings, "otel_signals", None)
    monkeypatch.setattr(settings, "otel_traces_exporter", "otlp")
    monkeypatch.setattr(settings, "otel_metrics_exporter", "none")
    monkeypatch.setattr(settings, "otel_logs_exporter", "none")
    monkeypatch.setattr(settings, "__pydantic_fields_set__", set())
    assert telemetry._enabled_signals() == {"traces"}

    settings.__pydantic_fields_set__.update(
        {"otel_metrics_exporter", "otel_logs_exporter"}
    )
    monkeypatch.setattr(settings, "otel_metrics_exporter", "otlp")
    monkeypatch.setattr(settings, "otel_logs_exporter", "otlp")
    assert telemetry._enabled_signals() == {"traces", "metrics", "logs"}


def test_agentbox_span_export_is_default_deny() -> None:
    context = SpanContext(1, 2, False, TraceFlags.SAMPLED)
    span = ReadableSpan(
        name="GET https://CANARY.test/private?token=CANARY",
        context=context,
        resource=Resource(
            {"service.name": "lemma-agentbox", "process.command": "CANARY"}
        ),
        attributes={
            "http.request.method": "GET",
            "http.route": "/sandboxes/{sandbox_id}",
            "url.full": "https://CANARY.test/?token=CANARY",
        },
        kind=SpanKind.SERVER,
        instrumentation_scope=InstrumentationScope(
            "opentelemetry.instrumentation.fastapi"
        ),
        status=Status(StatusCode.ERROR, "CANARY"),
        start_time=1,
        end_time=2,
    )
    capture = _Capture()
    exporter = telemetry.SanitizingSpanExporter(capture)
    assert exporter.export([span]) is SpanExportResult.SUCCESS
    safe = capture.spans[0]
    assert safe.name == "http.server"
    assert safe.attributes == {
        "http.request.method": "GET",
        "http.route": "/sandboxes/{sandbox_id}",
    }
    assert safe.status.description is None
    assert "CANARY" not in str(safe.to_json())
