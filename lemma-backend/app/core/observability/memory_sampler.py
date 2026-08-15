"""Report a process whose resident memory floor is climbing, and name the sites.

Production api pods go from ~370 MiB to ~3.5 GiB over nine hours against a 4 GiB
limit, and the worker does a slower version of the same thing. Nothing has been
OOM-killed yet, because the deploy cadence restarts these processes before they
get there — which is exactly why it went unnoticed: every pod looks fine for its
whole short life, and the trend only exists across generations.

The distinction this makes, and the reason it does not just alert on RSS, is
between a *spike* and a *floor*. A large upload or a document conversion pushes
resident memory up and then gives it back; a leak moves the level it returns to.
So the sampler tracks the minimum seen in each window and compares floors. A
process that peaks at 3 GiB every hour and settles back to 400 MiB is doing its
job. One that settles at 400, then 900, then 1400 is not.

``tracemalloc`` is what turns "it is growing" into "it is growing here", but it
roughly doubles allocation cost, so it stays off until someone switches it on for
a process already known to be growing. The report is useless without somewhere to
put it, so the top sites go out under the same ``stack_frames`` field the loop
stall sampler uses, and the logging pipeline already declines to truncate it.
"""

from __future__ import annotations

import asyncio
import time
import tracemalloc

from app.core.config import settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

_BYTES_PER_MIB = 1024 * 1024

# How many windows of headroom before the floor is believed. A floor is only
# meaningful once the window has had time to include an idle moment; three
# windows of sustained elevation is the difference between "busy right now" and
# "did not give it back".
_WINDOWS_TO_CONFIRM = 3

# Top allocation sites to report. Enough to see a pattern, few enough to read.
_TRACEMALLOC_SITES = 12


def resident_bytes() -> int | None:
    """Current resident set size, or None where it cannot be read.

    ``/proc/self/statm`` only. There used to be a ``resource.getrusage``
    fallback for macOS, and it was worse than nothing: ``ru_maxrss`` is a
    high-water *mark*, so it never decreases. Feeding a monotonic number to
    logic whose entire job is to tell a floor that climbs from a peak that comes
    back means every reading looks like growth — the sampler would report
    ``degraded`` on any long dev session and never recover. A detector that
    cannot be wrong is not a detector.

    So the sampler simply does not run without a real current reading, which in
    practice means it runs on Linux, which is where production is.
    """
    try:
        with open("/proc/self/statm", "rb") as handle:
            fields = handle.read().split()
        return int(fields[1]) * 4096  # resident pages
    except (OSError, IndexError, ValueError):
        return None


class MemoryFloorTracker:
    """Decides whether the floor has moved, from a stream of samples."""

    def __init__(self, *, growth_warn_bytes: float, service_name: str) -> None:
        self._growth_warn_bytes = growth_warn_bytes
        self._service_name = service_name
        self._baseline: int | None = None
        self._window_floor: int | None = None
        self._elevated_windows = 0
        self._degraded = False
        self._peak_floor = 0
        self._degraded_since = 0.0
        # Incremented on every report so a test can assert rather than sleep.
        self.reports = 0

    def observe(self, rss: int, *, now: float | None = None) -> None:
        """Fold one sample into the current window."""
        if self._window_floor is None or rss < self._window_floor:
            self._window_floor = rss
        if self._baseline is None:
            self._baseline = rss

    def close_window(self, *, now: float | None = None) -> None:
        """End the window and judge its floor against the baseline."""
        floor = self._window_floor
        self._window_floor = None
        if floor is None or self._baseline is None:
            return
        clock = time.monotonic() if now is None else now

        # A floor below the baseline is the real baseline: the first window
        # after startup is measured while imports are still resident and before
        # anything has been freed, so it reads high.
        if floor < self._baseline:
            self._baseline = floor

        growth = floor - self._baseline
        if growth < self._growth_warn_bytes:
            if self._degraded:
                self._recover(clock)
            else:
                self._elevated_windows = 0
            return

        self._elevated_windows += 1
        self._peak_floor = max(self._peak_floor, floor)
        if self._degraded or self._elevated_windows < _WINDOWS_TO_CONFIRM:
            return

        self._degraded = True
        self._degraded_since = clock
        self.reports += 1
        total_tasks, parked_tasks = parked_task_counts()
        logger.warning(
            "runtime.memory.degraded",
            service=self._service_name,
            rss_mib=round(floor / _BYTES_PER_MIB, 1),
            baseline_mib=round(self._baseline / _BYTES_PER_MIB, 1),
            growth_mib=round(growth / _BYTES_PER_MIB, 1),
            threshold_mib=round(self._growth_warn_bytes / _BYTES_PER_MIB, 1),
            total_tasks=total_tasks,
            parked_mcp_tasks=parked_tasks,
            stack_frames=top_allocation_sites(),
        )

    def _recover(self, clock: float) -> None:
        logger.info(
            "runtime.memory.recovered",
            service=self._service_name,
            peak_rss_mib=round(self._peak_floor / _BYTES_PER_MIB, 1),
            degraded_duration_ms=round((clock - self._degraded_since) * 1000, 1),
        )
        self._degraded = False
        self._elevated_windows = 0
        self._peak_floor = 0
        self._degraded_since = 0.0


