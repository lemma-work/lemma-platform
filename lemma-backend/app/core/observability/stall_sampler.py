"""Name what is blocking the event loop, while it is still blocking it.

The lag watchdog can tell you the loop stalled and by how much. It cannot tell
you *what* stalled it, and by the time it measures the lag the offending call
has already returned — the loop is running again, so there is nothing left to
look at. That gap is why a blocking call can sit in production for months: every
report says "loop lag 737ms" and the next question has no answer.

This samples from a plain OS thread instead. The loop publishes a tick each
time it gets a turn; the thread watches that tick go stale and, once it is
staler than the threshold, grabs the loop thread's stack with
``sys._current_frames()``. Because the thread is not on the loop, it runs
*during* the stall, and the frame it captures is the blocking call itself.

Deliberately not a profiler: one stack per incident, a cooldown between
incidents, and a thread that sleeps the rest of the time. The cost when nothing
is wrong is a monotonic clock read per loop turn.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import traceback
from collections.abc import Iterable

from app.core.log.log import get_logger

logger = get_logger(__name__)

# Frames belonging to the machinery that does the watching, or to the loop's own
# idle wait. Trimmed so the reported culprit is application code rather than
# `epoll_wait` and three layers of asyncio.
_UNINTERESTING = (
    "asyncio/base_events.py",
    "asyncio/selector_events.py",
    "asyncio/events.py",
    "selectors.py",
    "app/core/observability/stall_sampler.py",
)


# Clipped from the *front* if it overruns: the innermost frames name what
# blocked, and the outermost are scaffolding. Kept under the logging pipeline's
# own `stack_frames` allowance so the clip happens here, where the end that
# matters is known, rather than there, where it is not.
_MAX_STACK_CHARS = 7_000


# A thread parked here is waiting to be given work, not holding the GIL doing
# it. Reporting every idle pool thread on each stall would bury the one that
# matters.
_PARKED = (
    "threading.py",
    "queue.py",
    "concurrent/futures/thread.py",
    "socketserver.py",
)

#: Enough to name the culprit without turning one incident into a thread dump.
_MAX_REPORTED_THREADS = 4


def _is_interesting(frame_summary: traceback.FrameSummary) -> bool:
    return not any(part in frame_summary.filename for part in _UNINTERESTING)


def _is_doing_work(frames: Iterable[traceback.FrameSummary]) -> bool:
    """True when a non-loop thread is running code rather than waiting for some.

    Judged on the innermost frame: a worker blocked in ``queue.get`` is idle no
    matter how much application code sits below it on the stack, while a worker
    inside application code is exactly what a GIL-contention stall looks like.
    """
    frames = list(frames)
    if not frames:
        return False
    innermost = frames[-1]
    if any(part in innermost.filename for part in _PARKED):
        return False
    return any(_is_interesting(frame) for frame in frames)


def format_stall_stack(frames: Iterable[traceback.FrameSummary]) -> str:
    """The deepest application frames of a stalled loop, innermost last.

    Trimmed to the tail because the head is always the same lifespan/task-runner
    scaffolding, and an untrimmed 60-frame dump per incident is how a useful
    signal becomes something people filter out.

    The trim has one exception, and it is the case that made this report useless
    in production. When the *innermost* frame is itself uninteresting, the loop
    is sitting in the selector rather than in any code of ours — which is what
    GIL contention from another thread, or the process not being scheduled at
    all, looks like from here. Filtering that away left only
    ``runpy``/``asyncio.run``/``run_until_complete`` scaffolding, so every single
    report read the same and named nothing. Keeping the raw tail in that case
    turns a blank answer into a real one: *the loop was parked, not blocked*.
    """

    frames = list(frames)
    if not frames:
        return ""
    interesting = [frame for frame in frames if _is_interesting(frame)]
    if not interesting or interesting[-1] is not frames[-1]:
        selected = frames[-12:]
    else:
        selected = interesting[-12:]
    return "".join(traceback.format_list(selected)).rstrip()[-_MAX_STACK_CHARS:]


class LoopStallSampler:
    """Watches one event loop from a thread and reports what blocks it."""

    def __init__(
        self,
        *,
        stall_seconds: float,
        service_name: str,
        cooldown_seconds: float = 60.0,
        poll_seconds: float = 0.05,
    ) -> None:
        self._stall_seconds = stall_seconds
        self._service_name = service_name
        self._cooldown_seconds = cooldown_seconds
        self._poll_seconds = poll_seconds
        self._tick = time.monotonic()
        self._loop_thread_id: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_report = -1e9
        # Incremented on every report so a test can wait for one rather than
        # sleeping and hoping.
        self.reports = 0

    def note_loop_alive(self) -> None:
        """Called from the loop each turn: 'I am still being scheduled'."""
        self._tick = time.monotonic()

    def start(self) -> None:
        self._loop_thread_id = threading.get_ident()
        self._tick = time.monotonic()
        self._stop.clear()
        thread = threading.Thread(
            target=self._watch,
            name="loop-stall-sampler",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._thread = None

    def _watch(self) -> None:
        while not self._stop.wait(self._poll_seconds):
            stalled_for = time.monotonic() - self._tick
            if stalled_for < self._stall_seconds:
                continue
            now = time.monotonic()
            if now - self._last_report < self._cooldown_seconds:
                continue
            self._last_report = now
            self._report(stalled_for)

    def _report(self, stalled_for: float) -> None:
        stack, other_threads = self._capture()
        if stack is None:
            return
        self.reports += 1
        logger.warning(
            "runtime.loop_stall.degraded",
            service=self._service_name,
            stalled_ms=round(stalled_for * 1000, 1),
            threshold_ms=round(self._stall_seconds * 1000, 1),
            # Not `stack`: structlog reserves that key and drops the value.
            stack_frames=stack,
            other_thread_frames=other_threads,
        )

    def _capture(self) -> tuple[str | None, str | None]:
        """The loop thread's stack, plus any other thread that is doing work.

        Sampling only the loop thread cannot explain a stall it did not cause.
        A CPU-bound call on a ``run_blocking`` offload thread holds the GIL; the
        loop is then runnable but unable to proceed, and its own stack shows it
        parked in the selector — true, and useless on its own. The thread
        actually responsible was never looked at.
        """
        thread_id = self._loop_thread_id
        if thread_id is None:
            return None, None
        frames = sys._current_frames()  # noqa: SLF001 — the only way in
        loop_frame = frames.get(thread_id)
        if loop_frame is None:
            return None, None

        loop_stack = format_stall_stack(traceback.extract_stack(loop_frame))
        names = {thread.ident: thread.name for thread in threading.enumerate()}
        sampler_id = threading.get_ident()

        others: list[str] = []
        for other_id, frame in frames.items():
            if other_id in (thread_id, sampler_id):
                continue
            summaries = traceback.extract_stack(frame)
            if not _is_doing_work(summaries):
                continue
            label = names.get(other_id) or str(other_id)
            others.append(
                f"--- thread {label} ---\n" + format_stall_stack(summaries)
            )
            if len(others) >= _MAX_REPORTED_THREADS:
                break

        return loop_stack, ("\n".join(others) or None)


_sampler: LoopStallSampler | None = None


def get_loop_stall_sampler() -> LoopStallSampler | None:
    return _sampler


def start_loop_stall_sampler(
    *, stall_seconds: float, service_name: str
) -> LoopStallSampler:
    """Install the process-wide sampler. Call from the loop it should watch."""
    global _sampler
    stop_loop_stall_sampler()
    sampler = LoopStallSampler(
        stall_seconds=stall_seconds, service_name=service_name
    )
    sampler.start()
    _sampler = sampler
    return sampler


def stop_loop_stall_sampler() -> None:
    global _sampler
    if _sampler is not None:
        _sampler.stop()
        _sampler = None


async def keep_loop_tick_fresh(sampler: LoopStallSampler, interval: float) -> None:
    """Publish the loop's liveness for the sampler to watch.

    Its own sleep is the measurement: a loop that is being scheduled comes back
    from `sleep(0)` promptly, and one that is blocked does not come back at all
    — which is exactly the condition the sampler is looking for.
    """

    while True:
        sampler.note_loop_alive()
        await asyncio.sleep(interval)
