"""Whether an account marked ``REAUTH_REQUIRED`` may still be tried.

The mark used to be terminal: once set, every pod resolution refused the
account before any call, and only a reconnect cleared it. That made it the most
expensive answer the system could give, and it was given for cheap reasons -- a
403 on one operation the person lacked a scope for, or a token endpoint that
timed out once. A credential that was still perfectly good was refused forever.

Those marks cannot be told apart from deliberate ones by looking at the row, so
whether to try again is decided from what *trying* would risk:

- **Something a refresh can renew** (an OAuth refresh token, a Composio
  connection) is verified by refreshing it before use. The issuer is the
  authority on whether the grant is alive: a revoked grant, or tokens issued to
  an OAuth app an admin has since swapped out, fails the refresh and the account
  stays marked. A refresh that succeeds clears the mark.
- **A plain secret** (an API key, a password) has no issuer to ask, so the call
  itself is the check, and a successful call clears the mark. Only for secrets
  that are not OAuth-issued: an access token that cannot be refreshed may belong
  to an OAuth app the install no longer uses, and the call would accept it
  anyway.
- **An install whose config names where it connects** (an MCP or OpenAPI server
  URL, a database host) is never retried. Moving that target is the deliberate
  reason ``install_update`` marks accounts, and retrying would send the
  credential to a system it was never issued for.

Recovery is lazy, on the next use, so accounts already marked recover without a
backfill.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from app.core.domain.uow import IUnitOfWork
from app.modules.connectors.domain.account import AccountEntity, AccountStatus
from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.errors import AccountResolutionError
from app.modules.connectors.domain.ports import AccountRepositoryPort
from app.modules.connectors.services.credential_freshness import (
    serialize_credentials,
)

#: Config keys that name where an install connects, per kind. A change to any of
#: them means a stored credential now points at a different system -- which is
#: why ``install_update`` marks accounts on it, and why an install that has one
#: set never has a marked account retried.
TARGET_FIELDS: dict[ConnectorKind, tuple[str, ...]] = {
    ConnectorKind.MCP: ("server_url",),
    ConnectorKind.HTTP: ("server_url",),
    ConnectorKind.SQL: ("host", "port", "database"),
}


def credential_is_renewable(credentials: Mapping[str, object]) -> bool:
    """Whether a refresh could renew this credential and so vouch for it."""
    return bool(credentials.get("refresh_token") or credentials.get("connection_id"))


def _install_names_a_target(auth_config: AuthConfigEntity) -> bool:
    config = auth_config.config or {}
    return any(config.get(field) for field in TARGET_FIELDS.get(auth_config.kind, ()))


def reauth_may_recover(account: AccountEntity, auth_config: AuthConfigEntity) -> bool:
    """Whether a ``REAUTH_REQUIRED`` account may be tried again rather than refused."""
    if account.status is not AccountStatus.REAUTH_REQUIRED:
        return False
    if _install_names_a_target(auth_config):
        return False
    try:
        credentials = serialize_credentials(account.credentials)
    except AccountResolutionError:
        return False
    if not credentials:
        return False
    if credential_is_renewable(credentials):
        return True
    return not credentials.get("access_token")


def reauth_blocks_use(account: AccountEntity, auth_config: AuthConfigEntity) -> bool:
    """Whether resolution must refuse this account and ask for a reconnect."""
    return account.status is AccountStatus.REAUTH_REQUIRED and not reauth_may_recover(
        account, auth_config
    )


async def clear_reauth_mark(
    accounts: AccountRepositoryPort, uow: IUnitOfWork, account_id: UUID
) -> None:
    """Unmark an account whose credential the provider has just accepted.

    Only ``REAUTH_REQUIRED`` moves: a ``DISCONNECTED`` account was disconnected
    on purpose, and a call that happened to work does not undo that. The id
    comes from resolution, which already decided the account may be used.
    """
    account = await accounts.get(account_id)
    if account is None or account.status is not AccountStatus.REAUTH_REQUIRED:
        return
    account.status = AccountStatus.CONNECTED
    await accounts.update(account)
    await uow.commit()
