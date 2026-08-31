"""What the schedule-run pruner must never delete.

The ledger had no retention at all — an append-only table that only ever grew —
and every index on it pays for that forever. But it is also a live table with two
properties a careless DELETE would break: runs sit legitimately in flight for
months at a time (workflows parked on human form waits), and the circuit breaker
decides whether to deactivate a schedule by counting back through its completed
runs.

So these tests are mostly about the negative space: the predicate, and the
drain loop's stopping conditions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.schedule.config import schedule_settings
from app.modules.schedule.domain.schedule import ScheduleRunStatus
from app.modules.schedule.infrastructure import run_retention
from app.modules.schedule.infrastructure.models.run import ScheduleRun

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def begin(self):
        return self


def _session_maker():
    return _Session()


@pytest.fixture
def deleted(monkeypatch):
    """Capture the filters the pruner would delete with, returning batch sizes."""
    calls: list[tuple] = []
    sizes = iter([])

    async def fake_delete_batch(session, model, *filters, batch_size):
        calls.append((model, filters, batch_size))
        return next(sizes, 0)

    monkeypatch.setattr(run_retention, "delete_batch", fake_delete_batch)

    def configure(*batches: int):
        nonlocal sizes
        sizes = iter(batches)
        return calls

    return configure


def _predicate_sql(calls) -> str:
    return " ".join(str(f) for f in calls[0][1])


@pytest.mark.anyio
async def test_only_finished_runs_are_eligible(deleted) -> None:
    calls = deleted(0)

    await run_retention.prune_schedule_runs(_session_maker, now=NOW)

    sql = _predicate_sql(calls)
    assert "completed_at IS NOT NULL" in sql, "an in-flight run has no completed_at"
    assert calls[0][0] is ScheduleRun


def test_a_dispatched_run_awaiting_its_target_is_not_terminal() -> None:
    """The long-parked case: DISPATCHED with no outcome is in flight, not old.

    Asserted against the constant rather than the rendered SQL — SQLAlchemy
    compiles an IN list to a bind parameter, so a string check would pass
    whatever the list contained.
    """
    terminal = set(run_retention._TERMINAL_STATUSES)

    assert terminal.isdisjoint(
        {
            ScheduleRunStatus.DISPATCHED.value,
            ScheduleRunStatus.PROCESSING.value,
            ScheduleRunStatus.RECEIVED.value,
        }
    ), "a run that has not finished must never be prunable"
    assert terminal == {
        ScheduleRunStatus.COMPLETED.value,
        ScheduleRunStatus.TARGET_FAILED.value,
        ScheduleRunStatus.CANCELLED.value,
        ScheduleRunStatus.FILTERED.value,
        ScheduleRunStatus.DEAD_LETTERED.value,
    }


def test_every_run_status_is_classified_one_way_or_the_other() -> None:
    """A new status must be a deliberate decision, not an accidental omission.

    ``FAILED`` is the interesting one, and it is deliberately *not* prunable.
    It carries a ``completed_at``, so a predicate written on timestamps alone
    would delete it — but it is the retryable intermediate state, and the
    recovery sweep still picks it up. Retention only removes what is already
    finished; deciding that a run is abandoned belongs to recovery, which
    dead-letters a stale one and thereby hands it to retention properly.
    """
    retryable_or_in_flight = {
        ScheduleRunStatus.RECEIVED.value,
        ScheduleRunStatus.PROCESSING.value,
        ScheduleRunStatus.DISPATCHED.value,
        ScheduleRunStatus.FAILED.value,
    }
    classified = set(run_retention._TERMINAL_STATUSES) | retryable_or_in_flight

    assert classified == {status.value for status in ScheduleRunStatus}
    assert ScheduleRunStatus.FAILED.value not in run_retention._TERMINAL_STATUSES, (
        "FAILED is retryable; recovery must dead-letter it before retention sees it"
    )


@pytest.mark.anyio
async def test_the_window_outlives_any_streak_the_breaker_counts() -> None:
    """Pruning inside a live streak could silently un-arm a breaker.

    `consecutive_terminal_failures` counts back from the newest completed run to
    decide whether to deactivate. If retention could delete rows it would have
    read, a schedule failing steadily could have its streak shortened and never
    trip.
    """
    from app.modules.schedule.repositories.schedule_run_repository import (
        _BREAKER_SCAN_LIMIT,
    )

    # A schedule would have to fire more than this many times a day for ninety
    # days of history to be shorter than one breaker scan.
    fires_per_day_to_be_unsafe = _BREAKER_SCAN_LIMIT / (
        schedule_settings.schedule_run_retention_days
    )

    assert schedule_settings.schedule_run_retention_days >= 30
    assert fires_per_day_to_be_unsafe < 10, (
        "retention window is short enough that a busy schedule's streak could "
        "be pruned mid-count"
    )


@pytest.mark.anyio
async def test_the_cutoff_is_measured_from_the_supplied_clock(deleted) -> None:
    calls = deleted(0)

    await run_retention.prune_schedule_runs(_session_maker, now=NOW)

    expected = NOW - timedelta(days=schedule_settings.schedule_run_retention_days)
    # The `completed_at < cutoff` comparison, read off the bound parameter.
    cutoffs = [
        clause.right.value
        for clause in calls[0][1]
        if getattr(getattr(clause, "right", None), "value", None) is not None
    ]
    assert expected in cutoffs, f"expected a cutoff of {expected}, got {cutoffs}"


@pytest.mark.anyio
async def test_it_drains_rather_than_deleting_one_batch_a_run(deleted) -> None:
    """The lesson from the outbox: one batch per run lets the table outrun it."""
    batch = schedule_settings.schedule_run_retention_batch_size
    calls = deleted(batch, batch, 3)

    removed = await run_retention.prune_schedule_runs(_session_maker, now=NOW)

    assert len(calls) == 3, "a full batch means more remain; keep going"
    assert removed == batch * 2 + 3


@pytest.mark.anyio
async def test_a_short_batch_ends_the_sweep(deleted) -> None:
    calls = deleted(1)

    await run_retention.prune_schedule_runs(_session_maker, now=NOW)

    assert len(calls) == 1


@pytest.mark.anyio
async def test_a_zero_budget_deletes_exactly_one_batch(deleted, monkeypatch) -> None:
    monkeypatch.setattr(schedule_settings, "schedule_run_retention_budget_seconds", 0.0)
    batch = schedule_settings.schedule_run_retention_batch_size
    calls = deleted(batch, batch, batch)

    await run_retention.prune_schedule_runs(_session_maker, now=NOW)

    assert len(calls) == 1


@pytest.mark.anyio
async def test_the_cron_is_registered_on_the_bulk_lane() -> None:
    """Interactive work must not queue behind a drain."""
    from app.core.infrastructure.jobs.streaq_runtime import Lane, TASK_LANES
    import app.modules.schedule.events.tasks  # noqa: F401

    assert TASK_LANES.get("prune_schedule_runs") is Lane.BULK


@pytest.mark.anyio
async def test_the_module_registers_its_tasks() -> None:
    """A cron nothing imports never runs — the failure this almost shipped as."""
    from app.modules.schedule.module import module

    assert module.register_streaq is not None
    module.register_streaq()
    from app.core.infrastructure.jobs.streaq_runtime import TASK_LANES

    assert "prune_schedule_runs" in TASK_LANES


@pytest.mark.anyio
async def test_the_pruner_reports_nothing_when_the_table_is_clean(deleted) -> None:
    deleted(0)

    assert await run_retention.prune_schedule_runs(_session_maker, now=NOW) == 0


@pytest.mark.anyio
async def test_delete_batch_is_shared_with_the_event_pruner() -> None:
    """Reused, not reimplemented: SKIP LOCKED is what makes it concurrent-safe."""
    from app.core.infrastructure.events.retention import delete_batch

    assert run_retention.delete_batch is delete_batch


def test_the_deleting_side_effect_is_only_ever_a_delete() -> None:
    """A retention module that can UPDATE is one bad predicate from data loss."""
    import inspect

    source = inspect.getsource(run_retention)
    for forbidden in ("update(", "insert(", ".add(", "session.merge"):
        assert forbidden not in source, f"{forbidden} has no place in a pruner"


@pytest.mark.anyio
async def test_an_unfinished_run_is_excluded_even_with_a_stray_completion_time(
    deleted,
) -> None:
    """Belt and braces: completed_at alone must not be the whole predicate.

    It is sufficient today. It stops being sufficient the moment anything sets a
    completion time before the outcome is known, and that is not a change anyone
    would think to check retention against.
    """
    calls = deleted(0)

    await run_retention.prune_schedule_runs(_session_maker, now=NOW)

    sql = _predicate_sql(calls)
    assert "target_outcome IS NOT NULL" in sql
    assert "status IN" in sql
