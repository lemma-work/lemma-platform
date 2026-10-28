"""The next run of a conversation starts with exactly what the last one sent.

A provider caches the request prefix. Within a run that is automatic -- each
request extends the one before it. Across runs it is not: the next run rebuilds
the history from storage, and any byte the last run sent that storage does not
reproduce is where the cached prefix ends. Two of those used to sit near the
front of every request -- a time note stamped with "now" and never stored, and
a system prompt carrying the task list, memory and the attached doc -- so the
first request of every run re-read the whole conversation.

These drive the real in-process harness twice over one conversation, the second
time from rows shaped as the first run stored them, and compare what reached the
model on the wire.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.prompt.run_context import render_run_context
from app.modules.agent.domain.value_objects import MessageKind, MessageRole
from app.modules.agent.infrastructure.harness.lemma import harness as harness_module
from app.modules.agent.infrastructure.harness.lemma.harness import PydanticAIHarness
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.tools import NoArguments, ToolGroup, tool

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


class _Recorder:
    """A model that answers from a script and keeps every request it was sent."""

    def __init__(self, script: list[object]) -> None:
        self._script = script
        self.requests: list[list[ModelMessage]] = []
        self.instructions: list[str | None] = []

    async def stream(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        self.requests.append(list(messages))
        self.instructions.append(info.instructions)
        yield self._script[len(self.requests) - 1]


async def lookup(ctx: ToolContext, request: NoArguments) -> str:
    """Look the answer up."""
    return "42"


_LOOKUP = tool(lookup, ToolGroup.CALLABLES)


def _row(
    conversation: Conversation, sequence: int, run_id: UUID, **fields: object
) -> Message:
    return Message(
        conversation_id=conversation.id,
        sequence=sequence,
        agent_run_id=run_id,
        created_at=_T0 + timedelta(minutes=sequence),
        **fields,
    )


def _user(conversation: Conversation, sequence: int, run_id: UUID, text: str):
    return _row(
        conversation,
        sequence,
        run_id,
        role=MessageRole.USER,
        kind=MessageKind.TEXT,
        text=text,
    )


def _answer(conversation: Conversation, sequence: int, run_id: UUID, text: str):
    return _row(
        conversation,
        sequence,
        run_id,
        role=MessageRole.ASSISTANT,
        kind=MessageKind.TEXT,
        text=text,
    )


async def _run(
    recorder: _Recorder,
    *,
    conversation: Conversation,
    agent: Agent,
    messages: list[Message],
    run_context: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FunctionModel(stream_function=recorder.stream)
    monkeypatch.setattr(harness_module, "_runtime_profile_model", lambda options: model)
    ctx = ToolContext(
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
        conversation_id=conversation.id,
        agent_name=agent.name,
        run_context=run_context,
    )
    async for _ in PydanticAIHarness()._execute(
        agent=agent,
        conversation=conversation,
        messages=messages,
        ctx=ctx,
        options=HarnessOptions(
            model_name="test-model",
            history_summarization_enabled=False,
            tools=[_LOOKUP],
        ),
        agent_run_id=uuid4(),
        should_stop=None,
    ):
        pass


async def _wire(messages: list[ModelMessage]) -> list[str]:
    """The request as an OpenAI-compatible provider receives it, one message
    per entry -- the bytes its prefix cache is keyed on."""
    model = OpenAIChatModel("gpt-4o", provider=OpenAIProvider(api_key="unused"))
    mapped = await model._map_messages(messages, ModelRequestParameters())
    return [json.dumps(message, sort_keys=True, default=str) for message in mapped]


@pytest.mark.anyio
async def test_the_next_run_starts_with_exactly_what_the_last_one_sent(monkeypatch):
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="helper",
        instruction="Be brief.",
    )
    run0, run1, run2 = uuid4(), uuid4(), uuid4()
    earlier = [
        _user(conversation, 1, run0, "What is the plan?"),
        _answer(conversation, 2, run0, "Look it up, then report."),
    ]

    first = _Recorder(
        [
            {0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="call_1")},
            "It is 42.",
        ]
    )
    await _run(
        first,
        conversation=conversation,
        agent=agent,
        messages=[*earlier, _user(conversation, 3, run1, "Find the answer.")],
        run_context=render_run_context(sections=["# Your current task list\n- [ ] a"]),
        monkeypatch=monkeypatch,
    )

    # What the first run stored, and the next message.
    stored = [
        *earlier,
        _user(conversation, 3, run1, "Find the answer."),
        _row(
            conversation,
            4,
            run1,
            role=MessageRole.ASSISTANT,
            kind=MessageKind.TOOL_CALL,
            tool_name="lookup",
            tool_call_id="call_1",
            tool_args={},
        ),
        _row(
            conversation,
            5,
            run1,
            role=MessageRole.TOOL,
            kind=MessageKind.TOOL_RETURN,
            tool_name="lookup",
            tool_call_id="call_1",
            tool_result="42",
        ),
        _answer(conversation, 6, run1, "It is 42."),
        _user(conversation, 7, run2, "Thanks. And double it?"),
    ]
    second = _Recorder(["84."])
    await _run(
        second,
        conversation=conversation,
        agent=agent,
        messages=stored,
        # Everything that changes between runs changed: the task list was
        # ticked off and the clock moved on.
        run_context=render_run_context(
            sections=["# Your current task list\n- [x] a"],
            now=_T0 + timedelta(hours=3),
        ),
        monkeypatch=monkeypatch,
    )

    assert len(first.requests) == 2
    last_of_first = await _wire(first.requests[-1])
    first_of_second = await _wire(second.requests[0])

    # The first run's last request ends with its context part, and nothing
    # else of it is anywhere but the tail.
    assert "<run_context>" in last_of_first[-1]
    assert not any("<run_context>" in entry for entry in last_of_first[:-1])
    sent_before_the_context = last_of_first[:-1]
    assert first_of_second[: len(sent_before_the_context)] == sent_before_the_context
    # ...followed by the first run's final answer, as the provider produced it.
    assert "It is 42." in first_of_second[len(sent_before_the_context)]
    # And the system prompt in front of all of it did not move either.
    assert second.instructions[0] == first.instructions[-1]


@pytest.mark.anyio
async def test_a_replayed_message_carries_when_it_was_sent_not_when_it_is_read(
    monkeypatch,
):
    """The time note is part of the stored turn's rendering, so replaying it
    tomorrow reads the same as sending it today."""
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="helper",
        instruction="",
    )
    run1, run2 = uuid4(), uuid4()
    question = _user(conversation, 1, run1, "Hello?")

    sent = _Recorder(["Hi."])
    await _run(
        sent,
        conversation=conversation,
        agent=agent,
        messages=[question],
        run_context=render_run_context(sections=()),
        monkeypatch=monkeypatch,
    )
    replayed = _Recorder(["Fine."])
    await _run(
        replayed,
        conversation=conversation,
        agent=agent,
        messages=[
            question,
            _answer(conversation, 2, run1, "Hi."),
            _user(conversation, 3, run2, "How are you?"),
        ],
        run_context=render_run_context(sections=()),
        monkeypatch=monkeypatch,
    )

    first_turn = [entry for entry in await _wire(sent.requests[0]) if "Hello?" in entry]
    assert len(first_turn) == 1
    assert "Sent 2026-01-05T09:01:00Z" in first_turn[0]
    assert first_turn[0] in await _wire(replayed.requests[0])
