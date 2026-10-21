"""The after-commit analytics sink: bounded, batched, never in a commit's way."""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy.exc import OperationalError

from app.core.analytics import domain_events
from app.core.analytics.domain_events import DomainEventSink, projection
from app.core.domain.events import DomainEvent


class _Probe(DomainEvent):
    event_type: str = "test.sink.probe"
    n: int = 0


class _Unwatched(DomainEvent):
    event_type: str = "test.sink.unwatched"


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    monkeypatch.setattr(domain_events, "_projections", {})
    got: list[int] = []

    @projection(_Probe.get_event_type())
    async def record(event: dict) -> None:
        got.append(int(event["n"]))

    return got


async def test_events_nobody_projects_are_not_queued(seen: list[int]) -> None:
    sink = DomainEventSink()
    sink.offer([_Unwatched()])
    await sink.flush()
    assert seen == []


async def test_a_full_queue_drops_the_oldest_and_counts_it(seen: list[int]) -> None:
    sink = DomainEventSink(max_queued=3, interval_seconds=60)
    sink.offer([_Probe(n=i) for i in range(5)])
    assert sink.dropped == 2
    await sink.flush()
    assert seen == [2, 3, 4]
    await sink.aclose()


async def test_the_flusher_projects_on_its_own(seen: list[int]) -> None:
    sink = DomainEventSink(batch=2, interval_seconds=0.05)
    sink.offer([_Probe(n=1)])
    for _ in range(100):
        if seen:
            break
        await asyncio.sleep(0.01)
    assert seen == [1]
    await sink.aclose()


async def test_a_failing_projection_does_not_stop_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(domain_events, "_projections", {})
    got: list[int] = []

    @projection(_Probe.get_event_type())
    async def boom(event: dict) -> None:
        if event["n"] == 1:
            # What a projection's scope read raises when the database is down.
            raise OperationalError("SELECT 1", {}, OSError("connection refused"))
        got.append(int(event["n"]))

    sink = DomainEventSink(interval_seconds=60)
    sink.offer([_Probe(n=1), _Probe(n=2)])
    await sink.flush()
    assert got == [2]
    await sink.aclose()


async def test_ten_thousand_events_never_hold_up_the_offering_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A saturated sink (slow projection, full queue) costs the commit path ~nothing."""
    monkeypatch.setattr(domain_events, "_projections", {})

    @projection(_Probe.get_event_type())
    async def slow(event: dict) -> None:
        await asyncio.sleep(0.01)

    sink = DomainEventSink(max_queued=1_000, batch=200, interval_seconds=0.01)
    burst = [_Probe(n=i) for i in range(10_000)]
    worst = 0.0
    for start in range(0, len(burst), 10):  # one "commit" of 10 events
        began = time.perf_counter()
        sink.offer(burst[start : start + 10])
        worst = max(worst, time.perf_counter() - began)
    assert sink.dropped >= 8_000
    assert worst < 0.01, f"an offer took {worst * 1000:.1f} ms"
    task = sink._task  # noqa: SLF001 - the flusher must still be alive
    assert task is not None and not task.done()
    sink._queue.clear()  # noqa: SLF001 - do not wait out 1000 slow projections
    await sink.aclose()


async def test_close_projects_what_is_still_queued(seen: list[int]) -> None:
    sink = DomainEventSink(interval_seconds=60)
    sink.offer([_Probe(n=7), _Probe(n=8)])
    await sink.aclose()
    assert seen == [7, 8]
