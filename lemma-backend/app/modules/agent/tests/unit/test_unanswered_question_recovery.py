"""A question the user never answered must not stall or repeat the conversation.

An unmatched `ask_user` means one of two very different things. Either the
conversation is genuinely still waiting on a human -- in which case telling the
model its question failed would be a lie -- or the person read the question,
decided not to answer it, and sent something else instead. Both looked identical
to the history builder, which dropped the call in either case. So on the second
one the model had no record of ever having asked, and asked again instead of
reading what the person had actually said.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from uuid import uuid7
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import MessageKind, MessageRole
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    history_and_prompt,
)

pytestmark = pytest.mark.unit

CONVERSATION_ID = uuid7()
RUN_ID = uuid7()


def _msg(
    sequence,
    role,
    kind,
    *,
    text=None,
    tool_name=None,
    tool_call_id=None,
    tool_args=None,
    tool_result=None,
) -> Message:
    return Message.create(
        conversation_id=CONVERSATION_ID,
        sequence=sequence,
        agent_run_id=RUN_ID,
        role=role,
        kind=kind,
        text=text,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_args=tool_args,
        tool_result=tool_result,
    )


def _asked_but_unanswered() -> list[Message]:
    return [
        _msg(1, MessageRole.USER, MessageKind.TEXT, text="book me a flight"),
        _msg(
            2,
            MessageRole.ASSISTANT,
            MessageKind.TOOL_CALL,
            tool_name="ask_user",
            tool_call_id="q1",
            tool_args={"question": "Window or aisle?"},
        ),
    ]


def _tool_returns(history) -> list[ToolReturnPart]:
    return [
        part
        for message in history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


class TestWhenThePersonAnswersSomethingElse:
    def test_the_conversation_carries_on(self) -> None:
        messages = [
            *_asked_but_unanswered(),
            _msg(3, MessageRole.USER, MessageKind.TEXT, text="actually cancel it"),
        ]

        history, prompt = history_and_prompt(messages)

        assert "actually cancel it" in str(prompt)

    def test_the_model_is_told_the_question_went_unanswered(self) -> None:
        messages = [
            *_asked_but_unanswered(),
            _msg(3, MessageRole.USER, MessageKind.TEXT, text="actually cancel it"),
        ]

        history, _ = history_and_prompt(messages)

        answers = [str(part.content) for part in _tool_returns(history)]
        assert any("did not answer" in answer for answer in answers)

    def test_it_is_told_not_to_simply_ask_again(self) -> None:
        """The failure mode being fixed is the model re-asking a question the
        person has already declined to answer."""
        messages = [
            *_asked_but_unanswered(),
            _msg(3, MessageRole.USER, MessageKind.TEXT, text="actually cancel it"),
        ]

        history, _ = history_and_prompt(messages)

        answers = " ".join(str(part.content) for part in _tool_returns(history))
        assert "carry on" in answers

    def test_the_question_itself_is_still_in_the_history(self) -> None:
        """Dropping the call left no record of having asked."""
        messages = [
            *_asked_but_unanswered(),
            _msg(3, MessageRole.USER, MessageKind.TEXT, text="actually cancel it"),
        ]

        history, _ = history_and_prompt(messages)

        assert any("ask_user" in str(message) for message in history)


class TestWhenTheConversationIsStillWaiting:
    def test_an_open_question_is_not_reported_as_unanswered(self) -> None:
        """Nobody has been given the chance to reply yet; saying the question
        failed while the person is still looking at it would be false."""
        history, _ = history_and_prompt(_asked_but_unanswered())

        answers = " ".join(str(part.content) for part in _tool_returns(history))
        assert "did not answer" not in answers

    def test_an_answered_question_keeps_its_real_answer(self) -> None:
        messages = [
            *_asked_but_unanswered(),
            _msg(
                3,
                MessageRole.TOOL,
                MessageKind.TOOL_RETURN,
                tool_name="ask_user",
                tool_call_id="q1",
                tool_result={"answer": "window"},
            ),
            _msg(4, MessageRole.USER, MessageKind.TEXT, text="thanks"),
        ]

        history, _ = history_and_prompt(messages)

        answers = " ".join(str(part.content) for part in _tool_returns(history))
        assert "window" in answers
        assert "did not answer" not in answers
