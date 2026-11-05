"""``wait_for`` on work items: each wait ends once, however its endings race.

A wait and its item (``agent.wait.fire`` / ``agent.wait.poll``) commit together.
These pin the races the item has to settle against real Postgres: a stop or a
new message landing while the timer fires, a PROCESS poll that is already
running when the wait is cancelled, and a child finishing at its parent's
deadline.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import text
from starlette import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.infrastructure.work import WorkWriter, facts
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.domain.pauses import Pause
from app.modules.agent.domain.state_kinds import PauseStatus
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
)
from app.modules.agent.domain.wait import (
    AgentWaitType,
    AgentWaitWakeReason,
)
from app.modules.agent.events import wait_work
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.services.wait_targets import TargetOutcome
from app.modules.agent.services.wait_timers import arm_wait
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    finish,
    pause_by_id,
    pausing_call,
    running_run,
    runs,
    seeded_conversation,
    wait_pause,
)
from app.modules.test_support.e2e.agent_work import run_wait_work
from app.modules.test_support.e2e.builders import E2EScenario

pytestmark = [pytest.mark.e2e]


async def _waiting(
    scenario: E2EScenario,
    wait_type: AgentWaitType,
    *,
    target_ref: str | None = None,
    due_in: timedelta = timedelta(seconds=-1),
    deadline_in: timedelta = timedelta(hours=1),
    armed: bool = True,
    seeded: Seeded | None = None,
) -> tuple[Seeded, UUID, Pause]:
    """A conversation paused on ``wait_for``, its wait row and (``armed``) item."""
    seeded = seeded or await seeded_conversation(scenario)
    run_id = await running_run(seeded)
    call = await pausing_call(
        seeded, run_id, tool_name="wait_for", tool_args={"reason": "waiting"}
    )
    await finish(run_id, conversation_status=ConversationStatus.WAITING)
    now = datetime.now(timezone.utc)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        wait = wait_pause(
            conversation_id=seeded.conversation_id,
            run_id=run_id,
            pod_id=seeded.pod_id,
            tool_call_id=call,
            wait_type=wait_type,
            wake_at=now + due_in,
            spec={
                "reason": "waiting",
                "started_at": now.isoformat(),
                "deadline_at": (now + deadline_in).isoformat(),
                "target_ref": target_ref,
                "user_id": str(seeded.user_id),
            },
        )
        assert await Pauses(uow).open(wait)
        if armed:
            await arm_wait(WorkWriter.of(uow), wait, wait.wake_at)
        await uow.commit()
    return seeded, run_id, wait


async def _wait(wait_id: UUID) -> Pause:
    found = await pause_by_id(wait_id)
    assert found is not None
    return found


async def _items(wait_id: UUID, *, state: str = "queued") -> int:
    async with async_session_maker() as session:
        return int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM work_items WHERE dedup_key = :key "
                        "AND state = :state"
                    ),
                    {"key": f"wait:{wait_id}", "state": state},
                )
            ).scalar_one()
        )


async def _stop(scenario: E2EScenario, seeded: Seeded):
    return await scenario.owner_client.post(
        f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}/stop"
    )


# -- PROCESS: the poll re-arms itself, and stops re-arming -----------------------


@pytest.mark.drives_work
async def test_a_process_poll_rearms_until_the_process_exits(
    scenario, monkeypatch
) -> None:
    looks: list[str] = []

    async def _probe(target) -> TargetOutcome:
        looks.append(target.target_ref)
        if len(looks) < 3:
            return TargetOutcome(reason=None)
        return TargetOutcome(
            reason=AgentWaitWakeReason.TARGET_FINISHED, exit_code=0, message="done"
        )

    monkeypatch.setattr(wait_work, "probe_process_target", _probe)
    seeded, paused, wait = await _waiting(
        scenario, AgentWaitType.PROCESS, target_ref="proc-1"
    )

    for look in (1, 2):
        assert await run_wait_work(due_now=True) == 1
        assert (await _wait(wait.id)).status is PauseStatus.PENDING
        assert await _items(wait.id) == 1, f"look {look} did not re-arm its item"
        assert (await _wait(wait.id)).wait_spec.poll_attempt == look

    assert await run_wait_work(due_now=True) == 1

    ended = await _wait(wait.id)
    assert ended.status is PauseStatus.RESOLVED
    assert ended.wait_spec.woke_because is AgentWaitWakeReason.TARGET_FINISHED
    assert await _items(wait.id) == 0, "re-armed after the process exited"
    assert len(looks) == 3
    (_, resumed) = await runs(seeded.conversation_id)
    assert resumed.resumed_tool_call_id == wait.tool_call_id
    del paused


@pytest.mark.drives_work
async def test_a_stop_landing_while_the_poll_runs_is_not_rearmed(
    scenario, monkeypatch
) -> None:
    """Cancel sees the item running and leaves it; the poll re-reads the wait
    under its lock in the completion and does not re-arm a cancelled one."""
    probing = asyncio.Event()
    release = asyncio.Event()

    async def _slow_probe(target) -> TargetOutcome:
        probing.set()
        await release.wait()
        return TargetOutcome(reason=None)

    monkeypatch.setattr(wait_work, "probe_process_target", _slow_probe)
    seeded, _, wait = await _waiting(
        scenario, AgentWaitType.PROCESS, target_ref="proc-2"
    )

    polling = asyncio.create_task(run_wait_work(due_now=True))
    await asyncio.wait_for(probing.wait(), timeout=15)
    assert await _items(wait.id, state="running") == 1
    stopped = await _stop(scenario, seeded)
    assert stopped.status_code == status.HTTP_200_OK, stopped.text
    release.set()
    await polling

    ended = await _wait(wait.id)
    assert ended.status is PauseStatus.CANCELLED
    assert await _items(wait.id) == 0, "a cancelled wait was re-armed"
    assert await _items(wait.id, state="running") == 0
    assert len(await runs(seeded.conversation_id)) == 1, "a stopped agent woke"


# -- TIME: a stop racing the fire ------------------------------------------------


@pytest.mark.drives_work
async def test_a_stop_racing_the_timer_ends_the_wait_exactly_one_way(scenario) -> None:
    """Twenty races of Stop against a due TIME fire: resumed xor cancelled."""
    outcomes: list[str] = []
    for race in range(20):
        seeded, _, wait = await _waiting(scenario, AgentWaitType.TIME)

        async def _stop_after(delay: float, seeded: Seeded = seeded):
            # Swept across the window so either side wins some: the invariant
            # must hold both ways.
            await asyncio.sleep(delay)
            return await _stop(scenario, seeded)

        _, stopped = await asyncio.gather(run_wait_work(), _stop_after(race * 0.002))

        assert stopped.status_code == status.HTTP_200_OK, stopped.text
        ended = await _wait(wait.id)
        resumed = len(await runs(seeded.conversation_id)) == 2
        if ended.status is PauseStatus.RESOLVED:
            assert resumed, "the wait woke but no run resumed it"
            outcomes.append("resumed")
        else:
            assert ended.status is PauseStatus.CANCELLED
            assert not resumed, "cancelled, yet the agent woke"
            outcomes.append("cancelled")
        assert await _items(wait.id) == 0
    print(
        "WAIT_STOP_RACE "
        f"resumed={outcomes.count('resumed')} cancelled={outcomes.count('cancelled')}"
    )


async def test_a_new_message_cancels_the_wait_and_drops_its_timer(scenario) -> None:
    seeded, _, wait = await _waiting(
        scenario, AgentWaitType.TIME, due_in=timedelta(minutes=10)
    )
    assert await _items(wait.id) == 1

    sent = await scenario.owner_client.post(
        f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}/messages/append",
        json={"content": "actually, do something else"},
    )

    assert sent.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED), sent.text
    ended = await _wait(wait.id)
    assert ended.status is PauseStatus.CANCELLED
    assert await _items(wait.id) == 0, "the superseded wait's timer is still armed"


# -- SUBAGENT: the child finishing at the parent's deadline -------------------------


async def _finished_child(scenario: E2EScenario, parent: Seeded) -> tuple[UUID, UUID]:
    child = await seeded_conversation(scenario, parent_id=parent.conversation_id)
    child_run = await running_run(child)
    await finish(child_run, conversation_status=ConversationStatus.COMPLETED)
    return child.conversation_id, child_run


@pytest.mark.drives_work
async def test_a_child_finishing_at_the_deadline_resumes_the_parent_once(
    scenario,
) -> None:
    """The child's ``AgentRunFinished`` item and the deadline item run in one
    round, concurrently; ten times over, the parent resumes exactly once."""
    for _ in range(10):
        parent = await seeded_conversation(scenario)
        child_conversation, child_run = await _finished_child(scenario, parent)
        _, _, wait = await _waiting(
            scenario,
            AgentWaitType.SUBAGENT,
            target_ref=str(child_run),
            seeded=parent,
        )
        async with create_uow_from_session_maker(async_session_maker) as uow:
            written = await facts.emit(
                uow,
                AgentRunFinished(
                    run_id=child_run,
                    conversation_id=child_conversation,
                    pod_id=parent.pod_id,
                    status="COMPLETED",
                    conversation_status="COMPLETED",
                    parent_kind="agent",
                    parent_ref=str(parent.conversation_id),
                    origin="subagent",
                ),
            )
            await uow.commit()
        assert written >= 1

        await run_wait_work()

        ended = await _wait(wait.id)
        assert ended.status is PauseStatus.RESOLVED
        assert ended.wait_spec.woke_because is AgentWaitWakeReason.TARGET_FINISHED
        assert len(await runs(parent.conversation_id)) == 2, "resumed twice"
        assert await _items(wait.id) == 0


@pytest.mark.drives_work
async def test_a_child_still_running_at_the_deadline_is_left_running(scenario) -> None:
    parent = await seeded_conversation(scenario)
    child = await seeded_conversation(scenario, parent_id=parent.conversation_id)
    child_run = await running_run(child)
    _, _, wait = await _waiting(
        scenario,
        AgentWaitType.SUBAGENT,
        target_ref=str(child_run),
        seeded=parent,
        deadline_in=timedelta(seconds=-1),
    )

    await run_wait_work()

    ended = await _wait(wait.id)
    assert ended.wait_spec.woke_because is AgentWaitWakeReason.DEADLINE
    (child_status,) = [r.status for r in await runs(child.conversation_id)]
    assert child_status == AgentRunStatus.RUNNING.value


@pytest.mark.drives_work
async def test_a_deadline_item_fired_early_rearms_for_the_deadline(scenario) -> None:
    parent = await seeded_conversation(scenario)
    child = await seeded_conversation(scenario, parent_id=parent.conversation_id)
    child_run = await running_run(child)
    _, _, wait = await _waiting(
        scenario,
        AgentWaitType.SUBAGENT,
        target_ref=str(child_run),
        seeded=parent,
        due_in=timedelta(seconds=-1),
        deadline_in=timedelta(minutes=30),
    )

    await run_wait_work()

    assert (await _wait(wait.id)).status is PauseStatus.PENDING
    async with async_session_maker() as session:
        due = (
            await session.execute(
                text("SELECT run_at FROM work_items WHERE dedup_key = :key"),
                {"key": f"wait:{wait.id}"},
            )
        ).scalar_one()
    assert due > datetime.now(timezone.utc) + timedelta(minutes=29)


async def test_a_run_ending_on_its_own_wait_says_so_and_one_asking_a_person_does_not(
    scenario,
) -> None:
    """The completed frame's ``waiting_on_work``: what lets a client follow a
    conversation to the run that answers it, without reading it back."""
    from app.modules.agent.services.run_finalizer import waiting_on_work

    waiting, _, _ = await _waiting(scenario, AgentWaitType.SUBAGENT, armed=False)
    asking = await seeded_conversation(scenario)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        assert await waiting_on_work(uow, waiting.conversation_id) is True
        assert await waiting_on_work(uow, asking.conversation_id) is False

    # And a viewer that opens the conversation later reads the same.
    opened = await scenario.owner_client.get(
        f"/pods/{waiting.pod_id}/conversations/{waiting.conversation_id}"
    )
    assert opened.status_code == 200, opened.text
    assert (opened.json()["status"], opened.json()["waiting_on_work"]) == (
        "WAITING",
        True,
    )
