"""The history list is ordered by last activity, and pages through all of it.

Against a real database because both halves live in SQL: the order is an index
range over `(last_activity_at, id)`, and the stamp that moves a conversation up
is written by ``append_message`` under the row lock every writer takes.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.value_objects import MessageDraft, MessageRole
from app.modules.agent.infrastructure.repositories import ConversationRepository

pytestmark = pytest.mark.e2e


async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Activity Pod {uuid4().hex[:8]}",
            "description": "Conversation activity order E2E pod",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _create_conversation(authenticated_client, pod_id: str) -> str:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_runtime": {"profile_id": "system:lemma"}},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _say_something(conversation_id: str) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await ConversationRepository(uow).append_message(
            conversation_id=UUID(conversation_id),
            agent_run_id=None,
            draft=MessageDraft.of_text("still here", role=MessageRole.USER),
        )
        await uow.commit()


async def _all_pages(authenticated_client, pod_id: str, *, limit: int) -> list[str]:
    ids: list[str] = []
    token: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": limit}
        if token is not None:
            params["page_token"] = token
        response = await authenticated_client.get(
            f"/pods/{pod_id}/conversations", params=params
        )
        assert response.status_code == 200, response.text
        body = response.json()
        ids.extend(item["id"] for item in body["items"])
        token = body["next_page_token"]
        if token is None:
            return ids


class TestConversationActivityOrder:
    async def test_a_new_message_moves_an_older_conversation_to_the_top(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        pod_id = await _create_pod(authenticated_client, fixed_test_org)
        first = await _create_conversation(authenticated_client, pod_id)
        second = await _create_conversation(authenticated_client, pod_id)

        assert await _all_pages(authenticated_client, pod_id, limit=20) == [
            second,
            first,
        ]

        await _say_something(first)

        assert await _all_pages(authenticated_client, pod_id, limit=20) == [
            first,
            second,
        ]

    async def test_paging_visits_every_conversation_once_in_order(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        pod_id = await _create_pod(authenticated_client, fixed_test_org)
        created = [
            await _create_conversation(authenticated_client, pod_id) for _ in range(5)
        ]
        # Bring the oldest two back up, so the order is neither creation order
        # nor its reverse.
        await _say_something(created[0])
        await _say_something(created[1])

        paged = await _all_pages(authenticated_client, pod_id, limit=2)

        assert paged == [created[1], created[0], created[4], created[3], created[2]]

    async def test_a_bare_conversation_id_is_not_a_page_token(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        pod_id = await _create_pod(authenticated_client, fixed_test_org)

        response = await authenticated_client.get(
            f"/pods/{pod_id}/conversations", params={"page_token": str(uuid4())}
        )

        assert response.status_code == 400, response.text
