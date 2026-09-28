"""Stop the worker when a lane has stopped, instead of letting it look healthy.

A worker process runs two kinds of long-lived consumer: FastStream stream
subscribers (one reader task per subscriber) and streaq lanes (one worker task
per lane). Either can end while the process carries on:

* FastStream restarts a reader that raised -- unless the task ended
  *cancelled*, and a ``CancelledError`` leaked in from a client bound to some
  other task does exactly that. The subscriber still reports ``running``.
* The secondary streaq lanes are plain background tasks. When one crashed, its
  traceback was logged once and nothing ever ran it again.

Both happened together in development: the bulk lane crashed at 06:03, the
``datastore-file-events`` reader and its reclaimer died on the fallout within a
minute, and the worker went on reporting healthy for more than four hours
while ``datastore.events`` grew until Redis ran out of memory.

The repair is not to restart the task in place. What killed it -- a poisoned
connection pool, a corrupted cancel-scope stack -- is process state, and a task
restarted into the same process meets it again. So this watchdog's only move is
to take the process down cleanly and let whatever supervises it (Kubernetes, the
compose restart policy, Desktop's host process manager) start a fresh one:

1. report which lane died and why, once, at ERROR;
2. mark the process unhealthy, which stops the liveness heartbeat and the
   Redis ``alive`` key -- so even with no probe configured, readiness stops
   vouching for it;
3. deliver SIGTERM to itself, so the normal graceful shutdown runs;
4. and, as a last resort, hard-exit if that shutdown does not finish.
"""

from __future__ import annotations

import asyncio
import os
import signal
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.infrastructure.events.stream_guard import stream_guard_loop
from app.core.log.log import get_logger
from app.core.observability.process_health import (
    mark_process_unhealthy,
    process_unhealthy_reason,
)
from app.core.request_context import create_background_task

logger = get_logger(__name__)

#: How often task liveness is checked. Cheap -- it reads task objects, not Redis.
CHECK_INTERVAL_SECONDS = 10.0
#: Past the graceful shutdown, how long before the process is ended regardless.
#: A worker that has decided it is broken and then hangs in teardown is the one
#: outcome worse than the one this module exists to fix.
_HARD_EXIT_MARGIN_SECONDS = 60.0
#: Exit status for a process this watchdog ended. Distinct from 1 (startup
#: failure) so a crash loop can be told apart in a supervisor's history.
FAIL_FAST_EXIT_CODE = 70


@dataclass(frozen=True, slots=True)
class DeadLane:
    """A consumer that has stopped for good."""

    kind: str
    name: str
    stream: str = ""
    group: str = ""
    error_type: str = ""


def _task_error_type(task: asyncio.Task[None]) -> str:
    if task.cancelled():
        return "CancelledError"
    error = task.exception()
    return type(error).__name__ if error is not None else ""


def dead_subscribers(subscribers: Iterable[object]) -> list[DeadLane]:
    """Stream subscribers whose reader task has ended.

    A subscriber's ``tasks`` keeps every task it ever started, including the
    finished ones FastStream's supervisor replaced, so the test is that *none*
    is still running -- not that one has finished. A subscriber that was never
    started has no tasks and is not this watchdog's business.
    """
    dead: list[DeadLane] = []
    for subscriber in subscribers:
        tasks = list(getattr(subscriber, "tasks", None) or ())
        if not tasks or any(not task.done() for task in tasks):
            continue
        stream_sub = getattr(subscriber, "stream_sub", None)
        dead.append(
            DeadLane(
                kind="stream_subscriber",
                name=str(getattr(stream_sub, "consumer", "") or ""),
                stream=str(getattr(stream_sub, "name", "") or ""),
                group=str(getattr(stream_sub, "group", "") or ""),
                error_type=_task_error_type(tasks[-1]),
            )
        )
    return dead


def dead_lane_tasks(tasks: Iterable[asyncio.Task[None]]) -> list[DeadLane]:
    """Streaq lane tasks that have ended while the process was meant to be running."""
    return [
        DeadLane(
            kind="worker_lane",
            name=task.get_name(),
            error_type=_task_error_type(task),
        )
        for task in tasks
        if task.done()
    ]


def stop_process() -> None:
    """Begin a graceful shutdown of this process, with a hard deadline behind it."""
    _arm_hard_exit()
    # SIGTERM rather than cancelling tasks from here: the primary lane (or, in
    # the single-process build, uvicorn) already owns graceful shutdown on that
    # signal, and a second shutdown path is how teardown ordering bugs start.
    signal.raise_signal(signal.SIGTERM)


def fail_fast(
    dead: DeadLane,
    *,
    reason: str = "lane_dead",
    stop: Callable[[], None] = stop_process,
) -> None:
    """Take the process down so a supervisor starts a clean one.

    Idempotent: only the first caller reports and stops. Safe to call from a
    task done-callback, which is where a crashed lane is noticed first.
    """
    if not mark_process_unhealthy(f"{reason}:{dead.kind}:{dead.name}"):
        return
    logger.error(
        "worker.lane.dead",
        reason=reason,
        lane_kind=dead.kind,
        lane_name=dead.name,
        stream_name=dead.stream,
        group=dead.group,
        error_type=dead.error_type,
    )
    stop()


