"""A catalog OAuth URL may name an environment variable it needs filling in.

GitHub is why. A GitHub App's connect URL is
`https://github.com/apps/{slug}/installations/new`, and the slug identifies one
particular App -- there is a separate one per environment -- so it is
deployment configuration and cannot be baked into the catalog the way an
endpoint like `accounts.google.com/o/oauth2/v2/auth` can.
"""

from __future__ import annotations

import pytest

from app.modules.connectors.domain.connector import (
    AuthScheme,
    ConnectorEntity,
    HttpKindSpec,
    OAuth2Defaults,
    SystemOAuthCredentialRef,
)
from app.modules.connectors.infrastructure.adapters import env_system_oauth_config
from app.modules.connectors.infrastructure.adapters.env_system_oauth_config import (
    EnvSystemOAuthConfigAdapter,
)

pytestmark = pytest.mark.unit

INSTALL_URL = "https://github.com/apps/{CONNECTOR_GITHUB_APP_SLUG}/installations/new"


def _github(authorization_url: str = INSTALL_URL) -> ConnectorEntity:
    return ConnectorEntity(
        id="github",
        provider_capabilities=[
            HttpKindSpec(
                auth_scheme=AuthScheme.OAUTH2,
                oauth2_defaults=OAuth2Defaults(
                    authorization_url=authorization_url,
                    token_url="https://github.com/login/oauth/access_token",
                    userinfo_url="https://api.github.com/user",
                    default_scopes=[],
                ),
                system_oauth=SystemOAuthCredentialRef(
                    client_id_env="CONNECTOR_GITHUB_CLIENT_ID",
                    client_secret_env="CONNECTOR_GITHUB_CLIENT_SECRET",
                ),
            )
        ],
    )


@pytest.fixture(autouse=True)
def _clear_dotenv_cache():
    env_system_oauth_config._dotenv_values.cache_clear()
    yield
    env_system_oauth_config._dotenv_values.cache_clear()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(env_system_oauth_config, "_dotenv_values", dict)
    monkeypatch.setenv("CONNECTOR_GITHUB_CLIENT_ID", "Iv1.abc")
    monkeypatch.setenv("CONNECTOR_GITHUB_CLIENT_SECRET", "shh")
    return monkeypatch


def test_the_slug_is_substituted_from_env(env):
    env.setenv("CONNECTOR_GITHUB_APP_SLUG", "lemma-dev")
    defaults = EnvSystemOAuthConfigAdapter().resolve_oauth2_defaults(_github())
    assert defaults is not None
    assert defaults.authorization_url == (
        "https://github.com/apps/lemma-dev/installations/new"
    )
    # Nothing else is disturbed.
    assert defaults.token_url == "https://github.com/login/oauth/access_token"


def test_an_unfilled_placeholder_makes_the_connector_unconfigured(env):
    env.delenv("CONNECTOR_GITHUB_APP_SLUG", raising=False)
    adapter = EnvSystemOAuthConfigAdapter()
    assert adapter.resolve_oauth2_defaults(_github()) is None
    # And the client id/secret being present is not enough on its own: sending
    # someone to `github.com/apps/{CONNECTOR_GITHUB_APP_SLUG}/installations/new`
    # is a 404 with no explanation.
    assert adapter.has_default_oauth_config(_github()) is False
    assert adapter.get_default_oauth_config(_github()) is None


def test_a_url_without_a_placeholder_is_returned_as_it_stands(env):
    plain = "https://github.com/login/oauth/authorize"
    env.delenv("CONNECTOR_GITHUB_APP_SLUG", raising=False)
    adapter = EnvSystemOAuthConfigAdapter()
    defaults = adapter.resolve_oauth2_defaults(_github(plain))
    assert defaults is not None and defaults.authorization_url == plain
    assert adapter.has_default_oauth_config(_github(plain)) is True


def test_scopes_and_extra_params_are_not_substitutable(env):
    """Only endpoint fields are filled.

    Substituting into scopes or extra params would let catalog data read
    arbitrary environment variables into an outbound request.
    """
    env.setenv("CONNECTOR_GITHUB_APP_SLUG", "lemma-dev")
    env.setenv("DATABASE_URL", "postgresql://user:pw@host/db")
    connector = _github()
    capability = connector.provider_capabilities[0]
    assert capability.oauth2_defaults is not None
    capability.oauth2_defaults.extra_params = {"leak": "{DATABASE_URL}"}
    capability.oauth2_defaults.default_scopes = ["{DATABASE_URL}"]

    defaults = EnvSystemOAuthConfigAdapter().resolve_oauth2_defaults(connector)
    assert defaults is not None
    assert defaults.extra_params == {"leak": "{DATABASE_URL}"}
    assert defaults.default_scopes == ["{DATABASE_URL}"]
