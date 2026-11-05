"""A connection Postgres refuses arrives as a SQLAlchemy error, like every other.

SQLAlchemy wraps what the asyncpg adapter raises on a statement, but not what
``asyncpg.connect`` raises when the pool opens a connection: a full server
(``TooManyConnectionsError``, 53300), one starting up or shutting down, a
refused socket. Those reached callers raw, past every ``except
SQLAlchemyError`` written to tolerate the database -- the work runtime's
heartbeat and claim loop, the change handoff, the stop check -- so a moment of
connection pressure crashed the task that was meant to ride it out.

``translate_connect_errors`` wraps the dialect's connect: a refusal becomes
the adapter's ``OperationalError`` carrying the server's SQLSTATE, which
SQLAlchemy then raises as ``sqlalchemy.exc.OperationalError``.
"""

from __future__ import annotations

import asyncpg
from sqlalchemy import event
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import ConnectionPoolEntry

#: What ``asyncpg.connect`` raises when it cannot have a connection.
_REFUSALS = (asyncpg.PostgresError, asyncpg.InterfaceError, OSError)


def translate_connect_errors(engine: AsyncEngine) -> AsyncEngine:
    """Make ``engine``'s connect failures SQLAlchemy errors; returns ``engine``."""

    @event.listens_for(engine.sync_engine, "do_connect")
    def _connect(
        dialect: Dialect,
        conn_rec: ConnectionPoolEntry,
        cargs: tuple[object, ...],
        cparams: dict[str, object],
    ) -> object:
        del conn_rec
        dbapi = dialect.loaded_dbapi
        try:
            return dbapi.connect(*cargs, **cparams)
        except _REFUSALS as exc:
            refused = dbapi.OperationalError(f"{type(exc).__name__}: {exc}")
            sqlstate = getattr(exc, "sqlstate", None)
            refused.pgcode = refused.sqlstate = sqlstate
            raise refused from exc

    return engine


__all__ = ["translate_connect_errors"]