def parked_task_counts() -> tuple[int, int]:
    """(total tasks, tasks parked in a stateless MCP session).

    A cheap, specific test for the one unbounded retention found in the api
    process. ``mcp``'s streamable-HTTP manager starts a per-request session task
    in a *process-lifetime* task group and then does::

        await self._task_group.start(run_stateless_server)
        await http_transport.handle_request(scope, receive, send)
        await http_transport.terminate()

    with no ``try``/``finally``. ``handle_request`` raising — and a client
    disconnecting raises ``CancelledError``, which is a ``BaseException`` the
    inner ``except Exception`` does not catch — skips ``terminate()``, and the
    session task stays parked forever holding its streams. Checked against 1.28,
    1.29 and 2.0: all three have it, so upgrading is not the answer.

    Reported rather than fixed because the fix belongs upstream, and because
    nobody has yet confirmed it actually fires in production. A count that
    climbs with traffic settles that without attaching to a live process.
    """
    try:
        tasks = asyncio.all_tasks()
    except RuntimeError:  # pragma: no cover - no running loop
        return 0, 0
    parked = sum(1 for task in tasks if "run_stateless_server" in repr(task))
    return len(tasks), parked


def top_allocation_sites() -> str | None:
    """The largest tracemalloc allocation sites, or None when it is not tracing."""
    if not tracemalloc.is_tracing():
        return None
    snapshot = tracemalloc.take_snapshot()
    lines = []
    for stat in snapshot.statistics("lineno")[:_TRACEMALLOC_SITES]:
        frame = stat.traceback[0]
        lines.append(
            f"  {frame.filename}:{frame.lineno}  "
            f"{stat.size / _BYTES_PER_MIB:.1f} MiB in {stat.count} blocks"
        )
    return "\n".join(lines) or None


async def memory_sampler(*, service_name: str) -> None:
    """Sample resident memory forever. Cancelled with the lifespan that starts it."""
    interval = max(1.0, settings.memory_sampler_interval_seconds)
    if resident_bytes() is None:
        logger.debug("runtime.memory.unavailable.diagnostic", service=service_name)
        return

    if settings.memory_sampler_tracemalloc_enabled and not tracemalloc.is_tracing():
        # Depth 1: the report groups by allocation line, so deeper frames cost
        # memory to record and are never read back out.
        tracemalloc.start(1)

    tracker = MemoryFloorTracker(
        growth_warn_bytes=settings.memory_growth_warn_mib * _BYTES_PER_MIB,
        service_name=service_name,
    )
    # Windows are a fixed number of samples so the floor is taken over a span
    # long enough to contain an idle moment, rather than over whatever the
    # sampler happened to catch.
    samples_per_window = max(1, int(300 / interval))
    seen = 0
    while True:
        await asyncio.sleep(interval)
        rss = resident_bytes()
        if rss is None:
            continue
        tracker.observe(rss)
        seen += 1
        if seen >= samples_per_window:
            tracker.close_window()
            seen = 0
