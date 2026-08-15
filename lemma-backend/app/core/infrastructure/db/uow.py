from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.db.transaction_locks import (
    clear_transaction_scoped_lock,
)
from app.core.domain.uow import IUnitOfWork
from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.events.models import DomainEventOutbox
from app.core.infrastructure.events.outbox_wake import notify_outbox_wake
from app.core.log.log import get_logger

if TYPE_CHECKING:
    from app.core.domain.events import DomainEvent

logger = get_logger(__name__)


class SqlAlchemyUnitOfWork(IUnitOfWork):
    """SQLAlchemy unit of work with transactional event staging.

    Collected domain events are inserted into the outbox before the database
    commit. A separate dispatcher publishes them, so a Redis outage cannot lose
    a successfully committed domain change.
    Repositories call `collect_events()` after saving aggregates.
    """

    def __init__(self, session: AsyncSession, message_bus: MessageBus | None = None):
        self.session = session
        # Kept as a constructor compatibility shim for callers that still pass
        # a bus. Publication never occurs from inside the UoW.
        self._message_bus = message_bus
        self._pending_events: list["DomainEvent"] = []
        self._after_commit: list[Callable[[], Awaitable[None]]] = []
        # Back-reference so a service that only holds the session can still
        # defer work to after the commit. Services are constructed from the
        # session, not the unit of work, and threading one through every
        # constructor to schedule a cache invalidation is a worse trade.
        # Tolerant of a session double that has no `info` mapping: this is a
        # convenience for deferring work, never a requirement for committing.
        info = getattr(session, "info", None)
        if isinstance(info, dict):
            # Last writer wins: two units of work wrapping one session
            # sequentially will each claim it, and only the newest is
            # reachable. That is what the readers want (the active UoW), and
            # the reference is only ever used to ask whether events are
            # pending -- but it is a cycle and a shared slot, so nothing more
            # should be hung off it.
            info["lemma_uow"] = self

    def after_commit(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Run ``callback`` once the transaction has actually committed.

        For work that must not happen inside the transaction — cache
        invalidation above all. Doing it inline holds the connection across a
        Redis round trip, and is the wrong order besides: a concurrent reader
        can repopulate the cache from the pre-commit state between the
        invalidation and the commit.
        """
        self._after_commit.append(callback)

    def set_message_bus(self, message_bus: MessageBus) -> None:
        """Backward-compatible no-op setter; dispatch is outbox-driven."""
        self._message_bus = message_bus

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
        """Stage pending events and commit them with domain state."""
        await self._stage_pending_events()
        await self.session.commit()
        # The transaction that held any advisory lock has ended, so a later
        # release in this request is free to commit again.
        clear_transaction_scoped_lock(self.session)
        self._pending_events.clear()
        callbacks, self._after_commit = self._after_commit, []
        for callback in callbacks:
            await callback()

    async def _stage_pending_events(self) -> None:
        if not self._pending_events:
            return

        rows = [
            {
                "id": event.event_id,
                "stream": event.stream_name(),
                "event_type": event.event_type,
                "schema_version": event.schema_version,
                "producer": event.producer,
                "payload": event.model_dump(mode="json"),
                "occurred_at": event.occurred_at,
                "correlation_id": event.correlation_id,
                "causation_id": event.causation_id,
                "request_id": event.request_id,
            }
            for event in self._pending_events
        ]
        # executemany, not `.values(rows)`: the latter renders every row inline,
        # so the compiled SQL is a function of the batch size and the statement
        # cache misses on every size it has not seen. Measured on the datastore
        # twin of this insert as a 1027ms event-loop stall inside the
        # transaction. Same fix, same reason.
        await self.session.execute(
            insert(DomainEventOutbox).on_conflict_do_nothing(index_elements=["id"]),
            rows,
        )
        # In the same transaction as the insert, so the dispatcher can never be
        # woken for rows a rollback erased, nor before they are visible.
        await notify_outbox_wake(self.session)
        logger.debug(
            "infrastructure.uow.staged_domain_events_transactional_outbox.observed",
            event_count=len(rows),
        )

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
