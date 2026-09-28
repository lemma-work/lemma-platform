"""A consumer lane either keeps consuming or takes the process down with it.

What happened in development on 2026-09-28: the bulk lane crashed, the
``datastore-file-events`` reader and its reclaimer died on the fallout, and the
worker reported healthy for 4.4 hours while ``datastore.events`` filled Redis.
Each test here pins one link of that chain shut.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.concurrency.cancellation import (
    StrayCancellationError,
    as_stray_cancellation,
    is_stray_cancellation,
)
from app.core.infrastructure.events import quarantine as q
from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.jobs import lane_watchdog
from app.core.infrastructure.jobs.streaq_job_queue import SharedStreaqJobQueue
from app.core.observability import process_health

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _healthy_process():
    process_health.reset_process_health_for_tests()
    lane_watchdog.clear_shutting_down()
    yield
    process_health.reset_process_health_for_tests()
    lane_watchdog.clear_shutting_down()


class _Stops:
    """Stands in for stopping the process, which would stop pytest."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


@pytest.fixture
def stops() -> _Stops:
    return _Stops()


# -- telling a leaked cancellation from a real one -------------------------


async def test_a_cancellation_nobody_sent_is_stray():
    async def body() -> bool:
        return is_stray_cancellation(asyncio.CancelledError())

    assert await asyncio.create_task(body()) is True


async def test_a_real_cancellation_is_not_stray():
    seen: list[bool] = []
    started = asyncio.Event()

    async def body() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            seen.append(is_stray_cancellation(error))
            raise

    task = asyncio.create_task(body())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.gather(task)
    assert seen == [False]


def test_the_stray_error_is_an_ordinary_exception_that_keeps_its_cause():
    """Every retry path in the transport handles Exception and lets
    BaseException through; that is the whole reason for the conversion."""
    original = asyncio.CancelledError("Cancelled via cancel scope")
    stray = as_stray_cancellation(original)
    assert isinstance(stray, Exception)
    assert stray.__cause__ is original


# -- the broker middleware -------------------------------------------------


class _Message:
    raw_message = {"channel": "datastore.events"}
    message_id = "1790575425520-0"
    body = b"{}"


class _Counter:
    def __init__(self) -> None:
        self.count = 0

    async def eval(self, script, numkeys, key, ttl):
        self.count += 1
        return self.count


async def test_a_leaked_cancellation_fails_the_delivery_instead_of_the_reader(
    monkeypatch,
):
    """Raised as-is it ends the FastStream reader for good; as an ordinary
    failure it is retried by redelivery and counted toward quarantine."""
    monkeypatch.setattr(q, "get_redis", lambda **_: _Counter())

    async def handler(_msg):
        raise asyncio.CancelledError("Cancelled via cancel scope ... connect_tcp")

    middleware = q.StreamQuarantineMiddleware(None, context=None)
    task = asyncio.create_task(middleware.consume_scope(handler, _Message()))
    with pytest.raises(StrayCancellationError):
        await asyncio.gather(task)
    assert not task.cancelled()


async def test_a_handler_that_hangs_times_out_as_an_ordinary_failure(monkeypatch):
    monkeypatch.setattr(q, "get_redis", lambda **_: _Counter())
    monkeypatch.setattr(
        event_transport_settings, "redis_stream_handler_timeout_seconds", 0.05
    )

    async def handler(_msg):
        await asyncio.Event().wait()

    middleware = q.StreamQuarantineMiddleware(None, context=None)
    with pytest.raises(TimeoutError):
        await middleware.consume_scope(handler, _Message())


# -- the watchdog ----------------------------------------------------------


def _finished_task(*, error: BaseException | None = None) -> asyncio.Future:
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    if error is None:
        future.cancel()
    else:
        future.set_exception(error)
    return future


async def test_a_subscriber_with_no_running_task_is_dead():
    stream_sub = SimpleNamespace(
        name="datastore.events",
        group="datastore-file-events",
        consumer="datastore-file-events-consumer",
    )
    dead = SimpleNamespace(tasks=[_finished_task()], stream_sub=stream_sub)
    running = SimpleNamespace(
        tasks=[_finished_task(), asyncio.get_event_loop().create_future()],
        stream_sub=stream_sub,
    )
    never_started = SimpleNamespace(tasks=[], stream_sub=stream_sub)

    found = lane_watchdog.dead_subscribers([dead, running, never_started])

    assert [(lane.stream, lane.group, lane.error_type) for lane in found] == [
        ("datastore.events", "datastore-file-events", "CancelledError")
    ]


async def test_failing_fast_marks_the_process_unhealthy_and_stops_once(stops):
    lane = lane_watchdog.DeadLane(kind="worker_lane", name="worker-lane-bulk")

    lane_watchdog.fail_fast(lane, stop=stops)
    lane_watchdog.fail_fast(lane, stop=stops)

    assert stops.count == 1
    assert process_health.process_unhealthy_reason() is not None
    assert lane_watchdog.failed_fast()


