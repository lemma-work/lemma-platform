from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.modules.connectors.infrastructure.webhook_sources.composio import (
    _reshape as _normalize_composio_payload,
)

from app.modules.schedule.api.controllers.webhook_controller import (
    _read_bounded_body,
    router,
)


def test_direct_schedule_webhook_route_is_removed():
    """The unauthenticated, unverified POST /webhooks/schedules/{id} endpoint was
    deleted (security_authz-02). Only the composio-verified /webhooks/{source}
    routes remain."""
    paths = {route.path for route in router.routes}
    assert "/webhooks/schedules/{schedule_id}" not in paths
    assert "/webhooks/{source}" in paths


def test_normalize_composio_payload_maps_sdk_result_to_internal_shape():
    verification_result = {
        "version": "V3",
        "payload": {
            "id": "ti_123",
            "uuid": "ti_123",
            "user_id": "user_123",
            "toolkit_slug": "GOOGLECALENDAR",
            "trigger_slug": "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER",
            "metadata": {
                "id": "ti_123",
                "uuid": "ti_123",
                "toolkit_slug": "GOOGLECALENDAR",
                "trigger_slug": "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER",
                "trigger_data": None,
                "trigger_config": {},
                "connected_account": {
                    "id": "ca_123",
                    "uuid": "ca_123",
                    "auth_config_id": "ac_123",
                    "auth_config_uuid": "ac_123",
                    "user_id": "user_123",
                    "status": "ACTIVE",
                },
            },
            "payload": {
                "event_id": "evt_123",
            },
        },
        "raw_payload": {
            "id": "msg_123",
            "timestamp": "2026-03-22T06:50:57.477Z",
            "type": "composio.trigger.message",
            "metadata": {
                "log_id": "log_123",
            },
            "data": {
                "event_id": "evt_123",
            },
        },
    }

    normalized = _normalize_composio_payload(verification_result)

    assert normalized["type"] == "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER"
    assert normalized["webhook_type"] == "composio.trigger.message"
    assert normalized["data"] == {"event_id": "evt_123"}
    assert normalized["metadata"]["trigger_id"] == "ti_123"
    assert normalized["metadata"]["connected_account_id"] == "ca_123"
    assert normalized["metadata"]["version"] == "V3"


def test_normalize_composio_payload_falls_back_to_raw_data_payload():
    verification_result = {
        "version": "V3",
        "payload": {
            "id": "ti_123",
            "uuid": "ti_123",
            "user_id": "user_123",
            "toolkit_slug": "GOOGLECALENDAR",
            "trigger_slug": "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER",
            "metadata": {
                "connected_account": {
                    "id": "ca_123",
                    "auth_config_id": "ac_123",
                },
            },
            "payload": None,
        },
        "raw_payload": {
            "type": "composio.trigger.message",
            "data": {
                "event_id": "evt_123",
            },
        },
    }

    normalized = _normalize_composio_payload(verification_result)

    assert normalized["data"] == {"event_id": "evt_123"}
    assert normalized["metadata"]["trigger_id"] == "ti_123"


@pytest.mark.asyncio
async def test_composio_webhook_verification_does_not_run_on_the_event_loop(
    monkeypatch,
) -> None:
    """Signature verification runs the Composio SDK, which is synchronous.

    This path is unauthenticated and externally driven: the sender chooses the
    rate, and every delivery used to construct a fresh `Composio(...)` client
    (measured at 76ms cold / 4ms warm at the sibling call site in
    `composio_auth_provider`) and then run a blocking SDK call -- all of it on
    the event loop, where it stalls every other request in the process.

    Asserting on the thread rather than on elapsed time: a timing test would be
    flaky under load, while the thread identity is exactly the property. If
    someone drops the `run_blocking` the SDK lands back on the loop thread and
    this fails.
    """
    import threading

    from app.modules.connectors.config import connector_settings
    from app.modules.connectors.infrastructure import composio_triggers

    loop_thread = threading.current_thread()
    ran_on: list[threading.Thread] = []

    class _Triggers:
        def verify_webhook(self, **kwargs):
            ran_on.append(threading.current_thread())
            return {"payload": {"id": "ti_1"}}

    monkeypatch.setattr(
        composio_triggers,
        "_webhook_verification_client",
        lambda: SimpleNamespace(triggers=_Triggers()),
    )
    monkeypatch.setattr(
        connector_settings, "composio_webhook_secret", "shh", raising=False
    )

    result = await composio_triggers.verify_webhook("{}", {"webhook-id": "wh_1"})

    assert result == {"payload": {"id": "ti_1"}}
    (thread,) = ran_on
    assert thread is not loop_thread, (
        "the Composio SDK ran on the event loop thread; an unauthenticated "
        "sender can now stall every other request by sending webhooks"
    )


