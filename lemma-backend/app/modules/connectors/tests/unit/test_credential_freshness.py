"""When a credential is refreshed, and — more importantly — when it is not.

Every execution used to refresh unconditionally before doing any work. That is
three extra queries and a full round trip to the provider, spent on a token that
is usually valid for another hour, on the hot path of every connector call.

Refreshing is now driven by expiry, with the rejection case handled reactively.
These pin both halves, because getting the "when not" wrong reintroduces the
latency silently and getting the "when" wrong breaks live connectors.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.connectors.services.credential_freshness import credential_refresh_due

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


class TestWhenRefreshIsDue:
    def test_a_credential_with_no_expiry_is_never_refreshed_ahead_of_time(self):
        # API keys, bot tokens, SQL passwords, and OAuth providers that report no
        # expiry. For these an expiry check can only ever say "no", so refreshing
        # them pre-emptively was pure cost.
        assert credential_refresh_due({"access_token": "t"}, now=NOW) is False
        assert credential_refresh_due({}, now=NOW) is False
        assert credential_refresh_due(None, now=NOW) is False

    def test_a_token_valid_for_an_hour_is_left_alone(self):
        creds = {"expires_at": NOW + timedelta(hours=1)}
        assert credential_refresh_due(creds, now=NOW) is False

    def test_a_token_inside_the_skew_window_is_refreshed(self):
        # Refreshed slightly early, so a call does not race the expiry.
        creds = {"expires_at": NOW + timedelta(seconds=30)}
        assert credential_refresh_due(creds, now=NOW) is True

    def test_an_expired_token_is_refreshed(self):
        creds = {"expires_at": NOW - timedelta(minutes=5)}
        assert credential_refresh_due(creds, now=NOW) is True

    def test_an_iso_string_expiry_is_understood(self):
        creds = {"expires_at": (NOW - timedelta(minutes=1)).isoformat()}
        assert credential_refresh_due(creds, now=NOW) is True

    def test_a_naive_expiry_is_read_as_utc(self):
        creds = {"expires_at": (NOW + timedelta(hours=1)).replace(tzinfo=None)}
        assert credential_refresh_due(creds, now=NOW) is False

    def test_a_malformed_expiry_does_not_raise(self):
        # A bad stored value must not take down every execution for that account.
        assert credential_refresh_due({"expires_at": "not-a-date"}, now=NOW) is False
        assert credential_refresh_due({"expires_at": 12345}, now=NOW) is False


class TestTheHotPath:
    """A valid credential must reach the provider without any extra work."""

    @staticmethod
    def _service(connector_service):
        from app.modules.connectors.services.connector_operation_service import (
            ConnectorOperationService,
        )

        return ConnectorOperationService(
            connector_repository=AsyncMock(),
            operation_repository=AsyncMock(),
            operation_gateway=AsyncMock(),
            account_resolution_service=AsyncMock(),
            connector_service=connector_service,
        )

    @staticmethod
    def _account(expires_at):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=uuid4(),
            user_id=uuid4(),
            organization_id=uuid4(),
            credentials={
                "access_token": "tok",
                "refresh_token": "ref",
                "expires_at": expires_at,
            },
            connector=SimpleNamespace(auth_method="OAUTH2"),
        )

    @pytest.mark.asyncio
    async def test_a_healthy_token_triggers_no_refresh_at_all(self):
        connector_service = AsyncMock()
        service = self._service(connector_service)
        account = self._account(datetime.now(timezone.utc) + timedelta(hours=1))

        credentials = await service._resolve_execution_credentials(
            account, account.user_id
        )

        # Not merely "no network call" -- get_account_credentials is never
        # entered, so its three reads and its decrypt do not happen either.
        connector_service.get_account_credentials.assert_not_awaited()
        assert credentials["access_token"] == "tok"

    @pytest.mark.asyncio
    async def test_an_expiring_token_is_refreshed_before_use(self):
        connector_service = AsyncMock()
        connector_service.get_account_credentials.return_value = {
            "access_token": "fresh"
        }
        service = self._service(connector_service)
        account = self._account(datetime.now(timezone.utc) + timedelta(seconds=5))

        credentials = await service._resolve_execution_credentials(
            account, account.user_id
        )

        connector_service.get_account_credentials.assert_awaited_once()
        assert credentials["access_token"] == "fresh"

    @pytest.mark.asyncio
    async def test_a_non_oauth_account_is_never_refreshed(self):
        from types import SimpleNamespace

        connector_service = AsyncMock()
        service = self._service(connector_service)
        account = SimpleNamespace(
            id=uuid4(),
            user_id=uuid4(),
            organization_id=uuid4(),
            credentials={"username": "u", "password": "p"},
            connector=SimpleNamespace(auth_method="API_KEY"),
        )

        await service._resolve_execution_credentials(account, account.user_id)
        connector_service.get_account_credentials.assert_not_awaited()
