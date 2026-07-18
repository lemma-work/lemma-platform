from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.core.domain.uow import IUnitOfWork
from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.events.models import DomainEventOutbox
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
        self._pending_events.clear()

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
        await self.session.execute(
            insert(DomainEventOutbox)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["id"])
        )
        logger.debug(
            "infrastructure.uow.staged_domain_events_transactional_outbox.observed",
            event_count=len(rows),
        )

    async def rollback(self) -> None:
        """Rollback transaction and discard pending events."""
        await self.session.rollback()
        self._pending_events.clear()

    def has_pending_events(self) -> bool:
        """Check if there are pending events."""
        return len(self._pending_events) > 0
