"""Transaction-failure injection for run-finalization integration tests."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory


class FailOnceUnitOfWorkFactory(UnitOfWorkFactory):
    """Fail a transaction boundary once, then use the real database factory."""

    def __init__(self, wrapped: UnitOfWorkFactory) -> None:
        self.wrapped = wrapped
        self.failed = False

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[SqlAlchemyUnitOfWork]:
        if not self.failed:
            self.failed = True
            raise ConnectionError("receipt status transaction unavailable")
        async with self.wrapped() as uow:
            yield uow
