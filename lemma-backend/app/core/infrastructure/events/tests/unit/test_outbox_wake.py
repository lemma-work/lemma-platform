"""Contract tests for the LISTEN/NOTIFY wake path.

These cover the properties the design depends on that are ours to keep: the
wake is issued inside the staging transaction, it never becomes a delivery
channel, and losing it costs latency rather than events. The PostgreSQL-side
guarantees (transactional delivery, dedup, discard-with-no-listener) are the
database's contract, exercised in the e2e suite against a real server.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.outbox import OutboxDispatcher
from app.core.infrastructure.events.outbox_wake import (
    OUTBOX_WAKE_CHANNEL,
    asyncpg_connect_kwargs,
)
from app.core.domain.events import DomainEvent


class _WakeEvent(DomainEvent):
    event_type: str = "wake.test"
    producer: str = "test"

    @classmethod
    def stream_name(cls) -> str:
        return "wake_test_events"


class _RecordingSession:
    """Session double that records statement text in commit order."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.info: dict = {}

    async def execute(self, statement, parameters=None) -> None:
        self.log.append(str(statement))

    async def commit(self) -> None:
        self.log.append("COMMIT")

    async def rollback(self) -> None:
        self.log.append("ROLLBACK")


@pytest.mark.asyncio
async def test_wake_is_notified_inside_the_staging_transaction() -> None:
    """The notify must precede the commit, or it can wake for erased rows."""
    session = _RecordingSession()
    uow = SqlAlchemyUnitOfWork(session)  # type: ignore[arg-type]
    uow.collect_events([_WakeEvent()])

    await uow.commit()

    notify_index = next(i for i, s in enumerate(session.log) if "pg_notify" in s)
    assert notify_index < session.log.index("COMMIT")


@pytest.mark.asyncio
async def test_rollback_stages_nothing_and_notifies_nothing() -> None:
    session = _RecordingSession()
    uow = SqlAlchemyUnitOfWork(session)  # type: ignore[arg-type]
    uow.collect_events([_WakeEvent()])

    await uow.rollback()

    assert not any("pg_notify" in statement for statement in session.log)


@pytest.mark.asyncio
async def test_one_wake_per_transaction_regardless_of_event_count() -> None:
    """A hundred staged events must not mean a hundred notifications."""
    session = _RecordingSession()
    uow = SqlAlchemyUnitOfWork(session)  # type: ignore[arg-type]
    uow.collect_events([_WakeEvent() for _ in range(100)])

    await uow.commit()

    assert sum("pg_notify" in statement for statement in session.log) == 1


def test_connect_kwargs_lift_ssl_out_of_the_query_string() -> None:
    """asyncpg takes ssl as an argument; leaving it in the DSN fails to connect."""
    kwargs = asyncpg_connect_kwargs(
        "postgresql+asyncpg://u:p@h:5432/db?ssl=require", application_name="probe"
    )
    assert kwargs["dsn"] == "postgresql://u:p@h:5432/db"
    assert kwargs["ssl"] == "require"
    assert kwargs["server_settings"]["application_name"] == "probe"


def test_connect_kwargs_accept_the_libpq_spelling() -> None:
    kwargs = asyncpg_connect_kwargs(
        "postgresql+asyncpg://u:p@h/db?sslmode=verify-full", application_name="probe"
    )
    assert kwargs["ssl"] == "verify-full"


def test_connect_kwargs_omit_ssl_when_unset() -> None:
    kwargs = asyncpg_connect_kwargs(
        "postgresql+asyncpg://u:p@h/db", application_name="probe"
    )
    assert "ssl" not in kwargs


def test_wake_channel_is_a_bare_identifier() -> None:
    """It reaches pg_notify as a bound parameter, but keep it trivially safe."""
    assert OUTBOX_WAKE_CHANNEL.replace("_", "").isalnum()


def _dispatcher(wake: asyncio.Event | None) -> OutboxDispatcher:
    return OutboxDispatcher(lambda: None, object(), wake=wake)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_idle_wait_returns_immediately_when_woken() -> None:
    wake = asyncio.Event()
    dispatcher = _dispatcher(wake)
    wake.set()

    async with asyncio.timeout(1):
        await dispatcher._idle_wait(5.0)  # noqa: SLF001 - loop internals under test

    # Cleared so the next idle wait blocks again rather than spinning.
    assert not wake.is_set()


@pytest.mark.asyncio
async def test_idle_wait_falls_back_when_no_notification_arrives(monkeypatch) -> None:
    """A silent listener must cost latency, never delivery."""
    monkeypatch.setattr(
        event_transport_settings, "outbox_listen_fallback_poll_seconds", 0.05
    )
    dispatcher = _dispatcher(asyncio.Event())

    async with asyncio.timeout(1):
        await dispatcher._idle_wait(600.0)  # noqa: SLF001


@pytest.mark.asyncio
async def test_notification_during_dispatch_survives_to_the_next_pass() -> None:
    """Clearing after a claim would swallow a wake that raced the dispatch."""
    wake = asyncio.Event()
    dispatcher = _dispatcher(wake)
    wake.set()
    await dispatcher._idle_wait(5.0)  # noqa: SLF001 - consumes the first wake

    wake.set()  # arrives while the dispatcher is busy publishing

    async with asyncio.timeout(1):
        await dispatcher._idle_wait(600.0)  # noqa: SLF001 - must not block


@pytest.mark.asyncio
async def test_dispatcher_without_a_wake_still_sleeps_on_the_ladder() -> None:
    """Disabling the listener must restore timer-driven behaviour exactly."""
    dispatcher = _dispatcher(None)
    loop = asyncio.get_running_loop()

    started = loop.time()
    await dispatcher._idle_wait(0.05)  # noqa: SLF001
    assert loop.time() - started >= 0.04


def test_the_wake_listener_is_on_by_default() -> None:
    """The backoff ladder is what a chat message waits out before anything runs.

    An idle dispatcher climbs to ``outbox_idle_poll_max_seconds`` and a message
    landing mid-sleep waits out the remainder; measured against a local stack
    that was 1.4-4.1s per message, ahead of every other term in the turn. The
    listener removes it, and the fallback poll means turning it on cannot lose
    an event -- only find it sooner.
    """
    assert event_transport_settings.outbox_listen_enabled


def test_attaching_a_listener_cannot_be_slower_than_the_ladder_it_replaces() -> None:
    """The fallback is a recovery bound, and must not become a regression.

    A transaction-mode pooler swallows session-scoped LISTEN silently, so a
    deployment behind one gets the fallback rather than the wake. If that
    fallback were longer than the ladder, switching the listener on would make
    exactly those deployments slower -- which is the one outcome a hint is not
    allowed to have.
    """
    assert (
        event_transport_settings.outbox_listen_fallback_poll_seconds
        <= event_transport_settings.outbox_idle_poll_max_seconds
    )
