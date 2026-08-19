"""What PostgreSQL actually guarantees about the outbox wake.

The unit tests cover the half that is ours: the notify is issued inside the
staging transaction and the dispatcher treats a wake as a hint. Everything that
makes that safe is the database's behaviour, not ours, and none of it is
observable without a real server:

* a notification is delivered on commit and never on rollback, so a wake cannot
  point at rows that no longer exist;
* the row is visible to the listener by the time the wake arrives, so there is
  no wake-before-write race to work around;
* a notification issued while nothing is listening is discarded, with no
  replay.

The last one is the load-bearing one. It is why the fallback poll is not an
optimisation to be tuned away later, and why the listener forces a full pass on
reconnect instead of trusting the gap was empty. If someone deletes the poll,
this is the test that should stop them.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.events.models import DomainEventOutbox
from app.core.infrastructure.events.outbox_wake import (
    OUTBOX_WAKE_CHANNEL,
    OutboxWakeListener,
    asyncpg_connect_kwargs,
)
from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = [pytest.mark.e2e]

postgres_container = e2e_fixtures.postgres_container
test_database_url = e2e_fixtures.test_database_url


class _WakeEvent(DomainEvent):
    event_type: str = "wake.e2e"
    producer: str = "e2e"

    @classmethod
    def stream_name(cls) -> str:
        return "wake_e2e_events"


@pytest.fixture
async def outbox_engine(test_database_url: str):
    engine = create_async_engine(test_database_url)
    async with engine.begin() as connection:
        await connection.run_sync(
            DomainEventOutbox.__table__.create, checkfirst=True
        )
    yield engine
    await engine.dispose()


@pytest.fixture
async def listener(test_database_url: str):
    """A live listener on its own connection, as the dispatcher runs it."""
    instance = OutboxWakeListener(test_database_url, label="e2e")
    task = asyncio.create_task(instance.run())
    # run() sets the wake once on connect; consume that so tests observe only
    # what their own transaction produced.
    await asyncio.wait_for(instance.wake.wait(), timeout=10)
    instance.wake.clear()
    yield instance
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _stage(engine, *, commit: bool) -> None:
    session_maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        uow.collect_events([_WakeEvent()])
        if commit:
            await uow.commit()
        else:
            await uow._stage_pending_events()  # noqa: SLF001 - notify must precede
            await uow.rollback()


async def test_commit_wakes_the_listener(outbox_engine, listener) -> None:
    await _stage(outbox_engine, commit=True)

    await asyncio.wait_for(listener.wake.wait(), timeout=10)
    assert listener.notification_count == 1


async def test_rollback_wakes_nobody(outbox_engine, listener) -> None:
    """A wake for erased rows would send the dispatcher looking for nothing."""
    await _stage(outbox_engine, commit=False)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(listener.wake.wait(), timeout=2)
    assert listener.notification_count == 0


async def test_the_row_is_visible_when_the_wake_arrives(
    outbox_engine, listener, test_database_url: str
) -> None:
    """No wake-before-write race: the claim query cannot come up empty."""
    await _stage(outbox_engine, commit=True)
    await asyncio.wait_for(listener.wake.wait(), timeout=10)

    async with outbox_engine.connect() as connection:
        rows = await connection.exec_driver_sql(
            "SELECT count(*) FROM domain_event_outbox WHERE event_type = 'wake.e2e'"
        )
        assert rows.scalar_one() >= 1


async def test_a_notification_issued_with_no_listener_is_lost(
    test_database_url: str,
) -> None:
    """The reason the fallback poll exists. Do not delete the poll.

    Deliberately not routed through the listener fixture: the point is the
    window when nothing is listening at all.
    """
    connection = await asyncpg.connect(
        **asyncpg_connect_kwargs(test_database_url, application_name="lemma-e2e-probe")
    )
    try:
        await connection.execute(f"NOTIFY {OUTBOX_WAKE_CHANNEL}, 'while_nobody_listens'")
    finally:
        await connection.close()

    late = OutboxWakeListener(test_database_url, label="e2e-late")
    task = asyncio.create_task(late.run())
    try:
        # The connect-time wake is the listener's own doing, so it does not
        # count; what matters is that no *notification* was replayed to it.
        await asyncio.wait_for(late.wake.wait(), timeout=10)
        await asyncio.sleep(1)
        assert late.notification_count == 0
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
