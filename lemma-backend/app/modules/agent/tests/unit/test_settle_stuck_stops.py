"""Stops that no worker ever acted on.

STOP_REQUESTED is an active status and holds the conversation's one active run
slot. Until it settles a new message attaches to the dying run and starts
nothing, and Retry refuses -- so a stop the worker never saw left the
conversation looking broken. The only thing that used to free it was the orphan
sweep, keyed on when the run *started* and an hour after the fact.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.repositories.conversation_status_repair import (
    settle_stuck_stops,
)


class _Repo:
    def __init__(self, stuck: list) -> None:
        self._stuck = stuck
        self.finished: list[tuple] = []
        self.events: list = []

    async def list_runs_stuck_stopping(self, *, cutoff_seconds: int, limit: int = 200):
        self.cutoff_seconds = cutoff_seconds
        return self._stuck

    async def finish_agent_run(self, *, agent_run_id, status, error=None):
        self.finished.append((agent_run_id, status, error))
        return SimpleNamespace(updated=True, status=AgentRunStatus.STOPPED)

    def collect_events(self, events) -> None:
        self.events.extend(events)


def _ref():
    return SimpleNamespace(id=uuid4(), conversation_id=uuid4())


@pytest.mark.asyncio
async def test_a_stop_nobody_acted_on_is_settled_as_stopped() -> None:
    run = _ref()
    repo = _Repo([run])

    settled = await settle_stuck_stops(repo, cutoff_seconds=120)

    assert [(rid, status) for _cid, rid, status in settled] == [
        (run.id, AgentRunStatus.STOPPED)
    ]
    # STOPPED, not FAILED. The user asked for this run to end, and it ended.
    assert repo.finished == [(run.id, AgentRunStatus.STOPPED, None)]


@pytest.mark.asyncio
async def test_it_publishes_so_the_client_stops_waiting() -> None:
    """Settling the row alone leaves the UI showing a run that is still going."""
    run = _ref()
    repo = _Repo([run])

    await settle_stuck_stops(repo, cutoff_seconds=120)

    assert [event.agent_run_id for event in repo.events] == [run.id]
    assert repo.events[0].status is AgentRunStatus.STOPPED


@pytest.mark.asyncio
async def test_a_row_that_did_not_move_is_not_reported() -> None:
    """Another worker finishing it first is the ordinary race, not a settle."""
    run = _ref()
    repo = _Repo([run])

    async def already_finished(*, agent_run_id, status, error=None):
        return SimpleNamespace(updated=False, status=AgentRunStatus.COMPLETED)

    repo.finish_agent_run = already_finished

    settled = await settle_stuck_stops(repo, cutoff_seconds=120)

    assert settled == []
    assert repo.events == []


@pytest.mark.asyncio
async def test_nothing_stuck_is_a_no_op() -> None:
    repo = _Repo([])

    assert await settle_stuck_stops(repo, cutoff_seconds=120) == []
    assert repo.events == []
