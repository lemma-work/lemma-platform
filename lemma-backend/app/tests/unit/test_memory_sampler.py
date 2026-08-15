"""Tests for the resident-memory floor tracker.

The whole value of this thing is one distinction: a process that peaks high and
gives it back is working, and a process whose *resting* level climbs is leaking.
Production shows both shapes on the same graph — a worker that spikes to 3.9 GiB
hourly and settles at 800 MiB is fine; an api pod that settles at 780, then 3,486
and stays is not. A tracker that cannot tell them apart would either alert on
every document conversion or miss the leak entirely.
"""

from __future__ import annotations

import inspect

import anyio
import pytest

from app.core.observability.memory_sampler import MemoryFloorTracker, resident_bytes

MIB = 1024 * 1024


def _tracker(warn_mib: float = 1024.0) -> MemoryFloorTracker:
    return MemoryFloorTracker(
        growth_warn_bytes=warn_mib * MIB, service_name="lemma-test"
    )


def _window(tracker: MemoryFloorTracker, *samples_mib: float, now: float = 0.0) -> None:
    for sample in samples_mib:
        tracker.observe(int(sample * MIB))
    tracker.close_window(now=now)


def test_a_spike_that_comes_back_down_is_not_growth() -> None:
    """The exact shape of a large upload or a document conversion."""
    tracker = _tracker()
    _window(tracker, 400, 3900, 410)
    _window(tracker, 405, 3800, 400)
    _window(tracker, 400, 3950, 402)
    _window(tracker, 401, 3700, 400)

    assert tracker.reports == 0


def test_a_climbing_floor_is_reported() -> None:
    """Floors of 400 -> 1500 -> 1600 -> 1700 MiB against a 1 GiB threshold."""
    tracker = _tracker(warn_mib=1024)
    _window(tracker, 400, 900)
    _window(tracker, 1500, 2000)
    _window(tracker, 1600, 2100)
    _window(tracker, 1700, 2200)

    assert tracker.reports == 1


def test_growth_must_persist_before_it_is_believed() -> None:
    """One elevated window is a busy five minutes, not a leak."""
    tracker = _tracker(warn_mib=1024)
    _window(tracker, 400, 900)
    _window(tracker, 1500, 2000)
    _window(tracker, 420, 800)

    assert tracker.reports == 0


def test_the_baseline_follows_the_floor_downward() -> None:
    """The first window reads high: imports are resident, nothing is freed yet.

    Taking that as the baseline forever would hide a leak that starts from a
    lower resting level than the process booted at.
    """
    tracker = _tracker(warn_mib=500)
    _window(tracker, 900)  # startup
    _window(tracker, 300)  # settled: this is the real baseline
    _window(tracker, 850, now=1.0)
    _window(tracker, 860, now=2.0)
    _window(tracker, 870, now=3.0)

    assert tracker.reports == 1


def test_recovery_is_reported_once_the_floor_drops_back() -> None:
    tracker = _tracker(warn_mib=1024)
    _window(tracker, 400)
    for _ in range(3):
        _window(tracker, 1600)
    assert tracker.reports == 1

    _window(tracker, 420, now=10.0)

    # Reports counts degraded transitions only; recovery resets the state so a
    # second climb is reported again rather than swallowed.
    for _ in range(3):
        _window(tracker, 1600, now=11.0)
    assert tracker.reports == 2


def test_an_empty_window_is_ignored() -> None:
    """Closing without samples must not divide by, or compare against, nothing."""
    tracker = _tracker()
    tracker.close_window()

    assert tracker.reports == 0


@pytest.mark.skipif(
    resident_bytes() is None, reason="no resident-memory source on this platform"
)
def test_resident_bytes_reads_a_plausible_number() -> None:
    rss = resident_bytes()

    assert rss is not None
    assert rss > 8 * MIB, "a Python process with pytest loaded is bigger than this"


def test_it_declines_to_run_rather_than_read_a_high_water_mark() -> None:
    """No source at all beats a monotonic one.

    `resource.getrusage().ru_maxrss` is available everywhere and looks like a
    drop-in fallback for `/proc`. It is not: it is a high-water *mark*, so it
    never goes down. The whole job here is distinguishing a floor that climbs
    from a peak that comes back, and on a monotonic input every reading is a new
    floor — the sampler would report `degraded` on any long-running process and
    never recover. A detector that cannot be wrong is not a detector, so the
    only reading it accepts is a real current one.
    """
    import app.core.observability.memory_sampler as sampler

    source = inspect.getsource(sampler)

    assert "getrusage" not in source.split('"""')[-1], (
        "ru_maxrss must not be read outside the docstring explaining why not"
    )


@pytest.mark.anyio
async def test_the_sampler_exits_when_there_is_no_reading(monkeypatch) -> None:
    """It stops, rather than looping forever on `None`."""
    import app.core.observability.memory_sampler as sampler

    monkeypatch.setattr(sampler, "resident_bytes", lambda: None)

    # Returns instead of hanging; the timeout is the assertion.
    with anyio.fail_after(5):
        await sampler.memory_sampler(service_name="lemma-api")


# --- the MCP session-task probe ---------------------------------------------


@pytest.mark.anyio
async def test_parked_task_counts_reports_the_running_loop() -> None:
    """Cheap enough to run on every degraded report."""
    from app.core.observability.memory_sampler import parked_task_counts

    total, parked = parked_task_counts()

    assert total >= 1, "the test's own task is running"
    assert parked == 0, "nothing in a unit test parks an MCP session"


def test_parked_task_counts_outside_a_loop_is_zero() -> None:
    """The sampler starts before there is anything to count; it must not raise."""
    from app.core.observability.memory_sampler import parked_task_counts

    assert parked_task_counts() == (0, 0)
