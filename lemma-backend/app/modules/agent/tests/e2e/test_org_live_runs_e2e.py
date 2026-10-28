"""A conversation running, on the organization's live socket -- real websocket,
Postgres and Redis.

A run starts and ends through the conversation repository, the path every turn
takes; the ``org`` topic hears its conversation's status move and tells the
conversation's owner, and nobody else in the pod.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.value_objects import AgentRunStatus, AgentRuntimeConfig
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.test_support.e2e.live_socket import LiveClient, org_socket

pytestmark = [pytest.mark.e2e]


async def _start_run(conversation_id: UUID) -> UUID:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        run = await ConversationRepository(uow).create_agent_run(
            conversation_id=conversation_id,
            agent_id=None,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
        )
        await uow.commit()
    return run.id


async def _finish(run_id: UUID) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await ConversationRepository(uow).finish_agent_run(
            agent_run_id=run_id, status=AgentRunStatus.COMPLETED
        )
        await uow.commit()


async def _subscribed(live: LiveClient) -> None:
    answer = await live.subscribe("o", "org")
    assert answer["type"] == "subscribed", answer
    await live.event("o", "pods")


async def test_a_running_conversation_reaches_its_owner_alone(
    scenario, backend_server
) -> None:
    await scenario.create_org_with_pod(name_prefix="Org live runs")
    member = await scenario.create_user("org-live-runs-member")
    await scenario.add_user_to_pod(user=member, role="POD_EDITOR")
    made = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations",
        json={"title": "Plans", "type": "CHAT"},
    )
    assert made.status_code == 201, made.text
    conversation_id = made.json()["id"]

    async with (
        org_socket(
            backend_server, scenario.org_id, scenario.owner_user["token"]
        ) as owner,
        org_socket(backend_server, scenario.org_id, member["token"]) as other,
    ):
        await _subscribed(owner)
        await _subscribed(other)

        run_id = await _start_run(UUID(conversation_id))
        started = await owner.event(
            "o", "conversation.running", conversation_id=conversation_id, running=True
        )
        assert started["pod_id"] == scenario.pod_id
        assert started["agent_run_id"] == str(run_id)

        await _finish(run_id)
        await owner.event(
            "o", "conversation.running", conversation_id=conversation_id, running=False
        )
        await other.quiet()

    assert not other.events("o", "conversation.running")
