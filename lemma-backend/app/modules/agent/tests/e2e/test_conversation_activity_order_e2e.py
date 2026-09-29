"""The history list is ordered by last activity, and pages through all of it.

Against a real database because both halves live in SQL: the order is an index
range over `(last_activity_at, id)`, and the stamp that moves a conversation up
is written by ``append_message`` under the row lock every writer takes -- for a
person's message, not for the agent's own.
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


async def _create_conversation(
    authenticated_client, pod_id: str, *, title: str | None = None
) -> str:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_runtime": {"profile_id": "system:lemma"}, "title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _say_something(
    conversation_id: str,
    draft: MessageDraft = MessageDraft.of_text("still here", role=MessageRole.USER),
) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await ConversationRepository(uow).append_message(
            conversation_id=UUID(conversation_id),
            agent_run_id=None,
            draft=draft,
        )
        await uow.commit()


async def _all_pages(
    authenticated_client,
    pod_id: str,
    *,
    limit: int,
    search: str | None = None,
    token: str | None = None,
) -> list[str]:
    ids: list[str] = []
    while True:
        params: dict[str, str | int] = {"limit": limit}
        if search is not None:
            params["search"] = search
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

    async def test_the_agent_working_does_not_move_a_conversation(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        pod_id = await _create_pod(authenticated_client, fixed_test_org)
        first = await _create_conversation(authenticated_client, pod_id)
        second = await _create_conversation(authenticated_client, pod_id)

        # One tool call and its result per step of a run: stamping each would
        # rewrite the conversation row in every index for nothing visible.
        await _say_something(first, MessageDraft.of_text("working on it"))
        await _say_something(
            first, MessageDraft.of_tool_call(tool_name="search", tool_call_id="c1")
        )
        await _say_something(
            first, MessageDraft.of_tool_return(tool_call_id="c1", tool_result="ok")
        )

        assert await _all_pages(authenticated_client, pod_id, limit=20) == [
            second,
            first,
        ]

    async def test_search_pages_through_matching_titles_only(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        pod_id = await _create_pod(authenticated_client, fixed_test_org)
        matching = [
            await _create_conversation(
                authenticated_client, pod_id, title=f"Launch plan {n}"
            )
            for n in range(3)
        ]
        await _create_conversation(authenticated_client, pod_id, title="Pricing")
        # A literal `%`: it matches itself, not everything.
        await _create_conversation(authenticated_client, pod_id, title="100% done")

        found = await _all_pages(authenticated_client, pod_id, limit=2, search="LAUNCH")
        literal = await _all_pages(authenticated_client, pod_id, limit=20, search="%")

        assert found == list(reversed(matching))
        assert len(literal) == 1

    async def test_a_bare_conversation_id_token_continues_after_that_row(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        # What this endpoint handed out while it was ordered by id alone: a
        # client paging across the deploy carries on rather than failing.
        pod_id = await _create_pod(authenticated_client, fixed_test_org)
        created = [
            await _create_conversation(authenticated_client, pod_id) for _ in range(3)
        ]

        after_newest = await _all_pages(
            authenticated_client, pod_id, limit=20, token=created[2]
        )

        assert after_newest == [created[1], created[0]]

    async def test_an_unknown_bare_id_is_not_a_page_token(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        pod_id = await _create_pod(authenticated_client, fixed_test_org)

        response = await authenticated_client.get(
            f"/pods/{pod_id}/conversations", params={"page_token": str(uuid4())}
        )

        assert response.status_code == 400, response.text
