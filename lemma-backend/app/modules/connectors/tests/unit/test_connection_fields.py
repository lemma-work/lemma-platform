"""Per-connection fields: what an OAuth connect needs besides the sign-in.

Shopify's OAuth mode is the case that forced these. Signing in says who the
person is and not which store they mean, so Composio needs the store's
``subdomain`` before it can build an authorization URL. The catalog declared
that field and nothing carried it, which left Shopify with no way to connect.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.domain.connector import (
    AuthScheme,
    ComposioKindSpec,
    ConnectorEntity,
    ConnectorKind,
    HttpKindSpec,
)
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.services.account_credentials import (
    validated_connection_fields,
)
from app.modules.connectors.services.auth.composio_auth_provider import (
    ComposioAuthProvider,
)

# What the importer derives from Composio's Shopify OAUTH2 detail.
_SUBDOMAIN_SCHEMA = {
    "type": "object",
    "properties": {"subdomain": {"type": "string", "title": "Store name"}},
    "required": ["subdomain"],
    "additionalProperties": False,
}


def _shopify() -> ConnectorEntity:
    return ConnectorEntity(
        id="shopify",
        kinds=[
            ComposioKindSpec(
                toolkit_slug="shopify",
                system_default_available=False,
                auth_config_schema=_SUBDOMAIN_SCHEMA,
            )
        ],
    )


def test_a_declared_field_is_passed_through():
    fields = validated_connection_fields(
        _shopify(), ConnectorKind.COMPOSIO, {"subdomain": "acme"}
    )

    assert fields == {"subdomain": "acme"}


def test_a_missing_required_field_names_itself():
    with pytest.raises(ConnectorValidationError) as raised:
        validated_connection_fields(_shopify(), ConnectorKind.COMPOSIO, None)

    violations = raised.value.details["violations"]
    assert any("subdomain" in v["message"] for v in violations)


def test_an_undeclared_field_is_refused_rather_than_dropped():
    with pytest.raises(ConnectorValidationError):
        validated_connection_fields(
            _shopify(), ConnectorKind.COMPOSIO, {"subdomain": "acme", "shop": "x"}
        )


def test_a_connector_declaring_nothing_is_unchanged():
    gmail = ConnectorEntity(id="gmail", kinds=[ComposioKindSpec(toolkit_slug="gmail")])

    assert validated_connection_fields(gmail, ConnectorKind.COMPOSIO, None) is None
    assert validated_connection_fields(gmail, ConnectorKind.COMPOSIO, {}) is None


def test_a_native_oauth_install_takes_no_fields():
    # A native kind's `auth_config_schema` is the org's install form, not a
    # per-connection one, so it must never be read as the latter.
    github = ConnectorEntity(
        id="github", kinds=[HttpKindSpec(auth_config_schema=_SUBDOMAIN_SCHEMA)]
    )

    assert validated_connection_fields(github, ConnectorKind.HTTP, None) is None
    with pytest.raises(ConnectorValidationError):
        validated_connection_fields(github, ConnectorKind.HTTP, {"subdomain": "a"})


def _composio_with(initiate: MagicMock) -> ComposioAuthProvider:
    composio = SimpleNamespace(
        auth_configs=SimpleNamespace(
            create=MagicMock(return_value=SimpleNamespace(id="ac_shop"))
        ),
        connected_accounts=SimpleNamespace(initiate=initiate, link=initiate),
    )
    return ComposioAuthProvider(
        connector_repository=AsyncMock(), composio_client_factory=lambda: composio
    )


def _shopify_install() -> ResolvedAuthInstall:
    return ResolvedAuthInstall(
        connector_id="shopify",
        kind=ConnectorKind.COMPOSIO,
        auth_scheme=AuthScheme.OAUTH2,
        auth_config_id=uuid4(),
        organization_id=uuid4(),
        config_source=AuthConfigSource.ORG_CUSTOM,
        config={"client_id": "cid", "client_secret": "secret"},
        composio_toolkit_slug="shopify",
    )


@pytest.mark.asyncio
async def test_composio_receives_the_store_as_oauth2_connection_state():
    initiate = MagicMock(
        return_value=SimpleNamespace(id="ca_shop", redirect_url="https://acme/oauth")
    )

    await _composio_with(initiate).get_authorization_url(
        install=_shopify_install(),
        user_id=uuid4(),
        state="s",
        redirect_uri="https://lemma/callback",
        connection_fields={"subdomain": "acme"},
    )

    config = initiate.call_args.kwargs["config"]
    assert config["auth_scheme"] == "OAUTH2"
    assert config["val"]["subdomain"] == "acme"
    assert config["val"]["status"] == "INITIALIZING"


@pytest.mark.asyncio
async def test_a_sign_in_without_fields_uses_link_not_the_retiring_initiate():
    """Composio is retiring `initiate()` for redirect sign-ins and warns on each
    call; `link()` returns the same connected-account id and redirect."""
    link = MagicMock(
        return_value=SimpleNamespace(id="ca_g", redirect_url="https://g/oauth")
    )
    initiate = MagicMock()
    composio = SimpleNamespace(
        auth_configs=SimpleNamespace(
            create=MagicMock(return_value=SimpleNamespace(id="ac_g"))
        ),
        connected_accounts=SimpleNamespace(initiate=initiate, link=link),
    )
    provider = ComposioAuthProvider(
        connector_repository=AsyncMock(), composio_client_factory=lambda: composio
    )

    url, provider_state = await provider.get_authorization_url(
        install=_shopify_install(),
        user_id=uuid4(),
        state="s",
        redirect_uri="https://lemma/callback",
    )

    assert (url, provider_state) == ("https://g/oauth", "ca_g")
    assert link.call_args.kwargs["callback_url"] == "https://lemma/callback?state=s"
    initiate.assert_not_called()
