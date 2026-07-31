"""Durable publisher for events without an existing domain transaction."""

from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.session import get_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger

logger = get_logger(__name__)


class EventPublisher:
    """Insert an event into the transactional outbox in a short transaction.

    Callers that already own a UoW must use ``uow.collect_events`` so state and
    event commit atomically. This adapter is for ingress/scheduler boundaries
    that have no associated domain-state mutation.
    """

    @classmethod
    async def publish(cls, stream: str, event: DomainEvent) -> None:
        declared_stream = event.stream_name()
        if stream != declared_stream:
            raise ValueError(
                f"Event stream mismatch: requested {stream!r}, declared {declared_stream!r}"
            )
        async with SessionUnitOfWorkFactory(get_session_maker())() as uow:
            uow.collect_events([event])
        logger.debug(
            "infrastructure.publisher.staged_event_transactional_outbox.observed",
            event_id=str(event.event_id),
            event_type=event.event_type,
        )
