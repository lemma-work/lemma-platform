"""A forced sample is a tracing decision, never an authentication one.

The allowlisted account's id is read from the access token WITHOUT verifying
it, so a token anyone can forge reaches the sampler. Here such a token goes
through the real application and the real SuperTokens verification: the
request must be traced, and must still be refused.
"""

from __future__ import annotations

import base64
import json

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF
from opentelemetry.trace import SpanKind

from app.core.observability.forced_sampling import (
    ForceSampleMiddleware,
    ForcibleSampler,
)

pytestmark = [pytest.mark.e2e]

_ALLOWLISTED = "0199a1b2-0000-7000-8000-000000000001"


def _forged_token(sub: str) -> str:
    def part(value: dict[str, object]) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
        return raw.rstrip("=")

    return f"{part({'alg': 'RS256', 'kid': 'x'})}.{part({'sub': sub})}.forged"


async def test_a_forged_allowlisted_token_is_traced_and_still_refused(
    test_app,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ForcibleSampler(ALWAYS_OFF))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # The production order: the marker outside the tracing middleware,
    # around the real application with its real authentication.
    served = ForceSampleMiddleware(
        OpenTelemetryMiddleware(test_app, tracer_provider=provider),
        frozenset({_ALLOWLISTED}),
    )

    async with AsyncClient(
        transport=ASGITransport(app=served), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/users/me",
            headers={"Authorization": f"Bearer {_forged_token(_ALLOWLISTED)}"},
        )

    assert response.status_code == 401, response.text
    servers = [s for s in exporter.get_finished_spans() if s.kind is SpanKind.SERVER]
    assert len(servers) == 1
