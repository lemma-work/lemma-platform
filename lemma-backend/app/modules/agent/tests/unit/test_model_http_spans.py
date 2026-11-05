"""Every model HTTP request gets a client span, as every other outbound call does.

The model provider SDKs are built on ``httpx2``, a separate package from
``httpx``: instrumenting ``httpx`` does not reach it. When the model clients
moved to it, their requests stopped producing client spans, and a slow or
failing provider call had nothing under the run's span to show for it.

The span carries what the other client spans carry -- method, host, status --
and never the request's headers, where the provider credential travels.
"""

from __future__ import annotations

import asyncio
import tomllib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from opentelemetry.instrumentation.httpx import HTTPX2ClientInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind

from app.core.observability import telemetry
from app.modules.agent.infrastructure.providers import model_factory as factory

pytestmark = pytest.mark.unit

_SECRET = "sk-test-not-a-real-key"


@pytest.fixture
async def provider_url() -> AsyncIterator[str]:
    """A local endpoint that answers every request with an empty JSON object."""

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
            b"content-length: 2\r\nconnection: close\r\n\r\n{}"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        yield f"http://127.0.0.1:{port}"


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """Instrument the libraries as start-up does, into an in-memory exporter."""
    spans = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))

    import opentelemetry.instrumentation.aiohttp_client as aiohttp_mod
    import opentelemetry.instrumentation.httpx as httpx_mod

    # Only httpx2 is under test; the others would patch process-wide.
    monkeypatch.setattr(
        aiohttp_mod.AioHttpClientInstrumentor, "instrument", lambda self, **kw: None
    )
    monkeypatch.setattr(
        httpx_mod.HTTPXClientInstrumentor, "instrument", lambda self, **kw: None
    )
    instrument = HTTPX2ClientInstrumentor.instrument
    monkeypatch.setattr(
        HTTPX2ClientInstrumentor,
        "instrument",
        lambda self, **kw: instrument(self, tracer_provider=provider, **kw),
    )
    monkeypatch.setattr(telemetry, "_libraries_instrumented", False)
    monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", "http")
    try:
        yield spans
    finally:
        HTTPX2ClientInstrumentor().uninstrument()


async def test_a_model_request_gets_a_client_span(
    exporter: InMemorySpanExporter, provider_url: str
) -> None:
    telemetry._instrument_libraries()
    client = factory._build_provider_client()
    try:
        response = await client.post(
            f"{provider_url}/v1/messages",
            headers={"x-api-key": _SECRET, "authorization": f"Bearer {_SECRET}"},
            json={"prompt": "a body that is not recorded"},
        )
        await response.aread()
    finally:
        await client.aclose()

    (span,) = exporter.get_finished_spans()
    attributes = dict(span.attributes or {})
    assert span.kind is SpanKind.CLIENT
    assert attributes["http.request.method"] == "POST"
    assert attributes["server.address"] == "127.0.0.1"
    assert attributes["http.response.status_code"] == 200
    recorded = repr(attributes)
    assert _SECRET not in recorded
    assert "not recorded" not in recorded
    assert not any(key.startswith("http.request.header") for key in attributes)


def test_the_declared_floor_has_the_httpx2_instrumentor() -> None:
    """Start-up calls ``HTTPX2ClientInstrumentor``, which arrived in 0.65b0.

    A floor below it lets a resolver install a version without the class, and
    start-up then fails on an ``AttributeError``. The OTel instrumentation
    packages release together, so they share the floor.
    """
    pyproject = Path(__file__).resolve().parents[5] / "pyproject.toml"
    with pyproject.open("rb") as handle:
        declared = tomllib.load(handle)["project"]["dependencies"]
    floors = {
        requirement.name: requirement.specifier
        for requirement in map(Requirement, declared)
        if requirement.name.startswith("opentelemetry-instrumentation-")
    }

    assert "opentelemetry-instrumentation-httpx" in floors
    for name, specifier in floors.items():
        assert not specifier.contains("0.64b0", prereleases=True), name
        assert specifier.contains("0.65b0", prereleases=True), name


def test_nothing_is_instrumented_with_observability_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instrumented: list[str] = []
    monkeypatch.setattr(
        telemetry, "_instrument_libraries", lambda: instrumented.append("libraries")
    )
    monkeypatch.setattr(telemetry, "_telemetry_initialized", False)
    monkeypatch.setattr(
        telemetry,
        "_get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "otel_sdk_disabled": False,
                "observability_enabled": False,
                "llm_otel_enabled": False,
            },
        )(),
    )

    telemetry.init_telemetry("lemma-test")

    assert instrumented == []
