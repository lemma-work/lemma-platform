from __future__ import annotations

from types import SimpleNamespace
import logging

from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
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

from app.core.observability import otel_logging, telemetry
from app.core.observability.span_sanitizer import (
    SanitizingSpanExporter,
    sanitize_http_route,
)


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
        "lemma_runtime_instance_id": "",
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


def _adversarial_span(
    *,
    http_route: str = "/pods/{pod_id}",
    kind: SpanKind = SpanKind.SERVER,
    scope_name: str = "opentelemetry.instrumentation.fastapi",
    extra: dict | None = None,
) -> ReadableSpan:
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
            "http.route": http_route,
            "url.full": "https://example.test/private?token=CANARY",
            "db.statement": "SELECT 'CANARY'",
            "db.system": "postgresql",
            "server.address": "CANARY.internal",
            "gen_ai.prompt": "CANARY prompt",
            "gen_ai.request.model": "safe-model",
            "lemma.request_id": "request-1",
            "binary": b"CANARY",
            **(extra or {}),
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
        kind=kind,
        instrumentation_scope=InstrumentationScope(scope_name),
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


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("/pods/{pod_id}/datastore/tables/{table_id}/records", True),
        ("/port-access/{token}/{path:path}", True),
        ("/health/ready", True),
        ("/openapi.json", True),
        ("/pods/019f99c1-5a45-739e-ba96-717bc9fccad7/functions", False),
        ("/users/12345", False),
        ("/runs/01K0YZH3VX8FB2M7G5WC6NQ9TR", False),
        ("/files/5383eddca8db622d42865ea7a9e672e15d058018", False),
        ("/reset/AbCdEf0123456789", False),
        ("/users/alice@example.test", False),
        ("https://example.test/health", False),
    ],
)
def test_http_route_export_boundary_rejects_concrete_identifiers(
    route: str,
    expected: bool,
) -> None:
    assert (sanitize_http_route(route) is not None) is expected
    capture = _CaptureExporter()
    exporter = SanitizingSpanExporter(capture)
    assert (
        exporter.export([_adversarial_span(http_route=route)])
        is SpanExportResult.SUCCESS
    )
    assert len(capture.spans) == 1
    assert ("http.route" in capture.spans[0].attributes) is expected


def test_fastapi_route_patch_filters_labels_before_span_and_metric_collection(
    monkeypatch,
) -> None:
    from opentelemetry.instrumentation import fastapi as otel_fastapi

    monkeypatch.setattr(telemetry, "_fastapi_route_details_patched", False)
    monkeypatch.setattr(
        otel_fastapi,
        "_get_route_details",
        lambda scope: scope["route"],
    )
    telemetry._patch_fastapi_route_details()

    assert otel_fastapi._get_route_details({"route": "/pods/{pod_id}"}) == (
        "/pods/{pod_id}"
    )
    assert (
        otel_fastapi._get_route_details(
            {"route": "/pods/019f99c1-5a45-739e-ba96-717bc9fccad7"}
        )
        is None
    )


@pytest.mark.parametrize(
    "key, value",
    [
        # Per-route error rate. The counter emits this and the FastAPI
        # histogram emits it too, which is what makes the two joinable.
        ("http.response.status_code", 503),
        # Which third party a client call went to.
        ("server.address", "api.example-provider.com"),
        # Connection-pool identity and slot state, which have to survive
        # together or the pool metric collapses to one label-less series.
        ("pool.name", "primary"),
        ("state", "used"),
        # Worker queue identity for the depth gauge.
        ("lane", "bulk"),
    ],
)
def test_metric_labels_the_dashboards_need_survive_the_export_boundary(
    key, value
) -> None:
    from app.core.observability.span_sanitizer import METRIC_ATTRIBUTE_KEYS

    assert key in METRIC_ATTRIBUTE_KEYS, (
        f"{key!r} is emitted but would be stripped by the metric view; a "
        "dashboard cannot group by a label that never leaves the process"
    )
    assert isinstance(value, (str, int))


