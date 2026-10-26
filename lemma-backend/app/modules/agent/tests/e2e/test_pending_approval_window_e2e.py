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
it, and the window only exists in SQL. All four now ask ``Pauses.pending``.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.pause_opening import PauseOpening
from app.modules.agent.domain.pauses import PERSON_KINDS, Pause
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.wait import AgentWaitType, WaitSpec
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    AgentRuntimeConfig,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.models import ConversationModel, MessageModel
from app.modules.agent.infrastructure.db.pauses import Awaiting, Pauses
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.runs import Runs

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


async def _pause_then_bury_it(
    conversation_id: UUID, *, tool_name: str, kind: PauseKind
) -> str:
    """Record a pausing tool call, then write enough messages to bury it."""
    tool_call_id = f"call-{uuid4().hex[:12]}"
    async with create_uow_from_session_maker(async_session_maker) as uow:
        messages_repo = Messages(uow)
        runs = Runs(uow)
        run = await runs.create_agent_run(
            conversation_id=conversation_id,
            agent_id=None,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
        )
        await messages_repo.append_message(
            conversation_id=conversation_id,
            agent_run_id=run.id,
            draft=MessageDraft.of_tool_call(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_args={"question": "may I?"},
                pause=PauseOpening(kind=kind),
            ),
        )
        if kind is PauseKind.WAIT:
            # Its tool writes a wait's row, beside its timer.
            conversation = await uow.session.scalar(
                select(ConversationModel).where(ConversationModel.id == conversation_id)
            )
            await Pauses(uow).open(
                Pause(
                    conversation_id=conversation_id,
                    run_id=run.id,
                    pod_id=conversation.pod_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    kind=kind,
                    wait_type=AgentWaitType.TIME,
                    wait_spec=WaitSpec(),
                )
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
        conversation_for_query, tool_name="request_approval", kind=PauseKind.APPROVAL
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        pending = await Pauses(uow).pending(
            conversation_for_query, kinds=PERSON_KINDS, awaiting=Awaiting.EITHER
        )

    assert [pause.tool_call_id for pause in pending] == [tool_call_id]


async def test_a_buried_approval_is_still_the_pause_a_reply_routes_to(
    conversation_for_query,
) -> None:
    """`oldest_unresolved_pause` -- surface reply routing."""
    tool_call_id = await _pause_then_bury_it(
        conversation_for_query, tool_name="ask_user", kind=PauseKind.QUESTION
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        pending = await Pauses(uow).pending(conversation_for_query, limit=1)

    assert [pause.tool_call_id for pause in pending] == [tool_call_id]


async def test_a_decided_approval_stays_listed_until_its_return_lands(
    conversation_for_query,
    fixed_test_user,
) -> None:
    """Listing by *decision* hid the card the moment Approve was clicked, so a
    worker that then died left no way to retry. The approvals list asks about the
    tool return instead, and keeps the card up while the approved tool runs."""
    tool_call_id = await _pause_then_bury_it(
        conversation_for_query, tool_name="request_approval", kind=PauseKind.APPROVAL
    )
    async with create_uow_from_session_maker(async_session_maker) as uow:
        pauses = Pauses(uow)
        pause = await pauses.get(
            conversation_id=conversation_for_query, tool_call_id=tool_call_id
        )
        assert pause is not None
        pause.resolve(
            AgentRunApprovalDecision.APPROVE_ONCE, by=UUID(fixed_test_user["id"])
        )
        assert await pauses.save(pause)
        await uow.commit()

    async with create_uow_from_session_maker(async_session_maker) as uow:
        pauses = Pauses(uow)
        listed = await pauses.pending(
            conversation_for_query, kinds=PERSON_KINDS, awaiting=Awaiting.EITHER
        )
        decided = await pauses.pending(conversation_for_query, kinds=PERSON_KINDS)

    assert [pause.tool_call_id for pause in listed] == [tool_call_id]
    # ...and the decision-aware question, which drives superseding and reply
    # routing, now says there is nothing outstanding.
    assert decided == []


async def test_a_returned_pause_is_not_pending_on_either_question(
    conversation_for_query,
) -> None:
    tool_call_id = await _pause_then_bury_it(
        conversation_for_query, tool_name="ask_user", kind=PauseKind.QUESTION
    )
    async with create_uow_from_session_maker(async_session_maker) as uow:
        messages = Messages(uow)
        await messages.append_message(
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
        pauses = Pauses(uow)
        assert (
            await pauses.pending(conversation_for_query, awaiting=Awaiting.EITHER) == []
        )
        assert await pauses.pending(conversation_for_query) == []


async def test_a_wait_is_never_a_user_approval(conversation_for_query) -> None:
    """A wait resolves on a timer, a process or a child run, with nobody
    involved, so it must not appear on an approvals list or be handed a typed
    reply."""
    tool_call_id = await _pause_then_bury_it(
        conversation_for_query, tool_name="wait_for", kind=PauseKind.WAIT
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        pauses = Pauses(uow)
        user_pauses = await pauses.pending(
            conversation_for_query, kinds=PERSON_KINDS, awaiting=Awaiting.EITHER
        )
        all_pauses = await pauses.pending(conversation_for_query)

    assert user_pauses == []
    assert [pause.tool_call_id for pause in all_pauses] == [tool_call_id]
