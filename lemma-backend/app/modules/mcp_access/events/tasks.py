"""Hourly sweep of connections nobody can use any more.

Each refresh already prunes its own grant's tokens, which is where the table
grows. What refreshing cannot catch is a connection that stops refreshing: its
refresh token lapses, and its rows would sit there, listed as connected,
forever. This ends such grants -- and any past `GRANT_MAX_AGE` -- and deletes
their tokens, a bounded batch at a time.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.infrastructure.db.session import get_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_runtime import Lane, streaq_cron
from app.core.log.log import get_logger
from app.modules.mcp_access.infrastructure.repositories import McpAccessRepository

logger = get_logger(__name__)

SWEEP_BATCH = 500
SWEEP_BATCHES_PER_RUN = 20
"""At most this many batches an hour: a sweep that is far behind catches up
over a few runs rather than holding the bulk lane."""


@streaq_cron("37 * * * *", name="sweep_mcp_access_grants", lane=Lane.BULK)
async def sweep_mcp_access_grants() -> None:
    uow_factory = SessionUnitOfWorkFactory(get_session_maker())
    ended = 0
    for _ in range(SWEEP_BATCHES_PER_RUN):
        async with uow_factory() as uow:
            count = await McpAccessRepository(uow).sweep(
                now=datetime.now(timezone.utc), batch=SWEEP_BATCH
            )
            await uow.commit()
        ended += count
        if count < SWEEP_BATCH:
            break
    logger.info("mcp_access.tasks.sweep_grants.observed", ended=ended)
