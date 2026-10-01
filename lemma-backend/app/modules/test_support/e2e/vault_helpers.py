"""Seeding an ``Account`` fixture's credentials the way the product stores them.

For fixtures that build the ORM row by hand. The purpose, scope and expiry come
from the connectors module's own helper, so a row seeded here reads back
through the real repository exactly as one the API wrote.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.infrastructure.models import Account
from app.modules.connectors.tests.support.stored_secrets import (
    account_credentials_secret,
)
from app.modules.vault.contracts import JsonObject


async def seed_account_credentials(
    session: AsyncSession, account: Account, credentials: JsonObject
) -> Account:
    """Store ``credentials`` for ``account`` (not yet flushed) and link them."""
    account.credentials_secret_id = await account_credentials_secret(
        session,
        organization_id=account.organization_id,
        user_id=account.user_id,
        credentials=credentials,
    )
    return account
