"""What moves a conversation up the history list, and what does not.

`last_activity_at` is indexed, so every stamp is a non-HOT rewrite of the
conversation row. The agent's own messages -- one per tool call, one per tool
result -- must not stamp; people, notifications and runs starting or ending do.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.repositories.conversation_activity import (
    is_activity,
)

_LONG_AGO = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "draft",
    [
        MessageDraft.of_text("hello", role=MessageRole.USER),
        MessageDraft.of_notification("your report is ready"),
    ],
)
def test_a_person_or_a_notification_is_activity(draft) -> None:
    assert is_activity(draft)


@pytest.mark.parametrize(
    "draft",
    [
        MessageDraft.of_text("working on it"),
        MessageDraft.of_thinking("hmm"),
        MessageDraft.of_tool_call(tool_name="search", tool_call_id="c1"),
        MessageDraft.of_tool_return(tool_call_id="c1", tool_result={"ok": True}),
    ],
)
def test_what_the_agent_says_while_it_works_is_not(draft) -> None:
    assert not is_activity(draft)


class _Session:
    def __init__(self, conversation, run=None) -> None:
        self.conversation = conversation
        self.run = run

    async def get(self, _model, _id):
        return self.conversation

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.run)

    def add(self, _model) -> None:
        return None

    async def flush(self) -> None:
        return None


def _repository(session) -> ConversationRepository:
    return ConversationRepository(
        SimpleNamespace(session=session, collect_events=lambda _events: None)
    )


def _conversation():
    return SimpleNamespace(
        status=ConversationStatus.COMPLETED.value,
        output_data=None,
        last_activity_at=_LONG_AGO,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("touch", "moved"), [(True, True), (False, False)])
async def test_a_status_change_stamps_only_when_asked(touch, moved) -> None:
    conversation = _conversation()

    await _repository(_Session(conversation))._update_conversation_status(
        conversation_id=uuid4(),
        status=ConversationStatus.RUNNING,
        touch_activity=touch,
    )

    assert (conversation.last_activity_at != _LONG_AGO) is moved


@pytest.mark.asyncio
async def test_finishing_a_run_is_activity() -> None:
    conversation = _conversation()
    run = SimpleNamespace(
        conversation_id=uuid4(),
        status=AgentRunStatus.RUNNING.value,
        run_metadata={},
        error=None,
        output_data=None,
        finished_at=None,
    )

    await _repository(_Session(conversation, run)).finish_agent_run(
        agent_run_id=uuid4(), status=AgentRunStatus.COMPLETED
    )

    assert conversation.last_activity_at != _LONG_AGO


@pytest.mark.asyncio
async def test_repairing_an_already_finished_run_is_not() -> None:
    # The terminal-repair branch settles a status out of step; nobody did
    # anything, so the conversation must not jump up the list. The run and the
    # conversation here are both already finished, so the repair finds nothing
    # to move either.
    conversation = _conversation()
    run = SimpleNamespace(
        conversation_id=uuid4(),
        status=AgentRunStatus.COMPLETED.value,
    )

    await _repository(_Session(conversation, run)).finish_agent_run(
        agent_run_id=uuid4(), status=AgentRunStatus.COMPLETED
    )

    assert conversation.last_activity_at == _LONG_AGO
