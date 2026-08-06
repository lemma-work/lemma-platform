"""What a dispatched run's prompt has to carry, and when.

A Lemma conversation maps to one provider session, kept in one working
directory, so the prompt is just the latest user message: the agent loads the
rest back itself. Sending more would duplicate the conversation in its context.

The exception is a harness that never advertised ``loadSession``. There is no
session to resume there, ever, and one lone message leaves the agent answering
a follow-up it has never seen the start of. It costs nothing in the usual case:
a resumable harness only lacks a stored session on a conversation's first turn,
where there is no history to send.
"""

from __future__ import annotations

from uuid import uuid7

import pytest

from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.value_objects import (
    ConversationStatus,
    ConversationType,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.harnesses.remote_payload import run_start_payload
from app.modules.agent.tools.context import BaseAgentContext


pytestmark = pytest.mark.asyncio

POD_ID = uuid7()
CONVERSATION_ID = uuid7()


def _agent() -> Agent:
    return Agent(
        id=uuid7(),
        pod_id=POD_ID,
        user_id=uuid7(),
        name="helper",
        instruction="Be brief.",
    )


def _conversation() -> Conversation:
    return Conversation(
        id=CONVERSATION_ID,
        pod_id=POD_ID,
        user_id=uuid7(),
        agent_id=uuid7(),
        title="continuity",
        type=ConversationType.CHAT,
        status=ConversationStatus.RUNNING,
    )


def _message(sequence: int, role: str, text: str) -> Message:
    return Message(
        id=uuid7(),
        conversation_id=CONVERSATION_ID,
        sequence=sequence,
        role=role,
        kind=MessageKind.TEXT,
        text=text,
    )


def _transcript() -> list[Message]:
    return [
        _message(1, MessageRole.USER, "Book me a table for four."),
        _message(2, MessageRole.ASSISTANT, "Which night?"),
        _message(3, MessageRole.USER, "Friday."),
    ]


def _ctx() -> BaseAgentContext:
    return BaseAgentContext(
        user_id=uuid7(), pod_id=POD_ID, conversation_id=CONVERSATION_ID
    )


def _user_prompt(*, carries_history: bool) -> str:
    payload = run_start_payload(
        agent=_agent(),
        conversation=_conversation(),
        messages=_transcript(),
        ctx=_ctx(),
        agent_run_id=uuid7(),
        runtime_instructions="",
        carries_history=carries_history,
    )
    return str(payload["prompt"]["user_prompt"])


class TestHistory:
    async def test_a_resumable_run_sends_only_the_latest_turn(self):
        """The provider session already holds the rest, and it is loaded back
        from the conversation's own working directory on every turn."""
        prompt = _user_prompt(carries_history=False)

        assert "Friday." in prompt
        assert "Book me a table" not in prompt
        assert "Which night?" not in prompt

    async def test_a_harness_that_cannot_resume_is_told_the_conversation(self):
        """Otherwise the agent answers a follow-up it has never seen the start
        of, on every single turn, for the life of the conversation."""
        prompt = _user_prompt(carries_history=True)

        assert "Book me a table for four." in prompt
        assert "Which night?" in prompt
        assert "Friday." in prompt
        assert prompt.index("Book me a table") < prompt.index("Friday.")


class TestCredentials:
    async def test_the_payload_never_carries_runtime_credentials(self):
        """This payload's destination is somebody's laptop."""
        payload = run_start_payload(
            agent=_agent(),
            conversation=_conversation(),
            messages=_transcript(),
            ctx=_ctx(),
            agent_run_id=uuid7(),
            runtime_instructions="",
            carries_history=False,
        )

        assert "runtime_credentials" not in payload
