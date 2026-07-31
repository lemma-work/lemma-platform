"""Reusable test doubles for unit-testing services without a database.

Phase 6 moved all SQLAlchemy out of the service layer (services depend on a
UnitOfWork + repositories/ports), which makes them unit-testable with fakes.
These are the shared building blocks: a ``FakeUnitOfWork`` and a tiny
``InMemoryRepository`` base. Per-module suites provide stub repositories/ports
that implement only the methods the service under test calls.
"""

from __future__ import annotations

from types import TracebackType
from typing import Generic, TypeVar
from uuid import UUID

from app.core.domain.uow import IUnitOfWork


class FakeUnitOfWork(IUnitOfWork):
    """In-memory UnitOfWork: records commit/rollback and collected events.

    ``session`` is None — services and repos that reach for ``uow.session`` are
    not unit-testable with this and should be exercised via e2e instead. Pure
    services (those that go through repositories/ports) need only this.
    """

    def __init__(self) -> None:
        self.session = None
        self.committed = False
        self.rolled_back = False
        self.collected_events: list[object] = []
        self._message_bus = None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # Mirror SqlAlchemyUnitOfWork-style commit-on-success semantics so
        # services that rely on `async with uow:` behave the same under test.
        if exc_type is None:
            await self.commit()
        else:
            await self.rollback()

    def collect_events(self, events) -> None:
        if events:
            self.collected_events.extend(events)

    def set_message_bus(self, message_bus) -> None:
        self._message_bus = message_bus


E = TypeVar("E")


class InMemoryRepository(Generic[E]):
    """Minimal id-keyed in-memory store for stub repositories.

    Entities must expose an ``id`` attribute. Subclass and add the query methods
    the service under test needs.
    """

    def __init__(self, uow: FakeUnitOfWork | None = None) -> None:
        self.uow = uow or FakeUnitOfWork()
        self._items: dict[UUID, E] = {}

    async def create(self, entity: E) -> E:
        self._items[entity.id] = entity  # type: ignore[attr-defined]
        return entity

    async def get(self, id: UUID) -> E | None:
        return self._items.get(id)

    async def delete(self, id: UUID) -> bool:
        return self._items.pop(id, None) is not None

    def all(self) -> list[E]:
        return list(self._items.values())
class PassthroughEventInbox:
    """Unit-test inbox double that executes one delivery without persistence."""

    async def process(self, consumer, event, handler):
        del consumer, event
        await handler()
        return True
