"""Getting a usable credential for a connected account, from outside the module.

One entry point, because there is one rule: anything that is about to *use* a
stored token refreshes it first if it is due. The sandbox's `git`/`gh` bridge
was the path that did not -- it read the stored credential verbatim while the
refresh machinery sat one module away -- and so it kept writing an expired token
into the workspace for as long as a session lasted.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.services.credential_freshness import (
    credential_expires_at,
    fresh_credentials,
)


async def fresh_account_credentials(
    uow: SqlAlchemyUnitOfWork, account: Any, user_id: Any
) -> dict[str, Any]:
    """This account's credentials, renewed if they are due to expire.

    Takes the caller's unit of work so the refresh -- which re-reads and
    re-encrypts the account -- happens in the transaction the caller already
    has, rather than opening a second one beside it.
    """
    from app.modules.connectors.api.dependencies import get_connector_service

    return await fresh_credentials(
        account, user_id, connector_service=get_connector_service(uow)
    )


def credentials_expire_at(credentials: dict[str, Any] | None) -> datetime | None:
    """When a credential stops working, if the provider said.

    Exposed alongside the refresh because a caller that *copies* a token
    somewhere -- into a sandbox, say -- has to know how long its copy is good
    for, and cannot ask the provider again to find out.
    """
    return credential_expires_at(credentials)


__all__ = ["credentials_expire_at", "fresh_account_credentials"]
