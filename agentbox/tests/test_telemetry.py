from __future__ import annotations

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
import agentbox.telemetry as telemetry


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
