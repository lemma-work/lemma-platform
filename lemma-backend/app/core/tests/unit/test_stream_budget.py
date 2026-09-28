"""The arithmetic of the stream budget: what it keeps, what it takes, and from whom.

The trims themselves run against a real Redis in
``test_stream_guard_e2e.py``; the reply shapes are the part a mock cannot pin.
These pin the decisions made from those replies.
"""

from __future__ import annotations

import pytest

from app.core.infrastructure.events import stream_budget as budget
from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.gap_replay import replay_window
from app.core.infrastructure.events.stream_guard import (
    RedisMemory,
    find_stalled_groups,
    streams_budget,
)

pytestmark = pytest.mark.unit


def _group(
    name: str,
    *,
    delivered: tuple[int, int] | None,
    lag: int | None = 0,
    pending: int = 0,
    oldest_pending: tuple[int, int] | None = None,
    inactive_ms: int | None = 0,
) -> budget.GroupState:
    return budget.GroupState(
        name=name,
        last_delivered=delivered,
        pending=pending,
        lag=lag,
        oldest_pending=oldest_pending,
        reader_inactive_ms=inactive_ms,
    )


def _stream(*groups: budget.GroupState, last=(900, 0), first=(100, 0)):
    return budget.StreamState(
        name="events",
        length=1_000,
        memory=10_000_000,
        first_id=first,
        last_id=last,
        groups=list(groups),
    )


def test_the_watermark_keeps_what_the_slowest_group_still_needs():
    state = _stream(
        _group("fast", delivered=(900, 0)), _group("slow", delivered=(400, 0))
    )
    assert state.consumed_watermark() == (400, 0)


def test_an_unacked_delivery_holds_the_watermark_behind_the_read_position():
    """The reclaimer can only hand back an entry that is still in the stream.

    Trimming at the read position alone deleted exactly the messages that had
    failed once and were waiting to be tried again."""
    state = _stream(
        _group("g", delivered=(900, 0), pending=1, oldest_pending=(300, 0)),
    )
    assert state.consumed_watermark() == (300, 0)


def test_an_unreadable_position_trims_nothing():
    state = _stream(
        _group("known", delivered=(900, 0)), _group("unknown", delivered=None)
    )
    assert state.consumed_watermark() is None


def test_a_stream_nobody_reads_is_consumed_by_definition():
    assert _stream(last=(900, 0)).consumed_watermark() == (900, 0)


def test_a_trim_names_every_group_that_lost_unread_entries():
    state = _stream(
        _group("caught-up", delivered=(900, 0)),
        _group("dead", delivered=(200, 5)),
        _group("retrying", delivered=(900, 0), pending=1, oldest_pending=(300, 0)),
    )
    gaps = budget._groups_losing_entries(state, new_first=(500, 0))

    assert {(gap.group, gap.after_ms, gap.until_ms) for gap in gaps} == {
        ("dead", 200, 500),
        ("retrying", 300, 500),
    }


def test_a_group_that_had_read_up_to_what_survived_loses_nothing():
    state = _stream(
        _group("at", delivered=(500, 0)), _group("past", delivered=(700, 0))
    )
    assert budget._groups_losing_entries(state, new_first=(500, 0)) == []


def test_an_uncertain_boundary_is_reported_as_a_gap():
    """Whether 499-10 existed is unknowable after the trim. Over-reporting costs
    one replayed window the inbox deduplicates; under-reporting loses events."""
    state = _stream(_group("g", delivered=(499, 9)))
    assert len(budget._groups_losing_entries(state, new_first=(500, 0))) == 1


def test_a_group_behind_by_lag_or_by_position():
    state = _stream(last=(900, 0))
    assert state.group_is_behind(_group("lagging", delivered=(800, 0), lag=10))
    assert not state.group_is_behind(_group("current", delivered=(900, 0), lag=0))
    # Redis reports no lag once entries past the group were trimmed.
    assert state.group_is_behind(_group("trimmed-past", delivered=(1, 0), lag=None))


def test_reclaimers_do_not_count_as_the_group_reading():
    """A reclaimer's XAUTOCLAIM poll kept a dead reader's group looking active."""
    consumers = [
        {"name": "datastore-file-events-consumer", "inactive": 15_785_801},
        {"name": "datastore-file-events-consumer-reclaimer", "inactive": 1_000},
    ]
    assert budget._reader_inactive_ms(consumers) == 15_785_801


def test_a_reader_that_never_read_has_no_activity():
    assert budget._reader_inactive_ms([{"name": "c", "inactive": -1}]) is None
    assert budget._reader_inactive_ms([]) is None


