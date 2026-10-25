"""Whether this deployment's datastore role can give pods their query roles.

Every pod's queries run as a role of its own, made when its schema is
(``infrastructure/query_role``). Making a role needs ``CREATEROLE``; without it
the schema is still created, its role is not, and every query against it --
the query API, an agent's SQL tool, a workflow step -- answers
``DATASTORE_QUERY_UNAVAILABLE``. That used to surface only as a warning per
schema, so a deployment missing the privilege found out from its users.

Reported on ``/health/ready`` without blocking it: the rest of the product
works, and taking every replica out of rotation would make one feature's
missing grant an outage.
"""

from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.log.log import get_logger

logger = get_logger(__name__)

OK = "ok"
MISSING_CREATEROLE = "missing_createrole"
UNKNOWN = "unknown"

#: Re-asked at most this often, whatever the last answer: granting the
#: privilege is picked up without a restart, and revoking it is noticed.
_RECHECK_INTERVAL_SECONDS = 60.0

_last_state = UNKNOWN
_last_checked_at: float | None = None


async def datastore_role_state(
    *, recheck_after: float = _RECHECK_INTERVAL_SECONDS
) -> str:
    """``ok``, ``missing_createrole``, or ``unknown`` when it could not be asked."""
    global _last_state, _last_checked_at
    now = time.monotonic()
    if _last_checked_at is not None and now - _last_checked_at < recheck_after:
        return _last_state
    _last_checked_at = now
    from app.modules.datastore.infrastructure.session import get_datastore_engine

    try:
        async with get_datastore_engine().connect() as conn:
            may = (
                await conn.execute(
                    text(
                        "SELECT rolsuper OR rolcreaterole FROM pg_roles "
                        "WHERE rolname = current_user"
                    )
                )
            ).scalar()
    except SQLAlchemyError, OSError:
        # The ``db`` component reports a database that cannot be reached.
        # Cached, so the answer until the next check is the last one seen,
        # not an ``ok`` that check could no longer confirm.
        _last_state = UNKNOWN
        return UNKNOWN
    state = OK if may else MISSING_CREATEROLE
    if state == MISSING_CREATEROLE and _last_state != MISSING_CREATEROLE:
        logger.error(
            "datastore.query_role.createrole_missing.failed",
            remedy="ALTER ROLE <datastore role> CREATEROLE",
        )
    _last_state = state
    return state


__all__ = ["MISSING_CREATEROLE", "OK", "UNKNOWN", "datastore_role_state"]
