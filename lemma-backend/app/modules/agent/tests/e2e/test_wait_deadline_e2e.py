"""A wait whose target never finishes ends at its deadline, and says so.

``PS-AGENT-023`` promises that when the limit passes the run resumes and the
agent is told the wait *expired* -- not that what it waited on finished. The
rule is a pure function and tested as one; what only a database can show is the
whole path: the wait's deadline item fires, reads a child run that is still
going, closes the wait as DEADLINE and resumes the parent with that message,
while the child is left alone to finish.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    MessageKind,
)
from app.modules.agent.domain.state_kinds import PauseStatus
from app.modules.agent.domain.wait import (
    AgentWaitType,
    AgentWaitWakeReason,
)
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.core.infrastructure.work import WorkWriter
from app.modules.agent.services.wait_timers import arm_wait
from app.modules.test_support.e2e.agent_work import run_wait_work
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    finish,
    messages,
    pause_by_id,
    pausing_call,
    running_run,
    runs,
    seeded_conversation,
    wait_pause,
)

pytestmark = [pytest.mark.e2e]


@pytest.mark.drives_work
async def test_a_wait_past_its_deadline_resumes_saying_the_wait_expired(
    scenario,
) -> None:
    parent = await seeded_conversation(scenario)
    child = await seeded_conversation(scenario, parent_id=parent.conversation_id)
    still_going = await running_run(child)

    waiting = await running_run(parent)
    call = await pausing_call(
        parent,
        waiting,
        tool_name="wait_for",
        tool_args={"subagent_run_id": str(still_going)},
    )
    await finish(waiting, conversation_status=ConversationStatus.WAITING)
    # Armed long ago, its limit already behind it: its item is due.
    armed = datetime.now(timezone.utc) - timedelta(hours=1)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        wait = wait_pause(
            conversation_id=parent.conversation_id,
            run_id=waiting,
            pod_id=parent.pod_id,
            tool_call_id=call,
            wait_type=AgentWaitType.SUBAGENT,
            external_ref=f"deadline-{call}",
            wake_at=armed + timedelta(minutes=15),
            spec={
                "reason": "waiting on the sub-agent",
                "started_at": armed.isoformat(),
                "deadline_at": (armed + timedelta(minutes=15)).isoformat(),
                "target_ref": str(still_going),
                "user_id": str(parent.user_id),
            },
        )
        assert await Pauses(uow).open(wait)
        await arm_wait(WorkWriter.of(uow), wait, wait.wake_at)
        await uow.commit()

    assert await run_wait_work() == 1

    closed = await pause_by_id(wait.id)
    assert closed is not None
    assert closed.status is PauseStatus.RESOLVED
    assert closed.wait_spec.woke_because is AgentWaitWakeReason.DEADLINE

    (told,) = [
        m
        for m in await messages(parent.conversation_id)
        if m.kind is MessageKind.TOOL_RETURN and m.tool_call_id == call
    ]
    assert told.tool_result["woke_because"] == AgentWaitWakeReason.DEADLINE.value
    assert "limit ran out" in told.tool_result["message"]
    assert "may still be running" in told.tool_result["message"]

    (_, resumed) = await runs(parent.conversation_id)
    assert resumed.resumed_tool_call_id == call
    (child_run,) = await runs(child.conversation_id)
    assert child_run.status == AgentRunStatus.RUNNING.value, (
        "giving up on a wait must not stop what was being waited on"
    )