def test_tenancy_reaches_spans_and_stays_off_metrics() -> None:
    """Tenant attribution belongs on spans, never on metric labels.

    A span attribute costs storage proportional to sampled traffic. The same
    key as a metric label multiplies every series by the customer count, which
    is how a cardinality incident starts.
    """
    from app.core.observability.span_sanitizer import (
        GENERAL_SPAN_ATTRIBUTE_KEYS,
        METRIC_ATTRIBUTE_KEYS,
    )

    assert "lemma.organization_id" in GENERAL_SPAN_ATTRIBUTE_KEYS
    assert "lemma.organization_id" not in METRIC_ATTRIBUTE_KEYS

    capture = _CaptureExporter()
    exporter = SanitizingSpanExporter(capture)
    assert (
        exporter.export(
            [_adversarial_span(extra={"lemma.organization_id": "org-abc123"})]
        )
        is SpanExportResult.SUCCESS
    )
    assert capture.spans[0].attributes["lemma.organization_id"] == "org-abc123"


def test_every_process_identifies_itself_on_the_resource(monkeypatch) -> None:
    """Replicas must be distinguishable or a Prometheus backend rejects them.

    Without this, every replica exports a byte-identical resource, the
    collector derives one target from it, and concurrent writers to the same
    series are dropped as duplicate samples.
    """
    monkeypatch.setattr(telemetry, "_get_settings", _settings)
    monkeypatch.setattr(telemetry.socket, "gethostname", lambda: "lemma-api-7d9f-2xk4")

    resource = telemetry._build_resource("lemma-api")

    assert resource.attributes["service.instance.id"] == "lemma-api-7d9f-2xk4"

    from app.core.observability.span_sanitizer import RESOURCE_ATTRIBUTE_KEYS

    assert "service.instance.id" in RESOURCE_ATTRIBUTE_KEYS


def test_an_explicit_instance_id_wins_over_the_hostname(monkeypatch) -> None:
    monkeypatch.setattr(
        telemetry,
        "_get_settings",
        lambda: _settings(lemma_runtime_instance_id="launch-123"),
    )
    monkeypatch.setattr(telemetry.socket, "gethostname", lambda: "some-host")

    resource = telemetry._build_resource("lemma-api")

    assert resource.attributes["service.instance.id"] == "launch-123"


def test_an_unusable_hostname_is_omitted_rather_than_exported(monkeypatch) -> None:
    """The resource crosses the export boundary, so it gets the same scrutiny."""
    monkeypatch.setattr(telemetry, "_get_settings", _settings)
    monkeypatch.setattr(
        telemetry.socket, "gethostname", lambda: "host with spaces/and-slashes"
    )

    resource = telemetry._build_resource("lemma-api")

    assert "service.instance.id" not in resource.attributes


def test_http_clients_are_instrumented_under_stable_semconv(monkeypatch) -> None:
    """All three HTTP clients must speak one vocabulary.

    pyqwest (via e2b and connectrpc) already emits the stable conventions, so
    aiohttp and httpx have to reach them too. This variable is process-global
    and the ASGI server instrumentation reads it as well, so setting it renames
    the inbound latency histogram too -- which is why this was ``http/dup``
    while the dashboards were still on the old name.

    They have moved: every inbound-latency panel reads
    ``http.server.request.duration`` now, and the superseded
    ``http.server.duration`` was still being emitted and paid for at 26 series.
    ``dup`` was always the migration step, not the destination.
    """
    monkeypatch.delenv("OTEL_SEMCONV_STABILITY_OPT_IN", raising=False)
    monkeypatch.setattr(telemetry, "_libraries_instrumented", False)
    instrumented: list[str] = []

    import opentelemetry.instrumentation.aiohttp_client as aiohttp_mod
    import opentelemetry.instrumentation.httpx as httpx_mod

    monkeypatch.setattr(
        aiohttp_mod.AioHttpClientInstrumentor,
        "instrument",
        lambda self, **kwargs: instrumented.append("aiohttp"),
    )
    monkeypatch.setattr(
        httpx_mod.HTTPXClientInstrumentor,
        "instrument",
        lambda self, **kwargs: instrumented.append("httpx"),
    )

    telemetry._instrument_libraries()

    assert instrumented == ["aiohttp", "httpx"]
    assert telemetry.os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] == "http"


