"""HTTP ingress validation, correlation minting, and context isolation."""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.app import RequestIdMiddleware
from app.core.request_context import current_observability_context


async def _run(inbound_headers):
    captured: dict = {}
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    async def inner_app(scope, _receive, _send):
        captured["request_headers"] = scope.get("headers")
        captured["context"] = current_observability_context()
        await _send({"type": "http.response.start", "status": 200, "headers": []})
        await _send({"type": "http.response.body", "body": b""})

    middleware = RequestIdMiddleware(inner_app)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/unit-test",
        "headers": list(inbound_headers),
    }
    await middleware(scope, receive, send)

    start = next(message for message in sent if message["type"] == "http.response.start")
    return captured, dict(start["headers"])


async def test_mints_request_and_server_controlled_correlation_ids() -> None:
    captured, response_headers = await _run([])
    minted = response_headers[b"x-request-id"].decode()
    assert len(minted) == 32
    assert captured["context"].request_id == minted
    assert isinstance(captured["context"].correlation_id, UUID)
    assert current_observability_context().as_log_fields() == {}


async def test_reuses_only_valid_bounded_request_id() -> None:
    captured, response_headers = await _run([(b"x-request-id", b"abc123")])
    assert response_headers[b"x-request-id"] == b"abc123"
    assert captured["context"].request_id == "abc123"


async def test_invalid_request_id_is_replaced_without_binding_rejected_value() -> None:
    rejected = b"secret value with spaces" + b"x" * 128
    captured, response_headers = await _run([(b"x-request-id", rejected)])
    replacement = response_headers[b"x-request-id"]
    assert replacement != rejected
    assert len(replacement) == 32
    assert captured["context"].request_id == replacement.decode()


async def test_public_caller_cannot_choose_correlation_or_event_lineage() -> None:
    supplied = b"11111111-1111-1111-1111-111111111111"
    captured, _ = await _run(
        [
            (b"x-lemma-correlation-id", supplied),
            (b"x-lemma-event-id", supplied),
            (b"x-lemma-job-id", b"attacker-job"),
        ]
    )
    context = captured["context"]
    assert str(context.correlation_id).encode() != supplied
    assert context.event_id is None
    assert context.job_id is None


async def test_concurrent_requests_do_not_leak_context() -> None:
    first, second = await asyncio.gather(
        _run([(b"x-request-id", b"request-one")]),
        _run([(b"x-request-id", b"request-two")]),
    )
    first_context = first[0]["context"]
    second_context = second[0]["context"]
    assert first_context.request_id == "request-one"
    assert second_context.request_id == "request-two"
    assert first_context.correlation_id != second_context.correlation_id
    assert current_observability_context().as_log_fields() == {}


async def test_response_contains_exactly_one_normalized_request_id() -> None:
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    async def inner_app(scope, _receive, _send):
        del scope
        await _send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-request-id", b"inner")],
            }
        )

    middleware = RequestIdMiddleware(inner_app)
    await middleware(
        {
            "type": "http",
            "method": "GET",
            "path": "/unit-test",
            "headers": [(b"x-request-id", b"ingress")],
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    ids = [value for key, value in start["headers"] if key == b"x-request-id"]
    assert ids == [b"ingress"]
