"""Addressing an agent in a conversation, against a real database.

`@mention` routing has one rule that only shows up end-to-end: the agent it
names is not the conversation's own, so every access check between the request
and the run has to accept an agent that is merely *present*. Any one of them
refusing turns a mention into a message that vanishes -- which is exactly how
this shipped broken.

The negative case matters as much: naming an agent nobody added must be refused,
or a name typed into a room reaches an agent that was never in it.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.infrastructure.repositories import ConversationRepository

pytestmark = pytest.mark.e2e


async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Addressing Pod {uuid4().hex[:8]}",
            "description": "Agent addressing E2E pod",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _create_agent(authenticated_client, pod_id: str, name: str) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": name,
            "instruction": "You are a test agent. Reply with one short line.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_conversation(authenticated_client, pod_id: str) -> str:
    """A conversation on the pod's default assistant, not on the named agent."""
    response = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_runtime": {"profile_id": "system:lemma"}},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


class TestListingWhatYouAreIn:
    async def test_a_conversation_you_were_added_to_appears_in_your_list(
        self, authenticated_client, fixed_test_org
    ) -> None:
        """Owner-only listing is what made being added to a conversation
        useless: you could open it from a link and never find it again."""
        pod_id = await _create_pod(authenticated_client, fixed_test_org)
        conversation_id = await _create_conversation(authenticated_client, pod_id)

        listed = await authenticated_client.get(f"/pods/{pod_id}/conversations")
        assert listed.status_code == 200, listed.text
        # The opener sees it because they own it; the participant row backfilled
        # for them means the same query finds it either way.
        assert conversation_id in {item["id"] for item in listed.json()["items"]}

    async def test_an_agent_participant_does_not_put_it_in_somebody_elses_list(
        self, authenticated_client, fixed_test_org
    ) -> None:
        """Agents share the participants table with people. Matching on the
        table rather than on `user_id` would list a conversation for everyone
        the moment an agent joined it."""
        pod_id = await _create_pod(authenticated_client, fixed_test_org)
        await _create_agent(authenticated_client, pod_id, "scout")
        conversation_id = await _create_conversation(authenticated_client, pod_id)
        added = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}/participants",
            json={"agent_name": "scout"},
        )
        assert added.status_code in (200, 201), added.text

        listed = await authenticated_client.get(f"/pods/{pod_id}/conversations")

        ids = [item["id"] for item in listed.json()["items"]]
        assert ids.count(conversation_id) == 1, "an agent must not duplicate the row"


class TestAddressingAnAgent:
    async def test_an_agent_in_the_conversation_can_be_addressed(
        self, authenticated_client, fixed_test_org
    ) -> None:
        pod_id = await _create_pod(authenticated_client, fixed_test_org)
        agent = await _create_agent(authenticated_client, pod_id, "batman")
        conversation_id = await _create_conversation(authenticated_client, pod_id)

        added = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}/participants",
            json={"agent_name": "batman"},
        )
        assert added.status_code in (200, 201), added.text

        response = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages/append",
            json={"content": "@batman hi", "agent_name": "batman"},
        )

        # The message must land, and it must land on *batman* -- a 200 alone
        # proves nothing, because the pod's default assistant answering is also
        # a 200. That weaker assertion is what let this ship broken.
        assert response.status_code == 200, response.text
        run_id = response.json()["agent_run_id"]
        assert run_id, response.text

        async with create_uow_from_session_maker(async_session_maker) as uow:
            run = await ConversationRepository(uow).get_agent_run(UUID(run_id))
        assert run is not None
        assert str(run.agent_id) == agent["id"]

    async def test_an_agent_that_was_never_added_is_refused(
        self, authenticated_client, fixed_test_org
    ) -> None:
        pod_id = await _create_pod(authenticated_client, fixed_test_org)
        await _create_agent(authenticated_client, pod_id, "joker")
        conversation_id = await _create_conversation(authenticated_client, pod_id)

        response = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages/append",
            json={"content": "@joker hi", "agent_name": "joker"},
        )

        assert response.status_code == 404, response.text
