from __future__ import annotations

import logging
from types import SimpleNamespace
from uuid import UUID

import pytest

import agentbox.api.app as app_module
from agentbox.config import settings
from agentbox.observability import current_context


async def _run(headers: list[tuple[bytes, bytes]]) -> tuple[dict[str, str], bytes]:
    observed: dict[str, str] = {}
    sent: list[dict] = []

    async def app(scope, receive, send):
        del scope, receive
        observed.update(current_context())
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app_module.RequestContextMiddleware(app)(
        {"type": "http", "headers": headers}, receive, send
    )
    response = next(item for item in sent if item["type"] == "http.response.start")
    return observed, dict(response["headers"])[b"x-request-id"]


@pytest.mark.asyncio
async def test_authenticated_manager_request_binds_full_lineage(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agentbox_api_key", "manager-key")
    correlation_id = UUID("11111111-1111-1111-1111-111111111111")
    event_id = UUID("22222222-2222-2222-2222-222222222222")
    observed, response_request_id = await _run(
        [
            (b"x-api-key", b"manager-key"),
            (b"x-request-id", b"request-1"),
            (b"x-lemma-correlation-id", str(correlation_id).encode()),
            (b"x-lemma-event-id", str(event_id).encode()),
            (b"x-lemma-job-id", b"job-1"),
        ]
    )
    assert observed == {
        "request_id": "request-1",
        "correlation_id": str(correlation_id),
        "event_id": str(event_id),
        "job_id": "job-1",
    }
    assert response_request_id == b"request-1"
    assert current_context() == {}


@pytest.mark.asyncio
async def test_untrusted_request_cannot_inject_internal_lineage(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agentbox_api_key", "manager-key")
    supplied = b"11111111-1111-1111-1111-111111111111"
    observed, _ = await _run(
        [
            (b"x-api-key", b"wrong"),
            (b"x-lemma-correlation-id", supplied),
            (b"x-lemma-event-id", supplied),
            (b"x-lemma-job-id", b"forged-job"),
        ]
    )
    assert observed.get("correlation_id") != supplied.decode()
    assert "event_id" not in observed
    assert "job_id" not in observed


@pytest.mark.asyncio
async def test_invalid_identifiers_are_replaced_or_omitted(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agentbox_api_key", "manager-key")
    rejected = b"contains spaces" + b"x" * 128
    observed, response_request_id = await _run(
        [
            (b"x-api-key", b"manager-key"),
            (b"x-request-id", rejected),
            (b"x-lemma-correlation-id", b"not-a-uuid"),
            (b"x-lemma-event-id", b"also-not-a-uuid"),
            (b"x-lemma-job-id", b"bad job id with spaces"),
        ]
    )
    assert response_request_id != rejected
    assert len(response_request_id) == 32
    assert observed["request_id"] == response_request_id.decode()
    UUID(observed["correlation_id"])
    assert "event_id" not in observed
    assert "job_id" not in observed


@pytest.mark.asyncio
async def test_unhandled_request_emits_one_safe_failure_and_response_id(
    monkeypatch, caplog
) -> None:
    monkeypatch.setattr(settings, "agentbox_api_key", "manager-key")
    sent: list[dict] = []

    async def failing_app(scope, receive, send):
        del scope, receive, send
        raise RuntimeError("CANARY provider payload /private/source.py")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    with caplog.at_level(logging.ERROR):
        await app_module.RequestContextMiddleware(failing_app)(
            {
                "type": "http",
                "method": "POST",
                "path": "/sandboxes/example",
                "headers": [(b"x-request-id", b"request-1")],
            },
            receive,
            send,
        )

    start = next(item for item in sent if item["type"] == "http.response.start")
    assert start["status"] == 500
    assert dict(start["headers"])[b"x-request-id"] == b"request-1"
    failures = [
        record for record in caplog.records if record.msg == "http.request.failed"
    ]
    assert len(failures) == 1
    fields = failures[0].lemma_fields
    assert fields["error_type"] == "RuntimeError"
    assert len(fields["error_stack_hash"]) == 64
    assert "CANARY" not in repr(fields)


@pytest.mark.asyncio
async def test_repeated_429s_emit_one_degraded_and_one_recovered(caplog) -> None:
    app_module._rate_limit_incidents.clear()
    status = 429

    async def downstream(_scope, _receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    middleware = app_module.RequestContextMiddleware(downstream)
    scope = {
        "type": "http",
        "method": "PUT",
        "path": "/sandboxes/function/id",
        "headers": [],
        "route": SimpleNamespace(path_format="/sandboxes/{workload_kind}/{logical_id}"),
    }
    with caplog.at_level(logging.INFO):
        for _ in range(5):
            await middleware(scope, receive, send)
        status = 200
        await middleware(scope, receive, send)

    events = [record.msg for record in caplog.records]
    assert events.count("dependency.degraded") == 1
    assert events.count("dependency.recovered") == 1


@pytest.mark.asyncio
async def test_verify_ready_cold_start_uses_route_aware_slow_threshold(
    monkeypatch,
    caplog,
) -> None:
    observed_state: dict[str, str] = {}

    async def downstream(scope, _receive, send):
        observed_state.update(scope["state"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    times = iter((0.0, 0.1, 2.6))
    monkeypatch.setattr(app_module.time, "perf_counter", lambda: next(times))
    scope = {
        "type": "http",
        "method": "PUT",
        "path": "/sandboxes/workspace/00000000-0000-0000-0000-000000000001",
        "query_string": b"verify_ready=true",
        "headers": [],
        "state": {},
        "route": SimpleNamespace(path_format="/sandboxes/{workload_kind}/{logical_id}"),
    }
    with caplog.at_level(logging.WARNING):
        await app_module.RequestContextMiddleware(downstream)(
            scope,
            receive,
            send,
        )

    assert observed_state["lemma_request_observer_owner"] == "agentbox"
    assert not any(record.msg == "http.request.slow" for record in caplog.records)


@pytest.mark.asyncio
async def test_regular_agentbox_request_still_warns_after_two_seconds(
    monkeypatch,
    caplog,
) -> None:
    async def downstream(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    times = iter((0.0, 0.1, 2.6))
    monkeypatch.setattr(app_module.time, "perf_counter", lambda: next(times))
    with caplog.at_level(logging.WARNING):
        await app_module.RequestContextMiddleware(downstream)(
            {
                "type": "http",
                "method": "PUT",
                "path": "/sandboxes/workspace/id/files:content",
                "query_string": b"",
                "headers": [],
                "state": {},
                "route": SimpleNamespace(
                    path_format=(
                        "/sandboxes/{workload_kind}/{logical_id}/files:content"
                    )
                ),
            },
            receive,
            send,
        )

    assert sum(record.msg == "http.request.slow" for record in caplog.records) == 1
