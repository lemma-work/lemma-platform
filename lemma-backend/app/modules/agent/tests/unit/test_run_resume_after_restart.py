"""A release should not end every conversation in flight.

A worker on its way out used to finalize each in-flight run FAILED, and streaq
recorded the interrupted job as *succeeded* so nothing redelivered it. Shipping
a version therefore terminated every agent run on the box, and each person had
to ask again.

Everything a run did is already durable — messages are persisted as they stream
and history is rebuilt from the database on every run — so the work does not
need re-doing, only picking back up.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    RESUMABLE_AGENT_RUN_STATUSES,
    TERMINAL_AGENT_RUN_STATUSES,
    AgentRunStatus,
    ConversationStatus,
)

pytestmark = pytest.mark.unit


class TestInterruptedIsItsOwnThing:
    def test_it_is_not_terminal(self) -> None:
        """Terminal would stop `finish_agent_run` transitioning out of it, which
        is the transition the whole feature is."""
        assert AgentRunStatus.INTERRUPTED not in TERMINAL_AGENT_RUN_STATUSES

    def test_it_is_not_active(self) -> None:
        """Active holds the conversation's one run slot. A person who gave up
        waiting and said something else must be able to start a run."""
        assert AgentRunStatus.INTERRUPTED not in ACTIVE_AGENT_RUN_STATUSES

    def test_it_is_the_status_a_worker_resumes_from(self) -> None:
        assert AgentRunStatus.INTERRUPTED in RESUMABLE_AGENT_RUN_STATUSES

    def test_no_terminal_status_is_resumable(self) -> None:
        """Resuming a completed or stopped run would re-run finished work."""
        assert not (RESUMABLE_AGENT_RUN_STATUSES & TERMINAL_AGENT_RUN_STATUSES)

    def test_the_conversation_has_no_matching_state(self) -> None:
        """Deliberate: the conversation stays RUNNING while a run is parked,
        because it is going to continue. A new user-visible status would reach
        the frontend, which switches on these values."""
        assert not hasattr(ConversationStatus, "INTERRUPTED")


class TestAnInterruptedToolCallDoesNotInviteARepeat:
    def test_the_model_is_not_told_to_run_it_again(self) -> None:
        """It was: "Run it again if you still need the result." A run
        interrupted mid-send that is told that sends twice — and now that runs
        resume rather than fail, that text is reached far more often."""
        from uuid import uuid7

        from pydantic_ai.messages import ModelRequest, ToolReturnPart

        from app.modules.agent.domain.entities import Message
        from app.modules.agent.domain.value_objects import MessageKind, MessageRole
        from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
            history_and_prompt,
        )

        conversation_id = uuid7()
        run_id = uuid7()
        messages = [
            Message.create(
                conversation_id=conversation_id,
                sequence=1,
                agent_run_id=run_id,
                role=MessageRole.USER,
                kind=MessageKind.TEXT,
                text="send the invoice",
            ),
            Message.create(
                conversation_id=conversation_id,
                sequence=2,
                agent_run_id=run_id,
                role=MessageRole.ASSISTANT,
                kind=MessageKind.TOOL_CALL,
                tool_name="send_email",
                tool_call_id="e1",
                tool_args={},
            ),
        ]

        history, _ = history_and_prompt(messages)

        returns = " ".join(
            str(part.content)
            for message in history
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        )
        assert "Run it again" not in returns
        assert "outcome is unknown" in returns
        assert "Check the current state" in returns
