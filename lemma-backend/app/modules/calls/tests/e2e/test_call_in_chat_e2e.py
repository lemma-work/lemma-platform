"""A call started from a chat is held in that chat.

It used to open a separate "Call with ..." conversation every time: the person
stayed on the chat they were in while the call's turns, the agent's work and
the transcript landed in one they were not looking at, and the agent knew the
chat only as a summary pasted under its first turn. Over HTTP against the real
repositories: a call opened with ``conversation_id`` is that conversation, is
picked up again when opened again, and is refused for a conversation that is
not the caller's own chat.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import update
from starlette import status

from app.core.infrastructure.db.session import async_session_maker
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.calls.config import calls_settings

pytestmark = pytest.mark.e2e


async def _chat(scenario, title: str) -> str:
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations",
        json={"title": title, "type": "CHAT"},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return created.json()["id"]


async def test_a_call_from_a_chat_is_held_in_that_chat_and_picked_up_again(
    scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calls_settings, "gemini_api_key", "test-key-unused")
    await scenario.create_org_with_pod(name_prefix="calls-in-chat")
    pod_id = scenario.pod_id
    chat_id = await _chat(scenario, "Planning the offsite")

    opened = await scenario.owner_client.post(
        f"/pods/{pod_id}/calls",
        json={"conversation_id": chat_id, "display_name": "Ria"},
    )
    assert opened.status_code == status.HTTP_201_CREATED, opened.text
    assert opened.json()["call_id"] == chat_id

    chat = await scenario.owner_client.get(f"/pods/{pod_id}/conversations/{chat_id}")
    assert chat.status_code == status.HTTP_200_OK, chat.text
    body = chat.json()
    # Still the chat it was: its own title, no call brief in its instructions.
    assert body["title"] == "Planning the offsite"
    mark = body["metadata"]["call"]
    assert mark["call_id"] == chat_id and mark["role"] == "coordinator"
    # Not live until a socket holds it, and the chat records when it became one.
    assert mark["live"] is False and mark["since"]

    again = await scenario.owner_client.post(
        f"/pods/{pod_id}/calls",
        json={"conversation_id": chat_id, "display_name": "Ria"},
    )
    assert again.status_code == status.HTTP_201_CREATED, again.text
    assert again.json()["call_id"] == chat_id
    assert (
        await scenario.owner_client.get(f"/pods/{pod_id}/conversations/{chat_id}")
    ).json()["metadata"]["call"]["since"] == mark["since"]


async def test_a_call_is_refused_in_a_chat_that_is_not_the_callers_own(
    scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calls_settings, "gemini_api_key", "test-key-unused")
    await scenario.create_org_with_pod(name_prefix="calls-not-own")
    pod_id = scenario.pod_id
    chat_id = await _chat(scenario, "Mine")
    colleague = await scenario.create_user("calls-other")
    await scenario.add_user_to_pod(user=colleague, role="POD_EDITOR")

    theirs = await scenario.owner_client.post(
        f"/pods/{pod_id}/calls",
        json={"conversation_id": chat_id},
        headers={"Authorization": f"Bearer {colleague['token']}"},
    )
    assert theirs.status_code == status.HTTP_404_NOT_FOUND, theirs.text

    # A sub-agent's conversation belongs to its parent's work, not to a call.
    sub_agent = await _chat(scenario, "A sub-agent")
    async with async_session_maker() as session:
        await session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == UUID(sub_agent))
            .values(parent_id=UUID(chat_id))
        )
        await session.commit()
    refused = await scenario.owner_client.post(
        f"/pods/{pod_id}/calls", json={"conversation_id": sub_agent}
    )
    assert refused.status_code == status.HTTP_409_CONFLICT, refused.text


async def test_a_resumed_call_reads_back_its_whole_tree_and_what_it_missed(
    scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What a reconnect reads, against real rows: every conversation in the
    call by its mark (a sub-agent's own sub-agent included), the relays sent
    after the last transcript was written, and the held time it adds."""
    from datetime import UTC, datetime, timedelta

    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.agent.contracts.calls import (
        CALL_TRANSCRIPT_KEY,
        call_conversations,
        held_call,
        missed_relays,
        set_call_live,
    )
    from app.modules.agent.infrastructure.models import MessageModel

    monkeypatch.setattr(calls_settings, "gemini_api_key", "test-key-unused")
    await scenario.create_org_with_pod(name_prefix="calls-resume")
    pod_id = scenario.pod_id
    call_id = await _chat(scenario, "The call")
    opened = await scenario.owner_client.post(
        f"/pods/{pod_id}/calls", json={"conversation_id": call_id}
    )
    assert opened.status_code == status.HTTP_201_CREATED, opened.text
    worker = {"call_id": call_id, "role": "worker", "live": True}
    child = await _chat(scenario, "A sub-agent")
    grandchild = await _chat(scenario, "Its sub-agent")
    unrelated = await _chat(scenario, "Another chat")
    now = datetime.now(UTC)
    async with async_session_maker() as session:
        for conversation_id in (child, grandchild):
            await session.execute(
                update(ConversationModel)
                .where(ConversationModel.id == UUID(conversation_id))
                .values(
                    conversation_metadata={"call": worker},
                    parent_id=UUID(child if conversation_id == grandchild else call_id),
                )
            )
        session.add_all(
            [
                MessageModel(
                    conversation_id=UUID(call_id),
                    sequence=1,
                    role="assistant",
                    kind="NOTIFICATION",
                    text="Said on the call -- ...",
                    message_metadata={CALL_TRANSCRIPT_KEY: "t1"},
                    created_at=now,
                ),
                MessageModel(
                    conversation_id=UUID(call_id),
                    sequence=2,
                    role="assistant",
                    kind="TOOL_CALL",
                    tool_name="relay_to_voice",
                    tool_call_id="r1",
                    tool_args={"text": "Found three venues.", "urgency": "now"},
                    created_at=now + timedelta(seconds=5),
                ),
            ]
        )
        await session.commit()

    unit = SessionUnitOfWorkFactory(async_session_maker)
    async with unit() as uow:
        tree = await call_conversations(uow, UUID(call_id))
        assert set(map(str, tree)) == {call_id, child, grandchild}
        assert unrelated not in map(str, tree)
        assert await missed_relays(uow, UUID(call_id)) == [
            ("Found three venues.", "now")
        ]
        await set_call_live(uow, UUID(call_id), live=True)
        await set_call_live(uow, UUID(call_id), live=False, held_for=90.0)
    async with unit() as uow:
        mark = await held_call(uow, UUID(call_id))
    assert mark is not None and mark.live is False
    assert mark.held_seconds == pytest.approx(90.0)
