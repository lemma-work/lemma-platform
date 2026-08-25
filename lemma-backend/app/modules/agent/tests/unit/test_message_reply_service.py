"""What happens to the asking conversation when the answers land.

Two branches, and the second one is the whole reason this is not just
``turns.start``: a conversation that happens to be asleep is holding a pausing
tool call, and posting a message past it would supersede that call while leaving
its wait row armed to fire again later.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.wait import AgentWaitWakeReason
from app.modules.agent.services import message_reply_service as module
from app.modules.agent.services.message_reply_service import (
    REPLY_SOURCE,
    MessageReplyService,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def service(monkeypatch):
    """A service whose every collaborator is a recorder."""
    state = SimpleNamespace(
        conversation=SimpleNamespace(id=uuid4(), user_id=uuid4()),
        wait=None,
        started=[],
        woke=[],
    )

    class _Conversations:
        def __init__(self, uow):
            pass

        async def get_conversation(self, conversation_id, include_runs=False):
            return state.conversation

    class _Waits:
        def __init__(self, uow):
            pass

        async def find_active_for_conversation(self, conversation_id):
            return state.wait

    class _Wake:
        def __init__(self, uow):
            pass

        async def wake(self, *, wait, reason):
            state.woke.append((wait, reason))
            return True

    class _Turns:
        async def start(self, conversation, **kwargs):
            state.started.append((conversation, kwargs))
            return SimpleNamespace(
                conversation_id=conversation.id,
                agent_run_id=uuid4(),
                started_new_run=True,
            )

    monkeypatch.setattr(module, "ConversationRepository", _Conversations)
    monkeypatch.setattr(module, "AgentConversationWaitRepository", _Waits)
    monkeypatch.setattr(module, "SnoozeWakeService", _Wake)
    monkeypatch.setattr(
        module,
        "create_authorization_data_service",
        lambda uow: SimpleNamespace(
            build_user_context=lambda **kwargs: _resolved(object())
        ),
    )
    monkeypatch.setattr(module, "set_current_context", lambda ctx: "token")
    monkeypatch.setattr(module, "reset_current_context", lambda token: None)

    built = MessageReplyService(uow=None)
    monkeypatch.setattr(
        built, "_conversation_service", lambda: SimpleNamespace(turns=_Turns())
    )
    return SimpleNamespace(built=built, state=state)


async def _resolved(value):
    return value


@pytest.mark.asyncio
async def test_a_finished_conversation_gets_a_fresh_turn(service):
    """The main path, and the one that makes `snooze` unnecessary here.

    The agent sent its messages and stopped. Nothing is suspended, so there is
    no pause to resolve — the replies are simply the next input.
    """
    pod_id = uuid4()

    delivered = await service.built.deliver(
        conversation_id=service.state.conversation.id, pod_id=pod_id
    )

    assert delivered is True
    assert len(service.state.started) == 1
    conversation, kwargs = service.state.started[0]
    assert conversation is service.state.conversation
    assert kwargs["pod_id"] == pod_id
    # The conversation's owner, never the person who answered: the turn runs
    # under the asker's authority, which is the only authority it ever had.
    assert kwargs["user_id"] == service.state.conversation.user_id
    assert kwargs["message_metadata"] == {"source": REPLY_SOURCE}
    assert "check_messages" in kwargs["content"]
    assert service.state.woke == []


@pytest.mark.asyncio
async def test_a_sleeping_conversation_has_its_pause_resolved_instead(service):
    """Not a message posted past the sleep.

    `turns.start` would auto-deny the pending `snooze` call and start a run, and
    the wait row it left behind would still be ACTIVE — due to fire later and
    try to resume a call that already has a return.
    """
    service.state.wait = SimpleNamespace(id=uuid4())

    delivered = await service.built.deliver(
        conversation_id=service.state.conversation.id, pod_id=uuid4()
    )

    assert delivered is True
    assert service.state.started == [], "a message was posted past a live pause"
    assert [reason for _, reason in service.state.woke] == [
        AgentWaitWakeReason.ANSWERED
    ]


@pytest.mark.asyncio
async def test_a_conversation_that_is_gone_is_not_an_error(service, monkeypatch):
    """Somebody answered a question whose asker was deleted.

    Their answer is still on its row. Raising would push that deletion back at
    the person who answered, as a failure of the thing they just did.
    """

    class _Missing:
        def __init__(self, uow):
            pass

        async def get_conversation(self, conversation_id, include_runs=False):
            return None

    monkeypatch.setattr(module, "ConversationRepository", _Missing)

    delivered = await MessageReplyService(uow=None).deliver(
        conversation_id=uuid4(), pod_id=uuid4()
    )

    assert delivered is False
