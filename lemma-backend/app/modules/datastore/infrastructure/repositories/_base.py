"""Shared plumbing for datastore metadata repositories."""

from __future__ import annotations

from typing import TypeVar

from app.core.domain.aggregate import AggregateRoot
from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

_E = TypeVar("_E", bound=AggregateRoot)


class DatastoreRepositoryBase:
    """Common UoW wiring + event collection for the datastore repositories."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ) -> None:
        self.uow = uow
        self.session = uow.session
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    async def commit(self) -> None:
        """Commit the unit of work this repository writes through.

        For a caller that has to make one store durable before touching
        another; the request's own boundary commits too late to order two
        databases against each other.
        """
        await self.uow.commit()

    def _collect_events(self, entity: AggregateRoot) -> None:
        events = entity.collect_events()
        if events:
            self.uow.collect_events(events)

    @staticmethod
    def _with_allowed_actions(
        entity: _E,
        allowed_actions: list[str] | tuple[str, ...] | None,
    ) -> _E:
        """Attach a row's projected `allowed_actions` to its domain entity."""
        if allowed_actions is not None:
            entity.allowed_actions = list(allowed_actions)
        return entity
