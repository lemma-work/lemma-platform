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
    last_authorization: dict[str, object] = {}

    def __init__(self, **kwargs):
        FakeOAuth2Session.last_init = dict(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def create_authorization_url(self, url, state=None, **kwargs):
        FakeOAuth2Session.last_authorization = dict(kwargs)
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


def _mcp_install() -> ResolvedAuthInstall:
    """An install of the shape MCP discovery produces: a dynamically registered
    public client (no secret) guarding one resource."""
    return ResolvedAuthInstall(
        connector_id="mcp",
        kind=ConnectorKind.MCP,
        auth_scheme=AuthScheme.OAUTH2,
        auth_config_id=uuid4(),
        organization_id=uuid4(),
        config_source=AuthConfigSource.ORG_CUSTOM,
        config={},
        oauth2=OAuth2Config(
            client_id="px_dcr_client",
            client_secret=None,
            default_scopes=[],
            authorization_url="https://phoenix.example/oauth2/authorize",
            token_url="https://phoenix.example/oauth2/token",
            resource="https://phoenix.example/mcp",
        ),
    )


async def test_resource_indicator_is_sent_on_both_authorization_and_token():
    """RFC 8707 wants the resource named in both requests.

    Sending it only at the token endpoint is not a partial implementation, it is
    a broken one: the authorization server binds the grant to the resources
    named at authorization time, so asking at exchange time for one the grant
    never carried is rejected as `invalid_target`. A real server did exactly
    that, which is why this asserts on both halves rather than on the token
    request alone.
    """
    provider = LemmaAuthProvider(oauth_session_factory=FakeOAuth2Session)
    install = _mcp_install()

    await provider.get_authorization_url(
        install=install,
        user_id=uuid4(),
        state="state-value",
        redirect_uri="https://lemma.example/callback",
        code_verifier="verifier",
    )
    assert (
        FakeOAuth2Session.last_authorization["resource"]
        == "https://phoenix.example/mcp"
    )

    await provider.exchange_code_for_credentials(
        install=install,
        redirect_uri="https://lemma.example/callback?code=abc&state=state-value",
        user_id=uuid4(),
        code_verifier="verifier",
    )
    assert (
        FakeOAuth2Session.last_fetch_token["resource"] == "https://phoenix.example/mcp"
    )


async def test_authorization_url_carries_pkce_for_a_secretless_client():
    """A dynamically registered client has no secret, so PKCE is the only thing
    binding the code to the browser that asked for it."""
    provider = LemmaAuthProvider(oauth_session_factory=FakeOAuth2Session)

    await provider.get_authorization_url(
        install=_mcp_install(),
        user_id=uuid4(),
        state="state-value",
        redirect_uri="https://lemma.example/callback",
        code_verifier="verifier",
    )

    assert FakeOAuth2Session.last_init["code_challenge_method"] == "S256"
    assert FakeOAuth2Session.last_authorization["code_verifier"] == "verifier"


async def test_no_resource_is_sent_when_the_install_names_none():
    """Most OAuth connectors are not resource-scoped, and sending an empty or
    absent indicator to one that never asked for it is a way to get refused."""
    provider = LemmaAuthProvider(oauth_session_factory=FakeOAuth2Session)

    await provider.get_authorization_url(
        install=_install(),
        user_id=uuid4(),
        state="state-value",
        redirect_uri="https://lemma.example/callback",
    )

    assert "resource" not in FakeOAuth2Session.last_authorization
