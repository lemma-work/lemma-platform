from __future__ import annotations

import inspect
import json

import httpx

from lemma_sdk import Lemma
from lemma_sdk.resources.agent_hosts import AgentHosts


def _lemma(handler) -> tuple[Lemma, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    lemma = Lemma(token="token", base_url="https://api.example.test")
    lemma._transport.generated.set_httpx_client(
        httpx.Client(
            transport=httpx.MockTransport(record),
            base_url="https://api.example.test",
        )
    )
    return lemma, seen


def test_create_pairing_sends_only_a_display_name():
    # `organization_id` used to be in the signature and was then assigned onto
    # an attrs slots model with no such field, so every call passing it raised
    # AttributeError from inside the SDK. The endpoint pairs a machine to the
    # *user*; it has no organization dimension to send one to.
    assert list(inspect.signature(AgentHosts.create_pairing).parameters) == [
        "self",
        "display_name",
    ]

    lemma, seen = _lemma(
        lambda _: httpx.Response(
            200,
            json={
                "expires_at": "2026-01-01T00:00:00Z",
                "pairing_code": "code-1",
                "pairing_id": "55555555-5555-4555-8555-555555555555",
            },
        )
    )

    created = lemma.agent_hosts.create_pairing(display_name="laptop")

    assert created.pairing_code == "code-1"
    assert json.loads(seen[0].content) == {"display_name": "laptop"}
