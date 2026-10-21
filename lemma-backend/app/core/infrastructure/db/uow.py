from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics.domain_events import offer_committed
from app.core.infrastructure.db.session_uow import SESSION_UOW_KEY
from app.core.infrastructure.db.transaction_locks import (
    clear_transaction_scoped_lock,
)
from app.core.domain.uow import IUnitOfWork
from app.core.log.log import get_logger

if TYPE_CHECKING:
    from app.core.domain.events import DomainEvent

logger = get_logger(__name__)


class SqlAlchemyUnitOfWork(IUnitOfWork):
    """SQLAlchemy unit of work; committed domain events go to the after-commit sink.

    Repositories call ``collect_events()`` after saving aggregates. Nothing the
    product depends on reads those events any more: work that must follow a
    change is a work item or a fact written in this transaction
    (``core/infrastructure/work``). After a successful commit the events go to
    the domain-event sink (``app.core.analytics.domain_events``), whose
    projections -- analytics, cache invalidation -- are best effort. A
    rolled-back transaction offers nothing.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self._pending_events: list["DomainEvent"] = []
        self._after_commit: list[Callable[[], Awaitable[None]]] = []
        # Back-reference so a service that only holds the session can still
        # defer work to after the commit. Services are constructed from the
        # session, not the unit of work, and threading one through every
        # constructor to schedule a cache invalidation is a worse trade.
        # Read it back with `active_uow`; tolerant of a session double that has
        # no `info` mapping, because this is a convenience for deferring work
        # and never a requirement for committing.
        info = getattr(session, "info", None)
        if isinstance(info, dict):
            # Last writer wins: two units of work wrapping one session
            # sequentially will each claim it, and only the newest is
            # reachable. That is what the readers want (the active UoW) -- but
            # it is a cycle and a shared slot, so nothing more should be hung
            # off it.
            info[SESSION_UOW_KEY] = self

    def after_commit(self, callback: Callable[[], Awaitable[object]]) -> None:
        """Run ``callback`` once the transaction has actually committed.

        For work that must not happen inside the transaction — cache
        invalidation above all. Doing it inline holds the connection across a
        Redis round trip, and is the wrong order besides: a concurrent reader
        can repopulate the cache from the pre-commit state between the
        invalidation and the commit.
        """
        self._after_commit.append(callback)

    def collect_events(self, events: Sequence["DomainEvent"]) -> None:
        """Collect domain events for publishing on commit.

        Called by repositories after saving aggregates.
        """
        self._pending_events.extend(events)

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            await self.rollback()

    async def commit(self) -> None:
        """Commit, then hand the collected events to the after-commit sink."""
        await self.session.commit()
        # The transaction that held any advisory lock has ended, so a later
        # release in this request is free to commit again.
        clear_transaction_scoped_lock(self.session)
        committed, self._pending_events = self._pending_events, []
        # Product analytics projects committed events after the commit, in
        # batches, off this task: it can neither block nor fail the commit.
        offer_committed(committed)
        callbacks, self._after_commit = self._after_commit, []
        for callback in callbacks:
            await callback()

    async def rollback(self) -> None:
        """Rollback transaction and discard pending events and callbacks."""
        await self.session.rollback()
        self._pending_events.clear()
        # After-commit callbacks belong to the work that just got thrown away.
        # Keeping them would fire a rolled-back mutation's side effect at the
        # next successful commit of a reused unit of work. Harmless for cache
        # invalidation, which is all that registers today, and wrong as a
        # primitive.
        self._after_commit = []

    def has_pending_events(self) -> bool:
        """Check if there are pending events."""
        return len(self._pending_events) > 0
