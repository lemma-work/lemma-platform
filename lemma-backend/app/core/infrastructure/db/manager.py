from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import CircularDependencyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.core.infrastructure.db.base import Base


@dataclass(frozen=True, slots=True)
class TableSnapshot:
    """Rows copied out of the e2e schema into ``schema``, one table per name."""

    schema: str
    tables: tuple[str, ...]


class DatabaseManager:
    def __init__(self, database_url: str):
        # pool_pre_ping: liveness-check a pooled connection on checkout and
        # transparently replace it if the server dropped it — mirrors the app
        # engine (session.py). Under parallel e2e load Postgres can close idle
        # backends, and without pre-ping the next borrower fails mid-statement
        # with "connection was closed in the middle of operation". pool_recycle
        # proactively retires connections older than the window.
        #
        # A small pool: one is built per test, beside the app's own pools and a
        # worker subprocess on the same server, and SQLAlchemy's default ceiling
        # of fifteen per manager was most of what ran a shared test server out
        # of connections under xdist. Tests hold one or two sessions at a time.
        self.engine = create_async_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_size=2,
            max_overflow=3,
            # A test's own statements finish well inside its budget. One that
            # runs away, or a transaction a timed-out test never ended, would
            # otherwise hold its locks until the run ends and fail every later
            # test's reset on that worker; these end it with a named error.
            connect_args={
                "server_settings": {
                    "statement_timeout": "60000",
                    "idle_in_transaction_session_timeout": "60000",
                }
            },
        )
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def create_tables(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def truncate_all(self, *, restore: TableSnapshot | None = None):
        """Fast data reset for e2e: empty every table without dropping the schema.

        ``restore`` puts a snapshot's rows back in the same transaction, so a
        test sees exactly the rows the snapshot holds and nothing else --
        whatever an earlier test changed or added in those tables included.

        Used between tests so the schema (and any shared worker connections) stay
        alive while data is isolated. CASCADE handles FK order in one statement.

        Only tables that actually hold rows are truncated. A typical test touches
        a handful of tables, so this avoids taking an ACCESS EXCLUSIVE lock on the
        ~100 empty tables in the schema on every test — the dominant per-test DB
        cost. Emptiness is checked with a cheap ``EXISTS`` per table (instant on an
        empty relation), and CASCADE still empties anything referencing a dirty
        table, so isolation is unchanged.
        """
        table_names = [table.name for table in reversed(Base.metadata.sorted_tables)]
        if not table_names:
            return
        async with self.engine.begin() as conn:
            await conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            # Find the few tables that actually hold rows. EXISTS is instant on an
            # empty relation, so probing all ~100 tables costs a handful of ms and
            # lets us skip the rest entirely.
            probe = " UNION ALL ".join(
                f'SELECT {i} AS ord WHERE EXISTS (SELECT 1 FROM "{name}")'
                for i, name in enumerate(table_names)
            )
            dirty_ords = sorted((await conn.execute(text(probe))).scalars().all())
            if not dirty_ords and restore is None:
                return
            # DELETE (not TRUNCATE): with only a handful of rows per table it is
            # ~15x cheaper than TRUNCATE's ACCESS EXCLUSIVE lock + per-table file
            # truncation. ``session_replication_role = replica`` disables FK
            # triggers for the wipe, so order is irrelevant and cyclic FKs (e.g.
            # apps <-> app_releases) are handled without CASCADE. SET LOCAL keeps
            # both settings scoped to this transaction, so they revert on commit
            # even if a DELETE raises. Sequences are intentionally not reset:
            # pod-scoped data lives in per-pod schemas and shared tables use UUID
            # keys, so no test depends on identity columns restarting at 1.
            await conn.execute(text("SET LOCAL session_replication_role = 'replica'"))
            for ord_ in dirty_ords:
                await conn.execute(text(f'DELETE FROM "{table_names[ord_]}"'))
            if restore is not None:
                for name in restore.tables:
                    await conn.execute(
                        text(
                            f'INSERT INTO "{name}" '
                            f'SELECT * FROM "{restore.schema}"."{name}"'
                        )
                    )

    async def snapshot(
        self, schema: str, *, exclude: Sequence[str] = ()
    ) -> TableSnapshot:
        """Copy every non-empty table's rows into ``schema``, replacing what it held.

        ``truncate_all(restore=...)`` puts them back. ``CREATE TABLE AS`` keeps
        the column order, so the copy can be restored with ``SELECT *``.
        """
        table_names = [table.name for table in Base.metadata.sorted_tables]
        async with self.engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            probe = " UNION ALL ".join(
                f'SELECT {i} AS ord WHERE EXISTS (SELECT 1 FROM "{name}")'
                for i, name in enumerate(table_names)
            )
            dirty = [
                table_names[ord_]
                for ord_ in sorted((await conn.execute(text(probe))).scalars().all())
            ]
            kept = [name for name in dirty if name not in exclude]
            for name in kept:
                await conn.execute(
                    text(f'CREATE TABLE "{schema}"."{name}" AS SELECT * FROM "{name}"')
                )
        return TableSnapshot(schema=schema, tables=tuple(kept))

    async def drop_snapshot(self, snapshot: TableSnapshot) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text(f'DROP SCHEMA IF EXISTS "{snapshot.schema}" CASCADE')
            )

    async def drop_tables(self):
        async with self.engine.begin() as conn:
            await conn.execute(text("SET lock_timeout = '5s'"))
            await conn.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND pid <> pg_backend_pid()
                    """
                )
            )
            try:
                await conn.run_sync(Base.metadata.drop_all)
            except CircularDependencyError:
                # Test teardown fallback for cyclic FK graphs (e.g. apps <-> app_releases).
                await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))

    async def close(self):
        await self.engine.dispose()
