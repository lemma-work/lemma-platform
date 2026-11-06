"""A conversation's status moves with its run, and nothing writes it back.

The run's finish writes the conversation's status in the same transaction, so
the two cannot drift by being written apart. What could still drift them was a
whole-entity save: a rename or an auto-title reads the conversation while its
run is going, and saving that entity after the run finished wrote RUNNING back
over the finished conversation, where nothing would ever move it again. A sweep
used to look for exactly those and repair them; these hold the two writers that
remain to never produce one.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    AgentRuntimeConfig,
    ConversationStatus,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.runs import Runs

pytestmark = [pytest.mark.e2e]


@pytest.fixture
async def conversation_id(authenticated_client, fixed_test_org) -> UUID:
    pod = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Status Pod {uuid4().hex[:8]}",
            "description": "conversation status e2e",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod.status_code == 201, pod.text
    pod_id = pod.json()["id"]
    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Status Agent",
            "instruction": "Answer in plain text.",
            "agent_runtime": {"profile_id": "system:lemma"},
        },
    )
    assert agent.status_code == 201, agent.text
    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": "status_agent", "title": "Status", "type": "CHAT"},
    )
    assert conversation.status_code == 201, conversation.text
    return UUID(conversation.json()["id"])


async def _start_run(conversation_id: UUID) -> UUID:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        run = await Runs(uow).create_agent_run(
            conversation_id=conversation_id,
            agent_id=None,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
        )
        await uow.commit()
    return run.id


async def _finish(run_id: UUID):
    async with create_uow_from_session_maker(async_session_maker) as uow:
        result = await Runs(uow).finish_agent_run(
            agent_run_id=run_id, status=AgentRunStatus.COMPLETED
        )
        await uow.commit()
    return result


async def _stored_status(conversation_id: UUID) -> ConversationStatus | None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        conversation = await Conversations(uow).get_conversation(conversation_id)
    assert conversation is not None
    return conversation.status


async def test_saving_an_entity_read_mid_run_does_not_reopen_the_conversation(
    conversation_id,
) -> None:
    run_id = await _start_run(conversation_id)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        stale = await Conversations(uow).get_conversation(conversation_id)
    assert stale is not None and stale.status is ConversationStatus.RUNNING

    # The run ends while the rename is still holding what it read.
    await _finish(run_id)

    stale.title = "Renamed while it ran"
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await Conversations(uow).update_conversation(stale)
        await uow.commit()

    assert await _stored_status(conversation_id) is ConversationStatus.COMPLETED


async def test_a_second_finish_reports_the_stored_status_and_writes_nothing(
    conversation_id,
) -> None:
    run_id = await _start_run(conversation_id)
    first = await _finish(run_id)
    assert first is not None and first.updated

    second = await _finish(run_id)

    assert second is not None
    assert second.updated is False
    assert second.conversation_status is ConversationStatus.COMPLETED
    assert await _stored_status(conversation_id) is ConversationStatus.COMPLETED