def _arm_hard_exit() -> None:
    deadline = settings.worker_shutdown_grace_period_seconds + _HARD_EXIT_MARGIN_SECONDS

    def _exit() -> None:
        # Not a log line: the logging pipeline may be the thing that is stuck.
        os._exit(FAIL_FAST_EXIT_CODE)

    timer = threading.Timer(deadline, _exit)
    timer.daemon = True
    timer.start()


def failed_fast() -> bool:
    """Whether this process was taken down by :func:`fail_fast`."""
    reason = process_unhealthy_reason()
    return reason is not None


async def lane_watchdog_loop(
    subscribers_source: object,
    lane_tasks: list[asyncio.Task[None]],
    *,
    interval_seconds: float = CHECK_INTERVAL_SECONDS,
    stop: Callable[[], None] = stop_process,
) -> None:
    """Check every reader and lane task, and fail fast on the first dead one.

    ``subscribers_source`` is anything with a ``subscribers`` attribute (the
    FastStream broker); read on each pass because routers attach after start.
    ``lane_tasks`` is the live list the runtime appends to.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        if _shutting_down.is_set():
            # Readers and lanes are being stopped on purpose; none of what
            # follows is a death.
            return
        dead = [
            *dead_subscribers(getattr(subscribers_source, "subscribers", ())),
            *dead_lane_tasks(lane_tasks),
        ]
        if dead:
            fail_fast(dead[0], stop=stop)
            return


def watch_lane_task(
    task: asyncio.Task[None], *, stop: Callable[[], None] = stop_process
) -> None:
    """Fail fast the moment a lane task ends, without waiting for the next pass.

    A lane cancelled by the runtime's own shutdown ends cancelled while the
    process is already stopping; that is the one ending that is not a death.
    """

    def _on_done(done: asyncio.Task[None]) -> None:
        if done.cancelled() and _shutting_down.is_set():
            return
        fail_fast(dead_lane_tasks([done])[0], stop=stop)

    task.add_done_callback(_on_done)


#: Set when the runtime begins an orderly shutdown, so lanes it cancels are not
#: mistaken for lanes that died.
_shutting_down = threading.Event()


def mark_shutting_down() -> None:
    _shutting_down.set()


def clear_shutting_down() -> None:
    """Arm the watchdog for a new run of the lanes (a test may run several)."""
    _shutting_down.clear()


def watch_lanes(tasks: Iterable[asyncio.Task[None]]) -> None:
    """Watch every lane task; see :func:`watch_lane_task`.

    A lane that ends on its own has died: it used to be logged once as
    ``background_task.failed`` and never run again, leaving its whole queue
    unconsumed behind a process that still looked healthy.
    """
    clear_shutting_down()
    for task in tasks:
        watch_lane_task(task)


async def stop_lanes(
    tasks: list[asyncio.Task[None]], *, timeout_seconds: float
) -> None:
    """Cancel the lane tasks as shutdown, not death, and wait briefly for them."""
    # First, so the watchdog reads the cancellations below as shutdown rather
    # than as lanes dying.
    mark_shutting_down()
    running = [task for task in tasks if not task.done()]
    tasks.clear()
    if not running:
        return
    for task in running:
        task.cancel()
    _, pending = await asyncio.wait(running, timeout=timeout_seconds)
    if pending:
        # Named, because "the worker had to be killed" is not a diagnosis.
        logger.warning(
            "infrastructure.streaq_runtime.lane_shutdown_timed_out.degraded",
            lanes=",".join(sorted(task.get_name() for task in pending)),
            timeout_seconds=timeout_seconds,
        )


def start_worker_guards(
    broker: object,
    lane_tasks: list[asyncio.Task[None]],
    message_bus: object,
    session_maker: Callable[[], AsyncSession],
) -> tuple[asyncio.Task[None], ...]:
    """Start the tasks that keep a worker honest about whether it is working.

    The lane watchdog takes the process down when a reader or lane has ended.
    The stream guard bounds Redis memory whatever the consumers do and reports
    a group whose reader has stopped reading -- and is itself watched, so a
    guard that crashes restarts the worker rather than quietly stop guarding.

    Clears the shutdown mark first. Every worker startup passes through here,
    including a single-lane one that never calls :func:`watch_lanes`, so a
    process that runs the lifespan again (a test, the embedded app restarting
    its worker) does not start with the watchdog already stood down.
    """
    clear_shutting_down()
    watchdog = create_background_task(
        lane_watchdog_loop(broker, lane_tasks), name="worker-lane-watchdog"
    )
    guard = create_background_task(
        stream_guard_loop(
            message_bus,
            session_maker=session_maker,
            on_stalled_group=_fail_fast_on_stalled_group,
        ),
        name="redis-stream-guard",
    )
    watch_lane_task(guard)
    return watchdog, guard


def _fail_fast_on_stalled_group(stream: str, group: str) -> None:
    """A group this process consumes has stopped reading: restart the process."""
    fail_fast(
        DeadLane(kind="stream_group", name=group, stream=stream, group=group),
        reason="group_stalled",
    )
