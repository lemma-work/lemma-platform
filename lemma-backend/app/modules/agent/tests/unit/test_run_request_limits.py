"""A run's request count is limited once, by its budget, and never by accident.

The run budget is what the run is told about: it warns as the run nears the end
and pauses to ask a person whether to carry on. A second, lower cap inside
pydantic-ai used to end the run first, with an error blaming the model settings,
so a long run never reached the pause its own instructions promised.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx2
import pytest
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from app.modules.agent.config import agent_settings
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.run_budget import RunBudget, RunSpend
from app.modules.agent.domain.tools import NoArguments, ToolGroup, tool
from app.modules.agent.domain.value_objects import AgentEvent, AgentEventType
from app.modules.agent.infrastructure.harness.lemma.harness import PydanticAIHarness
from app.modules.agent.infrastructure.harness.lemma.run_options import (
    harness_usage_limits,
)

pytestmark = pytest.mark.unit

_LONG_RUN_STEPS = 250


def _runner_limits():
    """The limits a production run is driven under, as the harness sets them."""
    return harness_usage_limits()


def _spend(model_requests: int) -> RunSpend:
    return RunSpend(
        budget=RunBudget(
            model_requests=model_requests,
            wall_clock_seconds=0,
            consecutive_tool_failures=0,
        )
    )


def _tool_returns(messages: list[ModelMessage]) -> int:
    return sum(
        isinstance(part, ToolReturnPart)
        for message in messages
        for part in message.parts
    )


async def _run(model: FunctionModel, *, spend: RunSpend, tools=None):
    pod_id = uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=uuid4())
    events: list[AgentEvent] = []
    async for event in PydanticAIHarness(model=model)._execute(
        agent=Agent(
            pod_id=pod_id,
            user_id=conversation.user_id,
            name="assistant",
            instruction="",
        ),
        conversation=conversation,
        messages=[],
        ctx=ToolContext(
            user_id=conversation.user_id,
            pod_id=pod_id,
            conversation_id=conversation.id,
        ),
        options=HarnessOptions(
            model_name="test-model",
            history_summarization_enabled=False,
            tools=tools or [],
            spend=spend,
        ),
        agent_run_id=uuid4(),
        should_stop=None,
    ):
        events.append(event)
    return events


def test_the_runner_leaves_request_counting_to_the_run_budget() -> None:
    limit = _runner_limits().request_limit

    assert limit is None or limit > agent_settings.agent_run_budget_model_requests


async def test_a_long_run_inside_its_budget_is_not_ended_by_a_hidden_cap() -> None:
    requests: list[int] = []

    async def step(ctx: ToolContext, request: NoArguments) -> str:
        del ctx, request
        return "ok"

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[object]:
        del info
        requests.append(1)
        if _tool_returns(messages) < _LONG_RUN_STEPS:
            yield {0: DeltaToolCall(name="step", json_args="{}")}
            return
        yield "all done"

    spend = _spend(agent_settings.agent_run_budget_model_requests)
    events = await _run(
        FunctionModel(stream_function=stream_fn),
        spend=spend,
        tools=[tool(step, ToolGroup.CALLABLES)],
    )

    assert [e for e in events if e.type is AgentEventType.ERROR] == []
    assert len(requests) == _LONG_RUN_STEPS + 1
    assert spend.model_requests == _LONG_RUN_STEPS + 1
    assert any(
        getattr(e.data, "text", None) == "all done"
        for e in events
        if e.type is AgentEventType.MESSAGE
    )


async def test_a_request_retried_after_a_drop_is_counted_once() -> None:
    attempts: list[int] = []

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        attempts.append(1)
        if len(attempts) == 1:
            yield "half an answer"
            raise httpx2.ReadError("connection reset by peer")
        yield "the answer"

    spend = _spend(agent_settings.agent_run_budget_model_requests)
    await _run(FunctionModel(stream_function=stream_fn), spend=spend)

    assert len(attempts) == 2, "the dropped request is re-asked"
    assert spend.model_requests == 1, "but it is still one request of the budget"
