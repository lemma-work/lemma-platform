from __future__ import annotations

from types import TracebackType

from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base
from .repository import AgentBoxRepository


class StateDatabase:
    """Own the SQLAlchemy engine and create short-lived AgentBox units of work."""

    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        self.database_url = database_url
        self.engine = self._create_engine(database_url, echo=echo)
        self._active_units_of_work = 0
        self._sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    @staticmethod
    def _create_engine(database_url: str, *, echo: bool) -> AsyncEngine:
        engine = create_async_engine(
            database_url,
            echo=echo,
            pool_pre_ping=True,
        )
        if database_url.startswith("sqlite+"):

            @event.listens_for(engine.sync_engine, "connect")
            def _configure_sqlite(dbapi_connection, _connection_record) -> None:
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA busy_timeout=5000")
                    cursor.execute("PRAGMA foreign_keys=ON")
                finally:
                    cursor.close()

        return engine

    def uow(self) -> AgentBoxUnitOfWork:
        return AgentBoxUnitOfWork(self._sessions, owner=self)

    @property
    def active_units_of_work(self) -> int:
        """Diagnostic count used by transaction-boundary conformance tests."""

        return self._active_units_of_work

    async def create_schema_for_test(self) -> None:
        """Create schema only for isolated tests; deployments use Alembic."""

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def drop_schema_for_test(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def healthcheck(self) -> None:
        """Run a bounded caller-controlled connectivity probe."""

        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))


class AgentBoxUnitOfWork:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        owner: StateDatabase,
    ) -> None:
        self._session_factory = session_factory
        self._owner = owner
        self._session: AsyncSession | None = None
        self.repository: AgentBoxRepository
        self._committed = False

    async def __aenter__(self) -> AgentBoxUnitOfWork:
        if self._session is not None:
            raise RuntimeError("unit of work cannot be entered twice")
        self._session = self._session_factory()
        await self._session.begin()
        self._owner._active_units_of_work += 1
        self.repository = AgentBoxRepository(self._session)
        return self

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work is not active")
        await self._session.commit()
        self._committed = True

    async def rollback(self) -> None:
        if self._session is not None and self._session.in_transaction():
            await self._session.rollback()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del traceback
        if self._session is None:
            return
        try:
            if exc_type is not None or not self._committed:
                await self.rollback()
        finally:
            await self._session.close()
            self._session = None
            self._owner._active_units_of_work -= 1
