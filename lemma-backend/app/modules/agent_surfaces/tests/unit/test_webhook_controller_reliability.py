from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.modules.agent_surfaces.api.controllers.webhook_controller import (
    _redacted_headers,
    _surface_source_event_id,
    handle_platform_webhook,
    handle_surface_webhook,
)
from app.modules.agent_surfaces.domain.entities import SurfacePlatform


def _request(body: bytes, *, content_type: str = "application/json") -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/surfaces/webhooks/test",
            "raw_path": b"/surfaces/webhooks/test",
            "query_string": b"",
            "headers": [(b"content-type", content_type.encode())],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 443),
        },
        receive,
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"event_id": "event-1"}, "event-1"),
        ({"update_id": 42}, "42"),
        ({"id": "root-id"}, "root-id"),
        ({"message_id": "message-1"}, "message-1"),
        ({"data": {"message_id": "nested-1"}}, "nested-1"),
    ],
)
def test_source_event_id_prefers_stable_provider_identifiers(payload, expected):
    assert _surface_source_event_id("telegram", payload, b"body") == (
        f"telegram:{expected}"
    )


def test_source_event_id_hashes_content_when_provider_has_no_identifier():
    raw = b'{"data":"no identifier"}'
    expected = hashlib.sha256(raw).hexdigest()

    assert _surface_source_event_id("custom", {"data": "not-a-dict"}, raw) == (
        f"custom:content-sha256:{expected}"
    )


def test_webhook_headers_are_redacted_before_event_serialization():
    headers = _redacted_headers(
        {"authorization": "Bearer canary-secret", "x-provider": "safe"}
    )

    assert "canary-secret" not in str(headers)
    assert headers["x-provider"] == "safe"


@pytest.mark.asyncio
async def test_platform_webhook_verifies_and_publishes_versioned_event():
    body = json.dumps({"update_id": 99, "message": {"text": "hello"}}).encode()
    security = SimpleNamespace(
        assert_platform_request_allowed=Mock(),
        verify_platform_request=AsyncMock(),
    )

    with patch(
        "app.modules.agent_surfaces.api.controllers.webhook_controller."
        "EventPublisher.publish",
        new=AsyncMock(),
    ) as publish:
        result = await handle_platform_webhook(
            "telegram", _request(body), security, SimpleNamespace()
        )

    assert result == {"message": "Webhook received"}
    security.assert_platform_request_allowed.assert_called_once_with("telegram")
    security.verify_platform_request.assert_awaited_once()
    event = publish.await_args.args[1]
    assert event.source_event_id == "telegram:99"
    assert event.source == "telegram"


@pytest.mark.asyncio
async def test_resend_webhook_resolves_surface_before_publishing():
    surface = SimpleNamespace(id=uuid4())
    repository = SimpleNamespace(
        get_active_by_address=AsyncMock(return_value=surface)
    )
    service = SimpleNamespace(surface_repository=repository)
    security = SimpleNamespace(verify_resend_request=AsyncMock())
    body = json.dumps(
        {
            "data": {
                "to": "pod@ops.lemma.work",
                "from": "sender@example.com",
                "message_id": "email-1",
            }
        }
    ).encode()

    with patch(
        "app.modules.agent_surfaces.api.controllers.webhook_controller."
        "EventPublisher.publish",
        new=AsyncMock(),
    ) as publish:
        result = await handle_platform_webhook(
            "resend", _request(body), security, service
        )

    assert result == {"message": "Webhook received"}
    security.verify_resend_request.assert_awaited_once()
    repository.get_active_by_address.assert_awaited_once_with(
        platform="RESEND", address="pod@ops.lemma.work"
    )
    event = publish.await_args.args[1]
    assert event.surface_id == surface.id
    assert event.source_event_id == "resend:email-1"


@pytest.mark.asyncio
async def test_surface_webhook_verifies_binding_and_publishes_surface_id():
    surface = SimpleNamespace(id=uuid4(), surface_type=SurfacePlatform.WHATSAPP)
    service = SimpleNamespace(get_surface=AsyncMock(return_value=surface))
    security = SimpleNamespace(verify_surface_request=AsyncMock())
    body = json.dumps({"id": "provider-event-1"}).encode()

    with patch(
        "app.modules.agent_surfaces.api.controllers.webhook_controller."
        "EventPublisher.publish",
        new=AsyncMock(),
    ) as publish:
        result = await handle_surface_webhook(
            surface.id, _request(body), security, service
        )

    assert result == {"message": "Webhook received"}
    service.get_surface.assert_awaited_once_with(surface.id)
    security.verify_surface_request.assert_awaited_once()
    event = publish.await_args.args[1]
    assert event.surface_id == surface.id
    assert event.source_event_id == "whatsapp:provider-event-1"
