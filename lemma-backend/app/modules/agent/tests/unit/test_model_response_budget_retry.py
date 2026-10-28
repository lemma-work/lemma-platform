"""An answer that runs past the time budget is not a dropped connection.

Re-sending the identical request takes the same time and expires again, so the
old behaviour -- treat the expiry as a drop, retry it three times, tell the
person the connection kept dropping -- paid for the same over-long answer three
times, threw all of it away, and sent the person round again. These pin the
replacement: one retry asking for less, and an honest sentence when that fails.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx2
import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.run_budget import RunBudget, RunSpend
from app.modules.agent.domain.value_objects import AgentEvent, AgentEventType
from app.modules.agent.infrastructure.harness.lemma.harness import PydanticAIHarness
from app.modules.agent.infrastructure.harness.lemma.retry import (
    RETRY_AFTER_CAP_SECONDS,
    _retry_delay,
)
from app.modules.agent.infrastructure.providers.transport_errors import (
    ModelResponseBudgetExceeded,
)
from app.modules.agent.services.run_input_settings import (
    MIN_REPLY_TOKENS,
    shorter_answer_settings,
)

pytestmark = pytest.mark.unit

_BUDGET_SECONDS = 900.0


def _expired() -> ModelResponseBudgetExceeded:
    return ModelResponseBudgetExceeded(
        "Model stream abandoned after 900.0s: the response ran past its time budget.",
        request=httpx2.Request("POST", "https://provider.example/v1/chat"),
        elapsed_seconds=_BUDGET_SECONDS,
        budget_seconds=_BUDGET_SECONDS,
    )


def _always_over_budget(seen_max_tokens: list[object]):
    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages
        seen_max_tokens.append((info.model_settings or {}).get("max_tokens"))
        yield "a long answer that never ends "
        raise _expired()

    return stream_fn


async def _run(
    model: FunctionModel,
    *,
    spend: RunSpend | None = None,
    lighter_reasoning: dict[str, object] | None = None,
):
    pod_id = uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=uuid4())
    events: list[AgentEvent] = []
    async for event in PydanticAIHarness(model=model).run(
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
            model_settings={"max_tokens": 16_000},
            lighter_reasoning_settings=lighter_reasoning,
            spend=spend,
        ),
        agent_run_id=uuid4(),
    ):
        events.append(event)
    return events


def _resets(events: list[AgentEvent]) -> int:
    return sum(
        event.type is AgentEventType.TOKEN
        and isinstance(event.data, dict)
        and event.data.get("kind") == "stream_reset"
        for event in events
    )


async def test_an_over_budget_answer_is_retried_once_asking_for_less() -> None:
    seen_max_tokens: list[object] = []

    events = await _run(
        FunctionModel(stream_function=_always_over_budget(seen_max_tokens))
    )

    assert seen_max_tokens == [16_000, 8_000], "one retry, with half the room"
    assert _resets(events) == 1
    (error,) = [e for e in events if e.type is AgentEventType.ERROR]
    assert "took longer than 15 minutes" in str(error.data)
    assert "dropping" not in str(error.data)


async def test_the_retry_lowers_effort_on_a_model_that_declares_reasoning() -> None:
    """The run sent no effort, but its model declares REASONING: the retry
    asks for a light one, as `generation_settings` translated it."""
    seen: list[object] = []

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages
        seen.append((info.model_settings or {}).get("openai_reasoning_effort"))
        yield "a long answer "
        raise _expired()

    await _run(
        FunctionModel(stream_function=stream_fn),
        lighter_reasoning={"openai_reasoning_effort": "low"},
    )

    assert seen == [None, "low"]


async def test_an_over_budget_answer_late_in_the_run_is_not_retried() -> None:
    """Another exchange that long would carry the run past its own clock."""
    seen_max_tokens: list[object] = []
    run_clock_seconds = 0.05
    spend = RunSpend(
        budget=RunBudget(
            model_requests=0,
            wall_clock_seconds=run_clock_seconds,
            consecutive_tool_failures=0,
        )
    )

    async def slow_then_over_budget(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages
        seen_max_tokens.append((info.model_settings or {}).get("max_tokens"))
        yield "a long answer "
        # The exchange itself used up the run's clock: a stand-in for the
        # fifteen minutes a real one takes.
        await asyncio.sleep(run_clock_seconds)
        raise _expired()

    events = await _run(
        FunctionModel(stream_function=slow_then_over_budget), spend=spend
    )

    assert seen_max_tokens == [16_000]
    assert _resets(events) == 0


async def test_an_over_budget_answer_that_then_succeeds_completes_the_run() -> None:
    attempts: list[int] = []

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        attempts.append(1)
        if len(attempts) == 1:
            yield "too long "
            raise _expired()
        yield "a shorter answer"

    events = await _run(FunctionModel(stream_function=stream_fn))

    assert len(attempts) == 2
    assert [e for e in events if e.type is AgentEventType.ERROR] == []
    assert events[-1].type is AgentEventType.COMPLETED


def test_the_shorter_answer_lowers_an_effort_it_was_already_sending() -> None:
    lighter = shorter_answer_settings(
        {"max_tokens": 1_500, "openai_reasoning_effort": "high"}
    )

    assert lighter["openai_reasoning_effort"] == "low"
    # Halving would go under the floor; the floor never raises the cap either.
    assert lighter["max_tokens"] == MIN_REPLY_TOKENS


def test_the_shorter_answer_never_introduces_an_effort() -> None:
    """A model that was not sent a reasoning effort may reject the parameter."""
    assert "openai_reasoning_effort" not in shorter_answer_settings({"max_tokens": 9})


def test_a_provider_retry_after_is_honoured_only_up_to_a_ceiling() -> None:
    """A misbehaving header must not park the conversation for an hour."""
    hour = ModelHTTPError(429, "test-model", body=None, headers={"Retry-After": "3600"})
    short = ModelHTTPError(429, "test-model", body=None, headers={"Retry-After": "2"})

    assert _retry_delay(hour, attempt=1) == RETRY_AFTER_CAP_SECONDS
    assert _retry_delay(short, attempt=1) == 2.0
