"""A wait always resolves, including when what it waited on cannot answer.

The happy paths are covered where the decision is made, against a pure function.
What needs a database is the other half: that a wait whose target has vanished
still wakes the agent with something truthful, and that a wait is fired by the
work item written with it -- through the runtime's own claim and execute --
and only when it is due.

These are the failure paths `PS-AGENT-023` promises and that a fake repository
cannot hold the line on -- claiming under a row lock, the partial unique index on
one ACTIVE wait per conversation, and the resume run are all SQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.infrastructure.work import WorkWriter
from app.modules.agent.domain.pausing_tools import WAIT_TOOL_NAME
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    MessageDraft,
)
from app.modules.agent.domain.wait import (
    AgentConversationWaitEntity,
    AgentWaitStatus,
    AgentWaitType,
    AgentWaitWakeReason,
)
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.wait_timers import arm_wait
from app.modules.agent.services.wait_wake_service import AgentWaitService
from app.modules.test_support.e2e.agent_work import run_wait_work

pytestmark = [pytest.mark.e2e]


@pytest.fixture
async def waiting_conversation(authenticated_client, fixed_test_org) -> dict:
    """A pod, an agent and a conversation to hang waits on."""
    pod = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Wait Pod {uuid4().hex[:8]}",
            "description": "wait resolution e2e",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod.status_code == 201, pod.text
    pod_id = pod.json()["id"]

    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Wait Agent",
            "instruction": "Answer in plain text.",
            "agent_runtime": {"profile_id": "system:lemma"},
        },
    )
    assert agent.status_code == 201, agent.text

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": "wait_agent", "title": "Waiting", "type": "CHAT"},
    )
    assert conversation.status_code == 201, conversation.text
    return {
        "pod_id": UUID(pod_id),
        "conversation_id": UUID(conversation.json()["id"]),
    }


async def _a_wait(
    waiting_conversation: dict,
    *,
    wait_type: AgentWaitType,
    target_ref: str,
    scheduled_in: timedelta = timedelta(seconds=10),
    deadline_in: timedelta = timedelta(hours=1),
) -> AgentConversationWaitEntity:
    """A suspended turn: the pausing tool call, and the wait row that wakes it."""
    now = datetime.now(timezone.utc)
    tool_call_id = f"call-{uuid4().hex[:12]}"
    async with create_uow_from_session_maker(async_session_maker) as uow:
        messages = Messages(uow)
        runs = Runs(uow)
        run = await runs.create_agent_run(
            conversation_id=waiting_conversation["conversation_id"],
            agent_id=None,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
            metadata={"source": "e2e_wait_resolution"},
        )
        await messages.append_message(
            conversation_id=waiting_conversation["conversation_id"],
            agent_run_id=run.id,
            draft=MessageDraft.of_tool_call(
                tool_call_id=tool_call_id,
                tool_name=WAIT_TOOL_NAME,
                tool_args={"reason": "waiting"},
            ),
        )
        wait = AgentConversationWaitEntity(
            conversation_id=waiting_conversation["conversation_id"],
            agent_run_id=run.id,
            pod_id=waiting_conversation["pod_id"],
            tool_call_id=tool_call_id,
            wait_type=wait_type,
            external_ref=str(uuid4()),
            scheduled_at=now + scheduled_in,
            spec={
                "reason": "waiting",
                "started_at": now.isoformat(),
                "deadline_at": (now + deadline_in).isoformat(),
                "target_ref": target_ref,
                "user_id": str(uuid4()),
            },
        )
        await AgentConversationWaitRepository(uow).create(wait)
        await arm_wait(WorkWriter.of(uow), wait, wait.scheduled_at)
        await uow.commit()
    return wait


async def _reload(wait_id: UUID) -> AgentConversationWaitEntity | None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        return await AgentConversationWaitRepository(uow).get(wait_id)


@pytest.mark.drives_work
async def test_a_process_whose_sandbox_is_gone_wakes_rather_than_waiting_out_the_hour(
    waiting_conversation,
) -> None:
    """The sandbox was reclaimed, so the outcome can never be learned.

    Sitting until the one-hour ceiling would be the worst answer available: the
    agent waits an hour to be told nothing, and the person watching sees a
    conversation that looks alive and is not. Driven through its
    ``agent.wait.poll`` item, which probes outside any transaction.
    """
    wait = await _a_wait(
        waiting_conversation,
        wait_type=AgentWaitType.PROCESS,
        # No sandbox was ever provisioned for this user, so the probe can find
        # neither the process nor anything that could still be running it.
        target_ref=str(uuid4()),
    )

    assert await run_wait_work(due_now=True) == 1

    resolved = await _reload(wait.id)
    assert resolved is not None
    assert resolved.status is AgentWaitStatus.COMPLETED
    assert resolved.spec["woke_because"] == AgentWaitWakeReason.TARGET_GONE.value


@pytest.mark.drives_work
async def test_a_subagent_run_that_no_longer_exists_wakes_as_gone(
    waiting_conversation,
) -> None:
    """A child conversation deleted underneath its parent is the same shape."""
    wait = await _a_wait(
        waiting_conversation,
        wait_type=AgentWaitType.SUBAGENT,
        target_ref=str(uuid4()),
    )

    assert await run_wait_work(due_now=True) == 1

    resolved = await _reload(wait.id)
    assert resolved is not None
    assert resolved.status is AgentWaitStatus.COMPLETED
    assert resolved.spec["woke_because"] == AgentWaitWakeReason.TARGET_GONE.value


async def test_a_second_resolution_does_not_start_a_second_run(
    waiting_conversation,
) -> None:
    """A late or duplicate fire is the common race: the row lock settles it.

    Whichever resolution gets there second sees a non-ACTIVE row and stops.
    """
    wait = await _a_wait(
        waiting_conversation,
        wait_type=AgentWaitType.SUBAGENT,
        target_ref=str(uuid4()),
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        first = await AgentWaitService(uow).fire(wait.id)
        await uow.commit()
    async with create_uow_from_session_maker(async_session_maker) as uow:
        second = await AgentWaitService(uow).fire(wait.id)
        await uow.commit()

    assert (first, second) == (True, False)


@pytest.mark.drives_work
async def test_a_wait_fires_when_due_and_its_item_goes_with_it(
    waiting_conversation,
) -> None:
    """Due now, the item fires it once; nothing of the wait is left queued."""
    wait = await _a_wait(
        waiting_conversation,
        wait_type=AgentWaitType.TIME,
        target_ref="",
        scheduled_in=-timedelta(seconds=1),
    )

    assert await run_wait_work() == 1
    assert await run_wait_work() == 0

    resolved = await _reload(wait.id)
    assert resolved is not None
    assert resolved.status is AgentWaitStatus.COMPLETED
    assert resolved.spec["woke_because"] == AgentWaitWakeReason.TIMER.value


@pytest.mark.drives_work
async def test_a_healthy_future_wait_is_left_alone(
    waiting_conversation,
) -> None:
    """Its item is not due, so nothing runs it early.

    Waking early would read as the feature working -- the agent does come
    back -- while removing the whole point of it.
    """
    wait = await _a_wait(
        waiting_conversation,
        wait_type=AgentWaitType.PROCESS,
        target_ref=str(uuid4()),
        scheduled_in=timedelta(minutes=30),
    )

    assert await run_wait_work() == 0

    untouched = await _reload(wait.id)
    assert untouched is not None
    assert untouched.status is AgentWaitStatus.ACTIVE
