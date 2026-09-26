"""The sign-in page draws a provider button only for what is registered here.

It asks SuperTokens' own `GET /loginmethods`, so the only thing worth proving
is that the answer lists exactly the providers `build_thirdparty_providers`
registered -- a Google button on a server with no Google client ends at
Google's "unknown client" page.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.modules.identity.infrastructure.supertokens_auth.initialization import (
    build_thirdparty_providers,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_login_methods_list_the_registered_providers_and_no_others(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get("/st/auth/loginmethods")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "OK", body
    listed = {provider["id"] for provider in body["thirdParty"]["providers"]}
    registered = {
        provider.config.third_party_id for provider in build_thirdparty_providers()
    }
    assert listed == registered
