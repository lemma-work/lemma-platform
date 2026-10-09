"""Hourly sweep of what web visitors leave behind.

A visitor's session ends by itself -- an anonymous one ninety days after it
began, a contact's thirty days after it was last used -- or is revoked; its
row is then worth nothing and is deleted, with the codes sent through it. So
are codes that were spent or ran out. And an anonymous web conversation nobody
has touched for ninety days goes too: it answers nobody the pod could name, so
nobody would ever ask for it to be exported or forgotten, and nothing else
would end it. A contact's conversation is never swept here; it is the pod's to
keep or forget.

Bounded batches, as ``mcp_access`` sweeps its grants: a sweep that is far
behind catches up over a few runs rather than holding the bulk lane.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from app.core.infrastructure.db.session import get_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.jobs.streaq_runtime import Lane, streaq_cron
from app.core.log.log import get_logger
from app.modules.agent.contracts.outsider_retention import (
    delete_idle_outsider_conversations,
)
from app.modules.contacts.contracts.visitor_sessions import (
    sweep_visitor_codes,
    sweep_visitor_sessions,
)

logger = get_logger(__name__)

SWEEP_BATCH = 500
SWEEP_BATCHES_PER_RUN = 20
#: How long an anonymous web conversation is kept after anything last happened.
ANONYMOUS_CONVERSATION_IDLE = timedelta(days=90)
WEB_SOURCE = "web_widget"

type Batch = Callable[[SqlAlchemyUnitOfWork, datetime], Awaitable[int]]


async def _drain(uow_factory: UnitOfWorkFactory, batch: Batch, now: datetime) -> int:
    removed = 0
    for _ in range(SWEEP_BATCHES_PER_RUN):
        async with uow_factory() as uow:
            count = await batch(uow, now)
            await uow.commit()
        removed += count
        if count < SWEEP_BATCH:
            break
    return removed


async def _sessions(uow: SqlAlchemyUnitOfWork, now: datetime) -> int:
    return await sweep_visitor_sessions(uow, now=now, batch=SWEEP_BATCH)


async def _codes(uow: SqlAlchemyUnitOfWork, now: datetime) -> int:
    return await sweep_visitor_codes(uow, now=now, batch=SWEEP_BATCH)


async def _conversations(uow: SqlAlchemyUnitOfWork, now: datetime) -> int:
    return await delete_idle_outsider_conversations(
        uow,
        source=WEB_SOURCE,
        idle_since=now - ANONYMOUS_CONVERSATION_IDLE,
        batch=SWEEP_BATCH,
    )


async def sweep_web_visitors(
    uow_factory: UnitOfWorkFactory, *, now: datetime | None = None
) -> tuple[int, int, int]:
    """Codes, then sessions, then conversations: how many of each went."""
    at = now or datetime.now(timezone.utc)
    codes = await _drain(uow_factory, _codes, at)
    sessions = await _drain(uow_factory, _sessions, at)
    conversations = await _drain(uow_factory, _conversations, at)
    return codes, sessions, conversations


@streaq_cron("41 * * * *", name="sweep_web_visitors", lane=Lane.BULK)
async def sweep_web_visitors_task() -> None:
    codes, sessions, conversations = await sweep_web_visitors(
        SessionUnitOfWorkFactory(get_session_maker())
    )
    logger.info(
        "agent_surfaces.tasks.sweep_web_visitors.observed",
        codes=codes,
        sessions=sessions,
        conversations=conversations,
    )
