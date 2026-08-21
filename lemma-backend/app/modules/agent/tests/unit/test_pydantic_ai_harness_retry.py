"""A dropped model stream must not end the conversation run.

Production loses ~20 runs a week to `httpx.ReadError` raised while iterating the
provider's SSE stream: the request was accepted, the connection died mid-answer,
and the exception unwound past the graph into the harness catch-all. These tests
pin the recovery — and, just as importantly, that recovering does not duplicate
the half-written response the user already saw.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import AgentEventType, HarnessOptions
from app.modules.agent.infrastructure.harnesses import pydantic_ai as harness_module
from app.modules.agent.infrastructure.harnesses.pydantic_ai import PydanticAIHarness

pytestmark = pytest.mark.unit


def _drop_then_succeed(drop_after: str, final: str, attempts: list[int]):
    """A stream that dies mid-response once, then answers normally."""

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        attempts.append(1)
        if len(attempts) == 1:
            yield drop_after
            raise httpx.ReadError("connection reset by peer")
        yield final

    return stream_fn


async def _run_harness(model, *, monkeypatch, should_stop=None):
    monkeypatch.setattr(harness_module, "_runtime_profile_model", lambda options: model)
    pod_id = uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=uuid4())
    return [
        event
        async for event in PydanticAIHarness()._execute(
            agent=Agent(
                pod_id=pod_id,
                user_id=conversation.user_id,
                name="assistant",
                instruction="",
            ),
            conversation=conversation,
            messages=[],
            ctx=AgentContext(
                user_id=conversation.user_id,
                pod_id=pod_id,
                conversation_id=conversation.id,
            ),
            options=HarnessOptions(
                model_name="test-model",
                history_summarization_enabled=False,
            ),
            agent_run_id=uuid4(),
            malformed_tool_call_ids=set(),
            emitted_tool_response_ids=set(),
            should_stop=should_stop,
        )
    ]


def _messages(events) -> list[str]:
    """Text of every message the run would persist."""
    return [
        event.data.text
        for event in events
        if event.type is AgentEventType.MESSAGE
        and getattr(event.data, "text", None) is not None
    ]


@pytest.mark.asyncio
async def test_mid_stream_drop_is_retried_and_the_run_completes(monkeypatch) -> None:
    attempts: list[int] = []
    model = FunctionModel(
        stream_function=_drop_then_succeed("partial ", "the real answer", attempts)
    )

    events = await _run_harness(model, monkeypatch=monkeypatch)

    assert len(attempts) == 2, "the dropped request should be re-issued exactly once"
    assert not [e for e in events if e.type is AgentEventType.ERROR]
    assert _messages(events) == ["the real answer"]


@pytest.mark.asyncio
async def test_the_abandoned_partial_response_is_never_persisted(monkeypatch) -> None:
    """The half-written response is not in `all_messages()`, so writing it as it
    streamed would duplicate it on resume. Only whole responses are persisted."""
    attempts: list[int] = []
    model = FunctionModel(
        stream_function=_drop_then_succeed(
            "this text was lost", "the real answer", attempts
        )
    )

    events = await _run_harness(model, monkeypatch=monkeypatch)

    persisted = _messages(events)
    assert persisted == ["the real answer"]
    assert not any("lost" in text for text in persisted)


@pytest.mark.asyncio
async def test_the_retry_does_not_replay_the_truncated_response(monkeypatch) -> None:
    """The retried request must not show the model its own abandoned half-answer.

    `run.all_messages()` after a mid-stream failure has grown a truncated
    ModelResponse and an empty ModelRequest. Resuming from that makes the model
    *continue* text whose first half we deliberately dropped, so the user reads a
    sentence that starts in the middle. The harness resumes from a snapshot taken
    before the failing request instead.
    """
    attempts: list[int] = []
    seen: list[list[str]] = []

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del info
        attempts.append(1)
        seen.append(
            [
                str(getattr(part, "content", ""))
                for message in messages
                for part in message.parts
            ]
        )
        if len(attempts) == 1:
            yield "half a sentence that gets"
            raise httpx.ReadError("connection reset by peer")
        yield "a whole answer"

    await _run_harness(
        FunctionModel(stream_function=stream_fn), monkeypatch=monkeypatch
    )

    assert len(attempts) == 2
    replayed = " ".join(seen[1])
    assert "half a sentence that gets" not in replayed
    # The retry sees exactly what the first attempt saw — same request, re-asked.
    assert seen[1] == seen[0]


@pytest.mark.asyncio
async def test_the_client_is_told_to_discard_the_partial_bubble(monkeypatch) -> None:
    attempts: list[int] = []
    model = FunctionModel(
        stream_function=_drop_then_succeed("partial ", "answer", attempts)
    )

    events = await _run_harness(model, monkeypatch=monkeypatch)

    resets = [
        event
        for event in events
        if event.type is AgentEventType.TOKEN
        and isinstance(event.data, dict)
        and event.data.get("kind") == "stream_reset"
    ]
    assert len(resets) == 1
    # Empty payload keeps older clients correct: they append "" and move on.
    assert resets[0].data["data"] == ""


@pytest.mark.asyncio
async def test_usage_from_the_abandoned_attempt_is_still_billed(monkeypatch) -> None:
    """The provider charges for the tokens it generated before the drop."""
    attempts: list[int] = []
    model = FunctionModel(
        stream_function=_drop_then_succeed("partial ", "answer", attempts)
    )

    events = await _run_harness(model, monkeypatch=monkeypatch)

    usage = [e for e in events if e.type is AgentEventType.USAGE]
    assert len(usage) == 1, "a retried run still reports exactly one usage total"
    # Two requests reached the provider even though one produced no message.
    assert usage[0].data.request_count >= 2


@pytest.mark.asyncio
async def test_a_retry_never_re_executes_a_completed_tool(monkeypatch) -> None:
    """The safety property the whole design rests on.

    If the stream dies on the model request *after* a tool ran, resuming replays
    that tool's recorded result instead of calling it again — so a retry can
    never double-charge a payment, double-send an email, or double-write a file.
    """
    from pydantic_ai.models.function import DeltaToolCall
    from pydantic_ai.toolsets import FunctionToolset

    side_effects: list[str] = []
    attempts: list[int] = []

    async def charge_card(amount: int) -> str:
        side_effects.append(f"charged {amount}")
        return "receipt-1"

    async def stream_fn(messages, info: AgentInfo):
        del info
        attempts.append(1)
        already_called = any(
            type(part).__name__ == "ToolReturnPart"
            for message in messages
            for part in message.parts
        )
        if not already_called:
            yield {0: DeltaToolCall(name="charge_card", json_args='{"amount": 100}')}
            return
        # Second model request — the one that drops the first time it runs.
        if len(attempts) == 2:
            yield "partial"
            raise httpx.ReadError("connection reset by peer")
        yield "done"

    monkeypatch.setattr(
        harness_module,
        "_runtime_profile_model",
        lambda options: FunctionModel(stream_function=stream_fn),
    )
    pod_id = uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=uuid4())
    events = [
        event
        async for event in PydanticAIHarness()._execute(
            agent=Agent(
                pod_id=pod_id,
                user_id=conversation.user_id,
                name="assistant",
                instruction="",
            ),
            conversation=conversation,
            messages=[],
            ctx=AgentContext(
                user_id=conversation.user_id,
                pod_id=pod_id,
                conversation_id=conversation.id,
            ),
            options=HarnessOptions(
                model_name="test-model",
                history_summarization_enabled=False,
                toolsets=[FunctionToolset(tools=[charge_card])],
            ),
            agent_run_id=uuid4(),
            malformed_tool_call_ids=set(),
            emitted_tool_response_ids=set(),
            should_stop=None,
        )
    ]

    assert side_effects == ["charged 100"], "the tool must run exactly once"
    assert "done" in _messages(events)


@pytest.mark.asyncio
async def test_a_non_retryable_provider_error_fails_fast(monkeypatch) -> None:
    """A 402 is a billing problem: retrying burns the same failure three times."""
    attempts: list[int] = []

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        attempts.append(1)
        yield ""
        raise ModelHTTPError(status_code=402, model_name="m", body={"e": "no credit"})

    monkeypatch.setattr(
        harness_module,
        "_runtime_profile_model",
        lambda options: FunctionModel(stream_function=stream_fn),
    )
    pod_id = uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=uuid4())
    with pytest.raises(ModelHTTPError):
        async for _ in PydanticAIHarness()._execute(
            agent=Agent(
                pod_id=pod_id,
                user_id=conversation.user_id,
                name="assistant",
                instruction="",
            ),
            conversation=conversation,
            messages=[],
            ctx=AgentContext(
                user_id=conversation.user_id,
                pod_id=pod_id,
                conversation_id=conversation.id,
            ),
            options=HarnessOptions(
                model_name="test-model", history_summarization_enabled=False
            ),
            agent_run_id=uuid4(),
            malformed_tool_call_ids=set(),
            emitted_tool_response_ids=set(),
            should_stop=None,
        ):
            pass

    assert len(attempts) == 1, "a 402 must not be retried"


@pytest.mark.asyncio
async def test_retrying_stops_at_the_configured_attempt_ceiling(monkeypatch) -> None:
    attempts: list[int] = []

    async def always_drops(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        attempts.append(1)
        yield "x"
        raise httpx.ReadError("connection reset by peer")

    monkeypatch.setattr(
        harness_module.agent_settings, "agent_model_stream_max_attempts", 3
    )
    with pytest.raises(httpx.ReadError):
        await _run_harness(
            FunctionModel(stream_function=always_drops), monkeypatch=monkeypatch
        )

    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_retrying_can_be_disabled(monkeypatch) -> None:
    attempts: list[int] = []

    async def always_drops(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        attempts.append(1)
        yield "x"
        raise httpx.ReadError("boom")

    monkeypatch.setattr(
        harness_module.agent_settings, "agent_model_stream_max_attempts", 1
    )
    with pytest.raises(httpx.ReadError):
        await _run_harness(
            FunctionModel(stream_function=always_drops), monkeypatch=monkeypatch
        )

    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_a_stop_request_wins_over_a_pending_retry(monkeypatch) -> None:
    """Someone pressing stop during the backoff must not get another attempt."""
    attempts: list[int] = []

    async def always_drops(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        attempts.append(1)
        yield "x"
        raise httpx.ReadError("boom")

    async def stop_now() -> bool:
        return True

    events = await _run_harness(
        FunctionModel(stream_function=always_drops),
        monkeypatch=monkeypatch,
        should_stop=stop_now,
    )

    assert len(attempts) == 1
    assert events[-1].type is AgentEventType.STOPPED
