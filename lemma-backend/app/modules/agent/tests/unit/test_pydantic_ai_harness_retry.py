"""A dropped model stream must not end the conversation run.

Runs were lost to a `ReadError` raised while iterating the provider's SSE
stream: the request was accepted, the connection died mid-answer,
and the exception unwound past the graph into the harness catch-all. These tests
pin the recovery — and, just as importantly, that recovering does not duplicate
the half-written response the user already saw.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx2
import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.tools import FlatArguments, NoArguments, ToolGroup, tool
from app.modules.agent.domain.value_objects import AgentEventType
from app.modules.agent.infrastructure.harness.lemma import harness as harness_module
from app.modules.agent.infrastructure.harness.lemma.harness import PydanticAIHarness

pytestmark = pytest.mark.unit


class _Charge(FlatArguments):
    amount: int


def _drop_then_succeed(drop_after: str, final: str, attempts: list[int]):
    """A stream that dies mid-response once, then answers normally."""

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        attempts.append(1)
        if len(attempts) == 1:
            yield drop_after
            raise httpx2.ReadError("connection reset by peer")
        yield final

    return stream_fn


def _budget_expiry_then_succeed(final: str, attempts: list[int]):
    """A stream abandoned by the progress budget once, then answering normally.

    Raises what `model_stream_budget` raises, message and all, rather than a
    bare `ReadTimeout`: the budget is only a fix if its error routes into this
    retry, and a test that invented its own exception would pass while the real
    one fell through to the catch-all and failed the run.
    """

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        attempts.append(1)
        if len(attempts) == 1:
            yield "a trickle "
            raise httpx2.ReadTimeout(
                "Model stream abandoned after 300.0s: "
                "the provider stopped making progress.",
                request=httpx2.Request("POST", "https://provider.example/v1/chat"),
            )
        yield final

    return stream_fn


def _stream_text(text: str):
    """A stream that answers normally."""

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield text

    return stream_fn


def _stream_then_fail(partial: str):
    """A stream that dies mid-response, every time, unretryably."""

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield partial
        raise ValueError("provider blew up")

    return stream_fn


def _stream_tool_call(tool_name: str):
    """A stream whose whole response is one call to `tool_name`."""

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[object]:
        del messages, info
        yield {0: DeltaToolCall(name=tool_name, json_args="{}")}

    return stream_fn


async def _run_harness(model, *, monkeypatch, should_stop=None, tools=None):
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
            ctx=ToolContext(
                user_id=conversation.user_id,
                pod_id=pod_id,
                conversation_id=conversation.id,
            ),
            options=HarnessOptions(
                model_name="test-model",
                history_summarization_enabled=False,
                tools=tools or [],
            ),
            agent_run_id=uuid4(),
            should_stop=should_stop,
        )
    ]


async def _run_harness_until_it_dies(model, *, monkeypatch, tools=None):
    """Events queued before `_execute` raised, plus the exception.

    `_execute` does not turn a pause or a fatal error into an event -- `run()`
    does, one layer up. Testing at this level sees exactly what reached the
    queue before the run died.
    """
    events = []
    try:
        async for event in _execute_events(model, monkeypatch, tools):
            events.append(event)
    except Exception as exc:  # noqa: BLE001 - returned for assertion
        # Deliberately not `BaseException`: the two endings under test
        # (`AgentInputRequired`, a provider error) are both `Exception`s, and
        # catching wider would swallow a `KeyboardInterrupt` that should stop
        # the suite. A `CancelledError` here is a real failure, not a result.
        return events, exc
    return events, None


def _execute_events(model, monkeypatch, tools):
    monkeypatch.setattr(harness_module, "_runtime_profile_model", lambda options: model)
    pod_id = uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=uuid4())
    return PydanticAIHarness()._execute(
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
        ),
        agent_run_id=uuid4(),
        should_stop=None,
    )


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
async def test_a_stream_abandoned_by_the_progress_budget_is_retried(
    monkeypatch,
) -> None:
    """The end of the chain the stream budget depends on.

    A provider trickling a token a second used to hold the run open until
    somebody gave up; it is now abandoned with a `ReadTimeout`. This is what
    makes that an improvement rather than a new way to fail: the run re-asks the
    request and answers, and the person never learns any of it happened.
    """
    attempts: list[int] = []
    model = FunctionModel(
        stream_function=_budget_expiry_then_succeed("the real answer", attempts)
    )

    events = await _run_harness(model, monkeypatch=monkeypatch)

    assert len(attempts) == 2, "the abandoned request should be re-issued once"
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
            raise httpx2.ReadError("connection reset by peer")
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
async def test_a_retry_never_re_executes_a_completed_tool(monkeypatch) -> None:
    """The safety property the whole design rests on.

    If the stream dies on the model request *after* a tool ran, resuming replays
    that tool's recorded result instead of calling it again — so a retry can
    never double-charge a payment, double-send an email, or double-write a file.
    """
    from pydantic_ai.models.function import DeltaToolCall

    side_effects: list[str] = []
    attempts: list[int] = []

    async def charge_card(ctx: ToolContext, request: _Charge) -> str:
        side_effects.append(f"charged {request.amount}")
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
            raise httpx2.ReadError("connection reset by peer")
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
            ctx=ToolContext(
                user_id=conversation.user_id,
                pod_id=pod_id,
                conversation_id=conversation.id,
            ),
            options=HarnessOptions(
                model_name="test-model",
                history_summarization_enabled=False,
                tools=[tool(charge_card, ToolGroup.CALLABLES)],
            ),
            agent_run_id=uuid4(),
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
        raise httpx2.ReadError("connection reset by peer")

    monkeypatch.setattr(
        harness_module.agent_settings, "agent_model_stream_max_attempts", 3
    )
    with pytest.raises(httpx2.ReadError):
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
        raise httpx2.ReadError("boom")

    monkeypatch.setattr(
        harness_module.agent_settings, "agent_model_stream_max_attempts", 1
    )
    with pytest.raises(httpx2.ReadError):
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
        raise httpx2.ReadError("boom")

    async def stop_once_an_attempt_began() -> bool:
        # Pressed during the first attempt, not before it. A stop already
        # pressed when the run starts is honoured by the consumer's own poll
        # (STOP_POLL_SECONDS) -- correctly -- and on a slow runner that poll
        # beat the driver to its first model call, so the run stopped with
        # no attempt at all and this test read 0.
        return bool(attempts)

    events = await _run_harness(
        FunctionModel(stream_function=always_drops),
        monkeypatch=monkeypatch,
        should_stop=stop_once_an_attempt_began,
    )

    assert len(attempts) == 1
    assert events[-1].type is AgentEventType.STOPPED


class TestTheHarnessDoesNotBill:
    """Billing is the metered model's: one receipt per request it makes.

    The harness used to also queue a USAGE total ahead of every terminal event,
    with ordering rules of its own -- and the recorder then discarded it for
    every protocol this harness can run. A second, dead billing path is one
    that misleads readers and can only fail, so it is gone; these pin that it
    stays gone however the run ends.
    """

    @staticmethod
    def _usage(events):
        return [e for e in events if e.type is AgentEventType.USAGE]

    @pytest.mark.asyncio
    async def test_a_completed_run_reports_no_usage_event(self, monkeypatch) -> None:
        attempts: list[int] = []
        events = await _run_harness(
            FunctionModel(
                stream_function=_drop_then_succeed("partial ", "answer", attempts)
            ),
            monkeypatch=monkeypatch,
        )

        assert len(attempts) == 2
        assert self._usage(events) == []

    @pytest.mark.asyncio
    async def test_a_stopped_run_reports_no_usage_event(self, monkeypatch) -> None:
        async def stop_now() -> bool:
            return True

        events = await _run_harness(
            FunctionModel(stream_function=_stream_text("some tokens")),
            monkeypatch=monkeypatch,
            should_stop=stop_now,
        )

        assert events[-1].type is AgentEventType.STOPPED
        assert self._usage(events) == []

    @pytest.mark.asyncio
    async def test_a_paused_run_reports_no_usage_event(self, monkeypatch) -> None:
        from app.modules.agent.domain.tools import AgentInputRequired

        async def request_approval(ctx: ToolContext, request: NoArguments) -> str:
            raise AgentInputRequired("call-1", "request_approval")

        events, exc = await _run_harness_until_it_dies(
            FunctionModel(stream_function=_stream_tool_call("request_approval")),
            monkeypatch=monkeypatch,
            tools=[tool(request_approval, ToolGroup.USER_INTERACTION)],
        )

        assert isinstance(exc, AgentInputRequired), "the run should have paused"
        assert self._usage(events) == []

    @pytest.mark.asyncio
    async def test_a_run_that_dies_reports_no_usage_event(self, monkeypatch) -> None:
        events, exc = await _run_harness_until_it_dies(
            FunctionModel(stream_function=_stream_then_fail("partial ")),
            monkeypatch=monkeypatch,
        )

        assert isinstance(exc, ValueError), exc
        assert self._usage(events) == []
