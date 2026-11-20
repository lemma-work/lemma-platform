"""A conversation waiting on its own process keeps its machine slot; any
other wait, and a conversation that ended, frees it."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.pauses import Pause
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.wait import AgentWaitType
from app.modules.agent.services.run_finalizer import (
    _end_machine_work,
    waits_on_a_process,
)


async def test_waiting_on_a_process_parks_the_slot_and_anything_else_frees_it() -> None:
    calls: list[tuple[str, object]] = []

    async def park(run_id):
        calls.append(("park", run_id))

    async def end(run_id):
        calls.append(("end", run_id))
        return []

    on_process, otherwise = uuid4(), uuid4()

    await _end_machine_work(on_process, waits_on_own_process=True, park=park, end=end)
    await _end_machine_work(otherwise, waits_on_own_process=False, park=park, end=end)

    assert calls == [("park", on_process), ("end", otherwise)]


def _pause(kind: PauseKind, wait_type: AgentWaitType | None = None) -> Pause:
    return Pause(
        conversation_id=uuid4(),
        run_id=uuid4(),
        pod_id=uuid4(),
        tool_call_id="call-1",
        tool_name="wait_for",
        kind=kind,
        wait_type=wait_type,
    )


def test_a_wait_on_a_process_is_the_only_one_that_parks() -> None:
    assert waits_on_a_process([_pause(PauseKind.WAIT, AgentWaitType.PROCESS)])


@pytest.mark.parametrize(
    "pause",
    [
        _pause(PauseKind.WAIT, AgentWaitType.TIME),
        _pause(PauseKind.WAIT, AgentWaitType.SUBAGENT),
        _pause(PauseKind.APPROVAL),
        _pause(PauseKind.QUESTION),
    ],
    ids=["timer", "sub-agent", "approval", "question"],
)
def test_waiting_on_a_person_a_timer_or_a_sub_agent_does_not_park(pause) -> None:
    """Parking on any WAITING held a pod machine's only run slot -- busy and
    billed -- through an approval nobody answered."""
    assert not waits_on_a_process([pause])


def test_no_pending_wait_does_not_park() -> None:
    assert not waits_on_a_process([])
