"""Checking that the RLS context a query ran under is the one we set.

Row-level security here is enforced through two session GUCs, and a custom
placeholder is settable by any session role -- PostgreSQL keeps no ACL for
customized options, so it cannot be revoked at the database level. The query
parser rejects ``set_config`` before user SQL runs, and this is the backstop
for the case where it does not recognise a call.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import DatastoreQueryError

logger = get_logger(__name__)


async def verify_rls_context(
    session: AsyncSession,
    user_id: UUID,
    *,
    is_pod_admin: bool = False,
) -> None:
    """Raise if the GUCs set by ``set_rls_context`` no longer hold.

    Unlike the parser, this does not depend on knowing every spelling of a
    tampering call: whatever moved the settings, they are transaction-local and
    do not revert on their own, so the change is still visible afterwards.
    """
    observed = (
        await session.execute(
            text(
                "SELECT current_setting('app.current_user_id', TRUE), "
                "current_setting('app.current_user_is_pod_admin', TRUE)"
            )
        )
    ).one()
    expected = (str(user_id), "true" if is_pod_admin else "false")
    if (str(observed[0] or ""), str(observed[1] or "").lower()) != expected:
        logger.warning("datastore.record.query.rls_context_tampered.degraded")
        raise DatastoreQueryError(
            "Query altered the row-level security context and was discarded"
        )
