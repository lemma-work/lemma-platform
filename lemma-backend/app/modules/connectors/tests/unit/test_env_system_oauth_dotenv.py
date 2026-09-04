"""A system OAuth client configured in `.env` is found.

The variable *names* come from catalog data (`client_id_env`), so they cannot be
pydantic-settings fields and are looked up by name at runtime. That meant
`os.getenv` and nothing else -- correct in production, where the variables are
exported, and wrong on a developer's machine, where every other setting in the
application comes from `.env`. The connector reported itself unconfigured and
offered nothing to explain why.
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


def _github() -> ConnectorEntity:
    return ConnectorEntity(
        id="github",
        provider_capabilities=[
            HttpKindSpec(
                auth_scheme=AuthScheme.OAUTH2,
                oauth2_defaults=OAuth2Defaults(
                    authorization_url="https://github.com/login/oauth/authorize",
                    token_url="https://github.com/login/oauth/access_token",
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


def test_an_exported_variable_is_used(monkeypatch):
    monkeypatch.setattr(env_system_oauth_config, "_dotenv_values", dict)
    monkeypatch.setenv("CONNECTOR_GITHUB_CLIENT_ID", "from-environ")
    monkeypatch.setenv("CONNECTOR_GITHUB_CLIENT_SECRET", "s")

    assert EnvSystemOAuthConfigAdapter().has_default_oauth_config(_github())


def test_a_dotenv_entry_is_found_when_nothing_is_exported(monkeypatch):
    monkeypatch.delenv("CONNECTOR_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("CONNECTOR_GITHUB_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(
        env_system_oauth_config,
        "_dotenv_values",
        lambda: {
            "CONNECTOR_GITHUB_CLIENT_ID": "from-dotenv",
            "CONNECTOR_GITHUB_CLIENT_SECRET": "s",
        },
    )

    adapter = EnvSystemOAuthConfigAdapter()
    assert adapter.has_default_oauth_config(_github())
    config = adapter.get_default_oauth_config(_github())
    assert config is not None
    assert config.client_id == "from-dotenv"


def test_the_exported_variable_wins_over_the_file(monkeypatch):
    """A deployment that exports the variable means it; the file is the
    fallback, not an override."""
    monkeypatch.setenv("CONNECTOR_GITHUB_CLIENT_ID", "from-environ")
    monkeypatch.setenv("CONNECTOR_GITHUB_CLIENT_SECRET", "s")
    monkeypatch.setattr(
        env_system_oauth_config,
        "_dotenv_values",
        lambda: {"CONNECTOR_GITHUB_CLIENT_ID": "from-dotenv"},
    )

    config = EnvSystemOAuthConfigAdapter().get_default_oauth_config(_github())
    assert config is not None
    assert config.client_id == "from-environ"


def test_no_client_anywhere_is_reported_as_unconfigured(monkeypatch):
    monkeypatch.delenv("CONNECTOR_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("CONNECTOR_GITHUB_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(env_system_oauth_config, "_dotenv_values", dict)

    assert not EnvSystemOAuthConfigAdapter().has_default_oauth_config(_github())


def test_a_missing_dotenv_file_is_not_an_error(monkeypatch):
    monkeypatch.setattr(env_system_oauth_config, "dotenv_path", lambda: "/nope/.env")
    env_system_oauth_config._dotenv_values.cache_clear()
    assert env_system_oauth_config._dotenv_values() == {}
