import pytest
from pydantic import ValidationError

from app.modules.connectors.api.schemas import AuthConfigCreateSchema
from app.modules.connectors.domain.auth_config import AuthConfigSource
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


def test_an_unknown_config_source_is_refused_before_it_reaches_the_service():
    """``CUSTOM`` is not a source: refused as the request's mistake (422), not
    carried in as a string until something fails on it (a 500)."""
    with pytest.raises(ValidationError):
        AuthConfigCreateSchema(connector_id="github", config_source="CUSTOM")
    made = AuthConfigCreateSchema(connector_id="github", config_source="ORG_CUSTOM")
    assert made.config_source == AuthConfigSource.ORG_CUSTOM
