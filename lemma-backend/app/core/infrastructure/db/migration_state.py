"""Is this process's schema the one its code was written against?

``/health/ready`` used to answer with a ``SELECT 1``, which proves the database
is up and nothing about whether it has the columns this build needs. During a
rolling deploy that is the difference PS-OPS-030 exists to name: a new replica
reports ready against the old schema and starts serving traffic that fails on a
missing column -- errors that read as application bugs and send the reader to
the wrong code. ``lemma-stack`` gates this externally by running migrations as a
one-shot before the backend starts; a Kubernetes or hand-rolled deployment has
no such gate.

Two cheap reads, both cached hard once they agree: the head revision the shipped
migration scripts define, and the revision the database says it is at.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db import session

CURRENT = "current"
PENDING = "pending"
UNKNOWN = "unknown"

#: Re-asked only while the answer is not yet ``current``, because that is the
#: window a deployment spends waiting for its migration job. Latching the
#: positive answer instead of caching every answer is what keeps a replica that
#: started one second early from being unready for the rest of its life.
_RECHECK_INTERVAL_SECONDS = 5.0

_schema_is_current = False
_last_state = UNKNOWN
_last_checked_at: float | None = None


@lru_cache(maxsize=1)
def _code_head_revision() -> str | None:
    """The head the shipped migration scripts define, or None if unanswerable.

    ``migrations/`` sits beside ``app/`` in the repository and in the image, so
    it is found relative to this file rather than through the working directory,
    which differs between the API, the worker and a test run.
    """
    # Alembic is imported here rather than at module scope: it is 110 modules,
    # this module is reached from `app.app`, and the head is read at most once
    # per process. The import budget gate is what noticed.
    from alembic.script import ScriptDirectory
    from alembic.util.exc import CommandError

    directory = Path(__file__).resolve().parents[4] / "migrations"
    if not directory.is_dir():
        return None
    try:
        return ScriptDirectory(str(directory)).get_current_head()
    except CommandError, OSError:
        # Several heads, or scripts that will not load. Both are real problems
        # and neither is one readiness can express, so it declines to answer
        # rather than reporting a schema mismatch that may not exist.
        return None


def forget_cached_schema_state() -> None:
    """Drop the cached verdict, for a process that has changed database.

    ``session.reset_engine_state`` exists for the same reason: the engine is
    process state, and a verdict about the schema behind it does not survive
    being pointed at a different one.
    """
    global _schema_is_current
    global _last_checked_at
    global _last_state
    _code_head_revision.cache_clear()
    _schema_is_current = False
    _last_state = UNKNOWN
    _last_checked_at = None


async def _applied_revision() -> str | None:
    # Through the module rather than a bound name: the engine is process state
    # that tests and the desktop supervisor both replace after import.
    async with session.get_engine().connect() as conn:
        result = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    return str(result) if result else None


async def schema_migration_state() -> str:
    """``current``, ``pending``, or ``unknown`` when it could not be asked."""
    global _schema_is_current
    global _last_checked_at
    global _last_state
    if _schema_is_current:
        return CURRENT
    now = time.monotonic()
    if _last_checked_at is not None and now - _last_checked_at < (
        _RECHECK_INTERVAL_SECONDS
    ):
        return _last_state
    _last_checked_at = now
    head = _code_head_revision()
    try:
        applied = await _applied_revision() if head else None
    except SQLAlchemyError:
        # No ``alembic_version`` table, or the database is unreachable. The
        # ``db`` component already reports the second, and neither is a reason
        # to hold a process out of rotation on a schema question.
        applied = None
    if applied is None:
        _last_state = UNKNOWN
        return _last_state
    _schema_is_current = applied == head
    _last_state = CURRENT if _schema_is_current else PENDING
    return _last_state
