from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.domain.connector import (
    AuthScheme,
    ConnectorKind,
    OAuth2Config,
)
from app.modules.connectors.services.auth.lemma_auth_provider import LemmaAuthProvider
from app.modules.connectors.services.credential_freshness import (
    credential_refresh_due,
)

pytestmark = pytest.mark.asyncio


class FakeOAuth2Session:
    """Stands in for authlib's AsyncOAuth2Client: an async context manager whose
    fetch_token/refresh_token are awaitable."""

    last_init: dict[str, object] = {}
    last_fetch_token: dict[str, object] = {}

    def __init__(self, **kwargs):
        FakeOAuth2Session.last_init = dict(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def create_authorization_url(self, url, state=None, **kwargs):
        return f"{url}?state={state}", state

    async def fetch_token(self, **kwargs):
        FakeOAuth2Session.last_fetch_token = kwargs
        return {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "token_type": "Bearer",
        }

    async def refresh_token(self, **kwargs):
        FakeOAuth2Session.last_fetch_token = kwargs
        return {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "token_type": "Bearer",
        }


class FakeSlackOAuth2Session(FakeOAuth2Session):
    async def fetch_token(self, **kwargs):
        FakeOAuth2Session.last_fetch_token = kwargs
        return {
            "access_token": "xoxp-user-token",
            "refresh_token": "refresh-token",
            "token_type": "bot",
            "authed_user": {
                "access_token": "xoxp-user-token",
                "token_type": "user",
            },
        }


def _install(connector_id: str = "slack") -> ResolvedAuthInstall:
    return ResolvedAuthInstall(
        connector_id=connector_id,
        kind=ConnectorKind.PACKAGE,
        auth_scheme=AuthScheme.OAUTH2,
        auth_config_id=uuid4(),
        organization_id=uuid4(),
        config_source=AuthConfigSource.SYSTEM_DEFAULT,
        config={},
        oauth2=OAuth2Config(
            client_id="client-id",
            client_secret="client-secret",
            default_scopes=["chat:write"],
            authorization_url="https://slack.com/oauth/v2/authorize",
            token_url="https://slack.com/api/oauth.v2.access",
        ),
    )


async def test_exchange_code_uses_clean_redirect_uri_for_token_exchange():
    provider = LemmaAuthProvider(oauth_session_factory=FakeOAuth2Session)
    callback_url = (
        "https://example.ngrok.app/connectors/connect-requests/oauth/callback"
        "?code=abc&state=xyz"
    )

    credentials = await provider.exchange_code_for_credentials(
        install=_install(),
        redirect_uri=callback_url,
        user_id=uuid4(),
    )

    expected_redirect_uri = (
        "https://example.ngrok.app/connectors/connect-requests/oauth/callback"
    )
    assert FakeOAuth2Session.last_init["redirect_uri"] == expected_redirect_uri
    assert FakeOAuth2Session.last_fetch_token["authorization_response"] == callback_url
    assert "redirect_uri" not in FakeOAuth2Session.last_fetch_token
    assert credentials.access_token == "access-token"


async def test_exchange_code_normalizes_slack_token_type_to_bearer():
    provider = LemmaAuthProvider(oauth_session_factory=FakeSlackOAuth2Session)
    callback_url = (
        "https://example.ngrok.app/connectors/connect-requests/oauth/callback"
        "?code=abc&state=xyz"
    )

    credentials = await provider.exchange_code_for_credentials(
        install=_install(),
        redirect_uri=callback_url,
        user_id=uuid4(),
    )

    assert credentials.access_token == "xoxp-user-token"
    assert credentials.token_type == "Bearer"


class FakeExpiringOAuth2Session(FakeOAuth2Session):
    """A provider that reports an absolute expiry, the way a GitHub App does."""

    async def fetch_token(self, **kwargs):
        FakeOAuth2Session.last_fetch_token = kwargs
        return {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "token_type": "Bearer",
            "expires_at": 1_800_000_000,
        }


class FakeShortLivedOAuth2Session(FakeOAuth2Session):
    """A provider that reports a relative lifetime, the way Slack's user tokens do."""

    async def fetch_token(self, **kwargs):
        FakeOAuth2Session.last_fetch_token = kwargs
        return {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "token_type": "Bearer",
            "expires_in": 28_800,
        }


async def test_an_absolute_expiry_is_the_instant_the_provider_meant():
    """`fromtimestamp` without a tz returns the wall clock of whatever host ran
    it, and `credential_freshness` reads a naive value as UTC -- so west of UTC
    the token looked fresher than it was. The assertion that matters is
    `tzinfo`: an aware value cannot be misread, on any host."""
    provider = LemmaAuthProvider(oauth_session_factory=FakeExpiringOAuth2Session)

    credentials = await provider.exchange_code_for_credentials(
        install=_install(),
        redirect_uri="https://example.ngrok.app/callback?code=abc",
        user_id=uuid4(),
    )

    assert credentials.expires_at is not None
    assert credentials.expires_at.tzinfo is not None
    assert credentials.expires_at == datetime(2027, 1, 15, 8, 0, 0, tzinfo=timezone.utc)


async def test_a_relative_lifetime_is_measured_from_utc_now():
    provider = LemmaAuthProvider(oauth_session_factory=FakeShortLivedOAuth2Session)
    before = datetime.now(timezone.utc)

    credentials = await provider.exchange_code_for_credentials(
        install=_install(),
        redirect_uri="https://example.ngrok.app/callback?code=abc",
        user_id=uuid4(),
    )

    assert credentials.expires_at is not None
    assert credentials.expires_at.tzinfo is not None
    # An 8-hour token must read as ~8 hours away, not 8 hours plus the host's
    # UTC offset.
    remaining = credentials.expires_at - before
    assert timedelta(hours=7, minutes=59) <= remaining <= timedelta(hours=8, minutes=1)
    assert not credential_refresh_due({"expires_at": credentials.expires_at})
