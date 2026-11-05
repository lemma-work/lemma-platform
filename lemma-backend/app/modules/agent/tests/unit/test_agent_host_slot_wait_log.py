"""How long a run waited on a full computer is logged, since nothing else
records it: once when the wait begins, once when it ends."""

from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.modules.agent.infrastructure.agent_host.recovery import BusyHost
from app.modules.agent.infrastructure.harnesses.agent_host.slot_wait import SlotWait


class _Repository:
    def __init__(self, answers: list[BusyHost | None]) -> None:
        self._answers = answers

    async def hold_for_busy_host(self, **_: object) -> BusyHost | None:
        return self._answers.pop(0)


class _Uow:
    async def commit(self) -> None:
        return None


@asynccontextmanager
async def _uow_factory():
    yield _Uow()


@pytest.mark.asyncio
async def test_a_wait_is_logged_when_it_begins_and_when_it_ends(caplog) -> None:
    busy = BusyHost(name="Mac", active_runs=20)
    repository = _Repository([busy, busy, None])
    wait = SlotWait(wait_seconds=300)
    run_id = uuid4()

    with caplog.at_level("INFO"):
        for _ in range(3):
            await wait.check(_uow_factory, lambda _uow: repository, agent_run_id=run_id)

    assert caplog.text.count("agent.agent_host.run.waiting_for_slot") == 1
    assert caplog.text.count("agent.agent_host.run.slot_wait_ended") == 1
    assert "Mac" not in caplog.text, "the computer's name is the person's, not logged"
