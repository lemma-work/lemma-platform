"""Chosen accounts are always traced; everyone else follows the ratio."""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF
from opentelemetry.trace import SpanKind

from app.core.observability.forced_sampling import (
    ForcibleSampler,
    parse_user_ids,
    unverified_subject,
    wrap_outermost,
)

pytestmark = pytest.mark.unit

_ME = "0199a1b2-0000-7000-8000-000000000001"


def _token(sub: str) -> str:
    def part(value: dict[str, object]) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
        return raw.rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part({'sub': sub})}.not-a-signature"


@pytest.fixture
def served():
    """An app that samples nothing by ratio, with ``_ME`` allowlisted."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ForcibleSampler(ALWAYS_OFF))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = FastAPI()

    @app.get("/pods")
    async def pods() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/private")
    async def private() -> None:
        raise HTTPException(status_code=401)

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    wrap_outermost(app, f" {_ME} , other")
    try:
        yield TestClient(app), exporter
    finally:
        FastAPIInstrumentor.uninstrument_app(app)


def _server_spans(exporter: InMemorySpanExporter) -> list[object]:
    return [s for s in exporter.get_finished_spans() if s.kind is SpanKind.SERVER]


def test_an_allowlisted_bearer_is_always_traced(served) -> None:
    client, exporter = served
    client.get("/pods", headers={"Authorization": f"Bearer {_token(_ME)}"})
    assert len(_server_spans(exporter)) == 1


def test_an_allowlisted_session_cookie_is_always_traced(served) -> None:
    client, exporter = served
    client.cookies.set("sAccessToken", _token(_ME))
    client.get("/pods")
    assert len(_server_spans(exporter)) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": f"Bearer {_token('someone-else')}"},
        {"Authorization": "Bearer not.a-jwt"},
        {"Authorization": f"Basic {_token(_ME)}"},
    ],
)
def test_everyone_else_follows_the_ratio(served, headers) -> None:
    client, exporter = served
    client.get("/pods", headers=headers)
    assert _server_spans(exporter) == []


def test_forcing_a_sample_never_changes_the_answer(served) -> None:
    """The token is unverified: it may decide sampling, never access."""
    client, exporter = served
    response = client.get(
        "/private", headers={"Authorization": f"Bearer {_token(_ME)}"}
    )
    assert response.status_code == 401
    assert len(_server_spans(exporter)) == 1


def test_nothing_is_installed_when_no_account_is_named() -> None:
    app = FastAPI()
    before = app.build_middleware_stack
    wrap_outermost(app, " , ")
    assert app.build_middleware_stack == before
    assert parse_user_ids("a, b,,") == frozenset({"a", "b"})


def test_an_oversized_or_garbled_token_names_nobody() -> None:
    assert (
        unverified_subject([(b"authorization", b"Bearer " + b"a.b.c" * 4000)]) is None
    )
    assert unverified_subject([(b"authorization", b"Bearer a.!!!.c")]) is None


def test_a_forced_span_keeps_its_parents_tracestate() -> None:
    from opentelemetry import trace
    from opentelemetry.trace import (
        NonRecordingSpan,
        SpanContext,
        TraceFlags,
        TraceState,
    )

    from app.core.observability import forced_sampling

    state = TraceState([("vendor", "value")])
    parent = trace.set_span_in_context(
        NonRecordingSpan(
            SpanContext(
                trace_id=1,
                span_id=2,
                is_remote=True,
                trace_flags=TraceFlags(0),
                trace_state=state,
            )
        )
    )
    token = forced_sampling._forced.set(True)
    try:
        result = ForcibleSampler(ALWAYS_OFF).should_sample(parent, 1, "GET /pods")
    finally:
        forced_sampling._forced.reset(token)
    assert result.decision.is_sampled()
    assert result.trace_state == state
