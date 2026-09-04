"""A pause stays findable however long the conversation gets.

Three questions -- "what is waiting to be approved", "which pause does this
typed reply answer", and "which pause does a new message supersede" -- used to be
answered by loading the newest 500 messages and filtering them in Python. A long
agent run writes hundreds of tool messages, so past that window all three
answered *nothing pending*: the approval card disappeared, a reply had nowhere to
go, and the pause was never superseded either, so the next history rebuild
dropped the model's memory of ever having asked.

That is a wrong answer rather than a slow one, which is why a fake repository
cannot hold the line here -- a stand-in hands back whatever list the test gave
it, and the window only exists in SQL. The fourth path,
`unresolved_pausing_call_ids`, was already fixed this way; these are the other
three.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.pausing_tools import (
    PAUSING_TOOL_NAMES,
    USER_PAUSING_TOOL_NAMES,
)
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    AgentRuntimeConfig,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.models import MessageModel
from app.modules.agent.infrastructure.repositories import ConversationRepository

pytestmark = [pytest.mark.e2e]

#: Comfortably past the 500-message window the three scans used to apply, so a
#: reintroduced window cannot pass by luck.
_MESSAGES_AFTER_THE_PAUSE = 520


@pytest.fixture
async def conversation_for_query(authenticated_client, fixed_test_org) -> UUID:
    """One conversation, in its own pod, for the pauses in this module."""
    pod = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Approval Window Pod {uuid4().hex[:8]}",
            "description": "pending-approval window e2e",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod.status_code == 201, pod.text
    pod_id = pod.json()["id"]

    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Window Agent",
            "instruction": "Answer in plain text.",
            "agent_runtime": {"profile_id": "system:lemma"},
        },
    )
    assert agent.status_code == 201, agent.text

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": "window_agent", "title": "Window", "type": "CHAT"},
    )
    assert conversation.status_code == 201, conversation.text
    return UUID(conversation.json()["id"])


async def _pause_then_bury_it(conversation_id: UUID, *, tool_name: str) -> str:
    """Record a pausing tool call, then write enough messages to bury it."""
    tool_call_id = f"call-{uuid4().hex[:12]}"
    async with create_uow_from_session_maker(async_session_maker) as uow:
        repo = ConversationRepository(uow)
        run = await repo.create_agent_run(
            conversation_id=conversation_id,
            agent_id=None,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
            metadata={"source": "e2e_approval_window"},
        )
        await repo.append_message(
            conversation_id=conversation_id,
            agent_run_id=run.id,
            draft=MessageDraft.of_tool_call(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_args={"question": "may I?"},
            ),
        )
        # Written straight in rather than through `append_message`: the filler is
        # scenery, and five hundred locked round trips per test is the whole
        # runtime of this file.
        next_sequence = (
            int(
                (
                    await uow.session.execute(
                        select(
                            func.coalesce(func.max(MessageModel.sequence), -1)
                        ).where(MessageModel.conversation_id == conversation_id)
                    )
                ).scalar_one()
            )
            + 1
        )
        uow.session.add_all(
            [
                MessageModel(
                    conversation_id=conversation_id,
                    agent_run_id=run.id,
                    sequence=next_sequence + offset,
                    role=MessageRole.ASSISTANT.value,
                    kind=MessageKind.TEXT.value,
                    text=f"step {offset}",
                )
                for offset in range(_MESSAGES_AFTER_THE_PAUSE)
            ]
        )
        await uow.commit()
    return tool_call_id


async def test_an_approval_buried_by_a_long_run_is_still_listed(
    conversation_for_query,
) -> None:
    """`list_user_approvals` -- the approval card."""
    tool_call_id = await _pause_then_bury_it(
        conversation_for_query, tool_name="request_approval"
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        pending = await ConversationRepository(uow).pausing_calls_awaiting_a_return(
            conversation_id=conversation_for_query,
            pausing_tool_names=USER_PAUSING_TOOL_NAMES,
        )

    assert [message.tool_call_id for message in pending] == [tool_call_id]


async def test_a_buried_approval_is_still_the_pause_a_reply_routes_to(
    conversation_for_query,
) -> None:
    """`oldest_unresolved_pause` -- surface reply routing."""
    tool_call_id = await _pause_then_bury_it(
        conversation_for_query, tool_name="ask_user"
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        pending = await ConversationRepository(uow).pausing_calls_awaiting_a_decision(
            conversation_id=conversation_for_query,
            pausing_tool_names=PAUSING_TOOL_NAMES,
            limit=1,
        )

    assert [message.tool_call_id for message in pending] == [tool_call_id]


async def test_a_decided_approval_stays_listed_until_its_return_lands(
    conversation_for_query,
    fixed_test_user,
) -> None:
    """Listing by *decision* hid the card the moment Approve was clicked, so a
    worker that then died left no way to retry. The approvals list asks about the
    tool return instead, and keeps the card up while the approved tool runs."""
    tool_call_id = await _pause_then_bury_it(
        conversation_for_query, tool_name="request_approval"
    )
    async with create_uow_from_session_maker(async_session_maker) as uow:
        repo = ConversationRepository(uow)
        await repo.record_approval_decision(
            conversation_id=conversation_for_query,
            approval_id=tool_call_id,
            agent_run_id=None,
            tool_name="request_approval",
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={},
            resolved_by_user_id=UUID(fixed_test_user["id"]),
        )
        await uow.commit()

    async with create_uow_from_session_maker(async_session_maker) as uow:
        repo = ConversationRepository(uow)
        listed = await repo.pausing_calls_awaiting_a_return(
            conversation_id=conversation_for_query,
            pausing_tool_names=USER_PAUSING_TOOL_NAMES,
        )
        decided = await repo.pausing_calls_awaiting_a_decision(
            conversation_id=conversation_for_query,
            pausing_tool_names=USER_PAUSING_TOOL_NAMES,
        )

    assert [message.tool_call_id for message in listed] == [tool_call_id]
    # ...and the decision-aware question, which drives superseding and reply
    # routing, now says there is nothing outstanding.
    assert decided == []


async def test_a_returned_pause_is_not_pending_on_either_question(
    conversation_for_query,
) -> None:
    tool_call_id = await _pause_then_bury_it(
        conversation_for_query, tool_name="ask_user"
    )
    async with create_uow_from_session_maker(async_session_maker) as uow:
        repo = ConversationRepository(uow)
        await repo.append_message(
            conversation_id=conversation_for_query,
            agent_run_id=None,
            draft=MessageDraft.of_tool_return(
                tool_call_id=tool_call_id,
                tool_name="ask_user",
                tool_result={"answered": True},
            ),
        )
        await uow.commit()

    async with create_uow_from_session_maker(async_session_maker) as uow:
        repo = ConversationRepository(uow)
        assert (
            await repo.pausing_calls_awaiting_a_return(
                conversation_id=conversation_for_query,
                pausing_tool_names=USER_PAUSING_TOOL_NAMES,
            )
            == []
        )


async def test_a_snooze_is_never_a_user_approval(conversation_for_query) -> None:
    """A snooze resolves on a timer with nobody involved, so it must not appear
    on an approvals list or be handed a typed reply."""
    tool_call_id = await _pause_then_bury_it(conversation_for_query, tool_name="snooze")

    async with create_uow_from_session_maker(async_session_maker) as uow:
        repo = ConversationRepository(uow)
        user_pauses = await repo.pausing_calls_awaiting_a_return(
            conversation_id=conversation_for_query,
            pausing_tool_names=USER_PAUSING_TOOL_NAMES,
        )
        all_pauses = await repo.pausing_calls_awaiting_a_decision(
            conversation_id=conversation_for_query,
            pausing_tool_names=PAUSING_TOOL_NAMES,
        )

    assert user_pauses == []
    assert [message.tool_call_id for message in all_pauses] == [tool_call_id]