def test_a_deployment_can_still_pin_the_old_http_conventions(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", "")
    monkeypatch.setattr(telemetry, "_libraries_instrumented", False)

    import opentelemetry.instrumentation.aiohttp_client as aiohttp_mod
    import opentelemetry.instrumentation.httpx as httpx_mod

    monkeypatch.setattr(
        aiohttp_mod.AioHttpClientInstrumentor, "instrument", lambda self, **kw: None
    )
    monkeypatch.setattr(
        httpx_mod.HTTPXClientInstrumentor, "instrument", lambda self, **kw: None
    )

    telemetry._instrument_libraries()

    assert telemetry.os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] == ""


def test_exporter_drops_only_fastapi_asgi_internal_spans() -> None:
    capture = _CaptureExporter()
    exporter = SanitizingSpanExporter(capture)
    assert (
        exporter.export(
            [
                _adversarial_span(
                    kind=SpanKind.INTERNAL,
                    scope_name="opentelemetry.instrumentation.fastapi",
                ),
                _adversarial_span(
                    kind=SpanKind.INTERNAL,
                    scope_name="opentelemetry.instrumentation.asgi",
                ),
                _adversarial_span(kind=SpanKind.SERVER),
                _adversarial_span(
                    kind=SpanKind.CLIENT,
                    scope_name="opentelemetry.instrumentation.httpx",
                ),
                _adversarial_span(
                    kind=SpanKind.INTERNAL,
                    scope_name="app.core.background_jobs",
                ),
                _adversarial_span(
                    kind=SpanKind.INTERNAL,
                    scope_name="app.fastapi_adapter",
                ),
                _adversarial_span(
                    kind=SpanKind.INTERNAL,
                    scope_name="custom.asgi.pipeline",
                ),
            ]
        )
        is SpanExportResult.SUCCESS
    )
    assert [span.kind for span in capture.spans] == [
        SpanKind.SERVER,
        SpanKind.CLIENT,
        SpanKind.INTERNAL,
        SpanKind.INTERNAL,
        SpanKind.INTERNAL,
    ]


