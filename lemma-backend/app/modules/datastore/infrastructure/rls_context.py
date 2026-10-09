"""Naming a session's row principal, and checking it still holds afterwards.

Row-level security here is enforced through session GUCs, and a custom
placeholder is settable by any session role -- PostgreSQL keeps no ACL for
customized options, so it cannot be revoked at the database level. The query
parser rejects ``set_config`` before user SQL runs, and the check below is the
backstop for the case where it does not recognise a call.

What is written and what is checked come from the same ``RowPrincipal``, so the
two cannot disagree about how "no user" is spelled -- the disagreement that
once wrote the nil UUID, expected ``"None"``, and discarded every query an
anonymous run made.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import DatastoreQueryError
from app.modules.datastore.domain.row_security import (
    ADMIN_SETTING,
    AUDIENCE_SETTING,
    CONTACT_SETTING,
    USER_SETTING,
    RowPrincipal,
)

logger = get_logger(__name__)

_SETTINGS = (USER_SETTING, ADMIN_SETTING, AUDIENCE_SETTING, CONTACT_SETTING)

_APPLY_SQL = text(
    "SELECT "
    + ", ".join(
        f"set_config('{name}', :p{index}, true)" for index, name in enumerate(_SETTINGS)
    )
)
_READ_SQL = text(
    "SELECT " + ", ".join(f"current_setting('{name}', TRUE)" for name in _SETTINGS)
)


async def apply_row_principal(session: AsyncSession, principal: RowPrincipal) -> None:
    """Name the principal for this transaction, all four settings, in one trip.

    This runs before every RLS-guarded read and write, so a second statement
    here is a second round trip on the hottest path there is. Every setting is
    transaction-local (``set_config(..., true)`` is the function form of
    ``SET LOCAL``), so nothing leaks to the next borrower of the connection and
    a transaction-mode pooler stays usable.
    """
    values = principal.settings()
    await session.execute(
        _APPLY_SQL,
        {f"p{index}": values[name] for index, name in enumerate(_SETTINGS)},
    )


async def verify_rls_context(session: AsyncSession, principal: RowPrincipal) -> None:
    """Raise if the settings ``apply_row_principal`` wrote no longer hold.

    Unlike the parser, this does not depend on knowing every spelling of a
    tampering call: whatever moved the settings, they are transaction-local and
    do not revert on their own, so the change is still visible afterwards.
    """
    observed = (await session.execute(_READ_SQL)).one()
    expected = principal.settings()
    if tuple(str(value or "") for value in observed) != tuple(
        expected[name] for name in _SETTINGS
    ):
        logger.warning("datastore.record.query.rls_context_tampered.degraded")
        raise DatastoreQueryError(
            "Query altered the row-level security context and was discarded"
        )
