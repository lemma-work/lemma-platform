"""The Telegram webhook routes refuse with the exact code, over HTTP.

The product scenario for signature rejection accepts any 4xx or 5xx, so a
verifier that crashed would satisfy it. These pin the code a client actually
gets: 401 for an update that does not carry our secret, 404 for a surface that
does not exist -- each beside the same update accepted with the right secret,
so a route that refused everything would fail here too.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.tests.e2e.helpers import _telegram_payload
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_telegram_secret_headers,
)

pytestmark = pytest.mark.e2e

_SECRET = "native-secret"


@pytest.fixture(autouse=True)
def _verification_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "telegram_webhook_secret", "native-secret")


def _update(message_id: int) -> bytes:
    return json.dumps(
        _telegram_payload(text="hello", message_id=message_id, sender_id=424242)
    ).encode()


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Content-Type": "application/json"}, id="no-secret"),
        pytest.param(build_telegram_secret_headers("a-guess"), id="wrong-secret"),
    ],
)
async def test_a_telegram_update_without_our_secret_is_refused_as_unauthorized(
    authenticated_client: AsyncClient, headers: dict[str, str]
) -> None:
    refused = await authenticated_client.post(
        "/surfaces/webhooks/telegram", content=_update(1), headers=headers
    )
    accepted = await authenticated_client.post(
        "/surfaces/webhooks/telegram",
        content=_update(2),
        headers=build_telegram_secret_headers(_SECRET),
    )

    assert refused.status_code == status.HTTP_401_UNAUTHORIZED, refused.text
    assert accepted.status_code == status.HTTP_200_OK, accepted.text


async def test_an_update_for_a_surface_that_does_not_exist_is_not_found(
    authenticated_client: AsyncClient,
) -> None:
    response = await authenticated_client.post(
        f"/surfaces/{uuid4()}/webhook",
        content=_update(3),
        headers=build_telegram_secret_headers(_SECRET),
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND, response.text