def test_llm_pipeline_enables_content_and_uses_dedicated_provider(
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
    # Patched on pydantic_ai itself rather than through `telemetry.Agent`.
    # Telemetry imports Agent inside `_setup_llm_tracing` so that a deployment
    # with LLM tracing switched off never pays pydantic_ai's import, which
    # means there is no module attribute to reach through -- and patching the
    # class where it is defined is what these assertions were always about.
    from pydantic_ai import Agent

    monkeypatch.setattr(
        Agent,
        "instrument_all",
        lambda instrumentation_settings: captured.append(instrumentation_settings),
    )
    provider = telemetry._setup_llm_tracing("lemma-test")
    try:
        assert provider is not None
        assert len(captured) == 1
        instrumentation = captured[0]
        assert instrumentation.include_content is True
        assert instrumentation.include_binary_content is False
        assert instrumentation.tracer.resource is provider.resource
        # Content must land in span attributes, not log records, so the dedicated
        # LLM tracer is the only thing that ever carries it. pydantic-ai 2.x dropped
        # `event_mode`/`logger_provider` and made attributes the only behaviour, so
        # the guarantee is now that no logs pipeline is wired in at all.
        assert not hasattr(instrumentation, "event_mode")
        assert getattr(instrumentation, "logger_provider", None) is None
        assert instrumentation.version == 2
    finally:
        if provider is not None:
            provider.shutdown()


def test_llm_pipeline_exports_full_content_without_sanitization(monkeypatch) -> None:
    """The LLM pipeline is opt-in and isolated specifically to let developers
    review real prompts/responses, so — unlike the general pipeline — it must
    not run spans through SanitizingSpanExporter's allowlist/truncation."""
    configured = _settings(
        llm_otel_enabled=True,
        llm_otel_exporter_otlp_endpoint="http://phoenix:4317",
        llm_otel_traces_sampler="always_on",
    )
    monkeypatch.setattr(telemetry, "_get_settings", lambda: configured)
    capture = _CaptureExporter()
    monkeypatch.setattr(
        telemetry, "_build_span_exporter", lambda *args, **kwargs: capture
    )
    from pydantic_ai import Agent

    monkeypatch.setattr(Agent, "instrument_all", lambda instrumentation_settings: None)
    provider = telemetry._setup_llm_tracing("lemma-test")
    assert provider is not None
    try:
        long_prompt = "You are a helpful assistant. " * 20
        assert len(long_prompt) > 256
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("llm.call") as span:
            span.set_attribute(
                SpanAttributes.OPENINFERENCE_SPAN_KIND,
                OpenInferenceSpanKindValues.LLM.value,
            )
            span.set_attribute(SpanAttributes.INPUT_VALUE, long_prompt)
        assert provider.force_flush()
        assert len(capture.spans) == 1
        exported_value = capture.spans[0].attributes[SpanAttributes.INPUT_VALUE]
        assert exported_value == long_prompt
    finally:
        provider.shutdown()


def test_otel_log_handler_constructs_only_bounded_records(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(
        LoggingHandler,
        "emit",
        lambda _self, record: captured.append(record),
    )
    handler = otel_logging.SanitizingLoggingHandler(
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


def test_otel_log_handler_preserves_redacted_dependency_record_message(
    monkeypatch,
) -> None:
    captured = []
    monkeypatch.setattr(
        LoggingHandler,
        "emit",
        lambda _self, record: captured.append(record),
    )
    handler = otel_logging.SanitizingLoggingHandler(
        logger_provider=LoggerProvider(),
    )
    original = logging.LogRecord(
        "httpx",
        logging.WARNING,
        "/private/CANARY.py",
        99,
        "request failed token=%s",
        ("CANARY",),
        None,
    )
    handler.emit(original)
    assert len(captured) == 1
    safe = captured[0]
    assert safe.msg == "request failed token=[REDACTED]"
    assert safe.name == "httpx"
    assert safe.levelno == logging.WARNING
    assert "CANARY" not in str(safe.msg)


@pytest.mark.parametrize("value", ["console", "otlp,console", "invalid"])
def test_unknown_exporters_fail_closed(monkeypatch, value: str) -> None:
    configured = _settings(
        model_fields_set={"otel_logs_exporter"},
        otel_logs_exporter=value,
    )
    monkeypatch.setattr(telemetry, "_get_settings", lambda: configured)
    with pytest.raises(ValueError, match="unsupported otel_logs_exporter"):
        telemetry._enabled_signals()


def test_a_failed_provider_build_is_reported_and_leaves_telemetry_retryable(
    monkeypatch, caplog
) -> None:
    """`LOG_LEVEL=INFO` drops DEBUG before formatting, so a DEBUG record here is
    an operator who turned observability on, saw nothing arrive, and had no line
    to start from — and `_telemetry_initialized = True` made the first attempt
    the only one.

    The endpoint is a malformed IPv6 collector address, which is what the gRPC
    exporter actually refuses at construction: the failure is produced rather
    than injected, so the path this covers is the real one.
    """
    configured = _settings(
        otel_exporter_otlp_endpoint="http://[::1",
        observability_enabled=True,
        otel_sdk_disabled=False,
    )
    monkeypatch.setattr(telemetry, "_get_settings", lambda: configured)
    monkeypatch.setattr(telemetry, "_telemetry_initialized", False)
    monkeypatch.setattr(telemetry, "_trace_provider", None)

    with caplog.at_level("WARNING"):
        telemetry.init_telemetry("lemma-test")

    assert "observability.telemetry.setup_failed.degraded" in caplog.text
    assert "ValueError" in caplog.text
    assert telemetry._telemetry_initialized is False


def test_a_provider_that_cannot_flush_at_shutdown_says_so(monkeypatch, caplog) -> None:
    """A final flush that drops the last spans of a run is indistinguishable
    afterwards from a run that produced none. Both halves used to be
    ``except Exception: pass``."""

    class _StuckProvider:
        def force_flush(self, timeout_millis: int = 0) -> bool:
            raise TimeoutError("collector did not answer")

        def shutdown(self) -> None:
            raise RuntimeError("already stopped")

    monkeypatch.setattr(telemetry, "_trace_provider", _StuckProvider())
    monkeypatch.setattr(telemetry, "_llm_trace_provider", None)
    monkeypatch.setattr(telemetry, "_meter_provider", None)
    monkeypatch.setattr(telemetry, "_logger_provider", None)

    with caplog.at_level("WARNING"):
        telemetry.shutdown_telemetry()

    assert caplog.text.count("observability.telemetry.shutdown_step_failed") == 2
    assert "TimeoutError" in caplog.text
    assert "RuntimeError" in caplog.text
