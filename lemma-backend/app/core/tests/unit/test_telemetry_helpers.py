from __future__ import annotations

from types import SimpleNamespace
import json

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
    exporter = telemetry.FilteringSpanExporter(
        delegate, lambda span: span.name == "keep"
    )
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
    span = SimpleNamespace(
        attributes={},
        set_attribute=lambda key, value: span.attributes.update({key: value}),
    )
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


def test_agent_run_context_carries_openinference_session_and_user():
    """Phoenix groups a conversation's traces on `session.id` and nothing else."""
    with telemetry.agent_run_telemetry_context(
        conversation_id="conversation-1",
        agent_run_id="run-1",
        user_id="user-1",
        pod_id="pod-1",
        agent_name="assistant",
    ) as attributes:
        assert attributes[telemetry.SpanAttributes.SESSION_ID] == "conversation-1"
        assert attributes[telemetry.SpanAttributes.USER_ID] == "user-1"
        metadata = json.loads(attributes[telemetry.SpanAttributes.METADATA])

    # The metadata blob restates the lemma.* fields, unprefixed, and carries
    # nothing else -- it is built from them, so the two cannot disagree.
    assert metadata == {
        "conversation_id": "conversation-1",
        "agent_run_id": "run-1",
        "pod_id": "pod-1",
        "user_id": "user-1",
        "agent_name": "assistant",
    }


def test_agent_run_context_omits_user_id_when_absent():
    with telemetry.agent_run_telemetry_context(
        conversation_id="conversation-1",
        agent_run_id="run-1",
    ) as attributes:
        assert telemetry.SpanAttributes.USER_ID not in attributes
        assert attributes[telemetry.SpanAttributes.SESSION_ID] == "conversation-1"


def test_llm_fanout_processor_is_built_only_when_the_llm_backend_is_on(monkeypatch):
    settings = SimpleNamespace(
        llm_otel_enabled=False,
        llm_otel_exporter_otlp_endpoint="http://phoenix:4317",
        llm_otel_exporter_otlp_protocol="grpc",
        llm_otel_exporter_otlp_headers=None,
    )
    monkeypatch.setattr(telemetry, "_get_settings", lambda: settings)
    assert telemetry._build_llm_fanout_processor() is None

    settings.llm_otel_enabled = True
    settings.llm_otel_exporter_otlp_endpoint = None
    assert telemetry._build_llm_fanout_processor() is None

    settings.llm_otel_exporter_otlp_endpoint = "http://phoenix:4317"
    processor = telemetry._build_llm_fanout_processor()
    assert processor is not None
    processor.shutdown()


def test_record_span_input_and_output_encode_and_truncate():
    span = SimpleNamespace(
        attributes={},
        set_attribute=lambda key, value: span.attributes.update({key: value}),
    )

    telemetry.record_span_input(span, "what is the status of order 42?")
    telemetry.record_span_output(span, {"status": "shipped"})

    assert span.attributes[telemetry.SpanAttributes.INPUT_VALUE] == (
        "what is the status of order 42?"
    )
    assert span.attributes[telemetry.SpanAttributes.INPUT_MIME_TYPE] == "text/plain"
    assert span.attributes[telemetry.SpanAttributes.OUTPUT_VALUE] == (
        '{"status":"shipped"}'
    )
    assert (
        span.attributes[telemetry.SpanAttributes.OUTPUT_MIME_TYPE] == "application/json"
    )

    telemetry.record_span_input(span, "x" * 20_000)
    assert (
        len(span.attributes[telemetry.SpanAttributes.INPUT_VALUE])
        == telemetry._MAX_SPAN_CONTENT_CHARS
    )


def test_record_span_content_skips_nothing_worth_recording():
    span = SimpleNamespace(
        attributes={},
        set_attribute=lambda key, value: span.attributes.update({key: value}),
    )
    telemetry.record_span_input(span, None)
    telemetry.record_span_output(span, "")
    assert span.attributes == {}


def test_record_span_output_encodes_values_json_cannot_hold_natively():
    span = SimpleNamespace(
        attributes={},
        set_attribute=lambda key, value: span.attributes.update({key: value}),
    )

    telemetry.record_span_output(span, {"key": {1, 2}})
    assert span.attributes[telemetry.SpanAttributes.OUTPUT_MIME_TYPE] == (
        "application/json"
    )
    assert "1" in span.attributes[telemetry.SpanAttributes.OUTPUT_VALUE]


def _readable_span(name: str, *, parent, attributes=None):
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.trace import SpanContext, TraceFlags

    context = SpanContext(
        trace_id=0x1111,
        span_id=0x2222,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return ReadableSpan(
        name=name,
        context=context,
        parent=parent,
        attributes=attributes or {},
    )


def test_trace_rooting_exporter_drops_the_parent_the_backend_never_gets():
    """An orphan is not a root: Phoenix resolves roots with `parent_id IS NULL`."""
    from opentelemetry.trace import SpanContext, TraceFlags

    parent = SpanContext(
        trace_id=0x1111,
        span_id=0x3333,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    delegate = _Exporter()
    exporter = telemetry.TraceRootingSpanExporter(delegate)

    child = _readable_span("agent.run", parent=parent, attributes={"a": "b"})
    assert exporter.export([child]) is SpanExportResult.SUCCESS

    (exported,) = delegate.exported[0]
    assert exported.parent is None
    # Everything that identifies the span is untouched -- in particular its own
    # span id, which the model spans below it name as their parent.
    assert exported.context.span_id == child.context.span_id
    assert exported.context.trace_id == child.context.trace_id
    assert exported.name == "agent.run"
    assert exported.attributes == {"a": "b"}


def test_trace_rooting_exporter_leaves_a_real_root_alone():
    delegate = _Exporter()
    exporter = telemetry.TraceRootingSpanExporter(delegate)
    root = _readable_span("agent.run", parent=None)

    exporter.export([root])

    (exported,) = delegate.exported[0]
    assert exported is root


def test_trace_rooting_exporter_forwards_lifecycle():
    delegate = _Exporter(flush=False)
    exporter = telemetry.TraceRootingSpanExporter(delegate)
    assert exporter.force_flush() is False
    exporter.shutdown()
    assert delegate.shutdown_calls == 1


def test_llm_fanout_reroots_what_it_forwards(monkeypatch):
    """The two wrappers compose: filter to OpenInference spans, then re-root."""
    from opentelemetry.trace import SpanContext, TraceFlags

    delegate = _Exporter()
    monkeypatch.setattr(
        telemetry,
        "_get_settings",
        lambda: SimpleNamespace(
            llm_otel_enabled=True,
            llm_otel_exporter_otlp_endpoint="http://phoenix:4317",
            llm_otel_exporter_otlp_protocol="grpc",
            llm_otel_exporter_otlp_headers=None,
        ),
    )
    monkeypatch.setattr(
        telemetry,
        "_build_span_exporter",
        lambda endpoint, *, protocol, headers=None: delegate,
    )
    processor = telemetry._build_llm_fanout_processor()
    assert processor is not None

    parent = SpanContext(
        trace_id=0x1111,
        span_id=0x3333,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    agent_span = _readable_span(
        "agent.run",
        parent=parent,
        attributes={telemetry.SpanAttributes.OPENINFERENCE_SPAN_KIND: "AGENT"},
    )
    sql_span = _readable_span("SELECT", parent=parent)
    processor.span_exporter.export([agent_span, sql_span])

    exported = [span for batch in delegate.exported for span in batch]
    assert [span.name for span in exported] == ["agent.run"]
    assert exported[0].parent is None
    processor.shutdown()
