"""Tests for the connection-scope monitor.

The detector has to be right in both directions or it is worse than nothing: it
must catch a connection held across non-database work, and it must stay silent
on a session doing ordinary work — otherwise it gets switched off and the pool
sizing it protects goes back to being a guess.

Driven through the listener callbacks with fakes and an injected clock, so
there is no database, no sleeping, and nothing to flake. Same shape as the
loop-watchdog tests.
"""

from __future__ import annotations

import pytest

from app.core.observability import connection_scope
from app.core.observability.connection_scope import ConnectionScopeMonitor


class _Record:
    """Stands in for SQLAlchemy's _ConnectionRecord."""

    def __init__(self) -> None:
        self.info: dict = {}


class _Conn:
    """Stands in for a Connection.

    `Connection.info` is the *same dict object* as `connection_record.info`,
    which is what lets the pool events and the cursor events share state with
    no correlation step. The fake preserves that.
    """

    def __init__(self, record: _Record, *, in_transaction: bool = False) -> None:
        self.connection = record
        self._in_transaction = in_transaction

    def in_transaction(self) -> bool:
        return self._in_transaction


@pytest.fixture
def clock(monkeypatch):
    """A monotonic clock the test advances by hand."""

    current = {"t": 0.0}
    monkeypatch.setattr(connection_scope.time, "monotonic", lambda: current["t"])

    def advance(seconds: float) -> None:
        current["t"] += seconds

    advance.now = lambda: current["t"]  # type: ignore[attr-defined]
    return advance


def _monitor(**kwargs) -> ConnectionScopeMonitor:
    kwargs.setdefault("idle_hold_seconds", 0.5)
    return ConnectionScopeMonitor(**kwargs)


def _statement(monitor, conn, clock, *, duration: float) -> None:
    monitor._on_statement_start(conn, None, None, None, None, False)
    clock(duration)
    monitor._on_statement_end(conn, None, None, None, None, False)


def test_hold_across_non_db_work_is_reported(clock):
    monitor = _monitor(strict=True)
    record = _Record()
    conn = _Conn(record)

    monitor._on_checkout(None, record, None)
    _statement(monitor, conn, clock, duration=0.01)
    clock(3.0)  # the LLM call / HTTP request / sandbox op
    monitor._on_checkin(None, record)

    assert monitor.reports == 1
    violation = monitor.violations[0]
    assert violation.gap_seconds == pytest.approx(3.0, abs=0.01)
    assert violation.querying_seconds == pytest.approx(0.01, abs=0.001)
    assert violation.statements == 1


def test_commit_then_slow_work_is_not_reported(clock):
    """The prescribed fix must be structurally silent, not tuned to be silent.

    Committing checks the connection back in, so the slow work happens with no
    connection held at all — there is nothing for the monitor to see.
    """
    monitor = _monitor(strict=True)
    record = _Record()
    conn = _Conn(record)

    monitor._on_checkout(None, record, None)
    _statement(monitor, conn, clock, duration=0.01)
    monitor._on_checkin(None, record)  # commit

    clock(5.0)  # the external call, no connection held

    monitor._on_checkout(None, record, None)
    _statement(monitor, conn, clock, duration=0.01)
    monitor._on_checkin(None, record)

    assert monitor.reports == 0


def test_time_spent_querying_is_not_counted_as_idle(clock):
    """A slow query is `db_statement_timeout_seconds`' problem, not this one's."""
    monitor = _monitor(strict=True)
    record = _Record()
    conn = _Conn(record)

    monitor._on_checkout(None, record, None)
    _statement(monitor, conn, clock, duration=3.0)
    monitor._on_checkin(None, record)

    assert monitor.reports == 0


def test_many_quick_statements_do_not_accumulate_into_a_report(clock):
    """The trigger is one contiguous gap, not summed idle time.

    A hundred quick queries with ordinary Python between them sums to well over
    the threshold. That is a session doing its job; reporting it would train
    people to ignore the detector.
    """
    monitor = _monitor(idle_hold_seconds=0.5, strict=True)
    record = _Record()
    conn = _Conn(record)

    monitor._on_checkout(None, record, None)
    for _ in range(100):
        _statement(monitor, conn, clock, duration=0.001)
        clock(0.01)  # 1.0s of summed idle, no single gap over 10ms
    monitor._on_checkin(None, record)

    assert monitor.reports == 0