def test_the_published_verification_is_async_so_it_can_offload() -> None:
    """A sync `verify_webhook` could not offload, and callers cannot make it."""
    import inspect

    from app.modules.connectors.contracts.triggers import verify_webhook

    assert inspect.iscoroutinefunction(verify_webhook)


def _streaming_request(
    chunks: list[bytes], *, headers: list[tuple[bytes, bytes]] | None = None
):
    calls = 0

    async def receive():
        nonlocal calls
        index = calls
        calls += 1
        if index >= len(chunks):
            return {"type": "http.disconnect"}
        return {
            "type": "http.request",
            "body": chunks[index],
            "more_body": index < len(chunks) - 1,
        }

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/composio",
            "headers": headers or [],
        },
        receive,
    )
    return request, lambda: calls


@pytest.mark.asyncio
async def test_webhook_body_limit_rejects_declared_oversize_before_reading(monkeypatch):
    from app.modules.schedule.api.controllers import webhook_controller

    monkeypatch.setattr(webhook_controller, "MAX_WEBHOOK_BODY_BYTES", 8)
    request, receive_calls = _streaming_request(
        [b"x" * 9],
        headers=[(b"content-length", b"9")],
    )

    with pytest.raises(HTTPException) as rejected:
        await _read_bounded_body(request, source="composio")

    assert rejected.value.status_code == 413
    assert receive_calls() == 0


@pytest.mark.asyncio
async def test_webhook_body_limit_also_bounds_underreported_bodies(monkeypatch):
    from app.modules.schedule.api.controllers import webhook_controller

    monkeypatch.setattr(webhook_controller, "MAX_WEBHOOK_BODY_BYTES", 8)
    request, receive_calls = _streaming_request(
        [b"x" * 8, b"y", b"unread"],
        headers=[(b"content-length", b"1")],
    )

    with pytest.raises(HTTPException) as rejected:
        await _read_bounded_body(request, source="composio")

    assert rejected.value.status_code == 413
    assert receive_calls() == 2


@pytest.mark.asyncio
async def test_webhook_body_limit_accepts_exact_limit(monkeypatch):
    from app.modules.schedule.api.controllers import webhook_controller

    monkeypatch.setattr(webhook_controller, "MAX_WEBHOOK_BODY_BYTES", 8)
    payload = b"x" * 8
    request, _ = _streaming_request(
        [payload],
        headers=[(b"content-length", str(len(payload)).encode())],
    )

    assert await _read_bounded_body(request, source="composio") == payload


@pytest.mark.asyncio
async def test_webhook_body_limit_handles_extremely_long_content_length_safely():
    request, receive_calls = _streaming_request(
        [b"{}"],
        headers=[(b"content-length", b"9" * 5000)],
    )

    with pytest.raises(HTTPException) as rejected:
        await _read_bounded_body(request, source="composio")

    assert rejected.value.status_code == 413
    assert receive_calls() == 0


@pytest.mark.asyncio
async def test_github_webhook_uses_its_provider_payload_ceiling(monkeypatch):
    from app.modules.schedule.api.controllers import webhook_controller

    monkeypatch.setattr(webhook_controller, "MAX_GITHUB_WEBHOOK_BODY_BYTES", 8)
    request, _ = _streaming_request(
        [b"x" * 8],
        headers=[(b"content-length", b"8")],
    )

    assert await _read_bounded_body(request, source="github") == b"x" * 8


@pytest.mark.asyncio
async def test_non_github_sources_keep_the_smaller_body_ceiling(monkeypatch):
    from app.modules.schedule.api.controllers import webhook_controller

    monkeypatch.setattr(webhook_controller, "MAX_WEBHOOK_BODY_BYTES", 8)
    monkeypatch.setattr(webhook_controller, "MAX_GITHUB_WEBHOOK_BODY_BYTES", 16)
    request, receive_calls = _streaming_request(
        [b"x" * 9],
        headers=[(b"content-length", b"9")],
    )

    with pytest.raises(HTTPException) as rejected:
        await _read_bounded_body(request, source="composio")

    assert rejected.value.status_code == 413
    assert receive_calls() == 0


def test_unverified_schedule_handshake_route_is_not_exposed():
    """WhatsApp handshakes belong to the authenticated surface ingress.

    The schedule source registry accepts Composio, GitHub, and MCP; its old
    WhatsApp GET route echoed arbitrary challenges despite having no matching
    WhatsApp POST source. The real token-checked handshake lives under
    /surfaces/webhooks/{platform}.
    """
    paths = {route.path for route in router.routes}
    assert "/webhooks/{source}/verify" not in paths
