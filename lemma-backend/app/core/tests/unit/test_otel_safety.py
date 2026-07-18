from __future__ import annotations

from types import SimpleNamespace
import logging

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import (
    Link,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    TraceFlags,
)

from app.core.observability import telemetry
from app.core.observability.span_sanitizer import SanitizingSpanExporter


class _CaptureExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def _settings(**overrides):
    values = {
        "model_fields_set": set(),
        "otel_signals": None,
        "otel_traces_exporter": "otlp",
        "otel_metrics_exporter": "none",
        "otel_logs_exporter": "none",
        "otel_exporter_otlp_endpoint": "http://collector:4317",
        "otel_exporter_otlp_protocol": "grpc",
        "otel_exporter_otlp_headers": None,
        "otel_exporter_otlp_traces_endpoint": None,
        "otel_exporter_otlp_metrics_endpoint": None,
        "otel_exporter_otlp_logs_endpoint": None,
        "otel_exporter_otlp_traces_protocol": None,
        "otel_exporter_otlp_metrics_protocol": None,
        "otel_exporter_otlp_logs_protocol": None,
        "otel_exporter_otlp_traces_headers": None,
        "otel_exporter_otlp_metrics_headers": None,
        "otel_exporter_otlp_logs_headers": None,
        "otel_traces_sampler": "parentbased_traceidratio",
        "otel_traces_sampler_arg": 0.05,
        "otel_service_name": None,
        "otel_service_namespace": None,
        "environment": "testing",
        "llm_otel_enabled": False,
        "llm_otel_exporter_otlp_endpoint": None,
        "llm_otel_exporter_otlp_protocol": "grpc",
        "llm_otel_exporter_otlp_headers": None,
        "llm_otel_traces_sampler": "traceidratio",
        "llm_otel_traces_sampler_arg": 0.01,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_standard_signal_selectors_default_safe_and_allow_opt_in(monkeypatch) -> None:
    configured = _settings()
    monkeypatch.setattr(telemetry, "_get_settings", lambda: configured)
    assert telemetry._enabled_signals() == {"traces"}

    configured.model_fields_set = {"otel_metrics_exporter", "otel_logs_exporter"}
    configured.otel_metrics_exporter = "otlp"
    configured.otel_logs_exporter = "otlp"
    assert telemetry._enabled_signals() == {"traces", "metrics", "logs"}

    configured.otel_signals = "metrics,logs"
    configured.model_fields_set = set()
    assert telemetry._enabled_signals() == {"metrics", "logs"}

    configured.model_fields_set = {"otel_logs_exporter"}
    configured.otel_logs_exporter = "none"
    assert telemetry._enabled_signals() == {"metrics"}


def test_signal_endpoint_resolution_obeys_otlp_protocol_rules(monkeypatch) -> None:
    configured = _settings()
    monkeypatch.setattr(telemetry, "_get_settings", lambda: configured)
    assert telemetry._otlp_signal_endpoint("traces") == "http://collector:4317"

    configured.otel_exporter_otlp_protocol = "http/protobuf"
    assert telemetry._otlp_signal_endpoint("traces") == (
        "http://collector:4317/v1/traces"
    )
    configured.otel_exporter_otlp_traces_endpoint = "https://traces.test/custom"
    assert telemetry._otlp_signal_endpoint("traces") == ("https://traces.test/custom")


def _adversarial_span() -> ReadableSpan:
    context = SpanContext(
        trace_id=1,
        span_id=2,
        is_remote=False,
        trace_flags=TraceFlags.SAMPLED,
    )
    return ReadableSpan(
        name="GET https://user:CANARY@example.test/private?token=CANARY",
        context=context,
        parent=None,
        resource=Resource(
            {
                "service.name": "lemma-test",
                "service.version": "a" * 40,
                "process.command_args": ["--token=CANARY"],
            }
        ),
        attributes={
            "http.request.method": "GET",
            "http.route": "/pods/{pod_id}",
            "url.full": "https://example.test/private?token=CANARY",
            "db.statement": "SELECT 'CANARY'",
            "db.system": "postgresql",
            "server.address": "CANARY.internal",
            "gen_ai.prompt": "CANARY prompt",
            "gen_ai.request.model": "safe-model",
            "lemma.request_id": "request-1",
            "binary": b"CANARY",
        },
        events=(
            Event(
                "exception",
                {
                    "exception.type": "RuntimeError",
                    "exception.message": "CANARY exception",
                    "exception.stacktrace": "/private/CANARY.py",
                },
            ),
        ),
        links=(Link(context, {"url.full": "https://CANARY", "error.type": "Error"}),),
        kind=SpanKind.SERVER,
        instrumentation_scope=InstrumentationScope(
            "opentelemetry.instrumentation.fastapi"
        ),
        status=Status(StatusCode.ERROR, "CANARY status"),
        start_time=1,
        end_time=2,
    )


def test_export_boundary_span_sanitizer_drops_adversarial_content() -> None:
    capture = _CaptureExporter()
    exporter = SanitizingSpanExporter(capture)
    assert exporter.export([_adversarial_span()]) is SpanExportResult.SUCCESS
    assert len(capture.spans) == 1
    safe = capture.spans[0]
    assert safe.name == "http.server"
    assert safe.attributes == {
        "http.request.method": "GET",
        "http.route": "/pods/{pod_id}",
        "db.system": "postgresql",
        "lemma.request_id": "request-1",
    }
    assert safe.status.description is None
    assert safe.resource.attributes.get("process.command_args") is None
    assert safe.events[0].attributes == {"exception.type": "RuntimeError"}
    assert safe.links[0].attributes == {"error.type": "Error"}
    assert "CANARY" not in str(safe.to_json())


def test_llm_pipeline_disables_content_and_uses_dedicated_provider(
    monkeypatch,
) -> None:
    configured = _settings(
        llm_otel_enabled=True,
        llm_otel_exporter_otlp_endpoint="http://phoenix:4317",
    )
    monkeypatch.setattr(telemetry, "_get_settings", lambda: configured)
    monkeypatch.setattr(
        telemetry,
        "_build_span_exporter",
        lambda *args, **kwargs: _CaptureExporter(),
    )
    captured = []
    monkeypatch.setattr(
        telemetry.Agent,
        "instrument_all",
        lambda instrumentation_settings: captured.append(instrumentation_settings),
    )
    provider = telemetry._setup_llm_tracing("lemma-test")
    try:
        assert provider is not None
        assert len(captured) == 1
        instrumentation = captured[0]
        assert instrumentation.include_content is False
        assert instrumentation.include_binary_content is False
        assert instrumentation.tracer.resource is provider.resource
        assert instrumentation.event_mode == "attributes"
    finally:
        if provider is not None:
            provider.shutdown()


def test_otel_log_handler_constructs_only_bounded_records(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(
        LoggingHandler,
        "emit",
        lambda _self, record: captured.append(record),
    )
    handler = telemetry.SanitizingLoggingHandler(
        logger_provider=LoggerProvider(),
    )
    original = logging.LogRecord(
        "app.test",
        logging.ERROR,
        "/private/CANARY.py",
        99,
        {
            "event": "http.request.failed",
            "request_id": "request-1",
            "payload": "CANARY payload",
            "error_type": "RuntimeError",
        },
        (),
        None,
    )
    handler.emit(original)
    assert len(captured) == 1
    safe = captured[0]
    assert safe.msg == "http.request.failed"
    assert safe.pathname == ""
    assert safe.request_id == "request-1"
    assert safe.error_type == "RuntimeError"
    assert not hasattr(safe, "payload")


@pytest.mark.parametrize("value", ["console", "otlp,console", "invalid"])
def test_unknown_exporters_fail_closed(monkeypatch, value: str) -> None:
    configured = _settings(
        model_fields_set={"otel_logs_exporter"},
        otel_logs_exporter=value,
    )
    monkeypatch.setattr(telemetry, "_get_settings", lambda: configured)
    with pytest.raises(ValueError, match="unsupported otel_logs_exporter"):
        telemetry._enabled_signals()
