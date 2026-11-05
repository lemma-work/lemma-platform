"""A member left the organization: what they held in it goes with them.

Accounts belong to a person *within* an organization (design §3.2), so a
departed member's accounts there are disconnected rather than left for anything
to keep running on: every pod in the organization drops their pod accounts and
the grants sharing them -- the same per-pod cleanup a pod departure does -- and
then the org accounts themselves are revoked at the provider where that is
possible, and deleted. A SHARED connector that ran on one goes NEEDS_ACCOUNT.

That differs from a downgrade on purpose. An owner demoted to POD_VIEWER keeps
serving the accounts they shared, because sharing is about the credential and
they still hold it. Someone who has left the organization holds nothing in it.

Three phases, so no connection is held across a provider round trip: ``plan``
reads (short unit of work), ``revoke`` calls providers (no connection), and
``PodConnectorService.organization_member_left`` deletes (the job's completion
transaction).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.modules.connectors.domain.account import AccountEntity, OAuthCredentials
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.domain.ports import AuthProviderPort
from app.modules.connectors.services.account_revocation import revoke_one
from app.modules.connectors.services.connector_service import ConnectorService

#: Accounts read per page. A person has a handful; the bound is for the shape.
_PAGE = 100


@dataclass(frozen=True, slots=True)
class _Revocation:
    provider: AuthProviderPort
    install: ResolvedAuthInstall | None
    credentials: OAuthCredentials
    user_id: UUID


@dataclass(frozen=True, slots=True)
class Departure:
    """What leaving will take: the accounts, and the grants to give up first."""

    account_ids: list[UUID] = field(default_factory=list)
    revocations: list[_Revocation] = field(default_factory=list)


async def plan(
    service: ConnectorService, *, organization_id: UUID, user_id: UUID
) -> Departure:
    """Their accounts in the organization, unless they are a member again.

    The job runs after the removal commits; someone re-invited in between has
    connected afresh, and what they hold now is not what they left with.
    """
    if await service.organization_access.user_has_organization_role(
        user_id, organization_id
    ):
        return Departure()
    accounts: list[AccountEntity] = []
    cursor: UUID | None = None
    while True:
        page, cursor = await service.account_repository.list_by_user_and_org(
            user_id, organization_id, limit=_PAGE, cursor=cursor
        )
        accounts.extend(page)
        if cursor is None:
            break
    return Departure(
        account_ids=[account.id for account in accounts],
        revocations=[
            revocation
            for account in accounts
            if (revocation := await _revocation(service, account)) is not None
        ],
    )


async def _revocation(
    service: ConnectorService, account: AccountEntity
) -> _Revocation | None:
    if not account.credentials:
        return None
    auth_config = await service.auth_config_repository.get(account.auth_config_id)
    connector = await service.connector_repository.get(account.connector_id)
    if auth_config is None or not service._should_revoke_account(
        connector=connector, auth_config=auth_config
    ):
        return None
    return _Revocation(
        provider=service._get_auth_provider_by_name(
            service._provider_value(auth_config)
        ),
        install=(
            service._resolve_auth_install(connector, auth_config) if connector else None
        ),
        credentials=service._to_oauth_credentials(account.credentials),
        user_id=account.user_id,
    )


async def revoke(departure: Departure) -> None:
    """Give each credential up at its provider. Best effort, like a delete."""
    for revocation in departure.revocations:
        await revoke_one(
            revocation.provider,
            install=revocation.install,
            credentials=revocation.credentials,
            user_id=revocation.user_id,
        )


__all__ = ["Departure", "plan", "revoke"]
