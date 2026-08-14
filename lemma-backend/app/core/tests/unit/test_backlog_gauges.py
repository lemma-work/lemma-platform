"""Backlog gauges: what is waiting, not how fast it is moving."""

from __future__ import annotations

import asyncio

import pytest

from app.core.observability import backlog_gauges


@pytest.fixture(autouse=True)
def _clean_snapshot():
    def reset() -> None:
        backlog_gauges._backlog.forget_queues()
        backlog_gauges._backlog.forget_event_tables()

    reset()
    yield
    reset()


def _observations(callback):
    return list(callback(None))


def test_a_gauge_reports_nothing_until_something_has_been_sampled() -> None:
    """A level that was never read is not the same as a level of zero.

    Reporting zero here would say "no backlog" during the window before the
    first sample, which is exactly the reassuring-but-false reading these
    gauges exist to prevent.
    """
    assert _observations(backlog_gauges._observe_outbox_pending) == []
    assert _observations(backlog_gauges._observe_inbox_pending) == []
    assert _observations(backlog_gauges._observe_queue_depth) == []


def test_queue_depth_is_reported_per_lane() -> None:
    backlog_gauges._backlog.queue_depth = {"interactive": 3, "bulk": 41}

    observed = {
        obs.attributes["lane"]: obs.value
        for obs in _observations(backlog_gauges._observe_queue_depth)
    }

    assert observed == {"interactive": 3, "bulk": 41}


@pytest.mark.asyncio
async def test_a_failed_sample_drops_the_reading_rather_than_repeating_it(
    monkeypatch,
) -> None:
    """A stale gauge is worse than an absent one.

    These report a level, not a rate, so a reading left in place after the
    sampler starts failing flatlines at whatever was true last -- and a flat
    line reads as a healthy steady state, not as an outage.
    """
    backlog_gauges._backlog.queue_depth = {"interactive": 5}
    backlog_gauges._backlog.outbox_pending = 900

    async def _explode(*args, **kwargs):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(backlog_gauges, "_sample_queue_depth", _explode)
    monkeypatch.setattr(backlog_gauges, "_sample_event_tables", _explode)

    task = asyncio.create_task(
        backlog_gauges.backlog_gauge_loop(object(), interval_seconds=30)
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert _observations(backlog_gauges._observe_queue_depth) == []
    assert _observations(backlog_gauges._observe_outbox_pending) == []


@pytest.mark.asyncio
async def test_a_zero_interval_disables_the_sampler() -> None:
    await asyncio.wait_for(
        backlog_gauges.backlog_gauge_loop(object(), interval_seconds=0),
        timeout=1,
    )


@pytest.mark.asyncio
async def test_cancellation_is_never_swallowed_by_the_error_handling(
    monkeypatch,
) -> None:
    """Shutdown has to win over the broad catch around each sample."""

    async def _cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(backlog_gauges, "_sample_queue_depth", _cancelled)

    task = asyncio.create_task(
        backlog_gauges.backlog_gauge_loop(object(), interval_seconds=30)
    )
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)


def test_the_labels_these_gauges_use_survive_the_export_boundary() -> None:
    from app.core.observability.span_sanitizer import METRIC_ATTRIBUTE_KEYS

    assert "lane" in METRIC_ATTRIBUTE_KEYS
