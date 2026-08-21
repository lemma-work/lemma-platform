from __future__ import annotations

import pytest

from app.modules.connectors.domain.connector import (
    ConnectorEntity,
    AuthScheme,
    LemmaProviderCapability,
    OAuth2Defaults,
)
from app.modules.connectors.infrastructure.adapters.env_system_oauth_config import (
    EnvSystemOAuthConfigAdapter,
)


def _native_app(connector_id: str) -> ConnectorEntity:
    """A native app row as stored in the DB: LEMMA capability, no OAuth defaults."""
    return ConnectorEntity(
        id=connector_id,
        provider_capabilities=[LemmaProviderCapability(auth_scheme=AuthScheme.OAUTH2)],
    )


@pytest.fixture
def adapter() -> EnvSystemOAuthConfigAdapter:
    return EnvSystemOAuthConfigAdapter()


@pytest.mark.parametrize(
    ("connector_id", "expected_api_scope"),
    [
        ("gmail", "https://www.googleapis.com/auth/gmail.modify"),
        ("google_calendar", "https://www.googleapis.com/auth/calendar"),
        ("google_drive", "https://www.googleapis.com/auth/drive"),
        ("google_docs", "https://www.googleapis.com/auth/documents"),
        ("google_sheets", "https://www.googleapis.com/auth/spreadsheets"),
    ],
)
def test_resolve_oauth2_defaults_uses_registry_for_native_google_apps(
    adapter: EnvSystemOAuthConfigAdapter,
    connector_id: str,
    expected_api_scope: str,
):
    defaults = adapter.resolve_oauth2_defaults(_native_app(connector_id))

    assert defaults is not None
    assert defaults.authorization_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert defaults.token_url == "https://oauth2.googleapis.com/token"
    assert expected_api_scope in defaults.default_scopes
    # Identity scopes always present so the account email can be resolved.
    assert "openid" in defaults.default_scopes
    assert defaults.extra_params == {"access_type": "offline", "prompt": "consent"}


def test_resolve_oauth2_defaults_prefers_stored_capability_defaults(
    adapter: EnvSystemOAuthConfigAdapter,
):
    # An app that stores its own OAuth defaults (e.g. Slack/Jira from JSON config)
    # must win over the native registry — and apps not in the registry resolve
    # purely from what they store.
    stored = OAuth2Defaults(
        authorization_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        default_scopes=["chat:write"],
    )
    app = ConnectorEntity(
        id="slack",
        provider_capabilities=[LemmaProviderCapability(oauth2_defaults=stored)],
    )

    defaults = adapter.resolve_oauth2_defaults(app)

    assert defaults is not None
    assert defaults.authorization_url == "https://slack.com/oauth/v2/authorize"


def test_resolve_oauth2_defaults_returns_none_for_unknown_app(
    adapter: EnvSystemOAuthConfigAdapter,
):
    assert adapter.resolve_oauth2_defaults(_native_app("not-a-real-app")) is None


def test_system_default_availability_tracks_env_presence(
    adapter: EnvSystemOAuthConfigAdapter,
    monkeypatch,
):
    app = _native_app("gmail")

    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    assert adapter.has_default_oauth_config(app) is False
    assert adapter.get_default_oauth_config(app) is None

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "sys-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sys-client-secret")
    assert adapter.has_default_oauth_config(app) is True

    config = adapter.get_default_oauth_config(app)
    assert config is not None
    assert config.client_id == "sys-client-id"
    assert config.client_secret == "sys-client-secret"
    # System client is paired with the registry's Google endpoints/scopes.
    assert config.authorization_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert "https://www.googleapis.com/auth/gmail.modify" in config.default_scopes


def test_unknown_app_has_no_system_default(
    adapter: EnvSystemOAuthConfigAdapter,
    monkeypatch,
):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "sys-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sys-client-secret")

    app = _native_app("not-a-real-app")
    assert adapter.has_default_oauth_config(app) is False
    assert adapter.get_default_oauth_config(app) is None
