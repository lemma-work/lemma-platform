"""A wait always resolves, including when what it waited on cannot answer.

The happy paths are covered where the decision is made, against a pure function.
What needs a database is the other half: that a wait whose target has vanished
still wakes the agent with something truthful, and that a wait whose scheduler
event never arrived is picked up by the sweep rather than sitting ACTIVE and
past-due for the life of the deployment.

These are the failure paths `PS-AGENT-023` promises and that a fake repository
cannot hold the line on — claiming under a row lock, the partial unique index on
one ACTIVE wait per conversation, and the resume run are all SQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
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
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.wait_reconcile_service import (
    RECONCILE_AFTER,
    WaitReconcileService,
)
from app.modules.agent.services.wait_wake_service import AgentWaitService

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
        repo = ConversationRepository(uow)
        run = await repo.create_agent_run(
            conversation_id=waiting_conversation["conversation_id"],
            agent_id=None,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
            metadata={"source": "e2e_wait_resolution"},
        )
        await repo.append_message(
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
        await uow.commit()
    return wait


async def _reload(wait_id: UUID) -> AgentConversationWaitEntity | None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        return await AgentConversationWaitRepository(uow).get(wait_id)


async def test_a_process_whose_sandbox_is_gone_wakes_rather_than_waiting_out_the_hour(
    waiting_conversation,
) -> None:
    """The sandbox was reclaimed, so the outcome can never be learned.

    Sitting until the one-hour ceiling would be the worst answer available: the
    agent waits an hour to be told nothing, and the person watching sees a
    conversation that looks alive and is not.
    """
    wait = await _a_wait(
        waiting_conversation,
        wait_type=AgentWaitType.PROCESS,
        # No sandbox was ever provisioned for this user, so the probe can find
        # neither the process nor anything that could still be running it.
        target_ref=str(uuid4()),
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        resumed = await AgentWaitService(uow).resolve(wait=wait)
        await uow.commit()

    assert resumed is True, "a process that cannot be read must still wake the agent"
    resolved = await _reload(wait.id)
    assert resolved is not None
    assert resolved.status is AgentWaitStatus.COMPLETED
    assert resolved.spec["woke_because"] == AgentWaitWakeReason.TARGET_GONE.value


async def test_a_subagent_run_that_no_longer_exists_wakes_as_gone(
    waiting_conversation,
) -> None:
    """A child conversation deleted underneath its parent is the same shape."""
    wait = await _a_wait(
        waiting_conversation,
        wait_type=AgentWaitType.SUBAGENT,
        target_ref=str(uuid4()),
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        resumed = await AgentWaitService(uow).resolve(wait=wait)
        await uow.commit()

    assert resumed is True
    resolved = await _reload(wait.id)
    assert resolved is not None
    assert resolved.spec["woke_because"] == AgentWaitWakeReason.TARGET_GONE.value


async def test_a_second_resolution_does_not_start_a_second_run(
    waiting_conversation,
) -> None:
    """The sweep and the timer race on every wait, so this is the common case.

    The claim is a row lock, which is why this needs the database: whichever
    resolution gets there second sees a non-ACTIVE row and stops.
    """
    wait = await _a_wait(
        waiting_conversation,
        wait_type=AgentWaitType.SUBAGENT,
        target_ref=str(uuid4()),
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        first = await AgentWaitService(uow).resolve(wait=wait)
        await uow.commit()
    async with create_uow_from_session_maker(async_session_maker) as uow:
        second = await AgentWaitService(uow).resolve(wait=wait)
        await uow.commit()

    assert (first, second) == (True, False)


async def test_the_sweep_resolves_a_wait_whose_scheduler_event_never_arrived(
    waiting_conversation,
) -> None:
    """The backstop `PS-AGENT-023` promises: a lost fire must not strand a run.

    Overdue by more than the grace period, because firing the moment
    `scheduled_at` passes would race the real timer on every healthy wait.
    """
    wait = await _a_wait(
        waiting_conversation,
        wait_type=AgentWaitType.SUBAGENT,
        target_ref=str(uuid4()),
        scheduled_in=-(RECONCILE_AFTER + timedelta(minutes=5)),
    )

    woken = await WaitReconcileService().reconcile_due_waits()

    assert woken >= 1, "the sweep left a past-due wait ACTIVE"
    resolved = await _reload(wait.id)
    assert resolved is not None
    assert resolved.status is AgentWaitStatus.COMPLETED


async def test_a_healthy_future_wait_is_left_alone_by_the_sweep(
    waiting_conversation,
) -> None:
    """The other half of the grace period, and the one that would go unnoticed.

    A sweep that fired eagerly would wake every wait early, which reads as the
    feature working — the agent does come back — while removing the whole point
    of it.
    """
    wait = await _a_wait(
        waiting_conversation,
        wait_type=AgentWaitType.PROCESS,
        target_ref=str(uuid4()),
        scheduled_in=timedelta(minutes=30),
    )

    await WaitReconcileService().reconcile_due_waits()

    untouched = await _reload(wait.id)
    assert untouched is not None
    assert untouched.status is AgentWaitStatus.ACTIVE
