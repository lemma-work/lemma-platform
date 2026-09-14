"""The reaper, against a real Redis. The reply shapes are the whole point.

Every liveness fact it reads comes back from Redis in a shape no mock pins:
`last-delivered-id` is `<ms>-<seq>` and is `0-0` for a group that has never
read; `idle` only exists once a consumer has been created by an actual XREADGROUP;
`pending` is a count in one reply and a list in another. A unit test proves the
predicate agrees with my idea of those. This proves it agrees with Redis.

The test that matters most here is the streaq one. streaq creates a group called
`workers` on each of its lane queues, and nothing in this repo declares it -- so
the reaper must never see those streams at all. Getting that wrong destroys the
job queue's pending-entries list and every in-flight job with it, which is worse
than the leak the reaper exists to fix.
"""

from __future__ import annotations

import time

import pytest
import redis.asyncio as redis_asyncio

import app.events  # noqa: F401  -- registers the real stream topology
from app.core.infrastructure.events import group_reaper
from app.core.infrastructure.events.stream_observability import (
    _streaq_lane_queues,
)
from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

# Redis only: the reaper never touches the database.
redis_container = e2e_fixtures.redis_container
test_redis_url = e2e_fixtures.test_redis_url

#: A stream this platform really declares, and one of the groups it really
#: declares on it. Using the live topology rather than patching the registry is
#: the point: the reaper only ever looks at `owned_streams()`, so a test that
#: replaced that set would prove nothing about which streams it actually visits.
_STREAM = "agent_events"
_LIVE = "agent-events"
_DEAD = "reaper-probe-abandoned"
_WINDOW = 3600


@pytest.fixture
async def client(test_redis_url):
    redis = redis_asyncio.from_url(test_redis_url, decode_responses=True)
    await redis.delete(
        _STREAM, group_reaper._CLAIMS_KEY, group_reaper._CLAIMS_EPOCH_KEY
    )
    yield redis
    await redis.delete(
        _STREAM, group_reaper._CLAIMS_KEY, group_reaper._CLAIMS_EPOCH_KEY
    )
    await redis.aclose()


async def _reap(client):
    """The reaper, told what it needs rather than shown a patched settings object."""
    return await group_reaper.reap_abandoned_consumer_groups(
        client, window_seconds=_WINDOW, destroy=True
    )


async def _seed(client, *, dead_reads: bool) -> None:
    """One entry, two groups reading from the start; optionally the dead one reads."""
    await client.xadd(_STREAM, {"payload": "1"})
    for group in (_LIVE, _DEAD):
        await client.xgroup_create(_STREAM, group, id="0", mkstream=True)
    await client.xreadgroup(_LIVE, "c-live", {_STREAM: ">"}, count=10)
    if dead_reads:
        await client.xreadgroup(_DEAD, "c-dead", {_STREAM: ">"}, count=10)


async def _age_the_ledger(client) -> None:
    """Backdate the claim epoch so the bootstrap guard lets the reaper act."""
    await group_reaper.claim_registered_groups(client)
    old = int(time.time() * 1000) - (_WINDOW + 60) * 1000
    await client.set(group_reaper._CLAIMS_EPOCH_KEY, old)
    await client.hset(
        group_reaper._CLAIMS_KEY, group_reaper._field(_STREAM, _LIVE), old
    )


async def _group_names(client) -> set[str]:
    return {g["name"] for g in await client.xinfo_groups(_STREAM)}


async def test_an_undeclared_group_that_never_read_is_reaped(client) -> None:
    """`last-delivered-id` is `0-0` here, which the predicate must read as stale.

    This is the production shape: a group created by code that was then deleted,
    so it never advanced past the position it was created at.
    """
    await _seed(client, dead_reads=False)
    await _age_the_ledger(client)

    found = await _reap(client)

    assert [g.group for g in found] == [_DEAD]
    assert await _group_names(client) == {_LIVE}


async def test_a_group_holding_pending_entries_survives(client) -> None:
    """It read and did not ack, so its PEL is work in flight."""
    await _seed(client, dead_reads=True)
    await _age_the_ledger(client)

    found = await _reap(client)

    assert found == []
    assert await _group_names(client) == {_LIVE, _DEAD}


