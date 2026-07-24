from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
import pytest

from agentbox.config import settings
from agentbox.domain import (
    AgentBoxError,
    ErrorCode,
    RetryDisposition,
    SandboxProfileRef,
    WorkloadKind,
)
import agentbox.telemetry as telemetry
from agentbox.api.app import RequestContextMiddleware


@dataclass
class _CaptureInstrument:
    points: list[tuple[float, dict[str, Any]]] = field(default_factory=list)

    def add(self, value: float, attributes: dict[str, Any]) -> None:
        self.points.append((value, dict(attributes)))

    def record(self, value: float, attributes: dict[str, Any]) -> None:
        self.points.append((value, dict(attributes)))


class _CaptureInstruments:
    def __init__(self) -> None:
        self.operations = _CaptureInstrument()
        self.operation_duration = _CaptureInstrument()
        self.active = _CaptureInstrument()
        self.admission_wait = _CaptureInstrument()
        self.sandbox_start = _CaptureInstrument()
        self.rejections = _CaptureInstrument()
        self.timeouts = _CaptureInstrument()
        self.cleanup = _CaptureInstrument()
        self.reconcile = _CaptureInstrument()
        self.capacity: list[dict[str, Any]] = []

    def record_capacity(self, **values: Any) -> None:
        self.capacity.append(values)


@pytest.fixture
def captured_telemetry(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instruments = _CaptureInstruments()
    monkeypatch.setattr(
        telemetry,
        "_tracer",
        provider.get_tracer("agentbox.telemetry", "test"),
    )
    monkeypatch.setattr(telemetry, "_instruments", instruments)
    yield exporter, instruments
    provider.shutdown()


def _profile() -> SandboxProfileRef:
    return SandboxProfileRef(
        settings.agentbox_function_profile_name,
        "sha256:" + "a" * 64,
    )


def test_resource_identity_is_stable_and_complete(monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "release_sha", "b" * 40)
    monkeypatch.setattr(settings, "otel_service_namespace", None)
    resource = telemetry._resource()
    assert {
        key: resource.attributes[key]
        for key in (
            "service.namespace",
            "service.name",
            "service.version",
            "deployment.environment.name",
            "cloud.provider",
            "cloud.platform",
        )
    } == {
        "service.namespace": "lemma",
        "service.name": "lemma-agentbox",
        "service.version": "b" * 40,
        "deployment.environment.name": "production",
        "cloud.provider": "azure",
        "cloud.platform": "azure_container_apps",
    }


def test_disabled_exporters_leave_custom_metrics_noop(monkeypatch) -> None:
    monkeypatch.setattr(settings, "observability_enabled", True)
    monkeypatch.setattr(settings, "otel_sdk_disabled", False)
    monkeypatch.setattr(settings, "otel_traces_exporter", "none")
    monkeypatch.setattr(settings, "otel_metrics_exporter", "none")
    monkeypatch.setattr(telemetry, "_initialized", False)
    monkeypatch.setattr(telemetry, "_instruments", None)
    monkeypatch.setattr(
        telemetry,
        "_span_exporter",
        lambda _endpoint: pytest.fail("disabled trace exporter was constructed"),
    )
    monkeypatch.setattr(
        telemetry,
        "_metric_exporter",
        lambda _endpoint: pytest.fail("disabled metric exporter was constructed"),
    )
    telemetry.setup_telemetry()
    assert telemetry._instruments is None


def test_enabled_exporter_requires_managed_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(settings, "observability_enabled", True)
    monkeypatch.setattr(settings, "otel_sdk_disabled", False)
    monkeypatch.setattr(settings, "otel_traces_exporter", "otlp")
    monkeypatch.setattr(settings, "otel_metrics_exporter", "none")
    monkeypatch.setattr(settings, "otel_exporter_otlp_endpoint", None)
    monkeypatch.setattr(settings, "otel_exporter_otlp_traces_endpoint", None)
    monkeypatch.setattr(telemetry, "_initialized", False)
    with pytest.raises(RuntimeError, match="managed OTLP endpoint is missing"):
        telemetry.setup_telemetry()


def test_fastapi_extracts_w3c_context_and_exports_only_safe_route(
    monkeypatch,
) -> None:
    capture = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.namespace": "lemma",
                "service.name": "lemma-agentbox",
                "service.version": "d" * 40,
                "deployment.environment.name": "development",
                "cloud.provider": "azure",
                "cloud.platform": "azure_container_apps",
            }
        )
    )
    provider.add_span_processor(
        SimpleSpanProcessor(telemetry.SanitizingSpanExporter(capture))
    )
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/items/{item_id}")
    async def item(item_id: str) -> dict[str, bool]:
        del item_id
        return {"ok": True}

    monkeypatch.setattr(settings, "observability_enabled", True)
    monkeypatch.setattr(settings, "otel_sdk_disabled", False)
    monkeypatch.setattr(telemetry, "_trace_provider", provider)
    monkeypatch.setattr(telemetry, "_meter_provider", None)
    telemetry.instrument_fastapi_app(app)
    trace_id = "1" * 32
    response = TestClient(app).get(
        "/items/CANARY-private-id",
        headers={
            "traceparent": f"00-{trace_id}-{'2' * 16}-01",
            "x-request-id": "request-1",
        },
    )
    provider.force_flush()
    provider.shutdown()

    assert response.status_code == 200
    server_spans = [
        span for span in capture.get_finished_spans() if span.name == "http.server"
    ]
    assert len(server_spans) == 1
    span = server_spans[0]
    assert f"{span.context.trace_id:032x}" == trace_id
    assert span.attributes["http.route"] == "/items/{item_id}"
    assert span.attributes["lemma.request.id"] == "request-1"
    assert "CANARY" not in span.to_json()


