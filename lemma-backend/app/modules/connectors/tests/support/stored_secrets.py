"""Seeding connector secrets for e2e tests that build rows by hand.

An account's credentials and an install's config are not columns any more:
each is a vault secret the row points at. A test that constructs an ``Account``
or ``AuthConfig`` directly, rather than through the repositories, stores the
secret first and passes its id::

    Account(..., credentials_secret_id=await account_credentials_secret(
        db_session, organization_id=org_id, user_id=user_id,
        credentials={"api_key": "secret"},
    ))

The purpose and scope come from ``connector_secrets``, the same place the
repositories take them from, so a row seeded here reads back through the real
repository exactly as one written by the API would.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.connectors.infrastructure.repositories.connector_secrets import (
    ACCOUNT_CREDENTIALS_PURPOSE,
    ACCOUNTS_TABLE,
    AUTH_CONFIG_PURPOSE,
    AUTH_CONFIGS_TABLE,
    account_scope,
    auth_config_scope,
    credentials_expiry,
    serialize_credentials,
)
from app.modules.vault.contracts import JsonObject, vault_for


def _uuid(value: object) -> UUID:
    # Fixtures hand ids over as strings as often as UUIDs; the scope is part of
    # the ciphertext, so it has to be the one the repository will rebuild.
    return value if isinstance(value, UUID) else UUID(str(value))


async def account_credentials_secret(
    session, *, organization_id: object, user_id: object, credentials: object
) -> UUID:
    """Store ``credentials`` as an account's secret and return its id."""
    value = serialize_credentials(credentials)
    if value is None:
        raise ValueError("an account secret needs credentials to store")
    ref = await vault_for(session).put(
        scope=account_scope(_uuid(organization_id), _uuid(user_id)),
        purpose=ACCOUNT_CREDENTIALS_PURPOSE,
        value=value,
        owner_table=ACCOUNTS_TABLE,
        expires_at=credentials_expiry(value),
    )
    return ref.id


async def auth_config_secret(
    session, *, organization_id: object, config: JsonObject
) -> UUID:
    """Store ``config`` as an install's secret and return its id."""
    ref = await vault_for(session).put(
        scope=auth_config_scope(_uuid(organization_id)),
        purpose=AUTH_CONFIG_PURPOSE,
        value=config,
        owner_table=AUTH_CONFIGS_TABLE,
    )
    return ref.id
