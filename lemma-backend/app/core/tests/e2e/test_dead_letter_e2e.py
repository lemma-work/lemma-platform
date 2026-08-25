"""Work that cannot be completed is kept, not dropped.

`PS-OPS-031` promises three things about a background job that keeps failing:
retrying stops, what was given up on is findable, and nothing is deactivated in
silence. All three are properties of durable state — a row that stops being
claimed and starts being listed — so none of them is observable without a real
database, and none is reachable from the scenario suite, which forbids the
mocking needed to make publication fail on demand.

The failure injected here is the narrowest one that produces the state: a
message bus that raises. Everything else is the real dispatcher against a real
outbox table.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.events.models import DomainEventOutbox
from app.core.infrastructure.events.outbox import OutboxDispatcher, replay_outbox_event
from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = [pytest.mark.e2e]

postgres_container = e2e_fixtures.postgres_container
test_database_url = e2e_fixtures.test_database_url

#: Low on purpose. The promise is about what happens at the end of the retry
#: budget, and ten attempts of exponential backoff is a slow way to ask.
MAX_ATTEMPTS = 2


class _DoomedEvent(DomainEvent):
    event_type: str = "doomed.e2e"
    producer: str = "e2e"

    @classmethod
    def stream_name(cls) -> str:
        return "doomed_e2e_events"


@pytest.fixture
async def outbox(test_database_url: str):
    engine = create_async_engine(test_database_url)
    async with engine.begin() as connection:
        await connection.run_sync(DomainEventOutbox.__table__.create, checkfirst=True)
        # Each test asserts on the whole dead-letter listing, which is the
        # thing an operator actually reads. That only means something if the
        # table holds this test's rows and nobody else's.
        await connection.execute(delete(DomainEventOutbox))
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


async def _stage_one(session_maker) -> None:
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        uow.collect_events([_DoomedEvent()])
        await uow.commit()


async def _row(session_maker) -> DomainEventOutbox:
    async with session_maker() as session:
        rows = (
            await session.scalars(
                select(DomainEventOutbox).where(
                    DomainEventOutbox.event_type == "doomed.e2e"
                )
            )
        ).all()
        assert len(rows) == 1, f"expected exactly one staged event, saw {len(rows)}"
        return rows[0]


def _dispatcher_that_always_fails(session_maker) -> OutboxDispatcher:
    bus = AsyncMock()
    bus.publish.side_effect = RuntimeError("the broker is not answering")
    return OutboxDispatcher(
        session_maker, bus, max_attempts=MAX_ATTEMPTS, lease_seconds=5
    )


async def _drain(dispatcher, session_maker, *, passes: int) -> None:
    """Run the dispatcher until the budget is spent.

    `available_at` carries exponential backoff, so a later pass would find
    nothing to claim. The point is the attempt count, not the waiting, so the
    row is made eligible again between passes rather than sleeping through it —
    the suite does not sleep.
    """
    for _ in range(passes):
        await dispatcher.dispatch_once()
        async with session_maker() as session, session.begin():
            row = (
                await session.scalars(
                    select(DomainEventOutbox).where(
                        DomainEventOutbox.event_type == "doomed.e2e",
                        DomainEventOutbox.dead_lettered_at.is_(None),
                    )
                )
            ).first()
            if row is None:
                return
            row.available_at = row.occurred_at


async def test_a_job_that_keeps_failing_is_given_up_on_and_kept(outbox) -> None:
    """The whole of PS-OPS-031's first two clauses, in one life of one event."""
    await _stage_one(outbox)
    dispatcher = _dispatcher_that_always_fails(outbox)

    await _drain(dispatcher, outbox, passes=MAX_ATTEMPTS)

    given_up = await _row(outbox)
    assert given_up.dead_lettered_at is not None, (
        f"an event failed {given_up.attempts} times with a budget of "
        f"{MAX_ATTEMPTS} and is still not marked as given up, so it will be "
        f"retried forever"
    )
    assert given_up.published_at is None
    assert given_up.last_error_type == "RuntimeError", (
        "nothing recorded about why it was given up on, so an operator has "
        "only the fact and not the cause"
    )

    # Retrying really has stopped: another pass must not pick it up again.
    before = given_up.attempts
    await dispatcher.dispatch_once()
    assert (await _row(outbox)).attempts == before, (
        "a dead-lettered event was claimed again — 'stop retrying' is the "
        "promise, and an unbounded retry of work that cannot succeed is what "
        "it exists to prevent"
    )


async def test_what_was_given_up_on_can_be_found_and_replayed(outbox) -> None:
    """Kept somewhere an operator can find it, and can act on.

    Listing is what `python -m app.core.infrastructure.events.admin --dead`
    does; this asserts the query behind it rather than the printing, because
    the promise is that the work is *findable*, not that it is formatted.
    """
    await _stage_one(outbox)
    dispatcher = _dispatcher_that_always_fails(outbox)
    await _drain(dispatcher, outbox, passes=MAX_ATTEMPTS)

    async with outbox() as session:
        dead = (
            await session.scalars(
                select(DomainEventOutbox).where(
                    DomainEventOutbox.dead_lettered_at.is_not(None)
                )
            )
        ).all()
    assert [row.event_type for row in dead] == ["doomed.e2e"], (
        f"the given-up event is not in the dead-letter listing: {dead}"
    )

    # And an operator can put it back, which is what makes the record useful
    # rather than merely honest.
    assert await replay_outbox_event(outbox, dead[0].id) is True
    revived = await _row(outbox)
    assert revived.dead_lettered_at is None and revived.attempts == 0, (
        f"replaying left the event still given up on: {revived.__dict__}"
    )