def test_agentbox_operation_emits_bounded_span_metric_and_event(
    captured_telemetry, caplog
) -> None:
    exporter, instruments = captured_telemetry
    with caplog.at_level(logging.INFO):
        with telemetry.observe_agentbox_operation(
            operation="ensure",
            workload_kind=WorkloadKind.FUNCTION,
            provider="e2b",
            profile=_profile(),
        ):
            pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "agentbox.ensure"
    assert spans[0].attributes == {
        "agentbox.operation": "ensure",
        "agentbox.workload.kind": "function",
        "agentbox.provider": "e2b",
        "agentbox.profile": settings.agentbox_function_profile_name,
        "agentbox.outcome": "success",
    }
    assert instruments.operations.points[0][1] == {
        "operation": "ensure",
        "workload_kind": "function",
        "provider": "e2b",
        "profile": settings.agentbox_function_profile_name,
        "outcome": "success",
    }
    assert not (
        {
            "request_id",
            "trace_id",
            "sandbox_id",
            "logical_id",
            "url",
            "payload",
        }
        & instruments.operations.points[0][1].keys()
    )
    records = [
        record
        for record in caplog.records
        if record.msg == "agentbox.operation.completed"
    ]
    assert len(records) == 1
    assert "logical_id" not in records[0].lemma_fields


@pytest.mark.parametrize(
    ("code", "outcome", "instrument"),
    [
        (ErrorCode.CAPACITY_EXHAUSTED, "rejected", "rejections"),
        (ErrorCode.DEADLINE_EXCEEDED, "timeout", "timeouts"),
    ],
)
def test_agentbox_operation_classifies_rejection_and_timeout(
    captured_telemetry, caplog, code, outcome, instrument
) -> None:
    exporter, instruments = captured_telemetry
    error = AgentBoxError(
        code,
        "CANARY provider response",
        retry=RetryDisposition.WAIT,
        status_code=429,
    )
    with caplog.at_level(logging.WARNING):
        with pytest.raises(AgentBoxError):
            with telemetry.observe_agentbox_operation(
                operation="ensure",
                workload_kind=WorkloadKind.WORKSPACE,
                provider="e2b",
                profile=_profile(),
            ):
                raise error

    span = exporter.get_finished_spans()[0]
    assert span.attributes["agentbox.outcome"] == outcome
    assert span.attributes["error.type"] == "AgentBoxError"
    assert "CANARY" not in str(span.attributes)
    assert getattr(instruments, instrument).points[0][0] == 1
    terminal = next(
        record
        for record in caplog.records
        if record.msg
        == f"agentbox.operation.{outcome if outcome == 'rejected' else 'timed_out'}"
    )
    assert len(terminal.lemma_fields["error_fingerprint"]) == 64
    assert "CANARY" not in repr(terminal.lemma_fields)


@pytest.mark.asyncio
async def test_phase_metrics_are_bounded(captured_telemetry) -> None:
    exporter, instruments = captured_telemetry

    async def result() -> str:
        return "provider payload is never observed"

    assert (
        await telemetry.observe_phase(
            result(),
            phase="sandbox_readiness",
            workload_kind=WorkloadKind.FUNCTION,
            provider="e2b",
            profile=_profile(),
        )
        == "provider payload is never observed"
    )
    assert exporter.get_finished_spans()[0].attributes == {
        "agentbox.workload.kind": "function",
        "agentbox.provider": "e2b",
        "agentbox.profile": settings.agentbox_function_profile_name,
        "agentbox.phase": "sandbox_readiness",
        "agentbox.outcome": "success",
    }
    assert instruments.sandbox_start.points[0][1] == {
        "workload_kind": "function",
        "provider": "e2b",
        "profile": settings.agentbox_function_profile_name,
        "phase": "sandbox_readiness",
        "outcome": "success",
    }


def test_unknown_provider_and_profile_collapse_to_other(captured_telemetry) -> None:
    _exporter, instruments = captured_telemetry
    profile = SandboxProfileRef("user-controlled-profile", "sha256:" + "c" * 64)
    with telemetry.observe_agentbox_operation(
        operation="ensure",
        workload_kind=WorkloadKind.WORKSPACE,
        provider="user-controlled-provider",
        profile=profile,
    ):
        pass
    attributes = instruments.operations.points[0][1]
    assert attributes["provider"] == "other"
    assert attributes["profile"] == "other"


def test_real_metric_sdk_exports_reviewed_series_and_duration_buckets() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=(reader,))
    instruments = telemetry._AgentBoxInstruments(
        provider.get_meter("agentbox.telemetry", "test")
    )
    attributes = {
        "operation": "ensure",
        "workload_kind": "function",
        "provider": "e2b",
        "profile": settings.agentbox_function_profile_name,
        "outcome": "success",
    }
    instruments.operations.add(1, attributes)
    instruments.operation_duration.record(125, attributes)
    instruments.record_capacity(provider="e2b", limit=32, active=3, reserved=2)
    metrics_data = reader.get_metrics_data()
    provider.shutdown()

    exported = {
        metric.name: metric
        for resource in metrics_data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert {
        "lemma.agentbox.operations",
        "lemma.agentbox.operation.duration",
        "lemma.agentbox.capacity.limit",
        "lemma.agentbox.capacity.available",
    } <= exported.keys()
    duration = exported["lemma.agentbox.operation.duration"].data.data_points[0]
    assert duration.explicit_bounds == telemetry._DURATION_BOUNDARIES_MS
    assert dict(duration.attributes) == attributes
    capacity = exported["lemma.agentbox.capacity.available"].data.data_points[0]
    assert capacity.value == 27
    assert dict(capacity.attributes) == {"provider": "e2b"}
