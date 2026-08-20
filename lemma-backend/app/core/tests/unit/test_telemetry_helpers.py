from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from app.core.observability import telemetry

pytestmark = pytest.mark.unit


class _Exporter(SpanExporter):
    def __init__(self, *, flush: bool = True) -> None:
        self.exported: list[tuple[ReadableSpan, ...]] = []
        self.flush = flush
        self.shutdown_calls = 0

    def export(self, spans):
        self.exported.append(tuple(spans))
        return SpanExportResult.SUCCESS

    def shutdown(self):
        self.shutdown_calls += 1

    def force_flush(self, timeout_millis=30_000):
        return self.flush


def test_filtering_exporter_filters_and_flushes():
    delegate = _Exporter(flush=False)
    exporter = telemetry.FilteringSpanExporter(delegate, lambda span: span.name == "keep")
    keep = SimpleNamespace(name="keep")
    drop = SimpleNamespace(name="drop")

    assert exporter.export([drop]) is SpanExportResult.SUCCESS
    assert delegate.exported == []
    assert exporter.export([drop, keep]) is SpanExportResult.SUCCESS
    assert delegate.exported == [(keep,)]
    assert exporter.force_flush() is False
    exporter.shutdown()
    assert delegate.shutdown_calls == 1


def test_filtering_exporter_without_flush_method_succeeds():
    class NoFlush:
        def export(self, spans):
            return SpanExportResult.SUCCESS

        def shutdown(self):
            return None

    assert telemetry.FilteringSpanExporter(NoFlush(), lambda _: True).force_flush()


def test_agent_run_context_enriches_spans_and_restores_previous_context():
    span = SimpleNamespace(attributes={}, set_attribute=lambda key, value: span.attributes.update({key: value}))
    enricher = telemetry.AgentRunSpanEnricher()

    assert telemetry._agent_run_context.get() == {}
    with telemetry.agent_run_telemetry_context(
        conversation_id="conversation",
        agent_run_id="run",
        agent_id="agent",
        model_name="model",
    ) as attributes:
        assert attributes["lemma.conversation_id"] == "conversation"
        enricher.on_start(span)
        assert span.attributes["lemma.agent_id"] == "agent"
        assert span.attributes["lemma.model_name"] == "model"
    assert telemetry._agent_run_context.get() == {}
    assert enricher.force_flush() is True
    assert enricher.shutdown() is None


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        ("always_on", "StaticSampler"),
        ("always_off", "StaticSampler"),
        ("traceidratio", "TraceIdRatioBased"),
        ("parentbased_always_on", "ParentBased"),
        ("parentbased_always_off", "ParentBased"),
        ("parentbased_traceidratio", "ParentBased"),
    ],
)
def test_build_sampler_supports_configured_strategies(strategy, expected):
    settings = SimpleNamespace(otel_traces_sampler_arg="0.25")
    sampler = telemetry._build_sampler(settings, strategy=strategy)
    assert type(sampler).__name__ == expected


def test_build_sampler_rejects_invalid_strategy_and_ratio():
    settings = SimpleNamespace(otel_traces_sampler_arg="0.25")
    with pytest.raises(ValueError, match="unsupported OTEL"):
        telemetry._build_sampler(settings, strategy="unknown")
    with pytest.raises(ValueError, match="between 0 and 1"):
        telemetry._build_sampler(settings, strategy="always_on", ratio=2)


def test_telemetry_parsers_are_strict_and_predictable(monkeypatch):
    assert telemetry._normalize_otlp_protocol(None) == "grpc"
    assert telemetry._normalize_otlp_protocol(" HTTP ") == "http/protobuf"
    assert telemetry._parse_otlp_headers(" token = abc, invalid, empty= ") == {
        "token": "abc"
    }
    assert telemetry._parse_otlp_headers(None) is None
    assert telemetry._legacy_enabled_signals(None) == {"traces"}
    assert telemetry._legacy_enabled_signals("metrics, logs") == {"metrics", "logs"}
    with pytest.raises(ValueError, match="unsupported signals"):
        telemetry._legacy_enabled_signals("traces,unknown")
    with pytest.raises(ValueError, match="unsupported OTLP"):
        telemetry._normalize_otlp_protocol("udp")

    monkeypatch.setattr(
        telemetry,
        "_get_settings",
        lambda: SimpleNamespace(
            otel_service_name="bad name",
            otel_service_namespace="lemma/test",
            environment="test",
            lemma_runtime_instance_id="instance-1",
        ),
    )
    assert telemetry._resolve_service_name("backend") == "backend"
    assert telemetry._resolve_instance_id() == "instance-1"


def test_signal_resolution_prefers_explicit_standard_fields(monkeypatch):
    settings = SimpleNamespace(
        model_fields_set={"otel_metrics_exporter"},
        otel_signals="traces",
        otel_traces_exporter="otlp",
        otel_metrics_exporter="none",
        otel_logs_exporter="none",
    )
    monkeypatch.setattr(telemetry, "_get_settings", lambda: settings)
    assert telemetry._enabled_signals() == {"traces"}

    settings.model_fields_set = set()
    settings.otel_signals = "metrics,logs"
    assert telemetry._enabled_signals() == {"metrics", "logs"}


def test_otlp_endpoint_and_headers_follow_signal_protocol(monkeypatch):
    settings = SimpleNamespace(
        otel_exporter_otlp_endpoint="https://collector.test",
        otel_exporter_otlp_protocol="http/protobuf",
        otel_exporter_otlp_headers="api-key=secret",
        otel_exporter_otlp_traces_endpoint=None,
        otel_exporter_otlp_traces_protocol=None,
        otel_exporter_otlp_traces_headers=None,
    )
    monkeypatch.setattr(telemetry, "_get_settings", lambda: settings)
    assert telemetry._otlp_signal_endpoint("traces") == (
        "https://collector.test/v1/traces"
    )
    assert telemetry._otlp_signal_headers("traces") == {"api-key": "secret"}


def test_llm_span_detection_uses_openinference_kind():
    keep = SimpleNamespace(
        attributes={
            telemetry.SpanAttributes.OPENINFERENCE_SPAN_KIND: "LLM",
        }
    )
    drop = SimpleNamespace(attributes={})
    assert telemetry._is_llm_span(keep)
    assert not telemetry._is_llm_span(drop)
