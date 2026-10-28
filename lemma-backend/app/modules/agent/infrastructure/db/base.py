"""What every repository in this package holds: a unit of work, and its session."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


class UnitOfWorkRepository:
    """A repository over one unit of work's session.

    The session is read from the unit of work when it is used rather than
    copied when the repository is built, so code that only constructs one --
    a service wired with no database in a unit test -- never touches it.
    """

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow

    @property
    def session(self) -> AsyncSession:
        return self.uow.session
