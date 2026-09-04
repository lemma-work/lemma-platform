"""Redelivery must not replay a fire whose moment has long passed.

Redelivery answers one question: the dispatch was lost, should it be sent again?
For a dispatch lost moments ago, yes. For a run whose scheduled time was a month
back, re-firing does not produce the run the user wanted — it produces a
surprise, and one that can start a real agent run and spend real quota.

The bound was meant to come from ``MAX_ATTEMPTS``, but attempts only increments
when a redelivered run is actually claimed, so a run whose target never appears
can cycle for a long time before it stops. Age is the more honest limit: a
schedule that should have fired in July should not fire now, however many
attempts are left.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.modules.schedule.services.run_recovery_service import (
    ScheduleRunRecoveryService,
)

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _service() -> ScheduleRunRecoveryService:
    # `recover()` is exercised end-to-end in the e2e ledger suite, which needs a
    # database; the age predicate is pure and is tested here.
    return ScheduleRunRecoveryService.__new__(ScheduleRunRecoveryService)


def _run(*, source_occurred_at=None, created_at=None):
    return SimpleNamespace(source_occurred_at=source_occurred_at, created_at=created_at)


def test_a_recent_lost_dispatch_is_still_redelivered() -> None:
    run = _run(source_occurred_at=NOW - timedelta(minutes=10))

    assert _service()._too_late_to_redeliver(run, NOW) is False


def test_a_month_old_fire_is_not_replayed() -> None:
    """The production case: runs scheduled 2026-07-13, still unresolved in August."""
    run = _run(source_occurred_at=datetime(2026, 7, 13, 6, 0, tzinfo=timezone.utc))

    assert _service()._too_late_to_redeliver(run, NOW) is True


def test_creation_time_stands_in_when_there_is_no_scheduled_time() -> None:
    """Event-driven runs carry no ``source_occurred_at``; they still age out."""
    run = _run(created_at=NOW - timedelta(days=2))

    assert _service()._too_late_to_redeliver(run, NOW) is True


def test_a_naive_timestamp_does_not_raise() -> None:
    """Some rows predate the timezone-aware columns and read back naive.

    Comparing one of those against an aware ``now`` raises TypeError, which in
    this position would abort the whole sweep rather than skip one row.
    """
    run = _run(source_occurred_at=datetime(2026, 7, 13, 6, 0))

    assert _service()._too_late_to_redeliver(run, NOW) is True


def test_a_run_with_no_timestamps_at_all_is_left_alone() -> None:
    """Undatable is not the same as stale; fall through to the attempts bound."""
    run = _run()

    assert _service()._too_late_to_redeliver(run, NOW) is False


@pytest.mark.parametrize("hours,expected", [(5, False), (7, True)])
def test_the_boundary_is_the_configured_age(hours: int, expected: bool) -> None:
    run = _run(source_occurred_at=NOW - timedelta(hours=hours))

    assert _service()._too_late_to_redeliver(run, NOW) is expected
