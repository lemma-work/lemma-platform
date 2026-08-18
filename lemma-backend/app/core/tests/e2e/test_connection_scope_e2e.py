"""Prove the connection-scope detector works against a real engine.

The unit tests drive the listeners with fakes and an injected clock, which
proves the arithmetic. They cannot prove the wiring: that the listeners are
actually attached to the engines the app uses, that SQLAlchemy really delivers
these events for the pool class the tests run under, or that a session opened
the way application code opens one is seen at all.

That is what this file is for. If it ever fails, the detector is blind and
every other test that relies on it is worthless.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from app.core.observability import connection_scope
from app.modules.test_support import connection_scope as connection_scope_support
from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = [pytest.mark.e2e, pytest.mark.connection_scope]

postgres_container = e2e_fixtures.postgres_container
redis_container = e2e_fixtures.redis_container
supertokens_container = e2e_fixtures.supertokens_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager
strict_connection_scope = connection_scope_support.strict_connection_scope

# Comfortably above the fixture's 0.2s threshold, so the assertion is about
# behaviour rather than about how fast the machine is.
_SLOW_WORK_SECONDS = 0.6


async def test_a_session_held_across_non_db_work_is_caught(db_manager) -> None:
    """The failure mode, with a real engine and a real query."""
    from app.core.observability import connection_scope

    monitor = connection_scope.start_connection_scope_monitor(
        idle_hold_seconds=0.2, strict=True
    )
    try:
        from app.core.infrastructure.db.session import get_engine

        monitor.attach(get_engine())
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
            # Stands in for an LLM call, an HTTP request, a sandbox operation.
            await asyncio.sleep(_SLOW_WORK_SECONDS)
    finally:
        connection_scope.stop_connection_scope_monitor()

    assert monitor.reports == 1, "a held connection went unnoticed"
    hold = monitor.violations[0]
    assert hold.gap_seconds >= 0.2
    assert hold.statements >= 1
    # The report has to name the code that took the connection, or nobody can
    # act on it.
    assert "test_connection_scope_e2e.py" in hold.stack


async def test_committing_before_the_slow_work_is_silent(
    db_manager, strict_connection_scope
) -> None:
    """The prescribed fix, verified against a real engine.

    Committing returns the connection, so the slow work runs with none held.
    This is the pattern every fix in this area is converging on; if the
    detector complained about it, the detector would be the problem.
    """
    async with async_session_maker() as session:
        await session.execute(text("SELECT 1"))
        await session.commit()

    await asyncio.sleep(_SLOW_WORK_SECONDS)

    async with async_session_maker() as session:
        await session.execute(text("SELECT 1"))
        await session.commit()

    assert strict_connection_scope.reports == 0


async def test_a_session_doing_ordinary_work_is_silent(
    db_manager, strict_connection_scope
) -> None:
    """Many quick queries must not accumulate into a false positive."""
    async with async_session_maker() as session:
        for _ in range(50):
            await session.execute(text("SELECT 1"))
        await session.commit()

    assert strict_connection_scope.reports == 0


async def test_stopping_the_monitor_unbinds_its_listeners(db_manager) -> None:
    """A stopped monitor must stop receiving pool events.

    `stop_connection_scope_monitor` only cleared the module global. The
    SQLAlchemy listeners it installed stayed bound to the discarded monitor for
    the life of the process, so each start/stop cycle left another dead monitor
    receiving checkouts. Two of them then race over the same per-connection
    state and the live one stops reporting -- a detector that has gone blind
    while still looking green, which is the worst way for this particular thing
    to fail.

    Asserted structurally rather than by counting reports: the listener either
    is bound or it is not.
    """
    from sqlalchemy import event

    from app.core.infrastructure.db.session import get_engine

    monitor = connection_scope.start_connection_scope_monitor(
        idle_hold_seconds=0.05, strict=True
    )
    monitor.attach(get_engine())
    sync_engine = get_engine().sync_engine

    assert event.contains(sync_engine.pool, "checkout", monitor._on_checkout)

    connection_scope.stop_connection_scope_monitor()

    assert not event.contains(sync_engine.pool, "checkout", monitor._on_checkout), (
        "a stopped monitor is still bound to the pool; the next monitor will "
        "race it and the detector goes blind"
    )
    assert not event.contains(sync_engine, "handle_error", monitor._on_statement_error)


async def test_two_monitors_in_a_row_both_detect(db_manager) -> None:
    """The behavioural half of the test above.

    Without the unbind, the second monitor in a process silently detected
    nothing. That is exactly how it was found: a test that armed the monitor
    twice reported DID NOT RAISE on a hold that was plainly there.
    """
    from app.core.infrastructure.db.session import async_session_maker, get_engine

    for attempt in range(2):
        monitor = connection_scope.start_connection_scope_monitor(
            idle_hold_seconds=0.05, strict=True
        )
        monitor.attach(get_engine())
        try:
            async with async_session_maker() as session:
                await session.execute(text("SELECT 1"))
                await asyncio.sleep(0.3)
                await session.commit()
        finally:
            connection_scope.stop_connection_scope_monitor()

        assert monitor.reports >= 1, (
            f"monitor {attempt + 1} saw nothing; listeners from the previous "
            "monitor are still bound and racing it"
        )
