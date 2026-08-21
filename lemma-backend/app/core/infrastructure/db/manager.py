from sqlalchemy import text
from sqlalchemy.exc import CircularDependencyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.core.infrastructure.db.base import Base


class DatabaseManager:
    def __init__(self, database_url: str):
        # pool_pre_ping: liveness-check a pooled connection on checkout and
        # transparently replace it if the server dropped it — mirrors the app
        # engine (session.py). Under parallel e2e load Postgres can close idle
        # backends, and without pre-ping the next borrower fails mid-statement
        # with "connection was closed in the middle of operation". pool_recycle
        # proactively retires connections older than the window.
        self.engine = create_async_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=300,
        )
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def create_tables(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def truncate_all(
        self,
        preserve: frozenset[str] = frozenset(),
        keep_rows: dict[str, str] | None = None,
    ):
        """Fast data reset for e2e: empty every table without dropping the schema.

        ``preserve`` names tables to leave alone entirely — for a global catalog
        that is not per-test data at all.

        ``keep_rows`` maps a table to a SQL predicate identifying the rows that
        survive; everything else in that table is deleted as usual. This is how
        a session-scoped fixture outlives the test that created it without its
        table accumulating: preserving `users` wholesale looked equivalent and
        was not — leftover users from earlier tests changed the answer for a
        later one that looked a phone number up by owner.

        Used between tests so the schema (and any shared worker connections) stay
        alive while data is isolated. CASCADE handles FK order in one statement.

        Only tables that actually hold rows are truncated. A typical test touches
        a handful of tables, so this avoids taking an ACCESS EXCLUSIVE lock on the
        ~100 empty tables in the schema on every test — the dominant per-test DB
        cost. Emptiness is checked with a cheap ``EXISTS`` per table (instant on an
        empty relation), and CASCADE still empties anything referencing a dirty
        table, so isolation is unchanged.
        """
        table_names = [
            table.name
            for table in reversed(Base.metadata.sorted_tables)
            if table.name not in preserve
        ]
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
            if not dirty_ords:
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
            keep_rows = keep_rows or {}
            for ord_ in dirty_ords:
                name = table_names[ord_]
                kept = keep_rows.get(name)
                statement = f'DELETE FROM "{name}"'
                if kept:
                    statement += f" WHERE NOT ({kept})"
                await conn.execute(text(statement))

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