def test_gap_between_two_statements_is_caught(clock):
    """Not just a trailing hold — a hold in the middle counts too."""
    monitor = _monitor(strict=True)
    record = _Record()
    conn = _Conn(record)

    monitor._on_checkout(None, record, None)
    _statement(monitor, conn, clock, duration=0.01)
    clock(2.0)  # the external call, mid-session
    _statement(monitor, conn, clock, duration=0.01)
    monitor._on_checkin(None, record)

    assert monitor.reports == 1
    assert monitor.violations[0].gap_seconds == pytest.approx(2.0, abs=0.01)


def test_hold_before_the_first_query_is_caught(clock):
    """A session that does the slow thing before it ever queries still holds."""
    monitor = _monitor(strict=True)
    record = _Record()
    conn = _Conn(record)

    monitor._on_checkout(None, record, None)
    clock(3.0)
    _statement(monitor, conn, clock, duration=0.01)
    monitor._on_checkin(None, record)

    assert monitor.reports == 1


def test_open_transaction_is_flagged(clock):
    """Worse than a held connection: it is holding row locks too."""
    monitor = _monitor(strict=True)
    record = _Record()
    conn = _Conn(record, in_transaction=True)

    monitor._on_checkout(None, record, None)
    _statement(monitor, conn, clock, duration=0.01)
    clock(3.0)
    monitor._on_checkin(None, record)

    assert monitor.violations[0].in_transaction is True
    assert "row locks" in monitor.violations[0].render()


def test_statement_error_does_not_leave_phantom_query_time(clock):
    """`after_cursor_execute` does not fire when a statement raises.

    Without `handle_error` closing the interval, the failed statement's clock
    keeps running and every later gap is hidden behind it.
    """
    monitor = _monitor(strict=True)
    record = _Record()
    conn = _Conn(record)

    monitor._on_checkout(None, record, None)
    monitor._on_statement_start(conn, None, None, None, None, False)
    clock(0.01)

    class _ErrorContext:
        connection = conn

    monitor._on_statement_error(_ErrorContext())
    clock(3.0)
    monitor._on_checkin(None, record)

    assert monitor.reports == 1
    assert monitor.violations[0].querying_seconds == pytest.approx(0.01, abs=0.001)
    assert monitor.violations[0].gap_seconds == pytest.approx(3.0, abs=0.01)


def test_one_report_per_cooldown_but_every_violation_collected(clock):
    """The log is bounded; a strict test still sees everything."""
    monitor = _monitor(strict=True, cooldown_seconds=60.0)
    record = _Record()
    conn = _Conn(record)

    for _ in range(3):
        monitor._on_checkout(None, record, None)
        _statement(monitor, conn, clock, duration=0.01)
        clock(3.0)
        monitor._on_checkin(None, record)

    assert monitor.reports == 3
    assert len(monitor.violations) == 3


def test_state_from_a_previous_checkout_is_not_carried_over(clock):
    """Connections are reused; a stale hold must not leak into the next one."""
    monitor = _monitor(strict=True)
    record = _Record()
    conn = _Conn(record)

    monitor._on_checkout(None, record, None)
    _statement(monitor, conn, clock, duration=0.01)
    clock(3.0)
    monitor._on_checkin(None, record)
    assert monitor.reports == 1

    monitor._on_checkout(None, record, None)
    _statement(monitor, conn, clock, duration=0.01)
    monitor._on_checkin(None, record)

    assert monitor.reports == 1
    assert monitor.violations[-1].statements == 1


def test_checkin_without_a_checkout_is_ignored():
    """The monitor can be installed mid-process; a half-seen cycle is not a bug."""
    monitor = _monitor(strict=True)
    monitor._on_checkin(None, _Record())
    assert monitor.reports == 0


def test_non_strict_mode_collects_no_violations(clock):
    """Production reports to the log and keeps no unbounded list in memory."""
    monitor = _monitor(strict=False)
    record = _Record()
    conn = _Conn(record)

    monitor._on_checkout(None, record, None)
    _statement(monitor, conn, clock, duration=0.01)
    clock(3.0)
    monitor._on_checkin(None, record)

    assert monitor.reports == 1
    assert monitor.violations == []


def test_module_singleton_lifecycle():
    assert connection_scope.get_connection_scope_monitor() is None
    try:
        monitor = connection_scope.start_connection_scope_monitor(idle_hold_seconds=1.0)
        assert connection_scope.get_connection_scope_monitor() is monitor
    finally:
        connection_scope.stop_connection_scope_monitor()
    assert connection_scope.get_connection_scope_monitor() is None


def test_attach_is_a_no_op_without_a_monitor():
    """Engines are constructed before anything decides to watch them."""
    connection_scope.stop_connection_scope_monitor()
    connection_scope.attach_connection_scope_monitor(object())  # must not raise
