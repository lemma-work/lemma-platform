"""Report a pooled connection held while the database was asked nothing.

The pool is sized on one promise: a session holds its connection for a unit of
work and gives it back before any LLM call, HTTP request, sandbox operation or
thread offload. ``scripts/check_session_scope.py`` enforces that statically, but
it is a deny-list over an AST and says so — it cannot see a call it has no
pattern for, and it cannot see through a variable.

This measures the same property from the other end, at runtime, where there is
nothing to infer. A connection is checked out for some wall-clock span; some of
that span is spent executing statements; the difference is time the connection
was held while the database sat idle. That difference *is* the bug, directly
observed, whatever the code shape that produced it.

It is the sibling of :mod:`app.core.observability.stall_sampler`, which answers
the same question for the event loop, and it is built the same way: one small
class, one structured event, a cooldown so a pathological path cannot flood the
log, and a ``reports`` counter so a test can assert rather than sleep and hope.

Cost when nothing is wrong: two monotonic clock reads per checkout and two per
statement. No stack is captured unless a violation is being reported.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass

from sqlalchemy import event

from app.core.log.log import get_logger

logger = get_logger(__name__)

# Frames belonging to the machinery doing the watching, or to the SQLAlchemy and
# driver layers every checkout passes through. Trimmed so the reported culprit is
# application code.
_UNINTERESTING = (
    "sqlalchemy/",
    "app/core/observability/connection_scope.py",
    "contextlib.py",
)


def _is_interesting(frame: traceback.FrameSummary) -> bool:
    return not any(part in frame.filename for part in _UNINTERESTING)


def format_hold_stack(frames: list[traceback.FrameSummary]) -> str:
    """The application frames around a held connection, innermost last."""
    interesting = [frame for frame in frames if _is_interesting(frame)]
    return "".join(traceback.format_list((interesting or frames)[-12:])).rstrip()


@dataclass
class _Hold:
    """One checkout, and the longest stretch of it the database sat idle.

    The trigger is the longest CONTIGUOUS gap between statements, not total
    idle time. Summing would report a session that issues a hundred quick
    queries with ordinary Python between them -- which is a session doing its
    job, not a connection held across an LLM call. One contiguous gap is
    exactly one await, which is the sentence this detector exists to say.
    """

    checked_out_at: float
    last_activity_at: float
    querying_seconds: float = 0.0
    longest_gap_seconds: float = 0.0
    statements: int = 0
    statement_started_at: float | None = None
    opened_at_stack: str | None = None
    # Whether the last statement left a transaction open. A gap here is not
    # merely a held connection -- it is holding row locks, which blocks other
    # writers and not just the pool.
    in_transaction: bool = False

    def close_gap(self, now: float) -> None:
        """Bank the stretch that just ended and start a new one."""
        self.longest_gap_seconds = max(self.longest_gap_seconds, now - self.last_activity_at)
        self.last_activity_at = now


@dataclass
class ConnectionHold:
    """A reported violation, in the shape a test failure wants to print."""

    gap_seconds: float
    held_seconds: float
    querying_seconds: float
    statements: int
    in_transaction: bool
    stack: str

    def render(self) -> str:
        lock_note = ", in an open transaction (so row locks too)" if self.in_transaction else ""
        return (
            f"held {self.held_seconds * 1000:.0f}ms across {self.statements} "
            f"statement(s) totalling {self.querying_seconds * 1000:.0f}ms, with a "
            f"{self.gap_seconds * 1000:.0f}ms stretch where the database was asked "
            f"nothing{lock_note}\n{self.stack}"
        )


class ConnectionScopeMonitor:
    """Watches engines and reports connections held across non-database work."""

    def __init__(
        self,
        *,
        idle_hold_seconds: float,
        strict: bool = False,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self._idle_hold_seconds = idle_hold_seconds
        self._strict = strict
        self._cooldown_seconds = cooldown_seconds
        self._last_report = -1e9
        self._attached: set[int] = set()
        # Incremented on every report so a test can assert on it directly.
        self.reports = 0
        # Populated only in strict mode: what a failing test prints.
        self.violations: list[ConnectionHold] = []

    # ---------------------------------------------------------------- wiring

    def attach(self, engine) -> None:
        """Instrument one engine. Safe to call repeatedly; idempotent per engine."""
        sync_engine = getattr(engine, "sync_engine", engine)
        if id(sync_engine) in self._attached:
            return
        self._attached.add(id(sync_engine))
        event.listen(sync_engine.pool, "checkout", self._on_checkout)
        event.listen(sync_engine.pool, "checkin", self._on_checkin)
        event.listen(sync_engine, "before_cursor_execute", self._on_statement_start)
        event.listen(sync_engine, "after_cursor_execute", self._on_statement_end)
        # after_cursor_execute does NOT fire when a statement raises, so without
        # this the failed statement's interval never closes and its duration is
        # counted as querying forever after -- hiding every later gap.
        event.listen(sync_engine, "handle_error", self._on_statement_error)

    # ---------------------------------------------------------------- events

    def _on_checkout(self, dbapi_connection, connection_record, connection_proxy) -> None:
        del dbapi_connection, connection_proxy
        now = time.monotonic()
        hold = _Hold(checked_out_at=now, last_activity_at=now)
        if self._strict:
            # Only in tests: naming where the connection was TAKEN is worth a
            # stack walk per checkout, because that is the line a developer has
            # to change. In production the checkin stack is close enough and
            # costs nothing until something is actually wrong.
            hold.opened_at_stack = format_hold_stack(traceback.extract_stack())
        connection_record.info["lemma_connection_hold"] = hold

    def _on_statement_start(
        self, conn, cursor, statement, parameters, context, executemany
    ) -> None:
        del cursor, statement, parameters, context, executemany
        hold = self._hold_for(conn)
        if hold is None:
            return
        now = time.monotonic()
        # The stretch since the last statement ended is a gap: bank it before
        # this statement starts, so a hold BETWEEN two queries is caught and
        # not just one trailing off the end.
        hold.close_gap(now)
        hold.statement_started_at = now

    def _on_statement_end(
        self, conn, cursor, statement, parameters, context, executemany
    ) -> None:
        del cursor, statement, parameters, context, executemany
        hold = self._hold_for(conn)
        if hold is None or hold.statement_started_at is None:
            return
        now = time.monotonic()
        hold.querying_seconds += now - hold.statement_started_at
        hold.statement_started_at = None
        hold.last_activity_at = now
        hold.statements += 1
        try:
            hold.in_transaction = bool(conn.in_transaction())
        except Exception:  # pragma: no cover - defensive; never break a query
            hold.in_transaction = False

    def _on_statement_error(self, exception_context) -> None:
        """Close the open interval for a statement that raised."""
        hold = self._hold_for(getattr(exception_context, "connection", None))
        if hold is None or hold.statement_started_at is None:
            return
        now = time.monotonic()
        hold.querying_seconds += now - hold.statement_started_at
        hold.statement_started_at = None
        hold.last_activity_at = now
        hold.statements += 1

    def _on_checkin(self, dbapi_connection, connection_record) -> None:
        del dbapi_connection
        hold = connection_record.info.pop("lemma_connection_hold", None)
        if hold is None:
            return
        now = time.monotonic()
        hold.close_gap(now)
        if hold.longest_gap_seconds < self._idle_hold_seconds:
            return
        self._report(hold, held=now - hold.checked_out_at)

    @staticmethod
    def _hold_for(conn) -> _Hold | None:
        # `connection_record.info` and `Connection.info` are the same dict, so
        # the pool events and the cursor events share state with no correlation
        # step.
        try:
            return conn.connection.info.get("lemma_connection_hold")
        except Exception:  # pragma: no cover - defensive; never break a query
            return None

    # --------------------------------------------------------------- report

    def _report(self, hold: _Hold, *, held: float) -> None:
        stack = hold.opened_at_stack or format_hold_stack(traceback.extract_stack())
        self.reports += 1
        if self._strict:
            self.violations.append(
                ConnectionHold(
                    gap_seconds=hold.longest_gap_seconds,
                    held_seconds=held,
                    querying_seconds=hold.querying_seconds,
                    statements=hold.statements,
                    in_transaction=hold.in_transaction,
                    stack=stack,
                )
            )
        now = time.monotonic()
        if now - self._last_report < self._cooldown_seconds:
            return
        self._last_report = now
        logger.warning(
            "runtime.connection_scope.degraded",
            gap_ms=round(hold.longest_gap_seconds * 1000, 1),
            held_ms=round(held * 1000, 1),
            querying_ms=round(hold.querying_seconds * 1000, 1),
            statements=hold.statements,
            threshold_ms=round(self._idle_hold_seconds * 1000, 1),
            in_transaction=hold.in_transaction,
            stack=stack,
        )


_monitor: ConnectionScopeMonitor | None = None


def get_connection_scope_monitor() -> ConnectionScopeMonitor | None:
    return _monitor


def start_connection_scope_monitor(
    *, idle_hold_seconds: float, strict: bool = False
) -> ConnectionScopeMonitor:
    """Install the process-wide monitor. Attach engines to it afterwards."""
    global _monitor
    _monitor = ConnectionScopeMonitor(
        idle_hold_seconds=idle_hold_seconds, strict=strict
    )
    return _monitor


def stop_connection_scope_monitor() -> None:
    global _monitor
    _monitor = None


def attach_connection_scope_monitor(engine) -> None:
    """Instrument ``engine`` if a monitor is installed. No-op otherwise.

    Called from engine construction so both the primary and the datastore engine
    are covered without either module knowing the monitor exists.
    """
    if _monitor is not None:
        _monitor.attach(engine)
