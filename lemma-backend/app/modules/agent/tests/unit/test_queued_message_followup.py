"""The backstop for messages no run ever read.

`PendingUserMessagesCapability` normally gets there first: it claims a mid-run
message and steers it into the run already answering, so nothing is left owing
by the time that run ends. These cover what it cannot reach — a run built
without capabilities (Agent Host), and a run that died before draining — where
the person is otherwise waiting on an answer that never comes.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import AgentRun, Conversation
from app.modules.agent.domain.events import AgentRunStartedEvent
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    AgentRuntimeConfig,
    ConversationStatus,
)
from app.modules.agent.services.conversation_turns import TurnCoordinator


def _runtime() -> AgentRuntimeConfig:
    return AgentRuntimeConfig(profile_id="user:harness", model_name="claude-sonnet-4-5")


def _run(conversation_id, status=AgentRunStatus.RUNNING) -> AgentRun:
    return AgentRun(
        conversation_id=conversation_id,
        status=status,
        agent_runtime=_runtime(),
        started_at=datetime.now(timezone.utc),
    )


def _coordinator(*, queued: int, active_run: AgentRun | None = None):
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        organization_id=uuid4(),
        agent_id=uuid4(),
        agent_runtime=_runtime(),
    )
    finished = _run(conversation.id, AgentRunStatus.COMPLETED)
    created = _run(conversation.id)
    repository = SimpleNamespace(
        count_queued_user_messages=AsyncMock(return_value=queued),
        lock_conversation=AsyncMock(),
        get_active_agent_run_for_update=AsyncMock(return_value=active_run),
        get_agent_run=AsyncMock(return_value=finished),
        create_agent_run=AsyncMock(return_value=created),
    )
    uow = SimpleNamespace(collect_events=MagicMock(), commit=AsyncMock())
    coordinator = TurnCoordinator(
        uow=uow,
        conversation_repository=repository,
        agent_repository=SimpleNamespace(),
        approvals=SimpleNamespace(
            supersede_stale_pending_interactions=AsyncMock(return_value=[])
        ),
        pauses=SimpleNamespace(),
        usage_service=None,
    )
    return coordinator, conversation, repository, uow, finished, created


@pytest.mark.asyncio
async def test_a_message_queued_behind_a_busy_run_gets_its_own_turn() -> None:
    coordinator, conversation, repository, uow, finished, created = _coordinator(
        queued=3
    )

    started = await coordinator.start_queued_followup(
        conversation=conversation, completed_run_id=finished.id
    )

    assert started is not None
    run_id, superseded = started
    assert run_id == created.id
    # Frames come back rather than going out: the caller publishes them once
    # the pooled connection is handed back.
    assert superseded == []
    # No message is appended: the messages already exist, unanswered.
    assert repository.create_agent_run.await_args.kwargs["metadata"] == {
        "source": "queued_messages",
        "queued_behind_agent_run_id": str(finished.id),
    }
    [event] = uow.collect_events.call_args.args[0]
    assert isinstance(event, AgentRunStartedEvent)
    assert event.conversation_id == conversation.id
    assert event.agent_run_id == created.id
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_nothing_queued_starts_nothing() -> None:
    coordinator, conversation, repository, _uow, finished, _created = _coordinator(
        queued=0
    )

    assert (
        await coordinator.start_queued_followup(
            conversation=conversation, completed_run_id=finished.id
        )
        is None
    )
    repository.create_agent_run.assert_not_awaited()
    # Established without taking the conversation lock, which is the whole cost
    # on the overwhelmingly common path.
    repository.lock_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_followup_run_cannot_itself_recur() -> None:
    """The queued messages belong to the finished run, never to the new one.

    So the next completion asks about a run whose queue is empty, and the chain
    stops on its own rather than needing a depth counter.
    """
    coordinator, conversation, repository, _uow, finished, created = _coordinator(
        queued=3
    )
    await coordinator.start_queued_followup(
        conversation=conversation, completed_run_id=finished.id
    )

    assert repository.count_queued_user_messages.await_args.args == (finished.id,)
    assert created.id != finished.id


@pytest.mark.asyncio
async def test_a_run_that_ended_by_asking_a_question_is_left_alone() -> None:
    coordinator, conversation, repository, _uow, finished, _created = _coordinator(
        queued=2
    )
    conversation.status = ConversationStatus.WAITING

    assert (
        await coordinator.start_queued_followup(
            conversation=conversation, completed_run_id=finished.id
        )
        is None
    )
    # Starting a turn here would auto-deny the question before the person saw
    # it; their answer starts a run that picks the queue up anyway.
    repository.create_agent_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_someone_typing_first_takes_the_turn_instead() -> None:
    conversation_id = uuid4()
    coordinator, conversation, repository, _uow, finished, _created = _coordinator(
        queued=2, active_run=_run(conversation_id)
    )

    assert (
        await coordinator.start_queued_followup(
            conversation=conversation, completed_run_id=finished.id
        )
        is None
    )
    repository.create_agent_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stopped_run_does_not_start_the_next_one() -> None:
    """Stop means stop, whatever is sitting behind it."""
    from app.modules.agent.events.queued_followup import (
        start_followup_run_for_queued_messages,
    )
    from app.modules.agent.domain.events import AgentRunCompletedEvent

    factory = MagicMock(side_effect=AssertionError("must not open a transaction"))
    result = await start_followup_run_for_queued_messages(
        AgentRunCompletedEvent(
            conversation_id=uuid4(),
            agent_run_id=uuid4(),
            status=AgentRunStatus.STOPPED,
        ),
        uow_factory=factory,
    )

    assert result is None
    factory.assert_not_called()
