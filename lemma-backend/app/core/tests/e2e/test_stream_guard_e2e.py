"""The stream budget holds against a dead consumer group, and what it trims comes back.

Development ran Redis out of memory on 2026-09-28 with one consumer group dead
for 4.4 hours: every trim the platform had refused to touch that group's unread
entries, so ``datastore.events`` grew to 200,000 entries and 600MB. Every fact
the guard acts on comes from Redis in a shape no mock pins -- ``lag`` goes nil
once entries past a group are trimmed, ``inactive`` exists only from Redis 7.2,
``MEMORY USAGE`` is sampled -- so this runs against a real Redis, and the
replay against a real outbox table.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import redis.asyncio as redis_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.infrastructure.events import stream_budget
from app.core.infrastructure.events.gap_replay import (
    gap_key,
    record_gap,
    replay_gaps,
)
from app.core.infrastructure.events.models import DomainEventOutbox
from app.core.infrastructure.events.stream_guard import (
    RedisMemory,
    find_stalled_groups,
    update_memory_pressure,
)
from app.core.infrastructure.events.stream_keys import MEMORY_PRESSURE_KEY
from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

redis_container = e2e_fixtures.redis_container
test_redis_url = e2e_fixtures.test_redis_url
postgres_container = e2e_fixtures.postgres_container
test_database_url = e2e_fixtures.test_database_url

_STREAM = "stream_guard_e2e_events"
_LIVE = "guard-e2e-live"
_DEAD = "guard-e2e-dead"
_ENTRY = "x" * 4_000


@pytest.fixture
async def client(test_redis_url):
    redis = redis_asyncio.from_url(test_redis_url, decode_responses=True)
    keys = (
        _STREAM,
        MEMORY_PRESSURE_KEY,
        gap_key(_STREAM, _DEAD),
        gap_key(_STREAM, _LIVE),
    )
    await redis.delete(*keys)
    yield redis
    await redis.delete(*keys)
    await redis.aclose()


async def _fill(client, count: int) -> None:
    pipe = client.pipeline()
    for index in range(count):
        pipe.xadd(_STREAM, {"__data__": _ENTRY, "n": index})
    await pipe.execute()


async def _two_groups_one_dead(client, *, entries: int) -> None:
    """A live group that has read everything, and one that never reads."""
    await client.xgroup_create(_STREAM, _LIVE, id="0", mkstream=True)
    await client.xgroup_create(_STREAM, _DEAD, id="0", mkstream=True)
    await _fill(client, entries)
    await client.xreadgroup(_LIVE, "live-consumer", {_STREAM: ">"}, count=entries)
    # Acknowledged, so the live group holds nothing back.
    pending = await client.xpending_range(
        _STREAM, _LIVE, min="-", max="+", count=entries
    )
    await client.xack(_STREAM, _LIVE, *[entry["message_id"] for entry in pending])


async def test_the_budget_holds_through_a_group_that_never_reads(client):
    await _two_groups_one_dead(client, entries=2_000)
    state = await stream_budget.read_stream_state(client, _STREAM)
    assert state is not None
    budget_bytes = state.memory // 4

    outcome = await stream_budget.enforce_stream_budget(client, [state], budget_bytes)

    assert not outcome.still_over
    assert int(await client.memory_usage(_STREAM)) <= budget_bytes
    # Only the dead group lost anything, and the gap says exactly from where.
    assert [gap.group for gap in outcome.gaps] == [_DEAD]
    first_id = stream_budget.parse_id(
        (await client.xinfo_stream(_STREAM))["first-entry"][0]
    )
    assert first_id is not None
    assert outcome.gaps[0].until_ms == first_id[0]


async def test_a_budget_that_empties_the_stream_still_records_the_gap(client):
    """The trim that removes every entry loses the most, and must say so."""
    await _two_groups_one_dead(client, entries=500)
    state = await stream_budget.read_stream_state(client, _STREAM)
    assert state is not None

    outcome = await stream_budget.enforce_stream_budget(client, [state], budget_bytes=1)

    assert await client.xlen(_STREAM) == 0
    assert [gap.group for gap in outcome.gaps] == [_DEAD]


async def test_a_healthy_stream_is_trimmed_without_losing_anything(client):
    """Phase 1 only: what every group has read goes first, and is enough."""
    await client.xgroup_create(_STREAM, _LIVE, id="0", mkstream=True)
    await _fill(client, 2_000)
    await client.xreadgroup(_LIVE, "live-consumer", {_STREAM: ">"}, count=2_000)
    pending = await client.xpending_range(_STREAM, _LIVE, min="-", max="+", count=2_000)
    await client.xack(_STREAM, _LIVE, *[entry["message_id"] for entry in pending])
    await _fill(client, 10)  # unread by anyone
    state = await stream_budget.read_stream_state(client, _STREAM)
    assert state is not None

    outcome = await stream_budget.enforce_stream_budget(
        client, [state], state.memory // 4
    )

    assert outcome.gaps == []
    groups = await client.xinfo_groups(_STREAM)
    assert groups[0]["lag"] == 10, "unread entries were trimmed with room to spare"


async def test_a_reclaimer_does_not_hide_a_dead_reader(client, monkeypatch):
    """The reply shape the stall check depends on: ``inactive`` per consumer.

    In development the reclaimer held the one pending entry and kept polling,
    while the reader had not read for hours."""
    from app.core.infrastructure.events.config import event_transport_settings

    monkeypatch.setattr(event_transport_settings, "redis_stream_stall_seconds", 1)
    await client.xgroup_create(_STREAM, _DEAD, id="0", mkstream=True)
    await _fill(client, 5)
    await client.xreadgroup(_DEAD, f"{_DEAD}-consumer", {_STREAM: ">"}, count=1)
    await _fill(client, 5)
    await asyncio.sleep(1.2)
    # The reclaimer is busy -- and must not count.
    await client.xautoclaim(
        _STREAM, _DEAD, f"{_DEAD}-consumer-reclaimer", min_idle_time=0, count=1
    )
    state = await stream_budget.read_stream_state(client, _STREAM)
    assert state is not None

    stalled = find_stalled_groups([state], {(_STREAM, _DEAD)}, uptime_seconds=10)

    assert stalled == [(_STREAM, _DEAD)]


async def test_memory_pressure_pauses_and_releases_publishing(client, monkeypatch):
    from app.core.infrastructure.events.config import event_transport_settings

    monkeypatch.setattr(event_transport_settings, "redis_memory_critical_ratio", 0.85)
    monkeypatch.setattr(event_transport_settings, "redis_memory_resume_ratio", 0.6)

    assert await update_memory_pressure(client, RedisMemory(used=90, maximum=100))
    assert await client.exists(MEMORY_PRESSURE_KEY)
    # Between resume and critical the flag stays up, so publishing does not flap.
    assert await update_memory_pressure(client, RedisMemory(used=70, maximum=100))
    assert not await update_memory_pressure(client, RedisMemory(used=50, maximum=100))
    assert not await client.exists(MEMORY_PRESSURE_KEY)


# -- replay ---------------------------------------------------------------


@pytest.fixture
async def outbox(test_database_url: str):
    engine = create_async_engine(test_database_url)
    async with engine.begin() as connection:
        await connection.run_sync(DomainEventOutbox.__table__.create, checkfirst=True)
        await connection.execute(
            delete(DomainEventOutbox).where(DomainEventOutbox.stream == _STREAM)
        )
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with engine.begin() as connection:
        await connection.execute(
            delete(DomainEventOutbox).where(DomainEventOutbox.stream == _STREAM)
        )
    await engine.dispose()


async def _published_row(session_maker, published_at: datetime) -> None:
    async with session_maker() as session, session.begin():
        session.add(
            DomainEventOutbox(
                id=uuid4(),
                stream=_STREAM,
                event_type="guard.e2e",
                producer="e2e",
                payload={"n": 1},
                occurred_at=published_at,
                available_at=published_at,
                published_at=published_at,
                attempts=1,
            )
        )


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


async def test_a_trimmed_window_is_republished_once_the_group_reads_again(
    client, outbox
):
    now = datetime.now(timezone.utc)
    trimmed_from, trimmed_until = now - timedelta(hours=2), now - timedelta(hours=1)
    inside = trimmed_from + timedelta(minutes=30)
    outside = now - timedelta(hours=5)
    for moment in (inside, inside, outside):
        await _published_row(outbox, moment)
    await record_gap(
        client,
        stream_budget.StreamGap(
            stream=_STREAM,
            group=_DEAD,
            after_ms=_ms(trimmed_from),
            until_ms=_ms(trimmed_until),
        ),
        now_ms=_ms(now),
    )
    reading_again = {
        (_STREAM, _DEAD): stream_budget.GroupState(
            name=_DEAD, last_delivered=(1, 0), pending=0, lag=0, reader_inactive_ms=5
        )
    }

    requeued = await replay_gaps(client, outbox, reading_again, now_ms=_ms(now))

    assert requeued == 2
    async with outbox() as session:
        rows = (
            await session.scalars(
                select(DomainEventOutbox).where(DomainEventOutbox.stream == _STREAM)
            )
        ).all()
    assert sorted(row.published_at is None for row in rows) == [False, True, True]
    assert not await client.exists(gap_key(_STREAM, _DEAD)), (
        "a replayed gap was kept, so it would be replayed again"
    )


async def test_a_gap_waits_while_its_group_is_still_dead(client, outbox):
    now = datetime.now(timezone.utc)
    await _published_row(outbox, now - timedelta(minutes=30))
    await record_gap(
        client,
        stream_budget.StreamGap(
            stream=_STREAM,
            group=_DEAD,
            after_ms=_ms(now - timedelta(hours=1)),
            until_ms=_ms(now),
        ),
        now_ms=_ms(now),
    )
    still_dead = {
        (_STREAM, _DEAD): stream_budget.GroupState(
            name=_DEAD,
            last_delivered=None,
            pending=0,
            lag=None,
            reader_inactive_ms=None,
        )
    }

    assert await replay_gaps(client, outbox, still_dead, now_ms=_ms(now)) == 0
    assert await client.exists(gap_key(_STREAM, _DEAD))
