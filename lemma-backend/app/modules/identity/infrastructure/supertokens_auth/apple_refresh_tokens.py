"""Where the Apple refresh token kept for revocation lives: the vault.

One secret per user, bound to that user, with no owner row of ours: the
SuperTokens user's metadata names it (``apple.REVOCATION_METADATA_KEY``), and
revoking the sign-in -- which is what deleting the account does -- deletes it.
A new sign-in replaces it in place.

A user who signed in before the vault has the token sealed with the old secret
cipher inside the metadata record itself (``"sealed"``); :func:`open_legacy`
reads it until their next sign-in moves it here.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from cryptography.fernet import InvalidToken

from app.core.infrastructure.db.session import async_session_maker
from app.modules.vault.contracts import (
    SecretNotFound,
    SecretScope,
    legacy_json,
    vault_for,
)

REFRESH_TOKEN_PURPOSE = "identity.apple.refresh_token"
_OWNER = "supertokens.user_metadata"


def _scope(user_id: str) -> SecretScope:
    # The SuperTokens user id is the Lemma user's id.
    return SecretScope(None, user_id=UUID(user_id))


def secret_id_of(record: Mapping[str, object]) -> UUID | None:
    """The vault secret a metadata record names, if it names one."""
    raw = record.get("secret_id")
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


async def keep(user_id: str, secret_id: UUID, refresh_token: str) -> None:
    """``refresh_token`` as secret ``secret_id``: replaced, or put under it."""
    scope = _scope(user_id)
    async with async_session_maker() as session:
        vault = vault_for(session)
        try:
            await vault.replace(
                secret_id,
                expect=scope,
                purpose=REFRESH_TOKEN_PURPOSE,
                value=refresh_token,
            )
        except SecretNotFound:
            await vault.put(
                scope=scope,
                purpose=REFRESH_TOKEN_PURPOSE,
                value=refresh_token,
                owner_table=_OWNER,
                secret_id=secret_id,
            )
        await session.commit()


async def reveal(user_id: str, secret_id: UUID) -> str | None:
    """The kept token; None when the secret is gone."""
    try:
        async with async_session_maker() as session:
            revealed = await vault_for(session).reveal(
                secret_id, expect=_scope(user_id), purpose=REFRESH_TOKEN_PURPOSE
            )
    except SecretNotFound:
        return None
    return revealed.text()


async def forget(user_id: str, secret_id: UUID) -> None:
    async with async_session_maker() as session:
        await vault_for(session).delete(
            secret_id, expect=_scope(user_id), purpose=REFRESH_TOKEN_PURPOSE
        )
        await session.commit()


async def open_legacy(record: Mapping[str, object]) -> str | None:
    """The token sealed inside a record kept before the vault, if it opens."""
    sealed = record.get("sealed")
    if not isinstance(sealed, dict):
        return None
    try:
        opened = await legacy_json(sealed)
    except InvalidToken, ValueError:
        return None
    refresh_token = (opened or {}).get("refresh_token")
    return refresh_token if isinstance(refresh_token, str) else None


__all__ = [
    "REFRESH_TOKEN_PURPOSE",
    "forget",
    "keep",
    "open_legacy",
    "reveal",
    "secret_id_of",
]