def test_the_publish_cap_is_the_budget_in_entries():
    state = _stream()  # 10,000 bytes per entry
    assert budget.publish_entry_cap(state, budget_bytes=5_000_000) == 500
    assert budget.publish_entry_cap(state, budget_bytes=0) is None


@pytest.fixture
def ratios(monkeypatch):
    monkeypatch.setattr(event_transport_settings, "redis_streams_memory_fraction", 0.5)
    monkeypatch.setattr(event_transport_settings, "redis_memory_warn_ratio", 0.7)
    monkeypatch.setattr(
        event_transport_settings, "redis_streams_budget_bytes", 512 * 1024 * 1024
    )


def test_streams_get_their_share_of_maxmemory(ratios):
    memory = RedisMemory(used=300, maximum=1_000)
    assert streams_budget(memory, streams_bytes=200) == 500


def test_the_share_shrinks_when_everything_else_leaves_less_room(ratios):
    """Streams are not the only tenant: sessions, locks and job data count too.

    With 600 of 1,000 bytes used by other keys, streams may only hold what keeps
    total use under the 70% warn line."""
    memory = RedisMemory(used=800, maximum=1_000)
    assert streams_budget(memory, streams_bytes=200) == 100


def test_a_redis_with_no_maxmemory_uses_the_configured_budget(ratios):
    assert streams_budget(RedisMemory(used=10, maximum=0), 5) == 512 * 1024 * 1024
    assert streams_budget(None, 5) == 512 * 1024 * 1024


@pytest.fixture
def stall_window(monkeypatch):
    monkeypatch.setattr(event_transport_settings, "redis_stream_stall_seconds", 900)


def test_the_dev_incident_is_a_stall(stall_window):
    """Behind by 32,925, reader silent for 4.4 hours, reclaimer busy: stalled."""
    state = _stream(
        _group(
            "datastore-file-events",
            delivered=(500, 0),
            lag=32_925,
            pending=1,
            inactive_ms=15_785_801,
        )
    )
    stalled = find_stalled_groups(
        [state], {("events", "datastore-file-events")}, uptime_seconds=3_600
    )
    assert stalled == [("events", "datastore-file-events")]


def test_a_group_read_elsewhere_is_not_this_process_s_to_restart(stall_window):
    state = _stream(_group("other", delivered=(1, 0), lag=10, inactive_ms=10**9))
    assert find_stalled_groups([state], set(), uptime_seconds=3_600) == []


def test_a_caught_up_group_is_never_stalled_however_quiet(stall_window):
    state = _stream(_group("quiet", delivered=(900, 0), lag=0, inactive_ms=10**9))
    assert (
        find_stalled_groups([state], {("events", "quiet")}, uptime_seconds=3_600) == []
    )


def test_nothing_is_stalled_before_the_process_has_had_the_window(stall_window):
    """Reader timings belong to whichever process read last, not this one."""
    state = _stream(_group("g", delivered=(1, 0), lag=10, inactive_ms=10**9))
    assert find_stalled_groups([state], {("events", "g")}, uptime_seconds=60) == []


def test_replay_never_reaches_rows_it_republished_itself():
    """Re-published rows are stamped after the gap was recorded, so the window,
    capped there, can never select them again."""
    lower, upper = replay_window(
        after_ms=1_000_000, until_ms=2_000_000, recorded_ms=2_100_000
    )
    assert upper.timestamp() * 1000 == 2_100_000
    assert lower.timestamp() * 1000 == 1_000_000 - 5 * 60 * 1000


def test_a_lag_longer_than_the_stream_is_a_certain_loss():
    """Trimming leaves lag (entries added minus read) intact, so a lag longer
    than everything left means unread entries are gone -- whoever trimmed."""
    state = _stream(_group("dead", delivered=(50, 0), lag=1_500))  # length 1,000

    gaps = budget.observed_gaps(state)

    assert [(gap.group, gap.after_ms, gap.until_ms) for gap in gaps] == [
        ("dead", 50, 100)
    ]


def test_a_group_behind_but_within_the_stream_has_lost_nothing():
    """The dev incident before any trim: lag 32,925 inside 58,430 entries."""
    assert (
        budget.observed_gaps(_stream(_group("slow", delivered=(1, 0), lag=900))) == []
    )


def test_an_unacked_delivery_trimmed_away_is_a_certain_loss():
    state = _stream(
        _group("g", delivered=(900, 0), lag=0, pending=1, oldest_pending=(40, 0))
    )
    assert [gap.after_ms for gap in budget.observed_gaps(state)] == [40]