async def test_the_watchdog_stands_down_once_shutdown_has_begun(stops):
    """An orderly SIGTERM stops readers on purpose; exiting 70 would call it a death."""
    dead_reader = SimpleNamespace(
        tasks=[_finished_task()],
        stream_sub=SimpleNamespace(name="s", group="g", consumer="c"),
    )
    lane_watchdog.mark_shutting_down()

    await asyncio.wait_for(
        lane_watchdog.lane_watchdog_loop(
            SimpleNamespace(subscribers=[dead_reader]),
            [],
            interval_seconds=0.01,
            stop=stops,
        ),
        timeout=1,
    )

    assert stops.count == 0


async def test_a_lane_that_crashes_takes_the_process_down(stops):
    async def crash() -> None:
        raise RuntimeError("Attempted to exit a cancel scope that isn't current")

    task = asyncio.create_task(crash(), name="worker-lane-bulk")
    lane_watchdog.watch_lane_task(task, stop=stops)
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert stops.count == 1


async def test_a_lane_cancelled_by_shutdown_is_not_a_death(stops):
    task = asyncio.create_task(asyncio.Event().wait(), name="worker-lane-bulk")
    lane_watchdog.watch_lane_task(task, stop=stops)
    lane_watchdog.mark_shutting_down()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert stops.count == 0


async def test_the_watchdog_loop_stops_at_the_first_dead_lane(stops):
    lane_task = asyncio.create_task(asyncio.sleep(0), name="worker-lane-bulk")
    await asyncio.gather(lane_task)

    await asyncio.wait_for(
        lane_watchdog.lane_watchdog_loop(
            SimpleNamespace(subscribers=[]),
            [lane_task],
            interval_seconds=0.01,
            stop=stops,
        ),
        timeout=1,
    )

    assert stops.count == 1


# -- the job queue's clients -----------------------------------------------


class _Client:
    """A streaq client that records which task entered and left it."""

    def __init__(
        self, entered: list[asyncio.Task[object]], exited: list[asyncio.Task[object]]
    ) -> None:
        self._entered = entered
        self._exited = exited
        self._initialized = False

    async def __aenter__(self):
        self._initialized = True
        self._entered.append(asyncio.current_task())
        return self

    async def __aexit__(self, *exc):
        self._exited.append(asyncio.current_task())
        return False


@pytest.fixture
def clients(monkeypatch):
    entered: list[asyncio.Task[object]] = []
    exited: list[asyncio.Task[object]] = []
    monkeypatch.setattr(
        "app.core.infrastructure.jobs.streaq_job_queue.create_streaq_client",
        lambda **_: _Client(entered, exited),
    )
    return SharedStreaqJobQueue(lambda: _Client(entered, exited)), entered, exited


async def test_clients_are_opened_and_closed_on_their_own_task(clients):
    """Not on the caller's. In development the bulk client was opened from a
    job under its own timeout scope; when that scope fired, the pool was
    cancelled with it and every later connection raised CancelledError."""
    queue, entered, exited = clients

    async def a_job_with_a_deadline() -> None:
        async with asyncio.timeout(10):
            await queue.connect()

    caller = asyncio.create_task(a_job_with_a_deadline())
    await asyncio.gather(caller)

    assert entered and caller not in entered
    assert len({id(task) for task in entered}) == 1

    await queue.disconnect()
    assert exited and all(task is entered[0] for task in exited)


async def test_a_lost_owner_is_replaced_on_the_next_use(clients):
    queue, entered, _ = clients
    await queue.connect()
    first_owner = entered[0]
    queue._clients.owner.cancel()  # type: ignore[union-attr]
    await asyncio.gather(queue._clients.owner, return_exceptions=True)  # type: ignore[union-attr]

    await queue.connect()

    assert entered[-1] is not first_owner
    await queue.disconnect()


async def test_every_lane_is_opened_together(clients):
    queue, _, _ = clients
    await queue.connect()
    try:
        assert queue._clients is not None
        assert queue._clients.lanes  # the bulk lane, opened up front
    finally:
        await queue.disconnect()


# -- the publish cap -------------------------------------------------------


class _Get:
    def __init__(self, value) -> None:
        self.value = value

    async def get(self, key):
        return self.value


@pytest.mark.parametrize(
    ("stored", "maxlen", "expected"),
    [
        (b"30000", 50_000, 30_000),  # the guard's cap bounds a burst
        # ...but never below half of maxlen, which is as far behind as the
        # normal path lets any group be: the cap cannot cut unread entries.
        (b"500", 50_000, 25_000),
        (b"500_000", 100, 100),  # unparseable: unchanged
        (b"900000", 100, 100),  # never raises a lower cap
        (None, 50_000, 50_000),  # no guard running: unchanged
        (b"500", None, 500),  # an uncapped stream is capped
    ],
)
async def test_the_publish_cap_only_ever_lowers_maxlen(stored, maxlen, expected):
    from app.core.infrastructure.events.message_bus import _apply_memory_cap

    assert await _apply_memory_cap(_Get(stored), "events", maxlen) == expected
