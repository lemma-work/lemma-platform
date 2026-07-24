from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
import pytest

from agentbox.api.app import RequestContextMiddleware
from agentbox.config import settings
from agentbox.domain import (
    AgentBoxError,
    ErrorCode,
    RetryDisposition,
    SandboxProfileRef,
    WorkloadKind,
)
import agentbox.telemetry as telemetry


@pytest.fixture
def captured_telemetry(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        telemetry,
        "_tracer",
        provider.get_tracer("agentbox.telemetry", "test"),
    )
    yield exporter
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


def test_disabled_trace_exporter_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(settings, "observability_enabled", True)
    monkeypatch.setattr(settings, "otel_sdk_disabled", False)
    monkeypatch.setattr(settings, "otel_traces_exporter", "none")
    monkeypatch.setattr(telemetry, "_trace_provider", None)
    monkeypatch.setattr(
        telemetry,
        "_span_exporter",
        lambda _endpoint: pytest.fail("disabled trace exporter was constructed"),
    )

    telemetry.setup_telemetry()

    assert telemetry._trace_provider is None


def test_enabled_exporter_requires_managed_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(settings, "observability_enabled", True)
    monkeypatch.setattr(settings, "otel_sdk_disabled", False)
    monkeypatch.setattr(settings, "otel_traces_exporter", "otlp")
    monkeypatch.setattr(settings, "otel_exporter_otlp_endpoint", None)
    monkeypatch.setattr(settings, "otel_exporter_otlp_traces_endpoint", None)
    monkeypatch.setattr(telemetry, "_trace_provider", None)

    with pytest.raises(RuntimeError, match="managed OTLP endpoint is missing"):
        telemetry.setup_telemetry()


def test_fastapi_owns_w3c_extraction_and_exports_only_safe_route(
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

    monkeypatch.setattr(telemetry, "_trace_provider", provider)
    telemetry.instrument_fastapi_app(app)
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


def test_agentbox_operation_emits_bounded_span_and_terminal_event(
    captured_telemetry,
    caplog,
) -> None:
    with caplog.at_level(logging.INFO):
        with telemetry.observe_agentbox_operation(
            operation="ensure",
            workload_kind=WorkloadKind.FUNCTION,
            provider="e2b",
            profile=_profile(),
        ):
            pass

    spans = captured_telemetry.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "agentbox.ensure"
    assert spans[0].attributes == {
        "agentbox.operation": "ensure",
        "agentbox.workload.kind": "function",
        "agentbox.provider": "e2b",
        "agentbox.profile": settings.agentbox_function_profile_name,
        "agentbox.outcome": "success",
    }
    terminal = next(
        record
        for record in caplog.records
        if record.msg == "agentbox.operation.completed"
    )
    assert terminal.lemma_fields["operation"] == "ensure"
    assert "logical_id" not in terminal.lemma_fields


@pytest.mark.parametrize(
    ("code", "outcome"),
    [
        (ErrorCode.CAPACITY_EXHAUSTED, "rejected"),
        (ErrorCode.DEADLINE_EXCEEDED, "timeout"),
    ],
)
def test_agentbox_operation_classifies_rejection_and_timeout(
    captured_telemetry,
    caplog,
    code,
    outcome,
) -> None:
    error = AgentBoxError(
        code,
        "CANARY provider response",
        retry=RetryDisposition.WAIT,
        status_code=429,
    )

    observed_error = None
    with caplog.at_level(logging.WARNING):
        try:
            with telemetry.observe_agentbox_operation(
                operation="ensure",
                workload_kind=WorkloadKind.WORKSPACE,
                provider="e2b",
                profile=_profile(),
            ):
                raise error
        except AgentBoxError as exc:
            observed_error = exc

    assert observed_error is error
    span = captured_telemetry.get_finished_spans()[0]
    assert span.attributes["agentbox.outcome"] == outcome
    assert span.attributes["error.type"] == "AgentBoxError"
    assert "CANARY" not in str(span.attributes)
    event_suffix = "rejected" if outcome == "rejected" else "timed_out"
    terminal = next(
        record
        for record in caplog.records
        if record.msg == f"agentbox.operation.{event_suffix}"
    )
    assert len(terminal.lemma_fields["error_fingerprint"]) == 64
    assert "CANARY" not in repr(terminal.lemma_fields)


@pytest.mark.asyncio
async def test_phase_emits_bounded_span_without_extra_terminal_log(
    captured_telemetry,
    caplog,
) -> None:
    async def result() -> str:
        return "provider payload is never observed"

    with caplog.at_level(logging.INFO):
        observed = await telemetry.observe_phase(
            result(),
            phase="sandbox_readiness",
            workload_kind=WorkloadKind.FUNCTION,
            provider="e2b",
            profile=_profile(),
        )

    assert observed == "provider payload is never observed"
    assert captured_telemetry.get_finished_spans()[0].attributes == {
        "agentbox.workload.kind": "function",
        "agentbox.provider": "e2b",
        "agentbox.profile": settings.agentbox_function_profile_name,
        "agentbox.phase": "sandbox_readiness",
        "agentbox.outcome": "success",
    }
    assert not [
        record
        for record in caplog.records
        if record.msg == "agentbox.operation.completed"
    ]


def test_unknown_provider_and_profile_collapse_to_other(
    captured_telemetry,
) -> None:
    profile = SandboxProfileRef("user-controlled-profile", "sha256:" + "c" * 64)
    with telemetry.observe_agentbox_operation(
        operation="ensure",
        workload_kind=WorkloadKind.WORKSPACE,
        provider="user-controlled-provider",
        profile=profile,
    ):
        pass

    attributes = captured_telemetry.get_finished_spans()[0].attributes
    assert attributes["agentbox.provider"] == "other"
    assert attributes["agentbox.profile"] == "other"


@pytest.mark.asyncio
async def test_control_operation_keeps_success_at_debug(
    captured_telemetry,
    caplog,
) -> None:
    @telemetry.observed_control_operation("cleanup")
    async def cleanup() -> int:
        return 2

    with caplog.at_level(logging.DEBUG):
        assert await cleanup() == 2

    assert captured_telemetry.get_finished_spans()[0].name == "agentbox.cleanup"
    terminal = next(
        record
        for record in caplog.records
        if record.msg == "agentbox.cleanup.completed"
    )
    assert terminal.lemma_fields["count"] == 2
