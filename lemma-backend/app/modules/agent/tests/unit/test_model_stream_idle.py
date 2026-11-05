"""A model response that goes quiet mid-answer is abandoned and asked again.

A provider can stall after its first deltas and go on sending empty keep-alive
chunks: the bytes keep the per-chunk read timeout from firing, and the run
waits out the whole of it. The harness bounds the gap between two deltas it
can see, and the retry it already has asks the request again.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import AgentEventType
from app.modules.agent.infrastructure.harness.lemma import harness as harness_module
from app.modules.agent.infrastructure.harness.lemma.harness import PydanticAIHarness
from app.modules.agent.infrastructure.providers.transport_errors import (
    ModelStreamStalled,
    is_retryable_stream_error,
)

pytestmark = pytest.mark.unit

_IDLE = 0.2


def _stalls_once_then_answers(attempts: list[int]):
    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        attempts.append(1)
        if len(attempts) == 1:
            yield "Let me "
            # Keep-alives: something on the wire, nothing in the answer.
            while True:
                await asyncio.sleep(_IDLE / 4)
                yield ""
        yield "the real answer"

    return stream_fn


def _thinks_silently_then_answers():
    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        await asyncio.sleep(_IDLE * 3)
        yield "after a long think"

    return stream_fn


async def _run(model, monkeypatch) -> list:
    monkeypatch.setattr(harness_module, "_runtime_profile_model", lambda options: model)
    monkeypatch.setattr(
        harness_module.agent_settings,
        "agent_model_stream_idle_timeout_seconds",
        _IDLE,
    )
    pod_id = uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=uuid4())
    return [
        event
        async for event in PydanticAIHarness()._execute(
            agent=Agent(
                pod_id=pod_id, user_id=conversation.user_id, name="a", instruction=""
            ),
            conversation=conversation,
            messages=[],
            ctx=ToolContext(
                user_id=conversation.user_id,
                pod_id=pod_id,
                conversation_id=conversation.id,
            ),
            options=HarnessOptions(
                model_name="test-model", history_summarization_enabled=False
            ),
            agent_run_id=uuid4(),
            should_stop=None,
        )
    ]


def _answers(events) -> list[str]:
    return [
        event.data.text
        for event in events
        if event.type is AgentEventType.MESSAGE and getattr(event.data, "text", None)
    ]


@pytest.mark.asyncio
async def test_a_response_that_stalls_after_starting_is_asked_again(monkeypatch):
    attempts: list[int] = []

    events = await asyncio.wait_for(
        _run(
            FunctionModel(stream_function=_stalls_once_then_answers(attempts)),
            monkeypatch,
        ),
        timeout=10,
    )

    assert len(attempts) == 2
    assert not [event for event in events if event.type is AgentEventType.ERROR]
    assert _answers(events) == ["the real answer"]


@pytest.mark.asyncio
async def test_a_model_thinking_before_its_first_delta_is_not_cut_off(monkeypatch):
    events = await _run(
        FunctionModel(stream_function=_thinks_silently_then_answers()), monkeypatch
    )

    assert _answers(events) == ["after a long think"]


def test_a_stall_is_retried_as_a_drop() -> None:
    assert is_retryable_stream_error(ModelStreamStalled("quiet"))
