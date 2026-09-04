"""Stop must close whatever pause the conversation is sitting on.

``stop_conversation`` handled two states: a run in flight, and a snoozed turn.
A conversation paused on ``ask_user``/``request_approval`` is neither — the run
that asked finished when the tool paused it — so Stop returned the conversation
unchanged, with no error and no state change, on the state where a person most
wants to abandon a turn: an approval they do not want to grant.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import app.modules.agent.services.conversation_turns as turns
from app.core.authorization.permissions import Permissions
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.value_objects import (
    ConversationStatus,
    MessageKind,
    MessageRole,
)


def _conversation() -> Conversation:
    return Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        status=ConversationStatus.WAITING,
    )


def _deny_return(conversation: Conversation) -> Message:
    """What ``supersede_stale_pending_interactions`` hands back per closed call."""
    return Message.create(
        conversation_id=conversation.id,
        sequence=7,
        agent_run_id=uuid4(),
        role=MessageRole.TOOL,
        kind=MessageKind.TOOL_RETURN,
        tool_name="request_approval",
        tool_call_id="tc-1",
        tool_result={"decision": "DENY"},
    )


class _Uow:
    """Enough unit of work to observe ordering.

    The frames are live UI updates, so they must be published *after* the
    commit — publishing them inline holds a pooled connection across a Redis
    round trip.
    """

    def __init__(self) -> None:
        self.commits = 0
        self.callbacks: list[object] = []

    def after_commit(self, callback) -> None:
        self.callbacks.append(callback)

    async def commit(self) -> None:
        self.commits += 1
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            await callback()


def _coordinator(
    monkeypatch: pytest.MonkeyPatch,
    *,
    superseded: list[Message],
    published: list[tuple],
) -> tuple[turns.TurnCoordinator, _Uow, list[object]]:
    statuses: list[object] = []
    uow = _Uow()

    conversations = SimpleNamespace(
        set_conversation_status=AsyncMock(
            side_effect=lambda *, conversation_id, status: statuses.append(status)
        ),
    )
    approvals = SimpleNamespace(
        supersede_stale_pending_interactions=AsyncMock(return_value=superseded),
    )

    async def _publish(conversation_id, frame):
        published.append((conversation_id, frame))

    monkeypatch.setattr(turns, "publish_conversation_event", _publish)

    coordinator = turns.TurnCoordinator(
        uow,
        conversations,
        SimpleNamespace(),
        approvals,
        SimpleNamespace(),
        None,
    )
    return coordinator, uow, statuses


@pytest.mark.asyncio
async def test_stopping_a_waiting_conversation_denies_the_open_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = _conversation()
    published: list[tuple] = []
    coordinator, uow, statuses = _coordinator(
        monkeypatch,
        superseded=[_deny_return(conversation)],
        published=published,
    )

    await coordinator._deny_unresolved_pauses(
        conversation=conversation, user_id=conversation.user_id
    )

    coordinator.approvals.supersede_stale_pending_interactions.assert_awaited_once()
    assert statuses == [ConversationStatus.STOPPED]
    assert conversation.status is ConversationStatus.STOPPED
    assert uow.commits == 1
    assert len(published) == 1


@pytest.mark.asyncio
async def test_a_conversation_with_nothing_open_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop on a finished conversation must not restate it as STOPPED, and
    must not commit a transaction it had no writes for."""
    conversation = _conversation()
    conversation.status = ConversationStatus.COMPLETED
    published: list[tuple] = []
    coordinator, uow, statuses = _coordinator(
        monkeypatch, superseded=[], published=published
    )

    await coordinator._deny_unresolved_pauses(
        conversation=conversation, user_id=conversation.user_id
    )

    assert statuses == []
    assert conversation.status is ConversationStatus.COMPLETED
    assert uow.commits == 0
    assert published == []


@pytest.mark.asyncio
async def test_stop_with_no_active_run_reaches_the_pause_closers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wiring, not the helper.

    The defect was a missing branch, so a test of the helper alone would have
    passed against the broken code. The access seam is patched on the module
    the coordinator imports it from -- `setattr` refuses a name that is not
    there, so moving the call site fails this test rather than passing it.
    """
    conversation = _conversation()
    closed: list[str] = []

    async def _cancel_snooze(*, conversation):
        closed.append("snooze")

    async def _deny_pauses(*, conversation, user_id):
        closed.append("pauses")

    coordinator = turns.TurnCoordinator(
        SimpleNamespace(),
        SimpleNamespace(
            get_conversation=AsyncMock(return_value=conversation),
            get_active_agent_run_for_update=AsyncMock(return_value=None),
        ),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        None,
    )
    monkeypatch.setattr(
        turns, "validate_conversation_access", lambda loaded, **_kwargs: loaded
    )
    monkeypatch.setattr(turns, "require_agent_action", AsyncMock())
    monkeypatch.setattr(coordinator, "_cancel_active_snooze", _cancel_snooze)
    monkeypatch.setattr(coordinator, "_deny_unresolved_pauses", _deny_pauses)

    result = await coordinator.stop_conversation(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
    )

    assert closed == ["snooze", "pauses"]
    assert result is conversation
    turns.require_agent_action.assert_awaited_once()
    assert (
        turns.require_agent_action.await_args.kwargs["action"]
        is Permissions.AGENT_EXECUTE
    )
