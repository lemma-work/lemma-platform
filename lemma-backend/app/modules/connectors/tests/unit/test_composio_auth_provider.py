from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import BaseModel

os.environ.setdefault("COMPOSIO_CACHE_DIR", "/tmp/composio")

from app.modules.connectors.domain.account import OAuthCredentials
from app.modules.connectors.domain.connector import (
    ConnectorEntity,
    ComposioProviderCapability,
)
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.services.auth.composio_auth_provider import (
    ComposioAuthProvider,
)


class _FakeConnectionState(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_in: str | float | None = None
    token_type: str | None = None


def _connector(app_id: str = "google_calendar") -> ConnectorEntity:
    return ConnectorEntity(
        id=app_id,
        provider_capabilities=[
            ComposioProviderCapability(toolkit_slug="googlecalendar")
        ],
    )


def _provider(connection_state: BaseModel) -> ComposioAuthProvider:
    connected_accounts = SimpleNamespace(
        get=lambda _: SimpleNamespace(
            id="ca_test_connection",
            state=SimpleNamespace(val=connection_state),
        )
    )
    composio = SimpleNamespace(connected_accounts=connected_accounts)
    return ComposioAuthProvider(
        connector_repository=AsyncMock(),
        composio_client_factory=lambda: composio,
    )


@pytest.mark.asyncio
async def test_exchange_code_uses_composio_expires_in_for_google_accounts():
    provider = _provider(
        _FakeConnectionState(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_in="3600",
            token_type="Bearer",
        )
    )
    provider._get_google_token_expiration = AsyncMock(return_value=None)

    credentials = await provider.exchange_code_for_credentials(
        connector=_connector(),
        redirect_uri="https://app.example.com/callback?connectedAccountId=ca_test_connection",
        user_id=uuid4(),
    )

    assert credentials.access_token == "access-token"
    assert credentials.refresh_token == "refresh-token"
    assert credentials.expires_at is not None
    assert credentials.expires_at > datetime.now(timezone.utc) + timedelta(minutes=50)
    provider._get_google_token_expiration.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_credentials_falls_back_to_default_expiry_when_missing():
    provider = _provider(
        _FakeConnectionState(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_in=None,
            token_type="Bearer",
        )
    )
    provider._get_google_token_expiration = AsyncMock(return_value=None)

    credentials = await provider.refresh_credentials(
        connector=_connector(),
        credentials=OAuthCredentials(
            access_token="stale-token",
            connection_id="ca_test_connection",
        ),
        user_id=uuid4(),
    )

    assert credentials.expires_at is not None
    assert credentials.expires_at > datetime.now(timezone.utc) + timedelta(minutes=4)
    provider._get_google_token_expiration.assert_awaited_once()


def test_account_repository_serializes_expires_at_as_json_string():
    expires_at = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)

    serialized = AccountRepository._serialize_credentials(
        OAuthCredentials(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=expires_at,
            connection_id="ca_test_connection",
        )
    )

    assert serialized is not None
    assert serialized["expires_at"] == "2026-03-16T12:00:00Z"
    assert serialized["connection_id"] == "ca_test_connection"
