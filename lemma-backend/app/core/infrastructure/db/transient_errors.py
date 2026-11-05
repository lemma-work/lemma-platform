"""Database failures that say nothing about the work, only about the moment.

Postgres aborts a transaction to break a deadlock or a serialization conflict,
and the pool gives up when every connection is busy. In each case the same
work run again is expected to succeed, and the person whose request failed did
nothing wrong. Telling these apart from a real error is what lets a caller
retry them, and lets a failure message say "busy, try again" instead of
pointing the reader at their own settings.

Read off the driver's SQLSTATE rather than the message text: asyncpg surfaces
it, and matching on prose would break the moment a locale or a driver changes.
"""

from __future__ import annotations

import socket

import asyncpg

from sqlalchemy.exc import DBAPIError, InterfaceError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

__all__ = [
    "DEADLOCK_DETECTED",
    "SERIALIZATION_FAILURE",
    "is_database_unavailable",
    "is_schema_skew",
    "raise_if_database_unavailable",
    "raise_if_transient_database_error",
    "is_deadlock",
    "is_transient_database_error",
]

DEADLOCK_DETECTED = "40P01"
SERIALIZATION_FAILURE = "40001"

#: A column or table the code names and the database does not have (yet):
#: undefined_column, undefined_table.
_SCHEMA_SKEW_SQLSTATES = frozenset({"42703", "42P01"})

_RETRYABLE_SQLSTATES = frozenset({DEADLOCK_DETECTED, SERIALIZATION_FAILURE})

#: The server cannot take or keep the connection: too many clients (53300),
#: shutting down or starting up (57P01-57P03). Class 08 is the connection
#: itself failing, matched by prefix.
_UNAVAILABLE_SQLSTATES = frozenset({"53300", "57P01", "57P02", "57P03"})
_CONNECTION_EXCEPTION_CLASS = "08"

# How far down ``__cause__`` to look. A caller that wraps a database error in
# its own exception still failed for the database's reason; a short bound keeps
# a pathological chain from being walked forever.
_MAX_CAUSE_DEPTH = 4


def _sqlstate(exc: BaseException) -> str | None:
    """The SQLSTATE on a wrapped driver error, or on asyncpg's own.

    A connection the pool opens is refused before SQLAlchemy wraps anything:
    asyncpg's ``TooManyConnectionsError`` (53300) reaches the caller raw.
    """
    if isinstance(exc, DBAPIError):
        source: object = exc.orig
    elif isinstance(exc, asyncpg.PostgresError):
        source = exc
    else:
        return None
    sqlstate = getattr(source, "sqlstate", None)
    return sqlstate if isinstance(sqlstate, str) else None


def _chain(exc: BaseException, depth: int = 0) -> list[BaseException]:
    """``exc``, its direct causes, and -- for a group -- each member's chain.

    A task group (anyio, pydantic-ai's graph) raises what failed inside it as
    a group, and the member is what says why.
    """
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(chain) <= _MAX_CAUSE_DEPTH:
        chain.append(current)
        if isinstance(current, BaseExceptionGroup) and depth < _MAX_CAUSE_DEPTH:
            for member in current.exceptions:
                chain.extend(_chain(member, depth + 1))
        current = current.__cause__
    return chain


def is_deadlock(exc: BaseException) -> bool:
    """Whether Postgres aborted this transaction to break a deadlock (40P01)."""
    return _sqlstate(exc) == DEADLOCK_DETECTED


def _is_network_os_error(exc: BaseException) -> bool:
    """A socket that could not connect or was cut, not a file error.

    asyncpg raises a bare ``OSError`` when every address it tried refused
    (``localhost`` resolving to two), so the bare type counts; its subclasses
    for files and processes (``FileNotFoundError``, ``PermissionError``...) do
    not, nor does ``TimeoutError``, which a caller classifies as a timeout.
    """
    return type(exc) is OSError or isinstance(
        exc, ConnectionError | socket.gaierror | socket.herror
    )


def _link_unavailable(link: BaseException) -> bool:
    if isinstance(link, PoolTimeoutError | InterfaceError | asyncpg.InterfaceError):
        return True
    if isinstance(link, DBAPIError) and link.connection_invalidated:
        return True
    sqlstate = _sqlstate(link)
    if sqlstate is not None and (
        sqlstate in _UNAVAILABLE_SQLSTATES
        or sqlstate.startswith(_CONNECTION_EXCEPTION_CLASS)
    ):
        return True
    return _is_network_os_error(link)


def is_database_unavailable(exc: BaseException) -> bool:
    """The database could not be reached or could not take another connection.

    A pool with no free connection, a server refusing clients or restarting, a
    connection that broke mid-statement, a socket refused. Nothing about the
    work that hit it is wrong: the same work later is expected to succeed, so
    it is retried uncharged -- unavailability, in the work runtime's terms --
    and never reported as the work failing. Also true when one of those is in
    ``exc``'s chain of direct causes.
    """
    return any(_link_unavailable(link) for link in _chain(exc))


def is_schema_skew(exc: BaseException) -> bool:
    """The code and the schema disagree: a column or table is missing.

    During a release, new code runs before ``alembic upgrade head`` finishes
    and old code after it, each against the other's schema. The same work is
    expected to succeed once the rollout settles -- or never, if it is a real
    bug, which is why the caller bounds how long it believes the first.
    """
    return any(_sqlstate(link) in _SCHEMA_SKEW_SQLSTATES for link in _chain(exc))


def is_transient_database_error(exc: BaseException) -> bool:
    """A deadlock, a serialization failure, or the database being unavailable.

    Also true when one of those is the direct cause of ``exc``.
    """
    return is_database_unavailable(exc) or any(
        _sqlstate(link) in _RETRYABLE_SQLSTATES for link in _chain(exc)
    )


def raise_if_database_unavailable(exc: BaseException) -> None:
    """Re-raise ``exc`` if it is the database being unavailable.

    For a catch that records a failure: an outage is not the work failing,
    so it goes to the work item, which retries it uncharged within its
    unavailable budget instead of the catch recording a failure for good.
    """
    if is_database_unavailable(exc):
        raise exc


def raise_if_transient_database_error(exc: BaseException) -> None:
    """Re-raise ``exc`` if the same statement later is expected to succeed.

    The database unavailable, or a deadlock or serialization failure: for a
    mapper that would otherwise call the error the caller's fault.
    """
    if is_transient_database_error(exc):
        raise exc
