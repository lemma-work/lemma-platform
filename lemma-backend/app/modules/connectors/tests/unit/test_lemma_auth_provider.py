from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.connectors.domain.connector import ConnectorEntity, OAuth2Config
from app.modules.connectors.services.auth.lemma_auth_provider import LemmaAuthProvider

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


def _connector() -> ConnectorEntity:
    return ConnectorEntity(
        id="slack",
        oauth2_config=OAuth2Config(
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
        connector=_connector(),
        redirect_uri=callback_url,
        user_id=uuid4(),
    )

    expected_redirect_uri = (
        "https://example.ngrok.app/connectors/connect-requests/oauth/callback"
    )
    assert FakeOAuth2Session.last_init["redirect_uri"] == expected_redirect_uri
    assert (
        FakeOAuth2Session.last_fetch_token["authorization_response"] == callback_url
    )
    assert "redirect_uri" not in FakeOAuth2Session.last_fetch_token
    assert credentials.access_token == "access-token"


async def test_exchange_code_normalizes_slack_token_type_to_bearer():
    provider = LemmaAuthProvider(oauth_session_factory=FakeSlackOAuth2Session)
    callback_url = (
        "https://example.ngrok.app/connectors/connect-requests/oauth/callback"
        "?code=abc&state=xyz"
    )

    credentials = await provider.exchange_code_for_credentials(
        connector=_connector(),
        redirect_uri=callback_url,
        user_id=uuid4(),
    )

    assert credentials.access_token == "xoxp-user-token"
    assert credentials.token_type == "Bearer"
