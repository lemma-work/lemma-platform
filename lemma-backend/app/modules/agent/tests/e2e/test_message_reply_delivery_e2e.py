"""What the asking conversation does when the people it messaged answer.

The news arrives as input, and input normally starts a turn. Three states make
that wrong, and each is a different answer:

- nothing is suspended: the news is the next turn;
- a `wait_for` is holding the turn: resolving that wait is the way back in, and
  a message posted past it would leave the wait armed to fire again;
- the owner was asked something and has not answered: the news is not their
  answer, and starting a turn would deny the question on their behalf.

Against real repositories: these replaced tests that patched six collaborators
inside the service and asserted the arguments of a mocked `start`, which could
not see any of the three.

The news itself is an `agent.replies_arrived` item, written once the last
question of a batch a conversation put to people closes
(`events/channel_question_work`); one test drives that item through the work
runtime, the rest call the service it reaches.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.infrastructure.work import (
    WorkClass,
    WorkRegistry,
    WorkWriter,
    pod_namespace,
)
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.agent.domain.pauses import PERSON_KINDS
from app.modules.agent.domain.state_kinds import PauseStatus
from app.modules.agent.domain.value_objects import (
    ConversationStatus,
    MessageKind,
    MessageRole,
)
from app.modules.agent.events.run_followup_work import register_run_followup_work
from app.modules.agent.events.turn_payloads import RepliesArrived
from app.modules.agent.events.work_kinds import AGENT_REPLIES_ARRIVED, replies_key
from app.modules.agent.domain.wait import AgentWaitType
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.services.message_reply_service import (
    REPLY_SOURCE,
    MessageReplyService,
)
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    finish,
    messages,
    pause_by_id,
    pausing_call,
    wait_pause,
    running_run,
    runs,
    seeded_conversation,
)
from app.modules.agent.domain.value_objects import QueueState

pytestmark = [pytest.mark.e2e]


async def _deliver(seeded: Seeded) -> bool:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        return await MessageReplyService(uow).deliver(
            conversation_id=seeded.conversation_id, pod_id=seeded.pod_id
        )


def _news(found):
    return [
        message
        for message in found
        if message.role is MessageRole.USER
        and (message.metadata or {}).get("source") == REPLY_SOURCE
    ]


async def test_a_finished_conversation_gets_the_news_as_its_next_turn(scenario):
    seeded = await seeded_conversation(scenario)

    assert await _deliver(seeded) is True

    (started,) = await runs(seeded.conversation_id)
    (news,) = _news(await messages(seeded.conversation_id))
    assert news.agent_run_id == started.id
    assert "channel_read(asked_by_me=True)" in (news.text or "")


@pytest.mark.drives_work
async def test_a_settled_batch_brings_the_asking_conversation_back_once(scenario):
    """The wake path end to end from the item: the agent's runtime turns it
    into the conversation's next turn -- once per batch, however often it is
    written or run."""
    seeded = await seeded_conversation(scenario)
    registry = WorkRegistry()
    register_run_followup_work(registry)
    settled = RepliesArrived(
        conversation_id=seeded.conversation_id, pod_id=seeded.pod_id, batch_id="b-1"
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        writer = WorkWriter(uow.session)
        for _ in range(2):
            await writer.add(
                AGENT_REPLIES_ARRIVED,
                pod_namespace(seeded.pod_id),
                settled,
                dedup_key=replies_key("b-1"),
            )
        await uow.commit()
    await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
    )
    # Delivered again after the first item ran: the batch's news is there.
    async with create_uow_from_session_maker(async_session_maker) as uow:
        assert (
            await MessageReplyService(uow).deliver(
                conversation_id=seeded.conversation_id,
                pod_id=seeded.pod_id,
                batch_id="b-1",
            )
            is False
        )

    (started,) = await runs(seeded.conversation_id)
    (news,) = _news(await messages(seeded.conversation_id))
    assert news.agent_run_id == started.id


async def test_an_open_question_is_not_answered_by_the_news(scenario):
    """The owner's approval stays pending; the news waits behind it."""
    seeded = await seeded_conversation(scenario)
    asking = await running_run(seeded)
    approval_id = await pausing_call(
        seeded,
        asking,
        tool_name="request_approval",
        tool_args={"tool_name": "exec_command", "args": {"cmd": "deploy"}},
    )
    await finish(asking, conversation_status=ConversationStatus.WAITING)

    assert await _deliver(seeded) is True

    assert [run.id for run in await runs(seeded.conversation_id)] == [asking]
    async with create_uow_from_session_maker(async_session_maker) as uow:
        open_questions = await Pauses(uow).pending(
            seeded.conversation_id, kinds=PERSON_KINDS
        )
    assert [call.tool_call_id for call in open_questions] == [approval_id]
    found = await messages(seeded.conversation_id)
    assert not [m for m in found if m.kind is MessageKind.TOOL_RETURN]
    (news,) = _news(found)
    assert news.agent_run_id == asking
    assert news.state.queue_state is QueueState.QUEUED


async def test_a_sleeping_conversation_is_woken_rather_than_written_past(scenario):
    seeded = await seeded_conversation(scenario)
    sleeping = await running_run(seeded)
    tool_call_id = await pausing_call(
        seeded, sleeping, tool_name="wait_for", tool_args={"reason": "replies"}
    )
    await finish(sleeping, conversation_status=ConversationStatus.WAITING)
    now = datetime.now(timezone.utc)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        wait = wait_pause(
            conversation_id=seeded.conversation_id,
            run_id=sleeping,
            pod_id=seeded.pod_id,
            tool_call_id=tool_call_id,
            wait_type=AgentWaitType.TIME,
            wake_at=now + timedelta(hours=1),
            spec={"started_at": now.isoformat(), "user_id": str(seeded.user_id)},
        )
        assert await Pauses(uow).open(wait)
        await uow.commit()

    assert await _deliver(seeded) is True

    found = await messages(seeded.conversation_id)
    assert _news(found) == [], "a message was posted past a live wait"
    (woke,) = [m for m in found if m.kind is MessageKind.TOOL_RETURN]
    assert woke.tool_call_id == tool_call_id
    assert woke.tool_result["woke_because"] == "ANSWERED"
    reloaded = await pause_by_id(wait.id)
    assert reloaded is not None and reloaded.status is PauseStatus.RESOLVED


async def test_a_conversation_that_is_gone_is_not_an_error(scenario):
    """Somebody answered a question whose asker was deleted; their answer is
    still on its row, and raising would push that deletion back at them."""
    seeded = await seeded_conversation(scenario)
    gone = Seeded(
        pod_id=seeded.pod_id,
        user_id=seeded.user_id,
        agent_id=seeded.agent_id,
        agent_name=seeded.agent_name,
        conversation_id=uuid4(),
    )

    assert await _deliver(gone) is False