async def test_nothing_is_reaped_while_the_ledger_is_young(client) -> None:
    """The first deploy, and any Redis that has been flushed."""
    await _seed(client, dead_reads=False)
    await group_reaper.claim_registered_groups(client)  # epoch = now

    found = await _reap(client)

    assert found == []
    assert await _group_names(client) == {_LIVE, _DEAD}


async def test_the_streaq_workers_group_survives_a_full_pass(client) -> None:
    """The job queue must be invisible to the reaper.

    streaq creates `workers` on each lane queue and no registry here declares
    it, so every guard downstream would wave it through. The only thing keeping
    it safe is that `owned_streams()` never names the stream -- so this asserts
    on the group still existing after a pass that had every other reason to take
    it.
    """
    # A real lane name, taken from the same helper `observable_streams()` uses.
    # A made-up one passes whatever the reaper is pointed at, because it is in
    # neither set -- which is how the first version of this test was green
    # against the very mistake it exists to catch.
    lane = sorted(_streaq_lane_queues())[0]
    await client.delete(lane)
    await client.xgroup_create(lane, "workers", id="0", mkstream=True)
    await _seed(client, dead_reads=False)
    await _age_the_ledger(client)

    try:
        await _reap(client)
        assert {g["name"] for g in await client.xinfo_groups(lane)} == {"workers"}
    finally:
        await client.delete(lane)


async def _candidate(client, group: str) -> group_reaper.AbandonedGroup:
    """A candidate judged from the group's state right now."""
    info = {g["name"]: g for g in await client.xinfo_groups(_STREAM)}[group]
    claimed = await client.hget(
        group_reaper._CLAIMS_KEY, group_reaper._field(_STREAM, group)
    )
    return group_reaper.AbandonedGroup(
        stream=_STREAM,
        group=group,
        last_delivered_age_seconds=_WINDOW * 2,
        last_delivered_id=info["last-delivered-id"],
        observed_claim=int(claimed) if claimed is not None else None,
    )


async def test_a_consumer_reading_after_the_judgement_keeps_its_group(client) -> None:
    """The race the script exists for, reproduced with the timing forced open.

    The candidate is judged, a consumer then reads, and only afterwards is the
    destroy attempted. Redis runs the script to completion with nothing
    interleaved, so the pending entry the consumer just created is seen by the
    same call that would have destroyed it.
    """
    await _seed(client, dead_reads=False)
    await _age_the_ledger(client)
    candidate = await _candidate(client, _DEAD)

    await client.xreadgroup(_DEAD, "late-consumer", {_STREAM: ">"}, count=10)
    await group_reaper._destroy(client, [candidate])

    assert _DEAD in await _group_names(client)


async def test_a_claim_renewed_after_the_judgement_keeps_its_group(client) -> None:
    """A deployment that came back and re-claimed while the scan was running."""
    await _seed(client, dead_reads=False)
    await _age_the_ledger(client)
    candidate = await _candidate(client, _DEAD)

    await client.hset(
        group_reaper._CLAIMS_KEY,
        group_reaper._field(_STREAM, _DEAD),
        str(int(time.time() * 1000)),
    )
    await group_reaper._destroy(client, [candidate])

    assert _DEAD in await _group_names(client)


async def test_an_expired_claim_left_behind_does_not_block_the_destroy(client) -> None:
    """Declared, claimed, then deleted from the code -- the reaper's own case.

    A claim is only removed when a destroy succeeds, so this group keeps its
    last one forever. An earlier revalidation rejected any claim at all, which
    meant such a group was detected on every pass and destroyed on none.
    """
    await _seed(client, dead_reads=False)
    await _age_the_ledger(client)
    stale = str(int(time.time() * 1000) - (_WINDOW + 600) * 1000)
    await client.hset(
        group_reaper._CLAIMS_KEY, group_reaper._field(_STREAM, _DEAD), stale
    )

    found = await _reap(client)

    assert [g.group for g in found] == [_DEAD]
    assert await _group_names(client) == {_LIVE}
