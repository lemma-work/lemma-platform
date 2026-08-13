"""What a run reports when the graph stops part-way through.

The harness drives pydantic-ai's graph in a child task so its anyio scopes
unwind in the task that created them. That child relays its exception back
through a queue, and a relayed `CancelledError` used to be dropped on the floor:
the generator returned normally, `run()` emitted COMPLETED, and the runner
finalized a successful run whose last message was a tool call nothing ever
answered. Sixty-five of those shipped in one afternoon with no log line anywhere.

The distinction these tests pin is *who* cancelled. Our own teardown is
routine and must stay silent. The driver dying under a healthy parent is a
failed run and must say so.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import AgentEventType, HarnessOptions
from app.modules.agent.infrastructure.harnesses import pydantic_ai as harness_module
from app.modules.agent.infrastructure.harnesses.pydantic_ai import (
    HarnessDriverCancelled,
    PydanticAIHarness,
)

pytestmark = pytest.mark.unit


class _AgentRun:
    """A graph run that never yields a node, ending however it is told to."""

    usage = SimpleNamespace(input_tokens=0, output_tokens=0, requests=0, tool_calls=0)

    def __init__(self, on_next) -> None:
        self._on_next = on_next

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._on_next()


def _install_agent(monkeypatch: pytest.MonkeyPatch, on_next) -> None:
    class _Agent:
        def __init__(self, model, **kwargs):
            del model, kwargs

        def iter(self, *args, **kwargs):
            del args, kwargs
            return _AgentRun(on_next)

    monkeypatch.setattr(harness_module, "PydanticAIAgent", _Agent)
    monkeypatch.setattr(
        harness_module, "_runtime_profile_model", lambda options: object()
    )


def _run_arguments() -> dict[str, object]:
    pod_id = uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=uuid4())
    return {
        "agent": Agent(
            pod_id=pod_id,
            user_id=conversation.user_id,
            name="assistant",
            instruction="",
        ),
        "conversation": conversation,
        "messages": [],
        "ctx": AgentContext(
            user_id=conversation.user_id,
            pod_id=pod_id,
            conversation_id=conversation.id,
        ),
        "options": HarnessOptions(
            model_name="test-model",
            history_summarization_enabled=False,
        ),
        "agent_run_id": uuid4(),
    }


async def _execute_events(harness: PydanticAIHarness, **overrides):
    arguments = _run_arguments() | overrides
    return [
        event
        async for event in harness._execute(
            malformed_tool_call_ids=set(),
            emitted_tool_response_ids=set(),
            should_stop=None,
            **arguments,  # type: ignore[arg-type]
        )
    ]


@pytest.mark.asyncio
async def test_a_driver_cancelled_under_a_healthy_parent_fails_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug itself. Nothing is cancelling *us*, so a run that stopped
    part-way must not come back looking like one that finished."""

    async def cancelled() -> object:
        raise asyncio.CancelledError()

    _install_agent(monkeypatch, cancelled)

    with pytest.raises(HarnessDriverCancelled):
        await _execute_events(PydanticAIHarness())


@pytest.mark.asyncio
async def test_the_failure_reaches_the_run_as_an_error_and_never_as_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COMPLETED is the event the runner turns into a successful agent_run row,
    so this is the assertion that keeps a truncated run out of the database."""

    async def cancelled() -> object:
        raise asyncio.CancelledError()

    _install_agent(monkeypatch, cancelled)
    arguments = _run_arguments()

    events = [event async for event in PydanticAIHarness().run(**arguments)]  # type: ignore[arg-type]
    types = [event.type for event in events]

    assert AgentEventType.COMPLETED not in types
    assert types[-1] == AgentEventType.ERROR
    # The user is told to retry, not to go and check a configuration that is fine.
    assert "Retry" in events[-1].data


@pytest.mark.asyncio
async def test_a_driver_that_ends_normally_still_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard must not fire on the ordinary path — a run with no nodes left
    is how every successful run ends."""

    async def finished() -> object:
        raise StopAsyncIteration

    _install_agent(monkeypatch, finished)
    arguments = _run_arguments()

    events = [event async for event in PydanticAIHarness().run(**arguments)]  # type: ignore[arg-type]

    assert events[-1].type == AgentEventType.COMPLETED


@pytest.mark.asyncio
async def test_our_own_cancellation_is_not_reported_as_a_driver_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A streaq timeout or worker shutdown cancels the consumer. That is a real
    cancellation of this task and must propagate as one: turning it into
    `HarnessDriverCancelled` would tell the runner the worker is healthy and
    the agent broke, which is backwards.
    """
    started = asyncio.Event()

    async def hang() -> object:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    _install_agent(monkeypatch, hang)

    consuming = asyncio.create_task(_execute_events(PydanticAIHarness()))
    await asyncio.wait_for(started.wait(), timeout=5)
    consuming.cancel()

    with pytest.raises(asyncio.CancelledError):
        await consuming
