"""Which account each member's per-member calls would run as, in two reads.

The answer ``PodConnectorResolver._member_account`` reaches one caller at a
time -- the member's default pod account on the installation, else their own
default org account on it -- asked for a whole pod at once, for the editor's
coverage view. Status only: nothing here decrypts a credential or reads an
account's name, because nobody but the account's owner is shown either.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.pod_connector import PodAccount

#: A person rarely has more than a couple of accounts on one installation.
_ACCOUNTS_PER_PERSON = 20


async def member_account_statuses(
    session: AsyncSession,
    *,
    pod_id: UUID,
    auth_config_id: UUID,
    user_ids: Collection[UUID],
) -> dict[UUID, str]:
    """``{user_id: account status}`` for the members who have an account on
    this installation. A member missing from the result has none."""
    wanted = sorted(set(user_ids))
    if not wanted:
        return {}
    pinned = await _pinned_accounts(
        session, pod_id=pod_id, auth_config_id=auth_config_id, user_ids=wanted
    )
    statuses: dict[UUID, str] = {}
    for account_id, user_id, status in await _accounts_on(
        session, auth_config_id=auth_config_id, user_ids=wanted
    ):
        if pinned.get(user_id) == account_id:
            statuses[user_id] = str(status)
        else:
            statuses.setdefault(user_id, str(status))
    return statuses


async def _pinned_accounts(
    session: AsyncSession,
    *,
    pod_id: UUID,
    auth_config_id: UUID,
    user_ids: list[UUID],
) -> dict[UUID, UUID]:
    """Each member's default pod account here: the one their calls use."""
    rows = await session.execute(
        select(PodAccount.owner_user_id, PodAccount.account_id)
        .where(
            PodAccount.pod_id == pod_id,
            PodAccount.auth_config_id == auth_config_id,
            PodAccount.is_member_default.is_(True),
            PodAccount.owner_user_id.in_(user_ids),
        )
        .limit(len(user_ids))
    )
    return {row[0]: row[1] for row in rows.all()}


async def _accounts_on(
    session: AsyncSession, *, auth_config_id: UUID, user_ids: list[UUID]
) -> list[tuple[UUID, UUID, str]]:
    """Their org accounts on the installation, in the order the resolver
    picks in: the default, then the oldest."""
    rows = await session.execute(
        select(Account.id, Account.user_id, Account.status)
        .where(
            Account.auth_config_id == auth_config_id,
            Account.user_id.in_(user_ids),
        )
        .order_by(Account.user_id, Account.is_default.desc(), Account.created_at)
        .limit(len(user_ids) * _ACCOUNTS_PER_PERSON)
    )
    return [(account_id, user_id, status) for account_id, user_id, status in rows.all()]


__all__ = ["member_account_statuses"]
