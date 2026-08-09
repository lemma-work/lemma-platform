from app.modules.connectors.domain.connector import AuthScheme
from app.modules.connectors.services.auth_config_schemas import (
    default_auth_config_schema,
)


def test_custom_slack_oauth_requires_its_signing_secret():
    slack = default_auth_config_schema(AuthScheme.OAUTH2, "slack")

    assert slack["required"] == ["client_id", "client_secret", "signing_secret"]
    assert slack["properties"]["signing_secret"]["format"] == "password"


def test_slack_schema_does_not_mutate_the_shared_oauth_schema():
    default_auth_config_schema(AuthScheme.OAUTH2, "slack")
    github = default_auth_config_schema(AuthScheme.OAUTH2, "github")

    assert github["required"] == ["client_id", "client_secret"]
    assert "signing_secret" not in github["properties"]
