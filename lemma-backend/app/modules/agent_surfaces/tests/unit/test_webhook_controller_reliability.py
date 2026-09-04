from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
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
from app.modules.agent_surfaces.platforms.resend.inbound import (
    resend_source_event_id,
    normalize_resend_inbound,
)
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent


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


def _reserved_whatsapp_message() -> bytes:
    return json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "global-phone"},
                                "messages": [
                                    {
                                        "id": "wamid.verify-1",
                                        "from": "14155552671",
                                        "type": "text",
                                        "text": {"body": "LEMMA VERIFY 23456789AB"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
    ).encode()


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
    assert (
        _surface_source_event_id("telegram", payload, b"body", receiver="a-surface")
        == f"telegram:a-surface:{expected}"
    )


def test_source_event_id_hashes_content_when_provider_has_no_identifier():
    raw = b'{"data":"no identifier"}'
    expected = hashlib.sha256(raw).hexdigest()

    assert (
        _surface_source_event_id(
            "custom", {"data": "not-a-dict"}, raw, receiver="a-surface"
        )
        == f"custom:a-surface:content-sha256:{expected}"
    )


def test_two_receivers_sharing_a_provider_id_are_two_events():
    """Telegram's ``update_id`` counts per bot, so every bot has an update 1."""
    an_update = {"update_id": 1}

    assert _surface_source_event_id(
        "telegram", an_update, b"body", receiver="surface-a"
    ) != _surface_source_event_id("telegram", an_update, b"body", receiver="surface-b")


def test_webhook_headers_are_redacted_before_event_serialization():
    headers = _redacted_headers(
        {"authorization": "Bearer canary-secret", "x-provider": "safe"}
    )

    assert "canary-secret" not in str(headers)
    assert headers["x-provider"] == "safe"


class _CountingUowFactory:
    """A factory that records how many scopes the route opened.

    The webhook routes take no request-scoped session: they open one short
    scope per lookup and hold nothing across a signature check or a Redis
    publish. `scopes` is what a test asserts on to keep that true — a route
    that goes back to a route-lifetime session opens one scope and never
    closes it before the publish, and `open_scopes` catches that.
    """

    def __init__(self) -> None:
        self.scopes = 0
        self.open_scopes = 0

    @asynccontextmanager
    async def __call__(self):
        self.scopes += 1
        self.open_scopes += 1
        try:
            yield SimpleNamespace(session=SimpleNamespace(info={}))
        finally:
            self.open_scopes -= 1


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
            "telegram", _request(body), security, uow_factory=_CountingUowFactory()
        )

    assert result == {"message": "Webhook received"}
    security.assert_platform_request_allowed.assert_called_once_with("telegram")
    security.verify_platform_request.assert_awaited_once()
    event = publish.await_args.args[1]
    assert event.source_event_id == "telegram:shared:99"
    assert event.source == "telegram"


@pytest.mark.asyncio
async def test_signed_reserved_whatsapp_message_publishes_only_identity_event(
    monkeypatch,
):
    from app.core.config import settings
    from app.modules.agent_surfaces.config import surface_settings
    from app.modules.identity.domain.events import (
        WhatsAppMobileVerificationReceivedEvent,
    )

    monkeypatch.setattr(settings, "auth_whatsapp_mobile_verification_enabled", True)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "global-phone")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "app-secret")
    monkeypatch.setattr(surface_settings, "whatsapp_verify_token", "verify-token")
    body = _reserved_whatsapp_message()
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
            "whatsapp",
            _request(body),
            security,
            uow_factory=_CountingUowFactory(),
        )

    assert result == {"message": "Verification message received"}
    security.verify_platform_request.assert_awaited_once()
    publish.assert_awaited_once()
    event = publish.await_args.args[1]
    assert isinstance(event, WhatsAppMobileVerificationReceivedEvent)
    assert event.sender_wa_id == "14155552671"
    assert event.whatsapp_message_id == "wamid.verify-1"


@pytest.mark.asyncio
async def test_reserved_whatsapp_text_routes_normally_when_verification_is_disabled(
    monkeypatch,
):
    from app.core.config import settings
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(settings, "auth_whatsapp_mobile_verification_enabled", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "global-phone")
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
            "whatsapp",
            _request(_reserved_whatsapp_message()),
            security,
            uow_factory=_CountingUowFactory(),
        )

    assert result == {"message": "Webhook received"}
    publish.assert_awaited_once()
    assert isinstance(publish.await_args.args[1], SurfaceWebhookReceivedEvent)


