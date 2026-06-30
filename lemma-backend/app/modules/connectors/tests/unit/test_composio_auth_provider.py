from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import BaseModel

os.environ.setdefault("COMPOSIO_CACHE_DIR", "/tmp/composio")

from app.modules.connectors.domain.account import (
    ComposioCredentials,
    OAuthCredentials,
)
from app.modules.connectors.domain.connector import (
    AuthScheme,
    ConnectorEntity,
    ComposioProviderCapability,
)
from app.modules.connectors.domain.errors import ConnectorValidationError
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


def _provider(connection_state: BaseModel, status: str = "ACTIVE") -> ComposioAuthProvider:
    connected_accounts = SimpleNamespace(
        get=lambda _: SimpleNamespace(
            id="ca_test_connection",
            status=status,
            state=SimpleNamespace(val=connection_state),
        )
    )
    composio = SimpleNamespace(connected_accounts=connected_accounts)
    return ComposioAuthProvider(
        connector_repository=AsyncMock(),
        composio_client_factory=lambda: composio,
    )


class _TokenlessConnectionState(BaseModel):
    """A Composio connection state that surfaces no raw access_token (e.g. Canva)."""

    access_token: str | None = None
    refresh_token: str | None = None
    expires_in: str | float | None = None
    token_type: str | None = None


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


@pytest.mark.asyncio
async def test_exchange_code_succeeds_when_access_token_missing():
    # Canva-style: the connected account is created/active but exposes no raw
    # access_token. The connection_id is the authoritative credential, so this
    # must succeed rather than raise on a missing token.
    provider = _provider(
        _TokenlessConnectionState(token_type="Bearer"),
        status="ACTIVE",
    )

    credentials = await provider.exchange_code_for_credentials(
        connector=_connector("canva"),
        redirect_uri="https://app.example.com/callback?connectedAccountId=ca_test_connection",
        user_id=uuid4(),
    )

    assert credentials.access_token is None
    assert credentials.connection_id == "ca_test_connection"


@pytest.mark.asyncio
async def test_exchange_code_raises_on_terminal_connection_state():
    provider = _provider(
        _TokenlessConnectionState(),
        status="FAILED",
    )

    with pytest.raises(ConnectorValidationError):
        await provider.exchange_code_for_credentials(
            connector=_connector("canva"),
            redirect_uri="https://app.example.com/callback?connectedAccountId=ca_test_connection",
            user_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_connect_with_credentials_initiates_api_key_connection():
    connector = ConnectorEntity(
        id="airtable",
        provider_capabilities=[
            ComposioProviderCapability(
                toolkit_slug="airtable",
                auth_scheme=AuthScheme.API_KEY,
            )
        ],
        composio_auth_config_id="ac_existing",
    )

    initiate = MagicMock(return_value=SimpleNamespace(id="ca_new_connection"))
    auth_configs = SimpleNamespace(create=MagicMock())
    composio = SimpleNamespace(
        connected_accounts=SimpleNamespace(initiate=initiate),
        auth_configs=auth_configs,
    )
    provider = ComposioAuthProvider(
        connector_repository=AsyncMock(),
        composio_client_factory=lambda: composio,
    )

    user_id = uuid4()
    credentials = await provider.connect_with_credentials(
        connector=connector,
        user_id=user_id,
        credentials={"api_key": "secret-key"},
    )

    assert isinstance(credentials, ComposioCredentials)
    assert credentials.connection_id == "ca_new_connection"
    # Reused the existing auth config (no create call) and passed an API_KEY config
    # with no callback_url (non-OAuth flow).
    auth_configs.create.assert_not_called()
    initiate.assert_called_once()
    _, kwargs = initiate.call_args
    assert kwargs["auth_config_id"] == "ac_existing"
    assert kwargs["user_id"] == str(user_id)
    assert "callback_url" not in kwargs
    assert kwargs["config"]["auth_scheme"] == "API_KEY"
    assert kwargs["config"]["val"]["api_key"] == "secret-key"


@pytest.mark.asyncio
async def test_connect_with_credentials_creates_custom_auth_config():
    # No pre-existing auth config id -> must create a use_custom_auth config
    # (API-key toolkits have no Composio-managed credentials).
    connector = ConnectorEntity(
        id="tavily",
        provider_capabilities=[
            ComposioProviderCapability(
                toolkit_slug="tavily",
                auth_scheme=AuthScheme.API_KEY,
            )
        ],
    )
    create = MagicMock(return_value=SimpleNamespace(id="ac_created"))
    initiate = MagicMock(return_value=SimpleNamespace(id="ca_created"))
    composio = SimpleNamespace(
        auth_configs=SimpleNamespace(create=create),
        connected_accounts=SimpleNamespace(initiate=initiate),
    )
    provider = ComposioAuthProvider(
        connector_repository=AsyncMock(),
        composio_client_factory=lambda: composio,
    )

    creds = await provider.connect_with_credentials(
        connector=connector,
        user_id=uuid4(),
        credentials={"generic_api_key": "k"},
    )

    assert creds.connection_id == "ca_created"
    create.assert_called_once()
    _, kwargs = create.call_args
    assert kwargs["options"]["type"] == "use_custom_auth"
    assert kwargs["options"]["auth_scheme"] == "API_KEY"
    assert initiate.call_args.kwargs["auth_config_id"] == "ac_created"


@pytest.mark.asyncio
async def test_connect_with_credentials_rejects_oauth_apps():
    provider = ComposioAuthProvider(
        connector_repository=AsyncMock(),
        composio_client_factory=lambda: SimpleNamespace(),
    )

    with pytest.raises(ConnectorValidationError):
        await provider.connect_with_credentials(
            connector=_connector("canva"),  # defaults to OAUTH2
            user_id=uuid4(),
            credentials={"api_key": "x"},
        )


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
