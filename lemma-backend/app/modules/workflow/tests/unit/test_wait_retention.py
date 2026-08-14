"""Machine waits are scaffolding. Human waits are a record.

The whole point of this sweep is the distinction, so the test that matters most
is the one asserting HUMAN never appears in the deletion predicate — at any age
and in any status.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.workflow.infrastructure.repositories.wait_retention import (
    prune_terminal_machine_waits,
)


class _Session:
    def __init__(self, rowcounts: list[int]) -> None:
        # Shared, not copied: the sweep opens one session per batch, so the
        # remaining rowcounts have to carry across them or the loop never ends.
        self._rowcounts = rowcounts
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return self

    async def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(
            rowcount=self._rowcounts.pop(0) if self._rowcounts else 0
        )


def _maker(rowcounts):
    remaining = list(rowcounts)
    sessions = []

    def make():
        session = _Session(remaining)
        sessions.append(session)
        return session

    return make, sessions


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()


_NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_human_waits_are_never_in_the_deletion_predicate() -> None:
    """An approval record has no expiry. This is the assertion that guards it."""
    maker, sessions = _maker([0])

    await prune_terminal_machine_waits(
        maker, retention_days=30, batch_size=100, budget_seconds=30, now=_NOW
    )

    sql = _sql(sessions[0].statements[0])
    assert "'HUMAN'" not in sql
    for machine in ("'FUNCTION'", "'AGENT'", "'TIME'"):
        assert machine in sql


@pytest.mark.asyncio
async def test_only_finished_waits_are_eligible() -> None:
    """An ACTIVE wait is live state — deleting it strands the run forever."""
    maker, sessions = _maker([0])

    await prune_terminal_machine_waits(
        maker, retention_days=30, batch_size=100, budget_seconds=30, now=_NOW
    )

    sql = _sql(sessions[0].statements[0])
    assert "'ACTIVE'" not in sql
    for terminal in ("'COMPLETED'", "'FAILED'", "'CANCELLED'"):
        assert terminal in sql


@pytest.mark.asyncio
async def test_a_backlog_drains_across_batches() -> None:
    maker, sessions = _maker([100, 100, 7])

    removed = await prune_terminal_machine_waits(
        maker, retention_days=30, batch_size=100, budget_seconds=60, now=_NOW
    )

    assert removed == 207
    assert len(sessions) == 3


@pytest.mark.asyncio
async def test_the_budget_stops_a_run_at_a_batch_boundary(monkeypatch) -> None:
    from app.modules.workflow.infrastructure.repositories import wait_retention

    maker, sessions = _maker([100] * 50)
    clock = iter([0.0, 1.0, 999.0] + [999.0] * 50)
    monkeypatch.setattr(wait_retention.time, "monotonic", lambda: next(clock))

    removed = await prune_terminal_machine_waits(
        maker, retention_days=30, batch_size=100, budget_seconds=5, now=_NOW
    )

    assert removed == 200
    assert len(sessions) == 2


@pytest.mark.asyncio
async def test_a_zero_budget_disables_the_sweep() -> None:
    maker, sessions = _maker([100])

    removed = await prune_terminal_machine_waits(
        maker, retention_days=30, batch_size=100, budget_seconds=0, now=_NOW
    )

    assert removed == 0
    assert sessions == []