@pytest.mark.asyncio
async def test_resend_webhook_resolves_surface_before_publishing():
    surface = SimpleNamespace(id=uuid4())
    repository = SimpleNamespace(get_active_by_address=AsyncMock(return_value=surface))
    service = SimpleNamespace(surface_repository=repository)
    security = SimpleNamespace(verify_resend_request=AsyncMock())
    body = json.dumps(
        {
            "data": {
                "to": "pod@ops.asur.work",
                "from": "sender@example.com",
                "message_id": "email-1",
            }
        }
    ).encode()

    factory = _CountingUowFactory()
    with (
        patch(
            "app.modules.agent_surfaces.api.controllers.webhook_controller."
            "EventPublisher.publish",
            new=AsyncMock(),
        ) as publish,
        patch(
            "app.modules.agent_surfaces.api.controllers.webhook_ingest."
            "get_surface_service",
            return_value=service,
        ),
    ):
        result = await handle_platform_webhook(
            "resend", _request(body), security, uow_factory=factory
        )

    assert result == {"message": "Webhook received"}
    # The address lookup opened and closed its own scope; the publish that
    # follows holds nothing.
    assert (factory.scopes, factory.open_scopes) == (1, 0)
    security.verify_resend_request.assert_awaited_once()
    repository.get_active_by_address.assert_awaited_once_with(
        platform="RESEND", address="pod@ops.asur.work"
    )
    event = publish.await_args.args[1]
    assert event.surface_id == surface.id
    assert event.source_event_id == f"resend:{surface.id}:email-1"


@pytest.mark.asyncio
async def test_surface_webhook_verifies_binding_and_publishes_surface_id():
    surface = SimpleNamespace(id=uuid4(), surface_type=SurfacePlatform.WHATSAPP)
    service = SimpleNamespace(get_surface=AsyncMock(return_value=surface))
    security = SimpleNamespace(verify_surface_request=AsyncMock())
    body = json.dumps({"id": "provider-event-1"}).encode()

    factory = _CountingUowFactory()
    with (
        patch(
            "app.modules.agent_surfaces.api.controllers.webhook_controller."
            "EventPublisher.publish",
            new=AsyncMock(),
        ) as publish,
        patch(
            "app.modules.agent_surfaces.api.controllers.webhook_controller."
            "get_surface_service",
            return_value=service,
        ),
    ):
        result = await handle_surface_webhook(
            surface.id, _request(body), security, uow_factory=factory
        )

    assert result == {"message": "Webhook received"}
    assert (factory.scopes, factory.open_scopes) == (1, 0)
    service.get_surface.assert_awaited_once_with(surface.id)
    security.verify_surface_request.assert_awaited_once()
    event = publish.await_args.args[1]
    assert event.surface_id == surface.id
    assert event.source_event_id == f"whatsapp:{surface.id}:provider-event-1"


def test_the_resend_webhook_and_the_resend_poller_mint_one_id_for_one_email():
    """Both Resend paths must land on the same durable id for the same email.

    A deployment can receive an email twice: Resend's inbound webhook fires, and
    a worker running the poller lists the same message -- the ordinary state
    when one Resend project serves several environments. The durable inbox only
    collapses those into one delivery when the two ids match; when they did not,
    what stopped the second agent run was ``claim_message``, a Redis key with a
    15-minute TTL, so the guarantee PS-SURF-011 makes "across a restart" held
    for fifteen minutes and only by accident.
    """
    surface_id = str(uuid4())
    envelope = {
        "type": "email.received",
        "data": {
            "email_id": "b1c2d3e4",
            "message_id": "<sender-chosen@example.com>",
            "to": ["pod@inbound.example.com"],
            "from": "someone@example.com",
        },
    }
    listed_row = {**envelope["data"], "id": envelope["data"]["email_id"]}

    from_webhook = resend_source_event_id(
        normalize_resend_inbound(envelope), receiver=surface_id
    )
    from_poller = resend_source_event_id(
        normalize_resend_inbound(
            {"data": {**listed_row, "email_id": listed_row["id"]}}
        ),
        receiver=surface_id,
    )

    assert from_webhook == from_poller == f"resend:{surface_id}:b1c2d3e4"


def test_one_email_to_two_surfaces_is_two_deliveries():
    """The receiver stays part of the id, as it is for every other platform."""
    normalized = normalize_resend_inbound({"data": {"email_id": "b1c2d3e4"}})

    assert resend_source_event_id(
        normalized, receiver="surface-a"
    ) != resend_source_event_id(normalized, receiver="surface-b")
