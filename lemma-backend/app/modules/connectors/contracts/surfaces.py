"""What a chat surface may ask about the account it runs on.

Eight operations, not the 1,400-line `ConnectorService` plus two repositories
plus two mapped classes that `app/composition/surface_connectors.py` published.
The service was the worst of it -- handed whole, it let a surface reach
`connector_service.account_repository` and `connector_service.auth_config_repository`
and call whatever it liked -- but the ORM classes were not the earned exception
they looked like. `Account` was one `session.get` and one six-column select;
`AuthConfig` was a single `session.get` followed by four field reads. Neither
was a join, so neither is here: both became operations, and the surfaces module
no longer holds a `select()` against another module's table.

**The storage detail that had escaped.** `for_account` in
`agent_surfaces/services/credential_resolver.py` was reading the raw
`accounts.credentials` column *itself*, checking it for a `_encrypted` marker,
and filling gaps in the decrypted credentials from the plaintext half. Which
fields are stored in the clear, and how the encrypted ones are marked, is
connectors' business twice over -- it is the storage format, and getting the
test wrong hands out either nothing or a ciphertext. `account_with_secrets`
answers that here, once.

A submodule for the same reason as `retirement` beside it: these reach the
service, repository and model layers, and `contracts/__init__` is imported by
anything that wants any contract at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.core.crypto import get_secret_cipher
from app.modules.connectors.api.dependencies import get_connector_service
from app.modules.connectors.domain.connector import AuthProvider, AuthScheme
from app.modules.connectors.domain.errors import ConnectorNotFoundError
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.infrastructure.repositories.auth_config_repository import (
    AuthConfigRepository,
)


@dataclass(frozen=True, slots=True)
class SurfaceAccount:
    """A connected account, with no credentials on it at all.

    Not "the account minus a field": a surface listing renders these straight
    into an API response, and a shape that never carries a secret cannot leak
    one by omission.
    """

    id: UUID
    user_id: UUID
    organization_id: UUID | None
    auth_config_id: UUID | None
    connector_id: str
    display_name: str | None
    email: str | None
    status: str | None


@dataclass(frozen=True, slots=True)
class SurfaceAuthConfig:
    """The install an account was connected through."""

    id: UUID
    kind: str
    connector_id: str
    #: "SYSTEM_DEFAULT" (Lemma's own OAuth app) or "ORG_CUSTOM" (the org's own).
    config_source: str | None


@dataclass(frozen=True, slots=True)
class SurfaceConnectCapability:
    """What connecting an account to this connector asks the person for."""

    auth_scheme: AuthScheme
    auth_config_schema: dict[str, object] | None
    credential_schema: dict[str, object] | None
    system_oauth_available: bool
    supports_org_custom_oauth: bool


@dataclass(frozen=True, slots=True)
class SurfaceConnector:
    """A connector as the surface catalog shows it.

    ``connect`` is ``None`` when the connector exposes no LEMMA capability --
    catalogued but not connectable this way. The catalog renders that as a
    visible "unavailable" row rather than failing the whole endpoint, so the
    distinction has to survive as data instead of as a raised error.
    """

    connector_id: str
    title: str | None
    description: str | None
    icon: str | None
    is_active: bool
    connect: SurfaceConnectCapability | None


def _accounts(uow) -> AccountRepository:
    return AccountRepository(uow, encryption=get_secret_cipher())


def _auth_configs(uow) -> AuthConfigRepository:
    return AuthConfigRepository(uow, encryption=get_secret_cipher())


def _as_surface_account(account) -> SurfaceAccount:
    return SurfaceAccount(
        id=account.id,
        user_id=account.user_id,
        organization_id=account.organization_id,
        auth_config_id=account.auth_config_id,
        connector_id=account.connector_id or "",
        display_name=account.display_name,
        email=account.email,
        status=_value_of(account.status),
    )


def _value_of(enum_or_string) -> str | None:
    raw = getattr(enum_or_string, "value", enum_or_string)
    return str(raw) if raw is not None else None


def _as_mapping(credentials) -> dict[str, object]:
    """Credentials as a plain mapping, whatever shape they arrived in."""
    if credentials is None:
        return {}
    dump = getattr(credentials, "model_dump", None)
    if callable(dump):
        return dict(dump(exclude_none=True))
    return dict(credentials) if isinstance(credentials, dict) else {}


async def account(uow, account_id: UUID) -> SurfaceAccount | None:
    """One connected account, or ``None`` when it is gone."""
    found = await _accounts(uow).get(account_id)
    return _as_surface_account(found) if found is not None else None


async def account_summaries(
    uow, account_ids: Sequence[UUID]
) -> dict[UUID, SurfaceAccount]:
    """A page of surfaces' accounts, in one query.

    Columns rather than entities on purpose: the read path needs no credentials,
    and loading whole accounts would decrypt one secret blob per surface for
    nothing.
    """
    ids = {account_id for account_id in account_ids if account_id is not None}
    if not ids:
        return {}
    rows = await uow.session.execute(
        select(
            Account.id,
            Account.user_id,
            Account.organization_id,
            Account.auth_config_id,
            Account.connector_id,
            Account.display_name,
            Account.email,
            Account.status,
        ).where(Account.id.in_(ids))
    )
    return {
        row.id: SurfaceAccount(
            id=row.id,
            user_id=row.user_id,
            organization_id=row.organization_id,
            auth_config_id=row.auth_config_id,
            connector_id=row.connector_id or "",
            display_name=row.display_name,
            email=row.email,
            status=_value_of(row.status),
        )
        for row in rows
    }


async def account_with_secrets(
    uow, account_id: UUID
) -> tuple[SurfaceAccount, dict[str, object]] | None:
    """An account and the credentials stored for it, decrypted.

    The merge is the point. Credentials are written as an encrypted blob, but a
    row can also carry plaintext keys beside it, and those are the only copy of
    fields the encrypted half never had. A caller reading the column itself has
    to know both that the marker is called ``_encrypted`` and which half wins;
    reading it here means it is known in one place, by the module that writes it.
    """
    found = await _accounts(uow).get(account_id)
    if found is None:
        return None
    credentials = _as_mapping(found.credentials)
    raw = await uow.session.scalar(
        select(Account.credentials).where(Account.id == account_id)
    )
    if isinstance(raw, dict) and not raw.get("_encrypted"):
        for key, value in raw.items():
            credentials.setdefault(key, value)
    return _as_surface_account(found), credentials


async def refreshed_credentials(
    uow, account_id: UUID, *, force_refresh: bool
) -> dict[str, object]:
    """Credentials good to use now, refreshing the token if it needs it.

    No ``user_id``: the owner is on the row, and the caller passing it was only
    ever repeating what the account already said. Getting it wrong answered
    "no such account", which reads as a missing account and is really a missing
    join.
    """
    owner = await _accounts(uow).get(account_id)
    if owner is None:
        return {}
    credentials = await get_connector_service(uow).get_account_credentials(
        account_id, owner.user_id, force_refresh=force_refresh
    )
    return _as_mapping(credentials)


async def require_account_owner(
    uow, account_id: UUID, *, user_id: UUID, organization_id: UUID | None
) -> None:
    """Assert this person owns this account; raise ``AccountNotFoundError`` if not.

    "Not yours" and "no such account" are deliberately the same answer, so the
    caller learns nothing about accounts they do not own.
    """
    await get_connector_service(uow).get_account(account_id, user_id, organization_id)


async def auth_config(uow, auth_config_id: UUID) -> SurfaceAuthConfig | None:
    """The install behind an account, or ``None`` when it is gone."""
    found = await _auth_configs(uow).get(auth_config_id)
    if found is None:
        return None
    return SurfaceAuthConfig(
        id=found.id,
        kind=_value_of(found.kind) or "",
        connector_id=found.connector_id,
        config_source=_value_of(found.config_source),
    )


async def app_signing_secret(uow, auth_config_id: UUID) -> str | None:
    """The signing secret of the provider app this install runs on.

    Its own operation rather than a field on :class:`SurfaceAuthConfig`, because
    a secret that rides along on every read of an install is a secret handed to
    every reader of one. Only webhook verification asks for it.
    """
    found = await _auth_configs(uow).get(auth_config_id)
    if found is None:
        return None
    secret = (found.config or {}).get("signing_secret")
    return str(secret).strip() or None if secret else None


async def surface_connector(uow, connector_id: str) -> SurfaceConnector | None:
    """The catalog entry for a surface's connector, or ``None`` if there is none."""
    try:
        found = await get_connector_service(uow).get_connector(connector_id)
    except ConnectorNotFoundError:
        return None
    return SurfaceConnector(
        connector_id=connector_id,
        title=found.title,
        description=found.description,
        icon=found.icon,
        is_active=bool(found.is_active),
        connect=_connect_capability(found),
    )


def _connect_capability(connector) -> SurfaceConnectCapability | None:
    try:
        capability = connector.capability_for(AuthProvider.LEMMA)
    except ValueError:
        return None
    return SurfaceConnectCapability(
        auth_scheme=capability.auth_scheme,
        auth_config_schema=capability.auth_config_schema,
        credential_schema=capability.credential_schema,
        system_oauth_available=bool(
            getattr(capability, "system_default_available", False)
        ),
        supports_org_custom_oauth=bool(
            getattr(capability, "supports_org_custom_oauth", False)
        ),
    )


__all__ = [
    "SurfaceAccount",
    "SurfaceAuthConfig",
    "SurfaceConnectCapability",
    "SurfaceConnector",
    "account",
    "account_summaries",
    "account_with_secrets",
    "app_signing_secret",
    "auth_config",
    "refreshed_credentials",
    "require_account_owner",
    "surface_connector",
]
