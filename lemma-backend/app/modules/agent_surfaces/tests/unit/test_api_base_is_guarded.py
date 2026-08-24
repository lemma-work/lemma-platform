"""A surface's API base URL is checked before any token is sent to it.

`api_base_url` is a real feature — a self-hosted Telegram Bot API server, a
sovereign-cloud Graph or Slack endpoint — so it stays. What it must not be is
unguarded. It arrives from stored account credentials, which makes it
tenant-supplied input, and every platform client dialled it with a bare httpx
client that asked no questions. Pointed at 169.254.169.254 it would fetch the
instance's own credentials and hand them back through the surface.

These prove the refusal at the point of use, for the clients an org admin can
actually aim: the connector kinds already had this check, and these bring the
surfaces up to the same standard.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.platforms.common import (
    UnsafeApiBaseError,
    assert_safe_api_base,
)
from app.modules.agent_surfaces.platforms.telegram.client import TelegramClient

METADATA = "http://169.254.169.254/latest/meta-data/"


class TestTheSharedGuard:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "api_base, reason",
        [
            (METADATA, "link_local_address"),
            ("http://127.0.0.1:8080", "loopback_address"),
            ("http://10.0.0.5/api", "private_address"),
        ],
    )
    async def test_a_private_base_is_refused(self, api_base, reason):
        with pytest.raises(UnsafeApiBaseError) as raised:
            await assert_safe_api_base(api_base, platform="Telegram")
        assert raised.value.reason == reason

    @pytest.mark.asyncio
    async def test_the_metadata_service_is_refused_even_when_self_hosting(
        self, monkeypatch
    ):
        """The self-hosting hatch is for your own network, not the instance keys."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)
        with pytest.raises(UnsafeApiBaseError) as raised:
            await assert_safe_api_base(METADATA, platform="Telegram")
        assert raised.value.reason == "link_local_address"

    @pytest.mark.asyncio
    async def test_a_self_hosted_base_still_works(self, monkeypatch):
        """The feature survives: a real self-hosted endpoint is allowed."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)
        base = "http://10.0.0.5/bot"
        assert await assert_safe_api_base(base, platform="Telegram") == base


class TestTelegramRefusesBeforeSending:
    @pytest.mark.asyncio
    async def test_the_bot_api_call_is_refused(self):
        """The token must not reach a host the guard would not allow."""
        client = TelegramClient(bot_token="secret-token", api_base=METADATA)
        with pytest.raises(UnsafeApiBaseError) as raised:
            await client.call("getMe", {})
        assert raised.value.reason == "link_local_address"
