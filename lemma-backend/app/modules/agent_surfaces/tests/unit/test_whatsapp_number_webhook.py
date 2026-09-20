"""The per-number WhatsApp callback URL, where the path is the discriminator.

Meta's GET handshake carries ``hub.mode``, ``hub.challenge`` and
``hub.verify_token`` and nothing that says *which number* is being verified, so
on one shared callback URL there is nothing to select a token by. These check
the thing that replaces it: the number is in the path, every credential is
chosen from the path, and the body is never allowed to pick the key it will be
checked against.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.modules.agent_surfaces.api.controllers.webhook_controller import (
    handle_whatsapp_number_webhook,
    verify_whatsapp_number_webhook,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.whatsapp_numbers import WhatsAppNumberEntity
from app.modules.agent_surfaces.services.webhook_security_service import (
    SurfaceWebhookAuthenticationError,
    SurfaceWebhookSecurityService,
)

# No module-level asyncio mark: `asyncio_mode = auto` already runs the async
# tests, and two of the checks below are ordinary functions.
_POOLED_NUMBER_ID = "pooled-phone-1"
_POOLED_APP_SECRET = "pooled-app-secret"


def _request(body: bytes = b"", *, query: str = "", method: str = "POST") -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    path = f"/surfaces/webhooks/whatsapp/numbers/{_POOLED_NUMBER_ID}"
    signature = hmac.new(_POOLED_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-hub-signature-256", f"sha256={signature}".encode()),
            ],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 443),
        },
        receive,
    )


def _message_for(phone_number_id: str) -> bytes:
    return json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": phone_number_id},
                                "messages": [
                                    {
                                        "id": "wamid.pooled-1",
                                        "from": "14155552671",
                                        "type": "text",
                                        "text": {"body": "hello"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
    ).encode()


class _UowFactory:
    """One short scope per lookup, like every other webhook route here."""

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


def _pooled(**overrides) -> WhatsAppNumberEntity:
    fields: dict[str, object] = {
        "phone_number_id": _POOLED_NUMBER_ID,
        "display_phone_number": "+15551230001",
        "waba_id": "waba-pooled",
        "app_secret": _POOLED_APP_SECRET,
        "verify_token": "pooled-verify-token",
    }
    fields.update(overrides)
    return WhatsAppNumberEntity(**fields)


def _pool_returning(number: WhatsAppNumberEntity | None):
    """Patch the pool lookup, which is the only database read these routes do."""
    return patch(
        "app.modules.agent_surfaces.api.controllers.webhook_controller."
        "WhatsAppNumberRepository",
        return_value=SimpleNamespace(
            get_by_phone_number_id=AsyncMock(return_value=number)
        ),
    )


async def test_a_pooled_number_is_verified_against_its_own_token(monkeypatch):
    """The token that answers is the row's, not the deployment's.

    This is the whole reason the route exists: before it, every number on the
    shared URL had to answer with ``surface_settings.whatsapp_verify_token``,
    because the handshake names no number and one URL cannot tell them apart.
    """
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_verify_token", "settings-token")

    with _pool_returning(_pooled()):
        response = await verify_whatsapp_number_webhook(
            _POOLED_NUMBER_ID,
            _request(
                query="hub.mode=subscribe&hub.challenge=nonce-1"
                "&hub.verify_token=pooled-verify-token",
                method="GET",
            ),
            uow_factory=_UowFactory(),
        )

    assert response.body == b"nonce-1"


async def test_the_deployment_token_does_not_verify_a_pooled_number(monkeypatch):
    """A number with its own token is not also reachable with the shared one.

    Two numbers under one Meta app share an ``app_secret`` by construction, so
    the verify token is the only per-number secret there is. If the settings
    token kept working here, adding a number to the pool would widen who can
    complete its handshake rather than narrow it.
    """
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_verify_token", "settings-token")

    with _pool_returning(_pooled()), pytest.raises(HTTPException) as raised:
        await verify_whatsapp_number_webhook(
            _POOLED_NUMBER_ID,
            _request(
                query="hub.mode=subscribe&hub.challenge=nonce-1"
                "&hub.verify_token=settings-token",
                method="GET",
            ),
            uow_factory=_UowFactory(),
        )

    assert raised.value.status_code == 403


async def test_a_number_with_no_row_falls_back_to_settings(monkeypatch):
    """A one-number deployment declares no rows and must keep working.

    Every credential column is nullable and absent means "fall back to
    settings", so a deployment that never ran the admin script has an empty
    pool -- and this route has to behave exactly like the shared one for it.
    """
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_verify_token", "settings-token")

    with _pool_returning(None):
        response = await verify_whatsapp_number_webhook(
            _POOLED_NUMBER_ID,
            _request(
                query="hub.mode=subscribe&hub.challenge=nonce-2"
                "&hub.verify_token=settings-token",
                method="GET",
            ),
            uow_factory=_UowFactory(),
        )

    assert response.body == b"nonce-2"


async def test_a_row_without_its_own_token_falls_back_to_settings(monkeypatch):
    """A row is not all-or-nothing: a NULL column falls back on its own.

    A number added for its Flow ids alone has no ``verify_token``, and treating
    the row's existence as "this number declares everything" would leave it
    unable to complete a handshake it previously could.
    """
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_verify_token", "settings-token")

    with _pool_returning(_pooled(verify_token=None)):
        response = await verify_whatsapp_number_webhook(
            _POOLED_NUMBER_ID,
            _request(
                query="hub.mode=subscribe&hub.challenge=nonce-3"
                "&hub.verify_token=settings-token",
                method="GET",
            ),
            uow_factory=_UowFactory(),
        )

    assert response.body == b"nonce-3"


async def test_a_delivery_is_verified_with_the_secret_the_path_selected(monkeypatch):
    """The signature is checked against the row the URL names, then published.

    The receiver on the event is the number rather than ``shared``: this URL has
    one receiver per pooled number, and the content-hash fallback in
    ``_surface_source_event_id`` is only unique within a receiver.
    """
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "settings-secret")
    security = SurfaceWebhookSecurityService()
    factory = _UowFactory()

    with (
        _pool_returning(_pooled()),
        patch(
            "app.modules.agent_surfaces.api.controllers.webhook_controller."
            "EventPublisher.publish",
            new=AsyncMock(),
        ) as publish,
    ):
        result = await handle_whatsapp_number_webhook(
            _POOLED_NUMBER_ID,
            _request(_message_for(_POOLED_NUMBER_ID)),
            security,
            uow_factory=factory,
        )

    assert result == {"message": "Webhook received"}
    # The pool read opened and closed its own scope; the publish holds nothing.
    assert (factory.scopes, factory.open_scopes) == (1, 0)
    event = publish.await_args.args[1]
    assert event.source == "whatsapp"
    # A WhatsApp body carries no id `_surface_source_event_id` recognises -- the
    # `wamid` is buried under entry/changes/value/messages -- so it falls to the
    # content hash, exactly as it does on the shared endpoint. What matters here
    # is the half this route decides: the receiver.
    assert event.source_event_id.startswith(f"whatsapp:{_POOLED_NUMBER_ID}:")


async def test_a_body_naming_another_number_is_refused(monkeypatch):
    """A signature proves the app, not the number, so the body must still agree.

    ``app_secret`` is per Meta *app*: two numbers co-tenanted under one app
    answer with the same value, so a delivery signed for one of them verifies
    perfectly on the other's URL. Without this check the payload would be
    attributed to whichever number the path happened to name, and a co-tenant
    could have its traffic filed under its neighbour.
    """
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    security = SurfaceWebhookSecurityService()

    with (
        _pool_returning(_pooled()),
        patch(
            "app.modules.agent_surfaces.api.controllers.webhook_controller."
            "EventPublisher.publish",
            new=AsyncMock(),
        ) as publish,
        pytest.raises(HTTPException) as raised,
    ):
        await handle_whatsapp_number_webhook(
            _POOLED_NUMBER_ID,
            _request(_message_for("a-co-tenanted-number")),
            security,
            uow_factory=_UowFactory(),
        )

    assert raised.value.status_code == 400
    publish.assert_not_awaited()


async def test_a_body_naming_this_number_and_another_is_refused(monkeypatch):
    """ "At least one matched" would wave the rest of the body through.

    This URL is a per-number override, so what Meta sends to it is that
    number's traffic and nothing else. A body carrying this number alongside a
    co-tenant is as much a delivery this route cannot account for as one
    naming only the co-tenant, and membership rather than equality would
    publish it with the extra change unexamined.
    """
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    security = SurfaceWebhookSecurityService()
    body = json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {"value": {"metadata": {"phone_number_id": _POOLED_NUMBER_ID}}},
                        {"value": {"metadata": {"phone_number_id": "a-co-tenant"}}},
                    ]
                }
            ]
        }
    ).encode()

    with (
        _pool_returning(_pooled()),
        patch(
            "app.modules.agent_surfaces.api.controllers.webhook_controller."
            "EventPublisher.publish",
            new=AsyncMock(),
        ) as publish,
        pytest.raises(HTTPException) as raised,
    ):
        await handle_whatsapp_number_webhook(
            _POOLED_NUMBER_ID, _request(body), security, uow_factory=_UowFactory()
        )

    assert raised.value.status_code == 400
    publish.assert_not_awaited()


async def test_the_signature_is_checked_before_the_body_is_read(monkeypatch):
    """The payload never gets to choose the key it is verified against.

    The body names a ``phone_number_id`` and selecting the secret with it would
    be the obvious implementation -- and it would let a forger name whichever
    number's app secret he holds. Here the secret comes from the path, so a body
    signed with some *other* number's secret is refused even though it names
    that other number honestly.
    """
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", None)
    security = SurfaceWebhookSecurityService()
    # Signed with `_POOLED_APP_SECRET` by `_request`, but the row this path
    # names declares a different one.
    with (
        _pool_returning(_pooled(app_secret="a-different-app-secret")),
        pytest.raises(SurfaceWebhookAuthenticationError) as raised,
    ):
        await handle_whatsapp_number_webhook(
            _POOLED_NUMBER_ID,
            _request(_message_for(_POOLED_NUMBER_ID)),
            security,
            uow_factory=_UowFactory(),
        )

    assert raised.value.status_code == 401


async def test_a_body_that_names_no_number_is_still_delivered(monkeypatch):
    """Not every WhatsApp change carries ``metadata``, and those are not forgeries.

    Account and template notifications have no ``phone_number_id`` to agree
    with, so refusing them would break them for a question they cannot answer.
    The signature has already established who sent them.
    """
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    security = SurfaceWebhookSecurityService()
    body = json.dumps(
        {"entry": [{"changes": [{"field": "account_update", "value": {}}]}]}
    ).encode()

    with (
        _pool_returning(_pooled()),
        patch(
            "app.modules.agent_surfaces.api.controllers.webhook_controller."
            "EventPublisher.publish",
            new=AsyncMock(),
        ) as publish,
    ):
        result = await handle_whatsapp_number_webhook(
            _POOLED_NUMBER_ID, _request(body), security, uow_factory=_UowFactory()
        )

    assert result == {"message": "Webhook received"}
    publish.assert_awaited_once()


async def test_a_delivery_falls_back_to_the_settings_app_secret(monkeypatch):
    """With no row, the route verifies exactly as the shared endpoint does."""
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", _POOLED_APP_SECRET)
    security = SurfaceWebhookSecurityService()

    with (
        _pool_returning(None),
        patch(
            "app.modules.agent_surfaces.api.controllers.webhook_controller."
            "EventPublisher.publish",
            new=AsyncMock(),
        ) as publish,
    ):
        result = await handle_whatsapp_number_webhook(
            _POOLED_NUMBER_ID,
            _request(_message_for(_POOLED_NUMBER_ID)),
            security,
            uow_factory=_UowFactory(),
        )

    assert result == {"message": "Webhook received"}
    publish.assert_awaited_once()


def test_a_per_number_callback_keeps_its_platform_label_in_analytics():
    """A deeper webhook path must still resolve to the WhatsApp platform.

    ``origin_for_path`` matched one path segment, so every delivery to a pooled
    number would have claimed no origin at all -- and the WhatsApp figure in
    origin analytics would silently have become "whatever is left on the shared
    URL". The phone number id is matched and discarded: it is not a platform and
    must never reach ``Origin.platform``.
    """
    from app.core.origin import OriginKind, origin_for_path

    resolved = origin_for_path(
        f"/surfaces/webhooks/whatsapp/numbers/{_POOLED_NUMBER_ID}"
    )

    assert resolved is not None
    assert resolved.kind is OriginKind.SURFACE
    assert resolved.platform == "whatsapp"


def test_a_per_number_callback_stays_unauthenticated():
    """The global auth gate must let Meta in, as it does on every webhook route.

    Meta has no Lemma session and never will; a 401 here would present as a
    webhook that Meta reports as failing for no reason anybody can see from the
    logs.
    """
    from app.core.security import EXCLUDED_PATHS

    assert f"/surfaces/webhooks/whatsapp/numbers/{_POOLED_NUMBER_ID}".startswith(
        EXCLUDED_PATHS
    )
