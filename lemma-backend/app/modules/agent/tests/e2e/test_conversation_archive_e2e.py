"""Archiving a conversation, against a real database.

Archiving is what this product has instead of deleting a conversation, so the
behaviour worth proving end-to-end is not that a flag flips -- it is that the
flag decides which list a conversation appears in, and that it cannot leave a
live conversation hidden. The un-archive-on-activity rule lives in
``append_message``, below every service that writes one, and this is the level
at which it can be observed at all.
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
            "name": f"Archive Pod {uuid4().hex[:8]}",
            "description": "Conversation archive E2E pod",
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


async def _listed_ids(authenticated_client, pod_id: str, *, archived: bool) -> set[str]:
    response = await authenticated_client.get(
        f"/pods/{pod_id}/conversations",
        params={"archived": str(archived).lower()},
    )
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


async def _say_something(conversation_id: str, text: str) -> None:
    """A message arriving by any path -- the repository is what they all share."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await ConversationRepository(uow).append_message(
            conversation_id=UUID(conversation_id),
            agent_run_id=None,
            draft=MessageDraft.of_text(text, role=MessageRole.USER),
        )
        await uow.commit()


class TestConversationArchive:
    async def test_archiving_moves_a_conversation_between_the_two_lists(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        pod_id = await _create_pod(authenticated_client, fixed_test_org)
        conversation_id = await _create_conversation(authenticated_client, pod_id)

        assert conversation_id in await _listed_ids(
            authenticated_client, pod_id, archived=False
        )

        archived = await authenticated_client.patch(
            f"/pods/{pod_id}/conversations/{conversation_id}",
            json={"is_archived": True},
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["is_archived"] is True

        # Out of the history, into the archive -- and still fetchable by id,
        # because archiving hides a conversation rather than ending it.
        assert conversation_id not in await _listed_ids(
            authenticated_client, pod_id, archived=False
        )
        assert conversation_id in await _listed_ids(
            authenticated_client, pod_id, archived=True
        )

        fetched = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["is_archived"] is True

        restored = await authenticated_client.patch(
            f"/pods/{pod_id}/conversations/{conversation_id}",
            json={"is_archived": False},
        )
        assert restored.status_code == 200, restored.text
        assert conversation_id in await _listed_ids(
            authenticated_client, pod_id, archived=False
        )

    async def test_a_new_message_brings_an_archived_conversation_back(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        """The case that makes archiving safe for a Slack thread: the origin
        keeps receiving whatever the list says, so the list has to follow."""
        pod_id = await _create_pod(authenticated_client, fixed_test_org)
        conversation_id = await _create_conversation(authenticated_client, pod_id)

        await authenticated_client.patch(
            f"/pods/{pod_id}/conversations/{conversation_id}",
            json={"is_archived": True},
        )
        assert conversation_id in await _listed_ids(
            authenticated_client, pod_id, archived=True
        )

        await _say_something(conversation_id, "actually, one more thing")

        assert conversation_id in await _listed_ids(
            authenticated_client, pod_id, archived=False
        )
        assert conversation_id not in await _listed_ids(
            authenticated_client, pod_id, archived=True
        )

    async def test_renaming_stores_the_typed_title_and_clearing_hands_it_back(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        pod_id = await _create_pod(authenticated_client, fixed_test_org)
        conversation_id = await _create_conversation(authenticated_client, pod_id)

        renamed = await authenticated_client.patch(
            f"/pods/{pod_id}/conversations/{conversation_id}",
            json={"title": "  Tokyo food tour  "},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["title"] == "Tokyo food tour"

        # Blank is not a title: it clears the field, which is what makes the
        # conversation eligible for auto-titling again.
        cleared = await authenticated_client.patch(
            f"/pods/{pod_id}/conversations/{conversation_id}",
            json={"title": "   "},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["title"] is None

        too_long = await authenticated_client.patch(
            f"/pods/{pod_id}/conversations/{conversation_id}",
            json={"title": "t" * 256},
        )
        assert too_long.status_code == 400, too_long.text
        assert too_long.json()["code"] == "CONVERSATION_VALIDATION_ERROR"
