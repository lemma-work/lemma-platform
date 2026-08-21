"""Fail a test that holds a pooled connection across non-database work.

Opt-in, by naming the fixture. Deliberately not autouse:

* ``session-scope-baseline.json`` still lists known violations, so an autouse
  gate would be red on day one and would grow an opt-out flag within a week —
  at which point the flag becomes the default and the gate means nothing.
* The e2e harness holds sessions across blocking fixture work on purpose, which
  is exactly why ``scripts/check_session_scope.py`` excludes ``tests`` and
  ``test_support``. A runtime gate should not re-introduce what the static gate
  deliberately leaves out.

Reporting happens at check-in, which fires synchronously inside the test's own
execution — so there is no polling, no interval, and nothing to flake.

Usage::

    async def test_import_does_not_hold_a_connection(strict_connection_scope, ...):
        ...
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.observability import connection_scope
from app.core.observability.connection_scope import ConnectionHold, ConnectionScopeMonitor

# Tighter than the production default: a test should not hold a connection for
# a fifth of a second, and a tight threshold is what makes the gate useful on a
# fast machine. Loose enough to survive a loaded CI runner.
STRICT_IDLE_HOLD_SECONDS = 0.2


@pytest.fixture
def strict_connection_scope() -> Iterator[ConnectionScopeMonitor]:
    """Fail this test if it holds a pooled connection while not querying.

    Works under ``NullPool`` — the testing default — because checkout and
    check-in fire there exactly as they do on a real pool. No engine juggling
    is needed, so do not "fix" this by flipping ``settings.environment``.
    """
    from app.core.infrastructure.db.session import get_engine

    monitor = connection_scope.start_connection_scope_monitor(
        idle_hold_seconds=STRICT_IDLE_HOLD_SECONDS, strict=True
    )
    # The engines are usually built before this fixture runs, so attach here
    # rather than relying on construction-time wiring.
    monitor.attach(get_engine())
    try:
        from app.modules.datastore.infrastructure.session import get_datastore_engine

        monitor.attach(get_datastore_engine())
    except Exception:  # pragma: no cover - datastore is optional in some suites
        pass

    try:
        yield monitor
    finally:
        connection_scope.stop_connection_scope_monitor()

    if monitor.violations:
        report = "\n\n".join(hold.render() for hold in monitor.violations)
        pytest.fail(
            f"{len(monitor.violations)} pooled connection(s) held across "
            f"non-database work:\n\n{report}",
            pytrace=False,
        )


# --- Sweep mode -------------------------------------------------------------
#
# Turning the strict fixture on test by test proves the paths it names. It says
# nothing about the paths nobody thought to name — which, given the audit found
# ~103 of those, is the more interesting question.
#
# ``LEMMA_CONNECTION_SCOPE_REPORT=1`` runs the monitor for the whole pytest
# session in report-only mode and writes what it saw to
# ``LEMMA_CONNECTION_SCOPE_REPORT_PATH`` (default /tmp/lemma_connection_holds.txt).
# Nothing fails; the point is to discover, which is what makes it safe to run
# over a suite that is not clean yet.

SWEEP_ENV = "LEMMA_CONNECTION_SCOPE_REPORT"
SWEEP_PATH_ENV = "LEMMA_CONNECTION_SCOPE_REPORT_PATH"
DEFAULT_SWEEP_PATH = "/tmp/lemma_connection_holds.txt"


def sweep_enabled() -> bool:
    import os

    return os.environ.get(SWEEP_ENV, "").lower() in {"1", "true", "yes"}


def start_sweep() -> ConnectionScopeMonitor:
    """Watch every connection this pytest session checks out."""
    monitor = connection_scope.start_connection_scope_monitor(
        idle_hold_seconds=STRICT_IDLE_HOLD_SECONDS, strict=True
    )
    # No cooldown in a sweep: the whole point is the complete list.
    monitor._cooldown_seconds = 0.0  # noqa: SLF001
    return monitor


def write_sweep_report() -> str | None:
    """Write what the sweep found, newest last. Returns the path, or None."""
    import os
    from collections import Counter

    monitor = connection_scope.get_connection_scope_monitor()
    connection_scope.stop_connection_scope_monitor()
    if monitor is None or not monitor.violations:
        return None

    path = os.environ.get(SWEEP_PATH_ENV, DEFAULT_SWEEP_PATH)
    # Group by the innermost application frame: one bug produces many holds,
    # and a list of 400 individually-formatted holds is not a work list.
    by_site: Counter[str] = Counter()
    worst: dict[str, ConnectionHold] = {}
    for hold in monitor.violations:
        site = hold.stack.strip().splitlines()[-2].strip() if hold.stack else "<unknown>"
        by_site[site] += 1
        if site not in worst or hold.gap_seconds > worst[site].gap_seconds:
            worst[site] = hold

    lines = [
        f"{len(monitor.violations)} connection hold(s) across {len(by_site)} site(s)",
        "",
    ]
    for site, count in by_site.most_common():
        hold = worst[site]
        lines.append(f"### {count}x  worst gap {hold.gap_seconds * 1000:.0f}ms  {site}")
        lines.append(hold.render())
        lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return path


# --- Scoped guard -----------------------------------------------------------
#
# `strict_connection_scope` arms for the whole test, which is why no request-path
# test uses it: e2e setup creates orgs, pods, agents and workflows through the
# same API, and the harness holds sessions across blocking fixture work on
# purpose. Arming across all of that reports the scaffolding, not the flow.
#
# So the guard is a block instead. Set the world up unguarded, then arm around
# the one call under test:
#
#     async def test_the_import_holds_nothing(authenticated_client, scoped_connection_guard):
#         pod = await _create_pod(authenticated_client, org["id"])   # not guarded
#         async with scoped_connection_guard():
#             response = await authenticated_client.post(f"/pods/{pod}/bundle:import", ...)
#         assert response.status_code == 202
#
# What it protects is narrow and worth stating: between checkout and check-in,
# the connection must not be idle while something slow happens -- an HTTP call,
# an object-storage read, a Redis fan-out, a sleep. It says nothing about how
# many queries ran or how long they took.


# How often the loop watchdog wants to be woken while the guard is armed.
#
# Fine enough to attribute a stall against a 200ms threshold, coarse enough that
# the task itself is not the load.
LOOP_LAG_INTERVAL_SECONDS = 0.02


async def _watch_loop_lag(lag: list[float]) -> None:
    """Record how late this task's wakeups were, worst first.

    The monitor measures a hold in wall-clock, which is the right unit for the
    thing it protects — a connection is checked out for as long as the clock
    says, whatever the process was doing. It is the wrong unit for *blame*.
    A CI runner that deschedules the whole process for half a second produces a
    half-second gap on whichever connection happened to be open, and the path
    that opened it did nothing wrong.

    This is how the two are told apart. An `await` that hands control back —
    an HTTP call, a Redis round trip, `asyncio.sleep` — leaves this task on
    time, so its gap is real and stays reported. A stalled process makes this
    task late by the same amount it made everything else late, and that much of
    the gap is subtracted before the threshold is applied.

    What it deliberately does not catch: a *blocking* call inside a session,
    which stalls the loop and so would be excused here. That is a different
    defect with its own gate — `make lint-async` — and asking one detector to
    cover both is what would make this one unable to say anything precisely.
    """
    import asyncio
    import time

    while True:
        before = time.monotonic()
        await asyncio.sleep(LOOP_LAG_INTERVAL_SECONDS)
        lag.append(max(0.0, time.monotonic() - before - LOOP_LAG_INTERVAL_SECONDS))


def attributable_violations(
    violations: list[ConnectionHold], *, worst_lag_seconds: float, threshold: float
) -> list[ConnectionHold]:
    """The holds still over the threshold once the process stall is removed."""
    return [
        hold
        for hold in violations
        if hold.gap_seconds - worst_lag_seconds >= threshold
    ]


@pytest.fixture
def scoped_connection_guard():
    """Arm the strict connection-scope monitor around one block of a test."""
    import asyncio
    from contextlib import asynccontextmanager

    from app.core.infrastructure.db.session import get_engine

    @asynccontextmanager
    async def guard(*, idle_hold_seconds: float = STRICT_IDLE_HOLD_SECONDS):
        monitor = connection_scope.start_connection_scope_monitor(
            idle_hold_seconds=idle_hold_seconds, strict=True
        )
        monitor.attach(get_engine())
        try:
            from app.modules.datastore.infrastructure.session import (
                get_datastore_engine,
            )

            monitor.attach(get_datastore_engine())
        except Exception:  # pragma: no cover - datastore is optional
            pass
        lag: list[float] = []
        watchdog = asyncio.create_task(_watch_loop_lag(lag))
        try:
            yield monitor
        finally:
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                # The cancellation this block just requested, arriving. Awaiting
                # is what makes the task actually finish before the readings are
                # totted up; the exception it raises on the way out is the
                # acknowledgement, not a failure.
                pass
            connection_scope.stop_connection_scope_monitor()
        worst_lag = max(lag, default=0.0)
        blamed = attributable_violations(
            monitor.violations,
            worst_lag_seconds=worst_lag,
            threshold=idle_hold_seconds,
        )
        if blamed:
            report = "\n\n".join(hold.render() for hold in blamed)
            stall = (
                f" (the loop itself stalled for up to {worst_lag * 1000:.0f}ms "
                "during this block, which is already subtracted)"
                if worst_lag >= idle_hold_seconds / 4
                else ""
            )
            pytest.fail(
                f"{len(blamed)} pooled connection(s) held across "
                f"non-database work inside the guarded block{stall}:\n\n{report}",
                pytrace=False,
            )

    return guard
