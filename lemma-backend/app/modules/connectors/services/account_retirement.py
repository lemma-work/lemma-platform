"""Standing accounts down when their upstream tenant goes away.

Owned by this module because the rows are: a caller that knows an installation
was uninstalled should not have to know how `accounts` is shaped, and
`app/composition` reaching into another module's tables is the exact coupling
the architecture gate is there to stop.
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.domain.account import AccountStatus
from app.modules.connectors.infrastructure.models.account import Account


async def retire_accounts_for_tenant(
    session: AsyncSession, *, connector_id: str, external_ref: str
) -> int:
    """Mark every account bound to `external_ref` as needing re-authorization.

    Marked, not deleted, for the same reason the App cutover migration marks
    them: four things reference an account without a foreign key -- tool grants,
    a conversation's `metadata.repo.account_id`, bundle bindings, and publish's
    required `account_id` -- and deleting the rows would silently break sandbox
    `git` and pod publishing. Reconnecting repairs them in place.

    Returns how many rows changed, so the caller can say so.
    """
    result = await session.execute(
        update(Account)
        .where(
            Account.connector_id == connector_id,
            Account.external_ref == external_ref,
            Account.status != AccountStatus.REAUTH_REQUIRED.value,
        )
        .values(status=AccountStatus.REAUTH_REQUIRED.value)
    )
    return int(result.rowcount or 0)
