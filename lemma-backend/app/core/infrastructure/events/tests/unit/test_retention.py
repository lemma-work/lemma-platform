from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.infrastructure.events import retention as retention_module
from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.retention import prune_event_delivery_records


_CATEGORIES = (
    "outbox_published",
    "outbox_dead_letter",
    "inbox_completed",
    "inbox_dead_letter",
)


class _Session:
    """One transaction. ``rowcount`` is whatever the harness decides to return."""

    def __init__(self, rowcounts: list[int]) -> None:
        self.statements: list[object] = []
        self._rowcounts = rowcounts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return self

    async def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(rowcount=self._rowcounts.pop(0))


def _harness(rowcounts: list[int]):
    """Return a session_maker plus the list of sessions it hands out."""
    sessions: list[_Session] = []

    def session_maker() -> _Session:
        session = _Session(rowcounts)
        sessions.append(session)
        return session

    return session_maker, sessions


_NOW = datetime(2026, 7, 10, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_a_short_batch_ends_that_category() -> None:
    """The steady state: nothing to reclaim, so one statement per category."""
    session_maker, sessions = _harness([1] * 4)

    deleted = await prune_event_delivery_records(
        session_maker,  # type: ignore[arg-type]
        now=_NOW,
    )

    assert deleted == dict.fromkeys(_CATEGORIES, 1)
    assert len(sessions) == 4
    assert all(len(session.statements) == 1 for session in sessions)


@pytest.mark.asyncio
async def test_a_backlog_larger_than_one_batch_drains_fully(monkeypatch) -> None:
    """The regression this exists for.

    Deleting exactly one batch per category per run capped reclamation at
    ``batch_size`` rows an hour, which any install producing events faster than
    that outruns -- the table then grows without bound however short the
    retention window is. A backlog spanning several batches has to drain within
    the run.
    """
    monkeypatch.setattr(event_transport_settings, "event_retention_batch_size", 10)
    # Three full batches then a short one, for the first category only.
    session_maker, sessions = _harness([10, 10, 10, 4] + [0] * 3)

    deleted = await prune_event_delivery_records(
        session_maker,  # type: ignore[arg-type]
        now=_NOW,
    )

    assert deleted["outbox_published"] == 34
    assert all(deleted[name] == 0 for name in _CATEGORIES[1:])
    # Four transactions for the drained category, one each for the rest.
    assert len(sessions) == 7


@pytest.mark.asyncio
async def test_the_budget_stops_a_run_at_a_batch_boundary(monkeypatch) -> None:
    """A backlog too large for one run must not run into the next one.

    Stopping early loses nothing: the cutoff is recomputed from ``now`` on the
    following run and everything past it is still eligible.
    """
    monkeypatch.setattr(event_transport_settings, "event_retention_batch_size", 10)
    monkeypatch.setattr(
        event_transport_settings, "event_retention_run_budget_seconds", 5.0
    )
    # Every batch comes back full, so only the budget can end this.
    session_maker, sessions = _harness([10] * 100)
    # Two batches inside the budget, then past it.
    clock = iter([0.0, 1.0, 2.0, 99.0] + [99.0] * 100)
    monkeypatch.setattr(retention_module.time, "monotonic", lambda: next(clock))

    deleted = await prune_event_delivery_records(
        session_maker,  # type: ignore[arg-type]
        now=_NOW,
    )

    # Stopped mid-backlog rather than looping until the table emptied.
    assert deleted["outbox_published"] == 30
    assert len(sessions) < 100
    # Whole batches only -- a run never abandons a partially applied delete.
    assert deleted["outbox_published"] % 10 == 0


@pytest.mark.asyncio
async def test_a_zero_budget_restores_one_batch_per_category(monkeypatch) -> None:
    """The documented escape hatch, for an operator who wants the old cap."""
    monkeypatch.setattr(event_transport_settings, "event_retention_batch_size", 10)
    monkeypatch.setattr(
        event_transport_settings, "event_retention_run_budget_seconds", 0.0
    )
    session_maker, sessions = _harness([10] * 4)

    deleted = await prune_event_delivery_records(
        session_maker,  # type: ignore[arg-type]
        now=_NOW,
    )

    assert deleted == dict.fromkeys(_CATEGORIES, 10)
    assert len(sessions) == 4
