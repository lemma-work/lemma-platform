"""A consumer group nobody declares any more, and the five reasons not to trust that.

`surface-schedule-events` outlived the PR that deleted its subscriber. Nothing
removes a group from Redis when its code goes, so it sat there with zero
consumers and a frozen last-delivered-id, pinning the XTRIM watermark until
`schedule_events` reached 825MB against a 256MB budget and Redis hit `maxmemory`
-- twice, taking login down with it.

Destroying a group deletes its pending-entries list and every delivery in flight
with it, so the tests that matter here are the ones proving the reaper does
NOTHING: to a group another process claims, to one with pending work, to one that
read something recently, and -- the one that would hurt most -- to streaq's own
`workers` group on the job queues.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.infrastructure.events import group_reaper
from app.core.infrastructure.events import stream_subscriber as ss
from app.core.infrastructure.events.config import event_transport_settings

WINDOW = event_transport_settings.redis_stream_group_reap_after_seconds
_MS = 1000


@pytest.fixture(autouse=True)
def _declared(monkeypatch):
    """One declared pair, so "undeclared" means something specific."""
    monkeypatch.setattr(ss, "_DECLARED_STREAM_GROUPS", {("schedule_events", "live")})
    monkeypatch.setattr(ss, "_REGISTERED_STREAM_GROUPS", set())


def _client(*, groups, epoch_age_seconds=WINDOW * 2, claims=None, consumers=()):
    now_ms = int(time.time() * _MS)
    client = AsyncMock()
    client.xinfo_groups.return_value = groups
    client.xinfo_consumers.return_value = list(consumers)
    client.hgetall.return_value = claims or {}
    client.get.return_value = str(now_ms - epoch_age_seconds * _MS)
    # The pre-destroy re-read: no claim appeared while the scan was running.
    client.hget.return_value = None
    return client


def _group(name, *, pending=0, delivered_age_seconds=WINDOW * 2):
    delivered = int(time.time() * _MS) - delivered_age_seconds * _MS
    return {"name": name, "pending": pending, "last-delivered-id": f"{delivered}-0"}


@pytest.fixture
def destroy_enabled(monkeypatch):
    monkeypatch.setattr(
        event_transport_settings, "redis_stream_group_destroy_enabled", True
    )


def test_the_streaq_worker_group_is_out_of_scope() -> None:
    """The reaper must never look at the job queues.

    streaq creates a group called `workers` on each of its lane queues, and
    nothing in this repo's registry declares it. A reaper pointed at
    `observable_streams()` -- which is the natural-looking input, because it is
    what the snapshot loop uses -- would find it undeclared on every single tick
    and destroy the job queue's pending-entries list, taking every in-flight job
    with it.

    This test exists so that swapping the input fails here rather than in
    production.
    """
    from app.core.infrastructure.events.stream_observability import observable_streams

    assert not any(
        stream.startswith("streaq") for stream in group_reaper.owned_streams()
    )
    assert any(stream.startswith("streaq") for stream in observable_streams())


async def test_a_group_this_process_declares_is_never_a_candidate() -> None:
    client = _client(groups=[_group("live")])

    assert await group_reaper.reap_abandoned_consumer_groups(client) == []
    client.xgroup_destroy.assert_not_awaited()


async def test_a_group_claimed_by_the_fleet_is_left_alone() -> None:
    """Another deployment may declare groups this one has never heard of."""
    now_ms = int(time.time() * _MS)
    client = _client(
        groups=[_group("owned-elsewhere")],
        claims={group_reaper._field("schedule_events", "owned-elsewhere"): str(now_ms)},
    )

    assert await group_reaper.reap_abandoned_consumer_groups(client) == []
    client.xgroup_destroy.assert_not_awaited()


async def test_a_group_with_pending_entries_is_never_destroyed() -> None:
    """Its PEL is work in flight. Destroying it loses the deliveries."""
    client = _client(groups=[_group("dead", pending=1)])

    assert await group_reaper.reap_abandoned_consumer_groups(client) == []
    client.xgroup_destroy.assert_not_awaited()


async def test_a_group_that_delivered_recently_is_left_alone() -> None:
    client = _client(groups=[_group("busy", delivered_age_seconds=5)])

    assert await group_reaper.reap_abandoned_consumer_groups(client) == []


async def test_a_group_with_a_live_consumer_is_left_alone() -> None:
    client = _client(groups=[_group("dead")], consumers=[{"name": "c1", "idle": 1000}])

    assert await group_reaper.reap_abandoned_consumer_groups(client) == []


async def test_nothing_is_reaped_until_the_ledger_outlives_the_window() -> None:
    """The first deploy, and any Redis that has been flushed.

    Every group looks unclaimed because nothing has had time to claim one. This
    guard is the difference between a reaper and an outage.
    """
    client = _client(groups=[_group("dead")], epoch_age_seconds=60)

    assert await group_reaper.reap_abandoned_consumer_groups(client) == []
    client.xgroup_destroy.assert_not_awaited()


async def test_an_unreadable_ledger_reaps_nothing() -> None:
    client = _client(groups=[_group("dead")])
    client.get.return_value = None

    assert await group_reaper.reap_abandoned_consumer_groups(client) == []


async def test_report_only_names_the_group_and_destroys_nothing() -> None:
    """The default. The set is read against expectations before anything goes."""
    client = _client(groups=[_group("surface-schedule-events")])

    found = await group_reaper.reap_abandoned_consumer_groups(client)

    assert [g.group for g in found] == ["surface-schedule-events"]
    client.xgroup_destroy.assert_not_awaited()


async def test_a_stale_unclaimed_group_is_destroyed_when_enabled(
    destroy_enabled,
) -> None:
    client = _client(groups=[_group("surface-schedule-events")])

    found = await group_reaper.reap_abandoned_consumer_groups(client)

    assert [g.group for g in found] == ["surface-schedule-events"]
    client.xgroup_destroy.assert_awaited_once_with(
        name="schedule_events", groupname="surface-schedule-events"
    )


async def test_claiming_stamps_every_declared_pair() -> None:
    client = AsyncMock()
    # `Redis.pipeline()` is synchronous in redis.asyncio -- it returns the
    # pipeline, it does not await one. An AsyncMock would hand back a coroutine
    # and the real call site would break on it.
    pipe = AsyncMock()
    client.pipeline = MagicMock(return_value=pipe)

    await group_reaper.claim_registered_groups(client)

    pipe.hset.assert_called_once()
    mapping = pipe.hset.call_args.kwargs["mapping"]
    assert set(mapping) == {group_reaper._field("schedule_events", "live")}
    # NX, so the first writer sets the clock the grace window is measured from
    # and no later one can push it forward.
    assert pipe.set.call_args.kwargs.get("nx") is True


async def test_a_reap_window_of_zero_disables_the_reaper(monkeypatch) -> None:
    monkeypatch.setattr(
        event_transport_settings, "redis_stream_group_reap_after_seconds", 0
    )
    client = _client(groups=[_group("dead")])

    assert await group_reaper.reap_abandoned_consumer_groups(client) == []
    client.xinfo_groups.assert_not_awaited()


async def test_a_group_that_never_delivered_is_still_reapable(destroy_enabled) -> None:
    """`last-delivered-id` is `0-0` for a group created and never read from.

    Reading that as "just delivered" made such a group unreapable forever --
    a subscriber deleted before it ever ran would leave one behind permanently.
    It is not evidence either way, so it falls through to the ledger and the
    consumer check, which can tell a brand-new group from an abandoned one.

    Real Redis found this; the mocks here had agreed with the bug.
    """
    client = _client(
        groups=[{"name": "never-read", "pending": 0, "last-delivered-id": "0-0"}]
    )

    found = await group_reaper.reap_abandoned_consumer_groups(client)

    assert [g.group for g in found] == ["never-read"]


async def test_a_group_that_never_delivered_but_is_claimed_survives() -> None:
    """The brand-new-group case: created seconds ago, already on the ledger."""
    now_ms = int(time.time() * _MS)
    client = _client(
        groups=[{"name": "brand-new", "pending": 0, "last-delivered-id": "0-0"}],
        claims={group_reaper._field("schedule_events", "brand-new"): str(now_ms)},
    )

    assert await group_reaper.reap_abandoned_consumer_groups(client) == []


async def test_a_group_that_comes_back_between_the_check_and_the_destroy_survives(
    destroy_enabled,
) -> None:
    """The scan reads every stream before any destroy, so the gap is the pass.

    A deployment returning in that window re-creates its group and reads from
    it. Destroying it then would take the pending-entries list of a group that
    is alive again. The re-read catches it by the position having moved.

    This narrows the race to one round trip rather than closing it -- Redis has
    no compare-and-destroy for a consumer group -- which is why destruction is
    off by default.
    """
    client = _client(groups=[_group("came-back")])
    moved = int(time.time() * _MS)
    client.xinfo_groups.side_effect = [
        [_group("came-back")],  # the scan
        [{"name": "came-back", "pending": 3, "last-delivered-id": f"{moved}-0"}],
    ]

    found = await group_reaper.reap_abandoned_consumer_groups(client)

    assert [g.group for g in found] == ["came-back"]
    client.xgroup_destroy.assert_not_awaited()


async def test_a_claim_appearing_mid_pass_stops_the_destroy(destroy_enabled) -> None:
    """Somebody declared it after the ledger snapshot was taken."""
    client = _client(groups=[_group("reclaimed")])
    client.hget.return_value = str(int(time.time() * _MS))

    await group_reaper.reap_abandoned_consumer_groups(client)

    client.xgroup_destroy.assert_not_awaited()


async def test_an_expired_claim_still_sitting_there_does_not_block_the_destroy(
    destroy_enabled,
) -> None:
    """The lifecycle the reaper exists for: declared, claimed, then deleted.

    A claim is only removed when a destroy succeeds, so a group whose code was
    deleted keeps its last claim in the ledger forever. `_is_abandoned` reads an
    *expired* claim as evidence for abandonment -- but revalidation used to
    reject any claim at all, so this group was detected on every pass and
    destroyed on none of them. The two halves have to agree on what a claim
    means, which is why revalidation compares against what the scan saw rather
    than testing for existence.
    """
    stale = int(time.time() * _MS) - (WINDOW * 2 * _MS)
    field = group_reaper._field("schedule_events", "was-declared-once")
    client = _client(groups=[_group("was-declared-once")], claims={field: str(stale)})
    client.hget.return_value = str(stale)  # unchanged since the scan read it

    found = await group_reaper.reap_abandoned_consumer_groups(client)

    assert [g.group for g in found] == ["was-declared-once"]
    client.xgroup_destroy.assert_awaited_once_with(
        name="schedule_events", groupname="was-declared-once"
    )


async def test_a_claim_renewed_since_the_scan_still_blocks_the_destroy(
    destroy_enabled,
) -> None:
    """The case the comparison must keep catching: renewed, not merely present."""
    stale = int(time.time() * _MS) - (WINDOW * 2 * _MS)
    field = group_reaper._field("schedule_events", "came-back")
    client = _client(groups=[_group("came-back")], claims={field: str(stale)})
    client.hget.return_value = str(int(time.time() * _MS))  # renewed mid-pass

    await group_reaper.reap_abandoned_consumer_groups(client)

    client.xgroup_destroy.assert_not_awaited()
